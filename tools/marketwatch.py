#!/usr/bin/env python3
"""Automated market survey - the legal version of what revenue-management
software does with comps.

Watches competitor properties' OWN public websites, snapshots advertised
availability and pricing over time, and derives what one snapshot cannot show:
leasing velocity, price cuts, concession activity, seasonality.

Legal position (do not drift from this):
- Public advertised rents on a competitor's public site are public data.
  Observing them unilaterally is the classic "market survey" every leasing
  office does weekly by hand. We automate the clipboard.
- We never use non-public data, never pool data with other landlords, never
  coordinate pricing with anyone. That line is what the RealPage/DOJ case
  was about.
- Be a polite visitor: one fetch per property per run, weekly cadence, no
  hammering.

Usage:
  python3 tools/marketwatch.py add "Legacy at BR" https://www.thelegacybatonrouge.com/floorplans/
  python3 tools/marketwatch.py snapshot          # fetch everything on the watchlist
  python3 tools/marketwatch.py report            # what changed since last time
  python3 tools/marketwatch.py assumptions       # rent/velocity numbers for underwriting

Data lives in market-data/ inside the repo:
  watchlist.json                    the properties being watched
  snapshots/<date>/<slug>.html      raw page, kept so we can re-parse later
  observations.jsonl                one parsed record per property per snapshot
"""

import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "market-data"
WATCHLIST = DATA / "watchlist.json"
OBS = DATA / "observations.jsonl"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CONCESSION_RE = re.compile(
    r"(month\s+free|free\s+month|move[- ]in\s+special|special\s+offer|"
    r"\$0\s+deposit|waived?\s+(app|admin|deposit)|look\s*&?\s*lease|"
    r"reduced\s+rate|limited\s+time|"
    r"\$\s?[1-9][\d,]*(?:\.\d\d)?\s+off\b)", re.I)
PRICE_RE = re.compile(r"\$\s?([1-9][\d,]{2,5})(?:\.\d\d)?")
BED_NEAR_RE = re.compile(r"(\d)\s*(?:bed|br|bd)|studio", re.I)
# A $ amount near these words is a discount/fee, not a rent (audit 2026-08-08:
# Creekside's "$1,000 OFF 2 & 3 Bedrooms | $650 OFF 1 Bedrooms" banner became
# two fake unit rows at $650/$1,000).
DISCOUNT_CTX_RE = re.compile(
    r"\b(off|discount|special|deposit|fee|admin|waived?|save)\b", re.I)


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_watchlist():
    if WATCHLIST.exists():
        return json.loads(WATCHLIST.read_text())
    return []


def save_watchlist(wl):
    DATA.mkdir(exist_ok=True)
    WATCHLIST.write_text(json.dumps(wl, indent=2))


def fetch(url, dest):
    r = subprocess.run(
        ["curl", "-sL", "--max-time", "25", "-A", UA, url, "-o", str(dest),
         "-w", "%{http_code}"],
        capture_output=True, text=True)
    code = (r.stdout or "").strip()
    ok = code == "200" and dest.exists() and dest.stat().st_size > 5000
    return ok, code


# ---------------------------------------------------------------- parsing

def parse_jsonld(html):
    """schema.org blocks many apartment platforms embed - most reliable source."""
    out = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            graph = it.get("@graph", [it])
            for node in (graph if isinstance(graph, list) else [graph]):
                if not isinstance(node, dict):
                    continue
                t = str(node.get("@type", ""))
                if any(k in t for k in ("Apartment", "Accommodation", "Offer", "Product", "Residence")):
                    price = None
                    offers = node.get("offers") or {}
                    if isinstance(offers, dict):
                        price = offers.get("price") or offers.get("lowPrice")
                    price = price or node.get("price")
                    beds = node.get("numberOfBedrooms")
                    if price:
                        try:
                            out.append({"beds": beds, "price": float(str(price).replace(",", "")),
                                        "src": "jsonld", "name": node.get("name")})
                        except ValueError:
                            pass
    return out


def parse_heuristic(html):
    """Fallback: find prices in plausible rent range, grab nearby bed count."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    out = []
    for m in PRICE_RE.finditer(text):
        val = float(m.group(1).replace(",", ""))
        if not (400 <= val <= 5000):  # plausible monthly rent
            continue
        # Context guard: reject amounts surrounded by discount/fee language.
        ctx = text[max(0, m.start() - 40):m.end() + 40]
        if DISCOUNT_CTX_RE.search(ctx):
            continue
        window = text[max(0, m.start() - 120):m.end() + 120]
        bm = BED_NEAR_RE.search(window)
        beds = None
        if bm:
            beds = 0 if bm.group(0).lower().startswith("studio") else int(bm.group(1))
        out.append({"beds": beds, "price": val, "src": "heuristic", "name": None})
    # de-dup identical (beds, price)
    seen, dedup = set(), []
    for r in out:
        k = (r["beds"], r["price"])
        if k not in seen:
            seen.add(k)
            dedup.append(r)
    return dedup


def parse_page(html):
    units = parse_jsonld(html)
    if not units:  # heuristic only when jsonld found nothing - never both
        # (audit 2026-08-08: appending heuristic on top of jsonld double-counted
        # the same listings on pages where jsonld was thin)
        units = parse_heuristic(html)
    concessions = sorted(set(m.group(0).strip() for m in CONCESSION_RE.finditer(html)))
    return units, concessions


# ---------------------------------------------------------------- commands

def cmd_add(name, url):
    wl = load_watchlist()
    if any(w["url"] == url for w in wl):
        print("already watching that url")
        return
    wl.append({"name": name, "slug": slugify(name), "url": url,
               "added": date.today().isoformat()})
    save_watchlist(wl)
    print(f"watching {name} ({len(wl)} properties total)")


def cmd_snapshot():
    wl = load_watchlist()
    if not wl:
        print("watchlist empty - add comps first:  marketwatch.py add \"Name\" <url>")
        return
    day = date.today().isoformat()
    snapdir = DATA / "snapshots" / day
    snapdir.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(exist_ok=True)
    with open(OBS, "a") as obs:
        for w in wl:
            dest = snapdir / f"{w['slug']}.html"
            ok, code = fetch(w["url"], dest)
            if not ok:
                print(f"  FAIL {w['name']}: http {code}")
                rec = {"date": day, "slug": w["slug"], "name": w["name"],
                       "ok": False, "http": code}
            else:
                units, concessions = parse_page(dest.read_text(errors="replace"))
                prices = sorted(u["price"] for u in units)
                print(f"  OK   {w['name']}: {len(units)} listings"
                      f"{', concessions: ' + '; '.join(concessions[:2]) if concessions else ''}")
                rec = {"date": day, "slug": w["slug"], "name": w["name"], "ok": True,
                       "units": units, "concessions": concessions,
                       "n": len(units),
                       "min": prices[0] if prices else None,
                       "max": prices[-1] if prices else None}
            obs.write(json.dumps(rec) + "\n")
    print(f"snapshot saved -> {snapdir}")


def _history():
    if not OBS.exists():
        return {}
    hist = {}
    for line in OBS.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        hist.setdefault(r["slug"], []).append(r)
    return hist


def cmd_report():
    hist = _history()
    if not hist:
        print("no observations yet - run: marketwatch.py snapshot")
        return
    for slug, rows in hist.items():
        rows = [r for r in rows if r.get("ok")]
        if not rows:
            continue
        cur = rows[-1]
        print(f"\n{cur['name']}  ({cur['date']}, {cur['n']} listings, "
              f"${cur['min']:,.0f}-${cur['max']:,.0f})" if cur.get("min") else f"\n{cur['name']}")
        if cur.get("concessions"):
            print(f"  CONCESSIONS LIVE: {'; '.join(cur['concessions'])}")
        if len(rows) >= 2:
            prev = rows[-2]
            dn = cur["n"] - prev["n"]
            if dn < 0:
                print(f"  {-dn} listing(s) gone since {prev['date']} -> likely leased")
            elif dn > 0:
                print(f"  {dn} new listing(s) since {prev['date']} -> new vacancy")
            cp = {(u.get("beds"), u.get("name")): u["price"] for u in prev.get("units", [])}
            for u in cur.get("units", []):
                k = (u.get("beds"), u.get("name"))
                if k in cp and cp[k] != u["price"]:
                    print(f"  price change {k}: ${cp[k]:,.0f} -> ${u['price']:,.0f}")
            if prev.get("concessions") and not cur.get("concessions"):
                print("  concessions PULLED - demand strengthening")
            if cur.get("concessions") and not prev.get("concessions"):
                print("  concessions STARTED - demand softening")


def cmd_assumptions(include_heuristic=False):
    """Underwriting inputs derived from observations: rent by bed count and
    absorption, with sample sizes so thin data cannot masquerade as evidence."""
    hist = _history()
    rows = [r for rs in hist.values() for r in rs if r.get("ok")]
    if not rows:
        print("no observations yet")
        return
    by_beds = {}
    n_heuristic = 0
    for r in rows:
        for u in r.get("units", []):
            # src='heuristic' rows are regex guesses, not structured listings -
            # excluded from medians by default (the 2026-08-08 Creekside banner
            # discounts are in history as heuristic rows).
            if u.get("src") == "heuristic" and not include_heuristic:
                n_heuristic += 1
                continue
            if u.get("beds") is not None:
                by_beds.setdefault(u["beds"], []).append(u["price"])
    if include_heuristic:
        print("WARNING: including src=heuristic rows - regex-scraped prices that "
              "may be discounts, deposits, or fees, not asking rents.")
    print("OBSERVED ASKING RENT (all snapshots, all comps)")
    if not by_beds:
        print("  (no structured price observations yet)")
    if n_heuristic:
        print(f"  ({n_heuristic} heuristic row(s) excluded; --include-heuristic to override)")
    for beds in sorted(by_beds):
        p = sorted(by_beds[beds])
        med = p[len(p) // 2]
        print(f"  {beds} BR: median ${med:,.0f}  range ${p[0]:,.0f}-${p[-1]:,.0f}  (n={len(p)})")
    dates = sorted({r["date"] for r in rows})
    conc = sum(1 for r in rows if r.get("concessions"))
    print(f"\n{len(hist)} properties, {len(dates)} snapshot days, "
          f"concessions present in {conc}/{len(rows)} observations")
    print("Use the median minus ~3-5% for effective rent if concessions are common.")
    if len(dates) < 4:
        print("NOTE: velocity/seasonality need 4+ weekly snapshots to mean anything.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "add" and len(args) >= 3:
        cmd_add(args[1], args[2])
    elif cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "report":
        cmd_report()
    elif cmd == "assumptions":
        cmd_assumptions(include_heuristic="--include-heuristic" in args)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
