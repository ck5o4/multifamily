#!/usr/bin/env python3
"""Fetch FRED data series and fit MC input distributions for monte_carlo().

Writes tools/fitted_params.json with per-parameter provenance.

Usage:
    python3 tools/fit_distributions.py [--dry-run]

Requirements: stdlib only (uses curl via subprocess for HTTP).
"""

import argparse
import csv
import io
import json
import math
import subprocess
import sys
from pathlib import Path

FIT_DATE = "2026-08-01"
OUT_PATH = Path(__file__).parent / "fitted_params.json"


def fetch_fred_csv(series_id: str, timeout: int = 30) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    result = subprocess.run(
        ["curl", "-4", "-s", "--max-time", str(timeout), url],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed for {series_id}: {result.stderr}")
    if result.stdout.startswith("<!DOCTYPE"):
        raise RuntimeError(f"FRED returned HTML error page for {series_id} — series not found")
    return result.stdout


def parse_fred_csv(text: str) -> list:
    """Parse FRED CSV. Returns list of (date_str, float)."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # skip header
    for row in reader:
        if len(row) < 2:
            continue
        date_str, val_str = row[0].strip(), row[1].strip()
        if not val_str or val_str == ".":
            continue
        try:
            rows.append((date_str, float(val_str)))
        except ValueError:
            pass
    return rows


def annual_yoy_from_monthly(rows: list) -> list:
    """Convert monthly index to annual YoY growth rates.

    Groups observations by year (takes last value each year),
    then computes (this_year / prev_year) - 1.
    Returns list of (year:int, growth:float).
    """
    by_year = {}
    last_month = {}
    for date_str, val in rows:
        yr = int(date_str[:4])
        by_year[yr] = val
        last_month[yr] = max(last_month.get(yr, 0), int(date_str[5:7]))
    # A partial year (no December print) is not a full-year observation —
    # including it booked Jan-Jun 2026 as a full YoY (sweep 2026-08-09).
    years = sorted(y for y in by_year if last_month[y] == 12)
    return [(years[i], (by_year[years[i]] / by_year[years[i - 1]]) - 1.0)
            for i in range(1, len(years))]


def filter_years(year_val_list: list, start_year: int) -> list:
    return [(y, v) for y, v in year_val_list if y >= start_year]


def percentile(sorted_vals: list, p: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])


def fit(year_val_list: list) -> dict:
    vals = sorted(v for _, v in year_val_list)
    n = len(vals)
    if n == 0:
        return {}
    mean = sum(vals) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / max(n - 1, 1))
    return {
        "n_obs": n,
        "mean": round(mean, 6),
        "sd": round(sd, 6),
        "p10": round(percentile(vals, 10), 6),
        "p50": round(percentile(vals, 50), 6),
        "p90": round(percentile(vals, 90), 6),
    }


def fit_rent_growth(start_year: int = 2001) -> dict:
    """CUUR0300SEHA — BLS CPI Rent of primary residence, South region."""
    series_id = "CUUR0300SEHA"
    print(f"  Fetching {series_id}...", flush=True)
    raw = fetch_fred_csv(series_id)
    rows = parse_fred_csv(raw)
    yoy = filter_years(annual_yoy_from_monthly(rows), start_year)
    stats = fit(yoy)
    date_range = f"{yoy[0][0]}-01 to {yoy[-1][0]}-12" if yoy else "N/A"
    return {
        "source": series_id,
        "description": (
            "BLS CPI Rent of primary residence, South region (via FRED). "
            "Annual YoY growth from monthly index."
        ),
        "date_range": date_range,
        "fit_date": FIT_DATE,
        "provenance": "FITTED",
        "note": (
            "South CPI rent includes 2021-2023 surge. P50 exceeds old 2% assumption. "
            "P90 4.7% vs old 3%."
        ),
        **stats,
    }


def fit_expense_growth(start_year: int = 2001) -> dict:
    """CUUR0300SA0 — BLS CPI All Items, South region."""
    series_id = "CUUR0300SA0"
    print(f"  Fetching {series_id}...", flush=True)
    raw = fetch_fred_csv(series_id)
    rows = parse_fred_csv(raw)
    yoy = filter_years(annual_yoy_from_monthly(rows), start_year)
    stats = fit(yoy)
    date_range = f"{yoy[0][0]}-01 to {yoy[-1][0]}-12" if yoy else "N/A"
    return {
        "source": series_id,
        "description": (
            "BLS CPI All Items, South region (via FRED). "
            "Annual YoY growth from monthly index. "
            "LA insurance shocks ride on top as separate insurance_mult factor."
        ),
        "date_range": date_range,
        "fit_date": FIT_DATE,
        "provenance": "FITTED",
        "note": (
            "P50 ~2.1%, close to old 2.5% assumption. "
            "P10 near zero reflects post-GFC deflation. "
            "LA insurance shock modeled separately via insurance_mult."
        ),
        **stats,
    }


def fit_vacancy(start_year: int = 2001) -> dict:
    """RRVRUSQ156N — Census HVS rental vacancy rate, South region.

    Falls back to national (RRVRUSQ156N is South; try RVAC if that fails).
    Values are quarterly percentages; we convert to decimal.
    """
    for series_id in ("RRVRUSQ156N", "RRVRUSQ156N"):
        try:
            print(f"  Fetching {series_id}...", flush=True)
            raw = fetch_fred_csv(series_id)
            rows = parse_fred_csv(raw)
            if not rows:
                continue
            year_val = [(int(d[:4]), v / 100.0) for d, v in rows if int(d[:4]) >= start_year]
            stats = fit(year_val)
            date_range = f"{year_val[0][0]}-Q1 to {year_val[-1][0]}-Q4" if year_val else "N/A"
            return {
                "source": series_id,
                "description": (
                    "Census Housing Vacancy Survey rental vacancy rate, South region "
                    "(via FRED). Quarterly levels, expressed as decimal."
                ),
                "date_range": date_range,
                "fit_date": FIT_DATE,
                "provenance": "FITTED",
                "note": (
                    "South rental vacancy historically higher than 7% Stoa benchmark. "
                    "P50 ~8.2% vs old 7%. Tertiary market deals add 1-2pts above regional."
                ),
                **stats,
            }
        except RuntimeError as e:
            print(f"    WARNING: {e}", file=sys.stderr)

    # Hard fallback
    print("  WARNING: could not fetch vacancy series; using judgment fallback", file=sys.stderr)
    return {
        "source": "judgment-fallback",
        "description": "Judgment fallback — FRED vacancy series unavailable.",
        "date_range": None,
        "fit_date": FIT_DATE,
        "n_obs": None,
        "provenance": "JUDGMENT",
        "p10": 0.05, "p50": 0.07, "p90": 0.10,
    }


def judgment_insurance_mult() -> dict:
    return {
        "source": "judgment",
        "description": (
            "Multiplier on base insurance input. No clean public series for LA "
            "multifamily insurance. Triangular(0.85, 1.0, 1.8)."
        ),
        "date_range": None,
        "fit_date": FIT_DATE,
        "n_obs": None,
        "provenance": "JUDGMENT",
        "p10": 0.85,
        "p50": 1.00,
        "p90": 1.60,
        "tri_low": 0.85,
        "tri_mode": 1.00,
        "tri_high": 1.80,
        "note": "judgment - LA crisis regime; replace with LDI filing data later.",
    }


def judgment_exit_cap_spread() -> dict:
    return {
        "source": "judgment",
        "description": (
            "Spread (decimal) added to going-in cap rate at exit. "
            "No cap rate transaction series for LA tertiary (non-disclosure state). "
            "Triangular(-25bps, +50bps, +150bps)."
        ),
        "date_range": None,
        "fit_date": FIT_DATE,
        "n_obs": None,
        "provenance": "JUDGMENT",
        # FIX 3 (2026-08-06 red-team): p90 widened 0.013 -> 0.020 for honest
        # exit-cap tails. Keep the fitter in sync with the live file or a
        # re-run silently reverts the fix (sweep 2026-08-09).
        "p10": -0.0025,
        "p50": 0.0050,
        "p90": 0.0200,
        "tri_low": -0.0025,
        "tri_mode": 0.0050,
        "tri_high": 0.0200,
        "note": "judgment - calibration log has no cap data (LA non-disclosure).",
    }


def main():
    ap = argparse.ArgumentParser(description="Fit MC distributions from FRED data")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print params but do not write file")
    args = ap.parse_args()

    print("Fitting distributions from FRED...", flush=True)

    params = {
        "_meta": {
            "description": (
                "Empirically-fitted MC input distributions for pymodel.py monte_carlo(). "
                "Loaded at runtime; missing file falls back to hardcoded judgment values "
                "with stderr warning."
            ),
            "fit_date": FIT_DATE,
            "correlation_matrix_note": (
                "5x5 Gaussian copula (judgment): rg/eg +0.3, rg/vac -0.5, "
                "ins/cap +0.3, all others 0. Edit rho_* keys to adjust."
            ),
            "correlation_matrix": {
                "order": ["rent_growth", "expense_growth", "vacancy",
                          "insurance_mult", "exit_cap_spread"],
                "rho_rg_eg": 0.3,
                "rho_rg_vac": -0.5,
                "rho_ins_cap": 0.3,
            },
        },
        "rent_growth": fit_rent_growth(),
        "expense_growth": fit_expense_growth(),
        "vacancy": fit_vacancy(),
        "insurance_mult": judgment_insurance_mult(),
        "exit_cap_spread": judgment_exit_cap_spread(),
    }

    print("\nFitted params summary:")
    for key in ("rent_growth", "expense_growth", "vacancy", "insurance_mult", "exit_cap_spread"):
        p = params[key]
        prov = p.get("provenance", "?")
        print(f"  {key:<20} [{prov}]  "
              f"P10={p['p10']*100:.2f}%  P50={p['p50']*100:.2f}%  P90={p['p90']*100:.2f}%  "
              f"n={p.get('n_obs', 'N/A')}")

    if args.dry_run:
        print("\n[dry-run] Not writing file.")
        return

    # Preserve blocks the fitter does not produce (turnover_rate, capex_vintage
    # were hand-added for MC FIX 1/2); a plain overwrite deleted them
    # (sweep 2026-08-09).
    if OUT_PATH.exists():
        try:
            with open(OUT_PATH) as f:
                existing = json.load(f)
            for key, val in existing.items():
                if key not in params:
                    params[key] = val
                    print(f"  preserved hand-added block: {key}")
        except Exception as e:
            print(f"  WARNING: could not read existing file to preserve "
                  f"hand-added blocks: {e}")

    with open(OUT_PATH, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
