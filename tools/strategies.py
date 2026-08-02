"""Strategy comparison: hold vs refi-and-hold vs sell, side by side.

Runs the same deal through every exit path and prints IRR, total profit, and
equity multiple for each. IRR and dollars disagree on short holds - a quick
flip can show a high IRR on a small profit - so both are always shown.

The Stoa pattern (sell at year 2-3 into a hot market) appears as one labeled
scenario priced at a compressed cap. It is shown as UPSIDE, never as the base
case: market timing is a hope, not an underwriting input.
"""

import shutil

import recalc as rc
from safe_writer import ModelWriter


def _run(model_path, spec, work, sets):
    shutil.copy2(model_path, work)
    w = ModelWriter(work, spec)
    for k, v in sets.items():
        w.set(spec["scalars"][k], v)
    w.save()
    rc.recalc(work)
    v = rc.read_values(work, {
        "irr": spec["outputs"]["levered_irr"],
        "em": spec["outputs"]["equity_multiple"],
        "profit": spec["outputs"]["total_profit"],
        "lp_irr": spec["outputs"]["lp_irr"],
        "cash_out": spec["outputs"]["refi_cash_out"],
        "going_in": spec["outputs"]["going_in_cap"],
    })
    errs = rc.scan_errors(work)
    bad = [l for l, s in rc.read_checks(work, spec) if s != "OK"]
    return v, errs, bad


def compare(deal_workbook, spec, lp_capital=None):
    """Sweep exit strategies on an already-built deal workbook."""
    work = deal_workbook.parent / "__strategy_work.xlsx"
    base_v, _, _ = _run(deal_workbook, spec, work, {})
    going_in = base_v["going_in"] or 0.075
    honest_cap = round(going_in + 0.005, 5)

    scenarios = [
        ("Sell year 2",              {"hold_years": 2, "refi_year": 0, "exit_cap": honest_cap}),
        ("Sell year 3",              {"hold_years": 3, "refi_year": 0, "exit_cap": honest_cap}),
        ("Sell year 5",              {"hold_years": 5, "refi_year": 0, "exit_cap": honest_cap}),
        ("Refi yr 3, sell year 5",   {"hold_years": 5, "refi_year": 3, "exit_cap": honest_cap}),
        ("Refi yr 3, sell year 7",   {"hold_years": 7, "refi_year": 3, "exit_cap": honest_cap}),
        ("Refi yr 3, hold to yr 10", {"hold_years": 10, "refi_year": 3, "exit_cap": honest_cap}),
    ]

    print("\n  STRATEGY COMPARISON (exit cap held honest at "
          f"{honest_cap*100:.2f}% = going-in + 50bps)")
    print(f"    {'Path':<26}{'IRR':>8}{'Profit':>12}{'Mult':>7}{'Refi cash':>11}  Flags")
    results = []
    for label, sets in scenarios:
        v, errs, bad = _run(deal_workbook, spec, work, sets)
        irr = v["irr"] if isinstance(v["irr"], (int, float)) else None
        flag = "ERRORS" if errs else ("; ".join(b[:28] for b in bad[:1]) if bad else "")
        co = v["cash_out"] or 0
        results.append((label, irr, v["profit"], v["em"], co))
        print(f"    {label:<26}"
              f"{(irr*100 if irr is not None else 0):>7.1f}%"
              f"{'$' + format(int(v['profit'] or 0), ','):>12}"
              f"{(v['em'] or 0):>6.2f}x"
              f"{('$' + format(int(co), ',')) if co else '-':>11}  {flag}")

    # Stoa timing scenario - hot market bid, labeled as upside only
    hot_cap = max(0.06, honest_cap - 0.015)
    v, _, _ = _run(deal_workbook, spec, work,
                   {"hold_years": 3, "refi_year": 0, "exit_cap": hot_cap})
    irr = v["irr"] if isinstance(v["irr"], (int, float)) else 0
    print(f"    {'-'*70}")
    print(f"    {'IF market runs hot yr 3':<26}{irr*100:>7.1f}%"
          f"{'$' + format(int(v['profit'] or 0), ','):>12}{(v['em'] or 0):>6.2f}x"
          f"{'-':>11}  cap {hot_cap*100:.2f}% - UPSIDE ONLY, never the plan")

    # Plain-language readout
    best_irr = max((r for r in results if r[1] is not None), key=lambda r: r[1], default=None)
    best_profit = max(results, key=lambda r: r[2] or 0, default=None)
    if best_irr and best_profit:
        if best_irr[0] == best_profit[0]:
            print(f"\n    Best path both ways: {best_irr[0]} "
                  f"({best_irr[1]*100:.1f}% IRR, ${best_profit[2]:,.0f} profit)")
        else:
            print(f"\n    Fastest money: {best_irr[0]} ({best_irr[1]*100:.1f}% IRR, "
                  f"${dict((r[0], r[2]) for r in results)[best_irr[0]]:,.0f})")
            print(f"    Most money:    {best_profit[0]} (${best_profit[2]:,.0f}, "
                  f"{best_profit[1]*100:.1f}% IRR)")
            print("    High IRR on a short hold can be a small check. Pick by dollars")
            print("    unless the exit funds the next deal immediately.")

    work.unlink(missing_ok=True)
    return results
