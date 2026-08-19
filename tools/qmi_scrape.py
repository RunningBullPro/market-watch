#!/usr/bin/env python3
"""Weekly Ready Now web pull: builder QMI listing pages -> normalized raw rows.

There is no central feed for quick move-in inventory, so this walks each builder's own
site. It uses Monid's `context.dev /web/extract`, which crawls from a starting URL and
returns JSON matching a schema we hand it - one extractor for every builder instead of a
scraper per site. `factCheck: true` keeps every value grounded in text on the page, so a
field the builder does not publish comes back null rather than invented.

  python tools/qmi_scrape.py                       # every builder in feeds.json
  python tools/qmi_scrape.py "Toll Brothers"       # one builder
  python tools/qmi_scrape.py --fresh               # ignore the provider's cache

Output: tools/_work/readynow/web/<builder>.json, picked up by the web_qmi parser adapter:

  python tools/build_readynow.py parse tools/_work/readynow/web/*.json
  python tools/build_readynow.py geocode && python tools/build_readynow.py apply

Requires the Monid CLI on PATH with an active key (MONID_API_KEY in CI).
"""
import sys, os, re, json, time, shutil, subprocess, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from readynow import common as C

WORK = os.path.join(C.REPO, "tools", "_work", "readynow")
OUTDIR = os.path.join(WORK, "web")
FEEDS = os.path.join(C.RN_DIR, "feeds.json")
REPORT = os.path.join(WORK, "web_report.md")

# context.dev reliably returns 8 schema fields per call; 10 pushes the extraction past the
# gateway timeout and comes back as an HTML error page. So every builder is read twice with
# complementary 8-field shapes and the two passes are joined on address + plan + price.
# Crawl shallow: maxPages 3 kept Toll Brothers' listing page intact (42 homes) where
# maxPages 12 wandered onto individual home pages and returned 11.

def _schema(fields):
    types = {"price": "number", "wasPrice": "number", "sqft": "number", "beds": "number",
             "baths": "number", "stories": "number", "garage": "number"}
    props = {f: {"type": types.get(f, "string")} for f in fields}
    return {"type": "object", "required": ["homes"], "properties": {"homes": {
        "type": "array", "items": {"type": "object", "properties": props}}}}


PASS_A = ["community", "plan", "address", "price", "sqft", "beds", "baths", "availability"]
PASS_B = ["community", "plan", "address", "price", "wasPrice", "garage", "stories", "url"]

INSTRUCT_A = (
    "List every quick move-in / move-in-ready home this builder offers in the Las Vegas, "
    "Nevada metro (incl. Henderson, North Las Vegas, Summerlin, Boulder City, Mesquite, "
    "Pahrump). One entry per individual home, never per community or per floor plan. Copy "
    "price, sq ft, beds, baths, street address and availability exactly as published."
)
INSTRUCT_B = (
    "List every quick move-in / move-in-ready home this builder offers in the Las Vegas, "
    "Nevada metro. One entry per individual home. Set wasPrice ONLY when the page shows a "
    "previous, original or crossed-out price. url is the link to that home's own listing."
)


def monid_extract(url, schema, instructions, fresh=False, max_pages=3):
    body = {
        "url": url,
        "schema": schema,
        "instructions": instructions,
        "factCheck": True,
        "maxPages": max_pages,
        "maxAgeMs": 0 if fresh else 21600000,      # 6h cache unless --fresh
    }
    # Leave waitForMs / stopAfterMs / timeoutMS at the provider defaults: raising them makes
    # the gateway answer with an HTML error page instead of JSON.
    os.makedirs(WORK, exist_ok=True)
    bpath = os.path.join(WORK, "extract_body.json")
    opath = os.path.join(WORK, "extract_out.json")
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(body, f)
    if os.path.exists(opath):
        os.remove(opath)
    # On Windows the CLI is monid.cmd; subprocess needs the resolved path either way.
    exe = shutil.which("monid") or shutil.which("monid.cmd") or "monid"
    try:
        subprocess.run([exe, "run", "-p", "context.dev", "-e", "/web/extract",
                        "-f", bpath, "-w", "240", "-o", opath],
                       capture_output=True, timeout=360,
                       env={**os.environ, "NO_COLOR": "1", "MSYS_NO_PATHCONV": "1"})
    except Exception as e:
        return None, "monid error: %s" % e
    if not os.path.exists(opath):
        return None, "no output (fetch failed or CLI crashed)"
    try:
        with open(opath, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None, "unparseable response"
    node = d.get("data", d)
    for key in ("result", "data", "output", "extraction"):
        if isinstance(node, dict) and key in node and isinstance(node[key], dict):
            node = node[key]
    homes = node.get("homes") if isinstance(node, dict) else None
    if homes is None:
        return None, "no 'homes' array in response"
    return homes, None


def join_key(h):
    """Identify the same home across the two extraction passes."""
    return (C.nrm(h.get("address")), C.nrm(h.get("plan")), C.money(h.get("price")) or 0)


def merge_passes(a_rows, b_rows):
    """Pass A carries the specs, pass B the price history and links. Join, do not stack."""
    merged = {}
    for h in a_rows or []:
        if isinstance(h, dict):
            merged.setdefault(join_key(h), {}).update({k: v for k, v in h.items() if v not in (None, "")})
    for h in b_rows or []:
        if not isinstance(h, dict):
            continue
        k = join_key(h)
        if k in merged:
            for f, v in h.items():
                if v not in (None, "") and merged[k].get(f) in (None, ""):
                    merged[k][f] = v
        else:
            merged[k] = {f: v for f, v in h.items() if v not in (None, "")}
    return list(merged.values())


def community_urls(builder, limit):
    """Each of this builder's community pages, from the Market Watch dataset."""
    import build_readynow as B
    seen, out = set(), []
    for s in B.load_subs():
        if C.nrm(s.get("builder")) != C.nrm(builder):
            continue
        w = (s.get("website") or "").strip()
        if w.startswith("http") and w not in seen:
            seen.add(w)
            out.append((s["sub"], w))
    return out[:limit]


def pull_builder(builder, feed, fresh=False):
    """Two complementary extractions, joined into one set of homes."""
    url = feed["start"]
    pages = feed.get("maxPages", 3)
    a, err_a = attempt(url, PASS_A, INSTRUCT_A, fresh, pages)
    if err_a:
        return None, "core pass: %s" % err_a
    b, err_b = attempt(url, PASS_B, INSTRUCT_B, fresh, pages)
    if err_b:                       # the detail pass is optional; specs alone are still useful
        print("   (detail pass unavailable: %s)" % err_b)
        b = []
    homes = merge_passes(a, b)
    if homes or not feed.get("perCommunity"):
        return homes, None

    # Some builders (Beazer, Lennar, Richmond) never list inventory on a metro landing page -
    # it only exists on each community's own page. Slower and one call per community, so it
    # runs only for builders flagged perCommunity, and only when the landing pull came up dry.
    subs = community_urls(builder, feed.get("communityLimit", 12))
    print("   landing page had none; trying %d community pages" % len(subs))
    for name, curl in subs:
        rows, err = attempt(curl, PASS_A, INSTRUCT_A, fresh, 2, tries=1)
        if err or not rows:
            continue
        for r in rows:
            if isinstance(r, dict):
                r.setdefault("community", name)
        homes += rows
        print("      %-34s %d" % (name[:34], len(rows)))
    return merge_passes(homes, []), None


def attempt(url, fields, instructions, fresh, pages, tries=2):
    """The CLI intermittently dies mid-run on Windows; one retry clears it."""
    err = None
    for i in range(tries):
        rows, err = monid_extract(url, _schema(fields), instructions, fresh=fresh, max_pages=pages)
        if not err:
            return rows, None
        if i + 1 < tries:
            time.sleep(4)
    return None, err


def to_raw(builder, homes, feed):
    """Extractor output -> the raw-record contract the build script consumes."""
    today = C.today()
    out = []
    for h in homes or []:
        if not isinstance(h, dict):
            continue
        price = C.money(h.get("price"))
        addr = (h.get("address") or "").strip()
        if not price and not addr:
            continue
        street, city, zc = C.parse_addr(addr)
        out.append({
            "builder": builder,
            "community": (h.get("community") or "").strip(),
            "collection": "",
            "region": None,
            "salesOffice": None,
            "salesPhone": None,
            "plan": (h.get("plan") or "").strip() or None,
            "series": "",
            "facing": None,
            "address": street,
            "city": city or (h.get("city") or "").strip() or None,
            "zip": zc or (h.get("zip") or "").strip() or None,
            "homesite": (h.get("homesite") or "").strip() or None,
            "isModel": bool(re.search(r"\bmodel\b", (h.get("plan") or "") + " " +
                                      (h.get("availability") or ""), re.I)),
            "age55": False,
            "sqft": C.num(h.get("sqft")),
            "beds": C.num(h.get("beds")),
            "baths": C.num(h.get("baths")),
            "stories": C.num(h.get("stories")),
            "garage": C.num(h.get("garage")),
            "availLabel": (h.get("availability") or "").strip() or "Move-in ready",
            "price": price,
            "wasPrice": C.money(h.get("wasPrice")),
            "incentive": (h.get("incentive") or "").strip() or None,
            "url": (h.get("url") or "").strip() or feed.get("start"),
            "source": "builder site",
            "sourceDoc": feed.get("start"),
            "asOf": today,
        })
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fresh = "--fresh" in sys.argv
    feeds = C.load_json(FEEDS, {})
    feeds = {k: v for k, v in feeds.items() if not k.startswith("_")}
    if args:
        want = {C.nrm(a) for a in args}
        feeds = {k: v for k, v in feeds.items() if C.nrm(k) in want}
        if not feeds:
            raise SystemExit("no feed matches %s (see tools/readynow/feeds.json)" % args)

    os.makedirs(OUTDIR, exist_ok=True)
    lines = ["# Ready Now web pull - %s" % C.today(), ""]
    total = 0
    for builder, feed in sorted(feeds.items()):
        url = feed.get("start")
        if not url or feed.get("skip"):
            lines.append("- **%s** - skipped (%s)" % (builder, feed.get("skip") or "no start url"))
            continue
        print("%-22s %s" % (builder, url))
        homes, err = pull_builder(builder, feed, fresh=fresh)
        if err:
            print("   ! %s" % err)
            lines.append("- **%s** - FAILED: %s" % (builder, err))
            continue
        raw = to_raw(builder, homes, feed)
        C.save_json(os.path.join(OUTDIR, "%s.json" % C.slug(builder)), raw)
        total += len(raw)
        priced = sum(1 for r in raw if r["price"])
        cuts = sum(1 for r in raw if r.get("wasPrice") and r["price"] and r["wasPrice"] > r["price"])
        print("   %d homes (%d priced, %d showing a reduction)" % (len(raw), priced, cuts))
        lines.append("- **%s** - %d homes, %d priced, %d reduced" % (builder, len(raw), priced, cuts))

    lines += ["", "Total: **%d** homes across %d builders." % (total, len(feeds)), "",
              "Next: `python tools/build_readynow.py parse tools/_work/readynow/web/*.json`"]
    C.save_json(os.path.join(WORK, "_web_pull.json"), {"at": C.today(), "homes": total})
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n%d homes total -> %s" % (total, os.path.relpath(OUTDIR, C.REPO)))


if __name__ == "__main__":
    main()
