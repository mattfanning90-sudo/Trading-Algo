# Multi-Region Momentum Trading System

## What this is
A monthly-rebalanced 12-1 cross-sectional momentum strategy run as independent
regional sleeves — ASX (Australia), US (stocks + ETFs), FTSE (London) and TSX
(Canada), 25% each by default — with a portfolio layer that reports combined
equity in AUD. Includes a no-lookahead walk-forward backtester, persistent paper
books, an IBKR execution layer and a scheduler. Alongside it: a self-contained
**FX subsystem** (`trading_algo/forex/`) and a **Mac home screen**
(`trading_algo/home/`).

## Where things live
- **Module map:** `docs/ARCHITECTURE.md` (the single home — don't copy it).
- **Folder rules load on their own** when you work there:
  `trading_algo/forex/CLAUDE.md`, `trading_algo/dashboard/CLAUDE.md`,
  `trading_algo/home/CLAUDE.md`.
- **Every other doc:** `docs/README.md` — the catalogue, with each doc's purpose
  and whether it is current, active or an archive. Check it before reading a
  long doc.
- **The live books** run on GitHub Actions, which alone write `state/` and commit
  it to `main`. A local checkout's `state/` is usually stale; never edit it by
  hand or point a synthetic run at it.
- **The one weight function** is `strategy.compute_targets` (invariant 3); the one
  measurement layer is `metrics.py`.

## Commands
```bash
pytest -q                                           # full suite
python -m trading_algo.run_backtest [--region US] [--synthetic] [--point-in-time]
python -m trading_algo.paper_trade --account full   # daily run (all sleeves)
python -m trading_algo.engine --once --account full # one scheduler pass
python -m trading_algo.verify [--json --strict]     # audit every live book
python -m trading_algo.dashboard --account full     # terminal dashboard :8787
python -m trading_algo.home [--browser]             # Mac home screen
python -m trading_algo.forex.engine --once          # one FX cycle (all books)
```
Full list with explanations: `README.md` → Quick start; FX:
`trading_algo/forex/CLAUDE.md`.

## Invariants — do not break these
1. **No lookahead**: signals at t use data ≤ t; trades execute t+1. Any change to
   `signals.py`, `strategy.py` or `backtest.py` must preserve this.
2. **Costs always on**: never report backtest metrics without commission +
   slippage; UK stamp duty applies to FTSE buys.
3. **One weight function**: backtest and paper trading must both route through
   `strategy.compute_targets`. Do NOT add a second copy of the weight logic
   (this is what invariant #4 used to warn about — now enforced by
   `tests/test_consistency.py`).
4. **Whole shares** in paper trading; per-region commission floor respected.
5. **Synthetic-data results are pipeline tests only**; never present as performance.
6. **Each sleeve trades in its local currency**; only the portfolio/reporting
   layer converts to AUD via FX. Don't mix currencies inside a sleeve.

## Adding a region
Add one entry to `REGIONS` in `regions.py` (universe in `universes.py`, plus
index/currency/fees/calendar/routing). Flow: register → backtest → *then* fund by
adding it to `config.ALLOCATIONS`. Existing paper books keep the allocations
baked in at `--init`. The worked example (TSX) and the gate's numbers:
`docs/ARCHITECTURE.md` → "Regions: registered vs funded".

## Specs (`/spec`)
Goals and acceptance criteria for a piece of work live in
`docs/specs/<slug>.md`, all on one schema (`.claude/skills/spec/template.md`).
`/spec <topic>` drafts one collaboratively (Claude drafts, user confirms);
`/spec check <slug>` audits its acceptance criteria against the code.
`docs/specs/_example.md` is a worked example, not a real spec. Any spec that
changes stored data includes a "Migration & rollback" section.

## Environment notes
- Fresh containers do NOT ship numpy/pandas/yfinance — `pip install -r
  requirements.txt` first (a SessionStart hook in `.claude/settings.json` does
  this automatically on Claude Code web).
- Sandboxes may block outbound internet (Yahoo 403). Use `--synthetic` to
  smoke-test the pipeline offline; real backtests need network on your machine.

## Checking your own work
Everything here is an evidence claim. Before presenting a finding — in chat, a
doc, or a spec — it must survive these:

1. **Verify what a subagent reports.** A subagent's report is a claim, not a
   finding. Open the file or source and confirm it before repeating it. If you
   can't, label it unverified — never launder it into a fact.
2. **Read the file, don't grep it.** A grep returning nothing is not evidence of
   absence, especially for a doc another doc names. Absence is a finding only
   after you have read the file.
3. **A gap is not an impossibility.** "No test does X" and "no test could do X"
   are different claims. Don't upgrade one to the other because it reads better.
4. **Carry the source's caveat with its number.** If the doc that produced a
   figure calls it multiple-comparison discounted or not yet actionable, that
   travels with the figure — into summaries and option text, not just prose.
5. **Don't manufacture dependencies.** If B doesn't actually require A, say so,
   even when A-then-B is the tidier story.
6. **Quote the code you reason about.** A claim about behaviour cites file:line
   and the line itself. Paraphrase is how divergences get missed.

On substantial work, review it adversarially before presenting it: argue the
other side, then report what survives and what doesn't.

## Keeping knowledge in shape
- **One home per fact** — link to it; don't copy it. `docs/README.md` says which
  doc owns what.
- **Three layers:** this file stays short (< 150 lines) and holds only what every
  session needs; area detail goes in that folder's `CLAUDE.md`; reference goes in
  `docs/` and is listed in the catalogue.
- When you change behaviour, update the doc that owns the fact in the same change.
- Generated notes: `tools/build_obsidian_vault.py` writes several `obsidian/`
  notes — edit the generator, not the note.

## Style
Python 3.11+, pandas/numpy, type hints, small testable modules, no heavy
frameworks. Money is always in a known currency — label it.
