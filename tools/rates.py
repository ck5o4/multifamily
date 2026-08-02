"""Current rates vs. the model's assumptions. No API key.

CLAUDE.md carries a standing instruction: "WSJ Prime ... verify current before
each deal." This automates it. Pulls daily series from FRED's public CSV
endpoint (no key needed, unlike the JSON API) and diffs against defaults.py.

    python3 tools/rates.py

Series: DPRIME (bank prime = WSJ Prime), DGS10 (10-yr Treasury constant
maturity), SOFR. A model default more than 12.5bps off the live figure is
flagged; update defaults.py deliberately, never silently.
"""

import csv
import io
import subprocess
import sys

from defaults import ACQ_TEMPLATE, DEV_TEMPLATE

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

SERIES = {
    "DPRIME": "WSJ Prime (bank prime loan rate)",
    "DGS10":  "10-yr Treasury (perm/agency pricing basis)",
    "SOFR":   "SOFR (floating construction debt basis)",
}


def latest(series):
    """Return (date, value) of the last non-empty observation."""
    # curl -4: FRED resolves an IPv6 address that blackholes from some networks;
    # Python's urllib won't fall back to IPv4 the way curl does.
    url = FRED_CSV.format(series=series)
    proc = subprocess.run(
        ["curl", "-4", "-sL", "--max-time", "45", "--retry", "2", url],
        capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        raise SystemExit(f"FRED unreachable for {series}: curl exit {proc.returncode}")
    text = proc.stdout
    rows = [row for row in csv.reader(io.StringIO(text))
            if len(row) == 2 and row[1] not in (".", "") and row[0][:2] == "20"]
    if not rows:
        raise SystemExit(f"FRED returned no observations for {series}")
    date, value = rows[-1]
    return date, float(value)


def main():
    live = {}
    for series, label in SERIES.items():
        date, value = latest(series)
        live[series] = value
        print(f"{label:48s} {value:5.2f}%   (as of {date})")

    prime = live["DPRIME"] / 100.0
    checks = [
        ("acquisition rate",        ACQ_TEMPLATE["rate"][0],       prime),
        ("acquisition refi_rate",   ACQ_TEMPLATE["refi_rate"][0],  prime),
        ("development index_rate",  DEV_TEMPLATE["index_rate"][0], prime),
    ]
    print()
    stale = False
    for name, model, current in checks:
        drift_bps = (model - current) * 10000
        if abs(drift_bps) > 12.5:
            stale = True
            print(f"STALE: {name} = {model:.4f} but prime is {current:.4f} "
                  f"({drift_bps:+.0f}bps). Update defaults.py with a note.")
        else:
            print(f"ok   : {name} = {model:.4f} (prime {current:.4f})")
    sys.exit(1 if stale else 0)


if __name__ == "__main__":
    main()
