#!/usr/bin/env python3
"""Tests for the upgraded monte_carlo() in pymodel.py.

Tests:
  1. Deterministic seed reproducibility
  2. Marginals within expected ranges
  3. Sample correlations within ±0.15 of spec
  4. fitted_params.json loads and has required provenance keys
  5. Auto-scale runs at least 1000 draws
  6. P10 <= P50 <= P90

Run:
    python3 tools/test_mc.py
    python3 tools/test_mc.py -v
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pymodel

ROOT = Path(__file__).resolve().parent.parent

DEMO_INPUTS = {
    "price": 450_000,
    "unit_mix": [
        {"units": 4, "sf": 800, "rent": 850},
        {"units": 4, "sf": 1000, "rent": 1050},
    ],
    "taxes_annual": 8_000,
    "insurance": 2000,
    "vacancy": 0.07,
    "hold_years": 5,
}

PASSES = []
FAILS = []
VERBOSE = False


def ok(label):
    PASSES.append(label)
    if VERBOSE:
        print(f"  OK   {label}")


def fail(label, msg=""):
    PASSES  # keep reference
    FAILS.append(f"{label}: {msg}")
    print(f"  FAIL {label}: {msg}")


def assert_close(label, got, expected, tol):
    diff = abs(got - expected)
    if diff <= tol:
        ok(label)
    else:
        fail(label, f"got={got:.6g} expected={expected:.6g} diff={diff:.6g} tol={tol}")


def assert_in_range(label, val, lo, hi):
    if lo <= val <= hi:
        ok(label)
    else:
        fail(label, f"val={val:.4g} not in [{lo:.4g}, {hi:.4g}]")


# ---------------------------------------------------------------------------
# Test 1: Reproducibility
# ---------------------------------------------------------------------------
def test_reproducibility():
    print("\n[1] Deterministic seed reproducibility")
    mc1 = pymodel.monte_carlo(DEMO_INPUTS, n=200, seed=99)
    mc2 = pymodel.monte_carlo(DEMO_INPUTS, n=200, seed=99)
    mc3 = pymodel.monte_carlo(DEMO_INPUTS, n=200, seed=7)

    assert_close("seed99 P10 reproducible", mc1["p10"], mc2["p10"], tol=1e-12)
    assert_close("seed99 P50 reproducible", mc1["p50"], mc2["p50"], tol=1e-12)
    assert_close("seed99 P90 reproducible", mc1["p90"], mc2["p90"], tol=1e-12)
    assert_close("n_draws reproducible", mc1["n_draws"], mc2["n_draws"], tol=0)

    # Different seed → different result (with overwhelming probability)
    if mc1["p50"] != mc3["p50"]:
        ok("different seed gives different P50")
    else:
        fail("different seed gives different P50", "P50 identical across different seeds (unlikely but possible)")


# ---------------------------------------------------------------------------
# Test 2: Marginals within plausible ranges
# ---------------------------------------------------------------------------
def test_marginals():
    print("\n[2] Marginals within expected ranges")
    mc = pymodel.monte_carlo(DEMO_INPUTS, n=5000, seed=42)
    irrs = mc["irr_samples"]
    n = len(irrs)

    assert_in_range("n_valid >= 4800", n, 4800, 5000)

    # IRR distribution should be in a sane range for a stabilized deal
    # P10 can be negative in bad scenarios (high vacancy + rent drop + cap expansion)
    assert_in_range("P10 IRR in [-20%, 30%]", mc["p10"], -0.20, 0.30)
    assert_in_range("P50 IRR in [-10%, 30%]", mc["p50"], -0.10, 0.30)
    assert_in_range("P90 IRR in [0%, 40%]", mc["p90"], 0.0, 0.40)

    # P10 <= P50 <= P90
    if mc["p10"] <= mc["p50"]:
        ok("P10 <= P50")
    else:
        fail("P10 <= P50", f"P10={mc['p10']:.4f} P50={mc['p50']:.4f}")

    if mc["p50"] <= mc["p90"]:
        ok("P50 <= P90")
    else:
        fail("P50 <= P90", f"P50={mc['p50']:.4f} P90={mc['p90']:.4f}")

    # p_above_13 should be a valid probability
    assert_in_range("p_above_13 in [0,1]", mc["p_above_13"], 0.0, 1.0)

    # n_draws and se_p10 present
    if mc["n_draws"] >= 1:
        ok("n_draws populated")
    else:
        fail("n_draws populated", f"n_draws={mc['n_draws']}")

    if not math.isnan(mc["se_p10"]):
        ok("se_p10 is a number")
    else:
        fail("se_p10 is a number", "se_p10 is nan")


# ---------------------------------------------------------------------------
# Test 3: Sample correlations close to spec
# ---------------------------------------------------------------------------
def test_correlations():
    print("\n[3] Sample correlations within ±0.15 of spec")
    # We need raw draws to check correlations; collect them by running with a
    # patched version that stores draws. Instead, we reconstruct draws by
    # driving the copula directly.

    import random

    # Drive correlated draws directly
    n = 5000
    rng = random.Random(42)

    draws = {k: [] for k in ["rg", "eg", "vac", "ins", "cap"]}

    # Load fitted params for marginal bounds
    fp_path = ROOT / "tools" / "fitted_params.json"
    if fp_path.exists():
        with open(fp_path) as f:
            fp = json.load(f)
        rg = (fp["rent_growth"]["p10"], fp["rent_growth"]["p50"], fp["rent_growth"]["p90"])
        eg = (fp["expense_growth"]["p10"], fp["expense_growth"]["p50"], fp["expense_growth"]["p90"])
        vac = (fp["vacancy"]["p10"], fp["vacancy"]["p50"], fp["vacancy"]["p90"])
        ins = (fp["insurance_mult"]["p10"], fp["insurance_mult"]["p50"], fp["insurance_mult"]["p90"])
        cap = (fp["exit_cap_spread"]["p10"], fp["exit_cap_spread"]["p50"], fp["exit_cap_spread"]["p90"])
    else:
        rg = (0.01, 0.02, 0.03)
        eg = (0.02, 0.025, 0.04)
        vac = (0.05, 0.07, 0.10)
        ins = (0.85, 1.0, 1.60)
        cap = (-0.0025, 0.005, 0.013)

    for _ in range(n):
        u5 = pymodel._correlated_uniforms(rng)
        draws["rg"].append(pymodel._tri_icdf(u5[0], *rg))
        draws["eg"].append(pymodel._tri_icdf(u5[1], *eg))
        draws["vac"].append(pymodel._tri_icdf(u5[2], *vac))
        draws["ins"].append(pymodel._tri_icdf(u5[3], *ins))
        draws["cap"].append(pymodel._tri_icdf(u5[4], *cap))

    def pearson(x, y):
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / (n - 1))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / (n - 1))
        return cov / (sx * sy) if sx * sy > 0 else 0.0

    tol = 0.15

    rg_eg_corr = pearson(draws["rg"], draws["eg"])
    assert_in_range("rg↔eg corr near 0.3", rg_eg_corr, 0.3 - tol, 0.3 + tol)
    if VERBOSE:
        print(f"    rg↔eg sample corr = {rg_eg_corr:.3f} (spec 0.3)")

    rg_vac_corr = pearson(draws["rg"], draws["vac"])
    assert_in_range("rg↔vac corr near -0.5", rg_vac_corr, -0.5 - tol, -0.5 + tol)
    if VERBOSE:
        print(f"    rg↔vac sample corr = {rg_vac_corr:.3f} (spec -0.5)")

    ins_cap_corr = pearson(draws["ins"], draws["cap"])
    assert_in_range("ins↔cap corr near 0.3", ins_cap_corr, 0.3 - tol, 0.3 + tol)
    if VERBOSE:
        print(f"    ins↔cap sample corr = {ins_cap_corr:.3f} (spec 0.3)")

    # Cross-block correlations should be near 0
    rg_ins_corr = pearson(draws["rg"], draws["ins"])
    assert_in_range("rg↔ins corr near 0", rg_ins_corr, -tol, tol)
    if VERBOSE:
        print(f"    rg↔ins sample corr = {rg_ins_corr:.3f} (spec 0.0)")

    vac_cap_corr = pearson(draws["vac"], draws["cap"])
    assert_in_range("vac↔cap corr near 0", vac_cap_corr, -tol, tol)
    if VERBOSE:
        print(f"    vac↔cap sample corr = {vac_cap_corr:.3f} (spec 0.0)")


# ---------------------------------------------------------------------------
# Test 4: fitted_params.json has required provenance keys
# ---------------------------------------------------------------------------
def test_fitted_params_json():
    print("\n[4] fitted_params.json provenance check")
    fp_path = ROOT / "tools" / "fitted_params.json"

    if not fp_path.exists():
        fail("fitted_params.json exists", f"not found at {fp_path}")
        return

    ok("fitted_params.json exists")

    with open(fp_path) as f:
        fp = json.load(f)

    required_params = ["rent_growth", "expense_growth", "vacancy",
                       "insurance_mult", "exit_cap_spread"]
    required_keys = ["source", "fit_date", "provenance", "p10", "p50", "p90"]

    for param in required_params:
        if param not in fp:
            fail(f"{param} present in JSON", "missing key")
            continue
        ok(f"{param} present")

        entry = fp[param]
        for key in required_keys:
            if key not in entry:
                fail(f"{param}.{key} present", "missing key")
            else:
                ok(f"{param}.{key} present")

        # provenance must be FITTED or JUDGMENT
        prov = entry.get("provenance", "")
        if prov in ("FITTED", "JUDGMENT", "JUDGMENT-FALLBACK"):
            ok(f"{param}.provenance valid ({prov})")
        else:
            fail(f"{param}.provenance valid", f"got '{prov}'")

        # P10 <= P50 <= P90
        p10, p50, p90 = entry.get("p10"), entry.get("p50"), entry.get("p90")
        if p10 is not None and p50 is not None and p90 is not None:
            if p10 <= p50 <= p90:
                ok(f"{param} P10<=P50<=P90")
            else:
                fail(f"{param} P10<=P50<=P90", f"P10={p10} P50={p50} P90={p90}")


# ---------------------------------------------------------------------------
# Test 5: Auto-scale runs at least 1000 draws
# ---------------------------------------------------------------------------
def test_autoscale():
    print("\n[5] Auto-scale runs at least 1000 draws")
    # n=0 triggers auto-scale
    mc = pymodel.monte_carlo(DEMO_INPUTS, n=0, seed=42)
    if mc["n_draws"] >= 1000:
        ok(f"auto-scale n_draws >= 1000 (got {mc['n_draws']})")
    else:
        fail("auto-scale n_draws >= 1000", f"got {mc['n_draws']}")

    # SE should be a finite number
    if not math.isnan(mc["se_p10"]) and not math.isinf(mc["se_p10"]):
        ok(f"se_p10 is finite (got {mc['se_p10']*100:.3f}pts)")
    else:
        fail("se_p10 is finite", f"se_p10={mc['se_p10']}")

    # If we got enough draws, SE should be < target (or close)
    if mc["n_draws"] >= 2000:
        ok("auto-scale ran enough draws for convergence test")
        if mc["se_p10"] < 0.01:  # very generous
            ok(f"se_p10 < 1.0pts (got {mc['se_p10']*100:.3f}pts)")
        else:
            fail("se_p10 < 1.0pts", f"got {mc['se_p10']*100:.3f}pts")


# ---------------------------------------------------------------------------
# Test 6: P10 <= P50 <= P90 with multiple seeds
# ---------------------------------------------------------------------------
def test_ordering():
    print("\n[6] P10 <= P50 <= P90 across seeds")
    for seed in [0, 1, 42, 123, 999]:
        mc = pymodel.monte_carlo(DEMO_INPUTS, n=500, seed=seed)
        if mc["p10"] is None:
            fail(f"seed={seed} produced results", "no IRR samples")
            continue
        if mc["p10"] <= mc["p50"] <= mc["p90"]:
            ok(f"P10<=P50<=P90 seed={seed}")
        else:
            fail(f"P10<=P50<=P90 seed={seed}",
                 f"P10={mc['p10']:.4f} P50={mc['p50']:.4f} P90={mc['p90']:.4f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose

    print("=" * 60)
    print("MC TEST SUITE")
    print("=" * 60)

    test_reproducibility()
    test_marginals()
    test_correlations()
    test_fitted_params_json()
    test_autoscale()
    test_ordering()

    print(f"\n{'='*60}")
    n_pass = len(PASSES)
    n_fail = len(FAILS)
    print(f"RESULT: {n_pass} passed, {n_fail} failed")
    if FAILS:
        print("FAILURES:")
        for f in FAILS:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("ALL MC TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
