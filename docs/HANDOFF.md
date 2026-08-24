# Handoff / pick-up-here — 2026-08-24

Written because Anthony is moving to a different account. **Everything in
`~/.claude` auto-memory stays behind** — it is machine- and folder-scoped. This
repo is the only thing that travels. Read `CLAUDE.md` first (it now carries the
working agreements, company identity and buy-box rules that used to live in
memory), then this file for live state.

## Where the pipeline actually stands

Numbers below come from the live board, regenerated after the 2026-08-24 weekly
sweep fixed a solver bug that had been under-pricing every rung. **Any figure
you find in an older note or memory predates that fix — do not trust it.**

### Eden Church MHP — 18 pads, Denham Springs · PRIORITY 1, still live
Ask $1,699,000.

| Rung | Price | House rule |
|---|---|---|
| 13% | $1,559,000 | beats index 39% — **FAILS** |
| 16% | $1,443,000 | beats index 46% — **FAILS** |
| 22% | $1,251,000 | beats index 60% — **clears** |

Read that carefully: **only the 22% rung clears the house rule.** The deal is
live because the carry price (~$1,250,000) lands on that rung — which is exactly
why seller carry is the whole strategy here, not a nice-to-have. Without carry,
his $300K only closes ~$1,100,000. The gate is unchanged: a bindable insurance
quote at or under ~$1,600/unit.

### 1429 Governor Nicholls — 8 units, Tremé · PRIORITY 2, now failing
Ask $849,000. Every rung fails the house rule: 13% $714,000 (33%), 16% $663,000
(36%), 22% $579,000 (43%). All below the 46% at which Baker was passed, and it
holds even assuming a gut renovation. Stage is still "underwriting" pending
Anthony's call, but **the numbers say pass on the estimated basis.** Seller
documents were never received — the Hernandez request has never been sent.

### Baker Trails · PASSED 2026-08-17
OM ingested. 8 of 12 occupied with four long-term vacancies, built 1984. With
vintage capex active it beats the index 46% at $528K and 34% at $423K. Price was
never the binding problem, so a price cut does not revive it — retire the Crexi
alert. Full reasoning in `deal-intake/baker-trails/IC_MEMO_2026-08-17.md`.

### Everything else
cannon-rd, covington-2nd, weber-city-mhp, hwy42-mhp, central-city-2nd — dead.
Covington's revive band is real though: works at $907K/$841K/$733K with equity
$203-252K, which fits the $300K. It stays dead at the $1.25M ask and revives
only on a cut toward ~$900K.

## What is blocking everything

**Seven outreach drafts have been sitting unsent in Gmail.** This is the
pipeline's actual bottleneck — not tooling, not analysis. Vercher (Eden, the
top deal) has been waiting since 2026-08-03. Also queued: Rider, Apartment
Guard, Hernandez, Landreneau, Stirling, Beau Box.

**Check before sending the Vercher draft:** it contains the line *"I'm back in
the country next week."* A cloud sweep wrote that sentence. If it is not true it
is a fabricated excuse going to the broker on the top deal.

**The insurance quote gates more than Eden.** Per the metro/inland finding, one
bindable quote unblocks the entire New Orleans box. Apartment Guard has been
silent since 2026-08-05. Alternates worth trying in parallel: RLI, Palomar,
AmTrust, or any Louisiana-licensed specialty E&S market.

## System state

- **Automation works unattended.** Daily scan + weekly sweep run in the cloud,
  push to `main`, triage listings and self-correct. Confirmed 2026-08-19 after
  the Claude GitHub App was installed on `ck5o4` (it had been authorized but
  never installed, which is why reads worked and writes 403'd for two weeks).
- **Cloud containers cannot reach FRED or the comp sites** (egress proxy 403), so
  `rates.py` and `marketwatch.py` must be run locally. Both are clean locally.
- **Board artifact:** https://claude.ai/code/artifact/2b362f80-0ceb-4aaf-8d5b-41f212b377d0
  The older `7ff847d5` URL is dead. `tools/board.py` points at the live one.
- Rates as of 2026-08-19: WSJ Prime 6.75%, 10yr 4.71%, SOFR 3.65%. No drift.

## Two things to verify on arrival

1. **Is `ck5o4/multifamily` private?** It was PUBLIC on 2026-08-17 — the only
   public repo in the account — with walk-away prices, capital position, broker
   contacts and negotiating strategy in it. It had 0 forks/stars/watchers at the
   time. If still public: Settings → General → Danger Zone → Change visibility.
2. **Google security alert 2026-08-18: recovery phone changed** on
   ridgebackpeak@gmail.com. Probably Anthony, but it was never confirmed, and
   that account now holds real deal flow.

## Method notes worth keeping

- **Always pass `--year-built`.** A deterministic ladder can rise while the
  risk-adjusted answer falls. On Baker the honest stabilized 13% price ($582K)
  came out HIGHER than the earlier wrong figure ($528K) while the deal got
  worse. Never quote a broker a price off a solve alone — run the MC with
  vintage first.
- **On GitHub, "authorized" and "installed" are different things** with separate
  tabs on the Applications page. Check both before concluding anything about an
  app's repo access.
- **Read the alert email bodies.** Crexi listing alerts carry unit counts in the
  body even though every digest quoted only the address. Two "live" candidates
  died on scale from the body alone, without touching Crexi.
