#!/usr/bin/env python3
"""Ready Now (quick move-in) database builder.

Turns builder-published QMI drops - PDFs, spreadsheets, scraped listing pages - into the
app's home-grain `QMI` data, linked to Market Watch subdivisions, with price history and
derived deal benchmarks. See docs/READYNOW_PLAN.md.

Flow:

  1) python tools/build_readynow.py parse "ReadyNow/Ready September 2026 Division QMIs.pdf"
       - picks the parser adapter, normalizes rows, matches each home to a SUBS id
       - writes tools/_work/readynow/parsed.json
       - prints anything it could not match (fix tools/readynow/aliases.json, re-run)

  2) python tools/build_readynow.py geocode
       - US Census batch geocoder per street address, cached in tools/readynow/geocache.json
       - misses fall back to the community/sales-office coordinate, flagged "community"

  3) python tools/build_readynow.py apply
       - merges into tools/readynow/store.json (firstSeen / lastSeen / priceHistory / gone)
       - computes $/sq ft benchmarks, price cuts, aging and the deal score
       - injects QMI + QMI_ASOF into index.html

  python tools/build_readynow.py import-inventory   # one-time: fold the old INVENTORY in
  python tools/build_readynow.py status             # what is in the store right now

Then: python tools/verify.py  (must be green) -> commit + push.

Requires: pymupdf, requests  (pip install pymupdf requests)
"""
import sys, os, re, json, csv, io, math, datetime, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from readynow import common as C
from readynow.parsers import taylor_morrison, web_qmi

ADAPTERS = [taylor_morrison, web_qmi]

WORK = os.path.join(C.REPO, "tools", "_work", "readynow")
PARSED = os.path.join(WORK, "parsed.json")
GEOCACHE = os.path.join(C.RN_DIR, "geocache.json")

# Homes that vanish from a drop stay visible this long, marked "no longer listed".
GONE_DAYS = 30


# ------------------------------------------------------------------ index.html

def read_index():
    with open(C.INDEX, encoding="utf-8") as f:
        return f.read()


def load_subs(src=None):
    src = src or read_index()
    m = re.search(r"^const SUBS = (\[.*?\]);$", src, re.M | re.S)
    if not m:
        raise SystemExit("could not find SUBS in index.html")
    return json.loads(m.group(1))


def load_inventory(src=None):
    src = src or read_index()
    m = re.search(r"^const INVENTORY = (\{.*?\});$", src, re.M | re.S)
    return json.loads(m.group(1)) if m else {}


# ------------------------------------------------------------------- matching

def builder_code(builder):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", builder or "") if w]
    code = "".join(w[0] for w in words).lower()
    if len(code) < 2:
        code = C.nrm(builder)[:2]
    return code or "x"


def match_sub(rec, subs, aliases):
    """Resolve a builder's marketing community name to a Market Watch SUBS id."""
    comm = rec.get("community") or ""
    by_builder = aliases.get(rec.get("builder"), {})
    if comm in by_builder:
        return by_builder[comm], "alias"
    # Builders are inconsistent about hyphen vs en-dash vs em-dash and about capitalisation,
    # so an alias is matched on the normalized name, not the literal string.
    norm_alias = {C.nrm(k): v for k, v in by_builder.items()}
    if C.nrm(comm) in norm_alias:
        return norm_alias[C.nrm(comm)], "alias"

    same_builder = [s for s in subs if C.nrm(s["builder"]) == C.nrm(rec["builder"])]
    n = C.nrm(comm)
    for s in same_builder:                                    # exact normalized name
        if C.nrm(s["sub"]) == n:
            return s["id"], "exact"
    # "<name> at <master plan>" and "The <x> Collection at <name>"
    stripped = re.sub(r"^the\s+", "", comm.strip(), flags=re.I)
    stripped = re.sub(r"\s+collection\b.*$", "", stripped, flags=re.I)
    stripped = re.sub(r"\s+at\s+.*$", "", stripped, flags=re.I)
    ns = C.nrm(stripped)
    cands = [s for s in same_builder if C.nrm(s["sub"]) == ns]
    if len(cands) == 1:
        return cands[0]["id"], "trimmed"
    # containment, only when it is unambiguous
    cands = [s for s in same_builder if ns and (ns in C.nrm(s["sub"]) or C.nrm(s["sub"]) in n)]
    if len(cands) == 1:
        return cands[0]["id"], "contains"
    # last resort: same builder, same sales-office street address
    street, _, _ = C.parse_addr(rec.get("salesOffice"))
    if street:
        cands = [s for s in same_builder
                 if s.get("address") and C.nrm(street) and C.nrm(street) in C.nrm(s["address"])]
        if len(cands) == 1:
            return cands[0]["id"], "office"
    return None, "unmatched"


# ------------------------------------------------------------------ normalize

"""Rows that describe a community's inventory in aggregate ("4 QMI available, see builder
site") rather than one identified home. They carry a real starting price, so they stay in
the database, but they are flagged so counts and deal maths do not treat them as one home."""
POINTER = re.compile(r"qmi available|see builder site|now selling|models? (?:now open|for sale)"
                     r"|final (?:homes|opportunity)|home designs", re.I)


def zip_areas(subs):
    """ZIP -> area of town, learned from the Market Watch dataset itself. Lets a home in a
    community HBR has not listed yet still land in the right area filter and map."""
    counts = {}
    for s in subs:
        m = re.search(r"(\d{5})", s.get("address") or "")
        if m and s.get("area"):
            counts.setdefault(m.group(1), {}).setdefault(s["area"], 0)
            counts[m.group(1)][s["area"]] += 1
    return {z: max(a, key=a.get) for z, a in counts.items()}


def merge_raw(keep, other):
    """Two sources describing one home. The fresher drop wins a conflict; the other fills
    gaps (a builder PDF has facing and series, a listing page has was-price and a link)."""
    fresher = (other.get("asOf") or "") > (keep.get("asOf") or "")
    for k, v in other.items():
        if v in (None, "", False, 0):
            continue
        if keep.get(k) in (None, "", False, 0) or (fresher and k not in ("source", "sourceDoc")):
            keep[k] = v
    keep.setdefault("alsoSeen", [])
    if other.get("source") and other["source"] != keep.get("source") and other["source"] not in keep["alsoSeen"]:
        keep["alsoSeen"].append(other["source"])


def normalize(raw, subs, aliases):
    by_id = {s["id"]: s for s in subs}
    zareas = zip_areas(subs)
    out = []
    # Phase 1: give every raw row its stable id and fold duplicates together.
    # Builder + street address + ZIP IS the home's identity, so the same house appearing in
    # both a division PDF and the weekly web pull merges instead of becoming two listings.
    # Only address-less rows (community-level) get a disambiguating suffix.
    seen = {}
    order = []
    for r in raw:
        sub_id, how = match_sub(r, subs, aliases)
        street = r.get("address") or ""
        if street:
            base = "%s-%s-%s" % (builder_code(r["builder"]), C.slug(street, 34), r.get("zip") or "nv")
        else:
            base = "%s-%s-%s" % (builder_code(r["builder"]),
                                 C.slug(sub_id or r.get("community"), 28),
                                 C.slug(r.get("plan") or "home", 18))
        hid = base.replace("--", "-").strip("-")
        if hid in seen:
            if street:
                merge_raw(seen[hid][0], r)
                continue
            base, k = hid, 2
            while hid in seen:
                hid = "%s-%d" % (base, k)
                k += 1
        seen[hid] = (r, sub_id, how)
        order.append(hid)

    # Phase 2: shape each deduped row into an app record.
    for hid in order:
        r, sub_id, how = seen[hid]
        s = by_id.get(sub_id) or {}
        street = r.get("address") or ""
        avail_label = re.sub(r"\s+", " ", str(r.get("availLabel") or "")).strip()
        r["availLabel"] = avail_label
        status, avail_date = C.parse_availability(avail_label, r.get("asOf"))
        price, sqft = r.get("price"), r.get("sqft")
        out.append({
            "id": hid,
            "subId": sub_id,
            "match": how,
            "builder": r["builder"],
            "community": r.get("community"),
            "collection": r.get("collection") or "",
            "area": s.get("area") or zareas.get(r.get("zip") or "") or r.get("region"),
            "builderRegion": r.get("region") or "",
            "mp": s.get("mp") or "",
            "type": s.get("type") or "Detached",
            "plan": r.get("plan"),
            "series": r.get("series") or "",
            "planFull": r.get("planFull") or r.get("plan"),
            "facing": r.get("facing"),
            "isModel": bool(r.get("isModel")),
            "age55": bool(r.get("age55")),
            "pointer": bool(POINTER.search(r.get("availLabel") or "")),
            "address": street or None,
            "city": r.get("city"),
            "zip": r.get("zip"),
            "lat": None, "lng": None, "geoPrecision": None,
            "sqft": sqft, "beds": r.get("beds"), "baths": r.get("baths"),
            "stories": r.get("stories"), "garage": r.get("garage"),
            "price": price,
            "wasPrice": r.get("wasPrice") or None,
            "homesite": r.get("homesite") or None,
            "incentive": r.get("incentive") or "",
            "ppsf": int(round(price / sqft)) if (price and sqft) else None,
            "status": status,
            "availLabel": r.get("availLabel") or ("Ready Now" if status == "ready" else ""),
            "availDate": avail_date,
            "salesOffice": r.get("salesOffice"),
            "salesPhone": r.get("salesPhone"),
            "url": r.get("url") or s.get("website") or None,
            "source": r.get("source"),
            "sourceDoc": r.get("sourceDoc") or "",
            "divisionOffice": r.get("divisionOffice") or "",
            "priceNote": r.get("priceNote") or "",
            "asOf": r.get("asOf") or C.today(),
            "es": s.get("es") or "", "ms": s.get("ms") or "", "hs": s.get("hs") or "",
        })
    return out


# ------------------------------------------------------------------- geocoding

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_ONE = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "buyer2builder-marketwatch/2.1 (readynow ingest)"}

# Brand-new construction streets are routinely missing from the batch geocoder but present
# one address at a time, or in OSM. Both are tried before falling back to a community pin.
NV_BOUNDS = (35.0, 37.2, -116.6, -113.9)


def in_nv(lat, lng):
    return NV_BOUNDS[0] <= lat <= NV_BOUNDS[1] and NV_BOUNDS[2] <= lng <= NV_BOUNDS[3]


def km_between(a1, o1, a2, o2):
    r = math.radians
    return 2 * 6371.0 * math.asin(math.sqrt(
        math.sin(r(a2 - a1) / 2) ** 2 +
        math.cos(r(a1)) * math.cos(r(a2)) * math.sin(r(o2 - o1) / 2) ** 2))


def geocode_one(addr):
    import requests, time
    try:
        r = requests.get(CENSUS_ONE, params={"address": addr, "benchmark": "Public_AR_Current",
                                             "format": "json"}, timeout=30)
        ms = r.json().get("result", {}).get("addressMatches") or []
        if ms:
            c = ms[0]["coordinates"]
            if in_nv(c["y"], c["x"]):
                return [c["y"], c["x"]], "rooftop"
    except Exception:
        pass
    try:
        time.sleep(1.1)                      # Nominatim asks for <= 1 request/second
        r = requests.get(NOMINATIM, params={"q": addr, "format": "json", "limit": 1},
                         headers=UA, timeout=30)
        js = r.json()
        if js:
            lat, lng = float(js[0]["lat"]), float(js[0]["lon"])
            if in_nv(lat, lng):
                return [lat, lng], "street"
    except Exception:
        pass
    return None, None


def geocode_records(recs, subs):
    """Rooftop-geocode street addresses; fall back to the community coordinate."""
    cache = C.load_json(GEOCACHE, {})
    by_id = {s["id"]: s for s in subs}
    todo = []
    for r in recs:
        if not r.get("address"):
            continue
        key = "%s, %s, NV %s" % (r["address"], r.get("city") or "", r.get("zip") or "")
        if key not in cache:
            todo.append((r["id"], r["address"], r.get("city") or "", r.get("zip") or "", key))

    if todo:
        print("geocoding %d address(es) via US Census..." % len(todo))
        for i in range(0, len(todo), 500):
            chunk = todo[i:i + 500]
            buf = io.StringIO()
            w = csv.writer(buf)
            for hid, street, city, zc, _ in chunk:
                w.writerow([hid, street, city, "NV", zc])
            try:
                import requests
                resp = requests.post(
                    CENSUS,
                    files={"addressFile": ("addrs.csv", buf.getvalue(), "text/csv")},
                    data={"benchmark": "Public_AR_Current"}, timeout=180)
                resp.raise_for_status()
                got = {}
                for row in csv.reader(io.StringIO(resp.text)):
                    if len(row) >= 6 and row[2].strip() == "Match":
                        lng, lat = row[5].split(",")
                        got[row[0]] = (float(lat), float(lng))
                for hid, street, city, zc, key in chunk:
                    if hid in got and in_nv(*got[hid]):
                        cache[key] = {"pt": list(got[hid]), "p": "rooftop"}
            except Exception as e:            # offline or blocked: fall back cleanly
                print("  census batch geocoder unavailable (%s)" % e)

        misses = [t for t in todo if not cache.get(t[4])]
        if misses:
            print("  %d not in the batch index; retrying one at a time..." % len(misses))
            for hid, street, city, zc, key in misses:
                pt, prec = geocode_one(key)
                cache[key] = {"pt": pt, "p": prec} if pt else None
        C.save_json(GEOCACHE, cache)

    prec_count = {}
    for r in recs:
        key = "%s, %s, NV %s" % (r.get("address") or "", r.get("city") or "", r.get("zip") or "")
        hit = cache.get(key)
        s = by_id.get(r.get("subId")) or {}
        # A geocode that lands miles from its own sales office is wrong, not precise.
        if hit and hit.get("pt") and (s.get("lat") is None or
                                      km_between(hit["pt"][0], hit["pt"][1], s["lat"], s["lng"]) <= 5.0):
            r["lat"], r["lng"] = hit["pt"][0], hit["pt"][1]
            r["geoPrecision"] = hit.get("p") or "rooftop"
        elif s.get("lat") is not None:
            r["lat"], r["lng"], r["geoPrecision"] = s["lat"], s["lng"], "community"
        prec_count[r.get("geoPrecision") or "none"] = prec_count.get(r.get("geoPrecision") or "none", 0) + 1
    print("geocoded: " + " · ".join("%d %s" % (v, k) for k, v in sorted(prec_count.items())))
    return recs


# ----------------------------------------------------------------------- merge

def merge_store(store, fresh):
    """Fold a fresh drop into the durable store: history, first/last seen, gone flags."""
    today = C.today()
    homes = {h["id"]: h for h in store.get("homes", [])}
    fresh_ids = set()

    for r in fresh:
        fresh_ids.add(r["id"])
        prev = homes.get(r["id"])
        if not prev:
            r["firstSeen"] = today
            r["lastSeen"] = today
            r["priceHistory"] = ([{"d": r.get("asOf") or today, "p": r["price"], "s": r.get("source")}]
                                 if r.get("price") else [])
            r["sources"] = [r["source"]] if r.get("source") else []
            r["gone"] = False
            homes[r["id"]] = r
            continue
        hist = prev.get("priceHistory") or []
        stamp = r.get("asOf") or today
        src = r.get("source")
        same = [h for h in hist if h.get("s") == src]
        if r.get("price") and (not same or same[-1]["p"] != r["price"]):
            hist.append({"d": stamp, "p": r["price"], "s": src})
        merged = dict(prev)
        # A record from a different source only fills gaps unless it is at least as fresh,
        # so a dated builder sheet never overwrites a live listing (or the reverse).
        if prev.get("source") != src and (r.get("asOf") or "") < (prev.get("asOf") or ""):
            for k, v in r.items():
                if merged.get(k) in (None, "", False) and v not in (None, "", False):
                    merged[k] = v
        else:
            merged.update(r)
        merged["sources"] = sorted(set((prev.get("sources") or [prev.get("source")]) + [src]) - {None})
        merged["firstSeen"] = prev.get("firstSeen") or today
        merged["lastSeen"] = today
        merged["priceHistory"] = hist
        merged["gone"] = False
        merged["goneAt"] = None
        homes[r["id"]] = merged

    # anything from the same builder+source not in this drop is off the market
    builders = {(r["builder"], r["source"]) for r in fresh}
    for hid, h in homes.items():
        if hid in fresh_ids:
            continue
        if (h.get("builder"), h.get("source")) in builders and not h.get("gone"):
            h["gone"] = True
            h["goneAt"] = today

    store["homes"] = list(homes.values())
    store["updated"] = today
    return store


def prune(store):
    """Drop long-gone homes from the app projection (they stay in the store)."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=GONE_DAYS)).isoformat()
    keep, archived = [], 0
    for h in store["homes"]:
        if h.get("gone") and (h.get("goneAt") or "0000") < cutoff:
            archived += 1
            continue
        keep.append(h)
    return keep, archived


# ----------------------------------------------------------------- deal engine

def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def clamp01(x):
    return 0.0 if x is None or x < 0 else (1.0 if x > 1 else x)


def score_deals(homes):
    """Derive the benchmarks that turn a list price into a judgement.

    Three tiers, most specific first: the same floor plan in the same community, then the
    community's own $/sq ft, then the area's. Plus real price cuts and time on the list.
    """
    live = [h for h in homes if not h.get("gone") and not h.get("pointer")]
    plan_groups, comm_groups, area_groups = {}, {}, {}
    for h in live:
        if h.get("price") and h.get("sqft"):
            plan_groups.setdefault(
                (h.get("subId") or h.get("community"), C.nrm(h.get("plan")), h["sqft"]), []).append(h)
        if h.get("ppsf"):
            comm_groups.setdefault(h.get("subId") or h.get("community"), []).append(h["ppsf"])
            area_groups.setdefault(h.get("area"), []).append(h["ppsf"])

    today = datetime.date.today()
    for h in homes:
        d = {"score": 0, "badges": []}
        # 1. same plan, same community
        key = (h.get("subId") or h.get("community"), C.nrm(h.get("plan")), h.get("sqft"))
        peers = [p for p in plan_groups.get(key, []) if p["id"] != h["id"]]
        plan_pct = 0.0
        if peers and h.get("price"):
            pm = med([p["price"] for p in peers])
            if pm and pm > h["price"]:
                d["planPeers"] = len(peers)
                d["planMedian"] = int(pm)
                d["planDelta"] = int(pm - h["price"])
                plan_pct = (pm - h["price"]) / pm
                d["planPct"] = round(plan_pct * 100, 1)
                d["badges"].append("$%sk under the same plan here" % int(round(d["planDelta"] / 1000)))
        # 2. community $/sq ft
        comm_pct = 0.0
        cvals = [v for v in comm_groups.get(h.get("subId") or h.get("community"), [])]
        if len(cvals) >= 3 and h.get("ppsf"):
            cm = med(cvals)
            if cm and cm > h["ppsf"]:
                comm_pct = (cm - h["ppsf"]) / cm
                d["commPpsf"] = int(cm)
                d["commPct"] = round(comm_pct * 100, 1)
                if comm_pct >= 0.03:
                    d["badges"].append("%d%% under this community's $/sq ft" % round(comm_pct * 100))
        # 3. area $/sq ft
        area_pct = 0.0
        avals = area_groups.get(h.get("area"), [])
        if len(avals) >= 5 and h.get("ppsf"):
            am = med(avals)
            if am and am > h["ppsf"]:
                area_pct = (am - h["ppsf"]) / am
                d["areaPpsf"] = int(am)
                d["areaPct"] = round(area_pct * 100, 1)
        # 4. a real, dated price cut
        cut_pct = 0.0
        # Only compare prices from the SAME source: a dated builder sheet and a live listing
        # page disagreeing is not a price cut.
        hist = ([x for x in (h.get("priceHistory") or []) if x.get("s") == h.get("source")]
                or (h.get("priceHistory") or []))
        if h.get("wasPrice") and h.get("price") and h["wasPrice"] > h["price"]:
            d["cut"] = int(h["wasPrice"] - h["price"])          # the builder publishes the was-price
            d["cutFrom"] = int(h["wasPrice"])
            d["cutSince"] = h.get("asOf") or h.get("lastSeen")
            d["cutSource"] = "builder"
            cut_pct = d["cut"] / h["wasPrice"]
        elif len(hist) >= 2 and hist[0]["p"] and h.get("price") and hist[0]["p"] > h["price"]:
            d["cut"] = int(hist[0]["p"] - h["price"])           # we watched it fall
            d["cutFrom"] = hist[0]["p"]
            d["cutSince"] = hist[0]["d"]
            d["cutSource"] = "tracked"
            cut_pct = d["cut"] / hist[0]["p"]
        if d.get("cut"):
            d["cutPct"] = round(cut_pct * 100, 1)
            d["badges"].insert(0, ("Builder cut $%sk" % int(round(d["cut"] / 1000)))
                               if d.get("cutSource") == "builder"
                               else "Cut $%sk since %s" % (int(round(d["cut"] / 1000)),
                                                           fmt_day(d["cutSince"])))
        # 5. time on the list
        days = 0
        if h.get("firstSeen"):
            try:
                days = (today - datetime.date.fromisoformat(h["firstSeen"])).days
            except ValueError:
                days = 0
        d["days"] = days
        if days >= 60:
            d["badges"].append("On the list %d days" % days)
        # 6. quiet tell: builders list base prices as $XX9,990
        if h.get("price") and h["price"] % 1000 not in (0, 990, 900, 995, 500):
            d["adjusted"] = True

        d["score"] = int(round(min(100.0,
                                   35 * clamp01(plan_pct / 0.08) +
                                   25 * clamp01(comm_pct / 0.10) +
                                   15 * clamp01(area_pct / 0.15) +
                                   15 * clamp01(cut_pct / 0.05) +
                                   10 * clamp01(days / 120.0))))
        if h.get("isModel"):
            d["badges"].append("Model home")
        h["deal"] = d
    return homes


def fmt_day(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%b %-d")
    except Exception:
        try:
            return datetime.date.fromisoformat(iso).strftime("%b %d").replace(" 0", " ")
        except Exception:
            return iso


# ------------------------------------------------------------------ projection

APP_FIELDS = ["id", "subId", "builder", "community", "collection", "area", "builderRegion",
              "mp", "type", "plan", "series", "planFull", "facing", "isModel", "age55",
              "pointer", "address", "city", "zip",
              "lat", "lng", "geoPrecision", "sqft", "beds", "baths", "stories", "garage",
              "price", "wasPrice", "homesite", "incentive", "ppsf", "status", "availLabel", "availDate",
              "salesOffice", "salesPhone", "url", "source",
              "firstSeen", "lastSeen", "gone", "goneAt", "es", "ms", "hs", "deal"]

# Carried once per source document instead of on every row.
SOURCE_FIELDS = ["sourceDoc", "divisionOffice", "priceNote", "asOf", "salesPhone"]


def sources_of(homes):
    out = {}
    for h in homes:
        key = h.get("source") or "unknown"
        cur = out.setdefault(key, {})
        for f in SOURCE_FIELDS:
            if h.get(f) and (f != "asOf" or h[f] > cur.get("asOf", "")):
                cur[f] = h[f]
    return out


def project(homes):
    out = []
    for h in sorted(homes, key=lambda x: (x.get("area") or "", x.get("community") or "",
                                          x.get("price") or 0)):
        rec = {k: h.get(k) for k in APP_FIELDS if h.get(k) not in (None, "", False)}
        rec["id"] = h["id"]
        if h.get("planFull") == h.get("plan"):
            rec.pop("planFull", None)
        hist = h.get("priceHistory") or []
        if len(hist) >= 2:
            rec["priceHistory"] = hist[-4:]
        out.append(rec)
    return out


def inject(app_homes, asof, coverage, sources):
    src = read_index()
    payload = json.dumps(app_homes, separators=(",", ":"), ensure_ascii=False)
    block = ('/* Ready Now: builder-published quick move-in homes, one record per home.\n'
             '   Built by tools/build_readynow.py from tools/readynow/store.json. */\n'
             'const QMI_ASOF = %s;\n'
             'const QMI_COVERAGE = %s;\n'
             'const QMI_SOURCES = %s;\n'
             'const QMI = %s;' % (json.dumps(asof), json.dumps(coverage),
                                  json.dumps(sources, ensure_ascii=False), payload))

    if re.search(r"^/\* Ready Now: .*?^const QMI = \[.*?\];$", src, re.M | re.S):
        src = re.sub(r"^/\* Ready Now: .*?^const QMI = \[.*?\];$", lambda m: block, src,
                     count=1, flags=re.M | re.S)
    else:
        anchor = re.search(r"^const INVENTORY = \{.*?\};$", src, re.M | re.S)
        if not anchor:
            raise SystemExit("could not find the INVENTORY anchor in index.html")
        src = src[:anchor.end()] + "\n" + block + src[anchor.end():]

    with open(C.INDEX, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)


# ----------------------------------------------------------------- subcommands

def cmd_parse(paths):
    if not paths:
        raise SystemExit("usage: build_readynow.py parse <file> [file...]")
    subs = load_subs()
    aliases = C.load_json(C.ALIASES, {})
    raw = []
    for p in paths:
        adapter = next((a for a in ADAPTERS if a.detect(p)), None)
        if not adapter:
            raise SystemExit("no parser adapter recognises %s" % p)
        rows = adapter.parse(p)
        print("%-34s %-22s %d homes" % (os.path.basename(p), adapter.BUILDER, len(rows)))
        raw += rows

    recs = normalize(raw, subs, aliases)
    C.save_json(PARSED, recs)

    bad = [r for r in recs if not r["subId"]]
    weak = [r for r in recs if r["match"] in ("contains", "office")]
    print("\n%d homes normalized -> %s" % (len(recs), os.path.relpath(PARSED, C.REPO)))
    if weak:
        print("\nmatched loosely (confirm, then pin in tools/readynow/aliases.json):")
        for r in sorted({(r["builder"], r["community"], r["subId"], r["match"]) for r in weak}):
            print("   %-16s %-52s -> %-24s (%s)" % r)
    if bad:
        print("\nUNMATCHED - add these to tools/readynow/aliases.json and re-run parse:")
        for r in sorted({(r["builder"], r["community"]) for r in bad}):
            print("   %-16s %s" % r)
    else:
        print("every community matched a Market Watch subdivision.")
    return recs


def cmd_geocode():
    recs = C.load_json(PARSED, None)
    if recs is None:
        raise SystemExit("run parse first")
    geocode_records(recs, load_subs())
    C.save_json(PARSED, recs)


def cmd_apply():
    recs = C.load_json(PARSED, None)
    if recs is None:
        raise SystemExit("run parse first")
    store = C.load_json(C.STORE, {"homes": []})
    before = {h["id"]: dict(h) for h in store.get("homes", [])}
    store = merge_store(store, recs)
    app_homes, archived = prune(store)
    score_deals(store["homes"])
    score_deals(app_homes)
    C.save_json(C.STORE, store)

    asof = max([h.get("asOf") or "" for h in app_homes] or [C.today()])
    builders = sorted({h["builder"] for h in app_homes if not h.get("gone")})
    subs_covered = len({h.get("subId") for h in app_homes if h.get("subId") and not h.get("gone")})
    coverage = "%s · %d communities" % (", ".join(builders), subs_covered)
    inject(project(app_homes), asof, coverage, sources_of(app_homes))

    write_change_report(before, store, app_homes)
    live = [h for h in app_homes if not h.get("gone")]
    ready = [h for h in live if h.get("status") == "ready" and not h.get("pointer")]
    print("store: %d homes (%d archived beyond %d days)" % (len(store["homes"]), archived, GONE_DAYS))
    print("app:   %d homes · %d ready now · %d builders · %d communities · as of %s"
          % (len(app_homes), len(ready), len(builders), subs_covered, asof))
    print("injected QMI into index.html")


def write_change_report(before, store, app_homes):
    """What moved since the last run - the part a human actually needs to look at."""
    new, cut, up, gone = [], [], [], []
    for h in store["homes"]:
        prev = before.get(h["id"])
        if not prev:
            if not h.get("gone"):
                new.append(h)
            continue
        if prev.get("price") and h.get("price") and h["price"] != prev["price"]:
            (cut if h["price"] < prev["price"] else up).append((h, prev["price"]))
        if h.get("gone") and not prev.get("gone"):
            gone.append(h)

    def line(h, extra=""):
        return "- **%s** · %s · %s%s" % (
            h.get("plan") or "Home", h.get("community") or "-",
            ("$%s" % format(h["price"], ",")) if h.get("price") else "no price", extra)

    live = [h for h in app_homes if not h.get("gone") and not h.get("pointer")]
    out = ["# Ready Now refresh - %s" % C.today(), "",
           "%d homes live · %d ready now · %d builders · %d communities" % (
               len(live), sum(1 for h in live if h.get("status") == "ready"),
               len({h["builder"] for h in live}), len({h.get("subId") for h in live if h.get("subId")})),
           ""]
    if cut:
        out += ["## Price cuts (%d)" % len(cut)] +                [line(h, " · was $%s, **down $%s**" % (format(p, ","), format(p - h["price"], ",")))
                for h, p in sorted(cut, key=lambda x: x[0]["price"] - x[1])] + [""]
    if new:
        out += ["## New this run (%d)" % len(new)] + [line(h) for h in new[:60]] +                (["- ... and %d more" % (len(new) - 60)] if len(new) > 60 else []) + [""]
    if gone:
        out += ["## No longer listed (%d)" % len(gone)] + [line(h) for h in gone[:40]] + [""]
    if up:
        out += ["## Price increases (%d)" % len(up)] +                [line(h, " · was $%s" % format(p, ",")) for h, p in up] + [""]
    if not (cut or new or gone or up):
        out += ["No changes since the last run."]

    path = os.path.join(WORK, "apply_report.md")
    os.makedirs(WORK, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    if cut or new or gone:
        open(os.path.join(WORK, "HAS_CHANGES"), "w").close()
    print("changes: %d new · %d cut · %d increased · %d delisted -> %s"
          % (len(new), len(cut), len(up), len(gone), os.path.relpath(path, C.REPO)))


def cmd_import_inventory():
    """One-time: fold the older community-level INVENTORY table into the Ready Now store."""
    src = read_index()
    subs = {s["id"]: s for s in load_subs(src)}
    inv = load_inventory(src)
    asof = re.search(r'const PROMO_ASOF = "([^"]+)"', src)
    asof = asof.group(1) if asof else C.today()

    raw = []
    for sid, blk in inv.items():
        s = subs.get(sid)
        if not s:
            continue
        for h in blk.get("standingHomes") or []:
            status, _ = C.parse_availability(h.get("status") or "Move-in ready")
            street, city, zc = C.parse_addr(s.get("address"))
            raw.append({
                "builder": s["builder"], "community": s["sub"], "collection": "",
                "region": s.get("area"), "salesOffice": s.get("address"), "salesPhone": None,
                "plan": h.get("plan"), "facing": None,
                "address": h.get("homesite") or None, "city": city, "zip": zc,
                "isModel": False, "age55": False,
                "sqft": h.get("sqft"), "beds": h.get("beds"), "baths": h.get("baths"),
                "stories": None, "garage": None,
                "availLabel": h.get("status") or "Move-in ready", "price": h.get("price"),
                "url": h.get("url"), "source": "builder site", "asOf": asof,
                "_sub": sid,
            })
    # these already know their subdivision, so skip the matcher
    recs = normalize(raw, load_subs(src), C.load_json(C.ALIASES, {}))
    for r, o in zip(recs, raw):
        r["subId"] = o["_sub"]
        r["match"] = "inventory"
        s = subs[o["_sub"]]
        r["area"], r["mp"], r["type"] = s.get("area"), s.get("mp") or "", s.get("type")
        r["es"], r["ms"], r["hs"] = s.get("es") or "", s.get("ms") or "", s.get("hs") or ""
        r["lat"], r["lng"], r["geoPrecision"] = s.get("lat"), s.get("lng"), "community"

    store = C.load_json(C.STORE, {"homes": []})
    have = {h["id"] for h in store["homes"]}
    added = [r for r in recs if r["id"] not in have]
    store = merge_store(store, recs)
    C.save_json(C.STORE, store)
    print("imported %d homes from INVENTORY (%d new) across %d communities"
          % (len(recs), len(added), len({r['subId'] for r in recs})))
    print("run: python tools/build_readynow.py apply")


def cmd_status():
    store = C.load_json(C.STORE, {"homes": []})
    homes = store.get("homes", [])
    live = [h for h in homes if not h.get("gone")]
    print("store updated %s · %d homes (%d live, %d gone)"
          % (store.get("updated", "-"), len(homes), len(live), len(homes) - len(live)))
    by = {}
    for h in live:
        by.setdefault(h["builder"], []).append(h)
    for b in sorted(by):
        hs = by[b]
        ready = sum(1 for h in hs if h.get("status") == "ready")
        print("  %-22s %3d homes · %3d ready now · %2d communities"
              % (b, len(hs), ready, len({h.get("subId") for h in hs})))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "parse":
        cmd_parse(rest)
    elif cmd == "geocode":
        cmd_geocode()
    elif cmd == "apply":
        cmd_apply()
    elif cmd == "build":
        cmd_parse(rest)
        cmd_geocode()
        cmd_apply()
    elif cmd == "import-inventory":
        cmd_import_inventory()
    elif cmd == "status":
        cmd_status()
    else:
        raise SystemExit("unknown command: %s" % cmd)


if __name__ == "__main__":
    main()
