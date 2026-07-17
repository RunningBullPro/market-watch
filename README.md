# Buyer2Builder — Market Watch

An internal tool for querying the Henderson new-home market, viewing results visually,
printing reports, and annotating each subdivision with sales contacts, site-visit notes,
builder incentives, and a star rating.

Data source: Home Builders Research *Weekly Traffic & Sales Watch*, week ending 07/12/2026 —
61 Henderson subdivisions (AREA = H).

## What it does (v1)

- **Ask in plain English** — e.g. `townhomes 350 to 400k`, `detached under 500k in Cadence`,
  `Lennar available`, `2000+ sqft`. The query bar turns phrases into filter chips.
- **Structured filters** — home type, price range, min sq ft, master plan, builder, standing inventory.
- **Four views** — cards, sortable table, schematic map (color-coded by master plan), and charts.
- **Annotate a record** — click any subdivision to add a sales contact (name / phone / email),
  builder incentives, site-visit notes, and a 1–5 star rating. Every entry is stamped with
  who added it and when.
- **Print report** — the "Print report" button produces a clean, print/PDF-ready report of the
  **current filtered set**, including incentives and contacts.
- **Export CSV** — download the current results with your annotations.

## Deploy

This is a single static file — no build step.

**Option A — subfolder on buyer2builder.com**
1. Put `index.html` at `https://www.buyer2builder.com/watch/index.html`.
2. Protect `/watch/` with your host's password (e.g. `.htpasswd` on Apache, or your host's
   password-protect setting).

**Option B — GitHub + static host**
1. Commit `index.html` and `README.md` to a repo.
2. Deploy via GitHub Pages, Netlify, Vercel, or Cloudflare Pages, then point a subdomain
   (`watch.buyer2builder.com`) at it and gate it with the host's access control.

## Data persistence

v1 saves annotations in the **browser** (`localStorage`) on whatever device/browser is used,
namespaced under `b2b_watch_v1`. That means each person's notes live on their own machine.
When previewing inside Claude, storage is disabled by the sandbox, so it runs in-memory and
shows a banner — on your hosted site it persists normally.

## Roadmap → v2 (shared)

Phase two swaps the browser-storage layer for **Supabase** so the whole team sees the same
incentives, notes, contacts, and ratings, with individual logins (each entry attributed to a
real account). The data model is already shaped for it:

```
records[subdivision_id] = {
  contact:   { name, phone, email },
  rating:    { value, by, at },
  incentives:[ { id, text, by, at } ],
  notes:     [ { id, text, by, at } ],
  updatedBy, updatedAt
}
```

Suggested tables: `subdivisions` (the 61 records, refreshed weekly), `annotations`
(contact/rating), `incentives`, `notes`, and `profiles` for logins — with row-level security so
edits are attributed. Weekly refresh re-imports the HBR file and re-geocodes only new subdivisions.

## Refresh cadence

The dataset is a snapshot. Each week's HBR file can be re-processed to update prices, inventory,
and new/closed subdivisions, and to re-resolve any moved sales offices or builder pages.
