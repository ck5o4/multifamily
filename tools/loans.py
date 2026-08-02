#!/usr/bin/env python3
"""Loan advisor: given cash, which loan gets the most units - honestly.

Encodes the 2026 small-multifamily lending menu with eligibility gates a
first-time sponsor actually faces. Programs the borrower cannot get are shown
WITH the reason, so the menu never oversells.

Buying power math (solved in closed form per program):
    cash = down + closing(~3% of price) + 6mo debt-service reserve + reno budget
    =>  P = (cash - reno) / (down% + 3% + 0.5 * (1-down%) * payment_constant)

Usage:
  python3 tools/loans.py recommend 300000
  python3 tools/loans.py recommend 300000 --reno 60000
  python3 tools/loans.py recommend 300000 --units-price 75000   # $/unit assumption
"""

import argparse

# Verified 2026-08-01. Note: Freddie SBL retired 2026-04-15; successor
# "Conventional Small" is $2M-$10M loans - out of first-deal range.
PROGRAMS = [
    {
        "key": "local_bank",
        "name": "Local bank / credit union commercial",
        "units": "5-50",
        "down": 0.25, "rate": 0.0700, "amort": 25,
        "min_loan": 250_000, "max_loan": 3_000_000,
        "dscr": 1.25,
        "term_note": "usually a 5-yr fixed period, then rate resets (balloon risk)",
        "eligible": True,
        "why": "The realistic first-deal loan. Relationship-driven, no experience "
               "minimum, they keep the loan on their books. Some go to 20% down "
               "for a strong file - ask.",
    },
    {
        "key": "dscr_nonqm",
        "name": "DSCR loan (non-bank lender)",
        "units": "1-10",
        "down": 0.20, "rate": 0.0825, "amort": 30,
        "min_loan": 150_000, "max_loan": 2_000_000,
        "dscr": 1.20,
        "term_note": "30-yr fixed exists; prepay penalties are standard",
        "eligible": True,
        "why": "Qualifies on the PROPERTY's income, not your personal income. "
               "Easier approval, ~1%+ more expensive than a bank. Good fallback "
               "or speed play.",
    },
    {
        "key": "resi_conventional",
        "name": "Residential conventional (2-4 units)",
        "units": "2-4",
        "down": 0.25, "rate": 0.0700, "amort": 30,
        "min_loan": 100_000, "max_loan": 1_500_000,
        "dscr": None,
        "term_note": "true 30-yr fixed, no balloon - the safest debt available",
        "eligible": True,
        "why": "Only reaches 4 units, but it is the cheapest safe money in the "
               "market. Qualifies on your personal income (DTI), so W-2/income "
               "history matters.",
    },
    {
        "key": "fha_househack",
        "name": "FHA owner-occupied (2-4 units, you live in one)",
        "units": "2-4",
        "down": 0.035, "rate": 0.0650, "amort": 30,
        "min_loan": 100_000, "max_loan": 1_200_000,
        "dscr": None,
        "term_note": "must live in the property 12 months; MIP added to payment; "
                     "3-4 unit deals must pass FHA's self-sufficiency rent test",
        "eligible": True,
        "why": "The max-leverage backdoor: 3.5% down means a fourplex costs you "
               "~$40K instead of ~$200K, preserving cash for building #2. Only "
               "works if you will actually move in.",
    },
    {
        "key": "seller_finance",
        "name": "Seller financing / assumption",
        "units": "any",
        "down": 0.15, "rate": 0.0650, "amort": 25,
        "min_loan": 0, "max_loan": 10_000_000,
        "dscr": None,
        "term_note": "every term is negotiable; usually a 3-7 yr balloon",
        "eligible": True,
        "why": "Opportunistic - depends entirely on the seller. Tired landlords "
               "with paid-off buildings sometimes prefer the income stream. "
               "Always ask; costs nothing.",
    },
    {
        "key": "agency_small",
        "name": "Agency 'Conventional Small' (Fannie/Freddie)",
        "units": "5-50",
        "down": 0.20, "rate": 0.0625, "amort": 30,
        "min_loan": 2_000_000, "max_loan": 10_000_000,
        "dscr": 1.25,
        "term_note": "Freddie SBL retired Apr 2026; successor starts at $2M loans",
        "eligible": False,
        "why": "NOT AVAILABLE YET: $2M minimum loan (≈$2.5M price), net worth "
               "must exceed the loan amount, and they want 3 prior properties. "
               "This is the deal-3-or-4 loan.",
    },
    {
        "key": "hud_223f",
        "name": "HUD 223(f)",
        "units": "5+",
        "down": 0.1667, "rate": 0.0625, "amort": 35,
        "min_loan": 1_500_000, "max_loan": 100_000_000,
        "dscr": 1.176,
        "term_note": "6-12 months to close; ~$150K+ fixed third-party costs",
        "eligible": False,
        "why": "NOT WORTH IT AT THIS SIZE: the fixed costs and timeline only "
               "make sense above ~$3M. This is the REFINANCE loan later "
               "(Stoa's playbook), not the purchase loan now.",
    },
]

CLOSING_PCT = 0.03


def payment_constant(rate, amort_years):
    r = rate / 12
    n = amort_years * 12
    if r == 0:
        return 1 / amort_years
    m = r * (1 + r) ** n / ((1 + r) ** n - 1)
    return m * 12  # annual payment per $1 of loan


def max_price(cash, prog, reno=0.0):
    """Closed-form max purchase price under: cash covers down + closing +
    6 months of debt service + reno."""
    d = prog["down"]
    k = payment_constant(prog["rate"], prog["amort"])
    denom = d + CLOSING_PCT + 0.5 * (1 - d) * k
    p = max(0.0, (cash - reno)) / denom
    # respect program loan bounds
    if (1 - d) * p < prog["min_loan"]:
        return 0.0
    cap = prog["max_loan"] / (1 - d)
    return min(p, cap)


def max_units_of(prog):
    u = prog["units"].replace("+", "-999").split("-")
    try:
        return int(u[-1])
    except ValueError:
        return 999  # "any"


def recommend(cash, reno=0.0, unit_price=75_000):
    print(f"CASH: ${cash:,.0f}" + (f"   (reserving ${reno:,.0f} for renovation)" if reno else ""))
    print(f"Assumed Louisiana pricing: ${unit_price:,.0f}/unit "
          f"(C/B- class small multifamily trades ~$60-90K/unit)\n")
    rows = []
    for p in PROGRAMS:
        mp = max_price(cash, p, reno) if p["eligible"] else 0.0
        cap_units = max_units_of(p)
        units = min(mp / unit_price if mp else 0, cap_units)
        # A 2-4 unit program cannot buy more building than 4 units cost.
        mp = min(mp, cap_units * unit_price * 1.15)  # +15% for above-avg product
        rows.append((p, mp, units))
    rows.sort(key=lambda t: -t[2])

    print(f"{'Program':<46}{'Down':>6}{'Rate':>7}{'Max price':>12}{'~Units':>7}")
    for p, mp, units in rows:
        if p["eligible"] and mp > 0:
            print(f"{p['name'][:45]:<46}{p['down']*100:>5.1f}%{p['rate']*100:>6.2f}%"
                  f"{'$' + format(int(round(mp, -3)), ','):>12}{units:>7.0f}")
    print()
    for p, mp, units in rows:
        if p["eligible"] and mp > 0:
            print(f"* {p['name']}")
            print(f"    {p['why']}")
            print(f"    {p['term_note']}")
    print("\nNot available to you yet (and why):")
    for p in PROGRAMS:
        if not p["eligible"]:
            print(f"* {p['name']}: {p['why']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["recommend"])
    ap.add_argument("cash", type=float)
    ap.add_argument("--reno", type=float, default=0.0)
    ap.add_argument("--units-price", type=float, default=75_000)
    a = ap.parse_args()
    recommend(a.cash, a.reno, a.units_price)


if __name__ == "__main__":
    main()
