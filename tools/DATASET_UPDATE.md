# Marketwatch weekly dataset update — runbook

Marketwatch is refreshed **weekly** from a Home Builders Research **"Weekly Traffic & Sales Watch" (WTSW)** Excel export covering the Las Vegas Valley (~224 communities, ~16 builders). This document is the repeatable process.

## 0. Inputs & principle
- **Source file:** `WTSW <YYMMDD>.xlsx` (e.g. `WTSW 260719.xlsx`).
- **Golden rule:** community **`id`s must stay stable** week to week so saved client profiles (which store frozen snapshots by id) keep lining up. Always carry an existing community's `id` forward on match; only mint new ids for genuinely new communities.
- Client snapshots are frozen, so a new dataset **never alters existing client reports** — new communities just become available to pick.

## 1. Read the workbook
- Sheet **`WTSW <date>`** holds the data: 24 columns, 1 header row. (The `SUMMARY` sheet is builder monthly totals — ignore.)
- **Exclude** every row where `SUBDIVISION == "Sub Totals"` (subtotal rows, not communities).

## 2. Column mapping (HBR → app field)
| HBR column | App field | Notes |
|---|---|---|
| BUILDER | builder | |
| SUBDIVISION | sub | title-case |
| AREA | area (code) | map to name, see below — **primary lens** |
| PROD TYPE | type | `DET`→Detached, `ATT`→Attached |
| STANDING UNSOLD INVNTRY | standing | |
| UN SOLD LOTS | unsold | |
| YR NET SOLD | ytd | |
| TOTAL LOT | totalLot | |
| TRAFFIC | traffic | weekly |
| OPEN SINCE | opened | MM/DD/YYYY |
| MIN SQFT / MAX SQFT | minSqft / maxSqft | |
| LOW PRICE / HIGH PRICE | lo / hi | parse `"$ 504990"`→`504990`; blank → `null` ("coming soon") |
| MASTER PLAN | mp | secondary tag/filter |

Unused-but-available columns (sales velocity): NET SLS PER MO SINCE OPEN, NET SLS LAST 3 MO, etc. — can be surfaced later.

## 3. Area codes (area = primary lens: pin colors, legend, charts)
`NW`=Northwest · `H`=Henderson · `SW`=Southwest · `NLV`=North Las Vegas · `SO`=South · `E`=East · `BC`=Boulder City · `MSQ`=Mesquite · `P`=Pahrump.
(Client includes Mesquite & Pahrump even though they're outside the valley proper.) Occasionally the HBR area code disagrees with the geocoded ZIP (e.g. a community coded NW that sits in an SW ZIP) — trust the geocode for the map pin; keep the HBR area for the filter unless clearly wrong.

## 4. Carry-over vs. enrichment
1. Match each raw community to the **previous** dataset by normalized `builder+name` (then name-only). On match: **reuse `id` + address, lat, lng, mapUrl, website, webDomain, webType, geoNote**; refresh the live metrics (price, sqft, standing, unsold, ytd, traffic, totalLot, opened, type, area, mp) from the new file.
2. Everything else is **new** and needs enrichment (address, lat/lng, website, mapUrl, geoNote).

## 5. Enrichment (the heavy step) — for NEW communities only
Run **builder-batched research** (one research subagent per builder, split builders with >~12 into batches of ≤10). Each agent, per community, finds: `address` (street, city, NV, ZIP from the official builder site), `website` (community page), `webDomain`, `webType`, `lat`/`lng`, `geoNote` ("Sales office"), `confidence` (high/medium/low), `source`.

**Geocoding gotchas (learned the hard way):**
- **OSM Nominatim is IP-blocked (HTTP 429)** from this environment. Use the **US Census batch geocoder** (`https://geocoding.geo.census.gov/geocoder/locations/addressbatch`) + **Photon/komoot** + coords embedded in **Redfin JSON** or builder pages.
- Builder sites often block bots: **lennar.com returns 403**; **Richmond American** pages are JS-rendered with no coords in the HTML. Fall back to search results / aggregators.
- **Brand-new construction streets** are frequently absent from every geocoder → approximate from cross-streets/ZIP and mark **medium/low**.
- Many phases/collections **share one sales office** → reuse that address+coords for the set.

**Builder brand nuances:**
- **Sekisui House US** builds as **Woodside Homes** `"(WH)"` (woodsidehomes.com) and **Richmond American** `"(RAH)"` (richmondamerican.com). **Shawood** = Sekisui's luxury line (shawood.com).
- **Pulte Group** = Pulte (pulte.com), **Del Webb** `"(DW)"` (delwebb.com), **American West** `"(AW)"` (americanwesthomes.com), Centex.
- **Toll Brothers** division **StoryBook Homes** `"(SBH)"` (storybooknewhomes.com).
- **Summit Homes** = summithomesnv.com. DR Horton, KB, Lennar, Century, Beazer, Taylor Morrison, Tri Pointe, Touchstone (touchstoneliving.com), Pinnacle (pinnaclelv.com), Signature (signaturehomes.com) are straightforward.

## 6. Verify centrally
- Batch-geocode all enriched addresses via US Census; where it resolves and differs materially from the agent coord, prefer Census.
- Assert every coord is within Southern Nevada (lat 35.0–37.2, lng −116.6 to −113.9) and near its area center.
- Produce a **review list** of all medium/low-confidence and any out-of-bounds/area-mismatch rows for a human spot-check before shipping.

## 7. Build & ship
- `python tools/build_dataset.py apply` injects `SUBS` into index.html + bumps BUILD_DATE / footer week-ending.
- `python tools/schools.py` assigns CCSD zoned schools (`es`/`ms`/`hs`) to every subdivision via
  point-in-polygon against Clark County NV ArcGIS attendance boundaries, and geocodes the referenced
  schools for the map's school toggle (`SCHOOLS_GEO`). Idempotent; auto-zones new communities. (es/ms/hs
  also carry over week to week via build_dataset, so this only strictly needs re-running when coords change.)
- Run `python tools/verify.py` (Playwright browser suite) — must be green.
- Commit + push `main`; GitHub Pages auto-deploys.

## Typical weekly effort
Carry-over is instant; only the **new** communities need research. A full valley refresh with mostly-new data is a large one-time enrichment; subsequent weeks are mostly carry-over + a handful of new communities.
