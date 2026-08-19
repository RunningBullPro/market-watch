# Ready Now update — runbook

Ready Now is the **home-grain** half of Market Watch: builder-published quick move-in (QMI)
inventory, linked back to Market Watch subdivisions, with price history and derived deal
benchmarks. It refreshes **weekly**, separately from the WTSW dataset.

The full design rationale is in `docs/READYNOW_PLAN.md`. This is the repeatable process.

## 0. Inputs and the golden rules

Two kinds of input, both handled by the same pipeline through **parser adapters**:

| Input | Adapter | Example |
|---|---|---|
| A builder's division QMI sheet (PDF) | `readynow/parsers/taylor_morrison.py` | `ReadyNow/Ready September 2026 Division QMIs.pdf` |
| The weekly web pull (JSON) | `readynow/parsers/web_qmi.py` | `tools/_work/readynow/web/*.json` |

- **A home's id is `<builder code>-<street address>-<ZIP>`.** That is its identity, not a
  batch counter. The same house arriving from a PDF and from the builder's website must
  merge into one record, or the app shows it twice and the deal maths double-counts it.
- **Client snapshots are frozen.** A refresh never rewrites a home already saved to a client
  profile; the app flags "builder price is now X" instead.
- **Price history is per source.** A dated builder sheet and a live listing page disagreeing
  is not a price cut. Only two readings from the *same* source make one.

## 1. Weekly run

Automated: the **`Ready Now weekly refresh`** Action (Tuesdays 14:00 UTC) does all of this
and opens a pull request with the diff. Needs the `MONID_API_KEY` repo secret. To do it by
hand, or when a builder sends a PDF:

```bash
python tools/qmi_scrape.py                                    # every builder in feeds.json
python tools/build_readynow.py parse tools/_work/readynow/web/*.json \
                                     "ReadyNow/<any new builder PDF>"
python tools/build_readynow.py geocode
python tools/build_readynow.py apply
python tools/verify.py                                        # must be 87/87
```

Then review `tools/_work/readynow/apply_report.md` (cuts, new, delisted) and commit
`index.html`, `tools/readynow/store.json` and `tools/readynow/geocache.json`.

## 2. The web pull (`tools/qmi_scrape.py`)

Monid `context.dev /web/extract` crawls each builder's site from the `start` URL in
`tools/readynow/feeds.json` and returns JSON matching a schema we hand it, so there is one
extractor rather than a scraper per builder. `factCheck: true` keeps every value grounded in
page text; anything the builder does not publish comes back null instead of invented.

Three hard-won constraints — change them and the pull silently returns nothing:

- **8 schema fields per call, maximum.** Ten pushes the extraction past the gateway timeout
  and the CLI gets an HTML error page instead of JSON. Hence two passes per builder
  (`PASS_A` specs, `PASS_B` was-price/garage/stories/url) joined on address + plan + price.
- **Crawl shallow — `maxPages: 3`.** Toll Brothers returned 42 homes at 3 pages and 11 at 12,
  because a deeper crawl wanders off the listing page onto individual homes.
- **Leave `waitForMs` / `stopAfterMs` / `timeoutMS` unset.** Raising them triggers the same
  gateway HTML error.

On Windows the CLI needs `MSYS_NO_PATHCONV=1` (Git Bash rewrites `-e /web/extract` into a
filesystem path) and dies intermittently mid-run, so each pass retries once. CI runs Linux,
where it is stable.

## 3. When a builder returns 0 homes

Usually a dead `start` URL, not an empty inventory. Check it:

```bash
python tools/readynow/find_feeds.py "Beazer Homes"
```

and fix `tools/readynow/feeds.json`. Known-hard builders: **Lennar** is fully bot-protected;
**Richmond American** and **Summit** are JS-rendered; **Beazer** publishes QMIs only on
individual community pages (give it a higher `maxPages`). A builder that stays dry two weeks
running should be asked for a PDF drop instead — that is how Taylor Morrison works, and it is
the better source anyway.

## 4. Matching homes to subdivisions

`match_sub()` tries, in order: the curated alias map, an exact normalized name, the name with
`The …`/`… Collection`/`… at <master plan>` trimmed, an unambiguous containment, then the
shared sales-office address. Anything left over is **printed for a human, never guessed** —
it still ingests with `subId: null`, and its area is inferred from the ZIP so it stays
filterable and mappable.

When `parse` prints a loose match ("contains"/"office"), confirm it and pin it in
`tools/readynow/aliases.json` so it stops depending on fuzzy logic.

An unmatched community is often a real find: Pulte's **Cordora** is not in the HBR weekly
file at all, and its homes carry $71k–$79k builder discounts.

## 5. Geocoding

Per home address: US Census batch, then Census one-at-a-time, then OSM Nominatim (which the
batch index misses for brand-new construction streets — 41 of Taylor Morrison's 52 needed the
fallback). Results are cached in `tools/readynow/geocache.json`. Anything landing more than
5 km from its own sales office is rejected and the home sits on the community pin, drawn
hollow and labelled "approx." on the map. Never fake a rooftop.

## 6. The deal engine

Three benchmarks, most specific first, plus movement:

1. **Plan peers** — the same floor plan and sq ft in the same community. Works on the very
   first drop and it is the strongest signal.
2. **Community $/sq ft** — median of that community's other QMIs (needs 3+).
3. **Area $/sq ft** — median across the area of town (needs 5+).
4. **Price cut** — the builder's own was-price when published, otherwise a same-source
   change we watched happen.
5. **Days on the list** — from `firstSeen`.

Score = 35·(plan) + 25·(community) + 15·(area) + 15·(cut) + 10·(aging), each saturating.
Badges state what was measured ("$93k under the same plan here"), never "builder discount"
unless the builder published one.

`pointer` rows — "4 QMI available, see builder site" — carry a real starting price but do not
name a home. They are kept, flagged, excluded from home counts and from every benchmark, and
shown only in the community strip where they read correctly.

## 7. Delistings

A home absent from a new drop **from the same builder and source** is marked `gone` and stays
visible for 30 days as "no longer listed" (itself a useful signal: that is a home that sold),
then leaves the app projection. It is never deleted from `store.json`.
