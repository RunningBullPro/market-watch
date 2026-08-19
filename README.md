# Buyer2Builder - Market Watch

A new-home market-intelligence web app for the **entire Las Vegas Valley**. Market Watch
lets an agent explore every actively-selling new-home subdivision by area of town, track
builder incentives and move-in-ready inventory, and generate branded, client-ready reports.

**Live:** https://runningbullpro.github.io/market-watch

Two lenses, one shell:

- **Market Watch** - every actively-selling subdivision, at the community grain.
- **Ready Now** - standing inventory the builders need to move, at the **home** grain, with
  the pricing evidence for why a given home is a deal.

Data sources: Home Builders Research *Weekly Traffic & Sales Watch* (WTSW), refreshed weekly,
plus builder-published quick move-in drops (PDF and listing pages), refreshed weekly.
Current coverage: **230 subdivisions across 16 builders and 9 areas of town**, and
**331 quick move-in homes** (177 ready to move into today) across all 16 builders.

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

## Ready Now

A second lens on the same shell, one record per **home** instead of per subdivision.

- **The problem it solves.** No one publishes a valley-wide feed of quick move-in homes, and
  the weekly HBR file reports `standing: 0` for communities where the builder's own sheet
  lists dozens of finished houses. Ready Now assembles that inventory from the builders
  themselves.
- **The deal engine.** Builders publish a price, not a discount. Every home is benchmarked
  three ways - against the **same floor plan in the same community**, against that
  community's **$/sq ft**, and against its **area's** - then combined with any published
  price cut and its time on the list into a 0-100 deal score and plain-language badges
  ("$93k under the same plan here", "Builder cut $49k", "11% under this community's $/sq ft").
- **Price history.** Every refresh stamps each home's price per source, so a reduction is
  dated and provable. That is the one thing no public source offers for new construction.
- **Same everything else.** Shared price / sq ft / area / master plan / builder / school
  filters, plus beds, baths, stories, garage, availability and the deal filters. Cards,
  Table, Map (one pin per home, shaded by deal score), Charts, a ranked **Deals** report and
  a **Movers** report for cuts and aging.
- **Linked both ways.** Any subdivision with live inventory carries a **⚡ Ready Now** tag on
  its card, table row and detail panel; clicking it opens Ready Now filtered to that
  community. Every home links back to its community for traffic, YTD sold and schools.
- **Client-ready.** Homes go into the same client profiles as communities, frozen as
  snapshots, and print in the client report as their own numbered-map section.

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
- `build_readynow.py` - the Ready Now pipeline: parse a builder drop (PDF or scraped JSON)
  through a parser adapter, match each home to a subdivision, geocode it, merge into
  `tools/readynow/store.json` with price history, compute the deal benchmarks, and inject
  `QMI` into the app. `parse` / `geocode` / `apply`, plus `status` and `import-inventory`.
- `qmi_scrape.py` - the weekly web pull: every builder's quick move-in listings via Monid's
  `context.dev /web/extract` schema extractor, two complementary 8-field passes joined on
  address + plan + price. Feeds live in `tools/readynow/feeds.json`.
- `readynow/find_feeds.py` - finds a builder's real quick move-in URL when their pull dries up.
- `verify.py` - a Playwright suite (87/87) that drives the app headless and checks rendering,
  filtering, the map, client snapshots/reports, CSV, manual-add push-safety, and the whole
  Ready Now surface end to end.

Incentive and inventory data is gathered on the dev side (builder-site scraping) and injected as
the twice-weekly `PROMOS` / `INVENTORY` refresh. Raw HBR files and marketing assets are kept out
of the public repo (see `.gitignore`).

## Roadmap

- **Supabase team-sync** - move manual subdivisions and client profiles to Supabase so the whole
  team shares them with individual logins, while preserving frozen client snapshots and dataset
  integrity.
- **Automated twice-weekly incentive refresh** - scheduled scrape + rebuild + deploy, applying the
  full financial + non-financial incentive taxonomy and verifying every offer link resolves.
- **Ready Now coverage** - a handful of builders' sites still return nothing to the weekly
  pull (heavily JS-rendered or bot-protected). Those need a verified listing URL in
  `tools/readynow/feeds.json`, or a PDF drop from the builder.

## Refresh cadence

- **Dataset (SUBS):** weekly, from the HBR WTSW file.
- **Incentives (PROMOS):** twice weekly.
- **Ready Now (QMI):** weekly, via the `Ready Now weekly refresh` Action, which opens a pull
  request with the price cuts, new listings and delistings rather than pushing to the live
  site unreviewed. Standing inventory is now held in the Ready Now database; the Inventory
  view reads from it.
