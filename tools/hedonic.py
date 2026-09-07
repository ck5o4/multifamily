"""Hedonic price estimator — market approach valuation for Louisiana multifamily.

Fits an OLS regression on 71 real closed sales (2021-2026) from calibration.json.
Provides a second, independent $/unit estimate alongside the income-approach model.
Useful for deal screening: "the market approach says $X/unit ± band."

IMPORTANT: This is an honest tool with a small dataset. R² will be modest, the
error band will be wide. A wide honest band beats a narrow false one. The point
estimate is directional; always read the band.

Model:
    log($/unit) = β0
                + β1 * d_new_orleans
                + β2 * d_lafayette
                + β3 * d_northshore
                + β4 * log(units)
                + ε

    Base market: baton rouge
    All market dummies vs. baton rouge baseline.

Exclusions:
    - Entries missing units or price
    - Court-auction / distressed sales (note contains "auction" or "distressed")
    - Entries whose log($/unit) is beyond 2.5 MAD from the median log($/unit)

Usage:
    python3 tools/hedonic.py fit
    python3 tools/hedonic.py predict --market "baton rouge" --units 12
    python3 tools/hedonic.py predict --market northshore --units 18 --year 2026
"""

import argparse
import json
import math
import pathlib
import statistics
import sys

LOG = pathlib.Path(__file__).resolve().parent.parent / "portfolio" / "calibration.json"

KNOWN_MARKETS = {"baton rouge", "new orleans", "lafayette", "northshore"}
BASE_MARKET = "baton rouge"

# Parish -> comp market. The buy box spans four hedonic markets; the parish is
# the only fact that decides which one, so resolve from the deal's location.
PARISH_TO_MARKET = {
    "orleans": "new orleans", "jefferson": "new orleans",
    "st. bernard": "new orleans", "plaquemines": "new orleans",
    "st. tammany": "northshore", "tangipahoa": "northshore",
    "lafayette": "lafayette",
    "east baton rouge": "baton rouge", "ascension": "baton rouge",
    "livingston": "baton rouge", "west baton rouge": "baton rouge",
    "iberville": "baton rouge", "st. john the baptist": "baton rouge",
    "st. charles": "baton rouge", "st. james": "baton rouge",
}


def _market_support(fit_result):
    """How many clean sales back each market dummy. A dummy with one
    observation fits that observation exactly and is not a market estimate."""
    counts = {}
    for r in fit_result.get("clean", []):
        counts[r["market"]] = counts.get(r["market"], 0) + 1
    return counts


def market_for_location(location):
    """Deal location -> (hedonic market, how). (None, reason) if unresolvable.

    2026-09-07: icmemo and briefing each picked the market by searching the
    deal's free-text history for 'baker', 'nola', etc., with 'baker' tested
    first. The 2026-08-24 sweep note on treme-gov-nicholls contains the words
    "the 46pct at which Baker was PASSED", so a Tremé building was valued
    against Baton Rouge comps: $336,170 total / $42,021 per unit, against a
    correct New Orleans read of $664,377 / $83,047 — 49% low on a deal asking
    $106K/unit. CLAUDE.md is explicit that the Baton Rouge per-unit screen does
    not transfer to New Orleans. History prose is not a location; the parish is.
    """
    try:
        import latax
    except ImportError:
        return None, "latax unavailable"
    if not location:
        return None, "no location on the deal record"
    parish, how = latax.resolve_parish(location)
    if parish is None:
        return None, how
    market = PARISH_TO_MARKET.get(parish)
    if market is None:
        return None, f"no comp market mapped for {parish.title()} Parish"
    return market, f"{how} -> {market.title()} comps"


# ---------------------------------------------------------------------------
# Pure-stdlib OLS via normal equations (X'X)^{-1} X'y
# Works fine for n~60, k=6 on any machine without numpy.
# ---------------------------------------------------------------------------

def _matmul(A, B):
    """Matrix multiply A (m x n) by B (n x p) -> m x p."""
    m, n = len(A), len(A[0])
    p = len(B[0])
    C = [[0.0] * p for _ in range(m)]
    for i in range(m):
        for k in range(n):
            if A[i][k] == 0.0:
                continue
            for j in range(p):
                C[i][j] += A[i][k] * B[k][j]
    return C


def _transpose(A):
    m, n = len(A), len(A[0])
    return [[A[i][j] for i in range(m)] for j in range(n)]


def _mat_inv(A):
    """Gauss-Jordan inversion of square matrix A (in-place on a copy)."""
    n = len(A)
    M = [row[:] for row in A]
    I = [[float(i == j) for j in range(n)] for i in range(n)]
    for col in range(n):
        # pivot
        max_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[max_row] = M[max_row], M[col]
        I[col], I[max_row] = I[max_row], I[col]
        pivot = M[col][col]
        if abs(pivot) < 1e-15:
            raise ValueError("Matrix is singular — model is unidentifiable. "
                             "Check for collinear regressors.")
        for j in range(n):
            M[col][j] /= pivot
            I[col][j] /= pivot
        for row in range(n):
            if row == col:
                continue
            factor = M[row][col]
            for j in range(n):
                M[row][j] -= factor * M[col][j]
                I[row][j] -= factor * I[col][j]
    return I


def ols(X, y):
    """Ordinary least squares. X is list of row-vectors; y is list of scalars.
    Returns (coefficients, y_hat, residuals, r_squared, residual_se)."""
    n = len(y)
    k = len(X[0])
    Xt = _transpose(X)
    XtX = [[sum(Xt[r][i] * X[i][c] for i in range(n)) for c in range(k)] for r in range(k)]
    Xty = [sum(Xt[r][i] * y[i] for i in range(n)) for r in range(k)]
    XtX_inv = _mat_inv(XtX)
    beta = [sum(XtX_inv[i][j] * Xty[j] for j in range(k)) for i in range(k)]
    y_hat = [sum(beta[j] * X[i][j] for j in range(k)) for i in range(n)]
    residuals = [y[i] - y_hat[i] for i in range(n)]
    y_mean = statistics.mean(y)
    ss_tot = sum((v - y_mean) ** 2 for v in y)
    ss_res = sum(r ** 2 for r in residuals)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = n - k
    res_se = math.sqrt(ss_res / dof) if dof > 0 else float("nan")
    return beta, y_hat, residuals, r2, res_se


# ---------------------------------------------------------------------------
# Data loading & cleaning
# ---------------------------------------------------------------------------

def load_data():
    raw = json.loads(LOG.read_text())
    return raw


def prepare(raw):
    """Filter, exclude distressed/missing, apply MAD outlier rule.
    Returns (clean_rows, exclusions) where each row is a dict with added
    fields log_ppu and year_idx."""
    exclusions = []
    candidates = []

    for e in raw:
        name = e.get("name", "?")
        if e.get("units") is None or e.get("price") is None:
            exclusions.append((name, "missing units or price"))
            continue
        units = e["units"]
        price = e["price"]
        if units <= 0 or price <= 0:
            exclusions.append((name, "non-positive units or price"))
            continue
        ppu = price / units
        note = (e.get("note") or "").lower()
        if "auction" in note or "distressed" in note:
            exclusions.append((name, f"distressed/auction: {ppu:,.0f} $/unit"))
            continue
        mkt = (e.get("market") or "").lower().strip()
        if mkt not in KNOWN_MARKETS:
            exclusions.append((name, f"unknown market '{mkt}'"))
            continue
        year = int(e["date"][:4])
        candidates.append({**e, "ppu": ppu, "log_ppu": math.log(ppu),
                            "year": year, "year_idx": year - 2021, "market": mkt})

    if not candidates:
        print("ERROR: no usable rows after initial filtering.", file=sys.stderr)
        sys.exit(1)

    # MAD outlier rule on log($/unit)
    log_ppus = [r["log_ppu"] for r in candidates]
    med = statistics.median(log_ppus)
    abs_devs = [abs(v - med) for v in log_ppus]
    mad = statistics.median(abs_devs)
    threshold = 2.5 * mad

    clean = []
    for r, dev in zip(candidates, abs_devs):
        if dev > threshold:
            exclusions.append((r["name"],
                               f"MAD outlier: ${r['ppu']:,.0f}/unit "
                               f"(|log deviation| {dev:.3f} > 2.5 MAD {threshold:.3f})"))
        else:
            clean.append(r)

    return clean, exclusions


def build_design_matrix(rows):
    """Build X matrix (with intercept) and y vector from clean rows."""
    X, y = [], []
    for r in rows:
        mkt = r["market"]
        row = [
            1.0,                                    # intercept
            1.0 if mkt == "new orleans" else 0.0,  # d_new_orleans
            1.0 if mkt == "lafayette" else 0.0,    # d_lafayette
            1.0 if mkt == "northshore" else 0.0,   # d_northshore
            math.log(r["units"]),                  # log(units)
        ]
        X.append(row)
        y.append(r["log_ppu"])
    return X, y


# ---------------------------------------------------------------------------
# Model fit (cached in module-level dict so predict can reuse)
# ---------------------------------------------------------------------------

_FIT_CACHE = {}


def fit(data=None, verbose=True):
    """Fit OLS, print diagnostics, return fit dict."""
    if _FIT_CACHE and data is None:
        return _FIT_CACHE

    raw = data or load_data()
    clean, exclusions = prepare(raw)
    X, y = build_design_matrix(clean)
    n = len(clean)
    k = len(X[0])  # 6

    beta, y_hat, residuals, r2, res_se = ols(X, y)

    # Multiplicative error band: ±1 res_se in log space -> factor e^res_se
    band_factor = math.exp(res_se) - 1  # fractional: e.g. 0.34 means ±34%

    if verbose:
        _print_fit(clean, exclusions, beta, y_hat, residuals, r2, res_se,
                   band_factor, n, k)

    result = {
        "beta": beta,
        "r2": r2,
        "res_se": res_se,
        "band_factor": band_factor,
        "n": n,
        "k": k,
        "clean": clean,
        "y_hat": y_hat,
        "residuals": residuals,
        "units_range": (min(r["units"] for r in clean),
                        max(r["units"] for r in clean)),
    }
    _FIT_CACHE.update(result)
    return result


def _print_fit(clean, exclusions, beta, y_hat, residuals, r2, res_se,
               band_factor, n, k):
    print("=" * 70)
    print("HEDONIC MODEL FIT — Louisiana Multifamily/MHP 2021-2026")
    print("=" * 70)

    print(f"\nEXCLUSIONS ({len(exclusions)} removed):")
    for name, reason in exclusions:
        print(f"  - {name[:45]}: {reason}")

    print(f"\nSAMPLE: n={n} sales used, k={k} parameters, dof={n - k}")
    print()

    names = [
        "intercept (log $/unit, baton rouge base, 0 units-log)",
        "new orleans premium vs baton rouge",
        "lafayette premium vs baton rouge",
        "northshore premium vs baton rouge",
        "log(units) coefficient  [size discount/premium per 1% more units]",
    ]
    print("COEFFICIENTS:")
    for i, (name, b) in enumerate(zip(names, beta)):
        if i == 0:
            implied_base = math.exp(b)
            print(f"  β{i}  {b:+.4f}  {name}")
            print(f"       → implies base $/unit at 1 unit = ${implied_base:,.0f}")
        elif i in (1, 2, 3):
            pct = (math.exp(b) - 1) * 100
            print(f"  β{i}  {b:+.4f}  {name}")
            print(f"       → market multiplier {math.exp(b):.3f}x "
                  f"({pct:+.1f}% vs baton rouge)")
        elif i == 4:
            print(f"  β{i}  {b:+.4f}  {name}")
            pct_per_1pct = (math.exp(b * 0.01) - 1) * 100
            print(f"       → {b:.4f} in log-log: each 1% more units → "
                  f"{pct_per_1pct:+.3f}% change in $/unit")
            print(f"       → doubling units: $/unit factor = {math.exp(b * math.log(2)):.3f}x")

    print()
    print("MODEL QUALITY:")
    print(f"  R²             = {r2:.3f}  "
          f"{'(reasonable for cross-sectional RE)' if r2 >= 0.4 else '*** LOW — model explains little variation ***'}")
    print(f"  Residual SE    = {res_se:.4f} (in log space)")
    print(f"  ±1 SE band     ≈ ×/{math.exp(res_se):.3f}  "
          f"(roughly ±{band_factor * 100:.0f}% multiplicative)")
    print(f"  ±2 SE band     ≈ ×/{math.exp(2 * res_se):.3f}  "
          f"(roughly ±{(math.exp(2 * res_se) - 1) * 100:.0f}%)")

    if r2 < 0.40:
        print()
        print("  *** WARNING: R² < 0.40. The model captures broad market structure")
        print("      but leaves most price variation unexplained. Use the band,")
        print("      not the point estimate, as your decision input. ***")

    print()
    print("SAMPLE ACTUAL vs PREDICTED (5 sales):")
    sample_idx = [0, len(clean) // 4, len(clean) // 2,
                  3 * len(clean) // 4, len(clean) - 1]
    print(f"  {'name':<35} {'actual $/u':>10} {'pred $/u':>10} {'resid%':>8}")
    for i in sample_idx:
        r = clean[i]
        actual = r["ppu"]
        pred = math.exp(y_hat[i])
        resid_pct = (actual / pred - 1) * 100
        print(f"  {r['name'][:35]:<35} {actual:>10,.0f} {pred:>10,.0f} {resid_pct:>+8.1f}%")

    # Residual centering check
    mean_resid = statistics.mean(residuals)
    print(f"\n  Mean residual in log space: {mean_resid:.5f} "
          f"(should be ~0; OLS guarantees this with intercept)")


# ---------------------------------------------------------------------------
# Predict
# ---------------------------------------------------------------------------

def predict(market, units, year=2026, fit_result=None, loud=True):
    """Return dict with point estimate, low/high band, and total value.

    Refuses (SystemExit) if market unknown or units outside observed range.
    """
    market = market.lower().strip()
    if market not in KNOWN_MARKETS:
        print(f"ERROR: unknown market '{market}'. "
              f"Known: {sorted(KNOWN_MARKETS)}", file=sys.stderr)
        sys.exit(1)

    fr = fit_result or fit(verbose=False)
    lo_units, hi_units = fr["units_range"]
    if units < lo_units or units > hi_units:
        print(f"ERROR: {units} units is outside observed range "
              f"[{lo_units}, {hi_units}]. "
              f"Extrapolation refused — no support in data.", file=sys.stderr)
        sys.exit(1)

    beta = fr["beta"]
    res_se = fr["res_se"]

    row = [
        1.0,
        1.0 if market == "new orleans" else 0.0,
        1.0 if market == "lafayette" else 0.0,
        1.0 if market == "northshore" else 0.0,
        math.log(units),
    ]
    log_pred = sum(beta[j] * row[j] for j in range(len(beta)))
    point = math.exp(log_pred)
    low = math.exp(log_pred - res_se)
    high = math.exp(log_pred + res_se)

    # Fit-time warnings must reach predict-time consumers (icmemo/briefing),
    # not just whoever ran `hedonic.py fit` once.
    caveats = [
        "no time trend is modelled — the sample is perfectly confounded "
        "(2021-22 = 31 Baton Rouge-only sales, 2023-24 = no data, 2026 = 24 "
        "NOLA/Northshore/Lafayette-heavy), so a year term measures market mix, "
        "not appreciation. This is a cross-sectional read at no particular date",
    ]
    # 2026-09-07: the year regressor used to be IN the model, carrying the
    # caveat above while still multiplying every prediction by exp(-0.0881 x 5)
    # = 0.644. Applying a coefficient the module itself labels invalid put the
    # Baton Rouge read at $41,664/unit against $59,100 without it — and a
    # 12-unit Baker at $499,969 against $709,198, which made the market
    # approach appear to corroborate a sub-$500K ladder on the strength of an
    # artifact. Dropping it costs R2 0.243 -> 0.204 and moves the non-Baton
    # Rouge markets by under 2.5%.
    if year is not None:
        caveats.append(f"the 'year' argument ({year}) is accepted for "
                       "compatibility and has NO effect on the estimate")
    support = _market_support(fr)
    n_mkt = support.get(market, 0)
    if n_mkt < 5:
        caveats.append(
            f"THIN SUPPORT: the {market.title()} coefficient is fit on "
            f"{n_mkt} sale(s). "
            + ("A one-observation dummy fits that sale exactly, so this is that "
               "sale's price per unit, not a market. " if n_mkt <= 1 else "")
            + "Treat as a single data point, not a comp set")
    if fr["r2"] < 0.40:
        caveats.append(f"R² = {fr['r2']:.3f} < 0.40 — model explains little "
                       "variation; use the band, not the point estimate")

    result = {
        "market": market,
        "units": units,
        "year": year,
        "point_per_unit": round(point),
        "low_per_unit": round(low),
        "high_per_unit": round(high),
        "total_point": round(point * units),
        "total_low": round(low * units),
        "total_high": round(high * units),
        "band_note": f"±1 residual SE in log space (~±{(fr['band_factor'] * 100):.0f}%)",
        "caveats": caveats,
    }

    if loud:
        print("=" * 60)
        print(f"HEDONIC PREDICTION: {market.title()}, {units} units, {year}")
        print("=" * 60)
        print(f"  $/unit  point:  ${result['point_per_unit']:>10,}")
        print(f"  $/unit  low:    ${result['low_per_unit']:>10,}  (−1 SE)")
        print(f"  $/unit  high:   ${result['high_per_unit']:>10,}  (+1 SE)")
        print(f"  Total   point:  ${result['total_point']:>12,}")
        print(f"  Total   low:    ${result['total_low']:>12,}")
        print(f"  Total   high:   ${result['total_high']:>12,}")
        print(f"  Band:   {result['band_note']}")
        print(f"  Model:  n={fr['n']}, R²={fr['r2']:.3f}, SE={res_se:.4f}")
        for cav in caveats:
            print(f"  CAVEAT: {cav}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Hedonic multifamily valuation estimator (Louisiana 2021-2026)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fit", help="Fit model and print coefficients, R², SE, exclusions")

    p = sub.add_parser("predict", help="Predict $/unit for a deal")
    p.add_argument("--market", required=True,
                   help="baton rouge | new orleans | lafayette | northshore")
    p.add_argument("--units", type=int, required=True)
    p.add_argument("--year", type=int, default=2026)

    args = ap.parse_args()

    if args.cmd == "fit":
        fit(verbose=True)
    elif args.cmd == "predict":
        fr = fit(verbose=False)
        predict(args.market, args.units, args.year, fit_result=fr)


if __name__ == "__main__":
    main()
