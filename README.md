# Buyer2Builder - Market Watch

A new-home market-intelligence web app for the **entire Las Vegas Valley**. Market Watch
lets an agent explore every actively-selling new-home subdivision by area of town, track
builder incentives and move-in-ready inventory, and generate branded, client-ready reports.

**Live:** https://runningbullpro.github.io/market-watch

Data source: Home Builders Research *Weekly Traffic & Sales Watch* (WTSW), refreshed weekly.
Current coverage: **224 subdivisions across 16 builders and 9 areas of town.**

## What it does

- **Area of town is the primary lens** - Northwest, Henderson, Southwest, North Las Vegas,
  South, East, Boulder City, Mesquite, and Pahrump - with secondary filters for builder,
  master plan, home type, price, square footage, and standing inventory.
- **Ask in plain English** - e.g. `townhomes 350 to 400k`, `detached under 500k in Cadence`,
  `Lennar available`, `2000 to 3000 sq ft`. The query bar turns phrases into filter chips.
- **Six views** - Cards, sortable Table, an interactive **Map** (Leaflet + OpenStreetMap with a
  cluster/individual-pin toggle), clickable **Charts** (click a bar to filter to those
  subdivisions), an **Incentives** report, and a standing-**Inventory** report.
- **Incentives & standing inventory** - builder-wide and community-specific offers, covering both
  financial incentives (rate buydowns, closing-cost credits, down-payment assistance, flex cash,
  price cuts) and non-financial ones (design/appliance/upgrade credits, included solar, military
  and first-responder programs, grand-opening and model-home offers). Every offer links to the
  builder's actual published offer page. Refreshed twice weekly.
- **Subdivision detail** - click any subdivision for an at-a-glance panel, an OSM map snapshot,
  builder offers and move-in-ready homes, a star rating, sales contact, builder incentives, and
  site-visit notes. Each annotation is stamped with who added it and when.
- **Client profiles** - create a client (name, phone, email, notes), add selected communities to
  the profile, and generate a branded, printable/PDF report with a map, numbered communities, and
  notes. Community data is **frozen as a snapshot** when added, so pushing a new dataset never
  changes a report you already delivered. CSV export included.
- **Ad-hoc selection** - check any subdivisions (no client needed) and produce a report or CSV.
- **Manual add-subdivision** - enter on-site finds through the grouped **+ Add** modal (shared
  community fields plus one row per subdivision). Manual entries are stored separately and
  **survive weekly dataset pushes**; location and website are researched and folded in afterward.

## Architecture

A single self-contained `index.html` (HTML / CSS / inline JS), deployed on GitHub Pages - no
build step. Map tiles come from OpenStreetMap; Leaflet and MarkerCluster load from unpkg with
Subresource Integrity. A Content-Security-Policy, an HTML-escaping layer, a URL allow-list, and
CSV-injection guards harden the client.

## Data persistence

Annotations, client profiles, manual subdivisions, and preferences are saved in the **browser**
(`localStorage`, namespaced `b2b_watch_v1`) on the device in use. When previewed inside a sandbox
that disables storage, the app runs in-memory and shows a banner; on the hosted site it persists
normally. Client-report snapshots are frozen and never re-resolved against a newer dataset.

## Deploy

`index.html` is the whole app. Commit to `main` and GitHub Pages redeploys automatically.
Bump `BUILD` / `BUILD_DATE` near the bottom of the file to confirm a live update.

## Tooling (`tools/`)

- `build_dataset.py` - weekly WTSW converter: parse the new HBR Excel, carry over enrichment
  (address, lat/lng, website, map URL), verify, and inject the refreshed `SUBS` array.
- `verify.py` - a Playwright suite (55/55) that drives the app headless and checks rendering,
  filtering, the map, client snapshots/reports, CSV, and manual-add push-safety.

Incentive and inventory data is gathered on the dev side (builder-site scraping) and injected as
the twice-weekly `PROMOS` / `INVENTORY` refresh. Raw HBR files and marketing assets are kept out
of the public repo (see `.gitignore`).

## Roadmap

- **Supabase team-sync** - move manual subdivisions and client profiles to Supabase so the whole
  team shares them with individual logins, while preserving frozen client snapshots and dataset
  integrity.
- **Automated twice-weekly incentive refresh** - scheduled scrape + rebuild + deploy, applying the
  full financial + non-financial incentive taxonomy and verifying every offer link resolves.
- **Full per-home quick-move-in detail** - richer standing-inventory listings as scraping coverage
  expands.

## Refresh cadence

- **Dataset (SUBS):** weekly, from the HBR WTSW file.
- **Incentives & inventory (PROMOS / INVENTORY):** twice weekly.
