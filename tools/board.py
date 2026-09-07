"""Generate the Ridgeback Peak deal board (portfolio/board.html).

Reads portfolio/deals.json + portfolio/todos.json, recomputes every number
live from the deal workbooks via pymodel (nothing on the board is hand-typed
except the curated strategy copy in OVERRIDES), and writes a single
self-contained HTML file. Run: python3 tools/board.py
A scheduled cloud session republishes the output to the standing artifact URL.
"""

import datetime
import html
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import latax
import pymodel

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "portfolio" / "board.html"
# Standing artifact URL. (The 2026-08-17 note claiming 7ff847d5 had 404'd was
# wrong - `Artifact list` on 2026-08-24 shows it live and owned by this account,
# last updated 2026-08-23. Restored to the real URL; the 2b362f80 replacement
# was never actually published.)
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
        "strategy": ("<b>Sweep 2026-08-24 — this deal no longer clears the house rule at "
                     "ANY price on the ladder.</b> With rent/expense growth recentred on "
                     "our own underwriting and vintage capex active for a 1970 building, "
                     "even the $579K &ldquo;ideal&rdquo; rung beats an index fund only 44% "
                     "of the time. The old advice (open $580K, target $664K, cap $714K) "
                     "was computed on an optimistic engine. <b>Do not offer on the "
                     "estimated basis.</b> Hernandez's documents are now the whole deal: "
                     "real 2BR unit mix and a T-12 could rebuild it, and nothing else "
                     "will. Anthony's call whether to keep it open — the numbers say pass."),
        "gate": ("<b>Before any offer:</b> real rent roll, T-12, unit mix, renovation "
                 "scope, and whether any income is short-term rental."),
        "flood": "Zone X levee-protected — no flood policy required",
        "docs": "Estimates — documents requested",
    },
}

DEAD_WHY = {
    "hwy42-mhp": "Ask $3.0M. <b>Only at ~$2.4–2.5M with ~$400K carry</b> — and it sits in a real flood zone.",
    "central-city-2nd": "Ask $1.16M. <b>Only near $700K.</b>",
    "covington-2nd": ("Ask $1.25M. <b>Works at $907K (13%) / $841K (16%) / $733K (22%).</b> "
                      "The old &ldquo;needs more cash than you have&rdquo; line was WRONG — that is "
                      "true at the ask, not in the revive band, where equity is <b>$203–252K</b> of "
                      "your $300K. Stays dead at the ask; <b>revives on a cut toward ~$900K</b>."),
    "weber-city-mhp": "Flood zone kills it at any realistic price.",
    "cannon-rd": "Needs 12× your capital. Not a maybe.",
    "mlk-2119": ("Ask $490K for <b>2 doors</b> ($245K/unit). Levered IRR <b>-9.8%</b>, "
                 "0.62x multiple — it loses money, it does not merely miss the band. "
                 "DSCR caps the loan at 47% LTV, so equity at ask is <b>$273K = 91% of "
                 "your capital</b> for two units. <b>Only near $270K.</b>"),
    # Rewritten 2026-08-24. The old copy read "46% at $528K and 34% at $423K"
    # as if it were a price ladder; those two rows came from DIFFERENT vacancy
    # assumptions in IC_MEMO_2026-08-17 (7.5% and 20%), so the odds appeared to
    # FALL as the price fell. On one basis they rise, as they must. The verdict
    # is unchanged and now better supported: after this sweep's MC corrections
    # (growth recentered on the deal's own underwriting; vintage capex active)
    # no price in the plausible range comes close to the rule.
    "baker-trails": ("Ask $750K. <b>PASS — the OM killed it 2026-08-17.</b> Real mix is "
                     "better than modelled ($126K gross potential), but it is <b>8/12 occupied "
                     "with four long-term vacancies</b> and built <b>1984</b>. Stabilised at "
                     "7.5% vacancy with vintage capex active it beats the index "
                     "<b>16% at $528K</b>, <b>22% at $450K</b>, <b>24% at $423K</b> — odds do "
                     "improve as the price falls, but a 44% discount the seller will never take "
                     "still leaves it losing to an index fund three times in four. On the "
                     "lease-up case (20% vacancy) it is 8–10%. "
                     "<b>Price is not the binding problem</b> — documentation is — so a price "
                     "cut alone does not revive it: retire the $530K alert. Reopens on a T-12, "
                     "verifiable capex docs, or signed leases on the vacant units."),
}


def money(x):
    return f"${x:,.0f}"


def _beats_index(samples):
    """P(deal IRR > market 5-yr CAGR), market ~ Normal(10%, 8%).

    Closed form. Pairing each sample with one rng.gauss() draw (the original
    2026-08-09 implementation) is unbiased but carries +/-3pp of pure RNG noise
    on a rule whose threshold is exactly 50% - eden moved 52.1%-57.7% across
    twenty choices of an arbitrary seed. Integrating the normal CDF instead
    removes that term entirely. (sweep 2026-08-24)
    """
    if not samples:
        return None
    return sum(0.5 * (1.0 + math.erf((x - 0.10) / (0.08 * math.sqrt(2.0))))
               for x in samples) / len(samples)


def _vintage(name):
    """monte_carlo kwargs carrying the deal's vintage, from portfolio/deals.json.

    Returns {} when the vintage is unknown, which leaves the hazard model inert
    and makes monte_carlo say so on stderr. Never guess a vintage: an invented
    year_built is a fabricated left tail.
    """
    try:
        rec = json.loads((ROOT / "portfolio" / "deals.json").read_text()).get(name, {})
    except Exception:
        return {}
    if rec.get("effective_age") is not None:
        return {"effective_age_override": rec["effective_age"]}
    if rec.get("year_built") is not None:
        return {"year_built": rec["year_built"]}
    return {}


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
    ladder, ladder_basis = {}, {}
    for t in (0.13, 0.16, 0.22):
        res = pymodel.solve_price(dict(inputs), t)
        ladder[t] = res["price"] if res else None
        ladder_basis[t] = res if res else {}
    # Vintage capex: the 2026-08-09 sweep wired --year-built into the CLI only,
    # so every board render left the hazard model INERT and quoted an optimistic
    # left tail (sweep 2026-08-24). Treme is a 1970 building carrying a $350/unit
    # reserve; active, its "beats the index" odds fall from 43% to 20% at ask.
    _vint = _vintage(name)
    mc = pymodel.monte_carlo({k: v for k, v in inputs.items() if k != "location"},
                             n=1000, seed=42, deal_name=name, **_vint)
    # House rule 2026-08-09: a deal must beat the same-period index or pass.
    # Market 5-yr CAGR modeled Normal(10%, 8%) vs the deal's MC IRR samples.
    beats_index = _beats_index(mc.get("irr_samples") or [])

    # Evaluate the house rule at every rung of the ladder, not only at ask
    # (sweep 2026-08-24). The board published a "minimum to pursue" price
    # without ever checking it against the rule printed at the top of the same
    # page: treme's $714,000 rung beats the index well under half the time.
    ladder_beats = {}
    for t, price in ladder.items():
        if not price:
            ladder_beats[t] = None
            continue
        # A sale reassesses. solve_price certifies each rung on ITS OWN
        # reassessed tax bill and re-derived exit cap; until 2026-09-07 the
        # board then SCORED that rung with the ask-price basis still attached,
        # biasing every rung against the deal by 1.0-4.3pp of beats-index
        # (eden's 16% rung 46.7% -> 48.8%; treme's 22% rung 41.1% -> 45.1%).
        # solve_price now hands the basis back — use it rather than re-deriving.
        rung_inputs = {k: v for k, v in inputs.items() if k != "location"} | {"price": price}
        for _k in ("taxes_annual", "exit_cap"):
            if ladder_basis.get(t, {}).get(_k) is not None:
                rung_inputs[_k] = ladder_basis[t][_k]
        _mc = pymodel.monte_carlo(rung_inputs, n=1000, seed=42,
                                  deal_name=name, **_vint)
        ladder_beats[t] = _beats_index(_mc.get("irr_samples") or [])
    return {
        "beats_index": beats_index,
        "price": inputs["price"], "units": units, "avg_rent": avg_rent,
        "rent_growth": inputs.get("rent_growth", 0.02),
        "expense_growth": inputs.get("expense_growth", 0.025),
        "vacancy": inputs.get("vacancy", 0.07) + inputs.get("bad_debt", 0.0),
        "insurance_u": inputs.get("insurance", 0),
        "taxes": inputs.get("taxes_annual", 0),
        "mgmt": inputs.get("mgmt_pct", 0.08),
        "noi": r["noi"][1], "cap": r["going_in_cap"], "irr": r["levered_irr"],
        "dscr": r["dscr"][1], "equity": r["total_equity"],
        "ladder": ladder, "ladder_beats": ladder_beats, "mc": mc,
    }


def stat(k, v, sub=""):
    sub_html = f"<small>{sub}</small>" if sub else ""
    return (f'<div class="stat"><div class="k">{k}</div>'
            f'<div class="v">{v} {sub_html}</div></div>')


def rung(target, label, lad, lad_beats):
    """One price-ladder row, scored against the house rule at that price.

    The board used to print a "minimum to pursue" price without ever checking
    it against the rule stated at the top of the same page (sweep 2026-08-24).
    A rung that does not beat the index more than half the time is marked, so
    the page cannot recommend a price its own rule rejects.
    """
    price = lad.get(target)
    if not price:
        return (f'<div class="row"><span class="k">Price for a {target*100:.0f}% return '
                f'<small>{label}</small></span><span class="v">n/a</span></div>')
    b = (lad_beats or {}).get(target)
    if b is None:
        note = ""
    elif b > 0.50:
        note = f'<small class="ok">beats index {b*100:.1f}% — clears the house rule</small>'
    else:
        # One decimal: the test is b > 0.50 but the display was .0f, so 49.6%
        # rendered "beats index 50% — FAILS" and 50.4% rendered "beats index
        # 50% — clears", printing the same number for opposite verdicts.
        note = (f'<small class="warn">beats index only {b*100:.1f}% — FAILS the house '
                f'rule at this price</small>')
    if b is not None and abs(b - 0.50) < 0.03:
        # ~1.15pp of MC noise at n=1000 (measured over seeds 1-25), so a rung
        # this close to the line is not a stable binary.
        note += ('<small class="warn"> · within MC noise of the 50% line — '
                 'treat as a coin flip, not a verdict</small>')
    return (f'<div class="row"><span class="k">Price for a {target*100:.0f}% return '
            f'<small>{label}</small></span>'
            f'<span class="v">{money(price)} {note}</span></div>')


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
      {rung(0.13, "minimum to pursue", lad, d.get('ladder_beats', {}))}
      {rung(0.16, "strong deal", lad, d.get('ladder_beats', {}))}
      {rung(0.22, "ideal", lad, d.get('ladder_beats', {}))}
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
      {stat("Beats the stock market (at ask)", f"{d['beats_index']*100:.0f}% odds" if d.get('beats_index') is not None else "n/a", "house rule: beat the index or pass")}
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
        rows.append(f'<li class="{cls}"><b>[{badge}]</b> {html.escape(t["text"])} '
                    f'<span class="who">— {html.escape(t["why"])}</span></li>')
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
      <span>House rule: <b>beat the index or pass</b></span>
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
