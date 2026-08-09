"""Formatting and verdicts for recalculated models."""

from cellmap import IRR_REALISTIC, IRR_TARGET


def money(v):
    if v is None or isinstance(v, str):
        return "-"
    return f"${v:,.0f}"


def pct(v, dp=2):
    if v is None or isinstance(v, str):
        return "-"
    return f"{v * 100:.{dp}f}%"


def mult(v):
    if v is None or isinstance(v, str):
        return "-"
    return f"{v:.2f}x"


def num(v, dp=0):
    if v is None or isinstance(v, str):
        return "-"
    return f"{v:,.{dp}f}"


def irr_verdict(irr):
    if irr is None or isinstance(irr, str):
        return "NO IRR - model did not solve"
    if irr < 0:
        return "FAIL - negative IRR"
    if irr < 0.10:
        return "FAIL - below 10%, well under the 12-14% realistic band"
    if irr < IRR_REALISTIC[0]:
        return f"BELOW REALISTIC - under the {pct(IRR_REALISTIC[0],0)} floor of the realistic band"
    if irr < IRR_REALISTIC[1]:
        return "REALISTIC BAND (12-14%) - normal for stabilized LA at current rates"
    if irr < IRR_TARGET[0]:
        return "ABOVE REALISTIC, BELOW TARGET - between 14% and the 16% target floor"
    return "MEETS TARGET (16-17%) - verify inputs; this is aggressive for stabilized LA without value-add"


def dscr_verdict(dscr, minimum):
    if dscr is None or isinstance(dscr, str):
        return "NO DSCR"
    if minimum is None:
        return ""
    if dscr < minimum:
        return f"FAILS lender minimum {minimum:.2f}"
    if dscr < minimum + 0.05:
        return f"binds at lender minimum {minimum:.2f}"
    return f"clears minimum {minimum:.2f}"


def binding_constraint(loan, ltv, price):
    if None in (loan, ltv, price) or isinstance(loan, str):
        return "unknown"
    ltv_cap = ltv * price
    if abs(loan - ltv_cap) < max(1.0, 0.001 * ltv_cap):
        return f"LTV ({pct(ltv,0)} of price)"
    return "DSCR"


def print_acq(vals, checks, errors, scalars):
    price = scalars.get("purchase_price")
    units = vals.get("total_units")
    print("\n" + "=" * 64)
    print("ACQUISITION RESULT")
    print("=" * 64)
    print(f"  Purchase Price          {money(price)}")
    print(f"  Units                   {num(units)}")
    print(f"  Price / Unit            {money(vals.get('price_per_unit'))}")
    print(f"  Year 1 EGR              {money(vals.get('egr_yr1'))}")
    print(f"  Year 1 OpEx             {money(vals.get('opex_yr1'))}")
    print(f"  Year 1 NOI              {money(vals.get('noi_yr1'))}")
    print(f"  Going-In Cap            {pct(vals.get('going_in_cap'))}")
    print("  " + "-" * 60)
    print(f"  Loan Amount             {money(vals.get('loan_amount'))}"
          f"   [binds on {binding_constraint(vals.get('loan_amount'), scalars.get('ltv'), price)}]")
    print(f"  Monthly Debt Service    {money(vals.get('monthly_debt_service'))}")
    print(f"  Total Equity Required   {money(vals.get('total_equity'))}")
    print(f"    LP Capital            {money(vals.get('lp_capital'))}")
    print(f"    GP Capital            {money(vals.get('gp_capital'))}")
    print("  " + "-" * 60)
    d1 = vals.get("dscr_yr1")
    print(f"  Year 1 DSCR             {mult(d1)}   {dscr_verdict(d1, scalars.get('min_dscr'))}")
    print(f"  Avg DSCR (hold)         {mult(vals.get('avg_dscr'))}")
    print(f"  Year 1 CFADS            {money(vals.get('cfads_yr1'))}")
    print(f"  Avg Cash-on-Cash        {pct(vals.get('avg_coc'))}")
    if scalars.get("reno_units"):
        print("  " + "-" * 60)
        n = int(scalars["reno_units"])
        print(f"  VALUE-ADD: {n} units at {money(scalars.get('reno_cost_per_unit'))}/unit")
        print(f"    Renovation budget     {money(vals.get('reno_budget'))}  (funded at close)")
        print(f"    Rent premium          {money(scalars.get('reno_premium_month'))}/unit/month")
        print(f"    Pace                  {scalars.get('reno_units_per_year'):.0f}/yr from year "
              f"{scalars.get('reno_start_year'):.0f}, {scalars.get('reno_downtime_months'):.0f} mo downtime per turn")
        print(f"    NOI year 1 -> year 5  {money(vals.get('noi_yr1'))} -> {money(vals.get('noi_stabilized'))}")
    if scalars.get("refi_year"):
        print("  " + "-" * 60)
        print(f"  REFINANCE in year {int(scalars['refi_year'])}")
        print(f"    Appraised value       {money(vals.get('refi_value'))}")
        new_loan = vals.get("refi_new_loan")
        cap = (scalars.get("refi_ltv") or 0) * (vals.get("refi_value") or 0)
        binds = "LTV" if cap and abs((new_loan or 0) - cap) < max(1.0, 0.001 * cap) else "DSCR"
        print(f"    New loan              {money(new_loan)}   [binds on {binds}]")
        print(f"    Pays off old loan     {money(vals.get('refi_old_payoff'))}")
        print(f"    CASH BACK TO YOU      {money(vals.get('refi_cash_out'))}")
        _refi_reality_check(vals.get("refi_cash_out"), vals.get("total_equity"),
                            vals.get("lp_capital"))
    print("  " + "-" * 60)
    print(f"  Unlevered IRR           {pct(vals.get('unlevered_irr'))}")
    print(f"  Levered IRR             {pct(vals.get('levered_irr'))}")
    print(f"  Equity Multiple         {mult(vals.get('equity_multiple'))}")
    print(f"  Total Profit            {money(vals.get('total_profit'))}")
    print(f"  LP IRR / EM             {pct(vals.get('lp_irr'))} / {mult(vals.get('lp_em'))}")
    print(f"  GP IRR                  {pct(vals.get('gp_irr'))}")
    print("  " + "-" * 60)
    print(f"  VERDICT: {irr_verdict(vals.get('levered_irr'))}")
    _cap_compression_warning(vals.get("going_in_cap"), scalars.get("exit_cap"))
    _print_integrity(checks, errors)


def _refi_reality_check(cash_out, total_equity, lp_capital):
    """A refi only repays an investor if NOI grew enough to support a bigger
    loan. Say plainly how much of the investor is actually repaid."""
    if not cash_out or not lp_capital:
        return
    share = cash_out / lp_capital
    if share >= 1.0:
        print(f"    -> repays your investor in full ({share*100:.0f}% of their ${lp_capital:,.0f})")
    elif share >= 0.5:
        print(f"    -> repays {share*100:.0f}% of your investor's ${lp_capital:,.0f}")
    else:
        print(f"    -> only {share*100:.0f}% of your investor's ${lp_capital:,.0f}.")
        print("       A refinance returns capital only when NOI has grown enough to")
        print("       support a bigger loan. Flat rents mean nothing to pull out.")


def _cap_compression_warning(going_in, exit_cap):
    """Exiting at a cap below the going-in cap manufactures IRR out of an
    assumption rather than out of operations. Say so in basis points."""
    if going_in is None or exit_cap is None or isinstance(going_in, str):
        return
    spread = (going_in - exit_cap) * 10000
    if spread <= 25:
        return
    print(f"\n  WARNING: exit cap {exit_cap*100:.2f}% is {spread:.0f} bps BELOW the "
          f"going-in cap {going_in*100:.2f}%.")
    print("    The model is assuming the asset re-trades at a materially richer")
    print("    valuation than you bought it at. On small tertiary-market deals that")
    print("    assumption is unearned - most of the IRR above is coming from it, not")
    print("    from operations. Set exit_cap at or above the going-in cap to see the")
    print("    return without compression:  --set exit_cap=<going-in>")


def print_dev(vals, checks, errors, scalars):
    print("\n" + "=" * 64)
    print("DEVELOPMENT RESULT")
    print("=" * 64)
    print(f"  Units                   {num(vals.get('total_units'))}")
    print(f"  Net Rentable SF         {num(vals.get('nrsf'))}")
    print(f"  Total Project Cost      {money(vals.get('total_project_cost'))}")
    print(f"  Cost / Unit             {money(vals.get('cost_per_unit'))}")
    print(f"  Cost / SF               {money(vals.get('cost_per_sf'))}")
    print("  " + "-" * 60)
    print(f"  Constr Loan Peak        {money(vals.get('constr_loan_peak'))}")
    print(f"  Effective LTC           {pct(vals.get('effective_ltc'))}")
    print(f"  Net Equity Required     {money(vals.get('net_equity'))}")
    print("  " + "-" * 60)
    print(f"  Stabilized NOI          {money(vals.get('stabilized_noi'))}")
    print(f"  Development Yield       {pct(vals.get('development_yield'))}")
    print(f"  Refi Value              {money(vals.get('refi_value'))}")
    print(f"  Perm Loan               {money(vals.get('perm_loan'))}")
    print(f"  Net Cash-Out at Refi    {money(vals.get('cash_out_at_refi'))}")
    print(f"  Going-In DSCR (Perm)    {mult(vals.get('going_in_dscr'))}")
    print(f"  Sale Price              {money(vals.get('sale_price'))}")
    print("  " + "-" * 60)
    print(f"  Project IRR             {pct(vals.get('project_irr'))}")
    print(f"  Equity Multiple         {mult(vals.get('equity_multiple'))}")
    print(f"  Total Profit            {money(vals.get('total_profit'))}")
    print(f"  LP IRR / GP IRR         {pct(vals.get('lp_irr'))} / {pct(vals.get('gp_irr'))}")
    print("  " + "-" * 60)
    print(f"  VERDICT: {irr_verdict(vals.get('project_irr'))}")
    _print_integrity(checks, errors)


def _print_integrity(checks, errors):
    print("\n  CHECKS")
    bad = 0
    for label, status in checks:
        mark = "OK " if status == "OK" else "!! "
        if status != "OK":
            bad += 1
        print(f"    {mark}{label}: {status}")
    if errors:
        print(f"\n  FORMULA ERRORS: {len(errors)}")
        for e in errors[:20]:
            print(f"    {e}")
        if len(errors) > 20:
            print(f"    ... and {len(errors) - 20} more")
    else:
        print("\n  FORMULA ERRORS: 0")
    if bad or errors:
        print("\n  MODEL INTEGRITY: FAIL - do not trust the numbers above.")
    else:
        print("\n  MODEL INTEGRITY: PASS")


SOURCE_LABEL = {
    "stoa": "Stoa (tested)",
    "updated": "changed",
    "derived": "by deal size",
    "override": "OVERRIDE",
    "deal docs": "deal docs",
}


def fmt_input(key, v):
    if v is None:
        return "-"
    if key.endswith("_pct") or key in (
        "vacancy", "bad_debt", "concessions", "rent_growth", "other_income_growth",
        "expense_growth", "ltv", "rate", "exit_cap", "cost_of_sale", "pref",
        "residual_lp", "closing_pct", "loan_fee_pct", "ltc", "index_rate", "spread",
        "gc_overhead", "hard_contingency", "soft_contingency", "gc_profit",
        "developer_fee", "valuation_cap", "perm_ltv", "mip", "perm_fee",
        "land_closing_pct", "constr_loan_fee",
        "refi_rate", "refi_ltv", "refi_valuation_cap",
    ):
        return f"{v * 100:.2f}%"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.3f}"
    return f"{v:,.0f}"


def print_provenance(rows, overrides):
    print("\n  INPUT PROVENANCE (deal docs > override > template)")
    print(f"    {'Input':<22}{'Value':>11}  {'Source':<14}Why")
    for r in rows:
        val = fmt_input(r["key"], r["value"])
        src = SOURCE_LABEL.get(r["source"], r["source"])
        note = (r["note"] or "")[:62]
        print(f"    {r['key']:<22}{val:>11}  {src:<14}{note}")
    unknown = [k for k in overrides if k not in {r["key"] for r in rows}]
    if unknown:
        print(f"    (also set: {', '.join(unknown)})")


def print_benchmark_table(rows, units):
    print("\n  T-12 vs MODEL DEFAULTS ($/unit/yr, %d units)" % units)
    print(f"    {'Line':<26}{'Actual':>10}{'Model':>10}{'Stoa':>10}{'Dev':>9}  Flag")
    for r in rows:
        a = f"{r['actual_per_unit']:,.0f}" if r["actual_per_unit"] is not None else "-"
        d = f"{r['deviation']*100:+.0f}%" if r["deviation"] is not None else "-"
        print(f"    {r['key']:<26}{a:>10}{r['current']:>10,}{r['stoa']:>10,}{d:>9}  {r['flag']}")
