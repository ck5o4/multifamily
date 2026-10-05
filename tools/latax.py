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
    # Sweep 2026-10-05: tools/README.md promises "Town names or parish names
    # both work", but "Ascension Parish", "Orleans Parish" and "East Baton Rouge
    # Parish" all resolved to None, and the loud failure path freezes taxes
    # across every trial price - an optimistic ladder, on a string a human types
    # naturally. market.py:150 and jobs.py:297 already strip this word.
    # No MILLAGE key contains "parish", so this cannot shadow one; and
    # "Jefferson Davis Parish" correctly stays unresolved rather than becoming
    # Jefferson.
    key = re.sub(r"\s+parish$", "", key)
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


def infer_parish(location):
    """Resolve a town, parish name OR street address to a parish.

    -> (parish_key, how) or (None, reason).

    Sweep 2026-10-05. `defaults.wind_exposed` decided the $2,000-vs-$3,000
    wind-insurance basis by raw substring test against a hand-kept town tuple,
    which was wrong in both directions: six towns this module places in a wind
    parish (poydras/St. Bernard, elmwood/Jefferson, port sulphur, buras,
    boothville, venice/Plaquemines) returned False and were written up as
    "is inland", while any string merely CONTAINING a wind name matched -
    "5555 Jefferson Hwy, Baton Rouge", "1234 Harvey Ln, Baton Rouge",
    "900 Algiers St, Lafayette" and the real, non-coastal "Jefferson Davis
    Parish" all read as coastal.

    So geography is resolved here, once, against CITY_TO_PARISH:
      1. exact parish or town name (via resolve_parish),
      2. for anything that explicitly says "<name> Parish" and did not resolve,
         STOP - do not token-match, or "Jefferson Davis Parish" becomes
         Jefferson,
      3. otherwise treat it as an address: whole-token city match, longest
         match wins, ties broken toward the END of the string (the city
         position), so "Central Ave, Metairie" is Jefferson and not EBR.
    Unresolvable returns None so callers can disclose "unknown" rather than
    assert a basis.
    """
    raw = str(location or "").strip().lower().replace(",", "")
    if not raw:
        return None, "no location given"

    parish, how = resolve_parish(location)
    if parish:
        return parish, how

    if re.search(r"\bparish\b", raw):
        return None, (f"{location!r} names a parish that is not in the millage "
                      f"table. Known: {', '.join(sorted(MILLAGE))}")

    tokens = _norm(location).split()
    best = None   # (end_index, n_words, parish)
    for city, par in CITY_TO_PARISH.items():
        ct = city.split()
        n = len(ct)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == ct:
                cand = (i + n, n, par)
                if best is None or cand[:2] > best[:2]:
                    best = cand
    if best:
        return best[2], (f"matched town {' '.join(tokens[best[0]-best[1]:best[0]])!r} "
                         f"in {best[2].title()} Parish")
    return None, (f"no known Louisiana town found in {location!r}")


def validate_commercial_share(commercial_share):
    """Reject a percentage typed where a fraction belongs. Raises ValueError.

    Lives here so every caller shares one guard. parceltax.py did its own ratio
    arithmetic from the imported constants and so bypassed this entirely until
    the 2026-09-21 sweep - and parceltax is the tool run immediately before an
    offer, against one of CLAUDE.md's hard pre-offer gates.
    """
    if not 0 <= commercial_share <= 1:
        raise ValueError(
            f"commercial_share must be a fraction between 0 and 1, got "
            f"{commercial_share!r}. Use 0.3 for 30%.")
    return commercial_share


def estimate_tax(price, location, commercial_share=0.0):
    """-> (annual_tax, explanation) or (None, reason).

    commercial_share: fraction of value in non-residential use (mixed-use ground
    floor retail), assessed at 15% instead of 10%.
    """
    # "30" meaning 30 percent returned a $276,000 bill on a $1.5M Gonzales
    # building (16x the truth) and a negative share quietly cut the bill below
    # the residential floor. defaults.py already guards its own fraction keys.
    validate_commercial_share(commercial_share)
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
