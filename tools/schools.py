#!/usr/bin/env python3
"""Assign CCSD zoned schools to every subdivision, geocode them, and attach star ratings.

1. Point-in-polygon each subdivision (lat/lng) against Clark County NV public ArcGIS
   attendance-zone layers (elementary/middle/high) -> injects es/ms/hs onto SUBS.
2. Geocodes the referenced schools (US Census batch, Photon fallback) -> SCHOOLS_GEO.
3. Matches Nevada School Performance Framework (NSPF) star ratings (official, free) by
   name+level -> SCHOOL_RATING {es/ms/hs: {name: stars}}.

Idempotent. Run AFTER `build_dataset.py apply`.  Requires: shapely, requests, pdfplumber.
"""
import os, sys, re, json, io, csv, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dataset as bd
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO, "index.html")
BASE = "https://maps.clarkcountynv.gov/arcgis/rest/services/OpenData/Education/MapServer"
LAYERS = {"es": 4, "ms": 5, "hs": 6}
NSPF_PDF = "https://webapp-strapi-paas-prod-nde-001.azurewebsites.net/uploads/2025_star_ratings_36dc74df94.pdf"
STOP = set("elementary middle junior high school es ms jhs hs dr jr sr the and of at a".split())

def toks(name):
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split() if w and w not in STOP}

def fetch_zone(lid):
    r = requests.get(f"{BASE}/{lid}/query", params={
        "where": "1=1", "outFields": "SCHOOL,FULLNAME,ADDRESS,CITY,ZIPCODE",
        "outSR": 4326, "f": "geojson", "resultRecordCount": 1000}, timeout=90).json()
    polys, names, info = [], [], {}
    for ft in r.get("features", []):
        g = ft.get("geometry")
        if not g: continue
        try: geom = shape(g)
        except Exception: continue
        if not geom.is_valid: geom = geom.buffer(0)
        p = ft.get("properties", {})
        nm = (p.get("SCHOOL") or "").strip() or (p.get("FULLNAME") or "").strip()
        polys.append(geom); names.append(nm)
        if nm and nm not in info:
            info[nm] = {"addr": (p.get("ADDRESS") or "").strip(), "city": (p.get("CITY") or "").strip(),
                        "zip": str(p.get("ZIPCODE") or "").strip(), "full": (p.get("FULLNAME") or "").strip()}
    return polys, names, STRtree(polys), info

def census_batch(rows):
    buf = io.StringIO(); [csv.writer(buf).writerow(r) for r in rows]
    out = {}
    try:
        resp = requests.post("https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
                             files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")},
                             data={"benchmark": "Public_AR_Current"}, timeout=180)
        for row in csv.reader(io.StringIO(resp.text)):
            if len(row) >= 6 and row[2] == "Match":
                lon, lat = row[5].split(","); out[row[0]] = (round(float(lat), 6), round(float(lon), 6))
    except Exception as e:
        print("census batch error:", e)
    return out

def norm_full(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def fetch_campuses():
    """CCSD Campuses layer -> {normalized FULLNAME: [lat,lng]} (authoritative campus centroids)."""
    r = requests.get(f"{BASE}/1/query", params={"where": "1=1", "outFields": "FULLNAME",
                     "outSR": 4326, "f": "geojson", "resultRecordCount": 2000}, timeout=90).json()
    out = {}
    for ft in r.get("features", []):
        g = ft.get("geometry"); full = ((ft.get("properties") or {}).get("FULLNAME") or "").strip()
        if not g or not full: continue
        try: c = shape(g).representative_point()
        except Exception: continue
        out[norm_full(full)] = [round(c.y, 6), round(c.x, 6)]
    return out

def census_one(addr):
    try:
        r = requests.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                         params={"address": addr, "benchmark": "Public_AR_Current", "format": "json"}, timeout=30).json()
        m = r["result"]["addressMatches"]
        if m: c = m[0]["coordinates"]; return round(c["y"], 6), round(c["x"], 6)
    except Exception: pass
    return None

def photon(q):
    try:
        r = requests.get("https://photon.komoot.io/api/", params={"q": q, "limit": 1, "lat": 36.1, "lon": -115.2},
                         timeout=30, headers={"User-Agent": "marketwatch-geocode"}).json()
        f = r.get("features") or []
        if f:
            lon, lat = f[0]["geometry"]["coordinates"]
            if 35.5 <= lat <= 36.9 and -116.2 <= lon <= -114.3:
                return round(lat, 6), round(lon, 6)
    except Exception as e:
        print("photon error:", e)
    return None

def nspf_ratings():
    """Return {'es'|'ms'|'hs': [ (tokenset, stars) ]} parsed from the NSPF PDF."""
    import pdfplumber
    b = requests.get(NSPF_PDF, timeout=120).content
    by = {"es": [], "ms": [], "hs": []}
    row_re = re.compile(r"Clark County School\s+[\d.]+\s+(.+?)\s+(\d|Not Rated)\s*$")
    with pdfplumber.open(io.BytesIO(b)) as pdf:
        for pg in pdf.pages:
            for line in (pg.extract_text() or "").splitlines():
                m = row_re.search(line)
                if not m: continue
                nm, rat = m.group(1).strip(), m.group(2)
                nl = nm.lower()
                if "junior high" in nl or "middle school" in nl or re.search(r"\b(jhs|ms)\b", nl): lv = "ms"
                elif "high school" in nl or re.search(r"\b(hs|high)\b", nl): lv = "hs"
                elif "elementary" in nl or re.search(r"\bes\b", nl): lv = "es"
                else: continue
                by[lv].append((toks(nm), int(rat) if rat.isdigit() else 0))
    return by

def best_rating(cand, rated):
    best, bestscore = 0, 0
    for tk, stars in rated:
        sc = len(cand & tk)
        if sc > bestscore and sc >= 1: best, bestscore = stars, sc
    return best if bestscore else None

def main():
    zones = {k: fetch_zone(lid) for k, lid in LAYERS.items()}
    for k in LAYERS: print(f"  {k}: {len(zones[k][0])} zones")

    html = open(INDEX, encoding="utf-8").read()
    SUBS = json.loads(re.search(r"const SUBS = (\[.*?\}\]);", html, re.S).group(1))

    def zone_of(k, lat, lng):
        polys, names, tree, _ = zones[k]
        pt = Point(lng, lat)
        for i in tree.query(pt):
            if polys[i].contains(pt): return names[i]
        return ""

    cov = {"es": 0, "ms": 0, "hs": 0}; geocoded = 0; unzoned = []
    for s in SUBS:
        lat, lng = s.get("lat"), s.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            geocoded += 1
            for k in LAYERS:
                v = zone_of(k, lat, lng); s[k] = v
                if v: cov[k] += 1
            if not (s.get("es") or s.get("hs")): unzoned.append(s["sub"] + " (" + s.get("area", "") + ")")
        else:
            for k in LAYERS: s[k] = s.get(k, "") or ""

    # school pins: authoritative CCSD Campus centroids (matched by FULLNAME); geocode is fallback
    campuses = fetch_campuses()
    SCHOOLS_GEO = {"es": {}, "ms": {}, "hs": {}}
    fb = []
    for k in LAYERS:
        info = zones[k][3]
        for nm in sorted({s[k] for s in SUBS if s.get(k)}):
            a = info.get(nm) or {}
            c = campuses.get(norm_full(a.get("full", ""))) or campuses.get(norm_full(nm))
            if c: SCHOOLS_GEO[k][nm] = c
            elif a.get("addr"): fb.append((k, nm, a))
    if fb:
        geo = census_batch([(f"{k}|{nm}", a["addr"], a["city"] or "Las Vegas", "NV", a["zip"] or "") for k, nm, a in fb])
        for k, nm, a in fb:
            fa = f"{a['addr']}, {a['city'] or 'Las Vegas'}, NV {a['zip']}"
            p = geo.get(f"{k}|{nm}") or census_one(fa) or photon(f"{a['full'] or nm} School, {fa}") or photon(fa)
            if p: SCHOOLS_GEO[k][nm] = [p[0], p[1]]

    # NSPF star ratings, matched by name+level
    try:
        rated = nspf_ratings()
    except Exception as e:
        print("NSPF parse failed:", e); rated = {"es": [], "ms": [], "hs": []}
    SCHOOL_RATING = {"es": {}, "ms": {}, "hs": {}}
    for k in LAYERS:
        info = zones[k][3]
        for nm in sorted({s[k] for s in SUBS if s.get(k)}):
            cand = toks(nm) | toks((info.get(nm) or {}).get("full", ""))
            r = best_rating(cand, rated[k])
            if r: SCHOOL_RATING[k][nm] = r

    clean = [{k: s.get(k, "") for k in bd.KEYS} for s in SUBS]
    html = re.sub(r"const SUBS = \[.*?\}\];",
                  ("const SUBS = " + json.dumps(clean, ensure_ascii=False, separators=(", ", ": ")) + ";").replace("\\", "\\\\"),
                  html, count=1, flags=re.S)
    html = re.sub(r"const SCHOOLS_GEO = \{.*?\};",
                  ("const SCHOOLS_GEO = " + json.dumps(SCHOOLS_GEO, ensure_ascii=False, separators=(",", ":")) + ";").replace("\\", "\\\\"), html, count=1)
    html = re.sub(r"const SCHOOL_RATING = \{.*?\};",
                  ("const SCHOOL_RATING = " + json.dumps(SCHOOL_RATING, ensure_ascii=False, separators=(",", ":")) + ";").replace("\\", "\\\\"), html, count=1)
    open(INDEX, "w", encoding="utf-8", newline="").write(html)

    tot = lambda d: sum(len(d[k]) for k in d)
    ref = {k: len({s[k] for s in SUBS if s.get(k)}) for k in LAYERS}
    print(f"\nzoned {geocoded} subs: ES {cov['es']} | MS {cov['ms']} | HS {cov['hs']}")
    print(f"pins: ES {len(SCHOOLS_GEO['es'])}/{ref['es']} | MS {len(SCHOOLS_GEO['ms'])}/{ref['ms']} | HS {len(SCHOOLS_GEO['hs'])}/{ref['hs']}")
    print(f"ratings: ES {len(SCHOOL_RATING['es'])}/{ref['es']} | MS {len(SCHOOL_RATING['ms'])}/{ref['ms']} | HS {len(SCHOOL_RATING['hs'])}/{ref['hs']}")
    for k in LAYERS:
        miss_pin = sorted({s[k] for s in SUBS if s.get(k)} - set(SCHOOLS_GEO[k]))
        miss_rat = sorted({s[k] for s in SUBS if s.get(k)} - set(SCHOOL_RATING[k]))
        if miss_pin: print(f"  {k} no pin: {miss_pin}")
        if miss_rat: print(f"  {k} no rating: {miss_rat}")
    ex = list(SCHOOL_RATING["hs"].items())[:6]
    print("sample HS ratings:", ex)
    print("Injected schools + SCHOOLS_GEO + SCHOOL_RATING into index.html.")

if __name__ == "__main__":
    main()
