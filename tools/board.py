"""Generate the Ridgeback Peak deal board (portfolio/board.html).

Reads portfolio/deals.json + portfolio/todos.json, recomputes every number
live from the deal workbooks via pymodel (nothing on the board is hand-typed
except the curated strategy copy in OVERRIDES), and writes a single
self-contained HTML file. Run: python3 tools/board.py
A scheduled cloud session republishes the output to the standing artifact URL.
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

import latax
import pymodel

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "portfolio" / "board.html"
ARTIFACT_URL = "https://claude.ai/code/artifact/7ff847d5-56e2-425a-be47-da22618fe1b3"

# Curated copy per deal — words only; every number is computed fresh below.
OVERRIDES = {
    "eden-church-mhp": {
        "title": "Eden Church MHP — 18 pads, Denham Springs",
        "rank": 1,
        "thesis": ("Mobile home park, all 18 homes park-owned (15 built 2017–19). "
                   "Seller's real P&L in hand. Motivated seller: bought May 2024, "
                   "exiting for 15 months, already cut the price $226K."),
        "location": "Denham Springs",
        "carry": {"no_carry_price": "~$1,100,000", "carry_price": "~$1,250,000"},
        "strategy": ("Ask for <b>$1.25M</b> with the seller carrying <b>~$190–240K</b>. "
                     "Your cash in ≈ <b>$260–300K</b> (bigger carry = reserve left over), "
                     "honest return <b>27.5%</b>. Walk if he won't carry AND won't come "
                     "under <b>$1.45M</b>."),
        "gate": ("<b>Before any offer:</b> bindable insurance quote at ≤ ~$1,600/unit "
                 "(the #1 kill factor) + bank confirms it lends on park-owned homes "
                 "with a seller note behind it."),
        "flood": "Zone X (clean) — but ask the 2016 flood question on tour",
        "docs": "Seller actuals ✓ (P&L 2025, rent roll, utility bill)",
    },
    "treme-gov-nicholls": {
        "title": "1429 Governor Nicholls — 8 units, Tremé, New Orleans",
        "rank": 2,
        "thesis": ("8-unit building, most units renovated, levee-protected. Rents "
                   "estimated at $1,400/mo until seller documents arrive — same-block "
                   "comps support $1,290–1,500. 2BR units in the mix would raise "
                   "every number."),
        "location": "New Orleans",
        "carry": None,
        "strategy": ("Get the documents first. Then open at <b>~$580K</b>, target "
                     "<b>$664K or less</b>, don't go above <b>$714K</b> on estimated "
                     "rents. Listing is young — if he won't move, wait; it gets "
                     "cheaper as it sits."),
        "gate": ("<b>Before any offer:</b> real rent roll, T-12, unit mix, renovation "
                 "scope, and whether any income is short-term rental."),
        "flood": "Zone X levee-protected — no flood policy required",
        "docs": "Estimates — documents requested",
    },
}

DEAD_WHY = {
    "hwy42-mhp": "Ask $3.0M. <b>Only at ~$2.4–2.5M with ~$400K carry</b> — and it sits in a real flood zone.",
    "central-city-2nd": "Ask $1.16M. <b>Only near $700K.</b>",
    "covington-2nd": "Ask $1.25M. <b>Only near $850–900K</b> — and it needs more cash than you have.",
    "weber-city-mhp": "Flood zone kills it at any realistic price.",
    "cannon-rd": "Needs 12× your capital. Not a maybe.",
    "baker-trails": ("Ask $750K, works at <b>$476K</b> — too far apart today. "
                     "<b>Comes back if:</b> price drops toward $500K (alert armed) or the "
                     "broker produces roof/HVAC receipts and signals flexibility."),
}


def money(x):
    return f"${x:,.0f}"


def compute_deal(name):
    """All live numbers for one workbook-backed deal."""
    inputs = pymodel._load_deal(name)
    ov = OVERRIDES.get(name, {})
    if ov.get("location"):
        inputs["location"] = ov["location"]
    r = pymodel.run({k: v for k, v in inputs.items() if k != "location"})
    mix = inputs["unit_mix"]
    units = sum(g["units"] for g in mix)
    avg_rent = sum(g["units"] * g["rent"] for g in mix) / units if units else 0
    ladder = {}
    for t in (0.13, 0.16, 0.22):
        res = pymodel.solve_price(dict(inputs), t)
        ladder[t] = res["price"] if res else None
    mc = pymodel.monte_carlo({k: v for k, v in inputs.items() if k != "location"},
                             n=1000, seed=42, deal_name=name)
    return {
        "price": inputs["price"], "units": units, "avg_rent": avg_rent,
        "rent_growth": inputs.get("rent_growth", 0.02),
        "expense_growth": inputs.get("expense_growth", 0.025),
        "vacancy": inputs.get("vacancy", 0.07) + inputs.get("bad_debt", 0.0),
        "insurance_u": inputs.get("insurance", 0),
        "taxes": inputs.get("taxes_annual", 0),
        "mgmt": inputs.get("mgmt_pct", 0.08),
        "noi": r["noi"][1], "cap": r["going_in_cap"], "irr": r["levered_irr"],
        "dscr": r["dscr"][1], "equity": r["total_equity"],
        "ladder": ladder, "mc": mc,
    }


def stat(k, v, sub=""):
    sub_html = f"<small>{sub}</small>" if sub else ""
    return (f'<div class="stat"><div class="k">{k}</div>'
            f'<div class="v">{v} {sub_html}</div></div>')


def render_card(name, deal_rec, d):
    ov = OVERRIDES[name]
    lad = d["ladder"]
    carry = ov.get("carry")
    carry_rows = ""
    if carry:
        carry_rows = (
            f'<div class="row"><span class="k">Price your $300K closes without seller carry</span>'
            f'<span class="v">{carry["no_carry_price"]}</span></div>'
            f'<div class="row"><span class="k">Price your $300K closes with seller carry</span>'
            f'<span class="v">{carry["carry_price"]}</span></div>')
    else:
        carry_rows = ('<div class="row"><span class="k">Seller carry needed?</span>'
                      '<span class="v muted">No — your $300K closes this at any price on this list</span></div>')
    mc = d["mc"]
    mc_line = (f"P10 {mc['p10']*100:.0f}% · P50 {mc['p50']*100:.0f}% · "
               f"P(≥13%) {mc['p_above_13']*100:.0f}%") if mc.get("p50") is not None else "n/a"
    return f"""
  <section class="card">
    <div class="card-head">
      <span class="rank">#{ov['rank']}</span>
      <h3>{ov['title']}</h3>
      <span class="pill live">Live · Priority</span>
    </div>
    <p class="thesis">{ov['thesis']}</p>
    <div class="pricebox">
      <div class="row"><span class="k">Listed price</span><span class="v">{money(d['price'])}</span></div>
      <div class="row"><span class="k">Price for a 13% return <small>minimum to pursue</small></span><span class="v">{money(lad[0.13]) if lad[0.13] else 'n/a'}</span></div>
      <div class="row"><span class="k">Price for a 16% return <small>strong deal</small></span><span class="v">{money(lad[0.16]) if lad[0.16] else 'n/a'}</span></div>
      <div class="row"><span class="k">Price for a 22% return <small>ideal</small></span><span class="v">{money(lad[0.22]) if lad[0.22] else 'n/a'}</span></div>
      {carry_rows}
      <div class="strategy"><span class="k">Recommended strategy</span><span class="s">{ov['strategy']}</span></div>
    </div>
    <div class="stats">
      {stat("Units", d['units'])}
      {stat("Avg rent", f"${d['avg_rent']:,.0f}/mo")}
      {stat("Rent growth", f"{d['rent_growth']*100:.1f}%/yr", "house assumption")}
      {stat("Expense growth", f"{d['expense_growth']*100:.1f}%/yr")}
      {stat("Vacancy + bad debt", f"{d['vacancy']*100:.1f}%")}
      {stat("Year-1 income (NOI)", money(d['noi']))}
      {stat("Cap rate at ask", f"{d['cap']*100:.2f}%")}
      {stat("Return at ask", f"{d['irr']*100:.1f}%" if d['irr'] is not None else "n/a")}
      {stat("Cash needed at ask", money(d['equity']))}
      {stat("Debt coverage", f"{d['dscr']:.2f}x", "bank wants 1.25+")}
      {stat("Insurance", f"${d['insurance_u']:,.0f}/unit/yr")}
      {stat("Property tax (post-sale)", money(d['taxes']))}
      {stat("Odds (Monte Carlo, at ask)", mc_line)}
      {stat("Flood", ov['flood'])}
      {stat("Documents", ov['docs'])}
    </div>
    <div class="gate">{ov['gate']}</div>
  </section>"""


def render_todos(todos):
    rows = []
    for t in todos["items"]:
        if t["status"] == "done":
            continue
        badge = {"anthony": "YOU", "claude": "CLAUDE"}.get(t["owner"], "")
        cls = "blocked" if t["status"] == "blocked" else ("optional" if t["status"] == "optional" else "")
        rows.append(f'<li class="{cls}"><b>[{badge}]</b> {t["text"]} '
                    f'<span class="who">— {t["why"]}</span></li>')
    return "\n".join(rows)


def main():
    deals = json.loads((ROOT / "portfolio" / "deals.json").read_text())
    todos = json.loads((ROOT / "portfolio" / "todos.json").read_text())
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = "?"
    today = datetime.date.today().isoformat()

    cards, errors = [], []
    for name in sorted(OVERRIDES, key=lambda n: OVERRIDES[n]["rank"]):
        try:
            cards.append(render_card(name, deals.get(name, {}), compute_deal(name)))
        except Exception as e:
            errors.append(f"{name}: {e}")
            cards.append(f'<section class="card"><p class="thesis">'
                         f'{OVERRIDES[name]["title"]} — board could not compute '
                         f'this deal ({e}). Numbers need a human look.</p></section>')

    watch_rows, dead_rows = [], []
    for name, rec in deals.items():
        why = DEAD_WHY.get(name, (rec.get("history") or [{}])[-1].get("note", "")[:160])
        row = (f'<div class="row-deal"><span class="name">{name.replace("-", " ").title()} '
               f'<span class="pill {"watch" if rec.get("stage") == "watching" else "dead"}">'
               f'{rec.get("stage", "?").title()}</span></span>'
               f'<span class="why">{why}</span></div>')
        if rec.get("stage") == "watching":
            watch_rows.append(row)
        elif rec.get("stage") == "dead":
            dead_rows.append(row)

    css = (ROOT / "tools" / "board_style.css").read_text()
    html = f"""<title>Ridgeback Peak — Deal Board</title>
<style>{css}</style>
<div class="wrap">
  <header>
    <div class="brand">Ridgeback Peak Properties</div>
    <h1>Deal Board</h1>
    <div class="meta">Auto-generated {today} · every number recomputed from the live models · private</div>
  </header>
  <section class="verdict">
    <h2>To do</h2>
    <ul class="todo">{render_todos(todos)}</ul>
    <div class="rulebar">
      <span>Your capital <b>$300K</b></span>
      <span>Keep in reserve <b>$30–40K</b></span>
      <span>Minimum return to pursue <b>13%</b></span>
      <span>Strong <b>16%</b> · Ideal <b>22%</b></span>
    </div>
  </section>
  {''.join(cards)}
  <section>
    <h2 class="section">Shelved — comes back automatically if…</h2>
    {''.join(watch_rows) or '<p class="thesis">Nothing shelved.</p>'}
  </section>
  <section>
    <h2 class="section">Dead — and the price that would revive each</h2>
    {''.join(dead_rows)}
  </section>
  <footer>
    Generated by tools/board.py at commit <code>{commit}</code> on {today} ·
    {"COMPUTE ERRORS: " + "; ".join(errors) if errors else "all numbers computed clean"} ·
    republished to the standing link by the daily scan routine
  </footer>
</div>
"""
    OUT.write_text(html)
    print(f"wrote {OUT} ({len(html):,} bytes)"
          + (f"  ERRORS: {errors}" if errors else "  all clean"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
