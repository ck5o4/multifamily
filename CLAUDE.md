# Multifamily Investment Project — Context File

Read this fully before doing any work in this repo. It is the single source of truth
carried over from the original claude.ai project where the calculators were built.

## Owner profile
- New multifamily investor, Baton Rouge, LA. Ex-employee of Stoa Group (stoagroup.com).
- Capital: ~$300K cash. Primary LP investor: his mother (funds most of it).
- Goals: high DSCR, min 16-17% IRR target (see honesty note below), maximum units/scale as fast as possible.
- First deal: existing stabilized property in Louisiana. Construction/development later.
- Buy box (SUPERSEDES the original "only where Stoa operates" line, which was
  outgrown between 2026-08-02 and 08-17): East Baton Rouge, Ascension, Livingston,
  Tangipahoa, St. Tammany, Lafayette, Orleans, Jefferson, St. Bernard. See the
  buy-box section below for the rules that govern it.
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

## Data ingestion plan (BUILT — see tools/README.md; kept for design intent)
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

## How to work with Anthony (migrated from machine-local memory 2026-08-24)

These were living in `~/.claude` auto-memory, which is folder- and machine-scoped
and does NOT travel to another account or machine. They are working agreements,
not project state, so per Anthony's own KNOWLEDGE-MAP routing rule ("reusable
RULES go in the ACTIVE layer; MEMORY is for project STATE + pointers only") they
belong here, in the file that loads deterministically on every session.

**Autonomy (2026-08-01).** Always execute with the least manual work for him
possible. Do everything achievable without him first; only surface items that
genuinely require him (accounts, payments, credentials, decisions). Exhaust the
autonomous path — keyless APIs, connected MCP tools, browser automation — before
asking. When he IS needed, hand him a short exact list (links, one-line actions),
not options. He is building this solo with limited time; his value-add is broker
relationships and capital decisions, not setup work.

**Email: draft, never send (2026-08-02).** Draft all outreach; he personally
reviews and hits send. **Even when he says "go" on an email, that means finalize
the draft, not send it.** This rule exists because an earlier session sent a
broker email he had said "go" to. Outbound to brokers/sellers/lenders is his
voice and his relationships. Create the draft in Gmail, say which drafts are
ready and to whom, then stop. Never open Gmail in a browser to click send.

**Verify before instructing (2026-08-05).** His words, after being told to send
four drafts when he had already sent three of them hours earlier: *"Before
telling me to do something you need to really check that I 1. have to do it
2. it's the correct thing to do."* Before ANY action item, re-query the live
source in that same turn — Gmail drafts + `in:sent` for email tasks, file reads
for doc tasks, `deals.json` for deal-stage claims, the actual site for account
claims. If the check is impossible, say "unverified — check X" rather than
asserting. Build status updates from fresh queries, never from conversation
history. Memory of external state rots the moment he acts independently, and he
works the pipeline himself between and during sessions.

**Coach, don't just analyse (2026-08-02).** For every deal and relationship
(brokers, sellers, lenders, his mother as LP, property managers, insurance
agents): supply the strategy AND the words — what to say, how to phrase asks,
what not to say. Ground every script in the model's numbers ("offer $X because
that is where the 16% rung clears, and here is how to justify it without
insulting the broker"). Tell him honestly what he can credibly ask for at his
size and experience, and how to build credibility he does not yet have. Coach
BEFORE he communicates: draft the email, prep the call script, anticipate the
counters. He is a first-time sponsor with strong numbers; execution and
communication are where these deals are won or lost.

**Download authorization (2026-08-02, standing).** Download anything attached to
a listing judged to have legs — brochures, OMs, rent rolls, T-12s — without
per-file permission, and file it into `deal-intake/<deal>/`. Scope: listing
documents on real-estate platforms viewed through his logged-in browser session.
Does NOT extend to executables or anything outside deal research. Sending email
remains draft-only.

**Communication style.** Minimal words, facts and logic, no flattery, never
massage numbers. Say the uncomfortable thing plainly (see the honesty note above
on IRR expectations).

## Company identity

**Ridgeback Peak Properties** (chosen 2026-08-01). Official email
**ridgebackpeak@gmail.com** — use for broker lists and data accounts, never his
personal address. LLC filed 2026-08-02 via geauxBIZ as the brand/management
entity; per-deal single-purpose LLCs at contract time; his mother enters as
non-managing member at the deal-LLC level (8% pref, 90/10, 70/30 promote —
attorney review advised); no S-corp election; umbrella policy recommended.

**Brand meaning** (he wants this used as the company story): named for his late
dog Charli, half Rhodesian Ridgeback and half German Shepherd, with a black
widow's peak. Ridgebacks were bred as lion hounds — they track a lion and hold
it at bay for hours without ever lunging. Patience and nerve, no overcommitting:
find the deal, hold position, strike only when the numbers are right. The
Shepherd half is stewardship, guarding capital entrusted to him. Use in the bank
package sponsor section, LP materials, and any future website.

## Buy box and screening rules (migrated from memory 2026-08-24)

**Hard exclusions.** NO student housing, ever. NO Section 8 **unless the numbers
show he will legitimately make a lot of money** — flag voucher economics
explicitly per deal (EBR HUD payment standards often meet or beat market on
2/3BR; guaranteed payment vs inspections and wear). Never filter it silently and
never sneak it in.

**The house rule (2026-08-09), and it outranks the IRR bands.** His words: *"If
it can't make me more money by the time I try to sell it, I don't want to do
it."* Every deal must beat the same-period stock-index alternative or PASS.
Operationalised as P(deal MC IRR > market draw ~ Normal(10%, 8%)), scored per
price rung on the board. A coin flip against the index is never worth five
illiquid years. The 13% pursue floor is the deterministic implementation — a
3-point premium over the ~10% index average paying for illiquidity, labour and
concentration.

**Class reality.** At a $1-1.5M buy box, Class A is impossible (~$200K+/unit
product). Realistic target is Class B in good suburbs (Gonzales, Prairieville,
Denham Springs, Covington/Northshore) or B-/C+ in transitional submarkets
(Baker, North BR). Suburb quality and school district are the practical proxy
for "Class B" at this check size. Keep telling him this honestly.

**Multiple smaller properties are acceptable** if the numbers work. Do not
auto-discard 4-7 unit deals; flag them with the management-economics caveat
(third-party management is thin under ~8 units, and closing, loan fees and
insurance minimums are fixed per deal).

**Equity, not price, is the binding constraint.** The "≤ ~$2M" ceiling is looser
than the capital actually allows. A $1.9M / 44-door deal needs ~$532K of equity
against ~$300K available. Check equity before geography on anything above ~20
doors. Reserve rule: never close with less than $30-40K liquid.

**Report the filter, not just the result.** When reporting an empty pipeline,
state the filter that produced the emptiness. A narrow box manufactures a false
"no deals" signal — this was learned 2026-08-08 when NOLA listings were being
auto-killed as "wrong parish" while his own saved search kept surfacing them.

**Metro vs inland is the real line, not parish-by-parish** (settled 2026-08-17
by modelling the same 12-unit building three ways). Price clearing 13%: Baker
$528K (equity $146,520), Gretna $397K ($110,168), Chalmette $388K ($107,670).
St. Bernard vs Jefferson is 2.3% apart — noise. The $140K metro/inland gap is
**wind insurance** ($3,000/unit vs $2,000), which hits Orleans and Jefferson
identically. Corollary that matters more than the parish list: capital is NOT
the binding constraint on metro deals at this size — the bindable insurance
quote is, and one quote unblocks the whole metro box.

**NOLA underwriting differences** (Orleans/Jefferson/St. Bernard): millage
Orleans 131.99, Jefferson 118.40, St. Bernard 141.10 vs EBR 108.80. Insurance
$3,000/unit for wind vs $2,000 elsewhere. ALWAYS run flood.py — much of Orleans
is Zone X only because of levee protection, so say the residual risk out loud
rather than calling it clean. Never underwrite short-term-rental income as lease
income; Orleans regulates STR heavily. The $40-70K/unit Baton Rouge
price-per-unit screen does NOT transfer to New Orleans — judge on income math.

**MHP preference: vacant utilities-live pads are embedded value.** A new
manufactured home on an owned pad runs ~$70-90K/door all-in and yields 11-14% on
cost at $1,200-1,400 rents — a free option the seller's NOI-based price does not
charge for. Screening order: (1) parks with vacant live pads, (2) parks with
dying homes on good pads (replace at turnover), (3) full parks. Raw pads with no
utilities (+$15-25K build-out) do NOT count. Infill is a cash-flow play, not an
appraisal play (breaks even on value at an 11% exit cap). Never finance homes
with chattel debt (8.5%/15yr drops CoC to 8%) — fund from park cash flow or a
park-level refi.

**Strategy (adopted 2026-08-02).** Pursue band 13%+ IRR at negotiated price (16%
strong, 22% ideal — the solver prints all three). Types: MHPs (tenant-owned
homes preferred) plus SFR/duplex portfolios. Ground-up build is NO for now: no
cost edge, equity too small, no track record. The future path is 1-2 infill
fourplexes construction-to-perm AFTER the first stabilized asset. Method:
patience, aggressive below-ask offers on stale listings, off-market via brokers.

**Offer-stage hard gates.** Bindable insurance quote + parceltax reconciliation +
seller T-12/rent roll before ANY offer. House DSCR is 1.30 (banks require 1.25;
Stoa's own hurdle, adopted at his request).

## Pending work
1. DONE 2026-08-01: ingestion scripts built and verified (tools/intake.py, parsers.py,
   recalc.py + pymodel.py python twin; parity-tested per deal in tools/test_pymodel.py).
2. Optional: extend waterfall to Stoa's full 4-tier (8% pref / 10% / 12% / 10-20-30 promote).
3. Optional: floating-rate forward curve for construction loan (currently fixed rate).
4. Analyze live listings as the user brings them.
5. Verify the two master .xlsx workbooks against the 2026-08-09 engine fixes (post-payoff
   debt service when amort < hold; waterfall negative-distributable-year handling) — the
   Python engine is fixed; the workbook formulas may share the same edge-case bugs.
