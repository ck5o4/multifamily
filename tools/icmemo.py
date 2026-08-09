#!/usr/bin/env python3
"""Investment-committee memo generator.

  python3 tools/icmemo.py <deal-name>
  python3 tools/icmemo.py baker-trails --address "5087 Baker Blvd, Baker, LA 70714"
  python3 tools/icmemo.py eden-church-mhp --address "30263 Eden Church Rd, Denham Springs, LA 70726" \\
      --assessment 24637

Writes deal-intake/<deal>/IC_MEMO_<date>.md. Every number is pulled live from
the deal workbook via pymodel — the memo cannot disagree with the underwriting.
"""

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parent.parent
INTAKE = ROOT / "deal-intake"
DEALS_PATH = ROOT / "portfolio" / "deals.json"
TODAY = date.today().isoformat()


def _git_hash():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, cwd=str(ROOT)
        ).decode().strip()
    except Exception:
        return "unknown"


def _load_deals():
    if not DEALS_PATH.exists():
        return {}
    return json.loads(DEALS_PATH.read_text())


def _detect_rent_roll(deal_dir: Path):
    """Return (path, status) where status is 'actual' | 'estimated' | 'from_listing'."""
    for p in sorted(deal_dir.iterdir()):
        low = p.name.lower()
        if any(h in low for h in ("rentroll", "rent_roll", "rent roll", "_rr", "rr_")):
            if "estimated" in low:
                return p, "estimated"
            if "from_listing" in low or "from listing" in low:
                return p, "from_listing"
            return p, "actual"
    return None, None


def _detect_t12(deal_dir: Path):
    """Return path to T-12 file if one exists."""
    for p in sorted(deal_dir.iterdir()):
        low = p.name.lower()
        if any(h in low for h in ("t12", "t-12", "t_12", "trailing",
                                   "operating statement", "opstatement",
                                   "income statement", "p&l", "pnl",
                                   "pl_", "pl-", "actuals")):
            return p
    return None


def _insurance_noted(deal_dir: Path, history_text: str) -> bool:
    """True if an insurance quote is mentioned anywhere in deal files or notes."""
    for p in deal_dir.iterdir():
        if "insurance" in p.name.lower() or "flood" in p.name.lower():
            return True
    return ("insurance quote" in history_text.lower()
            or "flood quote" in history_text.lower()
            or "bindable" in history_text.lower())


def _parcel_tax_reconcile(parish: str, assessment: float, price: float) -> list[str]:
    """Return lines for parcel tax section using latax constants directly."""
    try:
        from latax import MILLAGE, ASSESSMENT_RATIO_RESIDENTIAL
    except ImportError:
        return ["  parcel tax: latax module unavailable"]
    parish = parish.lower().strip()
    if parish not in MILLAGE:
        return [f"  parcel tax: unknown parish '{parish}' (known: {', '.join(sorted(MILLAGE))})"]
    mills = MILLAGE[parish]
    ratio = ASSESSMENT_RATIO_RESIDENTIAL
    implied_fmv = assessment / ratio
    current_tax = assessment * mills / 1000
    new_assessment = price * ratio
    new_tax = new_assessment * mills / 1000
    lines = [
        f"  Parish            : {parish.title()}  ({mills:.2f} mills, parish avg)",
        f"  Current assessment: ${assessment:,.0f}  (assessor carries ~${implied_fmv:,.0f} FMV)",
        f"  Seller's tax bill : ${current_tax:,.0f}/yr",
        f"  Post-sale assess  : ${new_assessment:,.0f}  (at your ${price:,.0f} purchase)",
        f"  YOUR tax bill     : ${new_tax:,.0f}/yr",
        f"  Reassessment jump : ${new_tax - current_tax:+,.0f}/yr  ({new_tax/current_tax:.1f}x)" if current_tax else "",
    ]
    if implied_fmv < price * 0.6:
        lines.append("  NOTE: assessor's FMV is far below your price — broker pro forma")
        lines.append("        materially understates your tax bill. Expect the full jump.")
    return [l for l in lines if l]


def _infer_parish(address: str):
    """Infer parish from an address by whole-token city matching.

    Tokenizes via latax._norm (commas/state/zip stripped), matches multi-word
    city names as whole token runs, longest match, preferring matches nearest
    the END of the string (the city position). Fixes 'Central Ave, Metairie'
    resolving to EBR via the unanchored substring 'central'."""
    from latax import CITY_TO_PARISH, _norm
    tokens = _norm(address).split()
    best = None  # (end_index, n_words, parish)
    for city, par in CITY_TO_PARISH.items():
        ct = city.split()
        n = len(ct)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == ct:
                cand = (i + n, n, par)
                if best is None or cand[:2] > best[:2]:
                    best = cand
    return best[2] if best else None


def _is_mhp(deal: str, wb_path: Path) -> bool:
    """True if the deal name says MHP or the workbook unit types include lot rent."""
    if "mhp" in deal.lower():
        return True
    try:
        import openpyxl
        ws = openpyxl.load_workbook(wb_path, data_only=True)["Inputs"]
        for row in range(3, 11):
            t = ws.cell(row=row, column=6).value
            if t and "lot" in str(t).lower():
                return True
    except Exception:
        pass
    return False


MHP_HEDONIC_CAVEAT = ("hedonic market value UNRELIABLE for MHPs (apartment-sale "
                      "sample, 1 MHP in n=56) — income approach primary")


def main():
    ap = argparse.ArgumentParser(description="IC memo generator")
    ap.add_argument("deal", help="deal name under deal-intake/")
    ap.add_argument("--address", help="street address for FEMA flood zone check")
    ap.add_argument("--assessment", type=float,
                    help="parcel's current TOTAL assessed value from the assessor/listing")
    args = ap.parse_args()

    deal = args.deal
    deal_dir = INTAKE / deal
    if not deal_dir.exists():
        print(f"ERROR: no deal-intake folder at {deal_dir}")
        sys.exit(1)

    wb_path = deal_dir / f"{deal}_acq.xlsx"
    if not wb_path.exists():
        print(f"ERROR: no workbook at {wb_path}")
        print(f"  Run: python3 tools/intake.py --deal {deal} --price <price> --apply --recalc")
        sys.exit(1)

    import pymodel
    try:
        inputs = pymodel._load_deal(deal)
        r = pymodel.run(inputs)
    except Exception as e:
        print(f"ERROR: pymodel failed for {deal}: {e}")
        sys.exit(1)

    deals = _load_deals()
    deal_rec = deals.get(deal, {})
    history = deal_rec.get("history", [])
    history_text = " ".join(h.get("note", "") for h in history)

    # Audit 2026-08-05: without 'location', solve_price freezes taxes at the
    # loaded basis and reprints the exact ladder bias diagnosed on 08-03.
    loc = deal_rec.get("location")
    if loc:
        inputs["location"] = loc
    else:
        print(f"WARNING: no 'location' set for {deal} in portfolio/deals.json — "
              "price-solve ladder will run with FROZEN taxes (biased). "
              "Add a location field.", file=sys.stderr)

    # Detect rent roll and T-12
    rr_path, rr_status = _detect_rent_roll(deal_dir)
    t12_path = _detect_t12(deal_dir)
    ins_noted = _insurance_noted(deal_dir, history_text)

    # Pull deal header from workbook inputs
    price = inputs["price"]
    total_units = sum(g["units"] for g in inputs["unit_mix"])

    # Build memo lines
    lines = []

    def h1(txt):
        lines.append(f"# {txt}")
        lines.append("")

    def h2(txt):
        lines.append(f"## {txt}")
        lines.append("")

    def p(*args):
        lines.append(" ".join(str(a) for a in args))

    def blank():
        lines.append("")

    # Header
    h1(f"INVESTMENT COMMITTEE MEMO — {deal.upper()}")
    p(f"**Date:** {TODAY}  |  **Prepared by:** Ridgeback Peak Underwriting System")
    blank()

    # Deal header
    h2("DEAL HEADER")
    p(f"- **Deal:** {deal}")
    if args.address:
        p(f"- **Address:** {args.address}")
    p(f"- **Ask Price:** ${price:,.0f}")
    p(f"- **Units:** {int(total_units)}")
    if inputs["unit_mix"]:
        for g in inputs["unit_mix"]:
            if g["units"] > 0:
                p(f"  - {int(g['units'])} units  ·  {int(g.get('sf',0))} SF  ·  ${g['rent']:,.0f}/mo")
    blank()

    # Thesis
    h2("INVESTMENT THESIS")
    if history:
        thesis_notes = [h["note"] for h in history if h.get("note") and h["note"] != "created"]
        if thesis_notes:
            for note in thesis_notes[:3]:  # top 3 meaningful notes
                p(f"- {note}")
        else:
            p("- (No thesis notes in portfolio history)")
    else:
        p("- (No portfolio history found)")
    blank()

    # Numbers table
    h2("UNDERWRITING NUMBERS")
    noi1 = r["noi"][1]
    cap = r["going_in_cap"] * 100
    dscr1 = r["dscr"][1]
    equity = r["total_equity"]
    irr = (r["levered_irr"] or 0) * 100
    em = r["equity_multiple"]
    loan = r["loan_amount"]
    # Contract with pymodel: when any hold year has negative distributable cash,
    # run() returns lp_irr/gp_irr/lp_em = None and waterfall_invalid=True. Never
    # quote LP/GP splits in that state (they would overstate).
    wf_invalid = bool(r.get("waterfall_invalid")) or r.get("lp_irr") is None
    neg_years = r.get("waterfall_neg_years") or []
    p("| Metric              | Value             |")
    p("|---------------------|-------------------|")
    p(f"| Ask Price           | ${price:>14,.0f} |")
    p(f"| Y1 NOI              | ${noi1:>14,.0f} |")
    p(f"| Going-in Cap        | {cap:>14.2f}% |")
    p(f"| Loan Amount         | ${loan:>14,.0f} |")
    p(f"| Total Equity Req    | ${equity:>14,.0f} |")
    p(f"| Y1 DSCR             | {dscr1:>14.2f}x |")
    p(f"| Levered IRR         | {irr:>14.1f}% |")
    if wf_invalid:
        p(f"| LP IRR              | {'n/a':>15} |")
    else:
        p(f"| LP IRR              | {r['lp_irr']*100:>14.1f}% |")
    p(f"| Equity Multiple     | {em:>14.2f}x |")
    if wf_invalid:
        blank()
        yrs = ", ".join(str(y) for y in neg_years) if neg_years else "unknown"
        p(f"LP/GP returns: n/a — negative distributable cash in year(s) {yrs}; "
          "splits would overstate")
    blank()

    # Three-target solve ladder
    h2("PRICE SOLVE LADDER")
    p("*(solve_price bisects to hit target IRR with taxes re-derived per pass)*")
    blank()
    for label, target in (("13% — pursue threshold", 0.13),
                           ("16% — strong deal", 0.16),
                           ("22% — ideal", 0.22)):
        sol = pymodel.solve_price(inputs, target)
        if sol is None:
            p(f"- **{label}:** unreachable at any sensible price")
        elif sol.get("note"):
            p(f"- **{label}:** already clears at ${price:,.0f}")
        else:
            disc = (1 - sol["price"] / price) * 100
            p(f"- **{label}:** ${sol['price']:,.0f}  "
              f"({disc:.0f}% below asking, solved IRR {(sol['irr'] or 0)*100:.1f}%)")
    blank()

    # Tornado top-3
    h2("SENSITIVITY — TOP 3 FACTORS (TORNADO)")
    try:
        tresults = pymodel.tornado(inputs)
        base_irr = (r["levered_irr"] or 0) * 100
        p(f"*Base IRR: {base_irr:.1f}%*")
        blank()
        p("| Factor                   | Stressed IRR | Delta   |")
        p("|--------------------------|--------------|---------|")
        for row in tresults[:3]:
            sirr = (row["stressed_irr"] or 0) * 100
            delta = row["delta_irr"] * 100
            p(f"| {row['factor']:<24} | {sirr:>10.1f}%  | {delta:>+6.1f}% |")
    except Exception as e:
        p(f"tornado unavailable: {e}")
    blank()

    # Monte Carlo
    h2("MONTE CARLO SUMMARY")
    try:
        mc = pymodel.monte_carlo(inputs, deal_name=deal)
        if mc["p10"] is not None:
            # the call auto-scales - report the actual n, not a hardcoded 2,000
            p(f"- **Draws:** {mc.get('n_draws', mc.get('n_valid', 0)):,} (auto-scaled)")
            p(f"- **P10 IRR:** {mc['p10']*100:.1f}%")
            p(f"- **P50 IRR:** {mc['p50']*100:.1f}%")
            p(f"- **P90 IRR:** {mc['p90']*100:.1f}%")
            p(f"- **P(IRR ≥ 13%):** {mc['p_above_13']*100:.1f}%  ({mc['n_valid']:,} valid runs)")
            if mc.get("vacancy_note"):
                p(f"- {mc['vacancy_note']}")
        else:
            p("Monte Carlo returned no valid runs.")
    except Exception as e:
        p(f"MC unavailable: {e}")
    p("- House rule 2026-08-04: vacancy risk is judged by deterministic stress "
      "grid, not MC probabilities")
    blank()

    # Hedonic market approach
    h2("MARKET APPROACH (HEDONIC)")
    try:
        import hedonic
        hist_low = history_text.lower()
        if "baker" in deal or "baker" in hist_low:
            mkt = "baton rouge"
        elif "eden" in deal or "denham" in hist_low or "livingston" in hist_low:
            mkt = "baton rouge"
        elif "covington" in deal or "northshore" in hist_low:
            mkt = "northshore"
        elif "new orleans" in hist_low or "nola" in hist_low:
            mkt = "new orleans"
        elif "lafayette" in hist_low:
            mkt = "lafayette"
        else:
            mkt = "baton rouge"
        pred = hedonic.predict(mkt, int(total_units), year=2026,
                               fit_result=hedonic.fit(verbose=False), loud=False)
        bf = pred["band_note"]
        p(f"- **Market:** {mkt.title()}")
        p(f"- **Estimated total value:** ${pred['total_point']:,.0f}  "
          f"(band ${pred['total_low']:,.0f}–${pred['total_high']:,.0f})")
        p(f"- **Per unit:** ${pred['point_per_unit']:,.0f}  "
          f"(low ${pred['low_per_unit']:,.0f} / high ${pred['high_per_unit']:,.0f})")
        p(f"- **Band:** {bf}")
        fr = hedonic.fit(verbose=False)
        p(f"- *Model: n={fr['n']}, R²={fr['r2']:.3f}, SE={fr['res_se']:.4f} — "
          f"use the band, not the point estimate*")
        for cav in pred.get("caveats", []):
            p(f"- *Caveat: {cav}*")
        if _is_mhp(deal, wb_path):
            p(f"- **{MHP_HEDONIC_CAVEAT}**")
    except SystemExit as e:
        reason = str(e).replace("\n", " ").strip()
        p(f"market approach: n/a — {reason}")
    except Exception as e:
        p(f"hedonic error: {e}")
    blank()

    # Flood zone
    if args.address:
        h2("FLOOD ZONE")
        try:
            import flood as _flood
            fres = _flood.lookup(args.address)
            p(f"- **Address (matched):** {fres['address']}")
            p(f"- **FEMA Zone:** {fres['zone']}")
            p(f"- **SFHA:** {'YES — lender requires flood insurance' if fres['sfha'] else 'No'}")
            p(f"- **Note:** {fres['note']}")
            if fres["sfha"]:
                blank()
                p("**⚠ WARNING:** SFHA property. Flood insurance is lender-required and not")
                p("included in the template default. This screen is unreliable without a")
                p("bindable flood insurance quote in hand.")
        except Exception as e:
            p(f"flood check unavailable: {e}")
        blank()

    # Parcel tax reconciliation
    if args.assessment and price:
        h2("PARCEL TAX RECONCILIATION")
        # Infer parish from address (whole-token, end-anchored city match)
        parish = None
        if args.address:
            parish = _infer_parish(args.address)
        if parish is None:
            # Try deal name hints
            if "baker" in deal:
                parish = "east baton rouge"
            elif "eden" in deal or "denham" in deal:
                parish = "livingston"
        if parish:
            tax_lines = _parcel_tax_reconcile(parish, args.assessment, price)
            for tl in tax_lines:
                p(tl)
        else:
            p(f"  assessment ${args.assessment:,.0f} provided but parish not determinable from address/deal name.")
            p("  Run: python3 tools/parceltax.py --parish <parish> "
              f"--assessment {args.assessment} --price {price}")
        blank()

    # Portfolio history
    h2("PORTFOLIO HISTORY")
    if history:
        for h in history:
            note = h.get("note", "")
            p(f"- **{h['date']}** [{h['stage']}]: {note}")
    else:
        p("*(no history)*")
    blank()

    # Diligence gaps
    h2("DILIGENCE GAPS")
    gaps = []

    # Rent roll status
    if rr_path is None:
        gaps.append("❌ No rent roll file found in deal folder")
    elif rr_status == "estimated":
        gaps.append(f"⚠ Rent roll is ESTIMATED ({rr_path.name}) — "
                    "screen is directional only; request actual rent roll before offer")
    elif rr_status == "from_listing":
        gaps.append(f"⚠ Rent roll is FROM_LISTING ({rr_path.name}) — "
                    "not a seller-provided document; request actual rent roll before offer")
    else:
        gaps.append(f"✓ Rent roll: {rr_path.name}")

    # T-12
    if t12_path is None:
        gaps.append("❌ No T-12 / trailing income statement found — "
                    "request from seller before advancing past screen")
    else:
        gaps.append(f"✓ T-12 found: {t12_path.name}")

    # Insurance quote
    if ins_noted:
        gaps.append("✓ Insurance quote noted in deal history or files")
    else:
        gaps.append("❌ No insurance quote on file — "
                    "required before any offer (LA market; template default unreliable)")

    # SFHA warning if applicable
    if args.address:
        try:
            import flood as _flood
            fres = _flood.lookup(args.address)
            if fres["sfha"]:
                gaps.append("❌ SFHA flood zone — flood insurance quote required "
                            "(lender-mandatory; not in template default)")
        except Exception:
            gaps.append("⚠ Flood zone check failed — run flood.py before offer")

    for gap in gaps:
        p(f"- {gap}")
    blank()

    # Footer
    git_hash = _git_hash()
    lines.append("---")
    p(f"*Generated by the Ridgeback Peak underwriting system, {TODAY} — "
      f"figures verified against pymodel {git_hash}*")

    # Write file
    out_path = deal_dir / f"IC_MEMO_{TODAY}.md"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")
    print(f"  deal: {deal}  |  price: ${price:,.0f}  |  units: {int(total_units)}")
    print(f"  IRR: {irr:.1f}%  |  NOI: ${noi1:,.0f}  |  equity: ${equity:,.0f}")


if __name__ == "__main__":
    main()
