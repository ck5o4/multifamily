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
    #
    # Directional since the 2026-08-24 sweep. The failure mode this test exists
    # to catch is MC coming out OPTIMISTIC against the deal's own underwriting,
    # and a symmetric band cannot express that: once rent/expense growth were
    # also recentered, eden's honest P50 settled 4.2pts BELOW deterministic
    # (left skew from the insurance shock, the integer-unit vacancy process and
    # the exit-cap spread), which the old |diff| < 4pts band read as a failure.
    # Assert the direction that matters, and keep a loose floor for sanity.
    check("MC P50 does not exceed deterministic on eden (optimism guard)",
          mc["p50"] - det < 0.01,
          f"P50 {mc['p50']:.1%} vs det {det:.1%} (+{(mc['p50']-det)*100:.1f}pts)")
    check("MC P50 within 8pts below deterministic on eden (sanity floor)",
          det - mc["p50"] < 0.08,
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


def test_solver_returns_a_price_that_clears_its_own_target():
    """Every solved rung must actually hit the IRR it is labelled with.

    2026-08-24: the bisection break tested the last midpoint probe instead of
    the best clearing price it would return, so a near-miss midpoint ended the
    search while `best` still held a far lower price (hwy42 @16% reported
    $2,269,000 at 16.54% when $2,298,000 clears). And the result was ROUNDED to
    the nearest $1,000, which rounds UP past the boundary so the reported price
    no longer clears (treme 16% -> $664,000 @ 15.997%).
    """
    for deal in ("eden-church-mhp", "treme-gov-nicholls", "hwy42-mhp",
                 "covington-2nd", "baker-trails", "weber-city-mhp"):
        inputs = pymodel._load_deal(deal)
        for target in (0.13, 0.16, 0.22):
            res = pymodel.solve_price(dict(inputs), target)
            if not res or not res.get("price"):
                continue
            check(f"solve_price: {deal} @{target:.0%} clears its own target",
                  res["irr"] >= target,
                  f"${res['price']:,} -> {res['irr']:.4%} < {target:.0%}")

    # covington @16% was reported unreachable when it is reachable.
    res = pymodel.solve_price(dict(pymodel._load_deal("covington-2nd")), 0.16)
    check("solve_price: covington-2nd 16% is reachable, not 'unreachable'",
          res is not None and res.get("price") is not None,
          f"returned {res}")


def test_irr_requires_a_sign_change():
    """No sign change means no IRR. Returning a number there is fabrication.

    2026-08-24: all-zero flows returned the bisection bracket midpoint (450.05%)
    and [0,...,0,X] returned the Newton clamp (5000%). Both are reachable in one
    step from lp_pct=1.0 (investor funds all equity -> gp_capital == 0), which
    printed "GP IRR: 450.05%".
    """
    check("_irr: all-zero flows have no IRR", pymodel._irr([0, 0, 0, 0, 0, 0]) is None)
    check("_irr: inflow-only flows have no IRR",
          pymodel._irr([0, 0, 0, 0, 0, 17683]) is None)
    check("_irr: a normal flow still solves",
          pymodel._irr([-100, 10, 10, 10, 120]) is not None)
    r = pymodel.run(dict(pymodel._load_deal("covington-2nd"), lp_pct=1.0))
    check("GP IRR is None (not 450%) when the LP funds all equity",
          r["gp_irr"] is None and r["gp_capital"] == 0,
          f"gp_irr={r['gp_irr']} gp_capital={r['gp_capital']}")


def test_mc_growth_recentered_on_underwriting():
    """MC must not draw looser growth than the deal underwrites.

    2026-08-24: vacancy was recentered in the 2026-08-09 sweep but rent growth
    (fitted P50 3.215% vs a 2.0% underwrite) and expense growth (2.147% vs
    2.5%) were not, so MC P50 came out ABOVE the deterministic IRR on all seven
    deals and every quoted P(IRR>=13%) inherited the optimism.
    """
    for deal in ("eden-church-mhp", "treme-gov-nicholls", "baker-trails"):
        inputs = pymodel._load_deal(deal)
        det = pymodel.run(inputs)["levered_irr"]
        mc = pymodel.monte_carlo(inputs, n=1000, seed=42, deal_name=deal)
        check(f"MC P50 does not exceed deterministic on {deal}",
              mc["p50"] - det < 0.01,
              f"P50 {mc['p50']:.2%} vs det {det:.2%}")
    mc = pymodel.monte_carlo(pymodel._load_deal("eden-church-mhp"), n=200, seed=1,
                             deal_name="eden-church-mhp")
    check("MC reports the growth recentering note", bool(mc.get("growth_note")))


def test_part_year_t12_is_flagged_not_read_as_annual():
    """A 6-month statement must never be reported as an annual figure.

    2026-08-24: hwy42's 'P&L YTD 2026.xlsx' (JAN-JUN populated) parsed every
    expense at half its annual size with basis still reading 'under total
    header' - insurance $12,750 against a true $25,500. This became reachable
    when discovery started preferring the newest file.
    """
    import parsers
    from pathlib import Path
    import intake

    ytd = Path(intake.INTAKE) / "hwy42-mhp" / "P&L YTD 2026.xlsx"
    lines, notes = parsers.parse_t12(ytd, units=30)
    check("part-year T-12 raises a PART-YEAR note",
          any("PART-YEAR" in n for n in notes), f"notes={notes}")
    check("part-year T-12 stamps every line's basis",
          all("partial year" in v["basis"] for v in lines.values()),
          f"{[v['basis'] for v in lines.values()][:3]}")

    for full in ("hwy42-mhp/P&L 2025.xlsx", "eden-church-mhp/PL_2025_ACTUALS.xlsx"):
        _, n = parsers.parse_t12(Path(intake.INTAKE) / full, units=18)
        check(f"full-year statement is NOT flagged part-year ({full.split('/')[0]})",
              not any("PART-YEAR" in x for x in n))


def test_rent_roll_reports_its_own_implied_vacancy():
    """A roll with vacant units must surface the vacancy it implies.

    2026-08-24: vacant units' rents are imputed from the type median so they
    land in gross potential income, while the model was written the 7% template
    default. On baker-trails' OM roll that is 4 of 12 units - 33.3% vs 7%,
    roughly $30k/yr of NOI on a $750k asset.
    """
    import parsers
    from pathlib import Path
    import intake

    roll = (Path(intake.INTAKE) / "baker-trails"
            / "rentroll_baker_trails_OM_2026-08-13.csv")
    _, notes = parsers.parse_rent_roll(roll)
    hit = [n for n in notes if "IMPLIED PHYSICAL VACANCY" in n]
    check("rent roll reports implied physical vacancy", bool(hit), f"notes={notes}")
    check("implied vacancy on the baker OM roll reads 33.3%",
          bool(hit) and "33.3%" in hit[0], f"{hit}")


def test_flood_degrades_to_unknown_not_clear():
    """A failed or unstudied flood lookup is UNKNOWN, never 'no flood risk'.

    2026-08-24: interpret([]) returned sfha=False (so icmemo's `if sfha` gate
    dropped the flood line and asserted "SFHA: No"); FEMA's 'AREA NOT INCLUDED'
    and 'OPEN WATER' domain values fell through to "minimal hazard"; and
    fetch_json raised SystemExit (BaseException), making every `except Exception`
    guard dead code so a service failure killed the whole run.
    """
    import flood
    check("flood: FloodLookupError is a catchable Exception",
          issubclass(flood.FloodLookupError, Exception))
    check("flood: unmapped parcel is sfha UNKNOWN (None), not False",
          flood.interpret([])[1] is None)
    for z in ("AREA NOT INCLUDED", "OPEN WATER", "SOMETHING NEW"):
        zone, sfha, note = flood.interpret(
            [{"FLD_ZONE": z, "ZONE_SUBTY": None, "SFHA_TF": "F"}])
        check(f"flood: {z!r} is undetermined, not minimal",
              sfha is None and "UNDETERMINED" in note,
              f"sfha={sfha} note={note!r}")
    check("flood: plain X is still minimal and not SFHA",
          flood.interpret([{"FLD_ZONE": "X", "ZONE_SUBTY": None, "SFHA_TF": "F"}])[1] is False)
    check("flood: AE is still SFHA",
          flood.interpret([{"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T"}])[1] is True)


def test_rentcast_matches_by_address_hint():
    """A deal named for its neighbourhood still finds its address-keyed cache.

    2026-08-24: the slug-token matcher required every >3-char token of the deal
    slug to appear in the cache filename. Cache files are named for the street,
    so 'treme-gov-nicholls' (no shared token with '1429-governor-nicholls-st')
    silently missed its own paid cache and the MC ran with no rent-level risk.
    """
    good = pymodel._load_rentcast_mult("treme-gov-nicholls", 1400.0)
    check("rentcast: treme matches via the deals.json address hint",
          good is not None, f"got {good}")
    baker = pymodel._load_rentcast_mult("baker-trails", 800.0)
    check("rentcast: baker matches via the deals.json address hint",
          baker is not None, f"got {baker}")
    # A deal with no cache and no hint must produce a LOUD note, not silence.
    mc = pymodel.monte_carlo(pymodel._load_deal("weber-city-mhp"), n=200, seed=1,
                             deal_name="no-such-deal-xyz")
    check("rentcast: an unmatched deal emits a loud rent_level_note",
          "no RentCast cache matched" in (mc.get("rent_level_note") or ""),
          f"note={mc.get('rent_level_note')!r}")


def test_beats_index_is_deterministic():
    """The house gate must not depend on an arbitrary RNG seed.

    2026-08-24: pairing each IRR sample with one rng.gauss() draw put +/-3pp of
    pure RNG noise on a rule whose threshold is exactly 50% (eden ranged
    52.1%-57.7% across twenty seed choices). Closed form removes that term.
    """
    import board
    samples = [0.02, 0.08, 0.10, 0.12, 0.20, -0.05]
    a = board._beats_index(samples)
    b = board._beats_index(samples)
    check("beats_index is deterministic", a == b, f"{a} vs {b}")
    check("beats_index of a far-above-market sample set approaches 1",
          board._beats_index([0.60] * 50) > 0.99)
    check("beats_index of a far-below-market sample set approaches 0",
          board._beats_index([-0.40] * 50) < 0.01)
    check("beats_index of an at-market sample set is ~0.5",
          abs(board._beats_index([0.10] * 50) - 0.5) < 1e-9)
    check("beats_index returns None on no samples", board._beats_index([]) is None)


def main():
    print("REGRESSION TESTS (2026-08-09 sweep)")
    test_solve_not_false_unreachable()
    test_no_phantom_debt_service()
    test_waterfall_invalid_flag()
    test_mc_vacancy_centered_on_underwriting()
    test_rentcast_matcher_requires_all_tokens()
    print("REGRESSION TESTS (2026-08-24 sweep)")
    test_discovery_prefers_newest_and_reports_alternates()
    test_solver_returns_a_price_that_clears_its_own_target()
    test_irr_requires_a_sign_change()
    test_mc_growth_recentered_on_underwriting()
    test_part_year_t12_is_flagged_not_read_as_annual()
    test_rent_roll_reports_its_own_implied_vacancy()
    test_flood_degrades_to_unknown_not_clear()
    test_rentcast_matches_by_address_hint()
    test_beats_index_is_deterministic()
    if FAILURES:
        print(f"\nRESULT: {len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\nRESULT: all regression tests passed")


if __name__ == "__main__":
    main()
