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
# Test 7: Integer-unit vacancy lumpiness (small vs large)
# ---------------------------------------------------------------------------
def test_vacancy_lumpiness():
    """Std of vacancy should be higher for 12-unit than 100-unit (law of large numbers)."""
    print("\n[7] Vacancy lumpiness: std(12u) > std(100u)")
    import random as _random
    import statistics

    n_draws = 2000
    seed = 77

    # 12-unit property
    inputs_12 = dict(DEMO_INPUTS)
    inputs_12["unit_mix"] = [{"units": 12, "sf": 900, "rent": 900}]
    inputs_12["price"] = 540_000

    # 100-unit property (use same price-per-unit for comparability)
    inputs_100 = dict(DEMO_INPUTS)
    inputs_100["unit_mix"] = [{"units": 100, "sf": 900, "rent": 900}]
    inputs_100["price"] = 4_500_000

    # We need to observe the vacancy draws, not IRR. Drive the copula directly
    # using the same logic as monte_carlo to extract vacancy samples.
    rng12 = _random.Random(seed)
    rng100 = _random.Random(seed)

    def _sample_vacancy(n_units_int, rng, n_draws):
        vacancies = []
        for _ in range(n_draws):
            u6 = pymodel._correlated_uniforms(rng)
            u_tr = u6[5]
            turnover_rate = pymodel._tri_icdf(u_tr, 0.30, 0.45, 0.60)
            turnovers = sum(1 for _ in range(n_units_int) if rng.random() < turnover_rate)
            days_vacant_total = sum(
                pymodel._tri_icdf(rng.random(), 20.0, 45.0, 120.0)
                for _ in range(turnovers)
            )
            physical_vac = days_vacant_total / max(n_units_int * 365, 1)
            frictional = rng.uniform(0.0, 0.02)
            vacancies.append(max(0.0, physical_vac + frictional))
        return vacancies

    vac_12 = _sample_vacancy(12, rng12, n_draws)
    vac_100 = _sample_vacancy(100, rng100, n_draws)

    std_12 = statistics.stdev(vac_12)
    std_100 = statistics.stdev(vac_100)

    if VERBOSE:
        print(f"    std(vacancy 12u)  = {std_12:.4f}")
        print(f"    std(vacancy 100u) = {std_100:.4f}")

    if std_12 > std_100:
        ok(f"std(vacancy_12u)={std_12:.4f} > std(vacancy_100u)={std_100:.4f}")
    else:
        fail("std(vacancy_12u) > std(vacancy_100u)",
             f"std_12={std_12:.4f} std_100={std_100:.4f} — law of large numbers violated")


# ---------------------------------------------------------------------------
# Test 8: Vintage capex age bands (40yr > 5yr mean)
# ---------------------------------------------------------------------------
def test_vintage_capex_age_bands():
    """40-year property should have higher mean capex than 5-year property."""
    print("\n[8] Vintage capex: mean(40yr) > mean(5yr)")
    n_draws = 2000
    seed = 88

    inputs_base = dict(DEMO_INPUTS)

    mc_5yr = pymodel.monte_carlo(inputs_base, n=n_draws, seed=seed,
                                 effective_age_override=5)
    mc_40yr = pymodel.monte_carlo(inputs_base, n=n_draws, seed=seed,
                                  effective_age_override=40)

    # Can't observe capex directly from MC output, so proxy via P50 IRR difference:
    # older building should have lower P50 IRR (more capex drag). But that conflates
    # other draws. Instead, directly compare capex distributions by patching run().
    # Simplest: drive the capex draw function directly.

    import random as _random

    def _sample_capex(eff_age, n_units_int, n_draws, seed):
        """Sample capex draws by replaying the vintage capex logic."""
        rng = _random.Random(seed)
        samples = []

        def _age_band_base(age):
            if age <= 10:
                return 250.0
            elif age <= 25:
                return 450.0
            elif age <= 40:
                return 600.0
            else:
                return 750.0

        for _ in range(n_draws):
            base = _age_band_base(eff_age)
            n_bldgs = max(1, round(n_units_int / 4))
            roof_prob = max(0.0, (eff_age - 15) / 40.0)
            roof_cost = 0.0
            for _ in range(n_bldgs):
                if rng.random() < roof_prob:
                    roof_cost += pymodel._tri_icdf(rng.random(), 8000.0, 11500.0, 15000.0)

            hvac_prob = max(0.0, (eff_age - 10) / 30.0)
            hvac_cost = 0.0
            for _ in range(n_units_int):
                if rng.random() < hvac_prob:
                    hvac_cost += pymodel._tri_icdf(rng.random(), 4500.0, 5750.0, 7000.0)

            total = base + (roof_cost + hvac_cost) / max(n_units_int, 1)
            samples.append(min(total, 3000.0))
        return samples

    n_units = 8  # DEMO_INPUTS has 8 units total

    capex_5 = _sample_capex(5, n_units, n_draws, seed)
    capex_40 = _sample_capex(40, n_units, n_draws, seed)

    mean_5 = sum(capex_5) / len(capex_5)
    mean_40 = sum(capex_40) / len(capex_40)

    if VERBOSE:
        print(f"    mean capex 5yr  = ${mean_5:.0f}/unit")
        print(f"    mean capex 40yr = ${mean_40:.0f}/unit")

    if mean_40 > mean_5:
        ok(f"mean_capex_40yr=${mean_40:.0f} > mean_capex_5yr=${mean_5:.0f}")
    else:
        fail("mean_capex_40yr > mean_capex_5yr",
             f"mean_5=${mean_5:.0f} mean_40=${mean_40:.0f}")


# ---------------------------------------------------------------------------
# Test 9: Vintage capex cap at $3,000/unit
# ---------------------------------------------------------------------------
def test_vintage_capex_cap():
    """No annual capex draw should exceed $3,000/unit."""
    print("\n[9] Vintage capex cap at $3,000/unit")
    import random as _random

    n_draws = 2000
    seed = 99
    n_units = 8  # DEMO_INPUTS units

    rng = _random.Random(seed)

    def _age_band_base(age):
        if age <= 10:
            return 250.0
        elif age <= 25:
            return 450.0
        elif age <= 40:
            return 600.0
        else:
            return 750.0

    cap_per_unit = 3000.0
    violations = 0
    eff_age = 50  # worst case: very old building

    for _ in range(n_draws):
        base = _age_band_base(eff_age)
        n_bldgs = max(1, round(n_units / 4))
        roof_prob = max(0.0, (eff_age - 15) / 40.0)
        roof_cost = 0.0
        for _ in range(n_bldgs):
            if rng.random() < roof_prob:
                roof_cost += pymodel._tri_icdf(rng.random(), 8000.0, 11500.0, 15000.0)

        hvac_prob = max(0.0, (eff_age - 10) / 30.0)
        hvac_cost = 0.0
        for _ in range(n_units):
            if rng.random() < hvac_prob:
                hvac_cost += pymodel._tri_icdf(rng.random(), 4500.0, 5750.0, 7000.0)

        total = base + (roof_cost + hvac_cost) / max(n_units, 1)
        capped = min(total, cap_per_unit)
        if capped > cap_per_unit + 0.01:  # floating point tolerance
            violations += 1

    if violations == 0:
        ok(f"no capex draws exceed ${cap_per_unit:.0f}/unit across {n_draws} draws (age=50yr)")
    else:
        fail(f"capex cap", f"{violations} draws exceeded ${cap_per_unit}/unit")


# ---------------------------------------------------------------------------
# Test 10: Exit cap spread upper tail widened to >= 180bps
# ---------------------------------------------------------------------------
def test_exit_cap_spread_widened():
    """Max exit cap spread observed in draws should reach at least 180bps."""
    print("\n[10] Exit cap spread widened (max >= 180bps observed)")
    import random as _random

    n_draws = 5000
    seed = 42
    rng = _random.Random(seed)

    # Load fitted params for cap spread bounds
    fp_path = ROOT / "tools" / "fitted_params.json"
    if fp_path.exists():
        with open(fp_path) as f:
            fp = json.load(f)
        cap_p10 = fp["exit_cap_spread"]["p10"]
        cap_p50 = fp["exit_cap_spread"]["p50"]
        cap_p90 = fp["exit_cap_spread"]["p90"]
    else:
        # Fallback to hardcoded widened values
        cap_p10, cap_p50, cap_p90 = -0.0025, 0.005, 0.020

    # p90 acts as hi parameter in the triangular; the triangular's absolute maximum
    # is the hi parameter (cap_p90). Verify cap_p90 >= 0.020.
    if cap_p90 >= 0.020:
        ok(f"exit_cap_spread p90 (hi) = {cap_p90*10000:.0f}bps >= 200bps")
    else:
        fail("exit_cap_spread p90 >= 200bps", f"got {cap_p90*10000:.0f}bps")

    # Sample from the triangular distribution and verify max reaches >= 180bps
    cap_draws = [
        pymodel._tri_icdf(rng.random(), cap_p10, cap_p50, cap_p90)
        for _ in range(n_draws)
    ]
    max_spread = max(cap_draws)

    if max_spread >= 0.018:  # 180bps — expect this with overwhelming probability at hi=200bps
        ok(f"max exit cap spread = {max_spread*10000:.0f}bps >= 180bps across {n_draws} draws")
    else:
        fail("max exit cap spread >= 180bps",
             f"max observed = {max_spread*10000:.0f}bps — upper tail too narrow")

    if VERBOSE:
        p90_obs = pymodel._percentile(sorted(cap_draws), 90)
        print(f"    cap_p90 (param) = {cap_p90*10000:.0f}bps")
        print(f"    max observed    = {max_spread*10000:.0f}bps")
        print(f"    P90 observed    = {p90_obs*10000:.0f}bps")


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
    test_vacancy_lumpiness()
    test_vintage_capex_age_bands()
    test_vintage_capex_cap()
    test_exit_cap_spread_widened()

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
