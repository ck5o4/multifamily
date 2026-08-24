"""Regression tests for bugs fixed in the 2026-08-09 deep sweep.

Each test reproduces a bug that shipped and was caught by audit; a failure
here means the fix regressed. Run: python3 tools/test_regressions.py
"""

import sys

import pymodel


FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def base_inputs():
    return dict(pymodel._load_deal("baker-trails"),
                price=1_000_000, taxes_annual=12_000)


def test_solve_not_false_unreachable():
    """A midpoint IRR within tol just below target must not end the search."""
    inputs = base_inputs()
    asking = inputs["price"]
    mid = (max(50_000, asking * 0.35) + asking) / 2
    irr_mid = pymodel.run(dict(inputs, price=mid, exit_cap=None))["levered_irr"]
    res = pymodel.solve_price(dict(inputs), irr_mid + 0.00005)
    check("solve_price: no false 'unreachable' on tol-below midpoint",
          res is not None and res.get("price", 0) > 0,
          f"returned {res}")


def test_no_phantom_debt_service():
    """After the loan fully amortizes, debt service must be zero."""
    r = pymodel.run(dict(base_inputs(), amort_years=3, hold_years=5,
                         exit_cap=None))
    check("no phantom DS after amortization",
          r["total_ds"][4] == 0.0 and r["total_ds"][5] == 0.0,
          f"yr4={r['total_ds'][4]:,.0f} yr5={r['total_ds'][5]:,.0f}")
    check("final amortization year still charges its full 12 payments",
          abs(r["total_ds"][3] - r["total_ds"][1]) < 1.0,
          f"yr3={r['total_ds'][3]:,.0f} vs yr1={r['total_ds'][1]:,.0f}")


def test_waterfall_invalid_flag():
    """Negative distributable years must null LP/GP metrics and set the flag."""
    r = pymodel.run(dict(base_inputs(), refi_year=3, refi_valuation_cap=0.11,
                         refi_cost_pct=0.03, exit_cap=None))
    if not r.get("waterfall_neg_years"):
        check("waterfall invalid-flag scenario still produces a deficit year",
              False, "scenario no longer triggers; rebuild the trigger")
        return
    check("waterfall: LP/GP nulled when distributable cash is negative",
          r.get("waterfall_invalid") is True and r["lp_irr"] is None
          and r["gp_irr"] is None)


def test_mc_vacancy_centered_on_underwriting():
    """MC vacancy process must center on the deal's own vacancy + bad debt."""
    eden = pymodel._load_deal("eden-church-mhp")
    det = pymodel.run(eden)["levered_irr"]
    mc = pymodel.monte_carlo(eden, n=1000, seed=42,
                             deal_name="eden-church-mhp")
    # Pre-fix: P50 ~18.5% vs det 9.9% (vacancy swapped 13.7% -> 8.6%).
    # Post-fix P50 must sit within a few points of deterministic.
    check("MC P50 within 4pts of deterministic on eden",
          abs(mc["p50"] - det) < 0.04,
          f"P50 {mc['p50']:.1%} vs det {det:.1%}")
    check("MC reports the vacancy recentering note",
          bool(mc.get("vacancy_note")))


def test_rentcast_matcher_requires_all_tokens():
    """A different property sharing one name token must not match the cache."""
    good = pymodel._load_rentcast_mult("eden-church-mhp", 1180.0)
    bad = pymodel._load_rentcast_mult("church-street-8plex", 1000.0)
    check("rentcast: eden still matches its own cache", good is not None)
    check("rentcast: cross-property token match rejected", bad is None)


def test_discovery_prefers_newest_and_reports_alternates():
    """Multiple candidates for one slot: newest wins, losers are reported.

    2026-08-24 sweep: discover() took the alphabetically FIRST match and
    silently dropped the rest. baker-trails therefore resolved to
    rentroll_baker_trails_ESTIMATED.csv (GPR $118,800) instead of the seller's
    rentroll_baker_trails_OM_2026-08-13.csv (GPR $114,000 in-place, real
    8x2BR/4x3BR mix), with no warning that a second roll existed.
    """
    import intake
    from pathlib import Path

    baker = Path(intake.INTAKE) / "baker-trails"
    found, alt = intake.discover(baker)
    check("discovery: baker-trails resolves to the OM roll, not ESTIMATED",
          found.get("rent_roll") is not None
          and found["rent_roll"].name == "rentroll_baker_trails_OM_2026-08-13.csv",
          f"picked {found.get('rent_roll')}")
    check("discovery: the superseded roll is reported, not dropped",
          any(p.name == "rentroll_baker_trails_ESTIMATED.csv"
              for p in alt.get("rent_roll", [])),
          f"alternates {alt.get('rent_roll')}")

    hwy = Path(intake.INTAKE) / "hwy42-mhp"
    found2, alt2 = intake.discover(hwy)
    check("discovery: hwy42 T-12 picks YTD 2026 over P&L 2025",
          found2.get("t12") is not None and "2026" in found2["t12"].name,
          f"picked {found2.get('t12')}")
    check("discovery: hwy42's two rejected T-12s are both reported",
          len(alt2.get("t12", [])) == 2,
          f"alternates {alt2.get('t12')}")

    # A folder with one candidate per slot must report no alternates at all.
    eden = Path(intake.INTAKE) / "eden-church-mhp"
    _, alt3 = intake.discover(eden)
    check("discovery: no false alternates when each slot is unambiguous",
          alt3 == {}, f"alternates {alt3}")


def main():
    print("REGRESSION TESTS (2026-08-09 sweep)")
    test_solve_not_false_unreachable()
    test_no_phantom_debt_service()
    test_waterfall_invalid_flag()
    test_mc_vacancy_centered_on_underwriting()
    test_rentcast_matcher_requires_all_tokens()
    print("REGRESSION TESTS (2026-08-24 sweep)")
    test_discovery_prefers_newest_and_reports_alternates()
    if FAILURES:
        print(f"\nRESULT: {len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\nRESULT: all regression tests passed")


if __name__ == "__main__":
    main()
