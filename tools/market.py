"""Parish market screen: employment, population, income, rents.

Answers "does this parish deserve capital" with federal data instead of broker
narrative. Two sources:

  BLS (no key)      - unemployment rate + labor force, last ~2 years of monthly
                      data. Keyless tier is limited to ~25 queries/day; fine.
  Census ACS (key)  - population, median household income, median gross rent,
                      latest 5-yr estimates vs. five years prior. Requires the
                      free key from https://api.census.gov/data/key_signup.html
                      exported as CENSUS_API_KEY. Without it the ACS section is
                      skipped with a loud note, not silently.

    python3 tools/market.py ascension
    python3 tools/market.py "east baton rouge" livingston
    python3 tools/market.py --all

Coverage matches latax.py: the two metros and the corridor between them.
"""

import json
import os
import sys
import urllib.request

# parish -> county FIPS within state 22 (Louisiana)
PARISHES = {
    "east baton rouge":     "033",
    "lafayette":            "055",
    "orleans":              "071",
    "jefferson":            "051",
    "ascension":            "005",
    "livingston":           "063",
    "tangipahoa":           "105",
    "st. tammany":          "103",
    "st. john the baptist": "095",
    "st. charles":          "089",
    "west baton rouge":     "121",
    "iberville":            "047",
    "st. james":            "093",
    # 2026-09-07: these two were missing while the docstring claimed parity
    # with latax. St. Bernard is in the buy box and is the parish the
    # 2026-08-17 three-parish model settled INTO it (Chalmette clears 13% at
    # $388K) — the one parish that needed defending could not be screened.
    "st. bernard":          "087",
    "plaquemines":          "075",
}

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
ACS_URL = ("https://api.census.gov/data/{year}/acs/acs5"
           "?get=B01003_001E,B19013_001E,B25064_001E"
           "&for=county:{fips}&in=state:22&key={key}")
ACS_LATEST, ACS_PRIOR = 2023, 2018


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "deal-intake/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_json(url):
    # curl -4L: api.census.gov 302-redirects and its IPv6 path stalls urllib
    # from this network (same issue as FRED in rates.py).
    import subprocess
    proc = subprocess.run(["curl", "-4", "-sL", "--max-time", "30", url],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        raise SystemExit(f"Census API unreachable: curl exit {proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"Census API returned non-JSON (key inactive or bad "
                         f"request): {proc.stdout[:200]}")


def bls_labor(fips):
    """(latest unemployment %, yr-ago unemployment %, labor force trend %)."""
    rate_id = f"LAUCN22{fips}0000000003"
    lf_id = f"LAUCN22{fips}0000000006"
    data = post_json(BLS_URL, {"seriesid": [rate_id, lf_id],
                               "startyear": "2024", "endyear": "2026"})
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise SystemExit(f"BLS: {data.get('message')}")
    out = {}
    for s in data["Results"]["series"]:
        obs = [(d["year"] + d["period"], float(d["value"]))
               for d in s["data"]
               if d["period"].startswith("M") and d["value"] not in ("-", "")]
        obs.sort()
        out[s["seriesID"]] = obs
    rates, lf = out[rate_id], out[lf_id]
    if not rates or not lf:
        raise SystemExit(f"BLS returned no data for parish FIPS {fips} - "
                         "series may be discontinued or the ID is wrong")
    latest_rate = rates[-1][1]
    full_year = len(rates) >= 13
    yr_ago_rate = rates[-13][1] if full_year else rates[0][1]
    lf_trend = (lf[-1][1] / lf[-13][1] - 1) * 100 if len(lf) >= 13 else 0.0
    return latest_rate, yr_ago_rate, lf_trend, full_year


def acs(fips, key):
    def one(year):
        rows = get_json(ACS_URL.format(year=year, fips=fips, key=key))
        pop, income, rent = (float(v) for v in rows[1][:3])
        return pop, income, rent
    now, then = one(ACS_LATEST), one(ACS_PRIOR)
    return now, then


def screen(parish):
    fips = PARISHES[parish]
    print(f"\n== {parish.title()} Parish ==")
    rate, yr_ago, lf_trend, full_year = bls_labor(fips)
    direction = "improving" if rate <= yr_ago else "worsening"
    ago_label = "yr ago" if full_year else "start of short series"
    print(f"Unemployment      : {rate:.1f}% ({ago_label} {yr_ago:.1f}%, {direction})")
    print(f"Labor force, 12mo : {lf_trend:+.1f}%"
          + ("  <- people leaving" if lf_trend < -0.5 else ""))

    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        print("ACS               : skipped - no CENSUS_API_KEY set. Free key:")
        print("                    https://api.census.gov/data/key_signup.html")
        return
    (pop, inc, rent), (pop0, inc0, rent0) = acs(fips, key)
    yrs = ACS_LATEST - ACS_PRIOR
    print(f"Population        : {pop:,.0f} ({(pop / pop0 - 1) * 100:+.1f}% over {yrs}yr)")
    print(f"Median HH income  : ${inc:,.0f} ({(inc / inc0 - 1) * 100:+.1f}%)")
    print(f"Median gross rent : ${rent:,.0f} ({(rent / rent0 - 1) * 100:+.1f}%)")
    if pop < pop0:
        print("VERDICT: shrinking population - demand headwind, underwrite rent growth below 2%.")


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
