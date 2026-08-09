"""Template defaults with provenance.

Source of truth is the Stoa-derived Excel models. Every input carries where its
value came from, so a deal sheet can always be audited back to either Stoa's
tested figure or a documented change.

Precedence when filling a model: manual override > parsed from the deal's own
documents > template default.
"""

import sys

# value, source, note
# source: "stoa"    = Stoa's exact tested figure, unchanged
#         "updated" = changed from Stoa, reason recorded (see Benchmarks tabs)
#         "derived" = computed from deal size at runtime
ACQ_TEMPLATE = {
    "closing_pct":          (0.02,   "updated", "Stoa modeled land closing at 0.75%; 2% is typical for a small acquisition"),
    "loan_fee_pct":         (0.01,   "updated", "Stoa used a $95k flat construction fee; 1% suits bank acquisition debt"),
    "vacancy":              (0.07,   "stoa",    "7% physical vacancy"),
    "bad_debt":             (0.005,  "stoa",    "Stoa carried bad debt inside vacancy at 0.75%; broken out here"),
    "concessions":          (0.0,    "stoa",    "zero on a stabilized asset - raise for value-add lease-up"),
    "other_income_unit_mo": (40,     "updated", "Stoa itemized to $131/unit/mo at 288 units; small props support far less"),
    "rent_growth":          (0.02,   "stoa",    "2% annual"),
    "other_income_growth":  (0.02,   "updated", "Stoa used 0.5%"),
    "payroll":              (None,   "derived", "0 under 20 units (no on-site staff); $1,715 at or above"),
    "ga":                   (480,    "updated", "Stoa 438, escalated ~3%/yr from their 2023 budgets"),
    "marketing":            (415,    "updated", "Stoa 379, escalated"),
    "rm":                   (515,    "updated", "Stoa 472, escalated"),
    "contract_services":    (0,      "stoa",    "Stoa carried 0 - folded into R&M"),
    "utilities":            (755,    "updated", "Stoa 692, escalated"),
    "other":                (105,    "updated", "Stoa 95, escalated"),
    "insurance":            (2000,   "updated", "Stoa 1,164 (2023). LA insurance crisis - quote every deal"),
    "mgmt_pct":             (None,   "derived", "8% under 50 units, 5% to 150, 3% above (Stoa self-managed at 3%)"),
    "asset_mgmt_pct":       (0.0,    "updated", "Stoa charged 1% of NRI; zero until you have an asset-mgmt entity"),
    "capex_unit":           (250,    "stoa",    "$250/unit/yr reserve"),
    "expense_growth":       (0.025,  "updated", "Stoa 1% - too low against recent insurance and labor inflation"),
    "ltv":                  (0.75,   "stoa",    "75% max LTV"),
    "min_dscr":             (1.30,   "updated", "banks require 1.25; sized at 1.30 as the house standard (adopted 2026-08-02, Stoa's own hurdle) - cushion the lender doesn't demand but the LP deserves"),
    "rate":                 (0.0675, "updated", "WSJ Prime 6.75% as of Dec 11 2025 - verify before every deal"),
    "amort_years":          (25,     "updated", "Stoa's HUD perm was 420 months; bank acquisition debt is 25 years"),
    "io_years":             (0,      "updated", "no interest-only by default"),
    "hold_years":           (5,      "stoa",    "5-year hold"),
    "exit_cap":             (None,   "derived", "going-in cap + 50bps. Stoa's 6.5% was institutional Class A - on a small deal it sits below going-in and manufactures IRR"),
    "cost_of_sale":         (0.03,   "updated", "Stoa 0.75% selling $30M+ off-market; small deals pay brokerage"),
    "refi_year":            (0,      "updated", "0 = no refinance. Set --refi-year 3 to model repaying your investor"),
    "refi_valuation_cap":   (0.07,   "updated", "cap the appraiser uses at refi. Stoa refied at 5.5%; that was institutional"),
    "refi_ltv":             (0.75,   "stoa",    "75% LTV on the new loan"),
    "refi_min_dscr":        (1.30,   "updated", "banks size the refi on DSCR too - held to the same 1.30 house standard"),
    "refi_rate":            (0.0675, "updated", "assume today's rate; change if you expect a different environment"),
    "refi_amort_years":     (25,     "updated", "25-year amortization on the new loan"),
    "refi_cost_pct":        (0.02,   "stoa",    "~2% of proceeds, matching Stoa's perm loan fee"),
    "lp_pct":               (0.90,   "updated", "set to 1.0 if your investor funds all equity"),
    "pref":                 (0.08,   "stoa",    "8% preferred return"),
    "residual_lp":          (0.70,   "updated", "Stoa ran a 4-tier promote; this is the simplified residual split"),
}

DEV_TEMPLATE = {
    "land_closing_pct":  (0.0075, "stoa",    "0.75% of land"),
    "vacancy":           (0.07,   "stoa",    ""),
    "bad_debt":          (0.005,  "stoa",    ""),
    "rent_growth":       (0.02,   "stoa",    ""),
    "hard_cost_psf":     (135,    "updated", "Stoa built at $122.23/SF via DSLD vertical integration. $165 without that advantage; market GC is $160-180"),
    "hard_cost_adder":   (1000000, "stoa",   "fixed adder in their hard cost formula"),
    "gc_overhead":       (0.05,   "stoa",    "5% grossed up"),
    "hard_contingency":  (0.10,   "stoa",    ""),
    "soft_contingency":  (0.02,   "stoa",    ""),
    "gc_profit":         (0.04,   "stoa",    ""),
    "developer_fee":     (0.035,  "stoa",    "plus the 0.5% inside soft costs"),
    "impact_fee_unit":   (4127,   "stoa",    ""),
    "payroll":           (1715,   "updated", "Stoa 1,569, escalated"),
    "ga":                (480,    "updated", "Stoa 438, escalated"),
    "marketing":         (415,    "updated", "Stoa 379, escalated"),
    "rm":                (515,    "updated", "Stoa 472, escalated"),
    "contract_services": (0,      "stoa",    ""),
    "utilities":         (755,    "updated", "Stoa 692, escalated"),
    "other":             (105,    "updated", "Stoa 95, escalated"),
    "insurance":         (2000,   "updated", "Stoa 1,164 - LA repricing"),
    "mgmt_pct":          (0.03,   "stoa",    "3% of EGR - valid at development scale"),
    "asset_mgmt_pct":    (0.01,   "stoa",    "1%"),
    "capex_unit":        (250,    "stoa",    ""),
    "expense_growth":    (0.025,  "updated", "Stoa 1%"),
    "ltc":               (0.75,   "stoa",    "75% loan-to-cost"),
    "index_rate":        (0.0675, "updated", "WSJ Prime, was 7.00% in their model"),
    "spread":            (0.005,  "stoa",    "Prime + 0.50%"),
    "constr_loan_fee":   (0.005,  "updated", "Stoa used $95k flat; 0.5% of loan scales"),
    "valuation_cap":     (0.06,   "updated", "Stoa 5.5% - too tight at 2026 pricing"),
    "perm_ltv":          (0.75,   "stoa",    ""),
    "perm_min_dscr":     (1.176,  "stoa",    "HUD 223(f)"),
    "perm_amort_months": (420,    "stoa",    "35 years"),
    "mip":               (0.0025, "stoa",    "0.25% HUD MIP"),
    "perm_fee":          (0.02,   "stoa",    "~2.14% of proceeds in their model"),
    "exit_cap":          (0.065,  "stoa",    ""),
    "cost_of_sale":      (0.0075, "stoa",    ""),
    "pref":              (0.08,   "stoa",    ""),
    "residual_lp":       (0.70,   "updated", "simplified from their 4-tier promote"),
}

TEMPLATES = {"acq": ACQ_TEMPLATE, "dev": DEV_TEMPLATE}

# Keys whose model input is a fraction (0.08, not 8). Inferred from the
# template values; derived/zero-default fraction keys added by name since
# their template value carries no type information.
FRACTION_KEYS = {k for t in TEMPLATES.values() for k, (v, _, _) in t.items()
                 if k.endswith("_pct") or (isinstance(v, float) and 0 < v <= 1)}
FRACTION_KEYS |= {"exit_cap", "concessions"}


def derive(key, units):
    """Deal-size-dependent defaults."""
    if units is None:
        return None, "unit count unknown - not written"
    if key == "payroll":
        if units < 20:
            return 0, f"{units} units - no on-site staff, payroll folded into the management fee"
        return 1715, f"{units} units - on-site payroll applies"
    if key == "exit_cap":
        # Solved in a second pass once the going-in cap is known. Left unwritten
        # on the first pass so the model keeps its own default.
        return None, "solved after first recalc: going-in cap + 50bps"
    if key == "mgmt_pct":
        if units < 50:
            return 0.08, f"{units} units - third-party management on small props runs 7-10%"
        if units <= 150:
            return 0.05, f"{units} units - mid-size third-party management"
        return 0.03, f"{units} units - at scale, matches Stoa's self-managed 3%"
    return None, "no rule"


def parse_override(text):
    """'mgmt_pct=6%' or 'insurance=1800' -> (key, float)."""
    if "=" not in text:
        raise ValueError(f"--set expects key=value, got {text!r}")
    k, v = text.split("=", 1)
    k, v = k.strip(), v.strip()
    if v.endswith("%"):
        return k, float(v[:-1]) / 100.0
    val = float(v)
    # A bare 8 on a fraction key writes 800% into the model.
    if k in FRACTION_KEYS and val > 1:
        if val > 1.5:
            raise ValueError(f"{k}={v}: {k} is a fraction - did you mean {v}%? "
                             f"Write {k}={v}% or {k}={val / 100}")
        print(f"WARNING: --set {k}={v} taken literally as {val * 100:.0f}% - fraction "
              f"keys are normally <= 1. Use {k}={v}% if you meant {v} percent.",
              file=sys.stderr)
    return k, val


def resolve(model, units, parsed_keys, overrides):
    """-> list of {key, value, source, note} for every template input.

    parsed_keys: keys already supplied by the deal's own documents (skipped here
    so a T-12 always beats a template default).
    """
    tmpl = TEMPLATES[model]
    rows = []
    for key, (val, source, note) in tmpl.items():
        if key in overrides:
            rows.append({"key": key, "value": overrides[key], "source": "override", "note": "set on the command line"})
            continue
        if key in parsed_keys:
            rows.append({"key": key, "value": None, "source": "deal docs", "note": "taken from the deal's own documents"})
            continue
        if source == "derived":
            dval, dnote = derive(key, units)
            rows.append({"key": key, "value": dval, "source": "derived", "note": dnote})
            continue
        rows.append({"key": key, "value": val, "source": source, "note": note})
    return rows
