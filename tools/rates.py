"""Current rates vs. the model's assumptions. No API key.

CLAUDE.md carries a standing instruction: "WSJ Prime ... verify current before
each deal." This automates it. Pulls daily series from FRED's public CSV
endpoint (no key needed, unlike the JSON API) and diffs against defaults.py.

    python3 tools/rates.py

Series: DPRIME (bank prime = WSJ Prime), DGS10 (10-yr Treasury constant
maturity), SOFR. A model default more than 12.5bps off the live figure is
flagged; update defaults.py deliberately, never silently.

Every successful pull (all three series) is appended to market-data/rates.jsonl
and diffed against the newest previously stored row, so "has the 10yr moved
more than 25bps since the last snapshot" is answerable across runs instead of
only within one. A partial or failed pull stores NOTHING and substitutes
nothing: this tool fails closed, and a stale stored row is never printed in
place of a live figure.
"""

import csv
import datetime
import io
import json
import os
import subprocess
import sys

from defaults import ACQ_TEMPLATE, DEV_TEMPLATE

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# History lives next to the other market data, one JSON object per line.
HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "market-data", "rates.jsonl")

# A move of more than this against the last stored snapshot gets flagged.
# 25bps is the threshold the daily-scan routine asks about for the 10yr.
DRIFT_FLAG_BPS = 25.0

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


def read_history(path=HISTORY_PATH):
    """Every stored snapshot, oldest first. Malformed lines are skipped, not
    fatal: a corrupt row must not stop today's pull from being recorded."""
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and "observed" in row:
                    rows.append(row)
    except FileNotFoundError:
        return []
    rows.sort(key=lambda r: r.get("observed", ""))
    return rows


def last_snapshot(path=HISTORY_PATH):
    """Newest stored snapshot, or None when there is no history yet."""
    rows = read_history(path)
    return rows[-1] if rows else None


def append_snapshot(row, path=HISTORY_PATH):
    """Append one snapshot. Called only after ALL series fetched cleanly."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def drift(prev, live):
    """[(series, prev_value, live_value, delta_bps)] for series in both rows.

    delta_bps is live - prev, so a positive number means rates rose. Series
    missing from the stored row are returned with prev None and no delta,
    rather than silently treated as unchanged.
    """
    out = []
    prev_rates = (prev or {}).get("rates", {})
    for series in SERIES:
        if series not in live:
            continue
        before = prev_rates.get(series)
        if before is None:
            out.append((series, None, live[series], None))
        else:
            out.append((series, before, live[series],
                        (live[series] - before) * 100.0))
    return out


def main():
    live = {}
    observed = {}
    for series, label in SERIES.items():
        date, value = latest(series)
        live[series] = value
        observed[series] = date
        print(f"{label:48s} {value:5.2f}%   (as of {date})")

    # Diff against the newest stored row BEFORE appending today's, so the
    # comparison is run-to-run rather than against itself.
    previous = last_snapshot()
    print()
    drifted = False
    if previous is None:
        print("no stored snapshot to compare against - today's pull becomes "
              "the baseline for the next run")
    else:
        print(f"vs. last snapshot ({previous.get('observed')}):")
        for series, before, now, delta_bps in drift(previous, live):
            if delta_bps is None:
                print(f"  {series:7s} {now:5.2f}%   (not in the stored row - "
                      f"no comparison)")
                continue
            mark = "DRIFT" if abs(delta_bps) > DRIFT_FLAG_BPS else "ok   "
            if abs(delta_bps) > DRIFT_FLAG_BPS:
                drifted = True
            print(f"  {mark} {series:7s} {before:5.2f}% -> {now:5.2f}% "
                  f"({delta_bps:+.0f}bps)")
        if drifted:
            print(f"  At least one series moved more than "
                  f"{DRIFT_FLAG_BPS:.0f}bps. Review model assumptions.")

    append_snapshot({
        "observed": datetime.date.today().isoformat(),
        "rates": dict(live),
        "series_asof": observed,
        "source": "FRED fredgraph.csv",
    })

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
    sys.exit(1 if (stale or drifted) else 0)


if __name__ == "__main__":
    main()
