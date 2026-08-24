"""Solve for the purchase price that hits a target return.

This is the "Target Price" / "Ideal Price" idea from the tracking grid: given
everything else about a deal, what would you have to pay for it to clear 16%,
and what would you have to pay to clear 22%.

Price is bisected. On every iteration: taxes are recomputed from price (a
purchase resets the assessment) AND the exit cap is re-derived as that price's
going-in cap + 50bps. Re-deriving matters: a lower price means a higher going-in
cap, so the exit cap rises with it - the solved price never gets credit for
selling at a richer valuation than it bought at.
"""

import shutil
import sys

import latax
import recalc as rc
from safe_writer import ModelWriter


def _irr_at(model_path, spec, price, fixed, location, commercial_share, work_path):
    shutil.copy2(model_path, work_path)
    w = ModelWriter(work_path, spec)
    w.set(spec["scalars"]["purchase_price"], round(price))
    for k, v in fixed.items():
        if k == "exit_cap":
            continue  # re-derived below, never inherited from the asking price
        w.set(spec["scalars"][k], v)
    if location:
        tax, _ = latax.estimate_tax(price, location, commercial_share)
        if tax is not None:
            w.set(spec["scalars"]["taxes_annual"], round(tax))
    w.save()
    rc.recalc(work_path)
    # Second pass: exit cap = THIS price's going-in cap + 50bps.
    gi = rc.read_values(work_path, {"c": spec["outputs"]["going_in_cap"]})["c"]
    if isinstance(gi, (int, float)) and gi > 0:
        w2 = ModelWriter(work_path, spec)
        w2.set(spec["scalars"]["exit_cap"], round(gi + 0.005, 5))
        w2.save()
        rc.recalc(work_path)
    vals = rc.read_values(work_path, {
        "irr": spec["outputs"]["levered_irr"],
        "dscr": spec["outputs"]["dscr_yr1"],
        "equity": spec["outputs"]["total_equity"],
    })
    irr = vals["irr"]
    return (irr if isinstance(irr, (int, float)) else -1.0), vals


def solve_price(model_path, spec, target_irr, asking, fixed, location,
                commercial_share, work_path, tol=0.0015, max_iter=14):
    """-> dict or None if the target is unreachable at any sane price.

    Lower price means higher IRR, so the search runs downward from asking.
    """
    # Same loud warning as pymodel.solve_price: frozen taxes bias every trial
    # price optimistic (this exact omission skewed live-deal ladders 2026-08-02/03).
    if not location:
        print("WARNING [solve.solve_price]: no location - property taxes stay FROZEN "
              "at the workbook value across all trial prices, biasing solved prices "
              "optimistic. Pass --location for per-price reassessment. "
              "(This exact omission skewed live-deal ladders on 2026-08-02/03.)",
              file=sys.stderr)
    else:
        _tax, _exp = latax.estimate_tax(asking, location, commercial_share)
        if _tax is None:
            print(f"WARNING [solve.solve_price]: tax estimate unavailable for "
                  f"{location!r} ({_exp}) - property taxes stay FROZEN at the workbook "
                  "value across all trial prices, biasing solved prices optimistic.",
                  file=sys.stderr)
    hi = asking
    irr_hi, _ = _irr_at(model_path, spec, hi, fixed, location, commercial_share, work_path)
    if irr_hi >= target_irr:
        return {"price": hi, "irr": irr_hi, "note": "already clears at asking"}

    lo = max(50_000, asking * 0.35)
    irr_lo, _ = _irr_at(model_path, spec, lo, fixed, location, commercial_share, work_path)
    if irr_lo < target_irr:
        return None  # cannot get there even at a 65% discount

    best = None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        irr_mid, vals = _irr_at(model_path, spec, mid, fixed, location, commercial_share, work_path)
        if irr_mid >= target_irr:
            best = {"price": mid, "irr": irr_mid, "dscr": vals["dscr"], "equity": vals["equity"]}
            lo = mid
        else:
            hi = mid
        # Stop only once a clearing price exists, and judge the answer we would
        # RETURN rather than a probe we discarded (sweep 2026-08-24). This is
        # the same defect fixed in pymodel.solve_price on 2026-08-09 that was
        # never ported here - and solve.py is what `intake.py --solve-price`
        # actually calls. Testing irr_mid returned a stale `best` (eden 13%:
        # $1,423,000 when $1,559,000 clears - a $136k under-offer on the top
        # deal) and, when it fired on iteration 0 with best still None,
        # reported a reachable target as unreachable (covington-2nd @16%).
        if best is not None and abs(best["irr"] - target_irr) < tol:
            break
    if best:
        # Floor, never round - see pymodel.solve_price.
        rounded = int(best["price"] // 1000) * 1000
        if rounded != best["price"]:
            # Re-evaluate at the rounded price so the reported IRR belongs to
            # the reported price (audit 2026-08-05 — pymodel already does this).
            irr_r, vals_r = _irr_at(model_path, spec, rounded, fixed,
                                    location, commercial_share, work_path)
            if irr_r is not None:
                best = {"price": rounded, "irr": irr_r,
                        "dscr": vals_r["dscr"], "equity": vals_r["equity"]}
            else:
                best["price"] = rounded
    return best
