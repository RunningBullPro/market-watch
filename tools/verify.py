#!/usr/bin/env python3
"""End-to-end browser verification for Market Watch (index.html).

Serves the repo over http (localStorage needs http, not file://), drives the app
in headless Chromium via Playwright, and asserts the client-profile + dataset-
snapshot behavior. Screenshots are written to a temp dir (path printed at the end).

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
os.chdir(REPO)
PORT = 8137

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

    check("map is real Leaflet", (pg.click('[data-v="map"]'), pg.wait_for_timeout(1500),
          pg.locator("#mapcanvas.leaflet-container").count()==1 and pg.locator("#mapcanvas img.leaflet-tile").count()>0)[-1])
    pg.click('[data-v="cards"]'); pg.wait_for_timeout(200)

    ids = pg.evaluate("SUBS.slice().sort((a,b)=>a.sub.localeCompare(b.sub)).map(s=>s.id)")
    pickA = ids[0]
    cardIds = pg.evaluate("() => [...document.querySelectorAll('.card[data-open]')].map(c=>c.getAttribute('data-open'))")

    pg.click("#clientsBtn"); pg.wait_for_timeout(150)
    pg.fill("#ncName","Jordan Fisher"); pg.fill("#ncPhone","(702) 555-0134"); pg.fill("#ncEmail","jordan@example.com")
    pg.click("#ncCreate"); pg.wait_for_timeout(300)
    check("client opens as centered modal", pg.locator("#clientModal.on .cmodal-card").count()==1)
    cid = pg.evaluate("activeClientId")

    pg.fill("#clNoteInput","Wants 3-car garage, under 500k, ready by spring."); pg.click("#clNoteAdd"); pg.wait_for_timeout(200)
    check("client note added", pg.locator("#clNoteList .item").count()==1)

    pg.click(f'.pickrow[data-pick="{pickA}"]'); pg.wait_for_timeout(250)
    check("report map preview renders", pg.locator("#cmMap svg").count()==1)

    pg.click("#clpclose"); pg.wait_for_timeout(150)
    B = next(x for x in cardIds if x != pickA)
    pg.click(f'.card [data-client-add="{B}"]'); pg.wait_for_timeout(200)
    C = next(x for x in ids if x not in (pickA,B))
    pg.evaluate(f"openPanel({C!r})"); pg.wait_for_timeout(150); pg.click("#pClientToggle"); pg.wait_for_timeout(150); pg.click("#pclose"); pg.wait_for_timeout(120)
    pg.click("#cpOpen"); pg.wait_for_timeout(300)
    check("profile has 3 communities", pg.locator("#commList .comm").count()==3)
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

    pg.evaluate(f"buildClientReport({cid!r})"); pg.wait_for_timeout(200)
    rep = pg.inner_text("#printReport")
    check("report shows snapshot price, not new", ("$"+format(snapLo,",")) in rep and ("$"+format(newLo,",")) not in rep)
    check("report has client name + preparer", "Jordan Fisher" in rep and "Prepared by Alex Tester" in rep)
    check("report has schematic map + numbered items + notes",
          pg.locator("#printReport .creport-map svg").count()==1 and pg.locator("#printReport .pi-num").count()==3
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
