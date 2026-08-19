#!/usr/bin/env python3
"""Find each builder's real quick move-in listing URL from their own sitemap.

Guessing URL paths is how the pull silently returns nothing. This reads the builder's
sitemap through Monid (context.dev /web/scrape/sitemap, $0.0009/call), scores every URL
for "quick move-in" and "Las Vegas", and prints the best candidates to paste into
tools/readynow/feeds.json.

    python tools/readynow/find_feeds.py                 # every builder returning 0 homes
    python tools/readynow/find_feeds.py "Beazer Homes"  # one builder
"""
import sys, os, re, json, shutil, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from readynow import common as C

WORK = os.path.join(C.REPO, "tools", "_work", "readynow")

QMI_HINT = re.compile(r"quick[-_]?move|move[-_]?in[-_]?ready|movein|ready[-_]?now|"
                      r"available[-_]?homes|inventory[-_]?homes|homes[-_]?for[-_]?sale|qmi", re.I)
LV_HINT = re.compile(r"las[-_ ]?vegas|henderson|north[-_]?las[-_]?vegas|summerlin|nevada|/nv/|-nv-|_nv_", re.I)
BAD = re.compile(r"/blog|/news|/careers|/privacy|/terms|\.pdf$|/agent|/design-studio", re.I)


def sitemap(url):
    body = {"url": url}
    os.makedirs(WORK, exist_ok=True)
    bp = os.path.join(WORK, "sitemap_body.json")
    op = os.path.join(WORK, "sitemap_out.json")
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(body, f)
    if os.path.exists(op):
        os.remove(op)
    exe = shutil.which("monid") or shutil.which("monid.cmd") or "monid"
    try:
        subprocess.run([exe, "run", "-p", "context.dev", "-e", "/web/scrape/sitemap",
                        "-f", bp, "-w", "180", "-o", op], capture_output=True, timeout=300,
                       env={**os.environ, "NO_COLOR": "1", "MSYS_NO_PATHCONV": "1"})
    except Exception as e:
        return [], "monid error: %s" % e
    if not os.path.exists(op):
        return [], "no output"
    try:
        with open(op, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return [], "unparseable"
    urls = []
    def walk(n):
        if isinstance(n, str) and n.startswith("http"):
            urls.append(n)
        elif isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            for k, v in n.items():
                if k in ("url", "loc", "link") and isinstance(v, str):
                    urls.append(v)
                else:
                    walk(v)
    walk(d)
    return sorted(set(urls)), None


def score(u):
    s = 0
    if QMI_HINT.search(u):
        s += 3
    if LV_HINT.search(u):
        s += 2
    if BAD.search(u):
        s -= 5
    s -= u.count("/") * 0.05          # prefer a listing index over a single home
    return s


def main():
    feeds = C.load_json(os.path.join(C.RN_DIR, "feeds.json"), {})
    want = [a for a in sys.argv[1:] if not a.startswith("--")]
    outdir = os.path.join(WORK, "web")
    todo = []
    for b, f in sorted(feeds.items()):
        if b.startswith("_") or not isinstance(f, dict) or not f.get("start"):
            continue
        if want and C.nrm(b) not in {C.nrm(w) for w in want}:
            continue
        if not want:
            got = C.load_json(os.path.join(outdir, "%s.json" % C.slug(b)), None)
            if got:                   # already producing homes, leave it alone
                continue
        todo.append((b, f["start"]))

    for builder, start in todo:
        root = re.match(r"https?://[^/]+", start).group(0)
        print("\n%s  (%s)" % (builder, root))
        urls, err = sitemap(root)
        if err:
            print("   ! %s" % err)
            continue
        ranked = sorted(((score(u), u) for u in urls), reverse=True)[:8]
        for sc, u in ranked:
            if sc <= 0:
                break
            print("   %4.1f  %s" % (sc, u))
        print("   (%d urls in sitemap)" % len(urls))


if __name__ == "__main__":
    main()
