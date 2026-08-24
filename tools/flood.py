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
import subprocess
import sys
import urllib.parse

GEOCODER = ("https://geocoding.geo.census.gov/geocoder/locations/"
            "onelineaddress?address={addr}&benchmark=Public_AR_Current&format=json")
NFHL = ("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
        "?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326"
        "&spatialRel=esriSpatialRelIntersects"
        "&outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE&returnGeometry=false&f=json")

# Zones where lenders require flood insurance (SFHA). V zones add wave action.
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}


class FloodLookupError(RuntimeError):
    """A flood lookup failed to produce a determination (network, geocode, or
    malformed response). Callers catch this and record the gate as UNKNOWN -
    never as 'no flood risk'."""


def fetch_json(url, timeout=45):
    # curl -4, for the same reason rates.py uses it: hazards.fema.gov resolves an
    # IPv6 address that resets the connection from some networks, and Python's
    # urllib will not fall back to IPv4 the way curl does. Symptom was a hard
    # "[Errno 54] Connection reset by peer" on EVERY flood lookup (found
    # 2026-08-17 screening Chalmette). Flood is a gate, not a nicety - a lookup
    # that always raises means no deal in the NOLA box can be screened at all.
    proc = subprocess.run(
        ["curl", "-4", "-sL", "--max-time", str(timeout), "--retry", "2",
         "-A", "deal-intake/1.0", url],
        capture_output=True, text=True)
    # Raise a CATCHABLE exception, not SystemExit. SystemExit derives from
    # BaseException, so the `except Exception` guards in intake.py/icmemo.py were
    # dead code and every flood-service failure killed the whole run - discarding
    # a completed Monte Carlo in icmemo's case. That is the exact "a tool that
    # always raises blocks the whole box" failure 8b7ce5e set out to fix, moved
    # from urllib to curl. (sweep 2026-08-24)
    if proc.returncode != 0:
        raise FloodLookupError(f"Flood service unreachable: curl exit {proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise FloodLookupError(f"Flood service returned non-JSON: {proc.stdout[:200]}")


def geocode(address):
    data = fetch_json(GEOCODER.format(addr=urllib.parse.quote(address)))
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        raise FloodLookupError(f"Census geocoder found no match for: {address}\n"
                               "Try adding the ZIP, or check the street spelling.")
    m = matches[0]
    c = m["coordinates"]
    return c["x"], c["y"], m.get("matchedAddress", address)


def flood_zones(lon, lat):
    data = fetch_json(NFHL.format(lon=lon, lat=lat))
    if "error" in data:
        raise FloodLookupError(f"FEMA NFHL error: {data['error']}")
    # A valid JSON envelope with no "features" key at all is a malformed/failed
    # response, not evidence of no flood risk - treat it as a failure, not as
    # an empty (=> unmapped => not-SFHA) result. (sweep 2026-08-24)
    if "features" not in data:
        raise FloodLookupError(f"FEMA NFHL returned no 'features' key: {str(data)[:200]}")
    return [f["attributes"] for f in data.get("features", [])]


def interpret(zones):
    """Return (zone_string, sfha_bool, note)."""
    if not zones:
        # sfha is UNKNOWN here, not False. Returning False let icmemo's
        # `if fres["sfha"]` gate drop the flood line entirely and assert
        # "SFHA: No" on an unmapped parcel. (sweep 2026-08-24)
        return ("UNMAPPED", None,
                "No NFHL polygon here - area not mapped or data gap. Flood risk "
                "UNDETERMINED (not 'no'). Verify at msc.fema.gov before closing.")
    # A boundary parcel can intersect several polygons. If ANY is SFHA, the
    # lender treats it as SFHA - report the worst one, never the first one.
    def is_sfha(p):
        return p.get("SFHA_TF") == "T" or p.get("FLD_ZONE") in SFHA_ZONES
    sfha = any(is_sfha(p) for p in zones)

    def _nonsfha_rank(p):
        # worst-first among non-SFHA: D (unmapped risk) > levee-dependent X >
        # shaded X (0.2% annual) > plain X (audit 2026-08-05: zones[0] could
        # report "minimal hazard" while masking a D or levee polygon)
        zn = p.get("FLD_ZONE", "?")
        sub = (p.get("ZONE_SUBTY") or "").upper()
        if zn == "D":
            return 0
        if "LEVEE" in sub:
            return 1
        if "0.2" in sub or "SHADED" in sub:
            return 2
        return 3

    def _sfha_rank(p):
        # worst-first among SFHA: VE > V > AE > A-family. A coastal AE+VE
        # parcel must report VE (wave action), never whichever came first.
        return {"VE": 0, "V": 1, "AE": 2}.get(p.get("FLD_ZONE", "?"), 3)

    sfha_polys = [p for p in zones if is_sfha(p)]
    z = (min(sfha_polys, key=_sfha_rank) if sfha_polys
         else min(zones, key=_nonsfha_rank))
    zone = z.get("FLD_ZONE", "?")
    subty = (z.get("ZONE_SUBTY") or "").strip()
    # Zone D and the FEMA "not studied" domain values mean risk UNDETERMINED,
    # not minimal. Any FLD_ZONE we don't recognise falls here too rather than
    # dropping through to the "minimal hazard" else. sfha is None (unknown), so
    # no consumer can read an unstudied parcel as "no flood risk". (2026-08-24)
    _UNDETERMINED = {"D", "AREA NOT INCLUDED", "OPEN WATER"}
    _known = SFHA_ZONES | {"X"}
    if not sfha and (zone in _UNDETERMINED or zone not in _known):
        label = zone if zone in _UNDETERMINED else f"{zone} (unrecognised)"
        return (label, None,
                f"FEMA zone {zone!r} = flood risk UNDETERMINED, not minimal. "
                "Lenders can still require insurance; order a flood determination.")
    if sfha and zone.startswith("V"):
        note = ("SFHA, coastal high-hazard V zone with WAVE ACTION - lender "
                "will REQUIRE flood insurance at V-zone rates (well above "
                "A-zone). Get a flood quote before underwriting any further; "
                "do not use the template insurance default.")
    elif sfha:
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
