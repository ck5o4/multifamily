#!/usr/bin/env python3
"""Portfolio simulator for Ridgeback Peak Properties.

Models a sequence of acquisitions from a single equity base, tracks running
cash balance, flags capital gaps, and computes combined portfolio-level IRR.

Usage:
    python3 tools/portfolio_sim.py <scenario-name>
    python3 tools/portfolio_sim.py --list
    python3 tools/portfolio_sim.py --compare baker-then-fourplex eden-solo

Scenarios live in portfolio/scenarios/<name>.json.
"""

import argparse
import json
import math
import sys
from pathlib import Path

# Ensure tools/ is on the import path
sys.path.insert(0, str(Path(__file__).parent))

import pymodel
import latax

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "portfolio" / "scenarios"


# ---------------------------------------------------------------------------
# IRR helper — delegates to pymodel's verified Newton-Raphson implementation
# ---------------------------------------------------------------------------

def _portfolio_irr(cash_flows):
    """Annual IRR on a dated cash flow list.  cash_flows[0] = year-0 flow."""
    return pymodel._irr(cash_flows)


# ---------------------------------------------------------------------------
# Deal input builder
# ---------------------------------------------------------------------------

def _build_inputs(deal_spec):
    """Build a pymodel input dict from a scenario deal spec.

    deal_spec may include:
        "deal": "<name>"       load from deal-intake/<name>/<name>_acq.xlsx
        "price": <number>      override price (and recompute taxes if possible)
        "location": <str>      used with latax for tax recomputation
        "inputs": {<k>:<v>}    direct pymodel input overrides (highest priority)
    """
    base = {}

    # Load from workbook if "deal" key present
    deal_name = deal_spec.get("deal")
    if deal_name:
        base = pymodel._load_deal(deal_name)

    # Price override
    price_override = deal_spec.get("price")
    if price_override is not None:
        base["price"] = price_override
        base["exit_cap"] = None  # force re-derive at new price

        # Recompute taxes if location provided explicitly
        location = deal_spec.get("location")
        if location:
            tax, _ = latax.estimate_tax(price_override, location)
            if tax is not None:
                base["taxes_annual"] = tax
        elif deal_name:
            # Try to infer location from deal name parts (baker -> baker, etc.)
            for part in deal_name.split("-"):
                tax, _ = latax.estimate_tax(price_override, part)
                if tax is not None:
                    base["taxes_annual"] = tax
                    break

    # Merge explicit input overrides last (highest priority)
    extra = deal_spec.get("inputs", {})
    base.update(extra)

    if "unit_mix" not in base or not base["unit_mix"]:
        raise ValueError(
            "no unit_mix. Provide 'deal' to load a workbook, "
            "or include 'unit_mix' in 'inputs'."
        )

    return base


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

def simulate(scenario):
    """Run the portfolio simulation from a scenario dict.

    CASH FLOW MODEL
    ---------------
    The investor commits starting_equity at year 0 (one external outflow).
    Subsequent acquisitions are funded from the running cash balance (recycled
    CFADS + undeployed initial equity) — NOT additional external capital.
    If the balance is insufficient at an acquisition year, a GAP flag is raised
    AND the shortfall is added as an additional external outflow in port_cf so
    the IRR honestly reflects the extra capital required.
    Annual savings_contribution are treated as external inflows each year (yr 1+).

    Portfolio IRR perspective:
        year 0: -starting_equity
        year t: CFADS distributions from all active deals + savings
                (minus any gap-shortfall additional capital at that year)

    Returns a result dict with:
        scenario_name, starting_equity, annual_savings,
        deals            list of per-deal dicts
        portfolio_cf     list[float] indexed 0..max_year
        portfolio_irr    levered IRR on portfolio_cf
        total_profit     sum(portfolio_cf)
        peak_deployed    max cumulative equity deployed
        annual_table     list of year-dicts
        gap_flags        list of warning strings
        max_year         int
    """
    starting_equity = float(scenario.get("starting_equity", 300_000))
    annual_savings = float(scenario.get("annual_savings_contribution", 0))
    deals_spec = scenario.get("deals", [])

    gap_flags = []
    deal_results = []

    # -----------------------------------------------------------------------
    # Step 1: Run pymodel for each deal; finalise max_year
    # -----------------------------------------------------------------------
    specs_sorted = sorted(deals_spec, key=lambda s: int(s.get("year", 0)))
    max_year = 10

    prebuilt = []
    for spec in specs_sorted:
        label = spec.get("name", spec.get("deal", "?"))
        acq_year = int(spec.get("year", 0))

        try:
            inputs = _build_inputs(spec)
        except Exception as e:
            raise ValueError("Deal '%s': failed to build inputs: %s" % (label, e)) from e

        hold_years = int(inputs.get("hold_years", 5))
        exit_year = acq_year + hold_years
        max_year = max(max_year, exit_year)

        try:
            r = pymodel.run(inputs)
        except Exception as e:
            raise ValueError("Deal '%s': pymodel.run() failed: %s" % (label, e)) from e

        prebuilt.append((spec, inputs, r, label, acq_year, exit_year, hold_years))

    # -----------------------------------------------------------------------
    # Step 2: Build combined NOI/DS lookup and deal_cfads index
    # -----------------------------------------------------------------------
    combined_noi = {}
    combined_ds = {}
    for yr in range(max_year + 1):
        combined_noi[yr] = 0.0
        combined_ds[yr] = 0.0

    # deal_cfads[(idx, portfolio_year)] = cf from that deal that year
    deal_cfads = {}

    for idx, (spec, inputs, r, label, acq_year, exit_year, hold_years) in enumerate(prebuilt):
        deal_cf = r["lev_cf"]
        for i, cf in enumerate(deal_cf):
            py = acq_year + i
            if py <= max_year:
                deal_cfads[(idx, py)] = cf

        for yr_idx in range(1, hold_years + 1):
            py = acq_year + yr_idx
            if py <= max_year:
                combined_noi[py] += r["noi"].get(yr_idx, 0.0)
                combined_ds[py] += r["total_ds"].get(yr_idx, 0.0)

        deal_results.append({
            "name": label,
            "acq_year": acq_year,
            "exit_year": exit_year,
            "inputs": inputs,
            "result": r,
            "equity_deployed": r["total_equity"],
            "_idx": idx,
        })

    # -----------------------------------------------------------------------
    # Step 3: Walk year by year — track balance, flag gaps, build port_cf
    # -----------------------------------------------------------------------
    # Investor external cash flows:
    #   port_cf[0] = -starting_equity  (write the cheque once)
    #   port_cf[yr] += distributions from deals + savings  (real returns)
    #   port_cf[yr] -= gap shortfall if additional capital needed  (honest IRR)
    port_cf = [0.0] * (max_year + 1)
    port_cf[0] = -starting_equity

    annual_table = []
    running_bal = starting_equity  # cash in hand before year-0 deployments
    peak_deployed = 0.0
    cumulative_deployed = 0.0
    acquired = set()

    for yr in range(0, max_year + 1):
        cash_out_equity = 0.0   # equity deployed to buy deals this year
        cash_in_dist = 0.0      # CFADS / exit received this year

        # --- Acquire deals whose year == yr ---
        for dr in deal_results:
            if dr["acq_year"] == yr and dr["_idx"] not in acquired:
                acquired.add(dr["_idx"])
                eq = dr["equity_deployed"]

                if eq > running_bal + 0.01:  # float tolerance
                    shortfall = eq - running_bal
                    gap_flags.append(
                        "GAP: '%s' needs $%s at year %d, "
                        "only $%s available -- requires new capital of $%s"
                        % (
                            dr["name"],
                            "{:,.0f}".format(eq),
                            yr,
                            "{:,.0f}".format(running_bal),
                            "{:,.0f}".format(shortfall),
                        )
                    )
                    # Record the shortfall as an external outflow in port_cf
                    port_cf[yr] -= shortfall

                cash_out_equity += eq
                cumulative_deployed += eq
                peak_deployed = max(peak_deployed, cumulative_deployed)

        # --- Receive distributions from active deals (lev_cf index >= 1) ---
        for dr in deal_results:
            idx = dr["_idx"]
            i = yr - dr["acq_year"]
            cf = deal_cfads.get((idx, yr))
            if cf is not None and i > 0:
                if cf >= 0:
                    cash_in_dist += cf
                else:
                    # Negative CFADS (e.g. deeply negative deal): count as cost
                    cash_out_equity += abs(cf)

        # --- Annual savings (external inflow yr 1+) ---
        savings = annual_savings if yr > 0 else 0.0
        cash_in_total = cash_in_dist + savings

        # Update running balance: what we have before next year's events
        running_bal = running_bal - cash_out_equity + cash_in_total

        # Record distributions + savings in port_cf
        port_cf[yr] += cash_in_dist + savings

        noi_yr = combined_noi.get(yr, 0.0)
        ds_yr = combined_ds.get(yr, 0.0)
        dscr_yr = (noi_yr / ds_yr) if ds_yr > 0 else None

        annual_table.append({
            "year": yr,
            "cash_in": cash_in_total,
            "cash_out": cash_out_equity,
            "net_cf": cash_in_total - cash_out_equity,
            "running_balance": running_bal,
            "combined_noi": noi_yr,
            "combined_ds": ds_yr,
            "combined_dscr": dscr_yr,
        })

    # -----------------------------------------------------------------------
    # Step 4: Portfolio-level metrics
    # -----------------------------------------------------------------------
    portfolio_irr = _portfolio_irr(port_cf)
    total_profit = sum(port_cf)
    total_in = sum(x for x in port_cf if x > 0)

    return {
        "scenario_name": scenario.get("name", "?"),
        "starting_equity": starting_equity,
        "annual_savings": annual_savings,
        "deals": deal_results,
        "portfolio_cf": port_cf,
        "portfolio_irr": portfolio_irr,
        "total_profit": total_profit,
        "total_invested": starting_equity,
        "total_returned": total_in,
        "peak_deployed": peak_deployed,
        "annual_table": annual_table,
        "gap_flags": gap_flags,
        "max_year": max_year,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _pct(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return "%.1f%%" % (x * 100)


def _d(x):
    """Format as dollar amount."""
    if x is None:
        return "n/a"
    return "${:>12,.0f}".format(x)


def print_results(res):
    name = res["scenario_name"]
    print("\n" + "=" * 68)
    print("  SCENARIO: %s" % name)
    print("=" * 68)
    print("  Starting equity:   %s" % _d(res["starting_equity"]))
    if res["annual_savings"] > 0:
        print("  Annual savings:    %s/yr" % _d(res["annual_savings"]))
    print("  Deals:             %d" % len(res["deals"]))

    if res["gap_flags"]:
        print()
        for flag in res["gap_flags"]:
            print("  *** %s" % flag)

    # Per-deal header
    print()
    print("  %-22s %6s %7s %12s %10s %7s %7s %6s" % (
        "Deal", "AcqYr", "ExitYr", "Equity", "NOI Y1", "DSCR Y1", "IRR", "EM"))
    print("  " + "-" * 22 + " " + " ".join(["-" * w for w in [6, 7, 12, 10, 7, 7, 6]]))

    for dr in res["deals"]:
        r = dr["result"]
        print("  %-22s %6d %7d %12s %10s %7s %7s %6s" % (
            dr["name"][:22],
            dr["acq_year"],
            dr["exit_year"],
            "${:,.0f}".format(dr["equity_deployed"]),
            "${:,.0f}".format(r["noi"][1]),
            "%.2fx" % r["dscr"][1],
            _pct(r["levered_irr"]),
            "%.2fx" % r["equity_multiple"],
        ))

    # Annual table
    print()
    print("  Year-by-year portfolio:")
    print("  %4s  %12s  %12s  %13s  %11s  %10s  %6s" % (
        "Yr", "Cash In", "Cash Out", "Balance", "Comb NOI", "Comb DS", "DSCR"))
    print("  " + "  ".join(["-" * w for w in [4, 12, 12, 13, 11, 10, 6]]))

    for row in res["annual_table"]:
        dscr_s = "%.2fx" % row["combined_dscr"] if row["combined_dscr"] is not None else "---"
        noi_s = "${:>9,.0f}".format(row["combined_noi"]) if row["combined_noi"] else "         -"
        ds_s  = "${:>8,.0f}".format(row["combined_ds"]) if row["combined_ds"] else "        -"
        print("  {:4d}  {:>12s}  {:>12s}  {:>13s}  {:11s}  {:10s}  {:>6s}".format(
            row["year"],
            "${:,.0f}".format(row["cash_in"]),
            "${:,.0f}".format(row["cash_out"]),
            "${:,.0f}".format(row["running_balance"]),
            noi_s,
            ds_s,
            dscr_s,
        ))

    print()
    print("  Portfolio summary:")
    print("    Portfolio IRR:   %s" % _pct(res["portfolio_irr"]))
    print("    Total profit:    %s" % _d(res["total_profit"]))
    print("    Peak deployed:   %s" % _d(res["peak_deployed"]))
    total_in = res["total_returned"]
    se = res["starting_equity"]
    print("    Cash returned:   %s  (%.2fx starting equity)" % (_d(total_in), total_in / se if se else 0))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(results):
    """Print a side-by-side comparison and honest 3-line interpretation."""
    if not results:
        return

    print("\n" + "=" * 68)
    print("  STRATEGY COMPARISON")
    print("=" * 68)

    names = [r["scenario_name"] for r in results]
    col_w = max(len(n) for n in names) + 2

    fmt_hdr = "  %-28s" + ("  %" + str(col_w) + "s") * len(results)
    fmt_row = "  %-28s" + ("  %" + str(col_w) + "s") * len(results)

    print(fmt_hdr % tuple(["Metric"] + names))
    print("  " + "-" * 28 + ("  " + "-" * col_w) * len(results))

    def row(label, vals):
        print(fmt_row % tuple([label] + list(vals)))

    row("Portfolio IRR", [_pct(r["portfolio_irr"]) for r in results])
    row("Total profit", ["${:,.0f}".format(r["total_profit"]) for r in results])
    row("Peak capital deployed", ["${:,.0f}".format(r["peak_deployed"]) for r in results])
    row("Deals", [str(len(r["deals"])) for r in results])

    # Per-deal contributions
    for r_i, r in enumerate(results):
        for dr in r["deals"]:
            deal_irr = _pct(dr["result"]["levered_irr"])
            deal_em = "%.2fx" % dr["result"]["equity_multiple"]
            vals_irr = [deal_irr if j == r_i else "" for j in range(len(results))]
            vals_em  = [deal_em  if j == r_i else "" for j in range(len(results))]
            row("  %-24s IRR" % dr["name"][:24], vals_irr)
            row("  %-24s EM"  % dr["name"][:24], vals_em)

    # Gap flags
    all_gaps = [(r["scenario_name"], g) for r in results for g in r["gap_flags"]]
    if all_gaps:
        print()
        print("  CAPITAL GAP WARNINGS:")
        for sname, flag in all_gaps:
            print("    [%s] %s" % (sname, flag))

    # Honest interpretation
    print()
    print("  INTERPRETATION:")

    irr_vals   = {r["scenario_name"]: r["portfolio_irr"] or -999 for r in results}
    profit_vals = {r["scenario_name"]: r["total_profit"]          for r in results}
    deployed_vals = {r["scenario_name"]: r["peak_deployed"]       for r in results}

    irr_winner    = max(irr_vals,    key=irr_vals.get)
    profit_winner = max(profit_vals, key=profit_vals.get)
    same_winner   = irr_winner == profit_winner

    irr_strs = ", ".join(
        "%s: %s" % (n, _pct(irr_vals[n])) for n in names
    )
    profit_strs = ", ".join(
        "%s: $%s" % (n, "{:,.0f}".format(profit_vals[n])) for n in names
    )
    deployed_strs = ", ".join(
        "%s: $%s peak" % (n, "{:,.0f}".format(deployed_vals[n])) for n in names
    )

    lines = [
        "IRR -- %s. '%s' wins on percentage return." % (irr_strs, irr_winner),
        "Total profit -- %s (%s deployed). '%s' returns more absolute dollars."
        % (profit_strs, deployed_strs, profit_winner),
    ]

    if same_winner:
        lines.append(
            "Verdict: '%s' dominates on both IRR and profit. "
            "Higher IRR reflects deal quality, not financial engineering -- "
            "but these are pro-forma outputs; results depend on underwriting accuracy "
            "and whether deployed capital works as hard in idle years."
            % irr_winner
        )
    else:
        lines.append(
            "Verdict: '%s' deploys capital more efficiently (higher IRR); "
            "'%s' generates more absolute dollars by deploying more. "
            "With $300K available and idle equity losing real-terms value, "
            "the two-deal sequence only dominates if the second deal genuinely clears "
            "the hurdle -- check DSCR and gap flags before committing."
            % (irr_winner, profit_winner)
        )

    for i, line in enumerate(lines, 1):
        # Wrap at ~70 chars
        words = line.split()
        buf = "  %d. " % i
        indent = "     "
        for word in words:
            if len(buf) + len(word) + 1 > 72 and buf.strip():
                print(buf.rstrip())
                buf = indent + word + " "
            else:
                buf += word + " "
        if buf.strip():
            print(buf.rstrip())

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _list_scenarios():
    if not SCENARIOS_DIR.exists():
        print("No scenarios directory at:", SCENARIOS_DIR)
        return
    files = sorted(SCENARIOS_DIR.glob("*.json"))
    if not files:
        print("No scenarios in", SCENARIOS_DIR)
        return
    print("\nAvailable scenarios (%s):" % SCENARIOS_DIR)
    for f in files:
        print("  %s" % f.stem)


def _load_scenario(name):
    path = SCENARIOS_DIR / ("%s.json" % name)
    if not path.exists():
        raise FileNotFoundError("Scenario not found: %s" % path)
    with open(path) as fh:
        data = json.load(fh)
    if "name" not in data:
        data["name"] = name
    return data


def main():
    ap = argparse.ArgumentParser(
        description="Ridgeback Peak Properties -- portfolio acquisition simulator"
    )
    ap.add_argument("scenario", nargs="?", help="scenario name (without .json)")
    ap.add_argument("--list", action="store_true", help="list available scenarios")
    ap.add_argument("--compare", nargs="+", metavar="SCENARIO",
                    help="run and compare multiple scenarios side-by-side")
    args = ap.parse_args()

    if args.list:
        _list_scenarios()
        return

    if args.compare:
        results = []
        for name in args.compare:
            try:
                scenario = _load_scenario(name)
                res = simulate(scenario)
                print_results(res)
                results.append(res)
            except Exception as e:
                print("ERROR in scenario '%s': %s" % (name, e), file=sys.stderr)
                import traceback; traceback.print_exc()
                sys.exit(1)
        compare(results)
        return

    if not args.scenario:
        ap.print_help()
        sys.exit(0)

    try:
        scenario = _load_scenario(args.scenario)
    except FileNotFoundError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        _list_scenarios()
        sys.exit(1)

    try:
        res = simulate(scenario)
    except Exception as e:
        print("ERROR: %s" % e, file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print_results(res)


if __name__ == "__main__":
    main()
