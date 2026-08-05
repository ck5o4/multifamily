#!/usr/bin/env python3
"""Morning state briefing: pipeline board, pymodel snapshots, hedonic values,
calibration stats, and standing reminders.

  python3 tools/briefing.py

No network calls. Local state only. Target: ~60 lines of output.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parent.parent
DEALS_PATH = ROOT / "portfolio" / "deals.json"
ASKCAPS_PATH = ROOT / "portfolio" / "askcaps.json"
INTAKE = ROOT / "deal-intake"

TODAY = date.today().isoformat()
ACTIVE_STAGES = {"watching", "underwriting", "offered", "under_contract", "owned"}


def _days_since(iso_date: str) -> int:
    try:
        d = date.fromisoformat(iso_date)
        return (date.today() - d).days
    except ValueError:
        return -1


def _load_deals():
    if not DEALS_PATH.exists():
        return {}
    return json.loads(DEALS_PATH.read_text())


def _load_askcaps():
    if not ASKCAPS_PATH.exists():
        return []
    return json.loads(ASKCAPS_PATH.read_text())


def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def main():
    print(f"RIDGEBACK PEAK — MORNING BRIEFING  {TODAY}")

    deals = _load_deals()

    # (a) Pipeline board
    section("PIPELINE")
    active = {n: d for n, d in deals.items() if d["stage"] in ACTIVE_STAGES}
    if not active:
        print("  no active deals")
    else:
        for name, d in active.items():
            last = d["history"][-1]
            days = _days_since(last["date"])
            note = last.get("note", "")[:60]
            print(f"  {name:<24} [{d['stage']:<14}] {days:>2}d ago  {note}")

    # (b) Pymodel snapshots for underwriting/offered deals with workbooks
    import pymodel

    snap_deals = {n: d for n, d in deals.items()
                  if d["stage"] in ("underwriting", "offered")}
    if snap_deals:
        section("DEAL SNAPSHOTS (pymodel)")
        for name in snap_deals:
            wb_path = INTAKE / name / f"{name}_acq.xlsx"
            if not wb_path.exists():
                print(f"  {name}: no workbook (run intake --apply --recalc)")
                continue
            try:
                inputs = pymodel._load_deal(name)
                # Audit 2026-08-05: inject location so solve_price re-derives
                # taxes per trial price instead of freezing them (ladder bias).
                try:
                    _deals = json.loads((ROOT / "portfolio" / "deals.json").read_text())
                    _loc = _deals.get(name, {}).get("location")
                    if _loc:
                        inputs["location"] = _loc
                except Exception:
                    pass
                r = pymodel.run(inputs)
                price = inputs["price"]
                noi = r["noi"][1]
                cap = r["going_in_cap"] * 100
                irr = (r["levered_irr"] or 0) * 100
                em = r["equity_multiple"]
                # solve 13% price
                sol = pymodel.solve_price(inputs, 0.13)
                if sol and not sol.get("note"):
                    solve_str = f"pursue@${sol['price']:,.0f}"
                elif sol and sol.get("note"):
                    solve_str = "already clears 13%"
                else:
                    solve_str = "13% unreachable"
                print(f"  {name}")
                print(f"    ask ${price:>10,.0f}  NOI ${noi:>9,.0f}  cap {cap:.2f}%  "
                      f"IRR {irr:.1f}%  {em:.2f}x EM  | {solve_str}")
            except Exception as e:
                print(f"  {name}: pymodel error — {e}")

    # (c) Hedonic market-approach values
    import hedonic
    if snap_deals:
        section("MARKET APPROACH (hedonic, ~±34%)")
        for name in snap_deals:
            wb_path = INTAKE / name / f"{name}_acq.xlsx"
            if not wb_path.exists():
                continue
            try:
                inputs = pymodel._load_deal(name)
                total_units = sum(g["units"] for g in inputs["unit_mix"])
                # Infer market from deals history notes (best effort)
                hist_text = " ".join(h.get("note", "") for h in deals[name]["history"]).lower()
                if "baker" in name or "baker" in hist_text:
                    mkt = "baton rouge"
                elif "eden" in name or "denham" in hist_text or "livingston" in hist_text:
                    mkt = "baton rouge"
                elif "covington" in name or "northshore" in hist_text:
                    mkt = "northshore"
                elif "new orleans" in hist_text or "nola" in hist_text:
                    mkt = "new orleans"
                elif "lafayette" in hist_text:
                    mkt = "lafayette"
                else:
                    mkt = "baton rouge"  # default for LA tertiary
                pred = hedonic.predict(mkt, int(total_units), year=2026,
                                       fit_result=hedonic.fit(verbose=False), loud=False)
                print(f"  {name:<24} {mkt}  {int(total_units)}u  "
                      f"${pred['total_low']:,.0f}–${pred['total_high']:,.0f}  "
                      f"(mid ${pred['total_point']:,.0f})")
            except SystemExit as e:
                reason = str(e).replace("\n", " ").strip()
                print(f"  {name}: market approach: n/a - {reason}")
            except Exception as e:
                print(f"  {name}: hedonic error — {e}")

    # (d) Calibration stats one-liner
    section("CALIBRATION")
    askcaps = _load_askcaps()
    cal_path = ROOT / "portfolio" / "calibration.json"
    n_sales = 0
    if cal_path.exists():
        n_sales = len(json.loads(cal_path.read_text()))
    if askcaps:
        by_type = {}
        for e in askcaps:
            by_type.setdefault(e["type"], []).append(e["inflation_pts"])
        parts = [f"{t}: avg +{sum(v)/len(v):.1f}pts (n={len(v)})" for t, v in by_type.items()]
        print(f"  {n_sales} closed sales logged  |  ask-cap inflation: {', '.join(parts)}")
    else:
        print(f"  {n_sales} closed sales logged  |  no ask-cap entries yet")

    # (e) Reminders
    section("REMINDERS")
    # Scan notes for action words
    action_words = ("waiting", "sent", "pending")
    found_notes = []
    for name, d in deals.items():
        if d["stage"] == "dead":
            continue
        for h in d["history"]:
            note_low = h.get("note", "").lower()
            if any(w in note_low for w in action_words):
                found_notes.append(f"  [{name}] {h['note'][:80]}")
    for fn in found_notes:
        print(fn)
    # Standing reminders
    print("  Census key pending  (https://api.census.gov/data/key_signup.html)")
    print("  Crexi Intelligence purchase pending")
    print("  check Deal Flow digest in Gmail")

    print()


if __name__ == "__main__":
    main()
