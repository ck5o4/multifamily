"""Parsers for rent rolls, T-12s, and comps exports. CSV / XLSX / PDF.

Design rules learned from real packages:
- Multi-sheet workbooks are normal. Parse ONE sheet, chosen by score, and say which.
- Total/subtotal rows must be dropped or unit counts double.
- A T-12's annual figure is the column under a "Total"/"Annual" header, not the
  last number on the line (trailing columns are often % fixed, start month, etc).
- Benchmark/comparison blocks below the expense table must not be summed in.
- Every parser returns a `notes` list. Nothing ambiguous is silently resolved.
"""

import csv
import re
from pathlib import Path
from statistics import median

import openpyxl

NUM_RE = re.compile(r"^\(?-?\$?\s*[\d,]+(?:\.\d+)?\)?$")
_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?$")

RENT_MIN, RENT_MAX = 200, 20000
SF_MIN, SF_MAX = 100, 6000
COUNT_MAX = 5000

# Plurals matter: the \b after a bare "total" does not match "Totals", so a
# "Totals" subtotal row survived as a unit type (sweep 2026-09-21). A totals row
# whose SF and rent sums fall under SF_MAX/RENT_MAX looks exactly like a large
# unit, so nothing downstream catches it.
TOTAL_ROW_RE = re.compile(
    r"^\s*(totals?|sub-?totals?|averages?|avg|sums?|grand)\b", re.I)

# Sweep 2026-09-28: the ^ anchor above only catches labels that BEGIN with a
# total word, so "Building Totals", "Property Total", "Portfolio Total",
# "Summary" and "All Units" still parsed as a unit type. On any deal under
# ~15 units the summed rent stays inside RENT_MAX, so the row reads as one
# large unit - the identical shape and magnitude as the CRITICAL "Totals" bug
# fixed 2026-09-21. On a 6-unit fixture a "Building Totals" row took levered
# IRR from -0.83% to +32.69%.
# "grand"/"avg"/"sum" stay anchored deliberately: searched anywhere they would
# eat a property named "Grand Isle" or a unit type "Avg 2BR".
_TOTAL_ROW_ANYWHERE_RE = re.compile(
    r"\b(totals?|sub-?totals?|averages?|summary|all\s+units)\b", re.I)


def is_total_row(label):
    """True when a rent-roll label is a totals/subtotal/summary row."""
    s = str(label or "")
    return bool(TOTAL_ROW_RE.match(s) or _TOTAL_ROW_ANYWHERE_RE.search(s))


def to_num(s):
    if s is None:
        return None
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if not t:
        return None
    # Sweep 2026-09-28: accounting exports write a negative four ways the old
    # NUM_RE rejected outright, and a rejected amount on a MATCHED expense line
    # was then dropped with NO note (`if not nums: continue` below) - the
    # category had matched, so it never reached `unmapped` either. A seller's
    # insurance of "$(84,000)" vanished and the $2,000/unit template was used
    # instead: NOI +$36,000, levered IRR +2.70% where the truth was -2.32%.
    #   "$(48,000)"   currency INSIDE accounting parens (Yardi/QuickBooks/Excel)
    #   "−18,120"     unicode minus (routine in PDF text extraction)
    #   "12,360-"     trailing minus
    # An UNBALANCED paren ("(1,200") is a split or truncated cell, not a number.
    # The old regex accepted it and returned +1200 - a silent SIGN FLIP.
    if t.count("(") != t.count(")"):
        return None
    neg = "(" in t and ")" in t
    t = t.replace("(", "").replace(")", "").replace("−", "-")
    t = t.replace("$", "").replace(",", "").strip()
    if t.endswith("-"):
        neg, t = True, t[:-1].strip()
    if t.startswith("-"):
        neg, t = True, t[1:].strip()
    if not _DIGITS_RE.fullmatch(t):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def has_letter(s):
    return bool(re.search(r"[A-Za-z]", str(s or "")))


# ---------------------------------------------------------------- readers

def read_sheets(path):
    """-> list[(sheet_name, rows)]. CSV and PDF yield a single entry."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        with open(p, newline="", encoding="utf-8-sig", errors="replace") as fh:
            return [(p.name, [[c.strip() for c in row] for row in csv.reader(fh)])]
    if ext in (".xlsx", ".xlsm"):
        wb = openpyxl.load_workbook(p, data_only=True)
        out = []
        for ws in wb.worksheets:
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v).strip() for v in r])
            out.append((ws.title, rows))
        return out
    if ext == ".pdf":
        return [(p.name, _pdf_rows(p))]
    raise ValueError(f"unsupported file type: {ext}")


def _pdf_rows(p):
    import fitz

    rows = []
    doc = fitz.open(p)
    for page in doc:
        for line in page.get_text("text").splitlines():
            if not line.strip():
                continue
            cells = [c.strip() for c in re.split(r"\s{2,}|\t", line.strip()) if c.strip()]
            if len(cells) == 1:
                cells = [c for c in line.split() if c]
            rows.append(cells)
    doc.close()
    return rows


def pdf_ocr_hint(path):
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return []
    import fitz

    doc = fitz.open(p)
    pages, chars = doc.page_count, 0
    imgs = 0
    for pg in doc:
        chars += len(pg.get_text("text").strip())
        imgs += len(pg.get_images())
    doc.close()
    return [f"PDF: {pages} pages, ~{chars // max(pages,1)} text chars/page, {imgs} embedded images. "
            "Nothing parseable was found - the data is almost certainly inside the images. "
            "OCR it first, or export CSV/XLSX from the source system."]


# ---------------------------------------------------------------- unit types

_BB_PATTERNS = [
    # lookarounds keep "3/2" from matching inside longer digit runs (dates,
    # account numbers): 01/15/2026 must not read as 1BR/1BA
    re.compile(r"(?<![\d/.])(\d)\s*[xX/]\s*(\d(?:\.5)?)(?![\d/])"),
    re.compile(r"(\d)\s*(?:br|bd|bed(?:room)?s?)\b\D{0,12}?(\d(?:\.5)?)\s*(?:ba|bath(?:room)?s?)", re.I),
    re.compile(r"(\d)\s*bed\D{0,12}?(\d(?:\.5)?)\s*bath", re.I),
]

_DATE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


def _nondate_text(row):
    """Row text for fallback bed/bath scans, with date-shaped cells removed -
    whole-row joins let the 3/2 pattern land inside lease dates."""
    return " ".join(t for t in (str(c).strip() for c in row)
                    if t and not _DATE_RE.match(t))


def parse_bed_bath(text):
    if not text:
        return None
    t = str(text)
    if re.search(r"\bstudio\b|\befficiency\b", t, re.I):
        return (0, 1.0)
    for pat in _BB_PATTERNS:
        m = pat.search(t)
        if m:
            return (int(m.group(1)), float(m.group(2)))
    m = re.search(r"(\d)\s*(?:br|bd|bed(?:room)?s?)\b", t, re.I)
    if m:
        return (int(m.group(1)), None)
    return None


def canonical_type(beds, baths):
    if beds == 0:
        return "Studio/ 1 BA"
    if baths is None:
        baths = 1.0 if beds == 1 else 2.0
    b = int(baths) if float(baths).is_integer() else baths
    return f"{beds} BR/ {b} BA"


# ---------------------------------------------------------------- rent roll

_HDR_TYPE = ("unit type", "floorplan", "floor plan", "unit desc", "description", "plan", "type", "style")
_HDR_SF = ("sqft", "sq ft", "sq. ft", "square", "area", "size", "nrsf", "sf")
# Rent-column preference, most conservative first. Underwriting runs on IN-PLACE
# rent; "market rent" is the seller's aspiration and belongs last. Before the
# 2026-09-21 sweep this tuple was ordered market-first AND was dead code anyway -
# _find_header took whichever rent-ish column appeared leftmost, so a Yardi export
# ordering Market before Current silently underwrote the building at asking rents
# and suppressed the zero-rent vacancy warning with it.
_HDR_RENT = ("actual rent", "current rent", "in place rent", "in-place rent",
             "lease rent", "scheduled rent", "rent", "market rent", "asking")
_HDR_COUNT = ("# of units", "# units", "no. of units", "number of units", "unit count", "units", "qty", "count")


def _rent_rank(header_cell):
    """Position of the most-preferred _HDR_RENT keyword this header matches.

    Lower is better; None means the cell is not a rent column. Longer keywords
    are tested first so "market rent" is not scored as the bare "rent".
    """
    ranked = sorted(enumerate(_HDR_RENT), key=lambda kv: -len(kv[1]))
    for rank, keyword in ranked:
        if keyword in header_cell:
            return rank
    return None


def _find_header(rows):
    for i, row in enumerate(rows[:80]):
        low = [str(c).lower().strip() for c in row]
        has_type = any(any(k in c for k in _HDR_TYPE) for c in low)
        has_rent = any(any(k in c for k in _HDR_RENT) for c in low)
        has_sf = any(any(k in c for k in _HDR_SF) for c in low)
        if not (has_rent and (has_type or has_sf) and len(row) >= 2):
            continue
        idx = {}
        rent_cands = []
        for j, c in enumerate(low):
            if not c:
                continue
            if "count" not in idx and any(k in c for k in _HDR_COUNT):
                idx["count"] = j
                continue
            if "type" not in idx and any(k in c for k in _HDR_TYPE):
                idx["type"] = j
                continue
            if "sf" not in idx and any(k in c for k in _HDR_SF):
                idx["sf"] = j
                continue
            rank = _rent_rank(c)
            if rank is not None:
                rent_cands.append((rank, j, row[j]))
        if rent_cands:
            # Preference order decides, NOT column position: a roll listing
            # Market Rent left of Current Rent must still underwrite in-place.
            rank, j, label = min(rent_cands, key=lambda t: (t[0], t[1]))
            idx["rent"] = j
            idx["rent_label"] = str(label).strip()
            if len(rent_cands) > 1:
                idx["rent_alternates"] = [str(l).strip()
                                          for _, jj, l in sorted(rent_cands, key=lambda t: t[1])
                                          if jj != j]
            return i, idx
    return None, None


def _rent_records(rows):
    """-> (records, fallback, notes) from one sheet. Header-driven; falls back
    to line scan. A record's rent is None for vacant/zero-rent rows - the unit
    still counts; parse_rent_roll imputes the rent from the type median."""
    hi, idx = _find_header(rows)
    recs, fallback, rnotes = [], False, []

    if hi is not None:
        cnt_seq = []
        for row in rows[hi + 1:]:
            if not any(str(c).strip() for c in row):
                continue
            if is_total_row(row[0] if row else ""):
                continue

            def get(key):
                j = idx.get(key)
                return row[j] if j is not None and j < len(row) else None

            label = str(get("type") or "").strip()
            if is_total_row(label):
                continue
            if label.lower() in _HDR_TYPE or label.lower() in ("unit", "units", "unit no", "apt"):
                continue  # repeated header row inside a stacked table
            bb = parse_bed_bath(label)
            if bb is None:
                # Floorplan codes ("A1", "B2") carry no bed/bath. The real
                # designation is usually in another column on the same row.
                bb = parse_bed_bath(_nondate_text(row))
            if bb is None and not has_letter(label):
                continue  # total row or numeric junk
            rent = to_num(get("rent"))
            if rent is not None and RENT_MIN <= rent <= RENT_MAX:
                pass
            elif rent in (None, 0.0):
                rent = None  # vacant/subsidy unit: keep it in the count
            else:
                continue  # nonzero but implausible - junk row
            sf = to_num(get("sf"))
            if sf is not None and not (SF_MIN <= sf <= SF_MAX):
                sf = None
            cnt = to_num(get("count")) if "count" in idx else None
            if cnt is not None and not (0 < cnt <= COUNT_MAX):
                cnt = None
            if cnt is not None:
                cnt_seq.append(cnt)
            recs.append({"label": label, "bb": bb, "sf": sf, "rent": rent, "count": cnt or 1.0})

        # A "Units" column of unit NUMBERS is IDs, not counts: 30 rows numbered
        # 1..30 must not become 465 units.
        if len(cnt_seq) >= 5 and all(float(c).is_integer() for c in cnt_seq) \
                and all(b > a for a, b in zip(cnt_seq, cnt_seq[1:])):
            for r in recs:
                r["count"] = 1.0
            rnotes.append(f"count column runs {int(cnt_seq[0])}..{int(cnt_seq[-1])} strictly "
                          "increasing - read as unit numbers, not counts; counted 1 unit per row.")
    else:
        fallback = True
        cand = []
        for row in rows:
            if is_total_row(row[0] if row else ""):
                continue
            bb = parse_bed_bath(_nondate_text(row))
            if not bb:
                continue
            in_rng = [(j, n) for j, n in ((j, to_num(c)) for j, c in enumerate(row))
                      if n is not None and RENT_MIN <= n <= RENT_MAX]
            if not in_rng:
                continue
            cand.append((row, bb, in_rng))

        # Rent = the column index most rows agree on. "Last in-range number"
        # let a trailing deposit column win ('2BR/1BA, 900, 850, 500' -> 500).
        votes = {}
        for _, _, in_rng in cand:
            for j, _ in in_rng:
                votes[j] = votes.get(j, 0) + 1
        rent_col = None
        if votes:
            top = max(votes.values())
            tops = [j for j, v in votes.items() if v == top]
            if len(tops) == 1 and top >= max(2, len(cand) // 2):
                rent_col = tops[0]

        all_rents = []
        for row, bb, in_rng in cand:
            rent = dict(in_rng).get(rent_col, in_rng[-1][1]) if rent_col is not None else in_rng[-1][1]
            all_rents += [n for _, n in in_rng]
            nums = [n for n in (to_num(c) for c in row) if n is not None]
            sf = next((n for n in nums if SF_MIN <= n <= SF_MAX and n != rent), None)
            recs.append({"label": " ".join(str(c) for c in row)[:40], "bb": bb,
                         "sf": sf, "rent": rent, "count": 1.0})
        if recs and rent_col is None:
            rnotes.append("line scan: no rent-column consensus - took last in-range value per "
                          f"row. In-range values min ${min(all_rents):,.0f} / median "
                          f"${median(all_rents):,.0f} / max ${max(all_rents):,.0f} - verify "
                          "the rent is not a deposit or SF column.")

    return recs, fallback, rnotes


RENT_SHEET_HINTS = ("rent roll", "rentroll", "rent_roll", "unit mix", "unitmix",
                    "revenue", "rents", "subject", "rr")
COMP_SHEET_HINTS = ("comp", "market survey", "survey")


def _score_sheet(name, recs, mode):
    """bed/bath row count, with a heavy bonus for a sheet named for the job.
    Without the name bonus a workbook's comp table outscores the subject's own
    unit mix purely on row count."""
    n = sum(1 for r in recs if r["bb"] is not None)
    if n == 0:
        return -1
    low = str(name).lower()
    bonus = 0
    if mode == "rent":
        if any(h in low for h in RENT_SHEET_HINTS):
            bonus += 1000
        if any(h in low for h in COMP_SHEET_HINTS):
            bonus -= 500
    else:
        if any(h in low for h in COMP_SHEET_HINTS):
            bonus += 1000
    return bonus + n


def parse_rent_roll(path, sheet=None, mode="rent"):
    """-> (groups, notes). groups aggregated by canonical unit type."""
    notes = []
    sheets = read_sheets(path)
    if sheet:
        sheets = [(n, r) for n, r in sheets if n.lower() == sheet.lower()]
        if not sheets:
            return [], [f"sheet {sheet!r} not found in {Path(path).name}"]

    scored = []
    for name, rows in sheets:
        recs, fb, rn = _rent_records(rows)
        scored.append((_score_sheet(name, recs, mode), name, recs, fb, rn))
    scored.sort(key=lambda t: -t[0])
    best_score, best_name, best_recs, best_fb, best_rn = scored[0]

    if len(sheets) > 1:
        cands = [f"{n} ({sum(1 for r in rc if r['bb'])})" for s, n, rc, _, _ in scored[:4] if s > -1]
        notes.append(f"selected sheet {best_name!r} out of {len(sheets)}. "
                     f"Candidates (bed/bath rows): {', '.join(cands) or 'none'}. "
                     f"Override with --sheet if wrong.")
    if best_fb:
        notes.append("No header row detected - fell back to line scanning (verify output).")
    notes += best_rn
    if not best_recs:
        return [], notes + ["No rent rows parsed."] + pdf_ocr_hint(path)

    def _key(rec):
        return canonical_type(rec["bb"][0], rec["bb"][1]) if rec["bb"] else (rec["label"] or "UNKNOWN")

    vacant = [r for r in best_recs if r["rent"] is None]
    if vacant:
        meds = {}
        for r in best_recs:
            if r["rent"]:
                meds.setdefault(_key(r), []).append(r["rent"])
        for r in vacant:
            k = _key(r)
            r["rent"] = median(meds[k]) if k in meds else 0.0
        n_vac = int(round(sum(r["count"] for r in vacant)))
        n_tot = int(round(sum(r["count"] for r in best_recs)))
        notes.append(f"{n_vac} vacant/zero-rent rows: "
                     "counted as units, rent imputed from type median")
        # Surface the roll's OWN physical vacancy. The imputed rents put these
        # units into GPR, so unless the vacancy input is raised to match, the
        # model prices them as if they were let (sweep 2026-08-24). On the real
        # baker-trails OM roll that is 4 of 12 units - 33.3% against a 7%
        # template default, roughly $30k/yr of NOI on a $750k asset.
        if n_tot:
            notes.append(f"IMPLIED PHYSICAL VACANCY {n_vac}/{n_tot} = {n_vac / n_tot:.1%} "
                         "from this rent roll - compare against the vacancy input before "
                         "trusting NOI")

    buckets, unresolved = {}, 0
    for rec in best_recs:
        if rec["bb"]:
            key = canonical_type(rec["bb"][0], rec["bb"][1])
        else:
            key = rec["label"] or "UNKNOWN"
            unresolved += 1
        b = buckets.setdefault(key, {"units": 0.0, "sf_w": 0.0, "rent_w": 0.0, "sf_n": 0.0})
        n = rec["count"]
        b["units"] += n
        b["rent_w"] += rec["rent"] * n
        if rec["sf"]:
            b["sf_w"] += rec["sf"] * n
            b["sf_n"] += n

    groups = []
    for k, b in buckets.items():
        groups.append({
            "type": k,
            "units": int(round(b["units"])),
            "sf": int(round(b["sf_w"] / b["sf_n"])) if b["sf_n"] else 0,
            "rent": int(round(b["rent_w"] / b["units"])) if b["units"] else 0,
        })
    groups.sort(key=lambda g: -g["units"])

    if unresolved:
        notes.append(f"{unresolved} row(s) had no parseable bed/bath - grouped under raw label.")
    if any(g["sf"] == 0 for g in groups):
        notes.append("Some unit types have no SF - rent/SF will read 0. Fill manually if needed.")
    return groups, notes


def parse_comps(path, sheet=None):
    return parse_rent_roll(path, sheet=sheet, mode="comp")


# ---------------------------------------------------------------- T-12

_T12_MAP = [
    ("payroll", ("payroll", "salaries", "salary", "wages", "on-site", "onsite", "personnel", "employee")),
    ("insurance", ("insurance", "hazard")),
    ("taxes_annual", ("property tax", "real estate tax", "ad valorem", "taxes")),
    ("mgmt_pct", ("management fee", "mgmt fee", "property management")),
    ("marketing", ("marketing", "advertis", "promotion", "leasing", "locator")),
    ("rm", ("repair", "maintenance", "r&m", "turnover", "make ready", "make-ready")),
    # "lawn"/"mow" and "garbage"/"refuse"/"sanitation" were absent, though
    # tools/README.md already promised trash/waste keys into one of these two.
    # Live 2026-09-28: eden-church-mhp/PL_2025_ACTUALS.xlsx carries "Lawn Care"
    # $3,600/yr and hwy42-mhp/'P&L 2025.xlsx' "Lawn Care" $4,000/yr - both fell
    # into the unmapped note instead of an expense line. ("Garbage" $3,936 on
    # eden happens to be inside the hand-set $802/unit utilities figure, so that
    # one is not a live error - the keyword gap is.)
    ("contract_services", ("contract", "landscap", "lawn", "mow", "pest", "janitor",
                           "security", "grounds", "pool service")),
    ("utilities", ("utilit", "water", "sewer", "electric", "gas", "trash", "waste",
                   "garbage", "refuse", "sanitation")),
    ("ga", ("general", "administrat", "admin", "office", "legal", "accounting", "bank charge")),
    ("other", ("other", "miscellaneous", "misc")),
]

_SKIP = ("total", "net operating", "noi", "gross", "subtotal", "effective", "income",
         "revenue", "capital", "reserve", "debt service", "depreciation", "amortization",
         "growth rate", "per unit")

# Stop only at STATEMENT-ENDING totals. Interior group subtotals ("Total
# Utilities") must fall through to _SKIP, not terminate the parse — audit
# 2026-08-05: the old ^total\b stop silently dropped every expense line after
# the first subtotal (insurance/taxes/R&M lost on grouped statements).
_STOP_RE = re.compile(r"^\s*(total\s+(operating|expenses?)\b|net operating|noi\b)", re.I)

# A line that OFFSETS the expense it names, rather than adding to it.
# "recovery" is deliberately absent: "Utility Recovery" is a credit but
# "Recovery Services" is a vendor, and the false positive flips a real cost
# negative. Rebate/refund/reimburs/credit are unambiguous on an expense row.
# The (?!\s*card) guard is load-bearing: "Credit Card Fees" is an ordinary G&A
# bank charge, and matching it would flip a real expense negative.
_CREDIT_RE = re.compile(r"\b(rebate|refund|reimburs\w*|credit)s?\b(?!\s*card)", re.I)

# "'Flood Ins' / 'Hazard & Liability Ins'": bare 'ins'/'ins.' at word end is
# insurance shorthand; 'painting'/'maintenance' must not match.
_INS_ABBR_RE = re.compile(r"\bins\.?(?:\s|$)")

# 'year' deliberately absent: it selected 'Year Built'/'Year Model' columns and
# read the year (2018) as the annual dollar figure.
_TOTAL_HDR = ("total", "annual", "ttm", "t-12", "t12", "trailing", "12 month", "twelve")
_NOT_TOTAL_HDR = ("per unit", "/unit", "%", "month prior", "start month", "budget", "variance", "psf", "/sf")


def _find_total_col(rows):
    """Index of the column holding annual totals, from a header row.

    Guarded against title lines: 'Bayou Ridge - Trailing 12 Operating Statement'
    contains 'trailing' but is one long cell, not a column header. A real header
    row has 2+ cells and the matching cell is a short word like 'Total'."""
    for i, row in enumerate(rows[:60]):
        low = [str(c).lower().strip() for c in row]
        if sum(1 for c in low if c) < 2:
            continue  # single-cell row = title, not a header
        hits = []
        for j, c in enumerate(low):
            if not c or len(c) > 24:
                continue
            if any(k in c for k in _NOT_TOTAL_HDR):
                continue
            if any(c == k or c.startswith(k) or k in c for k in _TOTAL_HDR):
                hits.append(j)
        if hits:
            # Sweep 2026-09-28: this used to return the FIRST match, so a
            # two-year statement headed "Total 2024 | Total 2025" was parsed on
            # 2024 - silently underwriting the older, smaller year. On the
            # control fixture that read payroll $28,000 instead of $41,160 and
            # insurance $21,000 instead of $48,000: NOI $154,752 vs $101,112,
            # levered IRR 16.66% vs 2.70% - a false PURSUE at the 16% rung.
            # The rightmost total-ish column is the most recent period.
            return i, hits[-1], hits, [str(row[j]).strip() for j in hits]
    return None, None, [], []


def _t12_from_rows(rows):
    """-> (lines, matched_row_count, notes, has_total_col)"""
    hi, tcol, tcol_idxs, tcol_hdrs = _find_total_col(rows)
    # The part-year detector counts populated PERIOD cells left of the total
    # column. When a statement carries several total columns ("Total 2024 |
    # Total 2025") the cells left of the chosen one are the OTHER totals, not
    # months - counting them read a legitimate two-year statement as a
    # "1-month partial year". Count only up to the FIRST total column.
    period_hi = tcol_idxs[0] if tcol_idxs else tcol
    out, lnotes = {}, []
    matched = 0
    seen = {}      # normalized label -> last annual (duplicate-label guard)
    unmapped = {}  # label -> dollar amount that matched no category
    period_counts = []  # populated month columns per line (part-year detector)

    for ri, row in enumerate(rows):
        if hi is not None and ri <= hi:
            continue
        cells_raw = [str(c).strip() for c in row]
        cells = [c for c in cells_raw if c]
        if len(cells) < 2:
            continue
        label = cells[0]
        low = label.lower()

        if matched and _STOP_RE.match(label):
            break
        if any(s in low for s in _SKIP):
            continue

        key = None
        for k, kws in _T12_MAP:
            if any(kw in low for kw in kws):
                key = k
                break
        if key is None and _INS_ABBR_RE.search(low):
            key = "insurance"
        if key is None:
            if has_letter(label) and label not in unmapped:
                amts = [abs(n) for n in (to_num(c) for c in cells[1:]) if n is not None]
                if amts and max(amts) >= 100:
                    unmapped[label] = max(amts)
            continue

        # A credit/refund line OFFSETS the expense it names; abs() made it ADD.
        # Sweep 2026-09-28: "Insurance 48,000" + "Insurance Rebate (6,000)"
        # came to $54,000 where the truth is $42,000; "Utilities 18,120" +
        # "Utility Reimbursement -4,800" to $22,920 against $13,320. Direction
        # KILLS deals (opex overstated $21,600/yr on the control fixture,
        # levered IRR 1.00% vs 4.83%), so it never produced a false PURSUE -
        # but it is still a wrong number on every statement that nets credits.
        is_credit = bool(_CREDIT_RE.search(low))

        annual, basis = None, None
        if tcol is not None and tcol < len(cells_raw):
            v = to_num(cells_raw[tcol])
            if v is not None:
                # A credit reduces the line regardless of how the export signs
                # it: some write "(6,000)", some a bare "6,000" under a
                # "Rebate" label. -abs() is right either way.
                annual = -abs(v) if is_credit else abs(v)
                basis = f"column {tcol + 1} under total header"
                if tcol_hdrs:
                    basis += f" ({tcol_hdrs[-1]!r})"
                if is_credit:
                    basis += " [credit - offsets the line it names]"
                # Count the populated period cells feeding that total, so a
                # part-year statement cannot be read as annual (sweep
                # 2026-08-24). hwy42's 'P&L YTD 2026.xlsx' has JAN-JUN only:
                # every expense line came back at half its annual size, with
                # basis still reading 'under total header'.
                _periods = sum(1 for c in cells_raw[1:period_hi]
                               if (to_num(c) or 0) != 0)
                if 0 < _periods < 12:
                    period_counts.append(_periods)
        if annual is None:
            signed = [n for n in (to_num(c) for c in cells[1:]) if n is not None]
            nums = signed if is_credit else [abs(n) for n in signed]
            if not signed:
                # The category MATCHED but no cell parsed as a number. Before
                # 2026-09-28 this fell through `continue` silently: the line was
                # not in `unmapped` (the category had matched) and no note fired,
                # so a real expense simply disappeared and the template default
                # was used in its place.
                lnotes.append(
                    f"{label!r} matched an expense category but no amount on the "
                    f"row could be parsed - NOT captured "
                    f"(cells: {'; '.join(cells[1:]) or '(none)'})")
                continue
            if len(nums) >= 12:
                # Sweep 2026-09-28: the test was `== 12`, so 12 monthly columns
                # plus ANY 13th (a "Per Unit/Yr" or "% of EGR" column, routine on
                # broker statements) fell through to "last plausible value" and
                # read that 13th cell as the ANNUAL figure. intake then divided
                # by units a SECOND time: payroll $41,160 -> $1,715 -> $71/unit.
                # Opex understated $137,655/yr; NOI $238,824 vs $101,112; levered
                # IRR 37.70% vs 2.70% - clearing even the 22% "ideal" rung.
                annual, basis = sum(nums[:12]), "sum of 12 monthly columns"
                if len(nums) > 12:
                    basis += (f" (ignored {len(nums) - 12} trailing column(s); "
                              f"a 13th column is per-unit or a ratio, not an annual total)")
            else:
                big = [n for n in nums if abs(n) >= 100]
                annual = (big or nums)[-1]
                basis = ("last plausible value (totals column empty on this line)"
                         if tcol is not None else "last plausible value (no total header found)")
            if is_credit:
                annual = -abs(annual)
                basis += " [credit - offsets the line it names]"

        matched += 1
        prev = seen.get(low)
        if prev is not None:
            # Sweep 2026-09-28: this kept the LAST block on the theory that
            # "later = more current". On real broker statements the later block
            # is the seller's PRO FORMA, not the actual. Live on
            # hwy42-mhp/'P&L 2025.xlsx': rows 41-50 are 2025 actuals (Electric
            # -6,176, annotated "2025 included Air B&B rentals"); rows 60-69
            # repeat every label with the seller's 2026-adjusted figures
            # (Electric -1,200, "Adjusted for 2026 No Air B&B"). Keeping last
            # discarded $4,976/yr of real utility cost on a 30-pad park paying
            # electric, trash and a private sewer plant - $71,086 of price at a
            # 7% cap. A stacked multi-building statement fails the same way.
            # Keep the LARGER figure: on an expense that is the conservative
            # read, and it is the only choice that cannot manufacture a PURSUE.
            keep, drop = max(annual, prev), min(annual, prev)
            out[key]["annual"] += keep - prev
            lnotes.append(f"duplicate {label!r} lines: kept larger (${keep:,.0f}), "
                          f"ignored ${drop:,.0f} - if the smaller figure is the "
                          f"right one, set it explicitly with --set")
            seen[low] = keep
            continue
        if key in out:
            out[key]["annual"] += annual
            out[key]["label"] += f" + {label}"
        else:
            out[key] = {"annual": annual, "label": label, "basis": basis}
        seen[low] = annual

    if unmapped:
        lnotes.append("lines with dollar amounts that matched no expense category (NOT captured): "
                      + "; ".join(f"{l} (${a:,.0f})" for l, a in unmapped.items()))
    # If most lines agree that fewer than 12 periods are populated, this is a
    # part-year statement being read as annual. Say so loudly and label the
    # basis; never silently annualize (the run rate of a half year is not a
    # T-12, and which half it is matters for insurance and taxes).
    if period_counts and len(period_counts) >= max(2, matched // 2):
        modal = max(set(period_counts), key=period_counts.count)
        if period_counts.count(modal) >= len(period_counts) / 2 and modal < 12:
            lnotes.append(
                f"PART-YEAR STATEMENT: only {modal} of 12 period columns are populated, so "
                f"every figure below is a {modal}-month total, NOT annual. Multiply by "
                f"{12 / modal:.2f} only if the missing months are comparable, or supply a "
                f"full T-12 with --t12.")
            for k in out:
                out[k]["basis"] += f" [{modal}-month partial year, NOT annual]"
    return out, matched, lnotes, tcol is not None


def parse_t12(path, units=None, sheet=None):
    """-> (lines, notes). lines = {key: {'annual', 'label', 'basis'}}"""
    notes = []
    sheets = read_sheets(path)
    if sheet:
        sheets = [(n, r) for n, r in sheets if n.lower() == sheet.lower()]
        if not sheets:
            return {}, [f"sheet {sheet!r} not found in {Path(path).name}"]

    cands = []
    for name, rows in sheets:
        out, n, lnotes, has_tcol = _t12_from_rows(rows)
        cands.append({"name": name, "out": out, "n": n, "lnotes": lnotes, "tcol": has_tcol})
    # A sheet with a detected annual-totals column is a statement; one without
    # is usually a ledger. Row count alone let a junk ledger beat the real T-12.
    cands.sort(key=lambda c: (-int(c["tcol"]), -c["n"]))
    best = cands[0]
    best_name, best_out, best_n = best["name"], best["out"], best["n"]
    notes += best["lnotes"]

    if len(sheets) > 1:
        losers = ", ".join(f"{c['name']!r} ({c['n']} lines, "
                           f"{'totals col' if c['tcol'] else 'no totals col'})"
                           for c in cands[1:])
        notes.append(f"selected sheet {best_name!r} out of {len(sheets)} ({best_n} expense lines "
                     f"matched, {'totals column detected' if best['tcol'] else 'no totals column'}). "
                     f"Passed over: {losers}.")
    if not best_out:
        notes.append("No T-12 expense lines matched. Check the file layout.")
        notes += pdf_ocr_hint(path)
    if units:
        for v in best_out.values():
            v["per_unit"] = v["annual"] / units
    return best_out, notes


def compare_to_benchmarks(t12_lines, units, benchmarks, tol=0.25):
    rows = []
    for key, bm in benchmarks.items():
        actual = t12_lines.get(key, {}).get("annual")
        pu = (actual / units) if (actual is not None and units) else None
        dev, flag = None, ""
        if pu is not None and pu > 0 and bm["current"]:
            dev = pu / bm["current"] - 1
            if abs(dev) > tol:
                flag = "HIGH" if dev > 0 else "LOW"
        rows.append({"key": key, "actual_per_unit": pu, "current": bm["current"],
                     "stoa": bm["stoa"], "deviation": dev, "flag": flag})
    return rows
