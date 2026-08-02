# Multifamily Investment Project — Context File

Read this fully before doing any work in this repo. It is the single source of truth
carried over from the original claude.ai project where the calculators were built.

## Owner profile
- New multifamily investor, Baton Rouge, LA. Ex-employee of Stoa Group (stoagroup.com).
- Capital: ~$300K cash. Primary LP investor: his mother (funds most of it).
- Goals: high DSCR, min 16-17% IRR target (see honesty note below), maximum units/scale as fast as possible.
- First deal: existing stabilized property in Louisiana. Construction/development later.
- Only invests in markets where Stoa operates (Gulf Coast tertiary: LA, MS, AL, FL panhandle).
- Will use third-party property management for everything.
- Communication preference: minimal words, facts/logic/truth only, no flattery, never massage numbers.

## Honesty note (do not soften this)
16-17% IRR on a stabilized LA asset at current rates without value-add is aggressive.
Realistic: 12-14%. At market GC pricing ($160-180/SF), 2026 ground-up development in
these markets does not pencil — Stoa's edge was building ~25-30% below market via
vertical integration (DSLD Homes lineage), not spreadsheet magic. Say so when relevant.

## The two calculators (in this repo)
Both are original builds (NOT copies of the licensed Break Into CRE template Stoa used),
recalculated with zero formula errors, verified against manual calculations.

### Multifamily_Acquisition_Model.xlsx — annual, 11-yr forecast
Tabs: Read Me, Inputs, Annual CF, Waterfall, Sensitivity, Stoa Benchmarks, Checks.
- Loan sizing: MIN(LTV x price, PV of DSCR-constrained payment) — binds like a bank.
- Sale: forward (next-year) NOI / exit cap, less cost of sale, less payoff.
- Waterfall: LP pref (8%) -> LP return of capital -> GP return of capital -> residual split.
- Verified: demo 8-unit LA deal, NOI $43,822 (matched manual calc exactly), DSCR binds
  at 1.25, levered IRR 14.04%, all checks OK.

### Multifamily_Development_Model.xlsx — monthly, 96-month engine
Tabs: Read Me, Comps, Inputs, Construction Budget, Monthly CF, Waterfall, Summary,
Stoa Benchmarks, Checks.
- Same equation structure as the Stoa-era model: comp-driven rents (weighted comp avg
  by unit type x (1-delta)), phased lease-up (can start before completion), fixed/variable
  expense phase-in (%fixed + (1-%fixed) x occupancy, each line starts N months before
  lease-up), bell-curve construction draws (NORMDIST, SD 6 hard / SD 10 soft),
  equity-drawn-first-then-debt, capitalized construction interest, perm refi with
  cash-out, sell-or-hold toggle, GP in-kind fee equity toggle.
- Key engineering difference vs. original: NO circular references / iterative calc.
  Equity is fixed at (1-LTC) x pre-interest cost; the construction loan carries its own
  capitalized interest (reproduces Stoa's "effective LTC" behavior — Inverness was 75%
  nominal -> 79.5% effective; this model produced 77.9% on the same-scale demo).
- Verified: month-33 EGR matched manual calc to the dollar; draws sum to budget;
  waterfall ties to project CF; all checks OK.
- Demo inputs approximate Stoa's Waters at Inverness deal (288 units, mix 134/121/33,
  SF 764/1040/1318, deltas -4%/-5%/+12%) at 2026 pricing: result 10.4% IRR, 1.51x EM,
  breakeven refi (-$116K cash-out) at 6.0% valuation cap. Stoa's 2022 underwrite of the
  same deal: 25.8% IRR — the delta is caps, rates, and build cost, not equations.

## Stoa playbook (their actual strategy)
Build cheap (vertically integrated, DSLD Homes lineage — even-flow construction cadence,
Procore) -> lease to ~93% -> HUD/FHA 223(f) refi at ~33-36 months (value = stabilized
NOI x 12 / cap, 75% LTV, 420-mo am, 0.25% MIP) -> cash out most/all equity -> hold cheap
permanent debt or sell big to PE at peak. GP economics: 4% GC profit + 3.5% + 0.5% dev
fees taken as "in-kind" equity (little cash in, huge IRRs). Portfolio: 1,995 units /
7 projects, avg deal $28.4M, claimed avg stabilized IRR 49.58%, NOI $9,219/unit.
Unit mix philosophy: 45% 1BR / 45% 2BR / 10% 3BR. Class A in tertiary markets.

## Stoa exact benchmark assumptions (preserved in Benchmarks tabs)
- Expenses $/unit/yr (avg of their Hammond & Manhattan budgets, ~2023): Payroll 1,569;
  G&A 438; Marketing 379; R&M 472; Utilities 692; Other 95; Insurance 1,164.
- Vacancy 7%; rent growth 2%; expense growth 1%; mgmt 3% EGR; asset mgmt 1%;
  capex $250/unit/yr; lease-up 14 months.
- Hard cost formula: $122.23/SF x NRSF + $1,000,000. Contingency 10% hard / 2% soft;
  GC overhead 5% grossed up; impact fees $4,127/unit.
- Soft costs: architectural $1,800/u; landscape $150/u; geotech $20k; civil $95k;
  Phase 1 $2.5k; title $68k; attorney $5.1k; appraisal $4.5k; other closing $700;
  NGBS $230/u; consulting $200/u; builders risk DEBT/1000x8; GL hard/1000x3.
- Construction loan: 75% LTC, WSJ Prime + 0.50% floating (forward curve), fee $95k flat.
- Perm: FHA 223(f) at month 33; valuation cap 5.5%; exit cap 6.5%; cost of sale 0.75%.
- Waterfall: 8% pref -> 10% hurdle (10% promote) -> 12% hurdle (20%) -> 30% promote,
  monthly XIRR. (Current models simplify to pref + ROC + split; 4-tier is pending work.)
- Expense %fixed / months-prior-to-lease-up: Payroll 100%/4; G&A 100%/4; Marketing
  250%(capped 100% in our model)/4; R&M 20%/3; Utilities 50%/3; Other 0/0; Insurance 100%/4.

## Updated assumptions in our models (and why — do not silently revert)
- WSJ Prime 7.00% -> 6.75% (current since Dec 11, 2025). Verify current before each deal.
- Insurance $1,164 -> $2,000/unit (LA insurance crisis; quote every deal).
- Expense growth 1% -> 2.5%; expense levels escalated ~3%/yr (Payroll 1,715; G&A 480;
  Mktg 415; R&M 515; Util 755; Other 105). Payroll = 0 on small props (<~20 units).
- Mgmt fee 3% -> 8% in acquisition model (3rd-party mgmt on small props runs 7-10%);
  development model keeps 3% (at-scale).
- Cost of sale 0.75% -> 3% acquisition (brokerage on small deals).
- Hard cost $122.23 -> $135/SF demo ($165 without builder advantage; market GC $160-180).
- Valuation cap 5.5% -> 6.0%; builders risk rebased to hard cost (removes circularity);
  construction loan fee $95k flat -> 0.5% of loan.

## Stoa admin/legal reference
- LOI format: non-binding; DD period (e.g., 180 days) + 30-day extensions with
  non-refundable extension deposits ($20k each) applied to price; escrow deposit
  (~$200k on $3.7M land) within 10 days of PA, applied to price; closing 30 days after
  DD; contingent on entitlements/site-plan approval for minimum density; no broker;
  signed Prescott Bailey, Stoa Group / purchaser entity "Stoa Holdings, LLC".
- Partners: Toby Easterly (Managing Partner), Saun Sullivan (DSLD Homes CEO), Ryan Nash.
- Rezoning pitch themes: economic boost, walkability, tax revenue, design standards.

## Data ingestion plan (build next in Claude Code)
CoStar has NO public API for individuals. Workflow: user exports from CoStar/Crexi/
LoopNet/broker OMs (CSV, XLSX, PDF) into ./deal-intake/<deal-name>/. Scripts should:
1. Parse rent rolls (PDF/CSV) -> unit mix table -> write into model Inputs (openpyxl).
2. Parse T-12s -> map to the 8 expense lines + taxes + mgmt -> flag deviations vs.
   Stoa benchmarks (per-unit).
3. Parse comps exports -> Comps tab of development model.
4. Recalculate via LibreOffice headless and report: NOI, DSCR, loan sizing, IRR, EM,
   LP returns, and a verdict vs. the 12-14% realistic / 16-17% target bands.
Never overwrite formula cells — write only to the blue input cells documented in each
workbook's Read Me.

## Pending work
1. CoStar/rent-roll/T-12 ingestion scripts (above).
2. Optional: extend waterfall to Stoa's full 4-tier (8% pref / 10% / 12% / 10-20-30 promote).
3. Optional: floating-rate forward curve for construction loan (currently fixed rate).
4. Analyze live listings as the user brings them.
