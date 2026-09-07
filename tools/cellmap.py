"""Canonical cell addresses for both models. Single source of truth for the writer.

Derived by inspecting the workbooks produced by generators/build_acq.py and build_dev.py.
If a generator changes a row, change it here too.
"""

BLUE_RGB = "FF0000FF"

ERROR_TOKENS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")

# Unit types the development model can look up (Comps!F4:F6). Anything else
# returns 0 rent through the VLOOKUP and must be surfaced, not silently accepted.
DEV_LOOKUP_TYPES = ("1 BR/ 1 BA", "2 BR/ 2 BA", "3 BR/ 2 BA")

ACQ = {
    "file": "Multifamily_Acquisition_Model.xlsx",
    # Paste areas: (sheet, min_row, max_row, min_col, max_col)
    "paste_ranges": [
        ("Inputs", 3, 10, 5, 8),   # E3:H10 rent roll (units, type, SF, rent)
    ],
    "rent_roll": {
        "sheet": "Inputs",
        "first_row": 3,
        "last_row": 10,
        "col_units": 5,   # E
        "col_type": 6,    # F
        "col_sf": 7,      # G
        "col_rent": 8,    # H
    },
    "scalars": {
        "purchase_price": "Inputs!B2",
        "closing_pct": "Inputs!B3",
        "loan_fee_pct": "Inputs!B4",
        "vacancy": "Inputs!B9",
        "bad_debt": "Inputs!B10",
        "concessions": "Inputs!B11",
        "other_income_unit_mo": "Inputs!B12",
        "rent_growth": "Inputs!B13",
        "other_income_growth": "Inputs!B14",
        "payroll": "Inputs!B17",
        "ga": "Inputs!B18",
        "marketing": "Inputs!B19",
        "rm": "Inputs!B20",
        "contract_services": "Inputs!B21",
        "utilities": "Inputs!B22",
        "other": "Inputs!B23",
        "insurance": "Inputs!B24",
        "taxes_annual": "Inputs!B25",
        "mgmt_pct": "Inputs!B26",
        "asset_mgmt_pct": "Inputs!B27",
        "capex_unit": "Inputs!B28",
        "expense_growth": "Inputs!B29",
        "ltv": "Inputs!B32",
        "min_dscr": "Inputs!B33",
        "rate": "Inputs!B34",
        "amort_years": "Inputs!B35",
        "io_years": "Inputs!B36",
        "hold_years": "Inputs!B40",
        "exit_cap": "Inputs!B41",
        "cost_of_sale": "Inputs!B42",
        "lp_pct": "Inputs!B45",
        "pref": "Inputs!B47",
        "residual_lp": "Inputs!B48",
        "refi_year": "Inputs!F14",
        "refi_valuation_cap": "Inputs!F15",
        "refi_ltv": "Inputs!F16",
        "refi_min_dscr": "Inputs!F17",
        "refi_rate": "Inputs!F18",
        "refi_amort_years": "Inputs!F19",
        "refi_cost_pct": "Inputs!F20",
        "reno_units": "Inputs!F31",
        "reno_cost_per_unit": "Inputs!F32",
        "reno_premium_month": "Inputs!F33",
        "reno_units_per_year": "Inputs!F34",
        "reno_downtime_months": "Inputs!F35",
        "reno_start_year": "Inputs!F36",
    },
    "outputs": {
        "total_units": "Inputs!B5",
        "price_per_unit": "Inputs!B6",
        "loan_amount": "Inputs!B52",
        "monthly_debt_service": "Inputs!B53",
        "total_equity": "Inputs!B54",
        "lp_capital": "Inputs!B55",
        "gp_capital": "Inputs!B56",
        "going_in_cap": "Inputs!B57",
        "noi_yr1": "Annual CF!D25",
        "egr_yr1": "Annual CF!D11",
        "opex_yr1": "Annual CF!D24",
        "dscr_yr1": "Annual CF!D38",
        "dscr_yr1_post_reserve": "Annual CF!D40",
        "cfads_yr1": "Annual CF!D37",
        "unlevered_irr": "Annual CF!C54",
        "levered_irr": "Annual CF!C55",
        "equity_multiple": "Annual CF!C56",
        "total_profit": "Annual CF!C57",
        "avg_coc": "Annual CF!C58",
        "avg_dscr": "Annual CF!C59",
        "lp_irr": "Waterfall!C30",
        "lp_em": "Waterfall!C31",
        "lp_profit": "Waterfall!C32",
        "gp_irr": "Waterfall!C33",
        "refi_value": "Inputs!F23",
        "refi_new_loan": "Inputs!F24",
        "refi_old_payoff": "Inputs!F25",
        "refi_cash_out": "Inputs!F27",
        "reno_budget": "Inputs!F38",
        "reno_years": "Inputs!F39",
        "noi_stabilized": "Annual CF!H25",
    },
    "checks": ("Checks", 2, 13),
}

DEV = {
    "file": "Multifamily_Development_Model.xlsx",
    "paste_ranges": [
        ("Comps", 10, 80, 4, 8),   # D10:H80 comp data
        ("Inputs", 3, 8, 5, 7),    # E3:G8 unit mix (units, type, SF)
        ("Inputs", 3, 8, 11, 11),  # K3:K8 delta vs comp
    ],
    "unit_mix": {
        "sheet": "Inputs",
        "first_row": 3,
        "last_row": 8,
        "col_units": 5,   # E
        "col_type": 6,    # F
        "col_sf": 7,      # G
        "col_delta": 11,  # K
    },
    "comps": {
        "sheet": "Comps",
        "first_row": 10,
        "last_row": 80,
        "col_include": 4,  # D
        "col_units": 5,    # E
        "col_type": 6,     # F
        "col_sf": 7,       # G
        "col_rent": 8,     # H
    },
    "scalars": {
        "land_price": "Inputs!B2",
        "land_closing_pct": "Inputs!B3",
        "construction_months": "Inputs!B8",
        "construction_start_month": "Inputs!B9",
        "leaseup_start_month": "Inputs!B10",
        "leaseup_months": "Inputs!B11",
        "vacancy": "Inputs!B16",
        "bad_debt": "Inputs!B17",
        "other_income_unit_mo": "Inputs!B18",
        "rent_growth": "Inputs!B19",
        "hard_cost_psf": "Inputs!B22",
        "hard_cost_adder": "Inputs!B23",
        "gc_overhead": "Inputs!B24",
        "hard_contingency": "Inputs!B25",
        "soft_contingency": "Inputs!B26",
        "gc_profit": "Inputs!B27",
        "developer_fee": "Inputs!B28",
        "impact_fee_unit": "Inputs!B29",
        "payroll": "Inputs!B35",
        "ga": "Inputs!B36",
        "marketing": "Inputs!B37",
        "rm": "Inputs!B38",
        "contract_services": "Inputs!B39",
        "utilities": "Inputs!B40",
        "other": "Inputs!B41",
        "insurance": "Inputs!B42",
        "taxes_construction": "Inputs!B43",
        "taxes_stabilized": "Inputs!B44",
        "mgmt_pct": "Inputs!B45",
        "asset_mgmt_pct": "Inputs!B46",
        "capex_unit": "Inputs!B47",
        "expense_growth": "Inputs!B48",
        "ltc": "Inputs!B51",
        "index_rate": "Inputs!B52",
        "spread": "Inputs!B53",
        "constr_loan_fee": "Inputs!B55",
        "refi_month": "Inputs!B58",
        "valuation_cap": "Inputs!B59",
        "perm_ltv": "Inputs!B60",
        "perm_min_dscr": "Inputs!B61",
        "perm_rate": "Inputs!B62",
        "perm_amort_months": "Inputs!B63",
        "mip": "Inputs!B64",
        "perm_fee": "Inputs!B65",
        "sell_or_hold": "Inputs!B69",
        "sale_month": "Inputs!B70",
        "exit_cap": "Inputs!B71",
        "cost_of_sale": "Inputs!B72",
        "lp_pct": "Inputs!B75",
        "pref": "Inputs!B76",
        "residual_lp": "Inputs!B77",
        "gp_in_kind": "Inputs!B78",
    },
    "outputs": {
        "total_units": "Summary!B3",
        "nrsf": "Summary!B4",
        "total_project_cost": "Summary!B5",
        "cost_per_unit": "Summary!B6",
        "cost_per_sf": "Summary!B7",
        "constr_loan_peak": "Summary!B8",
        "effective_ltc": "Summary!B9",
        "net_equity": "Summary!B10",
        "stabilized_noi": "Summary!B11",
        "development_yield": "Summary!B12",
        "refi_value": "Summary!B13",
        "perm_loan": "Summary!B14",
        "cash_out_at_refi": "Summary!B15",
        "going_in_dscr": "Summary!B16",
        "sale_price": "Summary!B17",
        "project_irr": "Summary!B18",
        "equity_multiple": "Summary!B19",
        "total_profit": "Summary!B20",
        "lp_irr": "Summary!B21",
        "gp_irr": "Summary!B22",
    },
    "checks": ("Checks", 2, 8),
}

MODELS = {"acq": ACQ, "dev": DEV}

# Per-unit/yr operating expense benchmarks. Stoa = their exact ~2023 figures
# (Benchmarks tabs, reference only). Current = this project's updated defaults.
BENCHMARKS = {
    "payroll":   {"stoa": 1569, "current": 1715},
    "ga":        {"stoa": 438,  "current": 480},
    "marketing": {"stoa": 379,  "current": 415},
    "rm":        {"stoa": 472,  "current": 515},
    "utilities": {"stoa": 692,  "current": 755},
    "other":     {"stoa": 95,   "current": 105},
    "insurance": {"stoa": 1164, "current": 2000},
}

EXPENSE_LABELS = {
    "payroll": "Payroll",
    "ga": "General & Administrative",
    "marketing": "Marketing",
    "rm": "Repairs & Maintenance",
    "contract_services": "Contract Services",
    "utilities": "Utilities",
    "other": "Other",
    "insurance": "Insurance",
    "taxes_annual": "Property Taxes (total $)",
    "mgmt_pct": "Management Fee (% EGR)",
}

# IRR verdict bands from CLAUDE.md. Target is aggressive; do not soften.
IRR_REALISTIC = (0.12, 0.14)
IRR_TARGET = (0.16, 0.17)
# The house pursue ladder (CLAUDE.md, adopted 2026-08-02): 13% is the minimum
# to pursue, 16% strong, 22% ideal. The floor OUTRANKS the realistic band.
PURSUE_FLOOR, PURSUE_STRONG, PURSUE_IDEAL = 0.13, 0.16, 0.22
