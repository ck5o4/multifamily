# Model logic audit — 2026-08-01

Both models recalculate with **zero formula errors** and **all Checks OK**. Every
figure claimed in CLAUDE.md reproduces exactly:

| Claim | Recalculated |
|---|---|
| Acq demo NOI $43,822 | $43,821.50 |
| Acq DSCR binds at 1.25 | 1.25 (loan $422,838 vs LTV cap $450,000 — DSCR binds) |
| Acq levered IRR 14.04% | 14.04% |
| Dev effective LTC 77.9% | 77.92% |
| Dev IRR 10.4%, EM 1.51x | 10.39%, 1.509x |
| Dev refi cash-out −$116K | −$115,826 |

The equations are sound. What follows is where the *assumptions* are weak for the
deals you are actually buying, ordered by how much money they move.

---

## 1. Exit cap below going-in cap manufactures IRR — acquisition model

**Severity: highest. This one will make bad deals look like good ones.**

Default exit cap is 6.5%, inherited from Stoa. On a small Baton Rouge deal buying
at an 8.74% going-in cap, the model assumes you sell 224 bps richer than you
bought. Measured on the test deal:

| Exit cap | Levered IRR | Equity multiple | Total profit |
|---|---|---|---|
| 6.50% (Stoa default) | **27.28%** | 3.05x | $340,524 |
| 8.74% (= going-in) | **13.39%** | 1.75x | $124,501 |

Half the return is an assumption, not operations. Stoa's 6.5% was for
institutional Class A 288-unit product sold to PE. An 8-unit tertiary Louisiana
property does not trade there.

Note this cuts the other way in the development model, where a 7.68% development
yield against a 6.50% exit cap is a spread you *earned* by building. The flaw is
specific to acquisitions.

**FIXED.** The exit cap is now solved automatically: the pipeline recalculates
once, reads the going-in cap, sets exit cap to going-in + 50 bps, and
recalculates again. The shipped model default moved from 6.5% to 7.5% so it is
internally consistent, and a new Check fails whenever exit cap sits below
going-in. Override with `--set exit_cap=6.5%` when a deal genuinely warrants it.

## 2. No property tax reassessment on purchase — acquisition model

`Inputs!B25` is a flat annual dollar input grown at the expense growth rate. A
Louisiana purchase triggers reassessment; taxes typically step to the new basis
in the year after closing. Using the seller's current tax bill understates
expenses, which overstates NOI, which overstates both the DSCR-constrained loan
size and the IRR — three compounding errors from one input.

The development model already has the right mechanism (millage rate ×
assessment ratio × value, `Expenses!I12`). The acquisition model has nothing.

**FIXED.** `tools/latax.py` computes post-sale tax as
`price x assessment ratio x mills/1000`, applied on every acquisition run given
`--tax-district`. `--keep-sellers-tax` disables it.

Assessment ratio is **10%, not 15%**. Louisiana Constitution Art. VII §18
assesses improvements for residential purposes at 10% and other property at 15%;
apartments are residential improvements. Using the commercial 15% ratio — the
common assumption — overstates taxes by half.

2025 millage totals gathered 2026-08-01:

| District | Mills | Build-up |
|---|---|---|
| Baton Rouge, in city limits | 111.99 | parish 50.60 + school 43.45 + city 17.94 |
| Unincorporated East Baton Rouge | 94.05 | parish 50.60 + school 43.45 |
| Orleans, most East Bank | 131.99 | parish range is 98.47–154.48 |
| Tangipahoa (Hammond) | 100.00 | parish average; districts vary |

Note city and parish millages **stack** inside Baton Rouge city limits — a city
taxpayer pays parish taxes as well as city taxes. An earlier draft of this module
used the city line alone and understated the bill by 45%.

These are parish-level starting estimates. Millage varies by taxing district
within a parish, so confirm the total for the specific parcel on the assessor's
site before making an offer.

## 3. Preferred return accrues on original capital, not unreturned capital

Both waterfalls. `Waterfall!D10 = IF(year<=hold, $C$2*pref, 0)` where `$C$2` is
LP capital as originally contributed. After Tier 2 returns that capital, the
pref keeps accruing on the full original amount.

This is LP-favorable and non-standard — the market convention accrues pref on
*unreturned* capital, so it steps down as capital comes back. It matters
directly to your structure: your investor is repaid via refinance, and every
dollar returned should stop earning 8%.

**FIXED.** The accrual base is now `Waterfall!D15`, LP unreturned capital at the
start of the period, so returned capital stops earning the pref. No circular
reference: the beginning balance chains off the prior column.

This changes nothing on the current demo, where capital is not returned until
sale in year 5. It bites the moment a refinance returns capital mid-hold — which
is your stated repayment mechanism, so it will matter on a real deal.

## 4. DSCR is computed before the capital expense reserve

`Annual CF!D38 = D25/D34` — NOI (row 25) over debt service. The capex reserve
sits at row 26, below NOI. Lenders underwrite DSCR *after* replacement reserves.
On the test deal that is 1.40x reported versus 1.35x as a lender would size it.

At a 1.25x covenant a 0.05 overstatement is not fatal, but it is the wrong
direction on the one number that determines your loan proceeds.

**FIXED.** `Annual CF` row 40 now carries DSCR after the capex reserve, and a new
Check flags it below 1.15. Loan sizing still uses the pre-reserve figure, which
is what small-balance bank lenders typically underwrite; the post-reserve line
shows you what an agency or HUD lender would see. Demo: 1.25 pre, 1.19 post.

## 5. No value-add module

**FIXED.** A renovation block now sits at `Inputs!E30:F39` — units to renovate,
cost per unit, rent premium per unit per month, pace per year, downtime months
per turn, and start year. Zero units disables it and reproduces prior behaviour
exactly.

The renovation budget is **funded at close** and lands in Total Equity Required,
which is the conservative treatment and answers the question that actually
governs a small deal: is there enough cash to buy *and* renovate. Renovated units
add their premium to gross potential rent as they complete, and units being
turned lose their downtime months to vacancy. Two Checks cover the unit count not
exceeding the property and the programme finishing inside the hold. `Annual CF`
rows 62–64 show the phasing.

### What it proves

Test deal, 8 units at $600K, renovating all 8 at $12K/unit for a $200/month rent
premium, exit cap solved honestly at going-in + 50 bps in every case:

| Scenario | Levered IRR | Equity needed | Investor repaid at refi |
|---|---|---|---|
| Buy and hold, no work | 5.03% | $193,391 | — |
| Renovate, no refi | 11.38% | $262,500 | — |
| Renovate + refinance year 3 | 11.52% | $262,500 | 68% |

Value-add more than doubles the return. It is also **necessary but not
sufficient** — at this price the deal still lands under the 12% floor. Buying
right and forcing NOI are two separate requirements, and this deal only satisfies
one of them.

Run through the intake pipeline with taxes reassessed for Baton Rouge, the same
programme produces a 16.12% IRR and a year-3 refinance returning 109% of the
investor's capital. The difference is entirely the tax and rent inputs the
pipeline supplies, which is the argument for running deals through it rather than
typing into the workbook.

## 9. Price solver

`tools/solve.py`, exposed as `--solve-price`. Bisects purchase price to find what
you would have to pay to clear 16% (your hard minimum) and 22% (your ideal),
recomputing taxes from price on each iteration and holding the exit cap fixed —
paying less does not make the market value the building more richly on exit.

This is the "Target Price" and "Ideal Price" columns from your tracking grid,
computed rather than estimated. On the test deal: 16% needs $570,000 (5% below
asking), 22% needs $527,000 (12% below).

## 6. Interest-only leaves an undisclosed balloon

`Inputs!B53` sizes the payment over the full amortization from the original
balance. During an IO period the balance is held flat, then amortizes on that
original payment — so the loan does not retire within the stated term and a
balloon remains. Default `io_years` is 0, so this only bites when you use IO.

**FIXED (disclosed, not eliminated).** A Check now reads CHECK whenever
`io_years > 0`, so the balloon can no longer hide. Re-amortizing over the
remaining term would change the payment convention and is not worth doing until
you actually take an IO loan.

## 7. The acquisition model is annual

It cannot express a monthly distribution schedule or answer "what month is the
investor repaid." Your investor expects monthly distributions with payback
targeted inside 3 years. The development model is monthly and can answer this;
the acquisition model cannot.

**Fix:** either add a monthly distribution schedule tab driven by annual cash
flow, or accept annual granularity with an interpolated payback month. Needs
your call on which.

## Kept as-is, deliberately

- **Rent growth 2% against expense growth 2.5%.** Margins compress over the hold.
  Conservative and realistic; keep.
- **Forward-NOI exit pricing.** Standard and correctly implemented.
- **DSCR/LTV loan sizing via `MIN(LTV×price, PV(...))`.** Binds the way a bank
  binds. Verified: the demo binds on DSCR, the test deal binds on LTV.
- **Closed-form construction interest.** The algebraic solve removes the circular
  reference and lands within the 65–85% effective-LTC check.

## 8. The acquisition model could not model a refinance

**FIXED.** A refinance block now sits at `Inputs!E13:F28` — refi year, valuation
cap, LTV, min DSCR, rate, amortization, and cost. Setting the refi year to 0
disables it and reproduces the previous behaviour exactly.

The loan ledger switches to the new loan's rate and payment the year after the
refi. Net cash-out flows into levered cash flow, and therefore through the
waterfall, where it pays the preferred return and returns LP capital — which is
what makes finding 3 above matter. Two new Checks cover the refi year sitting
inside the hold period and the new loan not exceeding the appraised value.

### What testing it revealed

On the test deal, a year-3 refinance returns **$92,661 — 62% of the investor's
$149,850**, not the full repayment the plan assumes. And on the original demo,
refinancing made the deal *worse*: levered IRR fell from 7.12% to 6.23%, because
the new loan was DSCR-constrained, cash-out was only $25,904, and the 2% loan
cost plus higher debt service outweighed it.

**A refinance returns capital only when NOI has grown enough to support a bigger
loan.** At 2% rent growth against 2.5% expense growth, NOI barely moves in three
years, so there is nothing to pull out. Refinancing your investor out inside
three years requires forcing NOI up — which is the value-add module in finding 5,
not a financing trick. The two items are the same problem.

## Housekeeping

**FIXED.** Removed the malformed `rowfill` call for the beginning-loan-balance
row in `generators/build_acq.py`; the loop beneath it already wrote those cells.

**FIXED.** Both generators now emit explicit 8-character aRGB font colours.
openpyxl 3.1.5 expands a 6-character colour to `000000FF` while the version that
built the original workbooks produced `FF0000FF`, which silently broke input-cell
detection on regeneration. `safe_writer` now compares RGB only and ignores the
alpha byte, so it tolerates workbooks built by either version.

## Verification after these changes

Regenerated, recalculated, compared against the pre-change baseline:

| | Before | After |
|---|---|---|
| Year 1 NOI | $43,821.50 | $43,821.50 |
| Loan amount | $422,837.58 | $422,837.58 |
| Total equity | $193,390.79 | $193,390.79 |
| Year 1 DSCR | 1.25 | 1.25 (1.193 post-reserve) |
| Equity multiple | 1.8673x | 1.3810x |
| Levered IRR | 14.04% | **7.12%** |

Everything the operating model produces is unchanged. **IRR and equity multiple
moved because the shipped exit cap default changed from 6.5% to 7.5%**, which is
finding 1 being corrected rather than a regression: the demo's going-in cap is
7.30%, so the old 6.5% default was pricing in 80 bps of compression. 7.12% is
what the demo deal actually returns without that assumption.

Zero formula errors, all 10 Checks OK on the acquisition model and all 7 on the
development model. Pre-change copies are in `.backup/`.
