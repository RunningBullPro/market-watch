"""Parser adapter: Taylor Morrison "Division QMIs" PDF.

Layout (one row per home, grouped by region then community):

    HENDERSON | Available homes
    Opus at Cadence | Sales Office: 304 Taylor Street, Henderson, NV 89015
    Sycamore - Melody  South facing  113 Tardando Avenue  2,545  3  2.5  2  2  Ready Now  $549,990

Model homes carry the address on the line above and "MODEL HOME" on the line below, so
rows are reassembled from the neighbouring lines when the address cell comes back empty.
"""
import re
from .. import common as C

BUILDER = "Taylor Morrison"

FACING = r"North|South|East|West|Northeast|Northwest|Southeast|Southwest"

ROW = re.compile(
    r"^(?P<rest>.+?)\s+"
    r"(?P<sqft>\d{1,2},\d{3}|\d{3,5})\s+"
    r"(?P<beds>\d{1,2})\s+"
    r"(?P<baths>\d{1,2}(?:\.\d)?)\s+"
    r"(?P<stories>\d)\s+"
    r"(?P<garage>\d)\s+"
    r"(?P<avail>Ready Now|[A-Za-z]{3,9}\.?\s*\d{4})\s+"
    r"\$(?P<price>[\d,]+)$"
)
REST = re.compile(r"^(?P<plan>.+?)\s+(?P<facing>%s)\s+facing\s*(?P<address>.*)$" % FACING, re.I)
REGION = re.compile(r"^(?P<region>[A-Z][A-Z .'-]{2,})\s*\|?\s*Available homes\b", re.I)
OFFICE = re.compile(r"^(?P<community>.+?)\s*\|?\s*Sales Office:\s*(?P<office>.+)$")
ASOF = re.compile(r"Prices effective as of\s+([A-Za-z]+ \d{1,2}, \d{4})")
PHONE = re.compile(r"\((\d{3})\)\s*(\d{3})\s*-\s*(\d{4})")
COLLECTION = re.compile(r"^(?P<coll>The .+? Collection)\s+at\s+(?P<comm>.+)$")

# A row line must not be one of the header/section lines.
SKIP = re.compile(r"FLOOR PLAN|Sales Office:|Available homes|Prices effective", re.I)

# Division office in the sheet header, and the material half of the fine print.
DIVISION = re.compile(r"^(\d{3,6} [^,]+, (?:Las Vegas|Henderson|North Las Vegas), NV \d{5})$")
PRICENOTE = re.compile(
    r"(Quick Move-In Home prices shown above.*?no longer available\.)", re.S)
# "Sycamore - Melody" is floor plan + product series.
PLAN_SERIES = re.compile(r"^(?P<plan>.+?)\s+-\s+(?P<series>[A-Za-z][A-Za-z ]{2,20})$")


def detect(path):
    """True if this adapter recognises the file."""
    if not path.lower().endswith(".pdf"):
        return False
    try:
        pages = C.pdf_rows(path)
    except Exception:
        return False
    head = " ".join(C.line_text(w) for _, w in (pages[0] if pages else [])[:40])
    return "Taylor Morrison" in head and "FLOOR PLAN" in head


def parse(path):
    pages = C.pdf_rows(path)
    flat = []
    for pg in pages:
        for y, words in pg:
            flat.append(C.line_text(words).replace("’", "'").strip())

    asof = phone = division = None
    for ln in flat:
        if asof is None:
            m = ASOF.search(ln)
            if m:
                import datetime
                asof = datetime.datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        if phone is None:
            m = PHONE.search(ln)
            if m:
                phone = "(%s) %s-%s" % m.groups()
        if division is None:
            m = DIVISION.match(ln)
            if m:
                division = m.group(1)

    fine = " ".join(flat)
    m = PRICENOTE.search(fine)
    price_note = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    doc = __import__("os").path.basename(path)

    out = []
    region = community = collection = office = None
    age55 = False
    src = "Taylor Morrison division QMI sheet"

    for i, ln in enumerate(flat):
        if not ln:
            continue
        m = REGION.match(ln)
        if m:
            region = m.group("region").strip().title()
            continue
        m = OFFICE.match(ln)
        if m and not ROW.match(ln):
            raw = m.group("community").strip().rstrip("|").strip()
            age55 = bool(re.search(r"\b55\+|age qualified", raw, re.I))
            raw = re.sub(r"\s*55\+\s*$", "", raw).strip()
            cm = COLLECTION.match(raw)
            collection = cm.group("coll").strip() if cm else ""
            community = raw
            office = m.group("office").strip()
            continue
        if SKIP.search(ln):
            continue
        m = ROW.match(ln)
        if not m:
            continue
        rm = REST.match(m.group("rest").strip())
        if not rm:
            continue

        address = rm.group("address").strip()
        is_model = False
        # Model-home rows split the address onto the neighbouring lines.
        if not address:
            for j in (i - 1, i - 2):
                if j >= 0 and re.match(r"^\d+\s+\S", flat[j]) and not ROW.match(flat[j]):
                    address = flat[j].strip()
                    break
        for j in (i + 1, i + 2):
            if j < len(flat) and flat[j].strip().upper() == "MODEL HOME":
                is_model = True
                break
        address = re.sub(r"\s*MODEL HOME\s*$", "", address, flags=re.I).strip()

        _, ocity, ozip = C.parse_addr(office)
        street, city, zc = C.parse_addr(address)

        plan_full = rm.group("plan").strip()
        pm = PLAN_SERIES.match(plan_full)

        out.append({
            "builder": BUILDER,
            "community": community,
            "collection": collection or "",
            "region": region,
            "salesOffice": office,
            "salesPhone": phone,
            "divisionOffice": division,
            "priceNote": price_note,
            "sourceDoc": doc,
            "plan": pm.group("plan").strip() if pm else plan_full,
            "series": pm.group("series").strip() if pm else "",
            "planFull": plan_full,
            "facing": rm.group("facing").title(),
            "address": street,
            "city": city or ocity,
            "zip": zc or ozip,
            "isModel": is_model,
            "age55": age55,
            "sqft": C.num(m.group("sqft")),
            "beds": C.num(m.group("beds")),
            "baths": C.num(m.group("baths")),
            "stories": C.num(m.group("stories")),
            "garage": C.num(m.group("garage")),
            "availLabel": m.group("avail").strip(),
            "price": C.money(m.group("price")),
            "url": None,
            "source": src,
            "asOf": asof,
        })

    expected = sum(len(re.findall(r"\$[\d,]{6,}\s*$", ln)) for ln in flat)
    if expected and len(out) != expected:
        raise SystemExit(
            "taylor_morrison: parsed %d rows but the sheet has %d priced lines. "
            "Refusing a partial ingest." % (len(out), expected))
    return out
