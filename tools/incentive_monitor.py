#!/usr/bin/env python3
"""
Twice-weekly incentive + link monitor for Market Watch (runs in GitHub Actions on Linux).

For every builder promotion and community-specific offer currently stored in index.html
(PROMOS + INVENTORY), it:
  1. re-scrapes the linked offer page via Monid octen /extract,
  2. verifies the link still resolves and the page still shows an offer,
  3. flags stored terms (rates / dollar amounts / dates) that no longer appear on the page.

It writes tools/_work/refresh_report.md and, if anything needs attention, tools/_work/HAS_FINDINGS.
The heavy rewriting of offer text stays a human/LLM review step - this job's role is to tell you
exactly which builders and links changed so the twice-weekly update is targeted, not guesswork.
"""
import json, re, subprocess, pathlib, os, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
WORK = ROOT / "tools" / "_work"; WORK.mkdir(parents=True, exist_ok=True)

def grab(name, o, c):
    i = HTML.index("const " + name + " ="); s = HTML.index(o, i); depth = 0; j = s
    while j < len(HTML):
        if HTML[j] == o: depth += 1
        elif HTML[j] == c:
            depth -= 1
            if depth == 0: j += 1; break
        j += 1
    return HTML[s:j]

PROMOS = json.loads(grab("PROMOS", "{", "}"))
INVENTORY = json.loads(grab("INVENTORY", "{", "}"))

OFFER_HINTS = re.compile(r"apr|rate|buydown|incentive|offer|closing|credit|save|savings|promo|"
                         r"down payment|assistance|design|appliance|flex|%|\$\s?\d", re.I)

def octen(url):
    body = {"urls": [url], "max_age_seconds": 3600, "format": "markdown",
            "query": "current incentive offer promotion rate buydown closing cost credit "
                     "down payment assistance design appliance price savings expiration"}
    (WORK / "body.json").write_text(json.dumps(body))
    out = WORK / "out.json"
    if out.exists(): out.unlink()
    try:
        subprocess.run(["monid", "run", "-p", "octen", "-e", "/extract",
                        "-f", str(WORK / "body.json"), "-w", "90", "-o", str(out)],
                       capture_output=True, timeout=200, env={**os.environ, "NO_COLOR": "1"})
    except Exception as e:
        return {"ok": False, "reason": "monid error: %s" % e}
    if not out.exists():
        return {"ok": False, "reason": "no output (fetch failed or CLI crashed)"}
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
        r = (d.get("data", {}).get("results") or [None])[0]
    except Exception:
        return {"ok": False, "reason": "unparseable response"}
    if not r or r.get("status") != "success":
        return {"ok": False, "reason": "page did not render (status=%s)" % (r or {}).get("status")}
    text = (r.get("full_content") or "") + " " + " ".join(r.get("highlights") or [])
    return {"ok": True, "text": text, "has_offer": bool(OFFER_HINTS.search(text))}

def stored_terms(text):
    rates = re.findall(r"\d+\.\d+%", text)
    amounts = re.findall(r"\$[\d,]{3,}", text)
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", text)
    return rates, amounts, dates

def missing_terms(stored, page):
    p = page.lower().replace(",", "")
    miss = []
    rates, amounts, dates = stored_terms(stored)
    for r in set(rates):
        if r.rstrip("%") not in p: miss.append(r)
    for a in set(amounts):
        if a.replace("$", "").replace(",", "") not in p: miss.append(a)
    for d in set(dates):
        if d not in page: miss.append(d)
    return miss

# collect (label, url, stored_text) targets: builder promos + community offers
targets = []
for b, v in PROMOS.items():
    if v.get("url"): targets.append(("Builder: " + b, v["url"], v.get("text", "")))
for cid, v in INVENTORY.items():
    for inc in (v.get("incentives") or []):
        if inc.get("url"): targets.append(("Community: " + cid, inc["url"], inc.get("text", "")))

# de-dupe by url, keep first label/text
seen, uniq = set(), []
for label, url, text in targets:
    if url in seen: continue
    seen.add(url); uniq.append((label, url, text))

dead, no_offer, changed, ok = [], [], [], []
for label, url, text in uniq:
    res = octen(url)
    if not res["ok"]:
        dead.append((label, url, res["reason"]))
    elif not res["has_offer"]:
        no_offer.append((label, url))
    else:
        miss = missing_terms(text, res["text"])
        if miss:
            changed.append((label, url, miss))
        else:
            ok.append((label, url))

today = datetime.date.today().isoformat()
lines = ["# Market Watch - incentive & link refresh", "",
         "Run: %s  |  checked %d offer links" % (today, len(uniq)), ""]
if dead:
    lines += ["## Dead / unreachable links (%d) - fix the URL" % len(dead), ""]
    lines += ["- **%s** - `%s`  (%s)" % (l, u, why) for l, u, why in dead] + [""]
if no_offer:
    lines += ["## Link resolves but shows NO offer (%d) - likely wrong/expired page" % len(no_offer), ""]
    lines += ["- **%s** - %s" % (l, u) for l, u in no_offer] + [""]
if changed:
    lines += ["## Stored terms not found on page (%d) - review; offer may have changed" % len(changed), ""]
    lines += ["- **%s** - %s  (missing: %s)" % (l, u, ", ".join(m)) for l, u, m in changed] + [""]
lines += ["## OK - link resolves and terms still present (%d)" % len(ok), ""]
lines += ["- %s" % l for l, u in ok] + [""]

report = "\n".join(lines)
(WORK / "refresh_report.md").write_text(report, encoding="utf-8")
findings = len(dead) + len(no_offer) + len(changed)
if findings:
    (WORK / "HAS_FINDINGS").write_text(str(findings))
print(report)
print("\nFINDINGS:", findings)
