"""FEMA flood zone lookup by street address. No API key, no subscription.

Louisiana rule: run this on every deal before believing any insurance number.
A Special Flood Hazard Area (SFHA) means the lender WILL require flood
insurance on top of the property policy - in LA that can be $1,000+/unit/yr
and it kills small-deal DSCR. Zone X behind a levee is cheaper but carries
real residual risk (the levee is why it's Zone X).

    python3 tools/flood.py "41063 Cannon Rd, Gonzales, LA"
    python3 tools/flood.py "41063 Cannon Rd, Gonzales, LA" --json

Geocoding: US Census geocoder (public). Zones: FEMA National Flood Hazard
Layer (NFHL) MapServer layer 28. Both are unauthenticated federal services;
either being down prints an error rather than guessing.
"""

import json
import sys
import urllib.parse
import urllib.request

GEOCODER = ("https://geocoding.geo.census.gov/geocoder/locations/"
            "onelineaddress?address={addr}&benchmark=Public_AR_Current&format=json")
NFHL = ("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
        "?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326"
        "&spatialRel=esriSpatialRelIntersects"
        "&outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE&returnGeometry=false&f=json")

# Zones where lenders require flood insurance (SFHA). V zones add wave action.
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "deal-intake/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def geocode(address):
    data = fetch_json(GEOCODER.format(addr=urllib.parse.quote(address)))
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        raise SystemExit(f"Census geocoder found no match for: {address}\n"
                         "Try adding the ZIP, or check the street spelling.")
    m = matches[0]
    c = m["coordinates"]
    return c["x"], c["y"], m.get("matchedAddress", address)


def flood_zones(lon, lat):
    data = fetch_json(NFHL.format(lon=lon, lat=lat))
    if "error" in data:
        raise SystemExit(f"FEMA NFHL error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def interpret(zones):
    """Return (zone_string, sfha_bool, note)."""
    if not zones:
        return ("UNMAPPED", False,
                "No NFHL polygon here - area not mapped or data gap. "
                "Verify at msc.fema.gov before closing.")
    # A boundary parcel can intersect several polygons. If ANY is SFHA, the
    # lender treats it as SFHA - report the worst one, never the first one.
    def is_sfha(p):
        return p.get("SFHA_TF") == "T" or p.get("FLD_ZONE") in SFHA_ZONES
    sfha = any(is_sfha(p) for p in zones)
    z = next((p for p in zones if is_sfha(p)), zones[0])
    zone = z.get("FLD_ZONE", "?")
    subty = (z.get("ZONE_SUBTY") or "").strip()
    if zone == "D":
        return ("D", False,
                "Zone D = flood risk UNDETERMINED, not minimal. Lenders can "
                "still require insurance; order a flood determination.")
    if sfha:
        note = ("SFHA - lender will REQUIRE flood insurance. Get a flood quote "
                "before underwriting any further; do not use the template "
                "insurance default.")
    elif "LEVEE" in subty.upper():
        note = ("Zone X only because of a levee. Insurance not lender-required "
                "but residual risk is real - price a voluntary policy.")
    elif zone.startswith("X") and "0.2" in subty:
        note = "500-year zone (shaded X). Not required; cheap policy worth pricing."
    else:
        note = "Minimal flood hazard. Flood insurance not lender-required."
    return (f"{zone}" + (f" ({subty})" if subty else ""), sfha, note)


def lookup(address):
    lon, lat, matched = geocode(address)
    zones = flood_zones(lon, lat)
    zone, sfha, note = interpret(zones)
    return {"address": matched, "lon": lon, "lat": lat,
            "zone": zone, "sfha": sfha, "note": note}


def main(argv):
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    result = lookup(" ".join(args))
    if as_json:
        print(json.dumps(result, indent=2))
        return
    print(f"Address : {result['address']}")
    print(f"Zone    : {result['zone']}")
    print(f"SFHA    : {'YES - flood insurance required' if result['sfha'] else 'no'}")
    print(f"Note    : {result['note']}")


if __name__ == "__main__":
    main(sys.argv[1:])
