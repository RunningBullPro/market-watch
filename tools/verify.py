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
    # cluster toggle
    pg.wait_for_timeout(600)
    pg.click("#clusterToggle"); pg.wait_for_timeout(1300)
    check("cluster toggle -> individual pins", pg.evaluate("mapClustered")==False and pg.locator("#mapcanvas path.leaflet-interactive").count()>=50, str(pg.locator("#mapcanvas path.leaflet-interactive").count()))
    pg.click("#clusterToggle"); pg.wait_for_timeout(1000)
    check("cluster toggle -> back to clustered", pg.evaluate("mapClustered")==True and pg.locator("#mapcanvas .marker-cluster").count()>0)
    # clickable chart
    pg.click('[data-v="charts"]'); pg.wait_for_timeout(300)
    pg.click('[data-ak="area"][data-av="Henderson"]'); pg.wait_for_timeout(300)
    hcnt = str(pg.evaluate("SUBS.filter(s=>s.area==='Henderson').length"))
    check("chart click -> cards view filtered to Henderson", pg.evaluate("state.view")=="cards" and pg.inner_text("#count").strip()==hcnt, f"view={pg.evaluate('state.view')} count={pg.inner_text('#count')} expected={hcnt}")
    pg.evaluate("clearAll()"); pg.wait_for_timeout(150)
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(200)

    # incentives + standing-inventory report views (Stage A)
    pg.click('[data-v="incentives"]'); pg.wait_for_timeout(300)
    check("incentives view shows offers", pg.locator(".repwrap .offer").count()>=1 and pg.locator("#incReportBtn").count()==1)
    pg.evaluate("buildOffersReport('incentives')"); pg.wait_for_timeout(200)
    check("incentives report branded", "Incentives Report" in pg.inner_text("#printReport") and "Prepared by Alex Tester" in pg.inner_text("#printReport"))
    pg.click('[data-v="inventory"]'); pg.wait_for_timeout(300)
    check("inventory view lists QMI homes", pg.locator(".qmi-group").count()>=1 and pg.locator(".qmi-t tbody tr").count()>=1)
    pg.evaluate("buildOffersReport('inventory')"); pg.wait_for_timeout(200)
    check("inventory report branded", "Standing Inventory Report" in pg.inner_text("#printReport"))
    sid=pg.evaluate("Object.keys(INVENTORY).find(k=>INVENTORY[k].standingHomes.length)")
    pg.evaluate("id=>openPanel(id)", sid); pg.wait_for_timeout(300)
    _mt=pg.inner_text("#subModal").lower()
    check("subdivision modal shows builder offers + homes", ("builder offers" in _mt) and ("move-in-ready home" in _mt))
    pg.click("#pclose"); pg.wait_for_timeout(150)
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(150)

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

    # manual add subdivision (on-site) + non-overwrite
    pg.on("dialog", lambda d: d.accept())
    base_n = pg.evaluate("SUBS.length")
    pg.click("#addSubBtn"); pg.wait_for_timeout(200)
    check("add-subdivision modal opens", pg.locator("#addSubModal.on .cmodal-card").count()==1)
    pg.select_option("#asArea", "Northwest")
    pg.fill("#asBuilder", "On-Site Builder Co"); pg.fill("#asMp", "Test Master Plan")
    pg.fill("#addRows .arow .ar-sub", "Sunset Ridge TEST")
    pg.fill("#addRows .arow .ar-lo", "455000"); pg.fill("#addRows .arow .ar-hi", "525000")
    pg.click("#asSave"); pg.wait_for_timeout(300)
    check("manual sub added to market", pg.evaluate("SUBS.length")==base_n+1 and pg.evaluate("SUBS.some(s=>s.sub==='Sunset Ridge TEST' && s._manual)"))
    check("manual builder appears in filter facets", pg.evaluate("BUILDERS.includes('On-Site Builder Co')"))
    # non-overwrite: reload = a fresh deploy with regenerated inline SUBS; manual sub re-merges from localStorage
    pg.reload(); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(400)
    check("manual sub survives a dataset push (reload)", pg.evaluate("SUBS.some(s=>s.sub==='Sunset Ridge TEST' && s._manual)"))
    pg.click("#addSubBtn"); pg.wait_for_timeout(200)
    pg.click('#addSubInner [data-mdel]'); pg.wait_for_timeout(300)
    check("manual sub removable", not pg.evaluate("(JSON.parse(localStorage.getItem('b2b_watch_v1_manualSubs')||'[]')).some(s=>s.sub==='Sunset Ridge TEST')"))
    pg.evaluate("document.getElementById('addSubModal').classList.remove('on')"); pg.wait_for_timeout(120)

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
    check("report has a map + numbered items + notes + detail pages",
          pg.locator("#printReport .creport-map img, #printReport .creport-map svg").count()>=1
          and pg.locator("#printReport .pi-num").count()==3
          and pg.locator("#printReport .creport-notes").count()>=1
          and pg.locator("#printReport .sub-detail-page").count()==3 and "3-car garage" in rep)
    check("main card reflects new live price", pg.evaluate(f"document.body.innerHTML.includes({('$'+format(newLo,',')) !r})"))

    pg.reload(); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(400)
    check("client + notes + communities persist across reload",
          pg.evaluate(f"!!clientOf({cid!r}) && (clientOf({cid!r}).notes||[]).length===1 && (clientOf({cid!r}).communities||[]).length===3"))

    # ---------------- Ready Now ----------------
    check("Ready Now database is present", pg.evaluate("QMI.length")>0, str(pg.evaluate("QMI.length")))
    check("every Ready Now home has a stable id and a price or address",
          pg.evaluate("QMI.every(h=>h.id && (h.price!=null||h.address))"))
    total_q = pg.evaluate("QMI.length")
    linked = pg.evaluate("QMI.filter(h=>h.subId).length")
    check("homes link back to Market Watch subdivisions", linked >= total_q*0.9, "%d/%d" % (linked, total_q))
    check("every subId resolves to a real subdivision",
          pg.evaluate("QMI.filter(h=>h.subId).every(h=>SUBS.some(s=>s.id===h.subId))"))
    check("no duplicate home ids", pg.evaluate("new Set(QMI.map(h=>h.id)).size===QMI.length"))
    rn_ready = pg.evaluate("QMI.filter(h=>!h.gone&&!h.pointer&&h.status==='ready').length")
    check("ready-now count in the mode switch matches the data",
          pg.inner_text("#rnCount").strip()==str(rn_ready))

    pg.click('#modeSwitch button[data-mode="rn"]'); pg.wait_for_timeout(400)
    check("mode switch flips the shell to Ready Now",
          pg.evaluate("document.body.getAttribute('data-mode')")=="rn"
          and pg.locator("#tabs button[data-v='deals']").is_visible()
          and not pg.locator("#tabs button[data-v='inventory']").is_visible())
    check("default view is completed homes only",
          pg.inner_text("#count").strip()==str(rn_ready),
          "count=%s expected=%d" % (pg.inner_text("#count"), rn_ready))
    check("cards render one per home", pg.locator(".rncard").count()==rn_ready, str(pg.locator(".rncard").count()))

    pg.select_option("#rnBeds","4"); pg.wait_for_timeout(300)
    exp4 = pg.evaluate("QMI.filter(h=>!h.gone&&!h.pointer&&h.status==='ready'&&h.beds>=4).length")
    check("beds filter narrows to 4+", pg.inner_text("#count").strip()==str(exp4),
          "%s vs %d" % (pg.inner_text("#count"), exp4))
    pg.select_option("#rnBeds","0"); pg.wait_for_timeout(150)
    pg.select_option("#rnStories","1"); pg.wait_for_timeout(300)
    exp1 = pg.evaluate("QMI.filter(h=>!h.gone&&!h.pointer&&h.status==='ready'&&h.stories===1).length")
    check("single-story filter works", pg.inner_text("#count").strip()==str(exp1),
          "%s vs %d" % (pg.inner_text("#count"), exp1))
    pg.select_option("#rnStories","0"); pg.wait_for_timeout(150)
    pg.click('#rnAvail button[data-av="all"]'); pg.wait_for_timeout(300)
    check("availability 'all' widens past ready-now", int(pg.inner_text("#count"))>rn_ready, pg.inner_text("#count"))
    pg.click('#rnAvail button[data-av="ready"]'); pg.wait_for_timeout(250)

    check("deal benchmarks are computed", pg.evaluate("QMI.filter(h=>h.deal&&h.deal.score>0).length")>0)
    check("a plan-peer discount is never larger than the home's own price",
          pg.evaluate("QMI.every(h=>!h.deal||!h.deal.planDelta||h.deal.planDelta<h.price)"))
    check("deal scores stay in range", pg.evaluate("QMI.every(h=>!h.deal||(h.deal.score>=0&&h.deal.score<=100))"))
    pg.evaluate("state.rn.deals=new Set(['plan']); render()"); pg.wait_for_timeout(300)
    expp = pg.evaluate("rnFiltered().length")
    check("'under the same plan' filter keeps only homes with that benchmark",
          expp>0 and pg.evaluate("rnFiltered().every(h=>h.deal&&h.deal.planDelta>0)"), str(expp))
    pg.evaluate("state.rn.deals=new Set(); render()"); pg.wait_for_timeout(250)

    pg.click('#tabs button[data-v="map"]'); pg.wait_for_timeout(1200)
    check("Ready Now map draws pins",
          pg.locator("#mapcanvas .leaflet-marker-icon, #mapcanvas path.leaflet-interactive").count()>0)
    pg.click('#tabs button[data-v="deals"]'); pg.wait_for_timeout(400)
    check("Deals view ranks opportunities",
          pg.locator(".deal-row").count()>0 and pg.locator(".deal-rank").first.inner_text()=="1")
    pg.click('#tabs button[data-v="charts"]'); pg.wait_for_timeout(400)
    check("Ready Now charts render", pg.locator(".chartgrid svg").count()>0)
    pg.click('#tabs button[data-v="cards"]'); pg.wait_for_timeout(300)

    pg.locator(".rncard").first.click(); pg.wait_for_timeout(400)
    check("home detail opens", pg.locator("#homeModal.on").count()==1 and pg.locator(".hd-plan").count()==1)
    hd = pg.inner_text("#homeInner")
    check("home detail shows price, source and as-of", "$" in hd and "as of" in hd)
    pg.click("#homeClose"); pg.wait_for_timeout(250)

    sub_with = pg.evaluate("(QMI.find(h=>h.subId&&!h.pointer&&!h.gone&&h.status==='ready')||{}).subId")
    pg.evaluate("jumpToSub(%r)" % sub_with); pg.wait_for_timeout(400)
    check("Market Watch -> Ready Now jump filters to that community",
          pg.evaluate("rnFiltered().length")>0
          and pg.evaluate("rnFiltered().every(h=>h.subId===%r)" % sub_with))
    pg.evaluate("rnSubFocus=null; render()"); pg.wait_for_timeout(250)
    pg.click('#modeSwitch button[data-mode="mw"]'); pg.wait_for_timeout(400)
    check("Market Watch cards carry the Ready Now tag", pg.locator(".tag.qmi").count()>0)
    pg.evaluate("openPanel(%r)" % sub_with); pg.wait_for_timeout(400)
    check("subdivision panel lists its Ready Now homes",
          pg.locator(".qmi-sec").count()==1 and pg.locator(".qmi-sec .qs-row").count()>0)
    pg.evaluate("closePanel()"); pg.wait_for_timeout(200)
    check("the Inventory view now reads from the Ready Now database",
          pg.evaluate("invFor(%r).standingHomes.length>0" % sub_with))

    pg.click('#modeSwitch button[data-mode="rn"]'); pg.wait_for_timeout(350)
    hid = pg.evaluate("rnFiltered()[0].id")
    hprice = pg.evaluate("QMI_BY_ID[%r].price" % hid)
    pg.evaluate("toggleClientHome(%r,%r)" % (cid, hid)); pg.wait_for_timeout(300)
    check("home added to the client profile with a frozen snapshot",
          pg.evaluate("(clientOf(%r).homes||[]).length===1 && clientOf(%r).homes[0].snap.price===%d" % (cid, cid, hprice)))
    pg.evaluate("QMI_BY_ID[%r].price = %d" % (hid, hprice+40000))
    check("a later builder price change does not rewrite the snapshot",
          pg.evaluate("clientOf(%r).homes[0].snap.price===%d" % (cid, hprice))
          and pg.evaluate("homeSnapDiffers(clientOf(%r).homes[0].snap)" % cid))
    pg.evaluate("QMI_BY_ID[%r].price = %d" % (hid, hprice))

    pg.evaluate("buildClientReport(%r)" % cid); pg.wait_for_timeout(1200)
    rep2 = pg.inner_text("#printReport")
    check("client report gains a Ready Now section",
          "Ready Now" in rep2 and ("$"+format(hprice,",")) in rep2)

    pg.evaluate("buildRnReport('deals')"); pg.wait_for_timeout(400)
    rd = pg.inner_text("#printReport")
    check("Ready Now deals report prints with provenance",
          "Best Ready Now Opportunities" in rd and "builder-published" in rd
          and pg.locator("#printReport .prep-item").count()>0)

    with pg.expect_download() as di2:
        pg.click("#exportBtn")
    csv2 = open(di2.value.path(), encoding="utf-8").read()
    check("Ready Now CSV exports home-level columns",
          "Plan," in csv2 and "$/Sq Ft" in csv2 and "Deal Score" in csv2 and len(csv2.strip().splitlines())>1)

    pg.reload(); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(600)
    check("Ready Now mode persists across reload", pg.evaluate("state.mode")=="rn")
    check("saved home survives reload", pg.evaluate("(clientOf(%r).homes||[]).length===1" % cid))
    browser.close()

httpd.shutdown()
print("\n=== CONSOLE / PAGE ERRORS ===")
print("\n".join(errors) if errors else "(none)")
fails = [r for r in results if not r[1]]
print(f"\n=== SUMMARY: {len(results)-len(fails)}/{len(results)} passed ===  screenshots: {SHOT}")
sys.exit(1 if (fails or any('pageerror' in e for e in errors)) else 0)
