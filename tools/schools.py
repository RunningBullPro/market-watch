#!/usr/bin/env python3
"""Assign CCSD zoned schools to every subdivision + geocode those schools for the map.

1. Point-in-polygon each subdivision's lat/lng against Clark County NV's public ArcGIS
   attendance-zone layers (elementary / middle / high) -> injects es/ms/hs onto SUBS.
2. Batch-geocodes the schools referenced by our subdivisions (US Census) -> injects a
   compact SCHOOLS_GEO {es/ms/hs: {name:[lat,lng]}} for the map's school toggle.

Idempotent. Run AFTER `build_dataset.py apply` so new communities get zoned:

    python tools/schools.py

Requires: shapely, requests
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
            info[nm] = ((p.get("ADDRESS") or "").strip(), (p.get("CITY") or "").strip(),
                        str(p.get("ZIPCODE") or "").strip())
    return polys, names, STRtree(polys), info

def census_batch(rows):
    """rows: list of (id, street, city, state, zip) -> {id: (lat,lng)}"""
    buf = io.StringIO(); w = csv.writer(buf)
    for r in rows: w.writerow(r)
    out = {}
    try:
        resp = requests.post("https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
                             files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")},
                             data={"benchmark": "Public_AR_Current"}, timeout=180)
        for row in csv.reader(io.StringIO(resp.text)):
            if len(row) >= 6 and row[2] == "Match":
                lon, lat = row[5].split(",")
                out[row[0]] = (round(float(lat), 6), round(float(lon), 6))
    except Exception as e:
        print("census batch error:", e)
    return out

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

    # geocode the schools our subdivisions actually reference
    rows, idmap = [], {}
    for k in LAYERS:
        info = zones[k][3]
        refs = sorted({s[k] for s in SUBS if s.get(k)})
        for nm in refs:
            a = info.get(nm)
            if a and a[0]:
                rid = f"{k}|{nm}"; idmap[rid] = (k, nm)
                rows.append((rid, a[0], a[1] or "Las Vegas", "NV", a[2] or ""))
    geo = census_batch(rows) if rows else {}
    SCHOOLS_GEO = {"es": {}, "ms": {}, "hs": {}}
    for rid, (lat, lng) in geo.items():
        k, nm = idmap[rid]; SCHOOLS_GEO[k][nm] = [lat, lng]

    clean = [{k: s.get(k, "") for k in bd.KEYS} for s in SUBS]
    html = re.sub(r"const SUBS = \[.*?\}\];",
                  ("const SUBS = " + json.dumps(clean, ensure_ascii=False, separators=(", ", ": ")) + ";").replace("\\", "\\\\"),
                  html, count=1, flags=re.S)
    html = re.sub(r"const SCHOOLS_GEO = \{.*?\};",
                  ("const SCHOOLS_GEO = " + json.dumps(SCHOOLS_GEO, ensure_ascii=False, separators=(",", ":")) + ";").replace("\\", "\\\\"),
                  html, count=1)
    open(INDEX, "w", encoding="utf-8", newline="").write(html)

    print(f"\nzoned {geocoded} subs: ES {cov['es']} | MS {cov['ms']} | HS {cov['hs']}")
    print(f"school pins geocoded: ES {len(SCHOOLS_GEO['es'])} | MS {len(SCHOOLS_GEO['ms'])} | HS {len(SCHOOLS_GEO['hs'])} (of {len(rows)} requested)")
    if unzoned: print(f"outside CCSD [{len(unzoned)}]: " + ", ".join(unzoned))
    print("Injected schools + SCHOOLS_GEO into index.html.")

if __name__ == "__main__":
    main()
