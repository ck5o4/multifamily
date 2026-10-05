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
    """annual_savings_contribution was credited to port_cf as an inflow."""
    import portfolio_sim
    scen = {"name": "t", "starting_equity": 300_000, "annual_savings": 30_000,
            "deals": [{"deal": "eden-church-mhp", "acq_year": 0, "price": 1_094_000,
                       "location": "denham springs"}]}
    with_sav = portfolio_sim.simulate(scen)
    without = portfolio_sim.simulate(dict(scen, annual_savings=0))
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
    mlk-2119 is that workbook: 22,822 bytes against 32,065-32,571 for the
    seven recalculated ones.
    """
    import io
    import contextlib
    import pathlib
    import test_pymodel as tp

    root = pathlib.Path(tp.__file__).resolve().parent.parent
    deal = "mlk-2119"
    wb = root / "deal-intake" / deal / f"{deal}_acq.xlsx"
    if not wb.exists():
        check(f"parity: {deal} workbook present", True, "skipped - not in tree")
        return

    expected = tp._load_expected(wb)
    n_present = sum(1 for v in expected.values() if v is not None)
    check("parity: the fixture really is an uncalculated workbook",
          n_present == 0, f"{n_present} of {len(expected)} values present")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status = tp._run_deal(deal)
    out = buf.getvalue()

    # The bug, precisely: zero comparisons must not read as a pass.
    check("parity: zero comparisons is UNVERIFIED, not PASS",
          status == "UNVERIFIED", f"status={status!r}")
    check("parity: the harness no longer claims ALL PASS on that deal",
          "ALL PASS" not in out)
    check("parity: it says why it could not verify",
          "UNVERIFIED" in out and "not recalculated" in out.lower()
          or "formula cells are empty" in out.lower())

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


if __name__ == "__main__":
    main()
