# Deal intake

Parses a deal package into a per-deal copy of a model, recalculates, and reports
against the 12-14% realistic / 16-17% target IRR bands.

## Use

Drop the deal's files into `deal-intake/<deal-name>/`. Filenames are auto-matched
on keywords (`rentroll`, `t12` / `trailing` / `operating statement`, `comp`).
CSV, XLSX, and PDF are supported.

    python3 tools/intake.py --deal oak-street                          # dry run, writes nothing
    python3 tools/intake.py --deal oak-street --price 1250000 --apply --recalc
    python3 tools/intake.py --deal river-road --model dev --apply --recalc

Explicit paths override discovery: `--rent-roll`, `--t12`, `--comps`. The same
workbook can serve all three - point every flag at it and pick the tab with
`--sheet-rentroll`, `--sheet-t12`, `--sheet-comps`.

Multi-sheet workbooks are scored automatically: sheets named like a rent roll
(`Rent Roll`, `Revenue`, `Unit Mix`) win for the subject property, sheets named
like comps win for comps, and the candidate list is printed so a wrong pick is
visible and overridable.

## What it does

1. Rent roll -> unit mix aggregated by bed/bath -> acquisition `Inputs!E3:H10`
   (or development `Inputs!E3:G8`).
2. T-12 -> the 8 expense lines + property taxes, converted to $/unit/yr ->
   `Inputs!B17:B25`. Prints each line's per-unit figure against the current model
   default and Stoa's original, flagging anything off by more than 25%.
3. Comps export -> development `Comps!D10:H80` with Include=1.
4. LibreOffice headless recalc, full-workbook formula-error scan, Checks tab read,
   then the metrics and an IRR verdict.

## Guarantees

- Master workbooks are never modified. Each run copies to
  `deal-intake/<deal>/<deal>_<model>.xlsx`.
- `safe_writer.ModelWriter` refuses to write to any cell holding a formula, and to
  any cell that is neither blue-font nor inside a declared paste range in
  `cellmap.py`. Both refusals raise rather than warn.
- Nothing is silently dropped. Unit types beyond the model's row capacity,
  unparseable rows, and ambiguous mappings are printed under NOTES / UNRESOLVED.

## Verified against real data

Hand-checked cell by cell against `reference/The_Waters_at_Inverness_-_Under_Contract.xlsx`
(Stoa's real 288-unit deal), 2026-07-25:

| | Source | Parsed |
|---|---|---|
| 1 BR/ 1 BA | 134 u, 764 SF, $1,681.51 | 134, 764, $1,682 |
| 2 BR/ 2 BA | 121 u, 1040 SF, $1,949.52 | 121, 1040, $1,950 |
| 3 BR/ 2 BA | 33 u, 1318 SF, $2,202.25 | 33, 1318, $2,202 |
| Total units | 288 | 288 |

Weighted averages reproduce the source total row exactly (943.4375 SF,
$1,853.78 rent). All eight expense lines tie to the source `Total` column to the
dollar (Payroll 425,347.2 / G&A 115,436.8 / Marketing 113,796.8 / R&M 107,305.6
incl. Turnover / Contract Services 0 / Utilities 213,753.6 / Other 24,576 /
Insurance 355,200), summing to the source total of 1,355,416.

The first run on this file produced 53 junk unit types and 298 units. Four bugs
were found and fixed: no worksheet selection (all 20 sheets were parsed as one),
total rows consumed as data, repeated header rows inside stacked tables, and the
T-12 annual figure taken from the last column (which was `Start Month`) rather
than the column under the `Total` header.

## Known heuristics to verify per deal

- Management fee arrives as a dollar amount in a T-12 but the model input is a
  percentage of EGR, so it is never written automatically.
- Trash/waste can key into either utilities or contract services.
- The development model's rent VLOOKUP only resolves `1 BR/ 1 BA`, `2 BR/ 2 BA`,
  and `3 BR/ 2 BA`. Any other unit type returns 0 rent and is flagged.
- T-12 column choice is printed per line as `basis`. Preference order: the column
  under a `Total`/`Annual`/`TTM` header, then a sum of 12 monthly columns, then
  the last plausible value.
- Parsing stops at the first `Total` / `Net Operating` row that follows a matched
  expense line. This is what keeps benchmark and comparison blocks below the
  expense table from being summed in, but it also means nothing below that row is
  read (management fee and capex reserve usually sit there).

## Location and taxes

Pass the town and taxes are reassessed to the purchase price, because a sale
resets the assessment and the seller's old bill understates what you will owe.

    --location "Gonzales"        --location "Slidell"       --location "Ascension"

Covers both metros and the corridor between them: East Baton Rouge, Orleans,
Jefferson, Ascension, Livingston, Tangipahoa, St. Tammany, St. John the Baptist,
St. Charles, West Baton Rouge, Iberville, St. James. Town names or parish names
both work; an unrecognised town fails loudly rather than guessing.

Apartments assess at **10%** of value (residential improvements under the LA
Constitution), not the 15% commercial ratio. `--commercial-share 0.3` splits a
mixed-use building. `--keep-sellers-tax` disables reassessment.

## Refinance

    --refi-year 3

Models a refinance to repay your investor: appraises at the refi-year NOI over
the valuation cap, sizes the new loan on the lesser of LTV and DSCR, pays off the
old loan, and runs the net cash-out through the waterfall. The report states what
share of your investor's capital actually comes back.

## Value-add

    --reno-units 8 --reno-cost 12000 --reno-premium 200 --reno-per-year 4 --reno-start-year 1

Renovate N units at a cost per unit, for a rent increase per unit per month, at a
pace per year. The budget is funded at close, so it shows up in the cash you need
rather than appearing free. Units being turned lose `--reno-downtime` months to
vacancy (default 1).

## Price solver

    --solve-price

Bisects purchase price to find what you would have to pay to clear 16% and 22%
IRR, with everything else about the deal held constant. Taxes recompute from
price on each pass. Takes a couple of minutes - it is running the whole model
about thirty times.

## Market watch (automated market survey)

    python3 tools/marketwatch.py add "Comp Name" <its-own-website-floorplans-url>
    python3 tools/marketwatch.py snapshot      # weekly
    python3 tools/marketwatch.py report        # what changed: leased units, price cuts, concessions
    python3 tools/marketwatch.py assumptions   # median rents by bed count for underwriting

Watches competitors' own public websites over time. One snapshot = asking rents.
Weekly snapshots = leasing velocity (listings that disappear), price cuts on
sitting units, concession activity, seasonality. Raw HTML is kept per snapshot in
`market-data/` so pages can be re-parsed later.

Legal line, stated in the module docstring: public advertised prices observed
unilaterally = the standard market survey, automated. No pooled non-public data,
no coordination with anyone - that is the conduct the RealPage case was about.

Use the properties' OWN sites, not apartments.com/Zillow pages - aggregators
(CoStar-owned) block robots; property sites don't.

## Bank package generator

    python3 tools/bankpackage.py <deal-name>

Produces a print-ready loan-request document (HTML -> Cmd+P -> PDF) modeled on
the Stoa bank package structure: the request, sponsor, property + rent roll,
value-add business plan, sources & uses, five-year pro forma with DSCR by year,
and the repayment/takeout story. Every number is pulled live from the deal's
recalculated workbook so the package can never disagree with the underwriting.
Items the system cannot know (bio, photos, entity name) are highlighted [FILL].
Refuses politely if the workbook has failing checks - never send a banker
numbers that don't tie.

## Portfolio / deal-flow tracker

    python3 tools/portfolio.py add "oak-street" --stage underwriting
    python3 tools/portfolio.py stage "oak-street" offered --note "offered 725k"
    python3 tools/portfolio.py board                 # pipeline at a glance
    python3 tools/portfolio.py plan "oak-street"     # lock yr-1 targets from the deal workbook
    python3 tools/portfolio.py log "oak-street" june.csv   # ingest bank/PM CSV export
    python3 tools/portfolio.py pay "oak-street" 2500       # record investor distribution
    python3 tools/portfolio.py status "oak-street"   # actuals vs plan, on-track flags

State lives in `portfolio/deals.json` - the file a future dashboard will render.
`plan` freezes the underwritten monthly targets (rent, opex, NOI, cash flow)
from the deal's recalculated workbook; `log` parses any date/description/amount
CSV a bank or property manager exports and auto-categorizes (rent, debt,
insurance, repairs, reno, distributions); `status` compares actual monthly net
to the plan and answers "on track?" per month. The payback ledger tracks the
investor's repaid share against their contributed capital.

## Strategy comparison (hold vs refi vs sell)

    --compare

Runs the same deal through every exit path - sell at year 2 / 3 / 5, refi at 3
then sell at 5 / 7, refi and hold to 10 - and prints IRR, dollar profit, and
equity multiple for each, all at the honest exit cap. A hot-market year-3 sale
(Stoa timing, compressed cap) is shown as a labeled upside line, never the base
case. IRR and dollars are both shown because they disagree on short holds.

## Loan advisor

    python3 tools/loans.py recommend 300000 --reno 60000

Given cash on hand, shows every loan program a first-time sponsor can actually
get in 2026, the max purchase price each supports (cash must cover down payment
+ ~3% closing + 6 months of payments + renovation), and roughly how many units
that buys at Louisiana pricing. Programs he cannot get yet (agency, HUD) are
listed with the reason. Verified 2026-08-01: Freddie SBL retired Apr 2026;
successor starts at $2M loans.

## Template defaults

Every input carries provenance, printed on each run. Precedence is
**`--set` override > deal documents > template default** - an explicit flag you
typed always wins, a parsed T-12 beats a template value, and the provenance
table shows which source supplied every number.

    python3 tools/intake.py --deal oak-street --price 950000 \
        --set mgmt_pct=6% --set insurance=1800 --recalc

Defaults live in `tools/defaults.py`, each tagged `stoa` (their tested figure,
unchanged), `updated` (changed, with the reason recorded), or `derived`
(computed from deal size). Two are size-driven:

| Input | Rule |
|---|---|
| Management fee | 8% under 50 units, 5% to 150, 3% above (Stoa self-managed at 3%) |
| Payroll | $0 under 20 units, $1,715/unit at or above |

`--no-defaults` writes only what the deal documents supply and leaves the
model's own values untouched.

## Before any offer (hard rules, adopted 2026-08-02)

1. **Bindable insurance quote in hand** - never the template default. The LA
   market moves too fast for any assumption to survive contact with a quote.
2. **Parcel tax reconciliation** - `parceltax.py` with the parcel's actual
   assessment (printed on LoopNet listings) so the reassessment jump is exact.
3. **Seller documents received** - T-12 + rent roll; screens on estimated
   rent rolls never graduate to offers.
4. **Debt sized at 1.30 DSCR** (house standard; banks only require 1.25) -
   set in defaults.py.

## Parcel tax reconciliation

    python3 tools/parceltax.py --parish livingston --assessment 24637 --price 1699000

Seller's actual bill vs your post-reassessment bill, from the assessment
number printed on the listing. Flags when the assessor's carried value is so
far below your price that broker pro formas materially understate taxes.

## Calibration log (model vs the real market)

    python3 tools/calibrate.py add "Name" --price 15200000 --market northshore \
        --date 2026-07-13 --cap 5.9 --source "Pulse Northshore"
    python3 tools/calibrate.py report

Every closed sale The Pulse reports gets logged (the morning sweep extracts
them into the digest in paste-ready form). Over months the report shows
whether our caps/expenses run hot or cold against actual trades - measured
confidence instead of estimated confidence. Lives in portfolio/calibration.json.

## Free federal data (no subscriptions)

    python3 tools/flood.py "41063 Cannon Rd, Gonzales, LA"   # FEMA flood zone, SFHA flag
    python3 tools/rates.py                                   # prime/10yr/SOFR vs defaults.py, exits 1 if stale
    python3 tools/market.py ascension                        # parish employment + demographics

`flood.py` geocodes via the Census geocoder and reads the FEMA NFHL - run it on
every deal before trusting any insurance number; an SFHA hit means lender-required
flood insurance the T-12 won't show. `rates.py` automates the CLAUDE.md standing
instruction to verify WSJ Prime before each deal (FRED public CSV, keyless; uses
curl -4 because FRED's IPv6 blackholes from some networks). `market.py` uses BLS
keyless (~25 queries/day); the ACS section (population/income/rent growth) needs
the free `CENSUS_API_KEY` from https://api.census.gov/data/key_signup.html and is
skipped loudly without it.

## Requirement

LibreOffice 26.2.5 is installed at `/Applications/LibreOffice.app`. Both master
models recalculate with zero formula errors and all Checks OK, and every figure
claimed in CLAUDE.md reproduces exactly — see `MODEL_AUDIT.md`.
