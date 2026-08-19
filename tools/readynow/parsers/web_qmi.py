"""Parser adapter: normalized JSON produced by tools/qmi_scrape.py.

The scrape already emits the raw-record contract, so this adapter only validates and
labels. Kept as an adapter so `build_readynow.py parse` treats a weekly web pull and a
builder PDF the same way.
"""
import json, os
from .. import common as C

BUILDER = "web pull"


def detect(path):
    if not path.lower().endswith(".json"):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return False
    if not isinstance(d, list):
        return False
    if not d:                    # a builder with nothing published this week
        return os.sep + "web" + os.sep in os.path.abspath(path)
    return isinstance(d[0], dict) and d[0].get("source") == "builder site" and "builder" in d[0]


def parse(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    out = []
    for r in rows:
        if not r.get("price") and not r.get("address"):
            continue
        r.setdefault("sourceDoc", os.path.basename(path))
        r.setdefault("collection", "")
        r.setdefault("series", "")
        out.append(r)
    return out
