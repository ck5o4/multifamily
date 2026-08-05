"""Eden Church MHP — 5-year payoff scenarios (2026-08-04).

Runs the actuals-based workbook inputs through pymodel at candidate
transaction structures, overlaying a seller-carry second note (IO,
balloon at exit) on top of the bank loan the model sizes.

Structures:
  A. $1.25M bank-only (75% LTV / 1.30 DSCR sizing)      — equity check
  B. $1.25M bank 70% + seller note 15% @ 6% IO, 5yr     — carry path
  C. $1.35M bank 70% + seller note 15% @ 6% IO, 5yr     — carry, upper settle
  D. $1.699M ask, bank-only                              — reference only
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import pymodel
import latax

LOCATION = "Denham Springs"
DEAL = "eden-church-mhp"

base = pymodel._load_deal(DEAL)


def run_scenario(name, price, ltv, note_pct=0.0, note_rate=0.06):
    inp = dict(base)
    inp["price"] = price
    inp["ltv"] = ltv
    inp["exit_cap"] = None  # re-derive going-in + 50bps
    tax, tax_note = latax.estimate_tax(price, LOCATION, 0.0)
    if tax is not None:
        inp["taxes_annual"] = tax
    r = pymodel.run(inp)

    hold = 5
    note = price * note_pct
    note_int = note * note_rate

    # Overlay seller note on levered cash flows
    lev = list(r["lev_cf"])
    lev[0] += note                      # less equity out the door
    for yr in range(1, hold + 1):
        lev[yr] -= note_int             # IO payments
    lev[hold] -= note                   # balloon from sale proceeds

    equity = -lev[0]
    lp_cap = equity * base.get("lp_pct", 0.90)
    gp_cap = equity - lp_cap
    wf = pymodel._waterfall(lev, lp_cap, gp_cap, base.get("pref", 0.08),
                            base.get("residual_lp", 0.70), hold)
    irr = pymodel._irr(lev)
    profit = sum(lev)
    em = 1 + profit / equity if equity > 0 else 0

    print("=" * 78)
    print(f"SCENARIO {name}: price ${price:,.0f}  bank LTV {ltv:.0%}"
          + (f"  + seller note {note_pct:.0%} (${note:,.0f} @ {note_rate:.0%} IO, balloon yr 5)"
             if note else "  (no carry)"))
    print("-" * 78)
    print(f"  taxes reassessed: ${tax:,.0f}/yr   ({tax_note})")
    print(f"  bank loan ${r['loan_amount']:,.0f}   going-in cap {r['going_in_cap']:.2%}"
          f"   exit sale yr5 ${r['sale_price']:,.0f} (fwd NOI / {r['noi_fwd']/r['sale_price']:.2%})")
    print(f"  TOTAL EQUITY ${equity:,.0f}   (LP ${lp_cap:,.0f} / GP ${gp_cap:,.0f})")
    print(f"  deal IRR {irr:.1%}   EM {em:.2f}x   total profit ${profit:,.0f}")
    print(f"  LP  IRR {wf['lp_irr']:.1%}  EM {wf['lp_em']:.2f}x  profit ${wf['lp_profit']:,.0f}")
    print(f"  GP  IRR {wf['gp_irr']:.1%}  EM {wf['gp_em']:.2f}x  profit ${wf['gp_profit']:,.0f}")
    d = wf["detail"]
    hdr = (f"  {'yr':>2} {'NOI':>9} {'bankDS':>8} {'noteDS':>7} {'cashflow':>9} "
           f"{'DSCR*':>5} {'CoC':>6} {'$/mo':>7} {'LP dist':>9} {'GP dist':>8} {'LP cum':>9}")
    print(hdr)
    lp_cum = 0.0
    for yr in range(1, hold + 1):
        noi = r["noi"][yr]
        bank_ds = r["total_ds"][yr]
        cf = lev[yr] if yr < hold else lev[yr]  # yr5 includes exit
        op_cf = r["cfads"][yr] - note_int       # operating-only cash flow
        dscr = noi / (bank_ds + note_int) if (bank_ds + note_int) else 0
        coc = op_cf / equity
        lp_dist = d["lp_net"][yr]
        gp_dist = d["gp_net"][yr]
        lp_cum += lp_dist
        tag = " +EXIT" if yr == hold else ""
        print(f"  {yr:>2} {noi:>9,.0f} {bank_ds:>8,.0f} {note_int:>7,.0f} "
              f"{op_cf:>9,.0f} {dscr:>5.2f} {coc:>6.1%} {op_cf/12:>7,.0f} "
              f"{lp_dist:>9,.0f} {gp_dist:>8,.0f} {lp_cum:>9,.0f}{tag}")
    print(f"  yr5 exit: sale ${r['sale_price']:,.0f} - costs ${r['cost_of_sale_amt']:,.0f}"
          f" - bank payoff ${r['loan_payoff']:,.0f} - note balloon ${note:,.0f}"
          f" = net ${r['sale_price']-r['cost_of_sale_amt']-r['loan_payoff']-note:,.0f}")
    print(f"  * DSCR = NOI / (bank DS + note interest); bank-only DSCR yr1 {r['dscr'][1]:.2f}")
    return {"equity": equity, "irr": irr, "wf": wf}


run_scenario("A", 1_250_000, 0.75)
run_scenario("B", 1_250_000, 0.70, note_pct=0.15)
run_scenario("C", 1_350_000, 0.70, note_pct=0.15)
run_scenario("D", 1_699_000, 0.75)
