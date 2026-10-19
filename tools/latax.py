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
    # Sweep 2026-10-12: "central city" matched the EBR town "central" and
    # infer_parish asserted "matched town 'central' in East Baton Rouge Parish"
    # for an Orleans neighbourhood - the same token bug the 09-28 sweep removed
    # from portfolio_sim ($10,880 vs $13,199 of tax, 17.6% understated), and
    # `central-city-2nd` is a real pipeline record. Listing it explicitly lets
    # the longest-match tie-break resolve it.
    "orleans": ["new orleans", "nola", "algiers", "central city", "treme",
                "mid-city", "mid city", "bywater", "marigny", "gentilly",
                "lakeview", "broadmoor", "carrollton"],
    # Sweep 2026-10-12: estelle, timberlane, waggaman and bridge city were in
    # the pre-afcaa73 defaults._WIND_TOWNS tuple and were dropped when F4 moved
    # geography onto this table, so each silently fell to the $2,000 inland
    # insurance basis - a 13% clearing price of $655,451 against a correct
    # $529,750 on a 12-unit x $950 deal, $125,701 (23.7%) overstated, each.
    # All four are Jefferson Parish census places with multifamily stock.
    "jefferson": ["metairie", "kenner", "gretna", "harvey", "marrero", "westwego",
                  "terrytown", "harahan", "river ridge", "avondale", "jefferson", "elmwood",
                  "estelle", "timberlane", "waggaman", "bridge city"],
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
    # Sweep 2026-10-12: punctuation welded itself to the token, so
    # "Baton Rouge: 5555 Jefferson Hwy" tokenized as ["baton", "rouge:", ...]
    # and the two-word city could not match while the stray "jefferson" could.
    # Done AFTER the zip strip above, which needs its hyphen intact.
    key = re.sub(r"[:;()\[\]/\\|,-]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _norm_keep_parish(s):
    """_norm without the trailing-"parish" strip, for phrase detection."""
    key = str(s or "").strip().lower().replace(",", "")
    key = re.sub(r"\s+\d{5}(-\d{4})?$", "", key)
    key = re.sub(r"\s+(la|louisiana)$", "", key)
    key = re.sub(r"[:;()\[\]/\\|,-]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


# Louisiana parishes absent from MILLAGE whose names contain a town that is in
# CITY_TO_PARISH. Checked before town-token matching.
_CONFUSABLE_PARISHES = ("jefferson davis",)


# A town name immediately followed by one of these is part of a street name,
# not the locality: "Jefferson Hwy", "Central City Rd", "Baton Rouge Ave".
_STREET_SUFFIXES = {
    "st", "street", "rd", "road", "ave", "avenue", "blvd", "boulevard",
    "ln", "lane", "dr", "drive", "ct", "court", "cir", "circle", "pkwy",
    "parkway", "hwy", "highway", "way", "pl", "place", "ter", "terrace",
    "trail", "trl", "loop", "run", "row", "path", "pike", "rte", "route",
    "expy", "expressway", "bypass", "byp", "ext", "extension", "sq", "square",
}


_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "me", "md", "ma", "mi", "mn", "ms", "mo",
    "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or",
    "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi",
    "wy", "dc",
}


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

    # Sweep 2026-10-12, REGRESSION FROM afcaa73 (F4/F5): the bail-out below
    # tested for the word "parish" ANYWHERE, while _norm strips it only when
    # TRAILING. So the moment a town preceded the parish - the form a human
    # types - resolution returned None and both the insurance basis and the tax
    # reassessment were lost. All 97 towns failed under "<town>, <Parish>
    # Parish"; 25 of them are in a wind parish and the pre-fix substring code
    # got every one right. "Chalmette, St. Bernard Parish" took the $2,000
    # inland basis: 13% clearing price $640,229 against a correct $517,448,
    # $122,781 (23.7%) overstated - and treme-gov-nicholls, live and
    # board-ranked, records its location as "1429 Governor Nicholls St, Treme,
    # Orleans Parish", which is exactly that form.
    #
    # Precedence now: an explicit "<phrase> parish" wins (and an unknown one
    # still fails loudly, so "Jefferson Davis Parish" does NOT become
    # Jefferson), then town tokens.
    m = re.search(r"\b([a-z.'\s]+?)\s+parish\b", _norm_keep_parish(location))
    if m:
        named = _norm(m.group(1))
        # Longest trailing phrase first: "jefferson davis" must be tried whole
        # before "davis" or "jefferson".
        words = named.split()
        for start in range(len(words)):
            cand = " ".join(words[start:])
            if cand in MILLAGE:
                return cand, f"named {cand.title()} Parish"
            if cand in CITY_TO_PARISH:
                return CITY_TO_PARISH[cand], (f"matched town {cand!r} in "
                                              f"{CITY_TO_PARISH[cand].title()} Parish")
        # A parish was named and it is not one we carry: fail loudly rather
        # than fall through to a town token inside its name.
        return None, (f"{location!r} names a parish that is not in the millage "
                      f"table. Known: {', '.join(sorted(MILLAGE))}")

    # A non-Louisiana state makes every town match meaningless: "Jefferson, TX"
    # resolved to Jefferson Parish and took the Louisiana 118.40 millage.
    st = re.search(r"\b([a-z]{2})\b\s*(\d{5}(-\d{4})?)?$", _norm(location))
    if st and st.group(1) not in ("la",) and st.group(1) in _US_STATES:
        return None, (f"{location!r} is not in Louisiana "
                      f"(state {st.group(1).upper()})")

    tokens = _norm(location).split()

    # Sweep 2026-10-19. A match is only evidence of a LOCALITY if it is not
    # part of something else. Two things it can be part of, both reproduced:
    #
    #   1. A street name. 10-12 tested only the token immediately after the
    #      match, which misses the head of a longer town name: in
    #      "Gretna - 1 Central City Rd" the match "central" (East Baton Rouge)
    #      is followed by "city", not a suffix, so it scored CLEAN and beat
    #      Gretna on end_index - asserting "matched town 'central' in East
    #      Baton Rouge Parish" and taking the $2,000 inland insurance basis on
    #      a Jefferson property. On the real treme-gov-nicholls record the same
    #      Orleans->EBR slip moves the 13% rung $714,000 -> $817,000
    #      ($103,000 overstated). Extending the match to the LONGEST town that
    #      still starts at i and testing the suffix there catches it.
    #   2. A parish we do not carry whose name begins with a town we do:
    #      "Jennings, Jefferson Davis, LA" must not resolve to Jefferson.
    #      This used to be a hard bail before any town matching, which meant a
    #      real Orleans address on a street named for that parish resolved to
    #      nothing at all: "1234 Jefferson Davis Pkwy, New Orleans, LA 70119"
    #      returned None, froze taxes across the solve, and put the 13% rung at
    #      $748,000 against a correct Orleans $522,000. Demoting the match
    #      instead lets the explicit "new orleans" win while the Jennings case
    #      still resolves to nothing.
    def _not_a_locality(i, n):
        longest = n
        for city2, _ in CITY_TO_PARISH.items():
            ct2 = city2.split()
            if len(ct2) > longest and tokens[i:i + len(ct2)] == ct2:
                longest = len(ct2)
        after = tokens[i + longest] if i + longest < len(tokens) else ""
        if after in _STREET_SUFFIXES:
            return True
        for conf in _CONFUSABLE_PARISHES:
            cft = conf.split()
            if n <= len(cft) and tokens[i:i + len(cft)] == cft:
                return True
        return False

    best = None   # (not_in_a_street_name, n_words, end_index, parish)
    for city, par in CITY_TO_PARISH.items():
        ct = city.split()
        n = len(ct)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] != ct:
                continue
            # Sweep 2026-10-12, and then a second pass the same day.
            #
            # afcaa73's key was (end_index, n_words) - position before length -
            # so a 1-word town LATER in the string beat the real city earlier
            # in it: "Baton Rouge - 5555 Jefferson Hwy" resolved to Jefferson
            # Parish and said so as a fact, taking $3,000/unit wind on a Baton
            # Rouge property (13% rung $535,092 against a correct $662,167).
            #
            # Flipping it to (n_words, end_index) fixed that and broke the
            # mirror case, which the reporting auditor caught: a multi-word
            # table entry sitting inside a STREET name, ahead of a one-word
            # real city, then won - "1 Central City Rd, Baker, LA" resolved to
            # Orleans where the city is Baker, and "100 Baton Rouge Ave,
            # Gonzales, LA" to East Baton Rouge rather than Ascension.
            #
            # Neither ordering can be right, because the real signal is not
            # position or length: it is whether the match is part of a street
            # name. A town immediately followed by a street-type suffix is, so
            # it is demoted below any match that is not. That resolves both
            # directions, and single-city strings never exercise it at all.
            cand = (not _not_a_locality(i, n), n, i + n, par)
            if best is None or cand[:3] > best[:3]:
                best = cand
    if best and best[0]:
        _clean, n, end, par = best
        return par, (f"matched town {' '.join(tokens[end-n:end])!r} "
                     f"in {par.title()} Parish")
    if best:
        # Every candidate sat inside a street name or a parish we do not carry.
        # 10-12's demotion only ever RANKED candidates, so when they were all
        # demoted the best one was still asserted as fact: "100 Harvey Ln" came
        # back as Jefferson Parish with the $3,000/unit coastal basis, and
        # swapping two street names flipped the answer - "100 Baker Rd & Harvey
        # Ln" -> Jefferson vs "100 Harvey Ln & Baker Rd" -> East Baton Rouge,
        # $132,000 of clearing price from token order alone. Returning None
        # hands the caller its own loud unresolved path (defaults.derive,
        # pymodel.solve_price and solve.solve_price all have one) instead of a
        # confident guess.
        _clean, n, end, par = best
        return None, (f"no locality found in {location!r} - "
                      f"{' '.join(tokens[end-n:end])!r} appears only inside a "
                      f"street name or a parish not in the millage table, so "
                      f"it is not evidence of where the property is")
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
    # Sweep 2026-10-12: afcaa73 added infer_parish and wired exactly one of its
    # three consumers (defaults.wind_exposed) to it, so one address string
    # produced two different geographic verdicts in the same run - correct
    # $3,000 wind insurance and NO tax reassessment at all. estimate_tax
    # returned None for 97 of 97 towns under "123 Main St, <town>, LA", and
    # run() has no tax derivation of its own, so the $0 stuck: on Treme at its
    # $714,000 rung that is $75,500 NOI and 18.24% IRR against $66,076 and
    # 12.93%, and a 13% clearing price of $825,737 against $712,682 -
    # $113,055 (15.9%) overstated.
    parish, how = infer_parish(location)
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
