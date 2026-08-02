#!/usr/bin/env python3
"""Pure-Python mirror of Multifamily_Acquisition_Model.xlsx.

Replicates every calculation in the workbook with no LibreOffice dependency.
Verified against three real deals; see test_pymodel.py.

  run(inputs)           -> full model output dict
  solve_price(inputs, target_irr) -> bisect to target IRR
  tornado(inputs)       -> sensitivity by factor
  monte_carlo(inputs)   -> P10/P50/P90 and P(IRR>=13%)

CLI:
  python3 tools/pymodel.py --deal baker-trails
  python3 tools/pymodel.py --deal baker-trails --solve 0.16
  python3 tools/pymodel.py --deal baker-trails --tornado
  python3 tools/pymodel.py --deal baker-trails --mc
"""

import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import latax


# ---------------------------------------------------------------------------
# Finance primitives (stdlib only, no numpy)
# ---------------------------------------------------------------------------

def _pv_annuity(rate_per_period, n_periods, payment):
    """PV of level payments — Excel PV(rate, nper, pmt, 0, 0) semantics.

    Returns the magnitude (positive number), matching Excel's sign convention
    for loan sizing: PV(rate/12, years*12, -monthly_payment) > 0.
    """
    if rate_per_period == 0:
        return payment * n_periods
    return payment * (1 - (1 + rate_per_period) ** (-n_periods)) / rate_per_period


def _fv_annuity(rate_per_period, n_periods, payment, pv):
    """Remaining loan balance after n_periods monthly payments.

    Replicates the workbook: MAX(0, FV(rate/12, 12, monthly_pmt, -beg_bal))
    Excel FV sign convention: pmt flows in the opposite direction from pv.
    Here pv is the beginning balance (positive) and payment is the periodic
    payment amount (positive), both from the lender's perspective.

    Formula: beg_bal*(1+r)^n - pmt*((1+r)^n - 1)/r
    """
    factor = (1 + rate_per_period) ** n_periods
    if rate_per_period == 0:
        remaining = pv - payment * n_periods
    else:
        remaining = pv * factor - payment * (factor - 1) / rate_per_period
    return max(0.0, remaining)


def _pmt(rate_per_period, n_periods, pv):
    """Monthly payment for a loan — Excel PMT(rate, nper, -pv) semantics."""
    if rate_per_period == 0:
        return pv / n_periods
    return pv * rate_per_period / (1 - (1 + rate_per_period) ** (-n_periods))


def _irr(cash_flows, guess=0.1, max_iter=200, tol=1e-8):
    """Annual IRR matching Excel IRR() semantics (Newton-Raphson with bisect fallback).

    cash_flows: list starting at year 0 (negative = outflow).
    Returns None if not found.
    """
    def _npv(r):
        try:
            return sum(cf / (1 + r) ** t for t, cf in enumerate(cash_flows))
        except (OverflowError, ZeroDivisionError):
            return float('nan')

    # Newton-Raphson
    r = guess
    for _ in range(max_iter):
        npv = _npv(r)
        if math.isnan(npv):
            r = guess * 0.5
            continue
        h = 1e-6
        npv_h = _npv(r + h)
        if math.isnan(npv_h):
            break
        d_npv = (npv_h - npv) / h
        if d_npv == 0:
            break
        r_new = r - npv / d_npv
        # Clamp to avoid explosive divergence
        r_new = max(-0.999, min(r_new, 50.0))
        if abs(r_new - r) < tol:
            return r_new
        r = r_new

    # Bisection fallback: bracket where sign changes
    lo, hi = -0.999, 10.0
    npv_lo = _npv(lo)
    npv_hi = _npv(hi)
    if math.isnan(npv_lo) or math.isnan(npv_hi):
        return None
    if npv_lo * npv_hi > 0:
        # Try a tighter range
        for lo_try in [-0.5, -0.2, 0.0]:
            if _npv(lo_try) * npv_hi <= 0:
                lo, npv_lo = lo_try, _npv(lo_try)
                break
        else:
            return None
    for _ in range(100):
        mid = (lo + hi) / 2
        npv_mid = _npv(mid)
        if math.isnan(npv_mid):
            return None
        if abs(npv_mid) < 1e-6 or (hi - lo) < tol:
            return mid
        if npv_lo * npv_mid < 0:
            hi = mid
        else:
            lo, npv_lo = mid, npv_mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def run(inputs: dict) -> dict:
    """Run the acquisition model.  Returns a rich dict of outputs.

    Required inputs:
        price           purchase price ($)
        unit_mix        list of {units, sf, rent} dicts (rent = $/month in-place)
        taxes_annual    annual property tax ($, already reassessed or as-is)

    Optional (all have defaults matching defaults.py / the workbook):
        closing_pct, loan_fee_pct, vacancy, bad_debt, concessions,
        other_income_unit_mo, rent_growth, other_income_growth,
        payroll, ga, marketing, rm, contract_services, utilities,
        other, insurance, mgmt_pct, asset_mgmt_pct, capex_unit,
        expense_growth, ltv, min_dscr, rate, amort_years, io_years,
        hold_years, exit_cap,  cost_of_sale, lp_pct, pref, residual_lp,
        refi_year, refi_valuation_cap, refi_ltv, refi_min_dscr, refi_rate,
        refi_amort_years, refi_cost_pct,
        reno_units, reno_cost_per_unit, reno_premium_month, reno_units_per_year,
        reno_downtime_months, reno_start_year
    """
    p = _merge_defaults(inputs)

    # --- Rent roll totals (Inputs!E11, G11, H11) ---
    total_units = sum(g["units"] for g in p["unit_mix"])
    if total_units == 0:
        raise ValueError("unit_mix must have at least one row with units > 0")
    wt_rent = sum(g["units"] * g["rent"] for g in p["unit_mix"]) / total_units
    # wt_sf not needed directly but consistent with workbook

    price = p["price"]
    closing_pct = p["closing_pct"]
    loan_fee_pct = p["loan_fee_pct"]
    vacancy = p["vacancy"]
    bad_debt = p["bad_debt"]
    concessions = p["concessions"]
    other_income_unit_mo = p["other_income_unit_mo"]
    rent_growth = p["rent_growth"]
    other_income_growth = p["other_income_growth"]
    payroll = p["payroll"]
    ga = p["ga"]
    marketing = p["marketing"]
    rm = p["rm"]
    contract_services = p["contract_services"]
    utilities = p["utilities"]
    other_exp = p["other"]
    insurance = p["insurance"]
    taxes_annual = p["taxes_annual"]
    mgmt_pct = p["mgmt_pct"]
    asset_mgmt_pct = p["asset_mgmt_pct"]
    capex_unit = p["capex_unit"]
    expense_growth = p["expense_growth"]
    ltv = p["ltv"]
    min_dscr = p["min_dscr"]
    rate = p["rate"]
    amort_years = p["amort_years"]
    io_years = p["io_years"]
    hold_years = p["hold_years"]
    exit_cap = p["exit_cap"]
    cost_of_sale = p["cost_of_sale"]
    lp_pct = p["lp_pct"]
    pref_rate = p["pref"]
    residual_lp = p["residual_lp"]
    refi_year = p["refi_year"]
    refi_valuation_cap = p["refi_valuation_cap"]
    refi_ltv = p["refi_ltv"]
    refi_min_dscr = p["refi_min_dscr"]
    refi_rate = p["refi_rate"]
    refi_amort_years = p["refi_amort_years"]
    refi_cost_pct = p["refi_cost_pct"]
    reno_units = p["reno_units"]
    reno_cost_per_unit = p["reno_cost_per_unit"]
    reno_premium_month = p["reno_premium_month"]
    reno_units_per_year = p["reno_units_per_year"]
    reno_downtime_months = p["reno_downtime_months"]
    reno_start_year = p["reno_start_year"]

    # --- Gross potential rent base (year 1) ---
    # Formula: SUMPRODUCT(units*rent)*12 — does NOT compound in base; year
    # exponent is (year-1), so year 1 = base * (1+g)^0 = base.
    gpr_base = sum(g["units"] * g["rent"] for g in p["unit_mix"]) * 12  # Inputs!H11 * total_units

    # --- Renovation budget ---
    reno_budget = reno_units * reno_cost_per_unit

    # --- Year 1 NOI for loan sizing ---
    # Must compute year-1 NOI first to size the loan.
    noi_y1 = _noi(1, gpr_base, wt_rent, total_units,
                  rent_growth, vacancy, bad_debt, concessions,
                  other_income_unit_mo, other_income_growth,
                  payroll, ga, marketing, rm, contract_services,
                  utilities, other_exp, insurance, taxes_annual,
                  mgmt_pct, expense_growth,
                  reno_units, reno_units_per_year, reno_start_year,
                  reno_premium_month, reno_downtime_months)

    # --- Loan sizing: MIN(LTV*price, PV of DSCR-constrained payment) ---
    # Workbook: MIN(B32*B2, PV(B34/12, B35*12, -(NOI_y1/min_dscr)/12))
    ltv_loan = ltv * price
    monthly_rate = rate / 12
    n_payments = amort_years * 12
    dscr_pmt = (noi_y1 / min_dscr) / 12   # max monthly payment DSCR allows
    dscr_loan = _pv_annuity(monthly_rate, n_payments, dscr_pmt)
    loan_amount = min(ltv_loan, dscr_loan)

    monthly_pmt = _pmt(monthly_rate, n_payments, loan_amount)
    annual_debt_service = monthly_pmt * 12  # used in non-IO years

    # --- Total equity required ---
    total_equity = price + price * closing_pct + loan_amount * loan_fee_pct - loan_amount + reno_budget
    lp_capital = total_equity * lp_pct
    gp_capital = total_equity - lp_capital

    # --- Going-in cap rate ---
    going_in_cap = noi_y1 / price if price > 0 else 0.0

    # --- Refi block (Inputs!F23:F28) ---
    refi_value = 0.0
    refi_new_loan = 0.0
    refi_old_payoff = 0.0
    refi_cash_out = 0.0
    refi_new_monthly_pmt = 0.0
    refi_new_annual_ds = 0.0

    if refi_year > 0:
        # Refi value = NOI at refi_year / refi_valuation_cap
        noi_refi = _noi(refi_year, gpr_base, wt_rent, total_units,
                        rent_growth, vacancy, bad_debt, concessions,
                        other_income_unit_mo, other_income_growth,
                        payroll, ga, marketing, rm, contract_services,
                        utilities, other_exp, insurance, taxes_annual,
                        mgmt_pct, expense_growth,
                        reno_units, reno_units_per_year, reno_start_year,
                        reno_premium_month, reno_downtime_months)
        refi_value = noi_refi / refi_valuation_cap if refi_valuation_cap > 0 else 0.0

        # New loan: MIN(LTV*value, PV of DSCR-constrained payment at refi NOI)
        refi_dscr_pmt = (noi_refi / refi_min_dscr) / 12
        refi_monthly_rate = refi_rate / 12
        refi_n_pmt = refi_amort_years * 12
        refi_dscr_loan = _pv_annuity(refi_monthly_rate, refi_n_pmt, refi_dscr_pmt)
        refi_new_loan = min(refi_ltv * refi_value, refi_dscr_loan)

        refi_new_monthly_pmt = _pmt(refi_monthly_rate, refi_n_pmt, refi_new_loan)
        refi_new_annual_ds = refi_new_monthly_pmt * 12

        # Old payoff = ending balance of original loan at end of refi_year
        refi_old_payoff = _loan_balance_at(loan_amount, monthly_rate, monthly_pmt, refi_year, io_years)

        refi_costs = refi_new_loan * refi_cost_pct
        refi_cash_out = refi_new_loan - refi_old_payoff - refi_costs

    # --- Annual cash flows (years 1..hold_years) ---
    years = list(range(1, hold_years + 1))

    # Debt schedule per year
    beg_bal = {}
    end_bal = {}
    interest_pmt = {}
    principal_pmt = {}
    total_ds = {}

    for yr in years:
        if yr == 1:
            bb = loan_amount
        elif refi_year > 0 and yr == refi_year + 1:
            bb = refi_new_loan
        else:
            bb = end_bal[yr - 1]
        beg_bal[yr] = bb

        # Determine which rate/payment applies (post-refi vs original)
        post_refi = refi_year > 0 and yr > refi_year
        if post_refi:
            yr_rate = refi_rate
            yr_pmt = refi_new_monthly_pmt
        else:
            yr_rate = rate
            yr_pmt = monthly_pmt

        # Interest-only check (original IO period only applies pre-refi)
        if (not post_refi) and yr <= io_years:
            # IO: balance stays flat; interest = balance * rate (annual)
            interest_pmt[yr] = bb * yr_rate
            principal_pmt[yr] = 0.0
            end_bal[yr] = bb
        else:
            # Amortizing: use FV to find ending balance
            if post_refi:
                eb = _fv_annuity(refi_rate / 12, 12, yr_pmt, bb)
            else:
                eb = _fv_annuity(rate / 12, 12, monthly_pmt, bb)
            end_bal[yr] = eb
            # Interest = annual_pmt*12 - principal_reduction (or directly from FV formula)
            # Excel: interest = 12*pmt - (beg_bal - end_bal)
            interest_pmt[yr] = 12 * yr_pmt - (bb - eb)
            principal_pmt[yr] = bb - eb

        total_ds[yr] = interest_pmt[yr] + principal_pmt[yr]

    # Income and expense by year
    egr = {}
    nri = {}
    other_income = {}
    noi = {}
    capex_reserve = {}
    asset_mgmt = {}
    cfbds = {}   # cash flow before debt service
    cfads = {}   # cash flow after debt service
    dscr = {}
    dscr_post_capex = {}

    for yr in years:
        noi_yr = _noi(yr, gpr_base, wt_rent, total_units,
                      rent_growth, vacancy, bad_debt, concessions,
                      other_income_unit_mo, other_income_growth,
                      payroll, ga, marketing, rm, contract_services,
                      utilities, other_exp, insurance, taxes_annual,
                      mgmt_pct, expense_growth,
                      reno_units, reno_units_per_year, reno_start_year,
                      reno_premium_month, reno_downtime_months)
        noi[yr] = noi_yr

        # EGR and other income for reporting
        egr[yr] = _egr(yr, gpr_base, wt_rent, total_units,
                       rent_growth, vacancy, bad_debt, concessions,
                       other_income_unit_mo, other_income_growth,
                       reno_units, reno_units_per_year, reno_start_year,
                       reno_premium_month, reno_downtime_months)

        cr = -total_units * capex_unit * (1 + expense_growth) ** (yr - 1)
        capex_reserve[yr] = cr
        am = -egr[yr] * asset_mgmt_pct
        asset_mgmt[yr] = am
        cfbds[yr] = noi_yr + cr + am
        cfads[yr] = cfbds[yr] - total_ds[yr]
        dscr[yr] = noi_yr / total_ds[yr] if total_ds[yr] > 0 else 0.0
        dscr_post_capex[yr] = (noi_yr + cr) / total_ds[yr] if total_ds[yr] > 0 else 0.0

    # --- Exit (sale at end of hold_years) ---
    # Sale price = forward NOI (year hold+1) / exit_cap
    noi_fwd = _noi(hold_years + 1, gpr_base, wt_rent, total_units,
                   rent_growth, vacancy, bad_debt, concessions,
                   other_income_unit_mo, other_income_growth,
                   payroll, ga, marketing, rm, contract_services,
                   utilities, other_exp, insurance, taxes_annual,
                   mgmt_pct, expense_growth,
                   reno_units, reno_units_per_year, reno_start_year,
                   reno_premium_month, reno_downtime_months)
    sale_price = noi_fwd / exit_cap if exit_cap > 0 else 0.0
    cost_of_sale_amt = sale_price * cost_of_sale
    loan_payoff = end_bal[hold_years]
    net_sale_proceeds = sale_price - cost_of_sale_amt - loan_payoff

    # --- Refi proceeds in the waterfall year ---
    refi_proceeds = {}
    for yr in years:
        if refi_year > 0 and yr == refi_year:
            refi_proceeds[yr] = refi_cash_out
        else:
            refi_proceeds[yr] = 0.0

    # --- Levered cash flows (year 0 + years 1..hold) ---
    # Year 0: -(price + closing + loan_fee - loan + reno_budget)
    # Year t: cfads[t] + refi_proceeds[t]  (+ exit items in hold year)
    lev_cf = [0.0] * (hold_years + 1)
    lev_cf[0] = -(price * (1 + closing_pct) + loan_amount * loan_fee_pct - loan_amount + reno_budget)

    for yr in years:
        lev_cf[yr] = (cfads[yr] if yr <= hold_years else 0.0) + refi_proceeds[yr]
    # Add exit at hold year
    lev_cf[hold_years] += sale_price - cost_of_sale_amt - loan_payoff

    # --- Unlevered cash flows ---
    unlev_cf = [0.0] * (hold_years + 1)
    unlev_cf[0] = -(price * (1 + closing_pct) + reno_budget)  # C50 formula: C42+C43+C52
    for yr in years:
        unlev_cf[yr] = (cfbds[yr] if yr <= hold_years else 0.0)
    unlev_cf[hold_years] += sale_price - cost_of_sale_amt

    levered_irr = _irr(lev_cf)
    unlevered_irr = _irr(unlev_cf)
    total_profit = sum(lev_cf)
    equity_multiple = 1 + total_profit / (-lev_cf[0]) if lev_cf[0] < 0 else 0.0
    avg_coc = sum(cfads[yr] for yr in years) / hold_years / total_equity

    # --- Waterfall ---
    wf = _waterfall(lev_cf, lp_capital, gp_capital, pref_rate, residual_lp, hold_years)

    return {
        # Loan
        "loan_amount": loan_amount,
        "monthly_pmt": monthly_pmt,
        "total_equity": total_equity,
        "lp_capital": lp_capital,
        "gp_capital": gp_capital,
        "going_in_cap": going_in_cap,
        # NOI and cash flows by year
        "noi": noi,            # dict yr->value
        "egr": egr,
        "capex_reserve": capex_reserve,
        "cfbds": cfbds,
        "cfads": cfads,
        "total_ds": total_ds,
        "beg_bal": beg_bal,
        "end_bal": end_bal,
        "dscr": dscr,
        "dscr_post_capex": dscr_post_capex,
        # Exit
        "noi_fwd": noi_fwd,
        "sale_price": sale_price,
        "cost_of_sale_amt": cost_of_sale_amt,
        "loan_payoff": loan_payoff,
        "net_sale_proceeds": net_sale_proceeds,
        # Returns
        "levered_irr": levered_irr,
        "unlevered_irr": unlevered_irr,
        "equity_multiple": equity_multiple,
        "total_profit": total_profit,
        "avg_coc": avg_coc,
        "lev_cf": lev_cf,
        "unlev_cf": unlev_cf,
        # Refi
        "refi_value": refi_value,
        "refi_new_loan": refi_new_loan,
        "refi_old_payoff": refi_old_payoff,
        "refi_cash_out": refi_cash_out,
        # Waterfall
        "lp_irr": wf["lp_irr"],
        "lp_em": wf["lp_em"],
        "lp_profit": wf["lp_profit"],
        "gp_irr": wf["gp_irr"],
        "gp_em": wf["gp_em"],
        "gp_profit": wf["gp_profit"],
        "wf_detail": wf["detail"],
        # Reno
        "reno_budget": reno_budget,
    }


def _reno_units_cumulative(yr, reno_units, reno_units_per_year, reno_start_year):
    """Units renovated by end of year yr (workbook: MIN(reno_units, MAX(0,(yr-start+1)*per_yr)))."""
    if reno_units == 0 or reno_units_per_year == 0:
        return 0.0
    return min(reno_units, max(0.0, (yr - reno_start_year + 1) * reno_units_per_year))


def _reno_premium_this_year(yr, reno_units, reno_units_per_year, reno_start_year,
                             reno_premium_month, rent_growth, wt_rent,
                             reno_downtime_months):
    """Annual rent premium added by renovation, plus downtime deduction.

    Returns (premium_annual, downtime_loss_annual) — both positive magnitudes.
    Downtime loss is deducted from vacancy in the workbook (row 6 formula).
    """
    cum_now = _reno_units_cumulative(yr, reno_units, reno_units_per_year, reno_start_year)
    cum_prev = _reno_units_cumulative(yr - 1, reno_units, reno_units_per_year, reno_start_year)
    units_this_yr = cum_now - cum_prev  # newly renovated this year

    g = (1 + rent_growth) ** (yr - 1)
    # Premium on all completed renovations (same formula as row 63)
    premium = cum_now * reno_premium_month * 12 * g
    # Downtime: units being turned × downtime months × avg rent (this year's)
    downtime_loss = units_this_yr * reno_downtime_months * wt_rent * g

    return premium, downtime_loss


def _egr(yr, gpr_base, wt_rent, total_units,
          rent_growth, vacancy, bad_debt, concessions,
          other_income_unit_mo, other_income_growth,
          reno_units, reno_units_per_year, reno_start_year,
          reno_premium_month, reno_downtime_months):
    """Effective Gross Revenue = NRI + Other Income (rows 9+10)."""
    g = (1 + rent_growth) ** (yr - 1)

    # GPR including reno premium (workbook row 5)
    cum = _reno_units_cumulative(yr, reno_units, reno_units_per_year, reno_start_year)
    gpr = (gpr_base + cum * reno_premium_month * 12) * g

    # Vacancy + downtime (row 6): -(gpr * vacancy_pct + downtime_loss)
    cum_prev = _reno_units_cumulative(yr - 1, reno_units, reno_units_per_year, reno_start_year)
    units_turned = cum - cum_prev
    downtime_loss = units_turned * reno_downtime_months * wt_rent * g
    physical_vac = gpr * vacancy

    # Bad debt and concessions on base GPR (rows 7, 8) — workbook applies to gpr, not gpr+premium
    bad = gpr * bad_debt
    conc = gpr * concessions

    # NRI = gpr - vacancy - downtime - bad_debt - concessions
    nri = gpr - physical_vac - downtime_loss - bad - conc

    # Other income: units * oi_mo * 12 * (1 - vacancy) * (1+other_growth)^(yr-1)
    og = (1 + other_income_growth) ** (yr - 1)
    oi = total_units * other_income_unit_mo * 12 * (1 - vacancy) * og

    return nri + oi


def _noi(yr, gpr_base, wt_rent, total_units,
         rent_growth, vacancy, bad_debt, concessions,
         other_income_unit_mo, other_income_growth,
         payroll, ga, marketing, rm, contract_services,
         utilities, other_exp, insurance, taxes_annual,
         mgmt_pct, expense_growth,
         reno_units, reno_units_per_year, reno_start_year,
         reno_premium_month, reno_downtime_months):
    """Net Operating Income for year yr.  Matches workbook rows 11+24."""
    egr = _egr(yr, gpr_base, wt_rent, total_units,
               rent_growth, vacancy, bad_debt, concessions,
               other_income_unit_mo, other_income_growth,
               reno_units, reno_units_per_year, reno_start_year,
               reno_premium_month, reno_downtime_months)

    xg = (1 + expense_growth) ** (yr - 1)

    # Per-unit expenses (rows 14-21)
    exp_pu = (payroll + ga + marketing + rm + contract_services +
               utilities + other_exp + insurance) * total_units * xg

    # Property taxes: flat total grown at expense_growth (row 22)
    tax_exp = taxes_annual * xg

    # Management fee: % of EGR (row 23)
    mgmt = egr * mgmt_pct

    total_opex = -(exp_pu + tax_exp + mgmt)
    return egr + total_opex  # EGR + (negative) opex


def _loan_balance_at(loan_amount, monthly_rate, monthly_pmt, year, io_years):
    """Ending balance of the original loan at end of `year`."""
    bal = loan_amount
    for yr in range(1, year + 1):
        if yr <= io_years:
            pass  # IO: balance unchanged
        else:
            bal = _fv_annuity(monthly_rate, 12, monthly_pmt, bal)
    return bal


def _waterfall(lev_cf, lp_capital, gp_capital, pref_rate, residual_lp, hold_years):
    """LP/GP waterfall matching the Waterfall tab exactly.

    Tier 1: LP pref (8%) accrues on unreturned capital, paid from distributable CF
    Tier 2: Return of LP capital
    Tier 3: Return of GP capital
    Tier 4: Residual split (residual_lp / (1-residual_lp))

    distributable CF = lev_cf[yr] for yr >= 1 (year 0 is the equity outflow).
    LP initial investment = -lp_capital (outflow at year 0).
    """
    years = list(range(1, hold_years + 1))

    # Year 0 outflows for IRR calc
    lp_flows = [-lp_capital] + [0.0] * hold_years
    gp_flows = [-gp_capital] + [0.0] * hold_years

    # Waterfall state
    pref_owed_beg = {}
    pref_accrual = {}
    pref_paid = {}
    pref_owed_end = {}
    lp_unreturned_beg = {}
    lp_roc = {}
    lp_unreturned_end = {}
    gp_unreturned_beg = {}
    gp_roc = {}
    gp_unreturned_end = {}
    residual_cf = {}
    lp_residual = {}
    gp_residual = {}
    lp_net = {}
    gp_net = {}

    for yr in years:
        dist = lev_cf[yr]   # distributable cash flow this year

        # Pref owed beginning
        if yr == 1:
            pref_owed_beg[yr] = 0.0
            lp_unreturned_beg[yr] = lp_capital
            gp_unreturned_beg[yr] = gp_capital
        else:
            pref_owed_beg[yr] = pref_owed_end[yr - 1]
            lp_unreturned_beg[yr] = lp_unreturned_end[yr - 1]
            gp_unreturned_beg[yr] = gp_unreturned_end[yr - 1]

        # Pref accrues on UNRETURNED LP capital (not original capital)
        pref_accrual[yr] = lp_unreturned_beg[yr] * pref_rate if yr <= hold_years else 0.0
        pref_paid[yr] = min(max(dist, 0.0), pref_owed_beg[yr] + pref_accrual[yr])
        pref_owed_end[yr] = pref_owed_beg[yr] + pref_accrual[yr] - pref_paid[yr]

        # Tier 2: LP return of capital
        lp_roc[yr] = min(max(dist - pref_paid[yr], 0.0), lp_unreturned_beg[yr])
        lp_unreturned_end[yr] = lp_unreturned_beg[yr] - lp_roc[yr]

        # Tier 3: GP return of capital
        gp_roc[yr] = min(max(dist - pref_paid[yr] - lp_roc[yr], 0.0), gp_unreturned_beg[yr])
        gp_unreturned_end[yr] = gp_unreturned_beg[yr] - gp_roc[yr]

        # Tier 4: Residual
        residual_cf[yr] = max(dist - pref_paid[yr] - lp_roc[yr] - gp_roc[yr], 0.0)
        lp_residual[yr] = residual_cf[yr] * residual_lp
        gp_residual[yr] = residual_cf[yr] * (1.0 - residual_lp)

        # Net distributions
        lp_net[yr] = pref_paid[yr] + lp_roc[yr] + lp_residual[yr]
        gp_net[yr] = gp_roc[yr] + gp_residual[yr]

        lp_flows[yr] = lp_net[yr]
        gp_flows[yr] = gp_net[yr]

    lp_irr = _irr(lp_flows)
    gp_irr = _irr(gp_flows)
    lp_profit = sum(lp_flows)
    gp_profit = sum(gp_flows)
    lp_em = 1 + lp_profit / lp_capital if lp_capital > 0 else 0.0
    gp_em = 1 + gp_profit / gp_capital if gp_capital > 0 else 0.0

    return {
        "lp_irr": lp_irr,
        "lp_em": lp_em,
        "lp_profit": lp_profit,
        "gp_irr": gp_irr,
        "gp_em": gp_em,
        "gp_profit": gp_profit,
        "detail": {
            "pref_owed_beg": pref_owed_beg,
            "pref_accrual": pref_accrual,
            "pref_paid": pref_paid,
            "pref_owed_end": pref_owed_end,
            "lp_unreturned_beg": lp_unreturned_beg,
            "lp_roc": lp_roc,
            "lp_unreturned_end": lp_unreturned_end,
            "gp_unreturned_beg": gp_unreturned_beg,
            "gp_roc": gp_roc,
            "gp_unreturned_end": gp_unreturned_end,
            "residual_cf": residual_cf,
            "lp_residual": lp_residual,
            "gp_residual": gp_residual,
            "lp_net": lp_net,
            "gp_net": gp_net,
        },
    }


def _merge_defaults(inputs):
    """Fill in defaults for any keys not supplied by the caller."""
    defaults = {
        "closing_pct": 0.02,
        "loan_fee_pct": 0.01,
        "vacancy": 0.07,
        "bad_debt": 0.005,
        "concessions": 0.0,
        "other_income_unit_mo": 40,
        "rent_growth": 0.02,
        "other_income_growth": 0.02,
        "payroll": 0,
        "ga": 480,
        "marketing": 415,
        "rm": 515,
        "contract_services": 0,
        "utilities": 755,
        "other": 105,
        "insurance": 2000,
        "taxes_annual": 0,
        "mgmt_pct": 0.08,
        "asset_mgmt_pct": 0.0,
        "capex_unit": 250,
        "expense_growth": 0.025,
        "ltv": 0.75,
        "min_dscr": 1.30,
        "rate": 0.0675,
        "amort_years": 25,
        "io_years": 0,
        "hold_years": 5,
        "exit_cap": None,  # will be set to going_in + 50bps if None
        "cost_of_sale": 0.03,
        "lp_pct": 0.90,
        "pref": 0.08,
        "residual_lp": 0.70,
        "refi_year": 0,
        "refi_valuation_cap": 0.07,
        "refi_ltv": 0.75,
        "refi_min_dscr": 1.30,
        "refi_rate": 0.0675,
        "refi_amort_years": 25,
        "refi_cost_pct": 0.02,
        "reno_units": 0,
        "reno_cost_per_unit": 0,
        "reno_premium_month": 0,
        "reno_units_per_year": 0,
        "reno_downtime_months": 1,
        "reno_start_year": 1,
    }
    p = dict(defaults)
    p.update(inputs)

    # exit_cap: if not provided, solve going-in cap + 50bps
    if p["exit_cap"] is None:
        # Need year-1 NOI to get going-in cap
        gpr_base = sum(g["units"] * g["rent"] for g in p["unit_mix"]) * 12
        wt_rent = (sum(g["units"] * g["rent"] for g in p["unit_mix"])
                   / sum(g["units"] for g in p["unit_mix"]))
        total_units = sum(g["units"] for g in p["unit_mix"])
        noi_y1 = _noi(1, gpr_base, wt_rent, total_units,
                      p["rent_growth"], p["vacancy"], p["bad_debt"], p["concessions"],
                      p["other_income_unit_mo"], p["other_income_growth"],
                      p["payroll"], p["ga"], p["marketing"], p["rm"], p["contract_services"],
                      p["utilities"], p["other"], p["insurance"], p["taxes_annual"],
                      p["mgmt_pct"], p["expense_growth"],
                      p["reno_units"], p["reno_units_per_year"], p["reno_start_year"],
                      p["reno_premium_month"], p["reno_downtime_months"])
        going_in = noi_y1 / p["price"] if p["price"] > 0 else 0.0
        p["exit_cap"] = round(going_in + 0.005, 5)

    return p


# ---------------------------------------------------------------------------
# Solve, tornado, monte_carlo
# ---------------------------------------------------------------------------

def solve_price(inputs: dict, target_irr: float, tol: float = 0.0001,
                max_iter: int = 50):
    """Bisect purchase price to hit target_irr.

    On every iteration: taxes are re-derived from price via latax (if
    location is in inputs), and exit_cap is re-derived as going-in + 50bps
    (inputs may not override exit_cap — it is always re-derived).

    Returns {price, irr} or None if unreachable.
    """
    base = dict(inputs)
    location = base.pop("location", None)
    commercial_share = base.pop("commercial_share", 0.0)
    base.pop("exit_cap", None)   # always re-derive

    def _irr_at(price):
        p = dict(base)
        p["price"] = price
        p["exit_cap"] = None   # force re-derive
        if location:
            tax, _ = latax.estimate_tax(price, location, commercial_share)
            if tax is not None:
                p["taxes_annual"] = tax
        try:
            r = run(p)
            return r["levered_irr"]
        except Exception:
            return None

    asking = inputs["price"]
    irr_hi = _irr_at(asking)
    if irr_hi is not None and irr_hi >= target_irr:
        return {"price": asking, "irr": irr_hi, "note": "already clears at asking"}

    lo = max(50_000, asking * 0.35)
    irr_lo = _irr_at(lo)
    if irr_lo is None or irr_lo < target_irr:
        return None

    hi = asking
    best = None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        irr_mid = _irr_at(mid)
        if irr_mid is None:
            hi = mid
            continue
        if irr_mid >= target_irr:
            best = {"price": mid, "irr": irr_mid}
            lo = mid
        else:
            hi = mid
        if abs(irr_mid - target_irr) < tol:
            break

    if best:
        best["price"] = round(best["price"] / 1000) * 1000
        best["irr"] = _irr_at(best["price"])
    return best


def tornado(inputs: dict) -> list[dict]:
    """Sensitivity table: IRR impact per stress factor, sorted by magnitude.

    Returns list of {factor, delta_irr, stressed_irr, direction}.
    """
    base = run(inputs)
    base_irr = base["levered_irr"]
    if base_irr is None:
        raise ValueError("base case IRR cannot be computed")

    stresses = [
        ("rate +50bps",   {"rate": inputs.get("rate", 0.0675) + 0.005}),
        ("rate -50bps",   {"rate": inputs.get("rate", 0.0675) - 0.005}),
        ("insurance +50%",{"insurance": inputs.get("insurance", 2000) * 1.5}),
        ("insurance -50%",{"insurance": inputs.get("insurance", 2000) * 0.5}),
        ("rents -5%",     {
            "unit_mix": [dict(g, rent=g["rent"] * 0.95) for g in inputs["unit_mix"]]
        }),
        ("exit_cap +100bps", {
            "exit_cap": (inputs.get("exit_cap") or base["going_in_cap"] + 0.005) + 0.01
        }),
        ("vacancy +3pts", {"vacancy": inputs.get("vacancy", 0.07) + 0.03}),
    ]

    results = []
    for label, overrides in stresses:
        p = dict(inputs)
        p.update(overrides)
        try:
            r = run(p)
            stressed_irr = r["levered_irr"]
            if stressed_irr is None:
                continue
            delta = stressed_irr - base_irr
            results.append({
                "factor": label,
                "base_irr": base_irr,
                "stressed_irr": stressed_irr,
                "delta_irr": delta,
            })
        except Exception:
            pass

    results.sort(key=lambda x: abs(x["delta_irr"]), reverse=True)
    return results


def monte_carlo(inputs: dict, n: int = 2000, seed: int = 42) -> dict:
    """Monte Carlo with triangular distributions.

    Variables:
        rent_growth:   tri(1%, 2%, 3%)
        expense_growth: tri(2%, 2.5%, 4%)
        exit_cap:      tri(going_in-25bps, going_in+50bps, going_in+150bps)
        vacancy:       tri(5%, 7%, 10%)
        insurance_mult: tri(0.9, 1.0, 1.5) applied to insurance input

    Returns {p10, p50, p90, p_above_13, n_valid, irr_samples}.
    """
    random.seed(seed)

    def _tri(lo, mid, hi):
        return random.triangular(lo, hi, mid)

    base_run = run(inputs)
    base_going_in = base_run["going_in_cap"]
    base_insurance = inputs.get("insurance", 2000)

    irrs = []
    for _ in range(n):
        p = dict(inputs)
        p["rent_growth"] = _tri(0.01, 0.02, 0.03)
        p["expense_growth"] = _tri(0.02, 0.025, 0.04)
        p["vacancy"] = _tri(0.05, 0.07, 0.10)
        ins_mult = _tri(0.9, 1.0, 1.5)
        p["insurance"] = base_insurance * ins_mult
        # exit cap drawn relative to going-in
        ec_delta = _tri(-0.0025, 0.005, 0.015)
        p["exit_cap"] = base_going_in + ec_delta
        try:
            r = run(p)
            if r["levered_irr"] is not None:
                irrs.append(r["levered_irr"])
        except Exception:
            pass

    if not irrs:
        return {"p10": None, "p50": None, "p90": None, "p_above_13": None, "n_valid": 0}

    irrs.sort()
    n_valid = len(irrs)

    def _percentile(sorted_list, pct):
        idx = (pct / 100) * (len(sorted_list) - 1)
        lo_i = int(idx)
        hi_i = min(lo_i + 1, len(sorted_list) - 1)
        frac = idx - lo_i
        return sorted_list[lo_i] + frac * (sorted_list[hi_i] - sorted_list[lo_i])

    p_above = sum(1 for x in irrs if x >= 0.13) / n_valid

    return {
        "p10": _percentile(irrs, 10),
        "p50": _percentile(irrs, 50),
        "p90": _percentile(irrs, 90),
        "p_above_13": p_above,
        "n_valid": n_valid,
        "irr_samples": irrs,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_deal(deal_name: str) -> dict:
    """Load inputs from a deal workbook's Inputs tab (data_only)."""
    import openpyxl
    root = Path(__file__).resolve().parent.parent
    wb_path = root / "deal-intake" / deal_name / f"{deal_name}_acq.xlsx"
    if not wb_path.exists():
        raise FileNotFoundError(f"No workbook at {wb_path}")

    wb = openpyxl.load_workbook(str(wb_path), data_only=True)
    ws = wb["Inputs"]

    def cell(addr):
        return ws[addr].value

    # Scalars
    price = cell("B2")
    closing_pct = cell("B3")
    loan_fee_pct = cell("B4")
    vacancy = cell("B9")
    bad_debt = cell("B10")
    concessions = cell("B11")
    other_income_unit_mo = cell("B12")
    rent_growth = cell("B13")
    other_income_growth = cell("B14")
    payroll = cell("B17")
    ga = cell("B18")
    marketing = cell("B19")
    rm = cell("B20")
    contract_services = cell("B21")
    utilities = cell("B22")
    other = cell("B23")
    insurance = cell("B24")
    taxes_annual = cell("B25")
    mgmt_pct = cell("B26")
    asset_mgmt_pct = cell("B27")
    capex_unit = cell("B28")
    expense_growth = cell("B29")
    ltv = cell("B32")
    min_dscr = cell("B33")
    rate = cell("B34")
    amort_years = cell("B35")
    io_years = cell("B36")
    hold_years = cell("B40")
    exit_cap = cell("B41")
    cost_of_sale = cell("B42")
    lp_pct = cell("B45")
    pref = cell("B47")
    residual_lp = cell("B48")
    refi_year = cell("F14")
    refi_valuation_cap = cell("F15")
    refi_ltv = cell("F16")
    refi_min_dscr = cell("F17")
    refi_rate = cell("F18")
    refi_amort_years = cell("F19")
    refi_cost_pct = cell("F20")
    reno_units = cell("F31")
    reno_cost_per_unit = cell("F32")
    reno_premium_month = cell("F33")
    reno_units_per_year = cell("F34")
    reno_downtime_months = cell("F35")
    reno_start_year = cell("F36")

    # Unit mix from rent roll (rows 3-10)
    unit_mix = []
    for row in range(3, 11):
        u = ws.cell(row=row, column=5).value  # E
        r = ws.cell(row=row, column=8).value  # H (rent)
        s = ws.cell(row=row, column=7).value  # G (sf)
        if u and u > 0:
            unit_mix.append({"units": u, "sf": s or 0, "rent": r or 0})

    def _v(x, default=0):
        return x if x is not None else default

    return {
        "price": _v(price),
        "unit_mix": unit_mix,
        "closing_pct": _v(closing_pct, 0.02),
        "loan_fee_pct": _v(loan_fee_pct, 0.01),
        "vacancy": _v(vacancy, 0.07),
        "bad_debt": _v(bad_debt, 0.005),
        "concessions": _v(concessions, 0.0),
        "other_income_unit_mo": _v(other_income_unit_mo, 40),
        "rent_growth": _v(rent_growth, 0.02),
        "other_income_growth": _v(other_income_growth, 0.02),
        "payroll": _v(payroll, 0),
        "ga": _v(ga, 480),
        "marketing": _v(marketing, 415),
        "rm": _v(rm, 515),
        "contract_services": _v(contract_services, 0),
        "utilities": _v(utilities, 755),
        "other": _v(other, 105),
        "insurance": _v(insurance, 2000),
        "taxes_annual": _v(taxes_annual, 0),
        "mgmt_pct": _v(mgmt_pct, 0.08),
        "asset_mgmt_pct": _v(asset_mgmt_pct, 0.0),
        "capex_unit": _v(capex_unit, 250),
        "expense_growth": _v(expense_growth, 0.025),
        "ltv": _v(ltv, 0.75),
        "min_dscr": _v(min_dscr, 1.30),
        "rate": _v(rate, 0.0675),
        "amort_years": _v(amort_years, 25),
        "io_years": _v(io_years, 0),
        "hold_years": _v(hold_years, 5),
        "exit_cap": _v(exit_cap, None),
        "cost_of_sale": _v(cost_of_sale, 0.03),
        "lp_pct": _v(lp_pct, 0.90),
        "pref": _v(pref, 0.08),
        "residual_lp": _v(residual_lp, 0.70),
        "refi_year": _v(refi_year, 0),
        "refi_valuation_cap": _v(refi_valuation_cap, 0.07),
        "refi_ltv": _v(refi_ltv, 0.75),
        "refi_min_dscr": _v(refi_min_dscr, 1.30),
        "refi_rate": _v(refi_rate, 0.0675),
        "refi_amort_years": _v(refi_amort_years, 25),
        "refi_cost_pct": _v(refi_cost_pct, 0.02),
        "reno_units": _v(reno_units, 0),
        "reno_cost_per_unit": _v(reno_cost_per_unit, 0),
        "reno_premium_month": _v(reno_premium_month, 0),
        "reno_units_per_year": _v(reno_units_per_year, 0),
        "reno_downtime_months": _v(reno_downtime_months, 1),
        "reno_start_year": _v(reno_start_year, 1),
    }


def main():
    ap = argparse.ArgumentParser(description="Python acquisition model")
    ap.add_argument("--deal", required=True, help="deal name under deal-intake/")
    ap.add_argument("--solve", type=float, metavar="IRR",
                    help="solve for price to hit this IRR (e.g. 0.16)")
    ap.add_argument("--tornado", action="store_true", help="sensitivity table")
    ap.add_argument("--mc", action="store_true", help="Monte Carlo (n=2000)")
    args = ap.parse_args()

    inputs = _load_deal(args.deal)
    r = run(inputs)

    print(f"\n=== {args.deal.upper()} ===")
    print(f"  Price:          ${inputs['price']:>12,.0f}")
    print(f"  Loan:           ${r['loan_amount']:>12,.0f}")
    print(f"  Equity:         ${r['total_equity']:>12,.0f}")
    print(f"  Going-in cap:   {r['going_in_cap']*100:>10.2f}%")
    print(f"  Y1 NOI:         ${r['noi'][1]:>12,.0f}")
    print(f"  Y1 DSCR:        {r['dscr'][1]:>10.2f}x")
    print(f"  Y1 DSCR(+capex):{r['dscr_post_capex'][1]:>10.2f}x")
    print(f"  Levered IRR:    {r['levered_irr']*100 if r['levered_irr'] else float('nan'):>10.2f}%")
    print(f"  Equity Multiple:{r['equity_multiple']:>10.2f}x")
    print(f"  LP IRR:         {r['lp_irr']*100 if r['lp_irr'] else float('nan'):>10.2f}%")
    print(f"  GP IRR:         {r['gp_irr']*100 if r['gp_irr'] else float('nan'):>10.2f}%")

    if args.solve:
        target = args.solve
        print(f"\n--- SOLVE: price to hit {target*100:.1f}% IRR ---")
        res = solve_price(inputs, target)
        if res is None:
            print("  Unreachable at any sensible price")
        elif res.get("note"):
            print(f"  Already clears at ${inputs['price']:,.0f}")
        else:
            disc = (1 - res["price"] / inputs["price"]) * 100
            print(f"  Price: ${res['price']:,.0f}  ({disc:.0f}% below asking, IRR {res['irr']*100:.1f}%)")

    if args.tornado:
        print("\n--- TORNADO ---")
        print(f"  {'Factor':<25} {'Stressed IRR':>12} {'Delta':>8}")
        for row in tornado(inputs):
            print(f"  {row['factor']:<25} {row['stressed_irr']*100:>11.2f}%"
                  f" {row['delta_irr']*100:>+7.2f}%")

    if args.mc:
        print("\n--- MONTE CARLO (n=2000) ---")
        mc = monte_carlo(inputs)
        print(f"  P10 IRR:  {mc['p10']*100:.2f}%")
        print(f"  P50 IRR:  {mc['p50']*100:.2f}%")
        print(f"  P90 IRR:  {mc['p90']*100:.2f}%")
        print(f"  P(IRR>=13%): {mc['p_above_13']*100:.1f}%")
        print(f"  Valid runs: {mc['n_valid']}/2000")


if __name__ == "__main__":
    main()
