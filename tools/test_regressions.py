"""Regression tests for bugs fixed in the 2026-08-09 deep sweep.

Each test reproduces a bug that shipped and was caught by audit; a failure
here means the fix regressed. Run: python3 tools/test_regressions.py
"""

import re
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


def test_unknown_model_input_is_refused():
    """An input key the engine does not read must raise, not be ignored.

    2026-09-07: `run({**deal, "purchase_price": 1_251_000})` returned the
    ask-price answer (eden 9.87%) instead of the $1.251M answer (32.91%) with
    no diagnostic, because _merge_defaults did `dict(defaults); update(inputs)`
    and nothing ever read the stray key. The repo's own history records a price
    ladder published and retracted over this class of slip (baker-trails,
    2026-08-03 "RED-TEAM CORRECTION"). Silence is the bug; the raise is the fix.
    """
    base = pymodel._load_deal("eden-church-mhp")
    ok = pymodel.run(dict(base))
    check("unknown-key guard: a valid input set still runs",
          ok.get("levered_irr") is not None)

    for bad_key in ("purchase_price", "totally_bogus_key_xyz"):
        try:
            pymodel.run({**base, bad_key: 1_251_000})
            check(f"unknown model input {bad_key!r} raises", False,
                  "no exception raised")
        except ValueError as exc:
            check(f"unknown model input {bad_key!r} raises",
                  bad_key in str(exc), f"message did not name the key: {exc}")

    # The correctly-spelled key must still change the answer.
    at_ask = pymodel.run(dict(base))["levered_irr"]
    at_pursue = pymodel.run({**base, "price": 1_251_000})["levered_irr"]
    check("unknown-key guard: 'price' still overrides",
          at_pursue > at_ask + 0.10,
          f"ask {at_ask:.4f} vs pursue {at_pursue:.4f}")

    # Keys that are legitimate but absent from the defaults table (supplied by
    # _load_deal / callers) must NOT trip the guard.
    for legit in ("location", "unit_mix", "price"):
        check(f"unknown-key guard: {legit!r} is accepted",
              legit in base and pymodel.run(dict(base)) is not None)


def test_insurance_gate_needs_an_artifact_not_prose():
    """Prose naming an OPEN insurance gate must not close the gate.

    2026-09-07: _insurance_noted returned True on the substring "bindable".
    Eden's history says 'GATE: bindable habitational insurance <=~1600/u
    (Apartment Guard follow-up drafted)' — the sentence that says the quote is
    outstanding — so the IC memo printed '✓ Insurance quote noted' and the bank
    package labelled the seller's carried $1,060/unit premium
    '(Louisiana-adjusted)' to a lender. One of CLAUDE.md's four offer-stage
    hard gates, reported clear while open, on the Priority 1 deal.
    """
    import json as _json
    import tempfile
    from pathlib import Path as _Path
    import icmemo

    root = _Path(__file__).resolve().parent.parent
    deals = _json.loads((root / "portfolio" / "deals.json").read_text())

    for deal in ("eden-church-mhp", "treme-gov-nicholls", "baker-trails"):
        rec = deals.get(deal, {})
        hist = " ".join(h.get("note", "") for h in rec.get("history", []))
        noted, _ev = icmemo._insurance_noted(root / "deal-intake" / deal, hist, rec)
        check(f"insurance gate: {deal} has no quote artifact, so it reads UNMET",
              noted is False, f"got {noted}")

    # The exact sentence that used to close the gate.
    with tempfile.TemporaryDirectory() as td:
        empty = _Path(td)
        open_gate = ("GATE: bindable habitational insurance <=~1600/u "
                     "(Apartment Guard follow-up drafted)")
        noted, _ = icmemo._insurance_noted(empty, open_gate, {})
        check("insurance gate: the word 'bindable' alone does not close it",
              noted is False, f"got {noted}")
        for phrase in ("insurance quote requested", "flood quote pending"):
            noted, _ = icmemo._insurance_noted(empty, phrase, {})
            check(f"insurance gate: {phrase!r} does not close it", noted is False)

        # Positive control 1: a quote document filed in the deal folder.
        (empty / "insurance_quote_apartmentguard.pdf").write_text("x")
        noted, ev = icmemo._insurance_noted(empty, "", {})
        check("insurance gate: a filed quote document closes it",
              noted is True and "insurance_quote_apartmentguard.pdf" in ev,
              f"got {noted}, {ev!r}")

    # Positive control 2: an explicit record in deals.json.
    with tempfile.TemporaryDirectory() as td:
        noted, ev = icmemo._insurance_noted(
            _Path(td), "",
            {"insurance_quote": {"carrier": "Apartment Guard",
                                 "per_unit": 1550, "date": "2026-09-01"}})
        check("insurance gate: a deals.json insurance_quote record closes it",
              noted is True and "Apartment Guard" in ev, f"got {noted}, {ev!r}")


def test_bankpackage_diligence_gates_fail_closed():
    """A failure in the gap check must not produce a clean bank package.

    2026-09-07: the check sat under `except Exception: pass` with defaults of
    rent-roll-actual / T-12-present / insurance-quoted, so any error handed the
    lender a package with every gate silently reported clear.
    """
    import bankpackage
    import icmemo

    # Break the gap check the way a real failure would, and confirm the package
    # refuses instead of sailing through with every gate reported clear.
    original = icmemo._insurance_noted

    def _boom(*a, **k):
        raise RuntimeError("simulated gap-check failure")

    icmemo._insurance_noted = _boom
    try:
        raised = False
        try:
            bankpackage.gather("eden-church-mhp", force=False)
        except SystemExit:
            raised = True          # refused, which is the fail-closed behaviour
        except Exception:
            raised = True
        check("bankpackage: a broken gap check refuses rather than passing clean",
              raised, "gather() returned normally with the check broken")
    finally:
        icmemo._insurance_noted = original


def test_intake_apply_end_to_end_preserves_the_workbook():
    """`intake.py --apply` must write the deal, and never destroy it on failure.

    2026-09-07: intake.py:310 called `defaults.resolve(args.model)` (4 args
    required) behind an `hasattr(defaults, "resolve")` guard that is always
    True, so every --apply on a roll containing a vacant unit raised TypeError.
    clone_model() had already copied the blank master over the deal workbook,
    and w.save() never ran: baker-trails' underwriting silently became the
    master's $600,000 / 4x1BR+4x2BR demo deal. Two weeks live, because no test
    exercised intake.main() — only parsers.parse_rent_roll.

    Runs against a throwaway copy of the repo; touches nothing real.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path as _Path
    import openpyxl

    root = _Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as td:
        sandbox = _Path(td) / "repo"
        shutil.copytree(root, sandbox, ignore=shutil.ignore_patterns(
            ".git", "market-data", "docs", "reference", "*.pdf"))
        wb_path = sandbox / "deal-intake" / "baker-trails" / "baker-trails_acq.xlsx"

        def mix(path):
            ws = openpyxl.load_workbook(path)["Inputs"]
            return (ws["B2"].value,
                    [(ws.cell(r, 5).value, ws.cell(r, 6).value, ws.cell(r, 8).value)
                     for r in (3, 4)])

        before = mix(wb_path)
        proc = subprocess.run(
            [sys.executable, str(sandbox / "tools" / "intake.py"),
             "--deal", "baker-trails", "--price", "750000",
             "--location", "Baker", "--apply"],
            capture_output=True, text=True, timeout=600)
        out = proc.stdout + proc.stderr

        check("intake --apply: does not raise TypeError on a roll with vacancies",
              "TypeError" not in out, out[-300:])
        check("intake --apply: writes the workbook",
              "WROTE" in out and proc.returncode == 0,
              f"rc={proc.returncode}")

        after = mix(wb_path)
        # The master demo deal is $600,000 / 4x'1 BR/ 1 BA' - never the result.
        check("intake --apply: workbook is not replaced by the master demo deal",
              after[0] == 750000 and after[1][0][1] != "1 BR/ 1 BA",
              f"before={before} after={after}")
        check("intake --apply: the OM roll's real mix lands in the workbook",
              after[1] == [(8, "2 BR/ 1 BA", 725), (4, "3 BR/ 1 BA", 925)],
              f"got {after[1]}")
        check("intake --apply: the roll's own 33.3% vacancy is surfaced",
              "33.3% physical vacancy" in out)
        check("intake --apply: no staging file is left behind",
              not list(wb_path.parent.glob("*staging*")))

        # A failure after the clone must leave the ORIGINAL workbook intact.
        good = mix(wb_path)
        proc = subprocess.run(
            [sys.executable, str(sandbox / "tools" / "intake.py"),
             "--deal", "baker-trails", "--price", "750000",
             "--set", "no_such_input_key=1", "--apply"],
            capture_output=True, text=True, timeout=600)
        check("intake --apply: a failed run leaves the previous workbook intact",
              mix(wb_path) == good and proc.returncode != 0,
              f"rc={proc.returncode}, workbook now {mix(wb_path)}")


def test_solve_price_returns_the_basis_it_certified():
    """A rung must be re-scorable on the basis that certified it.

    2026-09-07: solve_price re-derived taxes and the exit cap at every trial
    price, then returned {price, irr} alone. Consumers re-ran the rung with the
    ASKING price's tax bill and exit cap still attached, understating the
    rung's own IRR and its P(IRR>=target) by 3.0-5.5pp — on eden's 22% rung,
    which is the live pursue basis.
    """
    for deal, target in (("eden-church-mhp", 0.22), ("treme-gov-nicholls", 0.13)):
        inputs = pymodel._load_deal(deal)
        res = pymodel.solve_price(dict(inputs), target)
        check(f"solve_price: {deal} @{target:.0%} solves", res is not None)
        if not res:
            continue
        check(f"solve_price: {deal} returns the tax basis it used",
              res.get("taxes_annual") is not None
              and abs(res["taxes_annual"] - inputs["taxes_annual"]) > 1,
              f"returned {res.get('taxes_annual')}, ask basis {inputs['taxes_annual']}")
        check(f"solve_price: {deal} returns the exit cap it used",
              res.get("exit_cap") is not None)
        # Re-scored on the returned basis, the rung reproduces its own label.
        rescored = pymodel.run(dict(inputs, price=res["price"],
                                    taxes_annual=res["taxes_annual"],
                                    exit_cap=res["exit_cap"]))["levered_irr"]
        check(f"solve_price: {deal} @{target:.0%} re-scores to its own label",
              abs(rescored - target) < 0.002,
              f"re-scored {rescored:.4f} vs target {target}")


def test_commercial_share_is_a_valid_model_input():
    """The unknown-key guard must not reject a key _load_deal injects.

    2026-09-07: the guard added earlier the same day omitted commercial_share,
    which _load_deal copies out of deals.json exactly as it copies location.
    Every mixed-use deal — supported, documented, and inside the NOLA buy box —
    would have raised in run/tornado/monte_carlo while solve_price (which pops
    the key) kept working: a confusing half-failure.
    """
    inputs = pymodel._load_deal("treme-gov-nicholls")
    inputs["commercial_share"] = 0.3
    r = pymodel.run({k: v for k, v in inputs.items() if k != "location"})
    check("commercial_share is accepted by run()",
          r.get("levered_irr") is not None)
    try:
        pymodel.run({**inputs, "comercial_share": 0.3})
        check("a misspelled commercial_share is still rejected", False, "no raise")
    except ValueError:
        check("a misspelled commercial_share is still rejected", True)


def test_icmemo_tornado_keeps_every_downside_row():
    """The vacancy stress must survive into the memo the house rule cites."""
    import icmemo
    inputs = pymodel._load_deal("treme-gov-nicholls")
    rows = pymodel.tornado(inputs)
    downside = [r for r in rows if (r["delta_irr"] or 0) < 0]
    check("tornado: treme has a vacancy downside row",
          any("vacancy" in r["factor"].lower() for r in downside),
          f"factors: {[r['factor'] for r in downside]}")
    check("tornado: more downside rows exist than the old top-3 slice showed",
          len(downside) > 3, f"{len(downside)} downside rows")
    memo = "\n".join(icmemo.build_memo_lines("treme-gov-nicholls")) \
        if hasattr(icmemo, "build_memo_lines") else None
    if memo is not None:
        check("icmemo: the vacancy stress row reaches the memo",
              "vacancy" in memo.lower())


def test_hedonic_market_comes_from_the_parish():
    """The comp market must follow the deal's parish, not its history prose."""
    import hedonic
    cases = [("new orleans", "new orleans"), ("gretna", "new orleans"),
             ("chalmette", "new orleans"), ("covington", "northshore"),
             ("hammond", "northshore"), ("baker", "baton rouge"),
             ("denham springs", "baton rouge"), ("gonzales", "baton rouge"),
             ("lafayette", "lafayette")]
    for loc, want in cases:
        got, how = hedonic.market_for_location(loc)
        check(f"hedonic: {loc} -> {want}", got == want, f"got {got} ({how})")
    got, why = hedonic.market_for_location("Nowheresville")
    check("hedonic: an unresolvable location fails rather than defaulting to "
          "baton rouge", got is None, f"got {got}")
    got, why = hedonic.market_for_location(None)
    check("hedonic: a missing location fails loudly", got is None)


def test_hedonic_has_no_year_regressor():
    """The year term measured market mix, not appreciation — and was applied.

    2026-09-07: fit() printed "Do NOT read it as a time trend" and predict()
    then multiplied every estimate by exp(-0.0881 * (year-2021)) = 0.644 at
    2026. Baton Rouge read $41,664/unit instead of $59,100; a 12-unit Baker
    priced at $499,969 instead of $709,198, so the market approach appeared to
    corroborate a sub-$500K ladder on the strength of an acknowledged artifact.
    """
    import hedonic
    fr = hedonic.fit(verbose=False)
    check("hedonic: design matrix has no year column", fr["k"] == 5, f"k={fr['k']}")
    a = hedonic.predict("baton rouge", 12, year=2021, fit_result=fr, loud=False)
    b = hedonic.predict("baton rouge", 12, year=2026, fit_result=fr, loud=False)
    check("hedonic: the year argument no longer moves the estimate",
          a["point_per_unit"] == b["point_per_unit"],
          f"{a['point_per_unit']} vs {b['point_per_unit']}")
    check("hedonic: baton rouge is back above the artifact-suppressed level",
          b["point_per_unit"] > 55_000, f"got {b['point_per_unit']}")
    ns = hedonic.predict("northshore", 18, fit_result=fr, loud=False)
    check("hedonic: a one-sale market coefficient is flagged as thin support",
          any("THIN SUPPORT" in c for c in ns["caveats"]), f"{ns['caveats']}")
    nola = hedonic.predict("new orleans", 8, fit_result=fr, loud=False)
    check("hedonic: a well-supported market is not flagged thin",
          not any("THIN SUPPORT" in c for c in nola["caveats"]))


def test_flood_cli_does_not_print_no_for_undetermined():
    """`SFHA: no` is the line that gets copied into memos."""
    import io
    import contextlib
    import flood
    for zone, sfha, want in (("X", False, "no"), ("AE", True, "YES"),
                             ("UNMAPPED", None, "UNDETERMINED"),
                             ("D", None, "UNDETERMINED")):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            flood._print_result({"address": "a", "zone": zone, "sfha": sfha,
                                 "note": "n"}, as_json=False)
        line = next(l for l in buf.getvalue().splitlines() if l.startswith("SFHA"))
        check(f"flood CLI: {zone} (sfha={sfha}) prints {want}",
              want in line, f"printed {line!r}")


def test_latax_rejects_a_percentage_typed_as_a_percent():
    """`--commercial-share 30` meaning 30% used to return a 16x tax bill."""
    import latax
    ok, _ = latax.estimate_tax(1_500_000, "Gonzales", 0.3)
    check("latax: a valid fraction still works", ok is not None and ok > 0)
    for bad in (30, -0.5, 1.5):
        try:
            latax.estimate_tax(1_500_000, "Gonzales", bad)
            check(f"latax: commercial_share={bad} is rejected", False, "no raise")
        except ValueError:
            check(f"latax: commercial_share={bad} is rejected", True)


def test_market_parish_coverage_matches_latax():
    """market.py's docstring claimed parity with latax; it was short two."""
    import latax
    import market
    missing = sorted(set(latax.MILLAGE) - set(market.PARISHES))
    check("market.py covers every latax parish", not missing, f"missing {missing}")
    check("market.py covers st. bernard (in the buy box)",
          "st. bernard" in market.PARISHES)


def test_irr_verdict_applies_the_pursue_floor():
    """12.5% is inside the realistic band and below the house floor."""
    import report
    check("irr_verdict: 12.5% is called out as below the pursue floor",
          "BELOW PURSUE FLOOR" in report.irr_verdict(0.125),
          report.irr_verdict(0.125))
    check("irr_verdict: 12.9% likewise",
          "BELOW PURSUE FLOOR" in report.irr_verdict(0.129))
    check("irr_verdict: 13.0% clears",
          "BELOW PURSUE FLOOR" not in report.irr_verdict(0.130),
          report.irr_verdict(0.130))


def test_portfolio_sim_charges_idle_capital_once():
    """Year 0 debited the whole cheque; later buys debited their equity again.

    2026-09-07: baker-then-fourplex printed IRR -1.1% / -$23,995 profit beside
    "Cash returned $437,208 (1.46x)" because $143,747 of idle year-0 cash was
    charged twice. On a capital-called convention the same sequence is +9.0%.
    """
    import portfolio_sim
    res = portfolio_sim.simulate(
        portfolio_sim._load_scenario("baker-then-fourplex"))
    cf = res["portfolio_cf"]
    check("portfolio_sim: a profitable sequence does not print as a loss",
          res["total_profit"] > 0, f"profit {res['total_profit']:,.0f}")
    check("portfolio_sim: portfolio IRR is positive on this scenario",
          (res["portfolio_irr"] or 0) > 0, f"IRR {res['portfolio_irr']}")
    check("portfolio_sim: year 0 charges deployed capital, not the whole cheque",
          abs(cf[0]) < res["starting_equity"] - 1,
          f"cf[0]={cf[0]:,.0f} vs cheque {res['starting_equity']:,.0f}")
    check("portfolio_sim: sum of flows equals reported profit",
          abs(sum(cf) - res["total_profit"]) < 1.0)


def test_portfolio_status_excludes_non_operating_credits():
    """A loan draw is not rent."""
    import portfolio
    acts = [
        {"date": "2027-01-05", "desc": "Rent", "amount": 1200, "cat": "rent"},
        {"date": "2027-01-06", "desc": "Construction loan draw",
         "amount": 25000, "cat": "debt"},
        {"date": "2027-01-15", "desc": "Repairs", "amount": -180, "cat": "repairs"},
    ]
    import io
    import contextlib
    import json as _json
    import shutil
    import tempfile
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    original = portfolio.DEALS
    with tempfile.TemporaryDirectory() as td:
        fake = _Path(td) / "deals.json"
        rec = {"testdeal": {"stage": "owned", "created": "2027-01-01",
                            "history": [], "actuals": acts, "payback": [],
                            "plan": {"monthly_noi_target": 11956}}}
        fake.write_text(_json.dumps(rec))
        portfolio.DEALS = fake
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                portfolio.cmd_status("testdeal")
            out = buf.getvalue()
        finally:
            portfolio.DEALS = original
    check("portfolio status: a $25,000 loan draw is not booked as collections",
          "$29,020" not in out and "$1,200" in out, out)
    check("portfolio status: the month reads NO against a $11,956 target",
          "NO" in out, out)
    check("portfolio status: the excluded credit is reported, not hidden",
          "25,000" in out, out)


def test_every_third_party_import_is_declared():
    """Advertised features must not depend on undeclared packages.

    2026-09-21: tools/parsers.py imported `fitz` (PyMuPDF) for the PDF path that
    README and CLAUDE.md both advertise, while requirements.txt declared only
    openpyxl. Every PDF in deal-intake/ raised ModuleNotFoundError on a fresh
    container. This test walks the real import graph so the next undeclared
    dependency fails here instead of on a live deal package.
    """
    import ast
    import pathlib

    tools = pathlib.Path(__file__).parent
    stdlib = set(sys.stdlib_module_names)
    local = {p.stem for p in tools.glob("*.py")}
    declared = set()
    for line in (tools.parent / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            declared.add(re.split(r"[=<>!~\[]", line)[0].strip().lower())
    # distribution name -> module name, where they differ
    aliases = {"pymupdf": "fitz"}
    declared |= {aliases[d] for d in list(declared) if d in aliases}

    undeclared = {}
    for p in sorted(tools.glob("*.py")):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            else:
                continue
            for m in mods:
                top = m.split(".")[0]
                if top in stdlib or top in local or top.lower() in declared:
                    continue
                undeclared.setdefault(top, set()).add(p.name)

    check("requirements.txt declares every third-party import in tools/",
          not undeclared,
          "; ".join(f"{m} <- {', '.join(sorted(f))}" for m, f in sorted(undeclared.items())))

    # The specific one that shipped broken.
    check("PyMuPDF (the PDF ingestion dependency) is declared",
          "pymupdf" in declared,
          f"declared={sorted(declared)}")


def test_recalc_detects_a_missing_calc_filter():
    """A soffice binary without the Calc filter must fail fast, not at convert time.

    2026-09-21: this container ships libreoffice-core but not libreoffice-calc.
    `soffice` was on PATH so find_soffice() reported recalc available, then every
    conversion died with the opaque 'source file could not be loaded' - after a
    full intake run had already written its inputs. Reproduced on a trivial
    two-cell workbook, so it was the filter and not the model workbooks.
    """
    import tempfile
    import pathlib

    import recalc

    with tempfile.TemporaryDirectory() as td:
        # A program dir holding only the binary: the broken shape.
        bare = pathlib.Path(td) / "bare"
        bare.mkdir()
        (bare / "soffice").write_text("#!/bin/sh\n")
        check("recalc: soffice without the Calc filter is detected",
              recalc.calc_filter_present(bare / "soffice") is False)

        # The same dir once the Calc filter library is present.
        (bare / "libscfiltlo.so").write_text("")
        check("recalc: the Calc filter next to the binary is accepted",
              recalc.calc_filter_present(bare / "soffice") is True)

        # Fail open: an unrecognised layout must not block a working install.
        check("recalc: an unreadable install layout does not block recalc",
              recalc.calc_filter_present(pathlib.Path(td) / "nope" / "soffice") is True)

    check("recalc: the install hint is not macOS-only",
          "apt-get" in recalc.INSTALL_HINT and "brew" in recalc.INSTALL_HINT,
          recalc.INSTALL_HINT)


def test_plural_totals_row_is_not_a_unit_type():
    """A 'Totals' subtotal row must not be parsed as a unit.

    2026-09-21: TOTAL_ROW_RE ended in \\b after a bare 'total', so 'Total'
    matched but 'Totals' did not. The row survived because its summed SF (3,000)
    and summed rent both sit inside SF_MAX/RENT_MAX, so it looked like one large
    unit. On a 4-unit fixture it added a phantom 5th unit at $3,450/mo:
    NOI $15,422 -> $46,794, IRR -1.23% -> +22.27%. A hard PASS reads as "ideal".
    """
    import parsers

    for label in ("Total", "Totals", "TOTALS", "Subtotal", "Subtotals",
                  "Sub-Totals", "Average", "Averages", "Grand Total"):
        check(f"rent roll: '{label}' row is suppressed",
              bool(parsers.TOTAL_ROW_RE.match(label)))


def test_rent_column_is_chosen_by_preference_not_position():
    """In-place rent must win over market rent whatever the column order.

    2026-09-21: _find_header scanned columns left-to-right and took the first
    rent-ish header, so the _HDR_RENT preference tuple was dead code - and that
    tuple listed 'market rent' FIRST, so honouring it as written would have been
    worse. A Yardi-ordered roll (Market before Current) underwrote baker-trails
    at asking rents: 2BR $725 -> $838, NOI +24.1%, the 13% clearing price
    +$104,111, and - worst - the four $0 vacant rows picked up a market rent, so
    both the zero-rent note and the 33.3% implied-vacancy warning vanished.
    """
    import parsers

    hdr = ["Unit", "Unit Type", "SqFt", "Market Rent", "Current Rent"]
    body = ["101", "2BR/1BA", "750", "838", "725"]
    _, idx = parsers._find_header([hdr, body])
    check("rent roll: Current Rent wins even when Market Rent is left of it",
          idx.get("rent_label", "").lower() == "current rent",
          f"chose {idx.get('rent_label')!r}")

    # The same roll in the other order must resolve identically.
    hdr2 = ["Unit", "Unit Type", "SqFt", "Current Rent", "Market Rent"]
    body2 = ["101", "2BR/1BA", "750", "725", "838"]
    _, idx2 = parsers._find_header([hdr2, body2])
    check("rent roll: column order does not change which rent is underwritten",
          idx2.get("rent_label", "").lower() == "current rent",
          f"chose {idx2.get('rent_label')!r}")

    check("rent roll: the rent column that lost is still reported",
          idx.get("rent_alternates") == ["Market Rent"],
          f"alternates={idx.get('rent_alternates')}")

    # Preference ranking itself: actual/current beat market/asking.
    check("rent roll: 'actual rent' outranks 'market rent'",
          parsers._rent_rank("actual rent") < parsers._rent_rank("market rent"))
    check("rent roll: 'current rent' outranks the bare 'rent'",
          parsers._rent_rank("current rent") < parsers._rent_rank("rent"))


def test_parceltax_validates_commercial_share():
    """The pre-offer gate tool must reject a percentage typed as a fraction.

    2026-09-21: the 2026-09-07 guard landed only in latax.estimate_tax.
    parceltax.py imported the two ratio constants and did its own arithmetic, so
    it never reached the guard: --commercial-share 30 printed a $276,000/yr bill
    on a $1.5M Ascension building (16x the $17,250 truth), and -0.5 printed
    $12,938 - 25% BELOW the residential floor, with no warning. parceltax is run
    immediately before an offer, against a CLAUDE.md hard gate.
    """
    import latax
    import parceltax

    check("parceltax imports the shared guard",
          parceltax.validate_commercial_share is latax.validate_commercial_share)

    for bad in (30, -0.5, 1.5):
        try:
            latax.validate_commercial_share(bad)
            check(f"commercial_share {bad} is rejected", False, "accepted")
        except ValueError:
            check(f"commercial_share {bad} is rejected", True)

    for good in (0, 0.3, 1):
        try:
            latax.validate_commercial_share(good)
            check(f"commercial_share {good} is accepted", True)
        except ValueError as e:
            check(f"commercial_share {good} is accepted", False, str(e))

    # The guard must not have changed the arithmetic it protects.
    tax, _ = latax.estimate_tax(1_500_000, "ascension", 0.3)
    check("latax: 30% commercial on $1.5M Ascension is $19,838/yr",
          abs(tax - 19_837.5) < 1.0, f"got {tax:,.2f}")


def test_mc_honours_the_deals_own_exit_cap():
    """MC must disperse around the underwritten exit cap, not invent its own.

    2026-09-21: monte_carlo overwrote exit_cap with going_in + cap_delta on every
    draw and never read inputs["exit_cap"], so a deal underwritten at any other
    cap had its MC - and the house-rule beats-index scored off it - evaluated at
    a cap the underwriting rejected. mlk-2119 (workbook 7.50% vs 5.03% going-in)
    drew 100% of its caps BELOW its own: MC P50 -1.93% against a deterministic
    -9.79%, i.e. the MC centre sat 7.9 points ABOVE the underwriting. Same
    principle as the rent/expense growth recentering: fitted SPREAD, deal's CENTRE.
    """
    inputs = {k: v for k, v in pymodel._load_deal("mlk-2119").items()
              if k != "location"}
    det = pymodel.run(inputs)["levered_irr"]
    mc = pymodel.monte_carlo(dict(inputs), n=400, seed=42, deal_name="mlk-2119")

    check("MC: an explicit exit cap is reported as the MC centre",
          "exit cap centered on underwriting" in (mc.get("growth_note") or ""),
          f"note={mc.get('growth_note')!r}")
    check("MC: P50 is not above a deeply negative deterministic IRR",
          mc["p50"] < det,
          f"MC P50 {mc['p50']:.2%} vs deterministic {det:.2%}")

    # A deal whose exit cap already equals going-in + 50bps must be unchanged.
    eden = {k: v for k, v in pymodel._load_deal("eden-church-mhp").items()
            if k != "location"}
    gi = pymodel.run(eden)["going_in_cap"]
    check("MC: the live deals still carry exit_cap == going-in + 50bps",
          abs(eden["exit_cap"] - round(gi + 0.005, 5)) < 1e-9,
          f"exit_cap={eden['exit_cap']} going_in+50bp={round(gi + 0.005, 5)}")


def test_portfolio_sim_credits_sales_before_it_buys():
    """The capital-gap verdict must see this year's proceeds and this year's buys.

    2026-09-21: running_bal was updated once per year, AFTER both the
    acquisition loop and the distribution loop. Two opposite failures:
      A) two deals in one year - the second was tested against cash the first
         had already spent, so a real $17,802 shortfall raised NO flag and the
         panel printed a negative year-0 balance;
      B) buying in the year another deal sells - the test ran before the sale
         was credited, inventing a $98,929 capital call against $175,953 of
         proceeds received that same year, and the balance was then overstated
         by exactly that phantom.
    CLAUDE.md makes equity the binding constraint, so this is the verdict the
    tool exists to give.
    """
    import copy
    import json
    import pathlib

    import portfolio_sim

    base = json.loads(
        (pathlib.Path(__file__).parent.parent
         / "portfolio" / "scenarios" / "baker-then-fourplex.json").read_text())

    # A: both deals in year 0. Equity needed 138,472 + 179,330 = 317,802.
    a = copy.deepcopy(base)
    a["deals"][1]["year"] = 0
    ra = portfolio_sim.simulate(a)
    check("portfolio_sim: a same-year double purchase raises a capital gap",
          any("Gonzales" in f for f in ra["gap_flags"]),
          f"gap_flags={ra['gap_flags']}")
    check("portfolio_sim: no year prints a negative running balance",
          all(row["running_balance"] >= -0.01 for row in ra["annual_table"]),
          f"balances={[round(r['running_balance']) for r in ra['annual_table'][:3]]}")

    # B: buy in the year the first deal exits, funded by its proceeds.
    b = copy.deepcopy(base)
    b["starting_equity"] = 145_000
    b["deals"][1]["year"] = 5
    rb = portfolio_sim.simulate(b)
    check("portfolio_sim: proceeds received this year fund this year's purchase",
          not rb["gap_flags"],
          f"invented gap: {rb['gap_flags']}")

    yr5 = next(r for r in rb["annual_table"] if r["year"] == 5)
    yr4 = next(r for r in rb["annual_table"] if r["year"] == 4)
    expected = yr4["running_balance"] + yr5["cash_in"] - yr5["cash_out"]
    check("portfolio_sim: the year-5 balance is prior + in - out",
          abs(yr5["running_balance"] - expected) < 1.0,
          f"printed {yr5['running_balance']:,.0f} vs {expected:,.0f}")

    # The ledger must close every year, on the shipped scenario.
    rs = portfolio_sim.simulate(copy.deepcopy(base))
    bal = rs["starting_equity"]
    for row in rs["annual_table"]:
        bal = bal + row["cash_in"] - row["cash_out"]
        if abs(bal - row["running_balance"]) > 1.0:
            check(f"portfolio_sim: balance ties in year {row['year']}", False,
                  f"{row['running_balance']:,.0f} vs {bal:,.0f}")
            break
    else:
        check("portfolio_sim: the running balance ties every year", True)


def test_intake_gates_refuse_a_misleading_write():
    """Four --apply paths that used to produce a confident wrong answer."""
    import pathlib, subprocess, sys as _sys
    tools = pathlib.Path(__file__).resolve().parent
    src = (tools / "intake.py").read_text()
    # --apply with no --price wrote the deal onto the MASTER'S DEMO PRICE.
    check("intake: --apply without --price is refused",
          "--apply needs --price" in src)
    # --units-override beat a rent roll that had already parsed.
    check("intake: --units-override contradicting a parsed roll is refused",
          "contradicts the rent" in src)
    # A part-year statement was flagged and then written as annual anyway.
    check("intake: a part-year statement blocks --apply unless accepted",
          "--accept-part-year" in src and "PART-YEAR STATEMENT" in src)
    # A parsed T-12 was printed and then discarded when units were unknown.
    check("intake: a T-12 with an unknown unit count is refused, not discarded",
          "unit count is unknown" in src)
    # And the gates must be real exits, not printed notes (2026-09-28: notes
    # print screens BELOW the verdict and nothing ever exits non-zero on one).
    check("intake: those gates raise SystemExit rather than appending a note",
          src.count("raise SystemExit") >= 4,
          f"only {src.count('raise SystemExit')} SystemExit gates")


def test_board_says_the_residual_levee_risk_out_loud():
    """board.py hand-typed 'Zone X levee-protected - no flood policy required'."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent / "board.py").read_text()
    check("board: the 'no flood policy required' phrasing is gone",
          "no flood policy required" not in src)
    check("board: the residual-risk half of flood.py's sentence is present",
          "residual risk is real" in src)

    # Sweep 2026-10-05: the two checks above are a grep over board.py's
    # hand-typed HTML strings -- they never call flood.py, so deleting its
    # whole levee branch left them passing, and test_flood_degrades_to_unknown_
    # not_clear covers [] / D / X / AE with no LEVEE subtype at all. CLAUDE.md
    # requires the residual-risk sentence for levee-protected Orleans, so
    # assert it where it is generated.
    import flood
    zone, sfha, note = flood.interpret([{
        "FLD_ZONE": "X",
        "ZONE_SUBTY": "AREA WITH REDUCED FLOOD RISK DUE TO LEVEE",
        "SFHA_TF": "F"}])
    check("flood: a levee-protected X zone is reported as levee-driven",
          "LEVEE" in zone.upper(), zone)
    check("flood: it is not in the SFHA", sfha is False, str(sfha))
    check("flood: it says the residual risk out loud (CLAUDE.md)",
          "residual risk is real" in note, note)
    check("flood: it does not call a levee zone minimal hazard",
          "minimal flood hazard" not in note.lower(), note)

    # A plain X must NOT pick up the levee sentence, or the branch is dead.
    _z, _s, plain = flood.interpret([{"FLD_ZONE": "X", "ZONE_SUBTY": "", "SFHA_TF": "F"}])
    check("flood: a plain X zone is distinguished from a levee X",
          "residual risk is real" not in plain, plain)


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
    print("REGRESSION TESTS (2026-09-07 sweep)")
    test_unknown_model_input_is_refused()
    test_insurance_gate_needs_an_artifact_not_prose()
    test_bankpackage_diligence_gates_fail_closed()
    test_intake_apply_end_to_end_preserves_the_workbook()
    test_solve_price_returns_the_basis_it_certified()
    test_commercial_share_is_a_valid_model_input()
    test_hedonic_market_comes_from_the_parish()
    test_hedonic_has_no_year_regressor()
    test_flood_cli_does_not_print_no_for_undetermined()
    test_latax_rejects_a_percentage_typed_as_a_percent()
    test_market_parish_coverage_matches_latax()
    test_irr_verdict_applies_the_pursue_floor()
    test_icmemo_tornado_keeps_every_downside_row()
    test_portfolio_sim_charges_idle_capital_once()
    test_portfolio_status_excludes_non_operating_credits()
    print("REGRESSION TESTS (2026-09-21 sweep)")
    test_every_third_party_import_is_declared()
    test_recalc_detects_a_missing_calc_filter()
    test_plural_totals_row_is_not_a_unit_type()
    test_rent_column_is_chosen_by_preference_not_position()
    test_parceltax_validates_commercial_share()
    test_mc_honours_the_deals_own_exit_cap()
    test_portfolio_sim_credits_sales_before_it_buys()
    print("REGRESSION TESTS (2026-09-28 sweep)")
    test_to_num_reads_accounting_negatives()
    test_thirteenth_column_is_not_the_annual_figure()
    test_two_year_t12_takes_the_latest_year()
    test_part_year_statement_is_still_detected()
    test_credit_lines_offset_rather_than_add()
    test_matched_expense_with_no_parseable_amount_is_reported()
    test_totals_row_is_caught_anywhere_in_the_label()
    test_garbage_and_lawn_map_to_an_expense_line()
    test_duplicate_expense_labels_keep_the_larger()
    test_tornado_never_drops_a_stress()
    test_negative_forward_noi_does_not_credit_the_seller()
    test_zero_exit_cap_is_refused()
    test_negative_refi_noi_is_refused()
    test_non_positive_equity_is_refused()
    test_wind_parishes_carry_the_higher_insurance_default()
    test_vintage_hazards_are_shared_with_the_engine()
    test_savings_are_capital_not_portfolio_return()
    test_price_override_reassesses_taxes_from_the_records_location()
    test_price_override_does_not_guess_the_parish_from_the_deal_name()
    test_cash_returned_is_what_was_actually_received()
    test_hedonic_fit_cache_is_not_poisoned_by_a_holdout()
    test_short_bls_series_does_not_print_a_zero_trend()
    test_hot_cap_floor_actually_binds()
    test_irr_verdict_agrees_with_the_number_it_prints()
    test_bankpackage_reports_sponsor_cash_not_total_equity()
    test_icmemo_prefers_the_seller_document_over_an_estimate()
    test_intake_gates_refuse_a_misleading_write()
    test_board_says_the_residual_levee_risk_out_loud()
    print("REGRESSION TESTS (2026-10-05 sweep)")
    test_parity_harness_cannot_pass_on_zero_comparisons()
    test_board_escapes_a_note_and_still_renders()
    test_writer_refuses_an_incoming_formula()
    test_apply_refuses_when_no_rent_roll_parsed()
    test_chol_factor_reproduces_the_declared_correlations()
    test_bankpackage_sees_the_documents_that_are_on_disk()
    test_wind_basis_agrees_with_the_parish_table()
    test_generators_refuse_to_overwrite_a_master()
    print("REGRESSION TESTS (2026-10-12 sweep)")
    test_every_covered_deal_is_actually_verified_not_unverified()
    test_board_mlk_copy_ties_to_the_engine_on_the_carried_basis()
    test_no_debt_service_after_the_loan_amortizes_away()
    test_parity_cannot_pass_with_every_return_metric_blank()
    test_geography_resolves_under_every_input_form()
    test_four_jefferson_towns_are_back_on_the_wind_basis()
    test_a_parish_we_do_not_carry_never_becomes_one_we_do()
    test_town_before_street_resolves_to_the_town()
    test_every_geography_consumer_uses_the_same_resolver()
    test_intake_never_reads_its_own_workbook_as_a_source()
    test_writer_refuses_an_excel_error_token()
    test_an_orleans_neighbourhood_is_not_read_as_a_baton_rouge_town()
    test_board_does_not_assert_a_verdict_inside_the_coin_flip_band()
    test_rates_persists_a_baseline_and_diffs_against_it()
    test_a_renovation_with_no_pace_does_not_pass_silently()
    test_a_town_inside_a_street_name_is_never_the_locality()
    test_the_board_number_and_its_verdict_agree_at_every_boundary()
    test_the_dscr_verdict_matches_the_dscr_it_prints()
    test_rates_drift_survives_a_second_run_and_a_poisoned_row()
    test_the_board_shows_his_own_return_not_only_the_deals()
    test_the_engine_refuses_inputs_that_invent_money()
    test_ingestion_does_not_drop_rows_or_zeros_in_silence()
    if FAILURES:
        print(f"\nRESULT: {len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\nRESULT: all regression tests passed")




# ===========================================================================
# 2026-09-28 sweep
# ===========================================================================

def _code_only(path):
    """Source with comment lines and docstring-ish text stripped.

    A source-text assertion that does not do this matches the very comment that
    documents the fix (all three of these tripped on their own explanation the
    first time they ran, 2026-09-28).
    """
    out = []
    for line in path.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def test_to_num_reads_accounting_negatives():
    """Accounting-export negatives must parse, and a split cell must not."""
    import parsers
    cases = [("$(48,000)", -48000.0), ("(48,000)", -48000.0),
             ("−18,120", -18120.0), ("12,360-", -12360.0),
             ("48,000", 48000.0), ("-500", -500.0)]
    for text, want in cases:
        got = parsers.to_num(text)
        check(f"to_num({text!r}) == {want}", got == want, f"got {got}")
    # An unbalanced paren is a truncated/split cell. The old regex accepted it
    # and returned +1200 -- a silent SIGN FLIP.
    check("to_num: an unbalanced paren is refused, not read as positive",
          parsers.to_num("(1,200") is None, f"got {parsers.to_num('(1,200')}")


def test_thirteenth_column_is_not_the_annual_figure():
    """12 monthly columns + a per-unit 13th must sum the 12, not read the 13th."""
    import parsers
    rows = [["Line"] + [f"M{i}" for i in range(1, 13)] + ["Per Unit/Yr"],
            ["Payroll"] + [3430] * 12 + [1715],
            ["Insurance"] + [4000] * 12 + [2000]]
    lines, _, _, _ = parsers._t12_from_rows(rows)
    check("t12: payroll is the 12-month sum, not the per-unit 13th column",
          abs(lines["payroll"]["annual"] - 41160) < 1,
          f"got {lines['payroll']['annual']}")
    check("t12: insurance likewise",
          abs(lines["insurance"]["annual"] - 48000) < 1,
          f"got {lines['insurance']['annual']}")
    check("t12: the basis says the trailing column was ignored",
          "ignored 1 trailing column" in lines["payroll"]["basis"],
          lines["payroll"]["basis"])


def test_two_year_t12_takes_the_latest_year():
    """'Total 2024 | Total 2025' must parse 2025 and name the column."""
    import parsers
    rows = [["Line", "Total 2024", "Total 2025"],
            ["Payroll", 28000, 41160],
            ["Insurance", 21000, 48000]]
    lines, _, notes, _ = parsers._t12_from_rows(rows)
    check("t12: the later year wins",
          abs(lines["payroll"]["annual"] - 41160) < 1,
          f"got {lines['payroll']['annual']}")
    check("t12: the chosen total column is named in the basis",
          "2025" in lines["payroll"]["basis"], lines["payroll"]["basis"])
    # The period counter must not treat the OTHER total column as a month.
    check("t12: a two-year statement is not flagged as a 1-month partial year",
          not any("PART-YEAR" in n for n in notes), str(notes))


def test_part_year_statement_is_still_detected():
    """The two-year fix must not blind the genuine part-year detector."""
    import parsers
    rows = [["Line"] + [f"M{i}" for i in range(1, 13)] + ["Total"],
            ["Payroll"] + [3430] * 6 + [0] * 6 + [20580],
            ["Insurance"] + [4000] * 6 + [0] * 6 + [24000]]
    _, _, notes, _ = parsers._t12_from_rows(rows)
    check("t12: a 6-of-12-month statement is still flagged PART-YEAR",
          any("PART-YEAR" in n for n in notes), str(notes))


def test_credit_lines_offset_rather_than_add():
    """A rebate/reimbursement reduces the line it names."""
    import parsers
    rows = [["Line", "Total"],
            ["Insurance", 48000], ["Insurance Rebate", "(6,000)"],
            ["Utilities", 18120], ["Utility Reimbursement", -4800]]
    lines, _, _, _ = parsers._t12_from_rows(rows)
    check("t12: insurance net of its rebate is 42,000 (was 54,000)",
          abs(lines["insurance"]["annual"] - 42000) < 1,
          f"got {lines['insurance']['annual']}")
    check("t12: utilities net of reimbursement is 13,320 (was 22,920)",
          abs(lines["utilities"]["annual"] - 13320) < 1,
          f"got {lines['utilities']['annual']}")
    # "Credit Card Fees" is a G&A bank charge, not a credit.
    check("t12: 'Credit Card Fees' is not treated as a credit",
          not parsers._CREDIT_RE.search("credit card fees"))


def test_matched_expense_with_no_parseable_amount_is_reported():
    """A matched category whose amount will not parse must not vanish."""
    import parsers
    rows = [["Line", "Total"], ["Insurance", "see schedule B"], ["Payroll", 41160]]
    lines, _, notes, _ = parsers._t12_from_rows(rows)
    check("t12: the unparseable insurance line is not silently written",
          "insurance" not in lines, str(list(lines)))
    check("t12: and it is reported in the notes",
          any("Insurance" in n and "no amount" in n for n in notes), str(notes))


def test_totals_row_is_caught_anywhere_in_the_label():
    """'Building Totals' is a totals row, not a unit type."""
    import parsers
    for lbl in ("Building Totals", "Property Total", "Portfolio Total",
                "Summary", "All Units", "Totals", "Total"):
        check(f"rent roll: {lbl!r} is a totals row", parsers.is_total_row(lbl))
    check("rent roll: a real unit type is not a totals row",
          not parsers.is_total_row("2 BR/ 2 BA"))


def test_garbage_and_lawn_map_to_an_expense_line():
    """Live gap: eden and hwy42 both carry 'Lawn Care'; neither mapped."""
    import parsers
    want = {"Lawn Care": "contract_services", "Mowing": "contract_services",
            "Garbage": "utilities", "Refuse Collection": "utilities",
            "Sanitation": "utilities"}
    for label, expect in want.items():
        low = label.lower()
        got = next((k for k, kws in parsers._T12_MAP
                    if any(kw in low for kw in kws)), None)
        check(f"t12: {label!r} maps to {expect}", got == expect, f"got {got}")


def test_duplicate_expense_labels_keep_the_larger():
    """The later block on a broker statement is the seller's pro forma."""
    import parsers
    rows = [["Line", "Total"], ["Electric", 6176], ["Electric", 1200]]
    lines, _, notes, _ = parsers._t12_from_rows(rows)
    check("t12: the larger duplicate survives (actual, not the adjusted pro forma)",
          abs(lines["utilities"]["annual"] - 6176) < 1,
          f"got {lines['utilities']['annual']}")
    check("t12: and both figures are named",
          any("kept larger" in n for n in notes), str(notes))


def test_tornado_never_drops_a_stress():
    """An unsolvable stress is a wipeout row, not an absent one."""
    inputs = dict(price=569_000, unit_mix=[{"units": 23, "sf": 750, "rent": 925}],
                  taxes_annual=32_200, vacancy=0.07, insurance=3000, mgmt_pct=0.10,
                  expense_growth=0.025, rent_growth=0.0, ltv=0.75, min_dscr=1.20,
                  rate=0.0675, hold_years=3, exit_cap=0.07)
    rows = pymodel.tornado(inputs)
    labels = [r["factor"] for r in rows]
    check("tornado: the insurance DOWNSIDE row is present (it used to vanish)",
          "insurance +50%" in labels, str(labels))
    check("tornado: both insurance directions are shown",
          "insurance -50%" in labels and "insurance +50%" in labels)
    wipeouts = [r for r in rows if r["delta_irr"] is None]
    check("tornado: the unsolvable stress is reported as a wipeout",
          len(wipeouts) == 1 and wipeouts[0]["factor"] == "insurance +50%",
          str([(r["factor"], r["delta_irr"]) for r in rows]))
    check("tornado: a wipeout sorts FIRST, so it cannot be cut by a top-N slice",
          rows[0]["delta_irr"] is None, rows[0]["factor"])
    check("tornado: the wipeout carries an explanatory note",
          bool(wipeouts[0].get("note")))


def test_negative_forward_noi_does_not_credit_the_seller():
    """A negative forward NOI must not yield a negative cost of sale."""
    base = pymodel._load_deal("baker-trails")
    r = pymodel.run(dict(base, insurance=6000))
    check("exit: sale price is floored at zero, not negative",
          r["sale_price"] >= 0.0, f"got {r['sale_price']}")
    check("exit: cost of sale is not a brokerage CREDIT",
          r["cost_of_sale_amt"] >= 0.0, f"got {r['cost_of_sale_amt']}")


def test_zero_exit_cap_is_refused():
    """exit_cap=0 used to silently drop the entire sale."""
    for bad in (0, 0.0, -0.01):
        try:
            pymodel.run(dict(price=1_000_000,
                             unit_mix=[{"units": 12, "sf": 800, "rent": 950}],
                             taxes_annual=10_000, exit_cap=bad))
            check(f"exit_cap={bad} is refused", False, "no error raised")
        except ValueError:
            check(f"exit_cap={bad} is refused", True)


def test_negative_refi_noi_is_refused():
    """A negative refi-year NOI produced a negative loan and a phantom cash call."""
    try:
        pymodel.run(dict(price=1_200_000,
                         unit_mix=[{"units": 20, "sf": 700, "rent": 560}],
                         taxes_annual=14_000, rent_growth=0.0, expense_growth=0.09,
                         insurance=3000, hold_years=10, refi_year=9,
                         refi_valuation_cap=0.07, exit_cap=0.07))
        check("refi: a negative refi-year NOI is refused", False, "no error raised")
    except ValueError as e:
        check("refi: a negative refi-year NOI is refused", "refi-year NOI" in str(e), str(e))


def test_non_positive_equity_is_refused():
    """Negative equity returned a borrowing root as an investment IRR."""
    try:
        pymodel.run(dict(price=1_000_000,
                         unit_mix=[{"units": 12, "sf": 800, "rent": 950}],
                         taxes_annual=10_000, ltv=1.05, min_dscr=0.5))
        check("equity: a non-positive equity requirement is refused", False,
              "no error raised")
    except ValueError as e:
        check("equity: a non-positive equity requirement is refused",
              "total equity" in str(e), str(e))


def test_wind_parishes_carry_the_higher_insurance_default():
    """CLAUDE.md 2026-08-17: the metro/inland gap IS wind insurance."""
    import defaults
    for loc in ("New Orleans, LA", "Chalmette", "Gretna", "Metairie",
                "Jefferson Parish", "St. Bernard"):
        val, _ = defaults.derive("insurance", 12, loc)
        check(f"defaults: {loc} carries $3,000/unit wind insurance", val == 3000,
              f"got {val}")
    for loc in ("Baker, LA", "Denham Springs", "Gonzales", "Covington"):
        val, _ = defaults.derive("insurance", 12, loc)
        check(f"defaults: {loc} carries the $2,000/unit inland basis", val == 2000,
              f"got {val}")
    val, note = defaults.derive("insurance", 12, None)
    check("defaults: with no location the inland basis is used AND disclosed",
          val == 2000 and "no location" in note, f"{val} / {note}")


def test_vintage_hazards_are_shared_with_the_engine():
    """test_mc.py reimplemented these and drifted to the pre-2026-08-09 formulas."""
    check("pymodel exposes vintage_hazards for the tests to read",
          hasattr(pymodel, "vintage_hazards"))
    check("pymodel exposes the capex cap",
          getattr(pymodel, "VINTAGE_CAPEX_CAP_PER_UNIT", None) == 3000.0)
    hv, rf = pymodel.vintage_hazards(40)
    check("vintage hazards are ANNUAL and capped (not lifetime-cumulative)",
          hv == 0.12 and rf == 0.09, f"hvac={hv} roof={rf}")
    hv0, rf0 = pymodel.vintage_hazards(5)
    check("a new building carries no vintage hazard", hv0 == 0.0 and rf0 == 0.0,
          f"hvac={hv0} roof={rf0}")
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent / "test_mc.py").read_text()
    check("test_mc.py no longer reimplements the hazard formulas",
          "(eff_age - 15) / 40.0" not in src.replace(
              "# Sweep 2026-09-28: was `max(0.0, (eff_age - 15) / 40.0)` - the", ""))


def test_savings_are_capital_not_portfolio_return():
    """annual_savings_contribution was credited to port_cf as an inflow.

    Sweep 2026-10-12: this test asserted NOTHING. The fixture set
    `annual_savings`, while `portfolio_sim.simulate` reads
    `annual_savings_contribution` (portfolio_sim.py:140) and `spec["year"]`,
    not `acq_year`. So `with_sav` and `without` were the identical
    zero-savings simulation and both checks compared a number to itself
    (portfolio_irr 0.2841410018 and total_profit $468,044.49 on both sides,
    byte-identical portfolio_cf). Re-adding `+ savings` to `port_cf[yr]` would
    have left it green - the same shape as the 10-05 F1/F2/F3 findings.

    With the keys the code actually reads, the 09-28 fix does hold: savings
    30k vs 0 gives IRR 28.4141% either way and profit $468,044 either way.
    The fixture now also asserts it is exercising a real difference, so a
    renamed key cannot hollow it out again.
    """
    import portfolio_sim
    scen = {"name": "t", "starting_equity": 300_000,
            "annual_savings_contribution": 30_000,
            "deals": [{"deal": "eden-church-mhp", "year": 0, "price": 1_094_000,
                       "location": "denham springs"}]}
    with_sav = portfolio_sim.simulate(scen)
    without = portfolio_sim.simulate(dict(scen, annual_savings_contribution=0))

    # Guard the fixture itself: the two runs must differ in the savings input,
    # or the comparisons below are vacuous.
    check("portfolio_sim: the fixture really varies the savings input",
          (with_sav.get("annual_savings") or 0) != (without.get("annual_savings") or 0)
          or with_sav.get("peak_deployed") != without.get("peak_deployed")
          or with_sav.get("annual_table") != without.get("annual_table"),
          f"both runs identical - simulate() ignored the key "
          f"(with={with_sav.get('annual_savings')!r} without={without.get('annual_savings')!r})")

    check("portfolio_sim: savings do not inflate the portfolio IRR",
          abs((with_sav["portfolio_irr"] or 0) - (without["portfolio_irr"] or 0)) < 0.005,
          f"{with_sav['portfolio_irr']} vs {without['portfolio_irr']}")
    check("portfolio_sim: savings do not inflate reported profit",
          abs(with_sav["total_profit"] - without["total_profit"]) < 1.0,
          f"{with_sav['total_profit']} vs {without['total_profit']}")


def test_price_override_reassesses_taxes_from_the_records_location():
    """The authoritative location sits in the deal RECORD, not only the scenario."""
    import portfolio_sim, latax
    # No "location" in the spec: it must be picked up from the loaded deal.
    got = portfolio_sim._build_inputs({"deal": "eden-church-mhp", "price": 1_094_000})
    want, _ = latax.estimate_tax(1_094_000, "denham springs")
    check("portfolio_sim: an overridden price re-derives the tax bill from the "
          "record's own location",
          want is not None and abs(got["taxes_annual"] - want) < 1.0,
          f"got {got.get('taxes_annual')}, want {want}")


def test_price_override_does_not_guess_the_parish_from_the_deal_name():
    """'central-city-2nd' (Orleans) matched the token 'central' -> East Baton Rouge."""
    import pathlib as _pl
    src = (_pl.Path(__file__).resolve().parent / "portfolio_sim.py").read_text()
    check("portfolio_sim: the unanchored deal-name token loop is gone",
          "for part in deal_name.split" not in src)
    check("portfolio_sim: an unresolvable location warns instead of freezing silently",
          "no location resolves" in src)


def test_cash_returned_is_what_was_actually_received():
    """'Cash returned' netted same-year acquisitions out of distributions."""
    import portfolio_sim, json, pathlib
    path = pathlib.Path(portfolio_sim.__file__).resolve().parent.parent / \
        "portfolio" / "scenarios" / "baker-then-fourplex.json"
    if not path.exists():
        check("portfolio_sim: baker-then-fourplex scenario present", True,
              "skipped - scenario missing")
        return
    res = portfolio_sim.simulate(json.loads(path.read_text()))
    gross = 0.0
    for row in res["annual_table"]:
        gross += row.get("cash_in_dist", row.get("cash_in", 0.0)) or 0.0
    check("portfolio_sim: cash returned is not less than the final balance",
          res["total_returned"] >= res["annual_table"][-1]["running_balance"] - 1.0,
          f"returned {res['total_returned']:,.0f} vs final balance "
          f"{res['annual_table'][-1]['running_balance']:,.0f}")


def test_hedonic_fit_cache_is_not_poisoned_by_a_holdout():
    """Any fit(data=...) used to overwrite the cache for all later callers."""
    import hedonic
    hedonic._FIT_CACHE.clear()
    full = hedonic.fit(verbose=False)
    n_full = full.get("n")
    rows = hedonic.load_sales() if hasattr(hedonic, "load_sales") else None
    if rows:
        holdout = [r for i, r in enumerate(rows) if i % 3 != 0]
        hedonic.fit(data=holdout, verbose=False)
    again = hedonic.fit(verbose=False)
    check("hedonic: a holdout fit does not poison the shared cache",
          again.get("n") == n_full, f"{again.get('n')} vs {n_full}")


def test_short_bls_series_does_not_print_a_zero_trend():
    """A series too short to measure printed '+0.0%' as if it were data."""
    import pathlib as _pl
    src = _code_only(_pl.Path(__file__).resolve().parent / "market.py")
    check("market: a short labor-force series yields None, not 0.0",
          "lf_trend = (lf[-1][1] / lf[-13][1] - 1) * 100 if len(lf) >= 13 else None" in src,
          "still returns 0.0")
    check("market: the caller prints n/a rather than a number",
          "series too short" in src)


def test_hot_cap_floor_actually_binds():
    """min(honest-0.005, max(0.045, honest-0.015)) defeated its own floor."""
    import pathlib as _pl
    src = _code_only(_pl.Path(__file__).resolve().parent / "strategies.py")
    check("strategies: the defeated min()/max() hot-cap expression is gone",
          "max(0.045, honest_cap - 0.015)" not in src)
    check("strategies: an explicit floor constant exists", "_HOT_FLOOR" in src)
    # The floor must actually bind, and the suppression branch must be reachable.
    for honest, expect_suppressed in ((0.045, True), (0.048, True),
                                      (0.055, True), (0.070, False)):
        hot = honest - 0.015
        suppressed = hot < 0.045 or hot >= honest
        check(f"strategies: honest cap {honest*100:.1f}% -> "
              f"{'suppressed' if expect_suppressed else f'hot cap {hot*100:.2f}%'}",
              suppressed == expect_suppressed, f"hot={hot}")


def test_irr_verdict_agrees_with_the_number_it_prints():
    """0.129951 printed '13.00%' and read BELOW PURSUE FLOOR; 0.13 read clears."""
    import report
    pairs = [(0.129951, 0.13), (0.099951, 0.10), (0.159951, 0.16), (0.139951, 0.14)]
    for lo, hi in pairs:
        check(f"report: {lo} and {hi} both print {hi*100:.2f}% and get one verdict",
              report.irr_verdict(lo) == report.irr_verdict(hi),
              f"{report.irr_verdict(lo)!r} vs {report.irr_verdict(hi)!r}")


def test_bankpackage_reports_sponsor_cash_not_total_equity():
    """'Cash In (Sponsor Side)' printed total equity -- the LP's money, ~10x over."""
    import pathlib
    src = _code_only(pathlib.Path(__file__).resolve().parent / "bankpackage.py")
    check("bankpackage: the sponsor KPI no longer prints total equity",
          "Cash In (Sponsor Side)" not in src)
    check("bankpackage: gp_capital is actually used",
          "gp_capital" in src and "money(gp_capital" in src)
    check("bankpackage: lp_capital is shown separately",
          "Investor equity (LP)" in src)


def test_icmemo_prefers_the_seller_document_over_an_estimate():
    """sorted(iterdir()) hit ESTIMATED before the OM on baker-trails."""
    import icmemo, pathlib
    d = pathlib.Path(icmemo.__file__).resolve().parent.parent / "deal-intake" / "baker-trails"
    if not d.exists():
        check("icmemo: baker-trails folder present", True, "skipped")
        return
    path, status, alternates = icmemo._detect_rent_roll(d)
    check("icmemo: the OM rent roll is chosen over the ESTIMATED one",
          path is not None and "ESTIMATED" not in path.name.upper(),
          f"chose {path.name if path else None}")
    check("icmemo: the rejected candidate is still named",
          any("ESTIMATED" in n.upper() for n, _ in alternates), str(alternates))

# ===========================================================================
# 2026-10-05 sweep
# ===========================================================================

def test_parity_harness_cannot_pass_on_zero_comparisons():
    """test_pymodel._run_deal certified the engine against nothing.

    _check() silently skips any expected value that is None. A workbook whose
    cached formula values are absent yields 22 of 22 None, so _run_deal made
    zero comparisons, printed "0 passed, 0 failed / ALL PASS" and returned
    True -- then raised TypeError formatting None in its key-numbers block.

    2026-10-12 sweep: this test pinned its fixture to the mlk-2119 workbook,
    which was the one uncalculated workbook in the tree on 2026-10-05 (22,822
    bytes against 32,065-32,571 for the seven recalculated ones). The
    2026-10-06 daily scan (commit 61b2ddc) recalculated it to 32,275 bytes, so
    21 of 22 cached values are now present, _run_deal correctly returns PASS,
    and all four assertions inverted. That was a STALE FIXTURE, not a product
    regression: the guard at test_pymodel.py:209 was intact the whole time.

    The fixture is therefore SYNTHESIZED now, not picked off disk. openpyxl
    drops cached formula values whenever it saves, which is the exact mechanism
    that produced the original uncalculated workbook, so a load-and-save of any
    recalculated workbook reproduces the bug's input condition on demand. No
    daily scan, recalc or new deal can invalidate it again.
    """
    import io
    import contextlib
    import pathlib
    import shutil
    import tempfile
    import openpyxl
    import test_pymodel as tp

    root = pathlib.Path(tp.__file__).resolve().parent.parent

    # Donor: any workbook that really is recalculated. Asserting this first is
    # what makes the synthesized stub meaningful rather than circular.
    donor = None
    for cand in sorted((root / "deal-intake").glob("*/*_acq.xlsx")):
        if sum(1 for v in tp._load_expected(cand).values() if v is not None) >= tp.MIN_COMPARISONS:
            donor = cand
            break
    check("parity: a recalculated donor workbook exists to synthesize from",
          donor is not None, "no workbook in the tree has cached formula values")
    if donor is None:
        return

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="parity-stub-"))
    try:
        stub_deal = "synthetic-uncalculated"
        stub_dir = tmp / stub_deal
        stub_dir.mkdir()
        stub = stub_dir / f"{stub_deal}_acq.xlsx"
        shutil.copy(donor, stub)
        # The mechanism: formulas survive, the cached results do not.
        openpyxl.load_workbook(str(stub)).save(str(stub))

        expected = tp._load_expected(stub)
        n_present = sum(1 for v in expected.values() if v is not None)
        check("parity: the synthesized fixture really is uncalculated",
              n_present == 0, f"{n_present} of {len(expected)} values present")

        orig_intake = tp.INTAKE
        tp.INTAKE = tmp
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                status = tp._run_deal(stub_deal)
            out = buf.getvalue()
        finally:
            tp.INTAKE = orig_intake
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The bug, precisely: zero comparisons must not read as a pass.
    check("parity: zero comparisons is UNVERIFIED, not PASS",
          status == "UNVERIFIED", f"status={status!r}")
    check("parity: the harness no longer claims ALL PASS on that deal",
          "ALL PASS" not in out)
    check("parity: it says why it could not verify",
          "UNVERIFIED" in out and ("not recalculated" in out.lower()
                                   or "formula cells are empty" in out.lower()))

    # It must not crash on the None-formatting path either.
    check("parity: _run_deal returns rather than raising TypeError",
          status in ("PASS", "FAIL", "UNVERIFIED", "MISSING"))

    # And a real workbook must still pass, on a real comparison count.
    good = "treme-gov-nicholls"
    if (root / "deal-intake" / good / f"{good}_acq.xlsx").exists():
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            status2 = tp._run_deal(good)
        check(f"parity: {good} still PASSes", status2 == "PASS", f"status={status2!r}")

    # Coverage: every deal with a workbook is in the harness's list. Three
    # live/board-ranked deals were absent until 2026-10-05.
    src = _code_only(pathlib.Path(tp.__file__))
    on_disk = sorted(p.parent.name for p in (root / "deal-intake").glob("*/*_acq.xlsx"))
    missing = [d for d in on_disk if f'"{d}"' not in src]
    check("parity: every deal with a workbook is covered by the harness",
          not missing, f"uncovered: {missing}")


# ===========================================================================
# 2026-10-12 sweep
# ===========================================================================

def test_every_covered_deal_is_actually_verified_not_unverified():
    """The 10-05 guard turns an uncalculated workbook into UNVERIFIED rather
    than a false PASS -- but nothing asserted that the tree currently has none.

    A workbook can lose its cached values at any time (any openpyxl save that
    is not followed by a LibreOffice recalc does it), and the harness's own
    summary reports UNVERIFIED without failing. So the parity suite can go
    quiet on a deal and still exit 0. mlk-2119 sat in exactly that state from
    the 10-05 sweep until the 10-06 daily scan recalculated it.

    This asserts the live condition: every deal workbook in the tree carries
    enough cached values to be compared against. It is the test that would
    have caught mlk-2119's stub state on 10-05 without pinning anything to
    mlk-2119.
    """
    import pathlib
    import test_pymodel as tp

    root = pathlib.Path(tp.__file__).resolve().parent.parent
    stubs = []
    for wb in sorted((root / "deal-intake").glob("*/*_acq.xlsx")):
        n = sum(1 for v in tp._load_expected(wb).values() if v is not None)
        if n < tp.MIN_COMPARISONS:
            stubs.append(f"{wb.parent.name} ({n} values)")
    check("parity: no deal workbook in the tree is an uncalculated stub",
          not stubs, f"UNVERIFIABLE: {stubs} - run intake.py --recalc")


def test_board_escapes_a_note_and_still_renders():
    """board.py injected a deals.json note into HTML unescaped.

    Latent only because all 7 watch/dead deals happen to carry DEAD_WHY
    entries, so the fallback branch never ran -- but 13 notes across 5 deals
    already contain < > &, and one `portfolio.py add <name> --stage watching`
    without a DEAD_WHY entry publishes a raw note.

    This test RUNS main(), because the obvious fix does not work: main() binds
    a local named `html` further down, which makes `html` local for the whole
    function, so `html.escape(...)` at the top raised UnboundLocalError and
    board.py crashed outright. A source grep for "html.escape" would have
    passed while the board was dead (the same shape as the bankpackage
    tuple-unpack bug). Hence the alias `_escape` and a real render here.
    """
    import pathlib
    import tempfile

    import board

    # 1. It must actually render.
    tmp = pathlib.Path(tempfile.mkdtemp()) / "board.html"
    original_out = board.OUT
    board.OUT = tmp
    try:
        try:
            board.main()
        except Exception as exc:                      # noqa: BLE001
            check("board: main() renders without raising",
                  False, f"{type(exc).__name__}: {exc}")
            return
        check("board: main() renders without raising", True)
        rendered = tmp.read_text()
        check("board: the render is non-trivial", len(rendered) > 5000,
              f"{len(rendered)} bytes")
    finally:
        board.OUT = original_out

    # 2. The fallback branch must escape. Drive it directly with a note that
    #    would break the page, rather than trusting that it is unreachable.
    hostile = 'ask <b>$1M</b> & carry <script>x</script>'
    escaped = board._escape(hostile[:160])
    check("board: the note escaper neutralises tags",
          "<b>" not in escaped and "&lt;b&gt;" in escaped, escaped)
    check("board: the note escaper neutralises ampersands",
          "&amp;" in escaped, escaped)
    check("board: the note escaper neutralises script tags",
          "<script>" not in escaped, escaped)

    # 3. And the fallback site must use it (not the shadowed `html` name).
    src = _code_only(pathlib.Path(board.__file__))
    check("board: the DEAD_WHY fallback escapes the note",
          "_escape((rec.get(\"history\")" in src)
    check("board: the fallback does not use the name main() shadows",
          "html.escape((rec.get(\"history\")" not in src)


def test_writer_refuses_an_incoming_formula():
    """The formula guard inspected only the cell's EXISTING value.

    A string starting with "=" could be written INTO a documented input cell,
    and openpyxl stores it with data_type='f' -- a live formula where a value
    belongs. Reachable from a file: parse_rent_roll keys an unresolved row on
    its raw label and write_rent_roll writes that label verbatim into
    Inputs!F3:F10, so a broker CSV row "103,=SUM(H3:H10)*9,850,900" put
    `=SUM(H3:H10)*9` into Inputs!F4. In the dev model Inputs!H3 does
    VLOOKUP(F3, ...), so column F is load-bearing.
    """
    import pathlib
    import shutil
    import tempfile

    import cellmap
    import safe_writer

    root = pathlib.Path(safe_writer.__file__).resolve().parent.parent
    spec = cellmap.ACQ
    master = root / spec["file"]
    if not master.exists():
        check("writer: master present", True, "skipped")
        return

    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        dest = tmp / "probe.xlsx"
        shutil.copy2(master, dest)
        w = safe_writer.ModelWriter(dest, spec)

        # A plain value into a declared input cell must still work.
        w.set("Inputs!B2", 1_250_000)
        check("writer: an ordinary value still writes", True)

        # Every formula shape must be refused, on both entry points.
        for bad in ("=SUM(H3:H10)*9", "=Inputs!B2*2", "  =1+1", "=cmd|'/c calc'!A1"):
            raised = False
            try:
                w.set("Inputs!B2", bad)
            except safe_writer.FormulaGuardError:
                raised = True
            check(f"writer: set() refuses the incoming formula {bad.strip()[:18]!r}",
                  raised, "it was written")

        raised = False
        try:
            # Inputs!F3 is inside the rent-roll paste range: the live path.
            w.set_rc("Inputs", 3, 6, "=SUM(H3:H10)*9")
        except safe_writer.FormulaGuardError:
            raised = True
        check("writer: set_rc() refuses an incoming formula in a paste range",
              raised, "it was written")

        # And the pre-existing refusals must be untouched.
        for ref in ("Inputs!B52", "Annual CF!D25"):
            raised = False
            try:
                w.set(ref, 1)
            except safe_writer.FormulaGuardError:
                raised = True
            check(f"writer: still refuses to overwrite the formula at {ref}", raised)

        # clear_block writes None and must keep working.
        w.clear_block("Inputs", 3, 3, 6, 6)
        check("writer: clear_block (None) is unaffected", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_refuses_when_no_rent_roll_parsed():
    """--apply with no parsed roll kept the MASTER'S DEMO unit mix.

    write_rent_roll sits behind `if groups:`; when nothing parsed it was never
    called, so Inputs!E3:H10 kept 4 x 1BR @ $900 + 4 x 2BR @ $1,100 while the
    deal's own price and expenses were written around it. A real 4-unit T-12 at
    --price 250000 --units-override 4 produced 8 phantom units at $150,000/unit:
    NOI $47,222 vs $17,726, cap 18.89% vs 7.09%, levered IRR 45.97% vs 4.08%,
    no note. This is the unit-mix twin of the F1 price gate.
    """
    import pathlib

    import intake

    src = _code_only(pathlib.Path(intake.__file__))
    check("intake: there is a gate on --apply with no parsed groups",
          "if args.apply and not groups:" in src)
    check("intake: the gate raises rather than appending a note",
          "ABORT: --apply with no" in src)
    check("intake: the gate names the demo mix as the consequence",
          "MASTER'S DEMO MIX" in src)
    # The gate must sit BEFORE the write block it protects.
    gate = src.index("if args.apply and not groups:")
    write = src.index("if groups:\n        dropped = write_unit_mix")
    check("intake: the gate precedes the unit-mix write", gate < write,
          f"gate@{gate} write@{write}")


def test_chol_factor_reproduces_the_declared_correlations():
    """_CHOL_L row 5 did not reproduce the declared correlation matrix.

    L[5] = [-0.3, 0, 0, 0, 0, sqrt(0.91)] satisfies rho(rg,turnover) = -0.3 and
    the unit norm, but is not the Cholesky factor: it leaked
    rho(expense_growth, turnover) = -0.090 and rho(vacancy, turnover) = +0.150
    where the spec declares both 0.000 (realized -0.0926 / +0.1480 on 100k
    draws), and the comment above it claimed "Verified: L @ L.T reproduces the
    correlation matrix exactly".

    test_mc's correlation test could not catch it: it samples _tri_icdf where
    the engine uses _pw_icdf, checks only 3 of the 15 pairs (neither of the
    broken ones), and both corrupted values sit inside its +/-0.15 tolerance.
    This assertion is on the constant itself, so it costs no draws and cannot
    be defeated by tolerance.
    """
    import pymodel

    L = pymodel._CHOL_L
    n = len(L)
    check("mc: the Cholesky factor is 6x6", n == 6 and all(len(r) == 6 for r in L))

    # The declared matrix, from the comment block above _CHOL_L.
    # order: rent_growth, expense_growth, vacancy, insurance_mult,
    #        exit_cap_spread, turnover_rate
    R = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i, j, v in ((0, 1, 0.3), (0, 2, -0.5), (3, 4, 0.3), (0, 5, -0.3)):
        R[i][j] = R[j][i] = v

    worst, where = 0.0, None
    for i in range(n):
        for j in range(n):
            got = sum(L[i][k] * L[j][k] for k in range(n))
            if abs(got - R[i][j]) > worst:
                worst, where = abs(got - R[i][j]), (i, j, got, R[i][j])
    check("mc: L @ L.T reproduces the declared correlation matrix (1e-9)",
          worst < 1e-9,
          f"worst |L@L.T - R| = {worst:.6f} at {where}")

    # Lower-triangular, and every row a unit vector (else a marginal is not
    # standard normal and every recentring claim is off).
    check("mc: the factor is lower-triangular",
          all(abs(L[i][j]) < 1e-12 for i in range(n) for j in range(i + 1, n)))
    norms = [sum(x * x for x in row) for row in L]
    check("mc: every row of the factor has unit norm",
          all(abs(v - 1.0) < 1e-9 for v in norms),
          str([round(v, 12) for v in norms]))


def test_bankpackage_sees_the_documents_that_are_on_disk():
    """bankpackage was DEAD: a 3-tuple unpacked into 2 names.

    The 2026-09-28 F26 fix made icmemo._detect_rent_roll return
    (path, status, alternates); bankpackage.py:78 still unpacked two. Every run
    raised ValueError, the fail-closed handler swallowed it, and the package
    either refused outright or -- with --force -- told a LENDER "no rent roll
    file in the deal folder" and "no trailing-12 operating statement on file"
    over eden's rentroll_eden_FROM_SELLER.csv and PL_2025_ACTUALS.xlsx, while
    the same-day IC memo listed both as present.

    test_bankpackage_diligence_gates_fail_closed could not catch it: it asserts
    only that a broken check refuses, which the bug itself satisfies.
    """
    import contextlib
    import io
    import pathlib

    import bankpackage

    root = pathlib.Path(bankpackage.__file__).resolve().parent.parent
    deal = "eden-church-mhp"
    if not (root / "deal-intake" / deal).exists():
        check(f"bankpackage: {deal} folder present", True, "skipped")
        return

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                bankpackage.gather(deal, force=True)
            except SystemExit:
                pass
            except Exception as exc:          # noqa: BLE001
                check("bankpackage: gather() does not raise on a real deal",
                      False, f"{type(exc).__name__}: {exc}")
                return
    stderr = err.getvalue()

    # The bug's signature.
    check("bankpackage: the diligence check no longer errors out",
          "diligence-gap check failed" not in stderr, stderr[:200])
    check("bankpackage: no tuple-unpack error",
          "too many values to unpack" not in stderr, stderr[:200])

    # And it must actually SEE the seller documents sitting in the folder.
    import icmemo
    rr = icmemo._detect_rent_roll(root / "deal-intake" / deal)
    check("bankpackage: _detect_rent_roll still returns 3 values",
          len(rr) == 3, f"returned {len(rr)}")
    check(f"bankpackage: {deal}'s seller rent roll is detected",
          rr[1] == "actual", f"status={rr[1]!r} path={rr[0]}")
    check(f"bankpackage: {deal}'s T-12 is detected",
          icmemo._detect_t12(root / "deal-intake" / deal) is not None)

    # Sources & Uses must not call the LP's money the sponsor's (F25 fixed the
    # KPI tile and the Sponsor table but not this third place, 10x on all three
    # live deals: eden $471,472 vs GP $47,147).
    src = _code_only(pathlib.Path(bankpackage.__file__))
    check("bankpackage: Sources & Uses no longer prints 'Sponsor equity'",
          "'Sponsor equity'" not in src)
    check("bankpackage: Sources & Uses splits GP cash from LP equity",
          src.count("money(gp_capital)") >= 2 and src.count("money(lp_capital)") >= 2,
          f"gp={src.count('money(gp_capital)')} lp={src.count('money(lp_capital)')}")


def test_wind_basis_agrees_with_the_parish_table():
    """wind_exposed() substring-matched a hand-kept town tuple, wrong BOTH ways.

    Six towns latax places in a wind parish were absent from the tuple, so they
    returned False -- not None -- and derive() wrote them up as "<town> is
    inland - $2,000/unit". poydras (St. Bernard) and elmwood (Jefferson) are
    both in the buy box; on one 12-unit building poydras cleared 13% at
    $640,000 against chalmette's $517,000 -- same parish, same millage,
    $123,000 apart purely because "chalmette" was in the tuple.
    In the other direction any string CONTAINING a wind name matched:
    "5555 Jefferson Hwy, Baton Rouge" took $3,000/unit and understated its
    clearing price by $96,000, and the inland "Jefferson Davis Parish" read as
    coastal. The old test only checked towns that were already in the tuple, so
    it could never see the gap -- hence the cross-check below.
    """
    import defaults
    import latax

    wind = {"orleans", "jefferson", "st. bernard", "plaquemines"}

    # Every town the tax math knows must get the same answer from the
    # insurance basis. This is the assertion whose absence hid the bug.
    mismatched = []
    for town, parish in latax.CITY_TO_PARISH.items():
        got = defaults.wind_exposed(town)
        if got is not (parish in wind):
            mismatched.append((town, parish, got))
    check("defaults: wind basis agrees with latax.CITY_TO_PARISH for every town",
          not mismatched, f"{mismatched[:6]}")

    # The specific towns that regressed.
    for town in ("poydras", "elmwood", "port sulphur", "buras", "boothville", "venice"):
        if town not in latax.CITY_TO_PARISH:
            continue
        amount, note = defaults.derive("insurance", 12, town)
        check(f"defaults: {town} takes the $3,000 wind basis",
              amount == 3000, f"${amount} / {note[:70]}")
        check(f"defaults: {town} is not described as inland",
              "inland" not in note.lower(), note[:70])

    # A street address must resolve on its CITY, not on a substring hit.
    for addr, expect_wind in (
            ("5555 Jefferson Hwy, Baton Rouge", False),
            ("1234 Harvey Ln, Baton Rouge", False),
            ("900 Algiers St, Lafayette", False),
            ("77 Kenner Ave, Hammond", False),
            ("Central Ave, Metairie", True),
            ("400 Elmwood Park Blvd, Harahan", True)):
        check(f"defaults: {addr!r} -> wind={expect_wind}",
              defaults.wind_exposed(addr) is expect_wind,
              f"got {defaults.wind_exposed(addr)}")

    # A real parish that merely starts with a wind-parish word is NOT coastal,
    # and must read as unknown rather than being guessed either way.
    check("defaults: 'Jefferson Davis Parish' is not treated as coastal",
          defaults.wind_exposed("Jefferson Davis Parish") is not True,
          f"got {defaults.wind_exposed('Jefferson Davis Parish')}")
    check("latax: 'Jefferson Davis Parish' does not resolve to Jefferson",
          latax.infer_parish("Jefferson Davis Parish")[0] is None,
          str(latax.infer_parish("Jefferson Davis Parish")))

    # An unresolved location must not be asserted as inland.
    _amt, note = defaults.derive("insurance", 12, "Jefferson Davis Parish")
    check("defaults: an unresolved location says ASSUMED, not 'is inland'",
          "inland basis ASSUMED" in note and "Parish is inland" not in note,
          note[:90])

    # "<Parish> Parish" is what a human types, and tools/README.md promises it
    # works. All three of these resolved to None before 2026-10-05, and the
    # loud failure path freezes taxes across every trial price.
    for text, expect in (("Ascension Parish", "ascension"),
                         ("Orleans Parish", "orleans"),
                         ("East Baton Rouge Parish", "east baton rouge"),
                         ("St. Tammany Parish", "st. tammany")):
        got, _why = latax.resolve_parish(text)
        check(f"latax: {text!r} resolves to {expect}", got == expect, f"got {got}")

    # And the word "parish" must not shadow a millage key.
    check("latax: no millage key contains 'parish'",
          not any("parish" in k for k in latax.MILLAGE))


def test_generators_refuse_to_overwrite_a_master():
    """Running a generator silently overwrote a master calculator (W7).

    generators/README.md said the scripts write to /home/claude/models/ and to
    "change the save path", but build_acq.py:497 and build_dev.py:475 already
    pointed at the repo-root masters. openpyxl writes formula STRINGS with no
    cached values and recalc needs LibreOffice Calc, which this container lacks,
    so the overwrite blanks every computed cell and is unrecoverable here.
    """
    import hashlib
    import pathlib
    import subprocess

    gen = pathlib.Path(__file__).resolve().parent.parent / "generators"
    root = gen.parent
    pairs = [("build_acq.py", "Multifamily_Acquisition_Model.xlsx"),
             ("build_dev.py", "Multifamily_Development_Model.xlsx")]

    for script, master in pairs:
        spath, mpath = gen / script, root / master
        if not spath.exists() or not mpath.exists():
            check(f"generators: {script} and {master} present", True, "skipped")
            continue

        before = hashlib.md5(mpath.read_bytes()).hexdigest()
        proc = subprocess.run(["python3", str(spath)], cwd=str(gen),
                              capture_output=True, text=True, timeout=300)
        after = hashlib.md5(mpath.read_bytes()).hexdigest()

        check(f"generators: {script} leaves {master} byte-identical",
              before == after, "THE MASTER WAS OVERWRITTEN")
        check(f"generators: {script} exits non-zero rather than overwriting",
              proc.returncode != 0, f"rc={proc.returncode}")
        check(f"generators: {script} says why it refused",
              "REFUS" in (proc.stdout + proc.stderr).upper(),
              (proc.stdout + proc.stderr)[:200])

    # The documented repair path must still work, or the guard just blocks work.
    src = _code_only(gen / "build_acq.py")
    check("generators: an explicit --force escape hatch exists", "--force" in src)
    check("generators: an --out redirect exists", "--out" in src)

    readme = (gen / "README.md").read_text()
    check("generators: README no longer claims the scripts write to /home/claude/models/",
          "/home/claude/models/" not in readme, readme[:200])



def test_board_mlk_copy_ties_to_the_engine_on_the_carried_basis():
    """The 2026-10-05 sweep told Anthony the board's mlk-2119 copy was false.

    Its deferred item reads: *"board.py:95 mlk-2119 'it loses money' is false --
    live +1.33% IRR, 1.066x, not -9.8%/0.62x"*, and correction C-c calls
    going-in + 50bps the "house rule exit" against the deal's carried 0.075.

    Reproduced 2026-10-12: +1.3347% / 1.0658x is exactly what the engine
    returns when exit_cap is replaced with going_in_cap + 50bps (0.05533). On
    the basis the deal actually carries -- exit_cap 0.075, the value in
    deals.json, in the workbook and in the parity-verified engine -- it is
    -9.7946% / 0.6155x, which is what the board prints.

    Both conventions are live in this repo, which is the real defect: pymodel
    derives going-in + 50bps when exit_cap is unset (pymodel.py:952,
    defaults.py:46) and uses the carried value when it is set. mlk-2119 carries
    0.075 explicitly, so the board, the record and the engine all agree at
    -9.79%. The 10-05 item substituted the derived default for that carried
    value and called the published number false, without saying it had changed
    basis -- an 11.13pp swing in IRR on one deal from an unlabelled convention
    switch. The board copy ties to the deal's own basis; the item does not.

    The verdict is DEAD on either basis (+1.33% misses the 13% floor by 11.67pp),
    so no deal decision turns on this. What it protects is the published number.
    """
    import pathlib
    import re

    r = pymodel.run(pymodel._load_deal("mlk-2119"))
    irr, em = r["levered_irr"], r["equity_multiple"]

    check("mlk: the carried basis still loses money",
          irr < 0, f"levered_irr={irr:.6f}")
    check("mlk: levered IRR is -9.79%, not the +1.33% of the 10-05 correction",
          abs(irr - (-0.097946)) < 0.0002, f"got {irr*100:.4f}%")
    check("mlk: equity multiple is 0.6155x, not 1.066x",
          abs(em - 0.6155) < 0.001, f"got {em:.4f}x")

    # And the board's hand-typed copy must still agree with that engine run.
    src = pathlib.Path(pymodel.__file__).resolve().parent / "board.py"
    copy = src.read_text()
    m = re.search(r'"mlk-2119":\s*\((.*?)\),\n', copy, re.S)
    check("mlk: the board still carries a DEAD_WHY entry", m is not None)
    if m:
        text = m.group(1)
        pct = re.search(r"<b>(-[\d.]+)%</b>", text)
        mult = re.search(r"([\d.]+)x multiple", text)
        check("mlk: the board's printed IRR matches the engine to 0.1pp",
              pct is not None and abs(float(pct.group(1)) - irr * 100) < 0.1,
              f"board={pct.group(1) if pct else None} engine={irr*100:.2f}")
        check("mlk: the board's printed multiple matches the engine to 0.01x",
              mult is not None and abs(float(mult.group(1)) - em) < 0.01,
              f"board={mult.group(1) if mult else None} engine={em:.4f}")


def test_no_debt_service_after_the_loan_amortizes_away():
    """Guards the 2026-08-09 fix: post-payoff debt service when amort < hold.

    CLAUDE.md pending item 5 asks whether the master workbooks share this bug.
    Reproduced on a scratch copy 2026-10-12 (the master itself was not touched):
    amortization 5 years, hold 8, loan $148,421, monthly payment $2,921.43.
    The workbook's Annual CF!32 computes interest as `12*payment - (beg - end)`
    whenever the interest-only gate is false, so once the balance reaches zero
    in year 5 it keeps charging a full 12 payments against a zero balance:

        yr   beg_bal   total_DS   end_bal
         5    33,808     35,057         0
         6         0     35,057         0
         7         0     35,057         0
         8         0     35,057         0

    $105,171 of debt service on a repaid loan, levered IRR 5.7135% against
    pymodel's 7.7465% on identical inputs -- 2.03pp -- and all twelve rows of
    the workbook's own Checks tab still read OK. Flagged for a human session,
    per the house rule against editing the masters' formulas.

    The engine side is correct and this pins it there.
    """
    import test_pymodel as tp
    import pathlib

    master = pathlib.Path(pymodel.__file__).resolve().parent.parent / "Multifamily_Acquisition_Model.xlsx"
    if not master.exists():
        check("payoff: master workbook present to read inputs from", True, "skipped")
        return

    inputs = dict(tp._load_inputs(master))
    inputs["amort_years"] = 5
    inputs["hold_years"] = 8
    r = pymodel.run(inputs)

    check("payoff: the loan is fully repaid in the amortization year",
          abs(r["end_bal"][5]) < 1.0, f"end_bal[5]={r['end_bal'][5]:,.2f}")
    after = {y: r["total_ds"][y] for y in (6, 7, 8)}
    check("payoff: no debt service is charged after the loan is repaid",
          all(abs(v) < 1.0 for v in after.values()),
          f"total_ds after payoff: {after}")
    check("payoff: cash flow after debt service equals cash flow before it",
          all(abs(r["cfads"][y] - r["cfbds"][y]) < 1.0 for y in (6, 7, 8)),
          f"cfads/cfbds diverge: {[(y, round(r['cfads'][y]-r['cfbds'][y],2)) for y in (6,7,8)]}")


def test_parity_cannot_pass_with_every_return_metric_blank():
    """A narrower sibling of the 10-05 F1 finding, found and fixed 2026-10-12.

    `Annual CF!C55` and `Waterfall!C30/C33` are `IFERROR(IRR(...),"")`, so they
    go blank whenever the cash-flow series has no sign change -- which is what a
    deeply underwater deal produces. `_check` skips a blank expected value, and
    the MIN_COMPARISONS guard does not notice, because the balance-sheet lines
    (NOI, EGR, loan, equity, DSCR, balances, capital) clear 10 on their own.

    Reproduced on a forced-negative-cash-flow workbook (LTV 95%, min DSCR 0.50,
    insurance $6,000/unit -> levered CF -13,822 / -14,001 / -14,193 / -14,399 /
    -130,791): 19 of 22 expected values present, `levered_irr`, `lp_irr` and
    `gp_irr` all blank and silently skipped. Nothing stopped `_run_deal` from
    printing PASS having verified every number except the four the system exists
    to produce.

    `_run_deal` now requires at least one of Levered IRR / Unlevered IRR /
    Equity multiple to have been compared, and reports UNVERIFIED otherwise. A
    genuine mismatch still outranks it and reports FAIL.
    """
    import io
    import contextlib
    import test_pymodel as tp

    deal = "treme-gov-nicholls"
    if not (tp.INTAKE / deal / f"{deal}_acq.xlsx").exists():
        check(f"returns: {deal} workbook present", True, "skipped - not in tree")
        return

    # Baseline: the deal passes, and it passes BECAUSE returns were compared.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        baseline = tp._run_deal(deal)
    check("returns: the unmodified deal still PASSes",
          baseline == "PASS", f"status={baseline!r}")

    # Now blank only the return metrics, leaving every other cached value real.
    real_loader = tp._load_expected

    def blank_returns(path):
        d = dict(real_loader(path))
        for k in ("levered_irr", "unlevered_irr", "equity_multiple",
                  "lp_irr", "lp_em", "gp_irr"):
            d[k] = ""
        return d

    tp._load_expected = blank_returns
    try:
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            status = tp._run_deal(deal)
        out = buf2.getvalue()
    finally:
        tp._load_expected = real_loader

    check("returns: a run with no return comparison is UNVERIFIED, not PASS",
          status == "UNVERIFIED", f"status={status!r}")
    check("returns: it does not claim ALL PASS",
          "ALL PASS" not in out)
    check("returns: it says which metrics were missing",
          "return metric" in out and "Levered IRR" in out)
    check("returns: the balance-sheet comparisons still ran",
          "passed" in out and "0 passed" not in out)


def test_geography_resolves_under_every_input_form():
    """REGRESSION FROM afcaa73 (the 10-05 F4/F5 fix), found 2026-10-12.

    `_norm` strips "parish" only when TRAILING, but `infer_parish` bailed out on
    the word appearing ANYWHERE, before any town matching. So "<town>, <Parish>
    Parish" - the form a human types - resolved to nothing, and both the
    insurance basis and the tax reassessment were lost. All 97 towns failed that
    form; 25 are in a wind parish, and the pre-fix substring code got every one
    of them right.

    Dollars, 12-unit x $950, house DSCR 1.30, 13% rung:
      "Chalmette"                      $3,000/u   $517,448   correct
      "Chalmette, St. Bernard Parish"  $2,000/u   $640,229   $122,781 (23.7%) high
      "Gretna, Jefferson Parish"       $2,000/u   $655,451   $125,701 (23.7%) high
    treme-gov-nicholls is live and board-ranked and records its location as
    "1429 Governor Nicholls St, Treme, Orleans Parish" - exactly that form.

    The 10-05 test could not see any of this: it iterated CITY_TO_PARISH and
    called wind_exposed(bare_town), comparing two things that read the same
    table. This test crosses every town with every input form instead.
    """
    import latax
    import defaults

    forms = [
        ("bare", lambda t, par: t),
        ("state", lambda t, par: f"{t}, LA"),
        ("state+zip", lambda t, par: f"{t}, LA 70000"),
        ("town+parish", lambda t, par: f"{t}, {par.title()} Parish"),
        ("street+town", lambda t, par: f"123 Main St, {t}, LA"),
    ]
    wind_parishes = {"orleans", "jefferson", "st. bernard", "plaquemines"}

    for label, mk in forms:
        wrong_parish, wrong_wind, no_tax = [], [], []
        for town, par in sorted(latax.CITY_TO_PARISH.items()):
            loc = mk(town, par)
            got, _ = latax.infer_parish(loc)
            if got != par:
                wrong_parish.append((loc, got))
            we = defaults.wind_exposed(loc)
            we = we[0] if isinstance(we, tuple) else we
            if bool(we) != (par in wind_parishes):
                wrong_wind.append((loc, we))
            tax, _why = latax.estimate_tax(500_000, loc)
            if tax is None:
                no_tax.append(loc)
        check(f"geo[{label}]: every town resolves to its parish",
              not wrong_parish, f"{len(wrong_parish)} wrong, e.g. {wrong_parish[:3]}")
        check(f"geo[{label}]: the wind basis agrees with the parish table",
              not wrong_wind, f"{len(wrong_wind)} wrong, e.g. {wrong_wind[:3]}")
        check(f"geo[{label}]: taxes reassess rather than silently freezing",
              not no_tax, f"{len(no_tax)} returned None, e.g. {no_tax[:3]}")


def test_four_jefferson_towns_are_back_on_the_wind_basis():
    """estelle, timberlane, waggaman and bridge city were in the pre-afcaa73
    defaults._WIND_TOWNS tuple and were dropped when F4 moved geography onto
    CITY_TO_PARISH, so each fell silently to the $2,000 inland basis: a 13%
    clearing price of $655,451 against a correct $529,750 on a 12-unit x $950
    deal, $125,701 (23.7%) overstated, each. All four are Jefferson Parish
    census places with multifamily stock.
    """
    import latax
    import defaults

    for town in ("estelle", "timberlane", "waggaman", "bridge city"):
        par, _ = latax.infer_parish(town)
        check(f"jeff4: {town!r} resolves to Jefferson Parish",
              par == "jefferson", f"got {par!r}")
        ins = defaults.derive("insurance", 8, town)
        ins = ins[0] if isinstance(ins, tuple) else ins
        check(f"jeff4: {town!r} takes the $3,000 wind basis",
              ins == 3000, f"got {ins!r}")


def test_a_parish_we_do_not_carry_never_becomes_one_we_do():
    """F4 named Jefferson Davis as its counterexample, and the 10-05 guard held
    only for the spelling that includes the word "parish". "Jennings, Jefferson
    Davis, LA" has no such word, so it matched the town "jefferson" and took
    Jefferson Parish's 118.40 millage and $3,000 wind basis for an inland
    rice-country deal. A non-Louisiana state did the same: "Jefferson, TX".
    """
    import latax

    for loc in ("Jefferson Davis Parish", "Jefferson Davis",
                "Jennings, Jefferson Davis, LA", "Jefferson, TX",
                "New Orleans, MS"):
        par, why = latax.infer_parish(loc)
        check(f"confusable: {loc!r} does not resolve",
              par is None, f"resolved to {par!r} ({why})")
        tax, _ = latax.estimate_tax(500_000, loc)
        check(f"confusable: {loc!r} yields no tax estimate",
              tax is None, f"got {tax!r}")

    # And the real ones still work.
    for loc, want in (("Jefferson Parish", "jefferson"),
                      ("Jefferson, LA", "jefferson"),
                      ("Metairie", "jefferson")):
        got, _ = latax.infer_parish(loc)
        check(f"confusable: {loc!r} still resolves to {want}",
              got == want, f"got {got!r}")


def test_town_before_street_resolves_to_the_town():
    """The tie-break key was (end_index, n_words), so a 1-word town LATER in the
    string beat the real city earlier in it, and the note asserted it as fact:
    "Baton Rouge - 5555 Jefferson Hwy" returned "Jefferson Parish is
    coastal-wind" for a Baton Rouge property, taking $3,000/unit. EBR 12-unit x
    $950: 13% rung $535,092 as resolved against $662,167 correct, $127,075
    (19.2%) understated - the same figure defaults.py:132 claims F4 removed.
    Preferring the longest match fixes the reversed order and leaves every
    street-then-city case unchanged.
    """
    import latax
    import defaults

    reversed_forms = [
        "Baton Rouge - 5555 Jefferson Hwy",
        "Baton Rouge: 5555 Jefferson Hwy",
        "Baton Rouge (5555 Jefferson Hwy)",
        "Baton Rouge 70806 - 5555 Jefferson Hwy",
    ]
    for loc in reversed_forms:
        par, why = latax.infer_parish(loc)
        check(f"order: {loc!r} -> East Baton Rouge",
              par == "east baton rouge", f"got {par!r} ({why})")
        ins = defaults.derive("insurance", 12, loc)
        ins = ins[0] if isinstance(ins, tuple) else ins
        check(f"order: {loc!r} takes the $2,000 inland basis",
              ins == 2000, f"got {ins!r}")

    # And the MIRROR case, which the first attempt at this fix broke: a
    # multi-word table entry inside a STREET name, ahead of a one-word real
    # city. Flipping the key to (n_words, end_index) made "1 Central City Rd,
    # Baker, LA" resolve to Orleans where the city is Baker. Neither pure
    # ordering can be right - the real signal is whether the match is followed
    # by a street-type suffix - so both directions are pinned here.
    for loc, want in (("1 Central City Rd, Baker, LA", "east baton rouge"),
                      ("100 Baton Rouge Ave, Gonzales, LA", "ascension"),
                      ("1 Gretna Blvd, Hammond, LA", "tangipahoa"),
                      ("200 Covington St, Gonzales, LA", "ascension")):
        got, why = latax.infer_parish(loc)
        check(f"order: {loc!r} -> {want} (town in a street name is demoted)",
              got == want, f"got {got!r} ({why})")

    # The street-then-city cases the 10-05 fix established must not move.
    for loc, want in (("5555 Jefferson Hwy, Baton Rouge", "east baton rouge"),
                      ("1234 Harvey Ln, Baton Rouge", "east baton rouge"),
                      ("900 Algiers St, Lafayette", "lafayette"),
                      ("77 Kenner Ave, Hammond", "tangipahoa"),
                      ("Central Ave, Metairie", "jefferson")):
        got, _ = latax.infer_parish(loc)
        check(f"order: {loc!r} still -> {want}", got == want, f"got {got!r}")


def test_every_geography_consumer_uses_the_same_resolver():
    """afcaa73 added infer_parish and wired exactly ONE of its three consumers
    to it, so one address string produced two different geographic verdicts in
    the same run: correct $3,000 wind insurance and NO tax reassessment at all.
    estimate_tax returned None for 97 of 97 towns under "123 Main St, <town>,
    LA", and run() has no tax derivation of its own, so the $0 stuck. On Treme
    at its $714,000 rung: NOI $75,500 and IRR 18.24% against $66,076 and
    12.93%, and a 13% clearing price of $825,737 against $712,682 - $113,055
    (15.9%) overstated, printing 12.06% at the ask against a true 6.47%.
    """
    import latax
    import defaults
    import hedonic

    addr = "1429 Governor Nicholls St, Treme, New Orleans, LA 70116"
    bare = "new orleans"

    ins_a = defaults.derive("insurance", 8, addr)
    ins_a = ins_a[0] if isinstance(ins_a, tuple) else ins_a
    tax_a, _ = latax.estimate_tax(714_000, addr)
    tax_b, _ = latax.estimate_tax(714_000, bare)

    check("consumers: insurance resolves the address", ins_a == 3000, f"got {ins_a!r}")
    check("consumers: estimate_tax resolves the same address",
          tax_a is not None, "returned None - taxes would silently freeze at 0")
    check("consumers: the address and the bare town agree on tax",
          tax_a is not None and tax_b is not None and abs(tax_a - tax_b) < 1.0,
          f"address {tax_a!r} vs bare town {tax_b!r}")

    # hedonic is the third copy; its own docstring records a 49% comp-market error.
    try:
        mkt = hedonic.market_for_location(addr)
        resolved = mkt is not None
    except Exception as e:
        resolved = False
    check("consumers: hedonic resolves the address too", resolved,
          "market_for_location returned None/raised on a full address")

    # parceltax is CLAUDE.md hard pre-offer gate 2 and had its own resolver.
    import subprocess
    import pathlib as _pl
    tools = _pl.Path(latax.__file__).resolve().parent
    for arg in ("livingston", "Livingston Parish", "ascension parish",
                "st tammany", "Gonzales"):
        r = subprocess.run([sys.executable, str(tools / "parceltax.py"),
                            "--parish", arg, "--assessment", "24637",
                            "--price", "525000"],
                           capture_output=True, text=True, cwd=str(tools))
        check(f"consumers: parceltax accepts --parish {arg!r}",
              r.returncode == 0, f"exit {r.returncode}: {r.stdout[-120:]}{r.stderr[-120:]}")


def test_intake_never_reads_its_own_workbook_as_a_source():
    """CRITICAL, 2026-10-12: discover() offered intake's own output back to
    intake. A deal slug only has to contain a FILE_HINTS substring, and
    street-derived slugs do - "pl_" (Place) matches the t12 hint - so
    `audubon-pl_acq.xlsx` was selected as the T-12 for deal `audubon-pl`, with
    the hidden `.audubon-pl_acq.staging.xlsx` as its alternate. The model's
    Inputs tab then parses as a T-12, its PER-UNIT expense defaults read as
    ANNUAL totals and divided by the unit count again (payroll $1,715 -> $71),
    so the SAME COMMAND RUN TWICE took a control deal from OpEx $178,543 /
    NOI $85,250 / IRR 2.84% / FAIL to OpEx $34,951 / NOI $228,842 / cap 19.07%
    / IRR 48.52% / "MEETS TARGET" - with zero formula errors and all twelve
    Checks OK. Runs 3-5 converge on that fixed point.

    Detection is by sheet signature, not filename, so a model workbook planted
    under any name also cannot be read as a rent roll (where it supplies the
    master's demo mix and so slips past the F7 "nothing parsed" gate by parsing
    to something).
    """
    import pathlib
    import shutil
    import tempfile
    import intake

    root = pathlib.Path(intake.__file__).resolve().parent.parent
    donor = next((root / "deal-intake").glob("*/*_acq.xlsx"), None)
    if donor is None:
        check("selfread: a model workbook exists to plant", True, "skipped")
        return

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selfread-"))
    try:
        deal = "audubon-pl"           # "pl_" matches the t12 hint
        d = tmp / deal
        d.mkdir()
        shutil.copy(donor, d / f"{deal}_acq.xlsx")
        shutil.copy(donor, d / f".{deal}_acq.staging.xlsx")
        shutil.copy(donor, d / "rentroll_planted.xlsx")   # any name
        (d / "rentroll.csv").write_text("Unit,Type,SF,Rent\n1,1BR/1BA,700,850\n")

        skipped = []
        found, alternates = intake.discover(d, skipped=skipped)

        chosen = {k: v.name for k, v in found.items()}
        check("selfread: our own output is not chosen as a source",
              all("_acq.xlsx" not in n for n in chosen.values()), f"chosen={chosen}")
        check("selfread: the hidden staging file is not chosen either",
              all(not n.startswith(".") for n in chosen.values()), f"chosen={chosen}")
        check("selfread: a model workbook under any name is excluded",
              "rentroll_planted.xlsx" not in chosen.values(), f"chosen={chosen}")
        check("selfread: it is not demoted to an alternate, it is excluded",
              all("_acq.xlsx" not in x.name and "planted" not in x.name
                  for lst in alternates.values() for x in lst),
              f"alternates={{k: [x.name for x in v] for k, v in alternates.items()}}")
        check("selfread: the exclusions are surfaced, not silent",
              len(skipped) == 3, f"skipped={[x.name for x in skipped]}")
        check("selfread: the genuine rent roll is still chosen",
              chosen.get("rent_roll") == "rentroll.csv", f"chosen={chosen}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # And no live deal's source selection moved.
    for deal_dir in sorted((root / "deal-intake").iterdir()):
        if not deal_dir.is_dir() or deal_dir.name.startswith("_"):
            continue
        sk = []
        found, _ = intake.discover(deal_dir, skipped=sk)
        check(f"selfread: {deal_dir.name} still finds a rent roll",
              "rent_roll" in found, f"found={list(found)}")


def test_writer_refuses_an_excel_error_token():
    """The 10-05 formula guard tests only a leading "=", so an Excel error token
    still got through - and it does not stay inert. openpyxl stores "#N/A" with
    data_type='e' and the LibreOffice recalc round-trip promotes it to `=#N/A`
    with data_type='f': a live formula in a documented input cell, the exact
    invariant F8 was written to establish. Reproduced 2026-10-12 on Inputs!F3
    via write_rent_roll from a broker roll carrying #N/A in the Unit Type column
    (ordinary cause: a broken VLOOKUP in the seller's own export); #REF! behaves
    identically.
    """
    import shutil
    import tempfile
    import pathlib
    import cellmap
    from safe_writer import ModelWriter, FormulaGuardError

    root = pathlib.Path(pymodel.__file__).resolve().parent.parent
    master = root / "Multifamily_Acquisition_Model.xlsx"
    if not master.exists():
        check("errtok: master present", True, "skipped")
        return

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="errtok-"))
    try:
        work = tmp / "m.xlsx"
        shutil.copy(master, work)
        spec = cellmap.MODELS["acq"]
        m = spec["rent_roll"]
        w = ModelWriter(work, spec)

        for tok in cellmap.ERROR_TOKENS:
            try:
                w.set_rc(m["sheet"], m["first_row"], m["col_type"], tok)
                check(f"errtok: {tok} is refused", False, "it was written")
            except FormulaGuardError:
                check(f"errtok: {tok} is refused", True)
        try:
            w.set_rc(m["sheet"], m["first_row"], m["col_type"], " #N/A ")
            check("errtok: a padded token is refused too", False, "it was written")
        except FormulaGuardError:
            check("errtok: a padded token is refused too", True)

        # A real unit type must still write.
        try:
            w.set_rc(m["sheet"], m["first_row"], m["col_type"], "1 BR/ 1 BA")
            check("errtok: a genuine unit type still writes", True)
        except FormulaGuardError as e:
            check("errtok: a genuine unit type still writes", False, str(e)[:80])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_an_orleans_neighbourhood_is_not_read_as_a_baton_rouge_town():
    """`infer_parish("Central City")` asserted "matched town 'central' in East
    Baton Rouge Parish" for an Orleans neighbourhood - the same token bug the
    09-28 sweep removed from portfolio_sim ($10,880 vs $13,199 of tax, 17.6%
    understated), reintroduced inside latax by the 10-05 F4 fix. The record
    `central-city-2nd` is a real pipeline entry, and this is the one Orleans
    neighbourhood that asserted a FALSE parish with a confident note rather
    than degrading to None.

    Punctuation was the other half: "Baton Rouge: 5555 Jefferson Hwy" tokenized
    as ["baton", "rouge:", ...], so the two-word city could not match and the
    stray "jefferson" won.
    """
    import latax
    import defaults

    for loc in ("Central City", "central city", "1234 Second St, Central City, LA",
                "Central City, New Orleans"):
        par, why = latax.infer_parish(loc)
        check(f"nbhd: {loc!r} -> Orleans", par == "orleans", f"got {par!r} ({why})")
        ins = defaults.derive("insurance", 8, loc)
        ins = ins[0] if isinstance(ins, tuple) else ins
        check(f"nbhd: {loc!r} takes the $3,000 wind basis", ins == 3000, f"got {ins!r}")

    # The genuine East Baton Rouge town of Central must not move.
    par, _ = latax.infer_parish("central")
    check("nbhd: the EBR town 'central' still resolves to East Baton Rouge",
          par == "east baton rouge", f"got {par!r}")

    # Punctuation must not weld itself to a token, and a zip+4 must survive.
    for loc, want in (("Baton Rouge: 5555 Jefferson Hwy", "east baton rouge"),
                      ("Baton Rouge (5555 Jefferson Hwy)", "east baton rouge"),
                      ("Baton Rouge, LA 70806-1234", "east baton rouge"),
                      ("Plaquemines Parish, LA 70037", "plaquemines")):
        got, _ = latax.infer_parish(loc)
        check(f"nbhd: {loc!r} -> {want}", got == want, f"got {got!r}")


def test_board_does_not_assert_a_verdict_inside_the_coin_flip_band():
    """The board published "clears the house rule" on eden's $1,443,000 rung -
    the price its own copy calls the strong-deal price - and that label is an
    artifact of the hardcoded n=1000, seed 42.

    Reproduced 2026-10-12 with the real generator, only `n` changed:

        16% rung $1,443,000   n=1000 seed42 = 50.16%  -> "clears"
                              n=10,000 x 12 seeds: mean 49.30% [48.82-50.08]
                              clears >50% in 1 of 12
        13% rung $1,559,000   n=1000 seed42 = 42.23%; n=10k mean 41.43%

    Every committed render since 10-05 carries "beats index 50.2% - clears the
    house rule" on that rung, while the 10-05 report's own Reporting table
    printed "eden 16% 49.0% FAILS" the same day. So that sweep's deferred claim
    that raising n flips no verdict is false for the fix it recommends.

    Raising the draw count is the real fix and is deferred with those numbers.
    What this pins is narrower and costs no draws: inside the +/-3pp band the
    page may not assert EITHER verdict. It used to print "clears" and then
    append a line calling the same number a coin flip.
    """
    import re
    import board

    for b in (0.5016, 0.4930, 0.52, 0.4701):
        out = board.rung(0.16, "strong", {0.16: 1_443_000}, {0.16: b})
        check(f"coinflip: b={b} does not claim it clears",
              "clears the house rule" not in out, out[-160:])
        check(f"coinflip: b={b} does not claim it FAILS either",
              "FAILS the house" not in out, out[-160:])
        check(f"coinflip: b={b} says it is too close to call",
              "too close to call" in out, out[-160:])
        check(f"coinflip: b={b} still prints the number to one decimal",
              f"{b*100:.1f}%" in out, out[-160:])

    # Outside the band both verdicts must still be stated plainly.
    out_hi = board.rung(0.16, "strong", {0.16: 1_443_000}, {0.16: 0.62})
    check("coinflip: a clear pass still reads 'clears the house rule'",
          "clears the house rule" in out_hi and "too close" not in out_hi)
    out_lo = board.rung(0.16, "strong", {0.16: 1_443_000}, {0.16: 0.42})
    check("coinflip: a clear fail still reads 'FAILS the house rule'",
          "FAILS the house" in out_lo and "too close" not in out_lo)

    # And a missing score must stay silent rather than invent a verdict.
    out_none = board.rung(0.16, "strong", {0.16: 1_443_000}, {0.16: None})
    check("coinflip: an unscored rung asserts nothing",
          "clears" not in out_none and "FAILS" not in out_none
          and "too close" not in out_none)


def test_rates_persists_a_baseline_and_diffs_against_it():
    """Every daily scan from 2026-09-29 to 10-12 recorded the same finding:
    rates.py printed live figures and stored nothing, so the routine's step-2
    question - "has the 10yr moved >25bps since the last snapshot" - was
    unanswerable BY CONSTRUCTION, there being no snapshot to diff against.

    This pins the persistence half, which is testable without FRED:
      - a fresh history reads as empty rather than raising
      - an appended row is read back, and is the one `last_snapshot` returns
      - `drift` reports bps deltas signed live-minus-stored
      - a series absent from the stored row yields no fabricated delta
      - a corrupt line does not stop the good rows being read
    """
    import json
    import os
    import tempfile

    import rates

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "rates.jsonl")

        # No file yet: empty history, no baseline, and no exception.
        check("rates: a missing history file reads as empty",
              rates.read_history(path) == [])
        check("rates: a missing history file has no baseline",
              rates.last_snapshot(path) is None)

        rates.append_snapshot(
            {"observed": "2026-08-17",
             "rates": {"DPRIME": 6.75, "DGS10": 4.68, "SOFR": 3.62},
             "source": "FRED fredgraph.csv"}, path)
        check("rates: an appended row is read back",
              len(rates.read_history(path)) == 1)
        check("rates: the appended row is the baseline",
              (rates.last_snapshot(path) or {}).get("observed") == "2026-08-17")

        # Newest-by-date wins even when appended out of order.
        rates.append_snapshot(
            {"observed": "2026-07-01",
             "rates": {"DPRIME": 7.00, "DGS10": 4.20, "SOFR": 3.90}}, path)
        check("rates: the newest row by date is the baseline, not the last line",
              (rates.last_snapshot(path) or {}).get("observed") == "2026-08-17")

        # A corrupt line must not hide the good rows.
        with open(path, "a") as fh:
            fh.write("{not json at all\n")
        check("rates: a corrupt line does not lose the good rows",
              len(rates.read_history(path)) == 2)

        prev = rates.last_snapshot(path)

    # Signed deltas, in bps, live minus stored.
    live = {"DPRIME": 6.75, "DGS10": 4.95, "SOFR": 3.50}
    by_series = {d[0]: d for d in rates.drift(prev, live)}
    check("rates: an unchanged series reads 0bps",
          abs(by_series["DPRIME"][3]) < 1e-9, str(by_series["DPRIME"]))
    check("rates: a 27bps rise in the 10yr is reported as +27bps",
          abs(by_series["DGS10"][3] - 27.0) < 1e-6, str(by_series["DGS10"]))
    check("rates: a fall is reported negative",
          by_series["SOFR"][3] < 0, str(by_series["SOFR"]))
    check("rates: a 27bps move exceeds the routine's 25bps flag",
          abs(by_series["DGS10"][3]) > rates.DRIFT_FLAG_BPS)

    # A series the stored row never carried must not produce a delta of 0.
    thin = {"observed": "2026-08-17", "rates": {"DPRIME": 6.75}}
    thin_drift = {d[0]: d for d in rates.drift(thin, live)}
    check("rates: a series missing from the stored row yields no delta",
          thin_drift["DGS10"][1] is None and thin_drift["DGS10"][3] is None,
          str(thin_drift["DGS10"]))

    # Fail-closed: the fetch path still raises rather than substituting a
    # stored figure for a live one.
    rates_src = open(os.path.join(os.path.dirname(rates.__file__),
                                  "rates.py")).read()
    fetch = rates_src[rates_src.index("def latest("):rates_src.index("def read_history(")]
    check("rates: a failed fetch still raises instead of falling back",
          "SystemExit" in fetch
          and "last_snapshot" not in fetch and "read_history" not in fetch)


def test_a_renovation_with_no_pace_does_not_pass_silently():
    """Sweep 2026-10-19. `reno_units > 0` with `reno_units_per_year == 0` spends
    the entire renovation budget at close and renovates NOTHING:
    _reno_units_cumulative (pymodel.py:623) short-circuits to 0.0 on a zero
    pace, so no unit is ever turned and no rent premium is ever captured.

    Reproduced on the master acquisition workbook's own demo, recalculated
    through LibreOffice Calc, 8 units at $15,000 = $120,000 of budget:

        pace 4/yr   levered IRR  10.40%   (workbook and engine agree to 0bps)
        pace 0/yr   levered IRR  -3.32%   (workbook and engine agree to 0bps)

    The workbook cannot catch it either - its "Renovation finishes inside the
    hold period" check reads OK because Years to Complete,
    IFERROR(ROUNDUP(F31/F34,0),0), collapses to 0 on the divide-by-zero. So a
    mis-specified pace reads as "the value-add does not work" rather than as bad
    input. The money is still charged, so the IRR is not wrong - it is
    unexplained, and that is what this pins.
    """
    import io
    import contextlib

    inputs = dict(base_inputs(), reno_units=8, reno_cost_per_unit=15_000,
                  reno_premium_month=150, reno_units_per_year=0,
                  exit_cap=None)

    pymodel._reset_warnings()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = pymodel.run(dict(inputs))
    msg = err.getvalue()
    check("reno: a 0/yr pace with units to renovate warns",
          "0 units/year" in msg, f"stderr was {msg!r}")
    check("reno: the warning names the budget that is spent for nothing",
          "120,000" in msg, f"stderr was {msg!r}")

    # And the condition it describes is real: nothing gets renovated.
    check("reno: a 0/yr pace renovates no units",
          pymodel._reno_units_cumulative(5, 8, 0, 1) == 0.0,
          f"got {pymodel._reno_units_cumulative(5, 8, 0, 1)}")

    # A real pace must stay silent and must actually renovate.
    pymodel._reset_warnings()
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        pymodel.run(dict(inputs, reno_units_per_year=4))
    check("reno: a real pace does not warn",
          "0 units/year" not in err2.getvalue(), err2.getvalue())
    check("reno: a 4/yr pace renovates all 8 units by year 2",
          pymodel._reno_units_cumulative(2, 8, 4, 1) == 8.0,
          f"got {pymodel._reno_units_cumulative(2, 8, 4, 1)}")

    # Zero units to renovate is not a mis-specification; it must stay silent.
    pymodel._reset_warnings()
    err3 = io.StringIO()
    with contextlib.redirect_stderr(err3):
        pymodel.run(dict(inputs, reno_units=0, reno_cost_per_unit=0,
                         reno_premium_month=0))
    check("reno: no renovation at all does not warn",
          "0 units/year" not in err3.getvalue(), err3.getvalue())


def test_a_town_inside_a_street_name_is_never_the_locality():
    """Sweep 2026-10-19. 10-12's street-suffix demotion looked only at the token
    immediately after the match, which misses the head of a longer town name,
    and it only RANKED candidates rather than disqualifying them.

    Three defects, all reproduced, all asserting a parish as fact:

      'Gretna - 1 Central City Rd'  -> east baton rouge   (want jefferson)
          'central' is followed by 'city', not a suffix, so it scored clean and
          beat Gretna on end_index. On treme-gov-nicholls the same Orleans->EBR
          slip moves the 13% rung $714,000 -> $817,000, $103,000 overstated.
      '100 Harvey Ln'               -> jefferson          (want None)
          every candidate sat inside a street name, and the best demoted one was
          still returned - with the $3,000/unit coastal insurance basis.
          '100 Baker Rd & Harvey Ln' -> jefferson vs
          '100 Harvey Ln & Baker Rd' -> east baton rouge: $132,000 of clearing
          price from token order alone.
      '1234 Jefferson Davis Pkwy, New Orleans, LA 70119' -> None (want orleans)
          the confusable-parish guard bailed before any town matching, so a real
          Orleans address froze taxes across the solve and put the 13% rung at
          $748,000 against a correct $522,000.
    """
    import latax

    # 1. The head of a longer town name is still part of the street name.
    for loc, want in (("Gretna - 1 Central City Rd", "jefferson"),
                      ("Chalmette - 1 Central City Rd", "st. bernard"),
                      ("Metairie - 100 Central City Rd", "jefferson")):
        got, why = latax.infer_parish(loc)
        check(f"locality: {loc!r} -> {want}", got == want, f"got {got!r} ({why})")

    # 2. All candidates demoted means the location is unresolved, not a guess.
    for loc in ("100 Harvey Ln", "1234 Harvey Ln, Apt 3",
                "100 Baker Rd & Harvey Ln", "100 Harvey Ln & Baker Rd"):
        got, why = latax.infer_parish(loc)
        check(f"locality: {loc!r} resolves to nothing rather than guessing",
              got is None, f"got {got!r} ({why})")
    # ... and the answer no longer depends on which street was typed first.
    a, _ = latax.infer_parish("100 Baker Rd & Harvey Ln")
    b, _ = latax.infer_parish("100 Harvey Ln & Baker Rd")
    check("locality: swapping two street names cannot change the parish",
          a == b, f"got {a!r} vs {b!r}")

    # 3. A confusable parish in a STREET name must not veto the real city.
    for loc, want in (("1234 Jefferson Davis Pkwy, New Orleans, LA 70119",
                       "orleans"),
                      ("3100 Jefferson Davis Pkwy, New Orleans", "orleans")):
        got, why = latax.infer_parish(loc)
        check(f"locality: {loc!r} -> {want}", got == want, f"got {got!r} ({why})")
    # But a genuine Jefferson Davis Parish location still resolves to nothing.
    got, _ = latax.infer_parish("Jennings, Jefferson Davis, LA")
    check("locality: a real Jefferson Davis Parish town stays unresolved",
          got is None, f"got {got!r}")

    # Every case the earlier passes pinned must still hold.
    for loc, want in (("1 Central City Rd, Baker, LA", "east baton rouge"),
                      ("100 Baton Rouge Ave, Gonzales, LA", "ascension"),
                      ("Baton Rouge - 5555 Jefferson Hwy", "east baton rouge"),
                      ("5555 Jefferson Hwy, Baton Rouge", "east baton rouge"),
                      ("Central Ave, Metairie", "jefferson"),
                      ("1 Gretna Blvd, Hammond, LA", "tangipahoa"),
                      ("Chalmette, St. Bernard Parish", "st. bernard"),
                      ("central", "east baton rouge"),
                      ("new orleans", "orleans")):
        got, why = latax.infer_parish(loc)
        check(f"locality: {loc!r} still -> {want}", got == want,
              f"got {got!r} ({why})")
    check("locality: a non-Louisiana state still wins",
          latax.infer_parish("Jefferson, TX")[0] is None)


def test_the_board_number_and_its_verdict_agree_at_every_boundary():
    """Sweep 2026-10-19. 10-12 added a +/-3pp coin-flip band tested on raw `b`
    while every branch prints `b*100:.1f`, so the display/verdict split that
    rung()'s own comment says was fixed at 50.0% simply moved to the new edges:

        b=0.46995 -> "beats index only 47.0% - FAILS the house rule"
        b=0.47005 -> "beats index 47.0% - too close to call"
        b=0.52995 -> "beats index 53.0% - too close to call"
        b=0.53005 -> "beats index 53.0% - clears the house rule"

    Same printed number, opposite assertions, on a pursue/don't-pursue call.
    """
    import re
    import board

    def verdict(out):
        txt = re.sub("<[^>]+>", "", out)
        for phrase in ("clears the house rule", "FAILS the house",
                       "too close to call"):
            if phrase in txt:
                return phrase
        return "none"

    for lo, hi in ((0.46995, 0.47005), (0.52995, 0.53005),
                   (0.49995, 0.50005)):
        out_lo = board.rung(0.16, "s", {0.16: 1_443_000}, {0.16: lo})
        out_hi = board.rung(0.16, "s", {0.16: 1_443_000}, {0.16: hi})
        shown_lo = re.search(r"(\d+\.\d)%", re.sub("<[^>]+>", "", out_lo))
        shown_hi = re.search(r"(\d+\.\d)%", re.sub("<[^>]+>", "", out_hi))
        if shown_lo and shown_hi and shown_lo.group(1) == shown_hi.group(1):
            check(f"boundary: {shown_lo.group(1)}% cannot carry two verdicts",
                  verdict(out_lo) == verdict(out_hi),
                  f"{lo}->{verdict(out_lo)!r} vs {hi}->{verdict(out_hi)!r}")

    # The three verdicts must still be reachable and correctly placed.
    cases = [(0.0, "FAILS the house"), (0.42, "FAILS the house"),
             (0.469, "FAILS the house"), (0.48, "too close to call"),
             (0.50, "too close to call"), (0.52, "too close to call"),
             (0.54, "clears the house rule"), (1.0, "clears the house rule")]
    for b, want in cases:
        out = board.rung(0.16, "s", {0.16: 1_443_000}, {0.16: b})
        check(f"boundary: b={b} reads {want!r}", verdict(out) == want,
              f"got {verdict(out)!r}")
    out_none = board.rung(0.16, "s", {0.16: 1_443_000}, {0.16: None})
    check("boundary: an unscored rung still asserts nothing",
          verdict(out_none) == "none")


def test_the_dscr_verdict_matches_the_dscr_it_prints():
    """Sweep 2026-10-19. report.mult() prints 2dp but dscr_verdict compared the
    raw float, so 1.29996 and 1.30004 both displayed "1.30x" while carrying
    opposite verdicts ("FAILS lender minimum 1.30" vs "binds at lender minimum
    1.30"). irr_verdict six lines above already rounds for exactly this reason.
    DSCR-bound deals land on the minimum by construction, so this is the one
    verdict guaranteed to sit on its own boundary.
    """
    import report

    for d in (1.29996, 1.30004):
        shown = report.mult(d)
        check(f"dscr: {d} displays as {shown}", shown == "1.30x", shown)
    check("dscr: the two sides of 1.30 that print the same cannot disagree",
          report.dscr_verdict(1.29996, 1.30) == report.dscr_verdict(1.30004, 1.30),
          f"{report.dscr_verdict(1.29996, 1.30)!r} vs "
          f"{report.dscr_verdict(1.30004, 1.30)!r}")
    # A genuine miss and a genuine clear must still read correctly.
    check("dscr: 1.24 still fails a 1.30 minimum",
          "FAILS" in report.dscr_verdict(1.24, 1.30))
    check("dscr: 1.31 still binds at a 1.30 minimum",
          "binds" in report.dscr_verdict(1.31, 1.30))
    check("dscr: 1.40 still clears a 1.30 minimum",
          "clears" in report.dscr_verdict(1.40, 1.30))
    check("dscr: a missing DSCR is still reported as missing",
          report.dscr_verdict(None, 1.30) == "NO DSCR")


def test_rates_drift_survives_a_second_run_and_a_poisoned_row():
    """Sweep 2026-10-19, two defects in the persistence added on 10-12.

    1. append_snapshot stamps `observed` with TODAY and last_snapshot returned
       the newest row by date, so run 2 of any day diffed today against itself:
       0bps on every series, no DRIFT line, exit 0. A stored 10-18 DGS10 of
       4.60 against a live 5.00 flagged +40bps on run 1 and nothing on run 2
       with identical data; 20bps/day for five days (4.60 -> 5.60, +100bps) gave
       exit codes 1,0,0,0,1. Nothing in defaults.py references DGS10 or SOFR, so
       for those two series this flag is the only detector there is.
    2. read_history skipped only non-JSON lines. A row that parsed with the
       wrong TYPES crashed the caller after the live fetch and before the
       append, so one half-written line discarded every later pull.
    """
    import json
    import os
    import tempfile

    import rates

    today = "2026-10-19"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rates.jsonl")
        rates.append_snapshot({"observed": "2026-10-18",
                               "rates": {"DPRIME": 6.75, "DGS10": 4.60,
                                         "SOFR": 3.62}}, path)
        # Today's own pull lands in the history...
        rates.append_snapshot({"observed": today,
                               "rates": {"DPRIME": 6.75, "DGS10": 5.00,
                                         "SOFR": 3.62}}, path)
        # ...and must not become its own baseline on the next run of the day.
        base = rates.last_snapshot(path, before=today)
        check("rates: a second run of the day diffs against a PREVIOUS day",
              (base or {}).get("observed") == "2026-10-18",
              f"got {(base or {}).get('observed')!r}")
        live = {"DPRIME": 6.75, "DGS10": 5.00, "SOFR": 3.62}
        d = {x[0]: x for x in rates.drift(base, live)}
        check("rates: the 40bps 10-yr move is still visible on run 2",
              abs(d["DGS10"][3] - 40.0) < 1e-6, str(d["DGS10"]))
        check("rates: and it still exceeds the flag threshold",
              abs(d["DGS10"][3]) > rates.DRIFT_FLAG_BPS)
        # Without the exclusion the baseline is today itself and the move is 0.
        self_base = rates.last_snapshot(path)
        d_self = {x[0]: x for x in rates.drift(self_base, live)}
        check("rates: diffing against today's own row is what used to hide it",
              abs(d_self["DGS10"][3]) < 1e-9, str(d_self["DGS10"]))

        # Rows that parse but carry the wrong types cost their own row only.
        good = len(rates.read_history(path))
        with open(path, "a") as fh:
            for bad in ('{"observed":"2026-10-20","rates":"6.75"}',
                        '{"observed":"2026-10-20","rates":[6.75]}',
                        '{"observed":"2026-10-20","rates":null}',
                        '{"observed":20261018,"rates":{"DPRIME":6.75}}',
                        '{"observed":null,"rates":{"DPRIME":6.75}}',
                        '{not json at all'):
                fh.write(bad + "\n")
        check("rates: schema-corrupt rows are skipped, not fatal",
              len(rates.read_history(path)) == good,
              f"got {len(rates.read_history(path))} want {good}")
        # The whole point: a poisoned file must not stop a diff from running.
        base2 = rates.last_snapshot(path, before=today)
        check("rates: a poisoned file still yields the real baseline",
              (base2 or {}).get("observed") == "2026-10-18",
              f"got {(base2 or {}).get('observed')!r}")

    # HISTORY_PATH was bound as a default argument, so repointing the module
    # global was silently ignored and writes landed in the repo (an auditor put
    # 9 fabricated rows into market-data/rates.jsonl this way before noticing).
    import inspect
    for fn in (rates.read_history, rates.last_snapshot, rates.append_snapshot):
        sig = inspect.signature(fn)
        check(f"rates: {fn.__name__} does not freeze HISTORY_PATH at import",
              sig.parameters["path"].default is None,
              f"default is {sig.parameters['path'].default!r}")
    with tempfile.TemporaryDirectory() as tmp:
        moved = os.path.join(tmp, "moved.jsonl")
        orig = rates.HISTORY_PATH
        try:
            rates.HISTORY_PATH = moved
            rates.append_snapshot({"observed": "2026-01-01", "rates": {}})
            check("rates: repointing HISTORY_PATH actually moves the writes",
                  os.path.exists(moved) and not os.path.exists(orig),
                  f"moved={os.path.exists(moved)} repo={os.path.exists(orig)}")
        finally:
            rates.HISTORY_PATH = orig


def test_the_board_shows_his_own_return_not_only_the_deals():
    """Sweep 2026-10-19. The "Return at ask" tile printed `levered_irr`, the
    whole-equity return, while CLAUDE.md's house rule is his own sentence about
    HIS money. Every live deal carries lp_pct 0.90, an 8% pref and a 70/30
    residual, so on a thin deal the pref consumes the distributions:

        treme  project +6.565%   GP -4.431%   (the page showed 6.6%)
        baker  project +2.687%   GP None - equity never turns positive
        eden   project +9.871%   GP +8.384%

    waterfall_neg_years is EMPTY on all three deterministic runs, so these are
    quotable rather than disclaimed. treme is the deal the record calls the only
    one that fits his capital with no carry and no partner.
    """
    import board

    out = board.gp_share_sub({"gp_irr": -0.044309, "wf_neg_years": []})
    check("gp: a negative GP return is shown, not hidden behind the project IRR",
          "-4.4%" in out, out)
    out_none = board.gp_share_sub({"gp_irr": None, "wf_neg_years": []})
    check("gp: a wiped GP position says so rather than printing n/a",
          "wiped" in out_none, out_none)
    out_pos = board.gp_share_sub({"gp_irr": 0.08384, "wf_neg_years": []})
    check("gp: a positive GP return prints its own figure",
          "8.4%" in out_pos, out_pos)
    # A deficit year makes the split invalid; the tile must not quote it.
    out_bad = board.gp_share_sub({"gp_irr": 0.25, "wf_neg_years": [3]})
    check("gp: a waterfall deficit year is not quoted as a GP return",
          "not quotable" in out_bad and "25" not in out_bad, out_bad)

    # And the engine really does return a GP figure below the project figure on
    # the live deals - this is the condition the tile exists to surface.
    for deal, want_gap in (("treme-gov-nicholls", True),
                           ("eden-church-mhp", True)):
        pymodel._reset_warnings()
        r = pymodel.run(dict(pymodel._load_deal(deal)))
        check(f"gp: {deal} deterministic waterfall is valid (quotable)",
              not r.get("waterfall_neg_years"),
              f"neg_years={r.get('waterfall_neg_years')}")
        check(f"gp: {deal} GP return is below the whole-deal return",
              (r.get("gp_irr") is None or r["gp_irr"] < r["levered_irr"]),
              f"gp={r.get('gp_irr')} project={r['levered_irr']}")


def test_the_engine_refuses_inputs_that_invent_money():
    """Sweep 2026-10-19. `exit_cap <= 0` was the only sanity check, so a cluster
    of neighbouring inputs each produced a confident, unwarned, materially wrong
    answer. All reproduced on baker-trails (12 units) before the fix:

        vacancy -0.05        IRR  2.6869% -> 21.2131%  (+18.53pp), no warning
        reno_units 30 / 12   IRR  2.6869% -> 12.3200%  (+9.63pp), $360,000 of
                             budget and 18 renovations of units that do not
                             exist - nearly clearing the 13% pursue floor
        reno_start_year -3   IRR  9.8986% -> 11.4632%  (+1.56pp)
        exit_cap 0.0001      sale $522,044,007, IRR 337.7706%, EM 1595x
        hold_years 0 / 5.5   KeyError / TypeError
        min_dscr 0           ZeroDivisionError

    The last three were raised from inside monte_carlo's per-draw try/except as
    *excluded draws*, which truncates the tail rather than stopping the run -
    so they were worse than a crash, not better. The workbook already refused
    the reno-unit case at Checks!B12; the Python twin did not.
    """
    import pymodel as pm

    def refuses(label, **over):
        try:
            pm.run({**base_inputs(), "exit_cap": None, **over})
        except ValueError as e:
            check(f"refuse: {label}", True)
            return str(e)
        except Exception as e:
            check(f"refuse: {label} (raised {type(e).__name__}, want ValueError)",
                  False, str(e)[:120])
            return ""
        check(f"refuse: {label}", False, "no exception - the run was accepted")
        return ""

    msg = refuses("a negative vacancy", vacancy=-0.05)
    check("refuse: the vacancy message says what the range is",
          "[0, 1)" in msg, msg[:140])
    refuses("a vacancy of 1.0 (no occupied unit)", vacancy=1.0)

    units = sum(g["units"] for g in base_inputs()["unit_mix"])
    msg = refuses(f"{units + 18:g} renovations on a {units:g}-unit property",
                  reno_units=units + 18, reno_cost_per_unit=12_000,
                  reno_premium_month=150, reno_units_per_year=6)
    check("refuse: the reno message names the property's real unit count",
          f"{units:g} units" in msg, msg[:160])

    refuses("a renovation back-dated before close", reno_units=4,
            reno_cost_per_unit=12_000, reno_premium_month=150,
            reno_units_per_year=2, reno_start_year=-3)
    refuses("a near-zero exit cap", exit_cap=0.0001)
    refuses("an absurd exit cap", exit_cap=0.95)
    refuses("a zero min_dscr", min_dscr=0)
    refuses("a zero hold", hold_years=0)
    refuses("a fractional hold", hold_years=5.5)
    refuses("a hold past the workbook's last column", hold_years=15)

    # The legitimate neighbours of every bound above must still run.
    for label, over in (("vacancy 0%", {"vacancy": 0.0}),
                        ("vacancy 35%", {"vacancy": 0.35}),
                        ("the full property renovated",
                         {"reno_units": units, "reno_cost_per_unit": 12_000,
                          "reno_premium_month": 150, "reno_units_per_year": 6}),
                        ("reno starting in year 1",
                         {"reno_units": 4, "reno_cost_per_unit": 12_000,
                          "reno_premium_month": 150, "reno_units_per_year": 2,
                          "reno_start_year": 1}),
                        ("a 1-year hold", {"hold_years": 1}),
                        ("a 10-year hold", {"hold_years": 10}),
                        ("exit cap 1%", {"exit_cap": 0.01}),
                        ("exit cap 30%", {"exit_cap": 0.30}),
                        ("the house DSCR", {"min_dscr": 1.30})):
        try:
            r = pm.run({**base_inputs(), "exit_cap": None, **over})
            check(f"allow: {label} still runs", r.get("levered_irr") is not None
                  or r.get("noi") is not None)
        except Exception as e:
            check(f"allow: {label} still runs", False,
                  f"{type(e).__name__}: {e}")

    # And every live deal must still pass its own validation unchanged.
    for deal in ("eden-church-mhp", "treme-gov-nicholls", "baker-trails"):
        try:
            pm.run(dict(pm._load_deal(deal)))
            check(f"allow: {deal} runs as carried", True)
        except Exception as e:
            check(f"allow: {deal} runs as carried", False,
                  f"{type(e).__name__}: {e}")


def test_ingestion_does_not_drop_rows_or_zeros_in_silence():
    """Sweep 2026-10-19, three ingestion paths that produced a confident wrong
    number with no flag. tools/README.md promises "Nothing is silently
    dropped"; all three broke that promise, and all three were workbook-
    verified by writing through safe_writer into a copy of the master and
    recalculating.

    1. A rent outside $200-$20,000 dropped the row with NO note. A 30-pad park
       with 8 pads on legacy $185 lot rents parsed as 22 units: equity $622,648
       against a true $509,838 ($112,810 overstated), price-per-unit $40,909
       against $30,000. Sub-$200 pad rents are routine in rural Louisiana parks
       and MHP is the stated #1 target class.
    2. A count cell that was present but unparseable silently counted as 1
       unit. "# of Units" of 24 then blank parsed as 25 units instead of 36;
       "24*" parsed as 13. Equity overstated $953,738, IRR off 8.76 points,
       zero notes either time.
    3. A T-12 line parsing to 0 was written as a real $0 expense AND marked as
       taken from the deal's own documents, so defaults.derive never fired -
       and compare_to_benchmarks gated its deviation test on `pu > 0`, so the
       most extreme value possible printed a blank deviation and a blank flag.
       On a 12-unit Gretna deal at $950,000: NOI $92,546 vs $56,546, going-in
       cap 9.74% vs 5.95%, equity understated $185,991, levered IRR +14.97% vs
       +1.45% - a 13.52-point swing across the 13% pursue floor, with 0 formula
       errors and 12/12 checks OK.
    """
    import os
    import tempfile

    import cellmap
    import parsers

    with tempfile.TemporaryDirectory() as tmp:
        low = os.path.join(tmp, "roll_lowrent.csv")
        with open(low, "w") as fh:
            fh.write("Unit,Unit Type,Rent\n1,3BR/2BA,185\n2,3BR/2BA,185\n"
                     "3,3BR/2BA,425\n4,3BR/2BA,425\n")
        groups, notes = parsers.parse_rent_roll(low)[:2]
        units = sum(g["units"] for g in groups)
        check("ingest: an out-of-range rent row is still excluded from the count",
              units == 2, f"got {units}")
        dropped = [n for n in notes if "DROPPED" in n]
        check("ingest: and the drop is reported rather than silent",
              len(dropped) == 1, f"notes were {notes}")
        check("ingest: the drop note names the count and the bounds",
              "2 row(s)" in dropped[0] and "$200" in dropped[0], dropped[0])

        bad = os.path.join(tmp, "roll_badcount.csv")
        with open(bad, "w") as fh:
            fh.write("Unit Type,# of Units,Rent\n2BR/1BA,24,850\n"
                     "1BR/1BA,24*,700\n3BR/2BA,,950\n")
        groups2, notes2 = parsers.parse_rent_roll(bad)[:2]
        check("ingest: an unparseable count cell is reported",
              any("UNPARSEABLE count" in n for n in notes2), f"notes {notes2}")
        check("ingest: a blank count cell is reported",
              any("BLANK count" in n for n in notes2), f"notes {notes2}")
        check("ingest: the unparseable-count note names the offending value",
              any("'24*'" in n for n in notes2), f"notes {notes2}")

    # A zero or negative expense line must carry a flag, not a blank.
    bm = {k: v for k, v in cellmap.BENCHMARKS.items()
          if k in ("insurance", "utilities", "rm")}
    rows = parsers.compare_to_benchmarks(
        {"insurance": {"annual": 0.0}, "utilities": {"annual": -600.0},
         "rm": {"annual": 6180.0}}, 12, bm)
    by = {r["key"]: r for r in rows}
    check("ingest: a $0 expense line is flagged ZERO, not left blank",
          by["insurance"]["flag"] == "ZERO", str(by["insurance"]))
    check("ingest: a negative expense line is flagged NEGATIVE",
          by["utilities"]["flag"] == "NEGATIVE", str(by["utilities"]))
    check("ingest: an on-benchmark line is still unflagged",
          by["rm"]["flag"] == "", str(by["rm"]))

    # intake must refuse to --apply a T-12 that parsed to nothing.
    src = open(os.path.join(os.path.dirname(parsers.__file__), "intake.py")).read()
    check("ingest: intake aborts on a supplied T-12 that parsed zero lines",
          "ZERO expense lines were parsed" in src)
    check("ingest: and that gate only bites under --apply",
          "not t12_lines and args.apply" in src)

    # The live deal #1 rent roll must be unaffected by any of this.
    import pymodel
    r = pymodel.run(dict(pymodel._load_deal("eden-church-mhp")))
    check("ingest: eden still reproduces its carried NOI",
          abs(r["noi"][1] - 143_474) < 500, f"got {r['noi'][1]:,.0f}")


if __name__ == "__main__":
    main()
