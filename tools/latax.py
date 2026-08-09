"""Louisiana property tax estimation for acquisitions.

A purchase resets the assessment to the sale price. Carrying the seller's
current tax bill into a pro forma understates expenses, which overstates NOI,
which overstates both the DSCR-constrained loan and the IRR.

    annual tax = purchase price x assessment ratio x (mills / 1000)

Assessment ratio: Louisiana Constitution Art. VII sec. 18 assesses land and
improvements for residential purposes at 10%, other property at 15%. Apartments
are improvements for residential purposes, so multifamily takes the 10% ratio -
not the 15% commercial ratio people often assume. A mixed-use building splits:
the residential portion at 10%, the commercial portion at 15%.

Millage is parish-wide and varies by taxing district inside each parish. These
are starting estimates - confirm the total for the specific parcel with the
parish assessor before making an offer.
"""

ASSESSMENT_RATIO_RESIDENTIAL = 0.10
ASSESSMENT_RATIO_COMMERCIAL = 0.15

# Parish totals in mills (per $1,000 of assessed value).
# Louisiana Tax Commission annual reports, via Southern Title, March 2026.
MILLAGE = {
    "east baton rouge":      108.80,
    # City of Lafayette 105.20 (2025); unincorporated parish 88.29. City figure
    # used because it is conservative and most listings are in-city.
    "lafayette":             105.20,
    "orleans":               131.99,
    "jefferson":             118.40,
    "ascension":             115.00,
    "livingston":            100.10,
    "tangipahoa":            100.00,
    "st. tammany":           125.61,
    "st. bernard":           141.10,
    "plaquemines":            70.69,
    "st. john the baptist":  108.40,
    "st. charles":           100.85,
    "west baton rouge":       98.84,
    "iberville":             103.50,
    "st. james":             105.39,
}

# Towns across the Baton Rouge <-> New Orleans corridor and both metros.
CITY_TO_PARISH = {}
for _parish, _towns in {
    "east baton rouge": ["baton rouge", "baker", "zachary", "central", "greenwell springs", "pride"],
    "lafayette": ["lafayette", "broussard", "youngsville", "carencro", "scott", "duson"],
    "orleans": ["new orleans", "nola", "algiers"],
    "jefferson": ["metairie", "kenner", "gretna", "harvey", "marrero", "westwego",
                  "terrytown", "harahan", "river ridge", "avondale", "jefferson", "elmwood"],
    "ascension": ["gonzales", "prairieville", "geismar", "sorrento", "donaldsonville",
                  "st. amant", "st amant", "dutchtown", "darrow"],
    "livingston": ["denham springs", "walker", "livingston", "watson", "springfield",
                   "french settlement", "albany", "holden"],
    "tangipahoa": ["hammond", "ponchatoula", "amite", "independence", "tickfaw",
                   "robert", "loranger", "kentwood"],
    "st. tammany": ["slidell", "covington", "mandeville", "madisonville", "abita springs",
                    "pearl river", "lacombe", "folsom"],
    "st. bernard": ["chalmette", "arabi", "meraux", "violet", "poydras"],
    "plaquemines": ["belle chasse", "port sulphur", "buras", "boothville", "venice"],
    "st. john the baptist": ["laplace", "la place", "reserve", "garyville", "edgard"],
    "st. charles": ["luling", "destrehan", "boutte", "norco", "hahnville", "st. rose",
                    "st rose", "paradis"],
    "west baton rouge": ["port allen", "addis", "brusly", "erwinville"],
    "iberville": ["plaquemine", "white castle", "maringouin", "st. gabriel", "st gabriel"],
    "st. james": ["gramercy", "lutcher", "vacherie", "convent", "paulina"],
}.items():
    for _t in _towns:
        CITY_TO_PARISH[_t] = _parish


import re


def _norm(s):
    key = str(s or "").strip().lower().replace(",", "")
    # Tolerate "Denham Springs, LA", "Baker, LA 70714", "Gonzales, Louisiana"
    # (audit 2026-08-05: state/zip suffixes silently failed parish resolution,
    # and intake then SKIPPED tax reassessment with only a NOTES line)
    key = re.sub(r"\s+\d{5}(-\d{4})?$", "", key)
    key = re.sub(r"\s+(la|louisiana)$", "", key)
    return key.strip()


def resolve_parish(location):
    """Accept a town or a parish name. -> (parish_key, how) or (None, reason)."""
    key = _norm(location)
    if not key:
        return None, "no location given"
    if key in MILLAGE:
        return key, "parish named directly"
    if key in CITY_TO_PARISH:
        p = CITY_TO_PARISH[key]
        return p, f"{location.title()} is in {p.title()} Parish"
    # tolerate "St Tammany" / "saint tammany"
    alt = key.replace("saint ", "st. ").replace("st ", "st. ")
    if alt in MILLAGE:
        return alt, "parish named directly"
    if alt in CITY_TO_PARISH:
        return CITY_TO_PARISH[alt], f"{location.title()} is in {CITY_TO_PARISH[alt].title()} Parish"
    return None, (f"don't know where {location!r} is. Known towns include: "
                  "Baton Rouge, New Orleans, Hammond, Gonzales, Prairieville, Denham Springs, "
                  "Walker, LaPlace, Slidell, Covington, Mandeville, Metairie, Kenner, "
                  "Port Allen, Plaquemine. Or name the parish directly.")


def estimate_tax(price, location, commercial_share=0.0):
    """-> (annual_tax, explanation) or (None, reason).

    commercial_share: fraction of value in non-residential use (mixed-use ground
    floor retail), assessed at 15% instead of 10%.
    """
    parish, how = resolve_parish(location)
    if parish is None:
        return None, how
    mills = MILLAGE[parish]
    res_val = price * (1 - commercial_share)
    com_val = price * commercial_share
    assessed = res_val * ASSESSMENT_RATIO_RESIDENTIAL + com_val * ASSESSMENT_RATIO_COMMERCIAL
    tax = assessed * (mills / 1000.0)
    ratio_txt = "10% residential" if not commercial_share else \
        f"10% on {(1-commercial_share)*100:.0f}% residential + 15% on {commercial_share*100:.0f}% commercial"
    exp = (f"${price:,.0f} assessed at {ratio_txt} x {mills:.2f} mills "
           f"({parish.title()} Parish) = ${tax:,.0f}/yr - {how}")
    return tax, exp
