"""Parish-level employment composition and trend tool using BLS QCEW open API.

Answers the demand-side question for rental housing: what industries employ
people in a parish, how concentrated is the base, and is it growing?  Used
for buy/no-buy context and the workforce-housing angle in the petrochemical
corridor (Ascension, Iberville, West Baton Rouge).

DATA SOURCE — BLS QCEW open data (no API key required):
  https://data.bls.gov/cew/data/api/YEAR/QTR/area/AREA.csv

QCEW CSV structure (columns used):
  area_fips       — 5-digit county FIPS (e.g. 22005 = Ascension)
  own_code        — 5 = private sector; 0 = all ownership; 1 = federal govt …
  agglvl_code     — 71 = county-total private (one row); 74 = county/sector
                    (one row per NAICS supersector, ~20 rows)
  industry_code   — NAICS supersector code at agglvl 74
  month3_emplvl   — employment at end of the quarter's third month
  avg_wkly_wage   — average weekly wage for the quarter
  disclosure_code — 'N' means the cell is suppressed (too few establishments
                    to release without identifying individual employers). We
                    flag suppressed cells rather than silently treating them
                    as zero. This is common for manufacturing in small parishes
                    — Ascension's 31-33 (Mfg) is suppressed despite being the
                    economic backbone; use the 5-yr-prior row as an indicator.

QUARTER AVAILABILITY: BLS typically releases QCEW data ~5-6 months after the
reference quarter. As of mid-2026 the most recent available quarter is usually
Q4 of the prior year or Q1 of the current year. The fetcher tries Q1 of the
current year and falls back by one quarter at a time (up to 6 tries).

NAICS SUPERSECTOR NAMES (agglvl_code 74 industry codes):
  11 = Agriculture/Forestry/Fishing/Hunting
  21 = Mining/Quarrying/Oil & Gas
  22 = Utilities
  23 = Construction
  31-33 = Manufacturing
  42 = Wholesale Trade
  44-45 = Retail Trade
  48-49 = Transportation & Warehousing
  51 = Information
  52 = Finance & Insurance
  53 = Real Estate & Rental
  54 = Professional & Technical Services
  55 = Management of Companies
  56 = Administrative & Waste Services
  61 = Educational Services
  62 = Health Care & Social Assistance
  71 = Arts, Entertainment & Recreation
  72 = Accommodation & Food Services
  81 = Other Services
  99 = Unclassified

    python3 tools/jobs.py ascension
    python3 tools/jobs.py "east baton rouge" ascension
    python3 tools/jobs.py --all
"""

import csv
import io
import subprocess
import sys
from datetime import date

# Import parish FIPS mapping from the adjacent market.py module
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from market import PARISHES  # noqa: E402

QCEW_URL = "https://data.bls.gov/cew/data/api/{year}/{qtr}/area/{fips}.csv"

SECTOR_NAMES = {
    "11":    "Agriculture / Forestry / Fishing",
    "21":    "Mining / Oil & Gas",
    "22":    "Utilities",
    "23":    "Construction",
    "31-33": "Manufacturing",
    "42":    "Wholesale Trade",
    "44-45": "Retail Trade",
    "48-49": "Transportation & Warehousing",
    "51":    "Information",
    "52":    "Finance & Insurance",
    "53":    "Real Estate & Rental",
    "54":    "Professional & Technical Services",
    "55":    "Management of Companies",
    "56":    "Administrative & Waste Services",
    "61":    "Educational Services",
    "62":    "Health Care & Social Assistance",
    "71":    "Arts / Entertainment / Recreation",
    "72":    "Accommodation & Food Services",
    "81":    "Other Services",
    "99":    "Unclassified",
}

# Sectors relevant to workforce-housing demand in the petrochemical corridor
PETROCHEM_SECTORS = {"21", "22", "31-33", "23"}  # Mining/Oil, Utilities, Mfg, Construction
PETROCHEM_PARISHES = {"ascension", "iberville", "west baton rouge"}


def fetch_csv(url):
    """Fetch URL via curl -4, return raw text. Falls back to urllib on curl failure."""
    proc = subprocess.run(
        ["curl", "-4", "-sL", "--max-time", "45", "--retry", "2", url],
        capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout
    # Fallback to urllib
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "deal-intake/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read().decode()
    except Exception as exc:
        raise SystemExit(f"QCEW unreachable ({url}): curl exit {proc.returncode}, urllib: {exc}")


def parse_qcew(text):
    """Parse QCEW CSV, return list of row dicts."""
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def get_latest_quarter():
    """Return (year, qtr) of the most recent likely-available QCEW quarter.

    QCEW releases ~5-6 months after the quarter ends; mid-2026 means Q4 2025
    should be available but we try Q1 current year first and fall back.
    """
    today = date.today()
    year = today.year
    # Walk quarters backwards from current year Q1
    candidates = []
    for y in range(year, year - 2, -1):
        for q in range(4, 0, -1):
            candidates.append((y, q))
    return candidates  # caller iterates until a fetch succeeds


def fetch_area(fips_5, year, qtr):
    """Fetch QCEW CSV for a county-FIPS and quarter. Returns parsed rows or None."""
    url = QCEW_URL.format(year=year, qtr=qtr, fips=fips_5)
    text = fetch_csv(url)
    if not text or "area_fips" not in text:
        return None
    rows = parse_qcew(text)
    return rows if rows else None


def find_latest(fips_5):
    """Try quarters newest-first until data appears. Returns (rows, year, qtr)."""
    for year, qtr in get_latest_quarter():
        try:
            rows = fetch_area(fips_5, year, qtr)
            if rows:
                # Confirm the data actually has content (not an error page)
                private_total = [r for r in rows
                                 if r.get("own_code") == "5"
                                 and r.get("agglvl_code") == "71"]
                if private_total:
                    return rows, year, qtr
        except SystemExit:
            continue
    raise SystemExit(f"QCEW: no data found for FIPS {fips_5} in any recent quarter")


def extract_sectors(rows):
    """Return (total_private_empl, total_wage, sectors_list) from QCEW rows.

    sectors_list: list of dicts with keys:
        code, name, empl, wage, disclosed
    """
    private_total = [r for r in rows
                     if r.get("own_code") == "5" and r.get("agglvl_code") == "71"]
    if not private_total:
        raise SystemExit("QCEW rows missing private-sector total (own_code=5, agglvl=71)")
    pt = private_total[0]
    total_empl = int(pt["month3_emplvl"]) if pt["month3_emplvl"] else 0
    total_wage = int(pt["avg_wkly_wage"]) if pt["avg_wkly_wage"] else 0

    sector_rows = [r for r in rows
                   if r.get("own_code") == "5" and r.get("agglvl_code") == "74"]
    sectors = []
    for r in sector_rows:
        code = r["industry_code"].strip()
        disclosed = r.get("disclosure_code", "").strip() != "N"
        empl = int(r["month3_emplvl"]) if r["month3_emplvl"] and disclosed else 0
        wage = int(r["avg_wkly_wage"]) if r["avg_wkly_wage"] and disclosed else 0
        sectors.append({
            "code": code,
            "name": SECTOR_NAMES.get(code, f"NAICS {code}"),
            "empl": empl,
            "wage": wage,
            "disclosed": disclosed,
        })
    return total_empl, total_wage, sectors


def screen(parish):
    fips_3 = PARISHES[parish]
    fips_5 = f"22{fips_3}"

    # --- Latest quarter ---
    rows_now, yr_now, qtr_now = find_latest(fips_5)
    total_now, avg_wage_now, sectors_now = extract_sectors(rows_now)

    # --- 5 years prior (same quarter) ---
    yr_prior = yr_now - 5
    try:
        rows_prior = fetch_area(fips_5, yr_prior, qtr_now)
        _, _, sectors_prior = extract_sectors(rows_prior) if rows_prior else (None, None, [])
    except (SystemExit, TypeError):
        rows_prior = None
        sectors_prior = []

    prior_by_code = {s["code"]: s for s in sectors_prior}

    # Sort by employment descending, filter zero/suppressed to end
    ranked = sorted(sectors_now, key=lambda s: (-s["empl"], s["code"]))

    # Top sector concentration check (use disclosed employment sum)
    disclosed_total = sum(s["empl"] for s in sectors_now if s["disclosed"])
    top6 = [s for s in ranked if s["empl"] > 0][:6]
    # Also surface suppressed petrochem sectors not in top6
    suppressed_petrochem = [s for s in sectors_now
                            if not s["disclosed"] and s["code"] in PETROCHEM_SECTORS]

    # --- Print ---
    petrochem = parish in PETROCHEM_PARISHES
    print(f"\n{'=' * 66}")
    print(f"  {parish.title()} Parish — Employment Composition")
    print(f"  QCEW {yr_now} Q{qtr_now}  |  Total private employment: "
          f"{total_now:,}  |  Avg weekly wage: ${avg_wage_now:,}")
    if yr_prior and rows_prior:
        print(f"  5-year comparison: {yr_prior} Q{qtr_now}")
    print(f"{'=' * 66}")

    print(f"\n{'SECTOR':<38} {'EMPL':>7} {'SHARE':>6} {'W.WAGE':>7} "
          f"{'vs.AVG':>7} {'5YR CHG':>8}")
    print("-" * 66)

    for s in top6:
        share = s["empl"] / total_now * 100 if total_now else 0
        wage_vs = s["wage"] - avg_wage_now
        # 5-year change
        prior = prior_by_code.get(s["code"])
        if prior and prior["empl"] > 0:
            chg_abs = s["empl"] - prior["empl"]
            chg_pct = chg_abs / prior["empl"] * 100
            chg_str = f"{chg_abs:+,} ({chg_pct:+.1f}%)"
        elif prior and not prior["disclosed"]:
            chg_str = "prior suppressed"
        else:
            chg_str = "n/a"

        petro_flag = " *" if s["code"] in PETROCHEM_SECTORS and petrochem else ""
        name_display = s["name"][:36] + petro_flag
        print(f"  {name_display:<38} {s['empl']:>7,} {share:>5.1f}% "
              f"${s['wage']:>6,} {wage_vs:>+7,}  {chg_str}")

    # Concentration warning
    if top6 and total_now > 0:
        top_sector = top6[0]
        top_share = top_sector["empl"] / total_now * 100
        if top_share > 25:
            print(f"\n  !! CONCENTRATION: {top_sector['name']} = {top_share:.1f}% of private")
            print(f"     employment — single-industry dependence risk.")

    # Suppressed petrochem sectors (important even though undisclosed)
    if suppressed_petrochem:
        print(f"\n  SUPPRESSED (non-disclosable) sectors — data withheld by BLS")
        print(f"  to protect individual employer identity, but sector is present:")
        for s in suppressed_petrochem:
            prior = prior_by_code.get(s["code"])
            if prior and prior["empl"] > 0:
                prior_str = f"5yr-prior: {prior['empl']:,} jobs"
            elif prior and not prior["disclosed"]:
                prior_str = "5yr-prior also suppressed"
            else:
                prior_str = ""
            petro_label = " [PETROCHEM]" if s["code"] in PETROCHEM_SECTORS and petrochem else ""
            print(f"    {s['name']}{petro_label}  {prior_str}")

    # Petrochem corridor note
    if petrochem:
        print(f"\n  [CORRIDOR] Ascension/Iberville/WBR petrochemical complex: sectors marked")
        print(f"  with * (Mining/Utilities/Mfg/Construction) drive workforce-housing demand.")
        print(f"  Suppressed Mfg data means large plant employers dominate — ask broker")
        print(f"  for plant headcount (BASF, Honeywell, LyondellBasell, CF Industries).")

    print()


def main(argv):
    if "--all" in argv:
        targets = list(PARISHES)
    else:
        joined = " ".join(a.lower().replace("parish", "").strip() for a in argv)
        targets = [p for p in PARISHES if p in joined or joined in p]
        if not targets:
            known = ", ".join(sorted(PARISHES))
            raise SystemExit(f"Unknown parish: {joined!r}. Known: {known}")
    for p in targets:
        screen(p)


if __name__ == "__main__":
    main(sys.argv[1:])
