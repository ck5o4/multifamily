#!/usr/bin/env python3
"""Portfolio and deal-flow tracker - the state file behind the future dashboard.

Every deal lives in portfolio/deals.json with a stage, history, its underwritten
plan (pulled from the deal workbook), actuals (parsed from bank/PM CSV exports),
and an investor-payback ledger. Terminal sessions read and update it; a
dashboard later is just a display over this file.

Stages: watching -> underwriting -> offered -> under_contract -> owned -> sold
        (or dead, from anywhere)

Usage:
  portfolio.py add "oak-street" --stage underwriting
  portfolio.py stage "oak-street" offered --note "offered 725k"
  portfolio.py board                          # the pipeline at a glance
  portfolio.py plan "oak-street"              # pull yr-1 targets from its workbook
  portfolio.py log "oak-street" june.csv      # ingest a bank/PM CSV export
  portfolio.py pay "oak-street" 2500          # record an investor distribution
  portfolio.py status "oak-street"            # actuals vs plan, on-track or not
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parsers import to_num  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PDIR = ROOT / "portfolio"
DEALS = PDIR / "deals.json"

STAGES = ["watching", "underwriting", "offered", "under_contract", "owned", "sold", "dead"]

# Bank/PM transaction categorization. Income positive, expenses negative.
CATEGORIES = [
    ("rent",      ("rent", "tenant", "lease payment", "deposit rec")),
    ("debt",      ("mortgage", "loan payment", "principal", "note pay")),
    ("insurance", ("insurance",)),
    ("taxes",     ("tax",)),
    ("utilities", ("entergy", "demco", "water", "sewer", "electric", "gas co", "utility", "trash", "waste")),
    ("repairs",   ("repair", "plumb", "hvac", "electric svc", "handyman", "roof", "appliance", "maintenance")),
    ("mgmt",      ("management", "mgmt")),
    ("reno",      ("renovation", "rehab", "contractor", "flooring", "cabinet", "paint")),
    ("distribution", ("distribution", "investor", "draw")),
]


def _load():
    if DEALS.exists():
        return json.loads(DEALS.read_text())
    return {}


def _save(d):
    PDIR.mkdir(exist_ok=True)
    DEALS.write_text(json.dumps(d, indent=2))


def _today():
    return date.today().isoformat()


def _iso_date(text):
    """Normalize bank-CSV dates to ISO. Handles 2027-01-15, 01/15/2027,
    1/15/27, 01-15-2027. Returns today when unparseable - monthly grouping
    depends on ISO order, so US formats must never pass through raw."""
    t = str(text).strip()
    import re as _re
    m = _re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", t)
    if m:
        mo, dy, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            return f"{yr}-{mo:02d}-{dy:02d}"
    return _today()


def cmd_add(name, stage, note):
    deals = _load()
    if name in deals:
        print(f"{name} already exists (stage: {deals[name]['stage']})")
        return
    deals[name] = {
        "stage": stage, "created": _today(),
        "history": [{"date": _today(), "stage": stage, "note": note or "created"}],
        "plan": None, "actuals": [], "payback": [],
    }
    _save(deals)
    print(f"added {name} at stage {stage}")


def cmd_stage(name, stage, note):
    deals = _load()
    if name not in deals:
        print(f"unknown deal {name}"); return
    deals[name]["stage"] = stage
    deals[name]["history"].append({"date": _today(), "stage": stage, "note": note or ""})
    _save(deals)
    print(f"{name} -> {stage}")


def cmd_board():
    deals = _load()
    if not deals:
        print("no deals tracked yet:  portfolio.py add <name> --stage watching")
        return
    for stage in STAGES:
        rows = [(n, d) for n, d in deals.items() if d["stage"] == stage]
        if not rows:
            continue
        print(f"\n{stage.upper().replace('_', ' ')}")
        for n, d in rows:
            last = d["history"][-1]
            extra = ""
            if d["stage"] == "owned" and d.get("payback"):
                paid = sum(p["amount"] for p in d["payback"])
                extra = f"  investor repaid ${paid:,.0f}"
            print(f"  {n:<24} since {last['date']}  {last.get('note', '')}{extra}")


def cmd_plan(name):
    """Pull year-1 monthly targets out of the deal's recalculated workbook."""
    deals = _load()
    if name not in deals:
        print(f"unknown deal {name}"); return
    wb_path = ROOT / "deal-intake" / name / f"{name}_acq.xlsx"
    if not wb_path.exists():
        print(f"no workbook at {wb_path} - run intake.py --deal {name} ... --recalc first")
        return
    import recalc as rc
    from cellmap import ACQ
    v = rc.read_values(wb_path, {
        "egr": ACQ["outputs"]["egr_yr1"], "noi": ACQ["outputs"]["noi_yr1"],
        "opex": ACQ["outputs"]["opex_yr1"],
        "ds": "Annual CF!D34", "cfads": ACQ["outputs"]["cfads_yr1"],
        "equity": ACQ["outputs"]["total_equity"], "lp": ACQ["outputs"]["lp_capital"],
    })
    if not isinstance(v["noi"], (int, float)):
        print("workbook has no computed values - recalc it first")
        return
    plan = {
        "set_on": _today(),
        "monthly_rent_target": round(v["egr"] / 12),
        "monthly_opex_target": round(abs(v["opex"]) / 12),
        "monthly_noi_target": round(v["noi"] / 12),
        "monthly_debt_service": round(abs(v["ds"]) / 12) if isinstance(v["ds"], (int, float)) else None,
        "monthly_cash_flow_target": round(v["cfads"] / 12),
        "investor_capital": round(v["lp"]) if isinstance(v["lp"], (int, float)) else None,
    }
    deals[name]["plan"] = plan
    _save(deals)
    print(f"PLAN SET for {name} (from {wb_path.name}):")
    for k, val in plan.items():
        if k != "set_on":
            print(f"  {k:<28}{'$' + format(val, ',') if val is not None else '-'}")


def cmd_log(name, csv_path):
    """Ingest a bank or property-manager CSV export: date, description, amount."""
    deals = _load()
    if name not in deals:
        print(f"unknown deal {name}"); return
    import csv as csvmod
    path = Path(csv_path)
    added, skipped = 0, 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csvmod.reader(fh):
            cells = [c.strip() for c in row if c.strip()]
            if len(cells) < 2:
                continue
            amount = None
            for c in reversed(cells):
                amount = to_num(c)
                if amount is not None:
                    break
            if amount is None:
                skipped += 1
                continue
            desc = " ".join(c for c in cells if to_num(c) is None)[:80]
            low = desc.lower()
            cat = next((k for k, kws in CATEGORIES if any(w in low for w in kws)), "uncategorized")
            when = _iso_date(cells[0]) if any(ch.isdigit() for ch in cells[0]) else _today()
            deals[name]["actuals"].append(
                {"date": when, "desc": desc, "amount": amount, "cat": cat})
            added += 1
    _save(deals)
    cats = {}
    for a in deals[name]["actuals"]:
        cats[a["cat"]] = cats.get(a["cat"], 0) + 1
    print(f"logged {added} transactions ({skipped} rows skipped)")
    unc = cats.get("uncategorized", 0)
    if unc:
        print(f"  {unc} uncategorized - review with: portfolio.py status {name}")


def cmd_pay(name, amount, note):
    deals = _load()
    if name not in deals:
        print(f"unknown deal {name}"); return
    deals[name]["payback"].append({"date": _today(), "amount": amount, "note": note or ""})
    _save(deals)
    paid = sum(p["amount"] for p in deals[name]["payback"])
    cap = (deals[name].get("plan") or {}).get("investor_capital")
    if cap:
        print(f"recorded ${amount:,.0f}. Investor repaid ${paid:,.0f} of ${cap:,.0f} "
              f"({paid / cap * 100:.0f}%)")
    else:
        print(f"recorded ${amount:,.0f}. Total repaid ${paid:,.0f} (no plan set - run plan first)")


def cmd_status(name):
    deals = _load()
    if name not in deals:
        print(f"unknown deal {name}"); return
    d = deals[name]
    print(f"{name}  [{d['stage']}]")
    for h in d["history"][-3:]:
        print(f"  {h['date']}  {h['stage']}  {h.get('note', '')}")
    plan = d.get("plan")
    acts = d.get("actuals", [])
    if not plan:
        print("\nno plan set - run: portfolio.py plan " + name)
    if not acts:
        print("no actuals logged - export a bank CSV and run: portfolio.py log " + name + " <file>")
        return
    # NOI compares operating items only. Mortgage, renovation, and investor
    # distributions are real cash but not operations - mixing them in made
    # every month look off-plan.
    NON_OPERATING = {"debt", "reno", "distribution"}
    months = {}
    for a in acts:
        m = str(a["date"])[:7]
        months.setdefault(m, {"in": 0.0, "op_out": 0.0, "debt": 0.0, "other_out": 0.0})
        b = months[m]
        if a["amount"] >= 0:
            b["in"] += a["amount"]
        elif a["cat"] == "debt":
            b["debt"] += -a["amount"]
        elif a["cat"] in NON_OPERATING:
            b["other_out"] += -a["amount"]
        else:
            b["op_out"] += -a["amount"]
    print(f"\n  {'Month':<9}{'Collected':>11}{'OpEx':>9}{'NOI':>10}{'Debt':>9}{'CashFlow':>10}", end="")
    if plan:
        print(f"{'Plan NOI':>10}  On track?")
    else:
        print()
    for m in sorted(months):
        b = months[m]
        noi = b["in"] - b["op_out"]
        cashflow = noi - b["debt"]
        line = (f"  {m:<9}{'$' + format(int(b['in']), ','):>11}{'$' + format(int(b['op_out']), ','):>9}"
                f"{'$' + format(int(noi), ','):>10}{'$' + format(int(b['debt']), ','):>9}"
                f"{'$' + format(int(cashflow), ','):>10}")
        if plan and plan.get("monthly_noi_target"):
            tgt = plan["monthly_noi_target"]
            ok = "YES" if noi >= tgt * 0.9 else ("close" if noi >= tgt * 0.75 else "NO")
            line += f"{'$' + format(tgt, ','):>10}  {ok}"
        print(line)
        if b["other_out"]:
            print(f"  {'':<9}{'':>11}{'':>9}{'':>10}{'':>9}"
                  f"  (+ ${b['other_out']:,.0f} reno/distributions, excluded from NOI)")
    if d.get("payback"):
        paid = sum(p["amount"] for p in d["payback"])
        cap = (plan or {}).get("investor_capital")
        if cap:
            print(f"\n  Investor repaid ${paid:,.0f} of ${cap:,.0f} ({paid / cap * 100:.0f}%)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("name"); p.add_argument("--stage", default="watching", choices=STAGES); p.add_argument("--note")
    p = sub.add_parser("stage"); p.add_argument("name"); p.add_argument("new_stage", choices=STAGES); p.add_argument("--note")
    sub.add_parser("board")
    p = sub.add_parser("plan"); p.add_argument("name")
    p = sub.add_parser("log"); p.add_argument("name"); p.add_argument("csv")
    p = sub.add_parser("pay"); p.add_argument("name"); p.add_argument("amount", type=float); p.add_argument("--note")
    p = sub.add_parser("status"); p.add_argument("name")
    a = ap.parse_args()
    if a.cmd == "add": cmd_add(a.name, a.stage, a.note)
    elif a.cmd == "stage": cmd_stage(a.name, a.new_stage, a.note)
    elif a.cmd == "board": cmd_board()
    elif a.cmd == "plan": cmd_plan(a.name)
    elif a.cmd == "log": cmd_log(a.name, a.csv)
    elif a.cmd == "pay": cmd_pay(a.name, a.amount, a.note)
    elif a.cmd == "status": cmd_status(a.name)


if __name__ == "__main__":
    main()
