"""Calibration log: what the model says vs what the market actually pays.

The Pulse delivers real closed sales in Baton Rouge, New Orleans, Lafayette,
and the Northshore every Monday. Each multifamily sale gets logged here with
whatever is known (price, units, NOI/cap if reported) alongside what our
assumptions would have valued it at. Over months, the report answers the only
question that matters: do our caps and expense assumptions run hot or cold
against real trades?

    python3 tools/calibrate.py add "Palmetto Greens" --price 15200000 --units 240 \\
        --market northshore --date 2026-07-13 --cap 5.9 --source "Pulse Northshore" \\
        --note "reported cap; institutional buyer"
    python3 tools/calibrate.py add "smalltown 8plex" --price 640000 --units 8 \\
        --market "baton rouge" --date 2026-08-01 --model-value 580000
    python3 tools/calibrate.py report

Log EVERY multifamily/MHP sale the Pulse reports, even oversized ones - big
trades calibrate the cap environment, small trades calibrate the buy box.
--model-value is optional: add it when we ran (or retro-run) the deal through
the model; the report separates "cap environment" evidence from "model vs
market" evidence.
"""

import argparse
import json
import pathlib

LOG = pathlib.Path(__file__).resolve().parent.parent / "portfolio" / "calibration.json"


def load():
    if LOG.exists():
        return json.loads(LOG.read_text())
    return []


def save(entries):
    LOG.write_text(json.dumps(entries, indent=2))


def cmd_add(args):
    entries = load()
    entry = {
        "name": args.name,
        "date": args.date,
        "market": args.market.lower(),
        "price": args.price,
        "units": args.units,
        "price_per_unit": round(args.price / args.units) if args.units else None,
        "reported_cap": args.cap,
        "model_value": args.model_value,
        "source": args.source,
        "note": args.note,
    }
    entries.append(entry)
    save(entries)
    ppu = f" (${entry['price_per_unit']:,}/unit)" if entry["price_per_unit"] else ""
    print(f"logged: {args.name} - ${args.price:,.0f}{ppu}")
    if args.model_value:
        drift = (args.model_value / args.price - 1) * 100
        print(f"model vs market: {drift:+.1f}% "
              f"({'model conservative' if drift < 0 else 'model rich'})")


def cmd_report(args):
    entries = load()
    if not entries:
        print("No sales logged yet. Feed it from the Monday Pulse issues.")
        return
    print(f"{len(entries)} logged sales\n")
    print(f"{'date':<12}{'market':<14}{'name':<32}{'price':>12}{'$/unit':>10}{'cap':>7}")
    for e in sorted(entries, key=lambda x: x["date"]):
        ppu = f"{e['price_per_unit']:,}" if e.get("price_per_unit") else "-"
        cap = f"{e['reported_cap']:.2f}%" if e.get("reported_cap") else "-"
        print(f"{e['date']:<12}{e['market']:<14}{e['name'][:30]:<32}"
              f"{e['price']:>12,.0f}{ppu:>10}{cap:>7}")
    caps = [e["reported_cap"] for e in entries if e.get("reported_cap")]
    if caps:
        print(f"\nreported caps: n={len(caps)}, avg {sum(caps)/len(caps):.2f}%, "
              f"range {min(caps):.2f}-{max(caps):.2f}%")
    pairs = [(e["model_value"], e["price"]) for e in entries if e.get("model_value")]
    if pairs:
        drifts = [(mv / p - 1) * 100 for mv, p in pairs]
        print(f"model vs market: n={len(drifts)}, avg drift {sum(drifts)/len(drifts):+.1f}% "
              "(negative = our model values below what buyers actually paid)")


ASKCAPS = pathlib.Path(__file__).resolve().parent.parent / "portfolio" / "askcaps.json"


def cmd_askcap(args):
    """Broker-inflation index: claimed cap vs our honest screened cap."""
    entries = json.loads(ASKCAPS.read_text()) if ASKCAPS.exists() else []
    entries.append({"listing": args.listing, "date": args.date,
                    "type": args.type, "claimed_cap": args.claimed,
                    "honest_cap": args.honest,
                    "inflation_pts": round(args.claimed - args.honest, 2)})
    ASKCAPS.write_text(json.dumps(entries, indent=2))
    print(f"logged: {args.listing} claimed {args.claimed:.2f}% vs honest "
          f"{args.honest:.2f}% (+{args.claimed - args.honest:.1f}pts inflation)")
    by_type = {}
    for e in entries:
        by_type.setdefault(e["type"], []).append(e["inflation_pts"])
    for t, pts in sorted(by_type.items()):
        print(f"  {t}: n={len(pts)}, avg inflation {sum(pts)/len(pts):+.1f}pts")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--price", type=float, required=True)
    a.add_argument("--units", type=int)
    a.add_argument("--market", required=True,
                   help="baton rouge / new orleans / lafayette / northshore / other")
    a.add_argument("--date", required=True, help="YYYY-MM-DD (sale or report date)")
    a.add_argument("--cap", type=float, help="reported cap rate, e.g. 6.5")
    a.add_argument("--model-value", type=float,
                   help="what our model valued it at, if we ran it")
    a.add_argument("--source", default="Pulse")
    a.add_argument("--note", default="")
    a.set_defaults(func=cmd_add)
    r = sub.add_parser("report")
    r.set_defaults(func=cmd_report)
    c = sub.add_parser("askcap")
    c.add_argument("listing")
    c.add_argument("--claimed", type=float, required=True, help="broker's stated cap, e.g. 11.5")
    c.add_argument("--honest", type=float, required=True, help="our screened honest cap")
    c.add_argument("--type", required=True, help="multifamily / mhp-park-owned / mhp-lot-rent / portfolio")
    c.add_argument("--date", required=True)
    c.set_defaults(func=cmd_askcap)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
