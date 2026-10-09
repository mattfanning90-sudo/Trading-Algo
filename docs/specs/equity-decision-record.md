---
title: Equity decision record
slug: equity-decision-record
status: draft            # draft → agreed → in-progress → done (or dropped)
created: 2026-10-09
last-updated: 2026-10-09
owner: Matt Fanning
---

# Equity decision record

Project **D0** of `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`.

## Context
Equity trades record *what* was traded — `decision`, `fill`, `entry`, `realized`,
costs — but not *why*: no momentum score, rank, volatility, trend or regime state
survives the rebalance. FX trades already record their bet in full (`agents`,
`indicators`, `regime`, `why`). The Mac dashboard's bets layer (project D) needs
"what the bet was and how it was calculated" for equity too.

The numbers already exist inside the one weight formula: `strategy.precompute`
builds a `SignalPanel` (`scores`, `vols`, `trend`, `risk_on`, `value`) and
`strategy.targets_at` reads one `.loc[asof]` row of it into weights. The live
rebalance happens at `paper_trade.py:853`
(`targets = strategy.compute_targets(prices, index_px, params, eligible=elig)`),
on prices from `data.load_region(region, cfg.START, use_cache=False)`.

**Approach (draft — confirm):**
- `targets_at` gains an optional `trace` dict that it fills as it runs (the as-of
  row values it read, the raw selection, the vol-target scale, the final weights).
  With `trace=None` — the default, and what the backtester uses — nothing changes.
- A new `trading_algo/decision_record.py` turns a filled trace into a plain,
  versioned record without calling any weight primitive, and owns reading/writing
  the record files.
- **Recorded:** `paper_trade` passes a trace at each rebalance and appends one record
  per rebalanced sleeve to `state/decisions_<account>.jsonl` (append-only).
- **Reconstructed:** `python -m trading_algo.decision_record backfill --account <a>`
  replays each past rebalance through `compute_targets` on prices cut off at that
  date, and writes `state/decisions_<account>.reconstructed.jsonl` (regenerated in
  full each run), each record reconciled against the trades the book actually made.

## Goals
- **G-1**: Every equity rebalance from deployment onward leaves a record of each
  eligible candidate's score, rank, volatility, trend and eligibility, the regime
  state, the parameters in force, and the resulting target weights — including
  rebalances that end flat (cash), with the reason the run already prints.
- **G-2**: Records come from the one weight formula itself: recorded final weights
  equal the weights the book acted on, bit for bit.
- **G-3**: Trading is unchanged: identical targets, trades, costs and book state.
- **G-4**: Every past rebalance of the four live equity books (since each book
  began) has a reconstructed record, clearly labelled as reconstructed, with a
  reconciliation against the trades actually made.
- **G-5**: Rollout and rollback are safe: pre-deploy backup, documented rollback,
  rehearsed restore.

## Non-goals
- **NG-1**: Making reconstructed records match history exactly. Today's prices are
  re-adjusted for dividends and splits and today's code/params may differ from the
  code that ran then; differences are **measured and reported**, not fixed.
- **NG-2**: FX books — they already record agents, indicators, regime and `why`.
- **NG-3**: Bet horizons, outcomes and right/wrong labels (project D).
- **NG-4**: Any change to selection, sizing, costs or rebalance timing.
- **NG-5**: Records from the backtester (its `targets_at` calls keep `trace=None`).
- **NG-6**: Displaying records (projects B and C).

## Acceptance criteria
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | With `trace=None`, `targets_at` output is identical to the pre-change output for all four book shapes: long-only with regime gate, regime off (`ultra`), long/short (`experimental`), `use_value` on. | `tests/test_decision_record.py::test_trace_off_is_bit_identical` | ☐ |
| AC-2  | With a trace, the record's final weights equal `targets_at`'s returned weights exactly, for the same four shapes. | `tests/test_decision_record.py::test_record_weights_equal_targets` | ☐ |
| AC-3  | A synthetic paper rebalance appends exactly one record per rebalanced sleeve to `decisions_<account>.jsonl`, containing: `v`, `source: "recorded"`, account, region, run date, as-of bar date, status, full params, `risk_on`, gross, and for **every eligible candidate** `ticker`, `score`, `rank_pct`, `vol`, `trend_ok`, `eligible`, `raw_weight`, `final_weight` (+ `value`/`rank_score` when `use_value`). | `tests/test_decision_record.py::test_paper_rebalance_writes_record` | ☐ |
| AC-4  | A rebalance that ends flat (regime risk-off; nothing eligible) is recorded with an empty target set and the reason string `_empty_target_reason` returns. | `tests/test_decision_record.py::test_flat_rebalance_recorded_with_reason` | ☐ |
| AC-5  | Held, idle, halted and below-minimum days write no record. | `tests/test_decision_record.py::test_no_record_without_a_decision` | ☐ |
| AC-6  | `decisions_<account>.jsonl` is append-only: no existing line is ever rewritten; a truncated final line (simulated crash) is skipped by the reader and does not block the next append. | `tests/test_decision_record.py::test_append_only_and_crash_tolerant` | ☐ |
| AC-7  | `decision_record` calls no weight primitive (`select_portfolio`, `select_long_short`, `vol_target`, `_apply_capacity`). | `tests/test_consistency.py` (guard extended to the new module) | ☐ |
| AC-8  | Running the same synthetic book with and without recording produces identical trades, equity history and book state. | `tests/test_decision_record.py::test_recording_does_not_change_trading` | ☐ |
| AC-9  | Every number in every record is finite or `null` — never `NaN`/`Infinity`. | `tests/test_decision_record.py::test_record_is_strict_json` | ☐ |
| AC-10 | One record for a 126-name US sleeve is ≤ 60 KB (draft — confirm). | `tests/test_decision_record.py::test_record_size_budget` | ☐ |
| AC-11 | **Backfill has no lookahead:** appending later price rows to the input does not change any reconstructed record. | `tests/test_decision_record.py::test_backfill_ignores_future_prices` | ☐ |
| AC-12 | **Backfill never writes book state:** every `paper_state_*.json` and `paper_books.db` is byte-identical before and after a backfill run. | `tests/test_decision_record.py::test_backfill_is_read_only_on_books` | ☐ |
| AC-13 | Each reconstructed record carries `source: "reconstructed"`, the code commit it was built with, the price source, `fetched_at`, how its date was found (`trades` or `calendar`), and a reconciliation: names it would hold vs names the book bought/sold/held that day (`matched`, `missing`, `extra`). | `tests/test_decision_record.py::test_reconstructed_record_provenance` | ☐ |
| AC-14 | Backfill is repeatable: two runs on the same prices produce byte-identical files. | `tests/test_decision_record.py::test_backfill_is_deterministic` | ☐ |
| AC-15 | Backfill run on the four live equity books (fresh copy of `origin/main` `state/`) prints, per book and sleeve, the number of rebalances reconstructed and the reconciliation match rate; Matt reviews it before the file is committed. | Manual check — commands in Verification plan | ☐ |
| AC-16 | Rollback rehearsal: with record files present, the previous `main` commit runs a synthetic day and saves every equity book without error. | Manual check — commands in Verification plan | ☐ |
| AC-17 | Pre-deploy backup of every live equity book (`paper_state_*.json` + `paper_books.db`) taken, and a restore rehearsed from it. | Manual check — commands in Verification plan | ☐ |
| AC-18 | Full suite green, and the live-book audit reports no new findings. | `pytest -q`; `python -m trading_algo.verify` | ☐ |

## Constraints & invariants
- **#1 No lookahead** — touched, preserved.
  - Recorded: the trace holds only values `targets_at` already read via
    `.loc[asof]`; each record stores the as-of bar date beside the run date.
  - Reconstructed: prices and index are cut at the decision's as-of bar before
    `compute_targets` runs; AC-11 proves later rows cannot leak in.
- **#2 Costs always on** — not touched (no change to trading; backfill does not
  simulate fills).
- **#3 One weight function** — touched, preserved. The trace is filled *inside*
  `targets_at`; backfill calls `compute_targets`; `decision_record` only reshapes;
  the consistency guard is extended to it (AC-7); weights proven unchanged (AC-1, AC-8).
- **#4 Whole shares** — not touched. Records hold target *weights*; whole-share
  orders stay in the trade ledger.
- **#5 Synthetic results are pipeline tests only** — synthetic data only exercises
  the pipeline; reconciliation rates come from real data (AC-15).
- **#6 Local currency per sleeve** — preserved. Scores, ranks, vols and weights are
  unitless; any money value in a record carries its currency code.
- Tests isolate `MOMENTUM_STATE_DIR` to a temp directory — a test or synthetic run
  must never write into live books.
- Records are readable without importing the trading code (plain JSON lines,
  self-describing via `v` and full params).
- No new dependency.

## Migration & rollback
- **Book state format: unchanged.** Records live in new files; no
  `STATE_SCHEMA_VERSION` bump, no `migrate_state` change.
- **Record format:** each line carries `"v": 1`; a future change bumps `v` and
  readers branch on it. The recorded file is never rewritten; the reconstructed
  file is regenerable from code + prices, so it can always be rebuilt or deleted.
- **Backup before deploy:** export `state/` from `origin/main` to
  `~/Library/Application Support/Momentum/backups/<timestamp>-pre-d0/` (git history
  is a second copy).
- **Rollback:** revert the D0 merge commit on `main`. The previous code never reads
  the new files (AC-16); they remain as inert files and can be deleted.
- **Restore:** copy the book files back from the backup and run
  `python -m trading_algo.verify` — rehearsed once before merge (AC-17).

## Verification plan
```bash
pytest -q tests/test_decision_record.py tests/test_consistency.py
pytest -q

# synthetic smoke run, isolated from live books
export MOMENTUM_STATE_DIR="$(mktemp -d)"
python -m trading_algo.paper_trade --account dr_smoke --init --capital 100000 --synthetic
python -m trading_algo.paper_trade --account dr_smoke --synthetic --force-rebalance
tail -c 400 "$MOMENTUM_STATE_DIR/decisions_dr_smoke.jsonl"

# AC-15 backfill on a fresh copy of the live books (needs network for prices)
LIVE="$(mktemp -d)" && git fetch -q origin main && git archive origin/main state/ | tar -x -C "$LIVE"
for a in full small ultra experimental; do
  MOMENTUM_STATE_DIR="$LIVE/state" python -m trading_algo.decision_record backfill --account "$a"
done

# AC-16 rollback rehearsal: previous main against a copy carrying record files
PREV="$(git rev-parse origin/main)"          # capture BEFORE D0 is merged
PREV_DIR="$(mktemp -d)/prev" && git worktree add "$PREV_DIR" "$PREV"
(cd "$PREV_DIR" && MOMENTUM_STATE_DIR="$MOMENTUM_STATE_DIR" \
   python -m trading_algo.paper_trade --account dr_smoke --synthetic)
git worktree remove "$PREV_DIR"

# AC-17 backup + restore rehearsal
B="$HOME/Library/Application Support/Momentum/backups/$(date +%Y%m%d-%H%M)-pre-d0"
mkdir -p "$B" && git archive origin/main state/ | tar -x -C "$B"
python -m trading_algo.verify
```

## Open questions
- **Q-4** Params snapshot: the full `StrategyParams` as a dict in every record, so a
  record never depends on code to interpret it (draft — confirm).
- **Q-5** How the reconstructed files reach `main`: run the backfill locally (needs
  network), Matt reviews the AC-15 report, and the files ship in the D0 pull request
  (draft — confirm).
- **Q-6** How backfill finds past rebalance dates (draft — confirm): every date the
  region has trades, plus the first run date of each calendar month (when
  `_should_rebalance` fires). Halt periods aren't stored historically, so a calendar
  date can land on a halted day; reconciliation will show it as "no trades", and the
  record keeps how its date was found.

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-09 | D0 is built first, before the mirror and app (records must start accumulating). | Matt |
| 2026-10-09 | `sleeve_history` values are in base currency (A$): `paper_trade.py` writes `round(v[2], 2)` where `v = (eq_local, currency, eq_base)`. Closes design Q-2. | Claude (verified in code) |
| 2026-10-09 | Q-1 → records live in a separate append-only file per book, `state/decisions_<account>.jsonl`; book state format unchanged. | Matt |
| 2026-10-09 | Q-2 → each record covers every eligible candidate (full cross-section). | Matt |
| 2026-10-09 | Q-3 → reconstruct past rebalances now, in D0 (was proposed for project D). | Matt |
