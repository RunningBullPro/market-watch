#!/usr/bin/env python3
"""End-to-end browser verification for Market Watch (index.html).

Serves the repo over http (localStorage needs http, not file://), drives the app
in headless Chromium via Playwright, and asserts the client-profile, dataset-
snapshot, client-filter, and report-map behavior. Screenshots go to a temp dir
(path printed at the end).

Setup (one time):
    pip install playwright
    python -m playwright install chromium

Run from anywhere:
    python tools/verify.py
Exit code is non-zero if any assertion fails or a page/console error occurs.
"""
import threading, http.server, socketserver, os, functools, sys, tempfile
from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = tempfile.mkdtemp(prefix="mw-verify-")
os.chdir(REPO); PORT = 8137
httpd = socketserver.TCPServer(("127.0.0.1", PORT), functools.partial(http.server.SimpleHTTPRequestHandler))
threading.Thread(target=httpd.serve_forever, daemon=True).start()

errors, results = [], []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS" if cond else "FAIL"), "-", name, ("| "+extra if extra else ""))

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width":1400,"height":1000}, accept_downloads=True)
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error","warning") else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.add_init_script("window.print = () => { window.__printed=(window.__printed||0)+1; };")
    pg.goto(f"http://127.0.0.1:{PORT}/index.html"); pg.wait_for_load_state("networkidle")
    if pg.locator("#idModal.on").count():
        pg.fill("#idInput","Alex Tester"); pg.click("#idGo")
    pg.wait_for_timeout(250)

    # query parser: a square-footage range must filter by sqft, not price
    pf = pg.evaluate("()=>parseQuery('2000 to 3000 sq ft').filters")
    check("sqft range parses as sqft, not price", pf.get('sqftMin')==2000 and pf.get('sqftMax')==3000 and 'priceMin' not in pf, str(pf))
    c1 = pg.evaluate("()=>{applyParsedQuery('2000 to 3000 sq ft'); return filtered().length;}")
    c2 = pg.evaluate("SUBS.filter(s=>s.maxSqft>=2000 && s.minSqft<=3000).length")
    check("sqft range count matches overlap", c1==c2 and c1>0, f"{c1} vs {c2}")
    check("price range still parses as price", pg.evaluate("()=>parseQuery('350 to 400k').filters").get('priceMin')==350000)
    pg.evaluate("clearAll()"); pg.wait_for_timeout(100)

    # valley scale + area-of-town lens
    check("valley scale (>=200 communities)", pg.evaluate("SUBS.length")>=200, str(pg.evaluate("SUBS.length")))
    check("areas are the lens", pg.evaluate("GROUPS.includes('Henderson') && GROUPS.includes('Northwest') && GROUPS.includes('Southwest')"))
    pg.check('[data-group="Henderson"]'); pg.wait_for_timeout(200)
    hend = str(pg.evaluate("SUBS.filter(s=>s.area==='Henderson').length"))
    check("Area-of-town filter narrows to Henderson", pg.inner_text("#count").strip()==hend, f"count={pg.inner_text('#count')} expected={hend}")
    pg.uncheck('[data-group="Henderson"]'); pg.wait_for_timeout(150)
    # master plan as a secondary filter + search
    mp0 = pg.evaluate("MASTERPLANS[0]")
    pg.check(f'[data-mp="{mp0}"]'); pg.wait_for_timeout(200)
    mpc = str(pg.evaluate("m=>SUBS.filter(s=>s.mp===m).length", mp0))
    check("master-plan filter narrows", pg.inner_text("#count").strip()==mpc, f"count={pg.inner_text('#count')} expected={mpc} ({mp0})")
    pg.uncheck(f'[data-mp="{mp0}"]'); pg.wait_for_timeout(150)
    check("master-plan parses from search", pg.evaluate("()=>(parseQuery('townhomes in Summerlin').filters.mps||[]).includes('Summerlin')"))
    check("'southwest' search doesn't also match 'South'", pg.evaluate("()=>{const g=parseQuery('southwest homes').filters.groups||[]; return g.includes('Southwest') && !g.includes('South');}"))

    check("map is real Leaflet + OSM tiles", (pg.click('[data-v="map"]'), pg.wait_for_timeout(1500),
          pg.locator("#mapcanvas.leaflet-container").count()==1 and pg.locator("#mapcanvas img.leaflet-tile").count()>0)[-1])
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(200)

    ids = pg.evaluate("SUBS.slice().sort((a,b)=>a.sub.localeCompare(b.sub)).map(s=>s.id)")
    pickA = ids[0]
    cardIds = pg.evaluate("() => [...document.querySelectorAll('.card[data-open]')].map(c=>c.getAttribute('data-open'))")

    # subdivision detail is now a centered modal with a map snapshot
    pg.evaluate(f"openPanel({pickA!r})"); pg.wait_for_timeout(200)
    check("subdivision opens as centered modal", pg.locator("#subModal.on .cmodal-card").count()==1)
    check("subdivision modal has two columns", pg.locator("#subModal .cmodal-body .cmodal-col").count()==2)
    check("subdivision keeps stats/contact/notes", pg.locator("#subModal #stars").count()==1 and pg.locator("#subModal #cName").count()==1 and pg.locator("#subModal #noteList").count()==1)
    pg.wait_for_timeout(3000)
    check("subdivision map snapshot renders", pg.locator("#subMap img").count()==1)
    # add a note + incentive, then generate the per-subdivision report
    pg.fill("#noteInput","Model home smelled of fresh paint; lot 12 backs to wash."); pg.click("#noteAdd"); pg.wait_for_timeout(150)
    pg.fill("#incInput","$10k flex cash + fridge"); pg.click("#incAdd"); pg.wait_for_timeout(150)
    check("site-visit note saved", pg.locator("#noteList .item").count()==1)
    pg.evaluate(f"buildSubReport({pickA!r})"); pg.wait_for_timeout(300)
    srep = pg.inner_text("#printReport")
    check("sub report has branding + preparer", "Subdivision Report" in srep and "Prepared by Alex Tester" in srep)
    check("sub report has map + stats", pg.locator("#printReport .creport-map img, #printReport .creport-map svg").count()>=1 and pg.locator("#printReport .sr-stat").count()>=6)
    check("sub report includes notes + incentives", "Site-visit notes" in srep and "fresh paint" in srep and "Builder incentives" in srep and "flex cash" in srep)
    pg.click("#pclose"); pg.wait_for_timeout(150)
    check("subdivision modal closes", pg.locator("#subModal.on").count()==0)

    # ad-hoc selection (no client) from cards + table
    selA, selB = cardIds[0], cardIds[1]
    pg.check(f'.card input[data-sel="{selA}"]'); pg.wait_for_timeout(150)
    check("card selection shows selection bar", "1 selected" in pg.inner_text("#selBar"))
    check("selected card highlighted", pg.locator(f'.card.sel input[data-sel="{selA}"]').count()==1)
    pg.click('[data-v="table"]'); pg.wait_for_timeout(200)
    check("selection persists into table (checked)", pg.is_checked(f'tr input[data-sel="{selA}"]'))
    pg.check(f'tr input[data-sel="{selB}"]'); pg.wait_for_timeout(150)
    check("table selection updates bar to 2", "2 selected" in pg.inner_text("#selBar"))
    # selection report (no client)
    pg.evaluate("buildSelectionReport()"); pg.wait_for_timeout(300)
    srep2 = pg.inner_text("#printReport")
    check("selection report: branding + preparer + count",
          "Selected Communities" in srep2 and "Prepared by Alex Tester" in srep2 and "2 communities" in srep2)
    check("selection report: map + 2 numbered items",
          pg.locator("#printReport .creport-map img, #printReport .creport-map svg").count()>=1 and pg.locator("#printReport .pi-num").count()==2)
    with pg.expect_download() as di2:
        pg.click("#selCSV")
    scsv = open(di2.value.path(), encoding="utf-8").read()
    check("selection CSV has 2 rows", len([l for l in scsv.strip().splitlines() if l])==3)
    pg.click("#selClear"); pg.wait_for_timeout(200)
    check("clear selection empties bar", pg.inner_text("#selBar").strip()=="")
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(150)

    pg.click("#clientsBtn"); pg.wait_for_timeout(150)
    pg.fill("#ncName","Jordan Fisher"); pg.fill("#ncPhone","(702) 555-0134"); pg.fill("#ncEmail","jordan@example.com")
    pg.click("#ncCreate"); pg.wait_for_timeout(300)
    check("client opens as centered modal", pg.locator("#clientModal.on .cmodal-card").count()==1)
    cid = pg.evaluate("activeClientId")
    pg.fill("#clNoteInput","Wants 3-car garage, under 500k, ready by spring."); pg.click("#clNoteAdd"); pg.wait_for_timeout(200)
    check("client note added", pg.locator("#clNoteList .item").count()==1)

    pg.click(f'.pickrow[data-pick="{pickA}"]'); pg.wait_for_timeout(150)
    pg.click("#clpclose"); pg.wait_for_timeout(150)
    B = next(x for x in cardIds if x != pickA)
    pg.click(f'.card [data-client-add="{B}"]'); pg.wait_for_timeout(200)
    C = next(x for x in ids if x not in (pickA,B))
    pg.evaluate(f"openPanel({C!r})"); pg.wait_for_timeout(150); pg.click("#pClientToggle"); pg.wait_for_timeout(150); pg.click("#pclose"); pg.wait_for_timeout(120)

    # client-only filter refines all views
    pg.click("#cpFilter"); pg.wait_for_timeout(250)
    check("client filter refines count to 3", pg.inner_text("#count").strip()=="3", pg.inner_text("#count"))
    pg.click('[data-v="map"]'); pg.wait_for_timeout(1600)
    check("map shows only client's 3 markers", pg.evaluate("mapMarkerCount")==3, str(pg.evaluate("mapMarkerCount")))
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(120)
    pg.click("#cpFilter"); pg.wait_for_timeout(200)
    total = str(pg.evaluate("SUBS.length"))
    check("client filter off -> all communities", pg.inner_text("#count").strip()==total, f"count={pg.inner_text('#count')} total={total}")

    pg.click("#cpOpen"); pg.wait_for_timeout(300)
    check("profile has 3 communities", pg.locator("#commList .comm").count()==3)
    pg.wait_for_timeout(3000)
    check("modal shows real map preview", pg.locator("#cmMap img, #cmMap svg").count()>=1)
    pg.screenshot(path=os.path.join(SHOT,"modal.png"))

    snapLo = pg.evaluate(f"clientOf({cid!r}).communities.find(e=>e.id==={pickA!r}).snap.lo")
    subA = pg.evaluate(f"clientOf({cid!r}).communities.find(e=>e.id==={pickA!r}).snap.sub")
    newLo = snapLo + 123000
    pg.evaluate(f"SUBS.find(s=>s.id==={pickA!r}).lo = {newLo}")
    pg.evaluate(f"renderClientPanel({cid!r}); render();"); pg.wait_for_timeout(250)
    check("snapshot keeps old price after dataset change",
          pg.evaluate(f"clientOf({cid!r}).communities.find(e=>e.id==={pickA!r}).snap.lo")==snapLo, f"snap={snapLo} live={newLo}")
    check("panel flags 'Latest data differs'", pg.locator("#commList .commtag.upd").count()>=1)

    with pg.expect_download() as di:
        pg.click("#clCSV")
    csv = open(di.value.path(), encoding="utf-8").read()
    check("client CSV has snapshot rows", "Subdivision" in csv and subA in csv and len([l for l in csv.strip().splitlines() if l])==4)

    pg.evaluate(f"buildClientReport({cid!r})"); pg.wait_for_timeout(300)
    rep = pg.inner_text("#printReport")
    check("report shows snapshot price, not new", ("$"+format(snapLo,",")) in rep and ("$"+format(newLo,",")) not in rep)
    check("report has client name + preparer", "Jordan Fisher" in rep and "Prepared by Alex Tester" in rep)
    check("report has a map + numbered items + notes",
          pg.locator("#printReport .creport-map img, #printReport .creport-map svg").count()>=1
          and pg.locator("#printReport .pi-num").count()==3
          and pg.locator("#printReport .creport-notes").count()==1 and "3-car garage" in rep)
    check("main card reflects new live price", pg.evaluate(f"document.body.innerHTML.includes({('$'+format(newLo,',')) !r})"))

    pg.reload(); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(400)
    check("client + notes + communities persist across reload",
          pg.evaluate(f"!!clientOf({cid!r}) && (clientOf({cid!r}).notes||[]).length===1 && (clientOf({cid!r}).communities||[]).length===3"))
    browser.close()

httpd.shutdown()
print("\n=== CONSOLE / PAGE ERRORS ===")
print("\n".join(errors) if errors else "(none)")
fails = [r for r in results if not r[1]]
print(f"\n=== SUMMARY: {len(results)-len(fails)}/{len(results)} passed ===  screenshots: {SHOT}")
sys.exit(1 if (fails or any('pageerror' in e for e in errors)) else 0)
