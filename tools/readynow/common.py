"""Shared helpers for the Ready Now (quick move-in) ingest pipeline.

A "raw record" is what a parser adapter emits: one standing/near-term home, with the
fields the builder published and nothing derived. Normalization, community matching,
geocoding, history and deal scoring all happen downstream in build_readynow.py.
"""
import re, json, os, datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RN_DIR = os.path.join(REPO, "tools", "readynow")
STORE = os.path.join(RN_DIR, "store.json")
ALIASES = os.path.join(RN_DIR, "aliases.json")
INDEX = os.path.join(REPO, "index.html")

# Raw-record contract. Parsers must emit every key; unknown values are None.
RAW_FIELDS = [
    "builder", "community", "collection", "region", "salesOffice", "salesPhone",
    "plan", "facing", "address", "city", "zip", "isModel",
    "sqft", "beds", "baths", "stories", "garage",
    "availLabel", "price", "url", "source", "asOf",
]


def nrm(x):
    """Normalize for matching: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def slug(x, maxlen=40):
    s = re.sub(r"[^a-z0-9]+", "-", str(x or "").lower()).strip("-")
    return s[:maxlen].strip("-")


def money(x):
    if x is None:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", str(x))
    if not m:
        return None
    return int(round(float(m.group(0).replace(",", ""))))


def num(x):
    if x is None or str(x).strip() == "":
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(x))
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    return int(v) if v == int(v) else v


def today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- availability

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_availability(label, asof=None):
    """'Ready Now' / 'Sept 2026' / 'Move-in ready' -> (status, availDate).

    status is 'ready' for a completed home, 'soon' for a dated future delivery.
    availDate is the first of the stated month (ISO) or None.
    """
    t = str(label or "").strip()
    low = t.lower()
    if not t:
        return "ready", None
    if "ready" in low or "move-in" in low or "movein" in low or "immediate" in low:
        return "ready", None
    m = re.search(r"([a-z]{3,9})\.?\s*(\d{4})", low)
    if m and m.group(1)[:3] in MONTHS:
        mo = MONTHS[m.group(1)[:3]]
        return "soon", "%s-%02d-01" % (m.group(2), mo)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
    if m:
        y = int(m.group(3))
        y += 2000 if y < 100 else 0
        return "soon", "%04d-%02d-%02d" % (y, int(m.group(1)), int(m.group(2)))
    return "soon", None


def parse_addr(addr):
    """Split 'x, City, NV 89015' into (street, city, zip). Tolerates a bare street."""
    if not addr:
        return None, None, None
    parts = [p.strip() for p in str(addr).split(",")]
    street = parts[0] if parts else None
    city = zc = None
    if len(parts) >= 2:
        city = parts[1] or None
    m = re.search(r"\b(\d{5})\b", str(addr))
    if m:
        zc = m.group(1)
    return street or None, city, zc


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------- pdf -> rows

def pdf_rows(path, ytol=9.0):
    """Reconstruct table rows from a PDF using word coordinates (PyMuPDF).

    Returns a list of pages; each page is a list of (y, [(x0, word), ...]) lines,
    sorted top to bottom. PyMuPDF decodes the fi/fl ligatures that pdfplumber turns
    into NULs, so plan names like "Sunflower" and "Portofino" survive intact.
    """
    import fitz
    doc = fitz.open(path)
    pages = []
    for page in doc:
        buckets = []
        for x0, y0, x1, y1, w, *_ in page.get_text("words"):
            yc = (y0 + y1) / 2.0
            for b in buckets:
                if abs(b[0] - yc) <= 2.0:
                    b[1].append((x0, w))
                    break
            else:
                buckets.append([yc, [(x0, w)]])
        buckets.sort(key=lambda b: b[0])
        pages.append([(y, sorted(ws)) for y, ws in buckets])
    doc.close()
    return pages


def line_text(words):
    return " ".join(w for _, w in words)
