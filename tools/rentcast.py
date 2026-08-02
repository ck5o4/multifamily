"""Rent comps by address via RentCast. The paid-API layer for small properties.

CoStar's comp coverage is thinnest exactly where the buy box lives (8-16 unit
tertiary LA). RentCast covers single addresses and small multifamily well.
Free tier: 50 requests/month - every call here costs one, so the tool prints
nothing speculative and caches every response to deal-intake/_rentcast/.

    python3 tools/rentcast.py "41063 Cannon Rd, Gonzales, LA 70737" --beds 2 --baths 2.5 --sqft 1100

Key: RENTCAST_API_KEY in the environment or in .env at the repo root.
Use the per-unit spec (beds/baths/sqft of ONE unit), not building totals -
the AVM prices a unit, and unit rent x units is what feeds the model.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "deal-intake" / "_rentcast"
API = "https://api.rentcast.io/v1/avm/rent/long-term"


def api_key():
    import os
    key = os.environ.get("RENTCAST_API_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("RENTCAST_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("No RENTCAST_API_KEY in environment or .env")
    return key


def fetch(address, beds, baths, sqft, prop_type):
    params = {"address": address, "propertyType": prop_type}
    # "is not None" matters: bedrooms=0 is RentCast's studio spec.
    if beds is not None:
        params["bedrooms"] = beds
    if baths is not None:
        params["bathrooms"] = baths
    if sqft is not None:
        params["squareFootage"] = sqft
    url = API + "?" + urllib.parse.urlencode(params)
    proc = subprocess.run(
        ["curl", "-4", "-s", "--max-time", "30",
         "-H", f"X-Api-Key: {api_key()}", url],
        capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        raise SystemExit(f"RentCast unreachable: curl exit {proc.returncode}")
    data = json.loads(proc.stdout)
    if "rent" not in data:
        raise SystemExit(f"RentCast error: {json.dumps(data)[:300]}")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("--beds", type=float)
    ap.add_argument("--baths", type=float)
    ap.add_argument("--sqft", type=int)
    ap.add_argument("--type", default="Apartment",
                    help="Apartment (default), Single Family, Multi-Family, Condo, Townhouse")
    args = ap.parse_args()

    data = fetch(args.address, args.beds, args.baths, args.sqft, args.type)

    CACHE.mkdir(exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in args.address.lower())[:60]
    # Per-unit spec in the filename: the same building gets queried once per
    # unit type, and each response costs a paid call - never overwrite one.
    spec = f"{args.beds}bd-{args.baths}ba-{args.sqft}sf-{args.type}".replace(" ", "")
    out = CACHE / f"{slug}__{spec}.json"
    out.write_text(json.dumps(data, indent=2))

    print(f"Rent estimate : ${data['rent']:,.0f}/mo  "
          f"(range ${data['rentRangeLow']:,.0f} - ${data['rentRangeHigh']:,.0f})")
    comps = data.get("comparables", [])
    print(f"Comparables   : {len(comps)}  (cached to {out.relative_to(ROOT)})")
    for c in comps[:10]:
        addr = c.get("formattedAddress", "?")
        print(f"  ${c.get('price', 0):>6,.0f}/mo  {c.get('bedrooms', '?')}bd/"
              f"{c.get('bathrooms', '?')}ba  {c.get('squareFootage', 0):>5,.0f}sf  "
              f"{c.get('distance', 0):.1f}mi  {addr}")
    if len(comps) > 10:
        print(f"  ... {len(comps) - 10} more in the cached JSON")


if __name__ == "__main__":
    main()
