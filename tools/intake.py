#!/usr/bin/env python3
"""Deal intake: parse a deal package into a per-deal copy of a model, recalculate,
and report against the 12-14% realistic / 16-17% target IRR bands.

  python3 tools/intake.py --deal oak-street                       # dry run
  python3 tools/intake.py --deal oak-street --price 1250000 --apply --recalc
  python3 tools/intake.py --deal river-road --model dev --apply --recalc

Master workbooks are never modified. Each run writes deal-intake/<deal>/<deal>_<model>.xlsx.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import defaults
import flood as _flood
import latax
import parsers
import recalc as rc
import report as rpt
import solve
import strategies
from cellmap import BENCHMARKS, DEV_LOOKUP_TYPES, MODELS
from safe_writer import ModelWriter, clone_model

ROOT = Path(__file__).resolve().parent.parent
INTAKE = ROOT / "deal-intake"

FILE_HINTS = {
    "rent_roll": ("rentroll", "rent_roll", "rent roll", "_rr", "rr_"),
    "t12": ("t12", "t-12", "t_12", "trailing", "operating statement", "opstatement", "income statement", "p&l", "pnl", "pl_", "pl-", "actuals"),
    "comps": ("comp", "comps", "rent comp", "market survey"),
}


_FULL_DATE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
_YEAR_ONLY = re.compile(r"(20\d{2})")


def _recency_key(p):
    """Sort key for picking the most recent of several candidate source files.

    Sweep 2026-08-24: discovery used to take the alphabetically FIRST match and
    silently drop the rest, which is actively perverse on real deal folders --
    ESTIMATED beat OM_2026-08-13 for baker-trails, and 'P&L 2025' beat
    'P&L YTD 2026' for hwy42. Newest date in the filename wins instead, and the
    losers are reported rather than dropped.
    """
    m = _FULL_DATE.search(p.name)
    if m:
        return (2, m.group(1) + m.group(2) + m.group(3), p.name)
    y = _YEAR_ONLY.search(p.name)
    if y:
        return (1, y.group(1), p.name)
    return (0, "", p.name)


def discover(deal_dir):
    """Return ({kind: chosen Path}, {kind: [rejected Paths]}).

    Every candidate is surfaced: nothing is silently dropped (tools/README.md).
    """
    cands = {kind: [] for kind in FILE_HINTS}
    if deal_dir.exists():
        for p in sorted(deal_dir.iterdir()):
            if p.suffix.lower() not in (".csv", ".xlsx", ".xlsm", ".pdf"):
                continue
            low = p.name.lower()
            for kind, hints in FILE_HINTS.items():
                if any(h in low for h in hints):
                    cands[kind].append(p)
    found, alternates = {}, {}
    for kind, lst in cands.items():
        if not lst:
            continue
        ranked = sorted(lst, key=_recency_key, reverse=True)
        found[kind] = ranked[0]
        if len(ranked) > 1:
            alternates[kind] = ranked[1:]
    return found, alternates


def write_rent_roll(w, spec, groups):
    m = spec["rent_roll"]
    sheet, r0, r1 = m["sheet"], m["first_row"], m["last_row"]
    cap = r1 - r0 + 1
    dropped = groups[cap:]
    for i in range(cap):
        r = r0 + i
        g = groups[i] if i < len(groups) else None
        w.set_rc(sheet, r, m["col_units"], g["units"] if g else 0)
        w.set_rc(sheet, r, m["col_type"], g["type"] if g else "BR/BA")
        w.set_rc(sheet, r, m["col_sf"], g["sf"] if g else 0)
        w.set_rc(sheet, r, m["col_rent"], g["rent"] if g else 0)
    return dropped


def write_unit_mix(w, spec, groups):
    m = spec["unit_mix"]
    sheet, r0, r1 = m["sheet"], m["first_row"], m["last_row"]
    cap = r1 - r0 + 1
    dropped = groups[cap:]
    for i in range(cap):
        r = r0 + i
        g = groups[i] if i < len(groups) else None
        w.set_rc(sheet, r, m["col_units"], g["units"] if g else 0)
        w.set_rc(sheet, r, m["col_type"], g["type"] if g else "BR/BA")
        w.set_rc(sheet, r, m["col_sf"], g["sf"] if g else 0)
    return dropped


def write_comps(w, spec, groups):
    m = spec["comps"]
    sheet, r0, r1 = m["sheet"], m["first_row"], m["last_row"]
    w.clear_block(sheet, r0, r1, m["col_include"], m["col_rent"])
    cap = r1 - r0 + 1
    for i, g in enumerate(groups[:cap]):
        r = r0 + i
        w.set_rc(sheet, r, m["col_include"], 1)
        w.set_rc(sheet, r, m["col_units"], g["units"])
        w.set_rc(sheet, r, m["col_type"], g["type"])
        w.set_rc(sheet, r, m["col_sf"], g["sf"])
        w.set_rc(sheet, r, m["col_rent"], g["rent"])
    return groups[cap:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    ap.add_argument("--model", choices=("acq", "dev"), default="acq")
    ap.add_argument("--rent-roll")
    ap.add_argument("--t12")
    ap.add_argument("--comps")
    ap.add_argument("--sheet-rentroll", help="worksheet name override (multi-sheet workbooks)")
    ap.add_argument("--sheet-t12")
    ap.add_argument("--sheet-comps")
    ap.add_argument("--price", type=float, help="purchase price (acq) or land price (dev)")
    ap.add_argument("--units-override", type=int, help="unit count for T-12 per-unit math if rent roll absent")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="KEY=VAL", help="override a template default, e.g. --set mgmt_pct=6%% --set insurance=1800")
    ap.add_argument("--no-defaults", action="store_true",
                    help="write only what the deal documents supply; leave the model's own values alone")
    ap.add_argument("--location", "--tax-district", dest="location",
                    help="town or parish, e.g. 'Gonzales', 'Slidell', 'Ascension'")
    ap.add_argument("--commercial-share", type=float, default=0.0,
                    help="fraction of a mixed-use building in commercial use (assessed at 15%%)")
    ap.add_argument("--refi-year", type=int,
                    help="model a refinance in this year to repay your investor (acq only)")
    ap.add_argument("--reno-units", type=int, help="number of units to renovate")
    ap.add_argument("--reno-cost", type=float, help="renovation cost per unit")
    ap.add_argument("--reno-premium", type=float, help="rent increase per renovated unit per month")
    ap.add_argument("--reno-per-year", type=int, help="units renovated per year")
    ap.add_argument("--reno-downtime", type=float, help="months a unit sits empty during its turn")
    ap.add_argument("--reno-start-year", type=int, help="year renovation begins")
    ap.add_argument("--address", help="street address for FEMA flood zone check, e.g. '5087 Baker Blvd, Baker, LA 70714'")
    ap.add_argument("--solve-price", action="store_true",
                    help="also solve what you would have to pay to hit 13%%, 16%% and 22%% IRR")
    ap.add_argument("--compare", action="store_true",
                    help="run every exit path (sell yr 2/3/5, refi+hold) side by side")
    ap.add_argument("--keep-sellers-tax", action="store_true",
                    help="do NOT reassess on purchase price; keep the seller's tax bill")
    ap.add_argument("--apply", action="store_true", help="write the deal workbook")
    ap.add_argument("--recalc", action="store_true", help="recalculate and report (implies --apply)")
    args = ap.parse_args()

    if args.recalc:
        args.apply = True

    spec = MODELS[args.model]
    deal_dir = INTAKE / args.deal
    src, alternates = discover(deal_dir)
    if args.rent_roll:
        src["rent_roll"] = Path(args.rent_roll)
    if args.t12:
        src["t12"] = Path(args.t12)
    if args.comps:
        src["comps"] = Path(args.comps)

    print(f"DEAL: {args.deal}   MODEL: {args.model}")
    print(f"  intake dir: {deal_dir}")
    for k in ("rent_roll", "t12", "comps"):
        print(f"  {k:<10} {src.get(k) if src.get(k) else '(none)'}")

    groups, comp_groups, t12_lines = [], [], {}
    all_notes = []

    # Several files matched the same slot. Say so loudly: the auto-pick is the
    # newest by filename date, which is a guess, and the override is one flag.
    _overrides = {"rent_roll": "--rent-roll", "t12": "--t12", "comps": "--comps"}
    for kind in ("rent_roll", "t12", "comps"):
        others = alternates.get(kind)
        if not others or getattr(args, kind if kind != "rent_roll" else "rent_roll"):
            continue
        print(f"\n  WARNING: {len(others) + 1} files match '{kind}' in {deal_dir.name}.")
        print(f"    using   {src[kind].name}   (newest date in filename)")
        for o in others:
            print(f"    ignored {o.name}")
        print(f"    override with {_overrides[kind]} <path> if that is the wrong one")
        all_notes.append(
            f"{kind}: {len(others) + 1} candidate files - used {src[kind].name}, "
            f"ignored {', '.join(o.name for o in others)}")

    if src.get("rent_roll"):
        groups, notes = parsers.parse_rent_roll(src["rent_roll"], sheet=args.sheet_rentroll)
        all_notes += [f"rent roll: {n}" for n in notes]
        print("\n  PARSED UNIT MIX")
        print(f"    {'Type':<18}{'Units':>7}{'SF':>8}{'Rent':>9}")
        for g in groups:
            print(f"    {g['type']:<18}{g['units']:>7}{g['sf']:>8}{g['rent']:>9,}")
        print(f"    {'TOTAL':<18}{sum(g['units'] for g in groups):>7}")

    if src.get("comps"):
        comp_groups, notes = parsers.parse_comps(src["comps"], sheet=args.sheet_comps)
        all_notes += [f"comps: {n}" for n in notes]
        print("\n  PARSED COMPS")
        for g in comp_groups:
            print(f"    {g['type']:<18}{g['units']:>7}{g['sf']:>8}{g['rent']:>9,}")

    units = args.units_override or sum(g["units"] for g in groups) or None

    if src.get("t12"):
        t12_lines, notes = parsers.parse_t12(src["t12"], units, sheet=args.sheet_t12)
        all_notes += [f"t12: {n}" for n in notes]
        print("\n  PARSED T-12")
        for k, v in t12_lines.items():
            print(f"    {k:<20}{v['annual']:>12,.0f}   [{v['basis']}]  <- {v['label'][:40]}")
        if units:
            rpt.print_benchmark_table(
                parsers.compare_to_benchmarks(t12_lines, units, BENCHMARKS), units)
        else:
            all_notes.append("t12: no unit count - skipped per-unit benchmark comparison.")
        if "mgmt_pct" in t12_lines:
            all_notes.append(
                f"t12: management fee parsed as ${t12_lines['mgmt_pct']['annual']:,.0f}/yr, but the model "
                "input is a % of EGR - NOT written. Set it manually after checking implied %.")
        if "contract_services" in t12_lines and "utilities" in t12_lines:
            all_notes.append(
                "t12: verify trash/waste landed in utilities, not contract services - keyword overlap.")

    if args.model == "dev":
        # Union: a unit-mix type absent from comps still hits the VLOOKUP and
        # silently reads 0 rent, so both lists must be validated.
        bad = [g["type"] for g in comp_groups + groups if g["type"] not in DEV_LOOKUP_TYPES]
        if bad:
            all_notes.append(
                "dev model VLOOKUP only resolves " + ", ".join(DEV_LOOKUP_TYPES)
                + f"; these will return 0 rent: {sorted(set(bad))}")

    # Overrides are parsed and validated before the dry-run exit so a bad
    # --set fails loudly even in preview mode.
    try:
        overrides = dict(defaults.parse_override(s) for s in args.sets)
    except ValueError as e:
        print(f"  BAD --set: {e}")
        sys.exit(2)
    # Unknown keys must fail loudly in BOTH defaults and --no-defaults mode -
    # a silently dropped override looks identical to an applied one.
    known = set(defaults.TEMPLATES[args.model]) | set(spec["scalars"])
    unknown = sorted(k for k in overrides if k not in known)
    if unknown:
        print(f"  BAD --set: unknown key(s): {', '.join(unknown)}. "
              f"Valid keys are the template defaults and model inputs.")
        sys.exit(2)
    if args.refi_year is not None:
        overrides["refi_year"] = args.refi_year
    for flag, key in (("reno_units", "reno_units"), ("reno_cost", "reno_cost_per_unit"),
                      ("reno_premium", "reno_premium_month"), ("reno_per_year", "reno_units_per_year"),
                      ("reno_downtime", "reno_downtime_months"), ("reno_start_year", "reno_start_year")):
        val = getattr(args, flag)
        if val is not None:
            overrides[key] = val

    if not args.apply:
        print("\n  DRY RUN - nothing written. Re-run with --apply --recalc.")
        _print_notes(all_notes)
        return

    dest = deal_dir / f"{args.deal}_{args.model}.xlsx"
    clone_model(ROOT / spec["file"], dest)
    w = ModelWriter(dest, spec)

    if groups:
        dropped = write_unit_mix(w, spec, groups) if args.model == "dev" else write_rent_roll(w, spec, groups)
        if dropped:
            all_notes.append(
                f"{len(dropped)} unit type(s) exceeded the model's row capacity and were NOT written: "
                + ", ".join(f"{g['type']} ({g['units']}u)" for g in dropped))
    if comp_groups and args.model == "dev":
        dropped = write_comps(w, spec, comp_groups)
        if dropped:
            all_notes.append(f"{len(dropped)} comp row(s) exceeded capacity and were NOT written.")

    parsed_keys = set()
    if args.price is not None:
        price_key = "purchase_price" if args.model == "acq" else "land_price"
        w.set(spec["scalars"][price_key], args.price)
        parsed_keys.add(price_key)

    if t12_lines and units:
        for key in ("payroll", "ga", "marketing", "rm", "contract_services",
                    "utilities", "other", "insurance"):
            if key in t12_lines and key in spec["scalars"]:
                w.set(spec["scalars"][key], round(t12_lines[key]["annual"] / units))
                parsed_keys.add(key)
        if "taxes_annual" in t12_lines:
            tax_ref = spec["scalars"].get("taxes_annual") or spec["scalars"].get("taxes_stabilized")
            w.set(tax_ref, round(t12_lines["taxes_annual"]["annual"]))
            parsed_keys.add("taxes_annual")

    # A sale resets the assessment. Keeping the seller's bill understates taxes,
    # which overstates NOI, loan size, and IRR together.
    if args.model == "acq" and args.price and not args.keep_sellers_tax:
        if not args.location:
            all_notes.append("tax: no --location given, so taxes were NOT reassessed to the purchase "
                             "price. Pass the town, e.g. --location 'Gonzales'.")
        else:
            est, exp = latax.estimate_tax(args.price, args.location, args.commercial_share)
            if est is None:
                all_notes.append(f"tax: {exp}")
            else:
                sellers = t12_lines.get("taxes_annual", {}).get("annual")
                w.set(spec["scalars"]["taxes_annual"], round(est))
                parsed_keys.add("taxes_annual")
                print("\n  PROPERTY TAX REASSESSED ON PURCHASE")
                print(f"    {exp}")
                if sellers:
                    print(f"    seller's current bill ${sellers:,.0f} -> ${est:,.0f} ({est - sellers:+,.0f}/yr)")
                print("    Parish-wide rate; districts inside a parish vary. Confirm with the "
                      "assessor before offering. --keep-sellers-tax to disable.")

    if not args.no_defaults:
        prov = defaults.resolve(args.model, units, parsed_keys, overrides)
        for row in prov:
            ref = spec["scalars"].get(row["key"])
            if ref and row["value"] is not None:
                w.set(ref, row["value"])
        rpt.print_provenance(prov, overrides)
        for row in prov:
            if row["source"] == "derived" and row["value"] is None:
                all_notes.append(f"default {row['key']}: {row['note']}")
    # Explicit --set always applies - including under --no-defaults, and for
    # keys outside the template (e.g. taxes_annual after reassessment).
    template_keys = set(defaults.TEMPLATES[args.model]) if not args.no_defaults else set()
    for k, v in overrides.items():
        if k in spec["scalars"] and k not in template_keys:
            w.set(spec["scalars"][k], v)

    w.save()
    print(f"\n  WROTE {len(w.writes)} input cells -> {dest}")

    if args.address:
        print("\n  FLOOD ZONE CHECK")
        try:
            fres = _flood.lookup(args.address)
            print(f"    Address : {fres['address']}")
            print(f"    Zone    : {fres['zone']}")
            print(f"    SFHA    : {'YES - flood insurance required' if fres['sfha'] else 'no'}")
            print(f"    Note    : {fres['note']}")
            if fres["sfha"]:
                print()
                print("    *** WARNING: SFHA PROPERTY ***")
                print("    Lender will require flood insurance. The template insurance")
                print("    default does NOT include flood coverage. This screen is")
                print("    UNRELIABLE until you have a bindable flood quote in hand.")
                print("    Do not advance this deal without a flood insurance quote.")
        except Exception as e:
            print(f"    flood check unavailable: {e}")

    if not args.recalc:
        print("  Not recalculated. Formula cells hold no cached values until you recalc.")
        _print_notes(all_notes)
        return

    try:
        rc.recalc(dest)
    except rc.RecalcUnavailable as e:
        print(f"\n  RECALC FAILED\n{e}")
        _print_notes(all_notes)
        sys.exit(2)

    # Second pass: the exit cap should key off the going-in cap, which is not
    # known until the first recalc. Selling into a richer cap than you bought is
    # an assumption, not performance, so default to 50bps of expansion.
    if args.model == "acq" and not args.no_defaults and "exit_cap" not in overrides:
        going_in = rc.read_values(dest, {"c": spec["outputs"]["going_in_cap"]})["c"]
        if isinstance(going_in, (int, float)) and going_in > 0:
            solved = round(going_in + 0.005, 5)
            w2 = ModelWriter(dest, spec)
            w2.set(spec["scalars"]["exit_cap"], solved)
            w2.save()
            try:
                rc.recalc(dest)
            except rc.RecalcUnavailable as e:
                print(f"\n  RECALC FAILED (exit-cap second pass - workbook values are stale)\n{e}")
                _print_notes(all_notes)
                sys.exit(2)
            print(f"\n  EXIT CAP SOLVED: going-in {going_in*100:.2f}% + 50bps -> {solved*100:.2f}% "
                  f"(override with --set exit_cap=6.5%)")

    errors = rc.scan_errors(dest)
    checks = rc.read_checks(dest, spec)
    vals = rc.read_values(dest, spec["outputs"])
    scal = rc.read_values(dest, spec["scalars"])

    (rpt.print_acq if args.model == "acq" else rpt.print_dev)(vals, checks, errors, scal)

    if args.compare and args.model == "acq":
        strategies.compare(dest, spec)

    if args.solve_price and args.model == "acq" and args.price:
        # The solver ALWAYS re-derives exit_cap per trial price (going-in +
        # 50bps): freezing the ask's cap would let every trial price sell into
        # unearned compression, inflating solve IRRs by 2-3pts (bug found by
        # pymodel cross-check 2026-08-02). The house rule is never to credit a
        # richer exit than entry, so an explicit --set exit_cap only applies to
        # the base workbook, never the solve.
        if "exit_cap" in overrides:
            print("\n  WARNING: exit_cap override ignored by solver: exit cap is always "
                  "re-derived as going-in+50bps per trial price")
        fixed_keys = ["reno_units", "reno_cost_per_unit", "reno_premium_month",
                      "reno_units_per_year", "reno_downtime_months", "reno_start_year",
                      "refi_year", "mgmt_pct", "insurance"]
        fixed = {k: scal[k] for k in fixed_keys if k in scal and scal[k] is not None}
        work = dest.parent / f"__solve_{args.deal}.xlsx"
        print("\n  PRICE SOLVER (what you would have to pay)")
        for label, target in (("13% - pursue threshold", 0.13),
                              ("16% - strong deal", 0.16),
                              ("22% - your ideal", 0.22)):
            res = solve.solve_price(dest, spec, target, args.price, fixed,
                                    args.location, args.commercial_share, work)
            if res is None:
                print(f"    {label:<24} unreachable at any sensible price")
            elif res.get("note"):
                print(f"    {label:<24} already clears at ${args.price:,.0f}")
            else:
                disc = (1 - res["price"] / args.price) * 100
                print(f"    {label:<24} ${res['price']:,.0f}  "
                      f"({disc:.0f}% below asking, IRR {res['irr']*100:.1f}%)")
        work.unlink(missing_ok=True)

    _print_notes(all_notes)


def _print_notes(notes):
    if notes:
        print("\n  NOTES / UNRESOLVED")
        for n in notes:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
