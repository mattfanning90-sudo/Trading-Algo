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
"what the bet was and how it was calculated" for equity too, and every month
without a record is a month that can only ever be *reconstructed* from today's
(re-adjusted) prices, never *recorded*.

The numbers already exist inside the one weight formula: `strategy.precompute`
builds a `SignalPanel` (`scores`, `vols`, `trend`, `risk_on`, `value`) and
`strategy.targets_at` reads one `.loc[asof]` row of it into weights. The rebalance
happens at `paper_trade.py:853`
(`targets = strategy.compute_targets(prices, index_px, params, eligible=elig)`).

**Proposed approach (draft — confirm):** `targets_at` gains an optional `trace`
dict that it fills as it runs (the as-of row values it read, the raw selection,
the vol-target scale, the final weights). With `trace=None` — the default, and
what the backtester uses — nothing changes. A new `decision_record` module turns a
filled trace into a plain, versioned record without calling any weight primitive.
`paper_trade` passes a trace at each rebalance and appends the record to the
storage chosen in Q-1.

## Goals
- **G-1**: Every equity rebalance from deployment onward leaves a record of each
  candidate's score, rank, volatility, trend and eligibility, the regime state, the
  parameters in force, and the resulting target weights — including rebalances
  that end flat (cash), with the same reason the run already prints.
- **G-2**: The record comes from the one weight formula itself: its final weights
  equal the weights the book acted on, bit for bit.
- **G-3**: Trading is unchanged: identical targets, trades, costs and state, apart
  from the record itself.
- **G-4**: Records are immutable and versioned, readable by the mirror (project A)
  and bets layer (project D) without importing the trading code.
- **G-5**: Rollout and rollback are safe: pre-deploy backup, documented rollback,
  rehearsed restore.

## Non-goals
- **NG-1**: Reconstructing past rebalances. That moves to project D, where the
  mirror's price history lives, and is labelled "reconstructed" there (draft —
  confirm, Q-3).
- **NG-2**: FX books — they already record agents, indicators, regime and `why`
  per trade.
- **NG-3**: Bet horizons, outcomes and right/wrong labels (project D).
- **NG-4**: Any change to selection, sizing, costs or rebalance timing.
- **NG-5**: Records from the backtester (its `targets_at` calls keep `trace=None`).
- **NG-6**: Displaying records (projects B and C).

## Acceptance criteria
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | With `trace=None`, `targets_at` output is identical to the pre-change output for all four book shapes: long-only with regime gate, regime off (`ultra`), long/short (`experimental`), `use_value` on. | `tests/test_decision_record.py::test_trace_off_is_bit_identical` | ☐ |
| AC-2  | With a trace, the record's final weights equal `targets_at`'s returned weights exactly, for the same four shapes. | `tests/test_decision_record.py::test_record_weights_equal_targets` | ☐ |
| AC-3  | A synthetic paper rebalance writes exactly one record per rebalanced sleeve, containing: record version, account, region, run date, as-of bar date, status, params in force, `risk_on`, gross, and per candidate `ticker`, `score`, `rank_pct`, `vol`, `trend_ok`, `eligible`, `raw_weight`, `final_weight` (+ `value` / `rank_score` when `use_value`). | `tests/test_decision_record.py::test_paper_rebalance_writes_record` | ☐ |
| AC-4  | A rebalance that ends flat (regime risk-off; nothing eligible) is recorded with an empty target set and the same reason string `_empty_target_reason` returns. | `tests/test_decision_record.py::test_flat_rebalance_recorded_with_reason` | ☐ |
| AC-5  | Held, idle, halted and below-minimum days write no record. | `tests/test_decision_record.py::test_no_record_without_a_decision` | ☐ |
| AC-6  | Records are append-only: no existing record is ever rewritten; a truncated final record (simulated crash) is skipped by the reader and does not block the next write. | `tests/test_decision_record.py::test_append_only_and_crash_tolerant` | ☐ |
| AC-7  | The record builder calls no weight primitive (`select_portfolio`, `select_long_short`, `vol_target`, `_apply_capacity`). | `tests/test_consistency.py` (guard extended to the new module) | ☐ |
| AC-8  | Running the same synthetic book with and without recording produces identical trades, equity history and book state (excluding the record). | `tests/test_decision_record.py::test_recording_does_not_change_trading` | ☐ |
| AC-9  | Every number in a record is finite or `null` — never `NaN`/`Infinity` (bare `NaN` is not JSON). | `tests/test_decision_record.py::test_record_is_strict_json` | ☐ |
| AC-10 | One rebalance record for a 126-name US sleeve is ≤ 60 KB (draft — confirm). | `tests/test_decision_record.py::test_record_size_budget` | ☐ |
| AC-11 | Rollback rehearsal: with records present, the previous `main` commit loads, runs a synthetic day and saves every equity book without error. | Manual check — commands in Verification plan | ☐ |
| AC-12 | Pre-deploy backup taken of every live equity book (`paper_state_*.json` + `paper_books.db`) and a restore rehearsed from it. | Manual check — commands in Verification plan | ☐ |
| AC-13 | Full suite green and the live-book audit reports no new findings. | `pytest -q`; `python -m trading_algo.verify` | ☐ |

## Constraints & invariants
- **#1 No lookahead** — touched, preserved. The trace records only values
  `targets_at` already read via `.loc[asof]`; the record stores the as-of bar date
  next to the run date so a reader can never mistake when it was knowable.
- **#2 Costs always on** — not touched (no change to trading).
- **#3 One weight function** — touched, preserved. The trace is filled *inside*
  `targets_at`; the builder only reshapes it; the consistency guard is extended to
  the new module (AC-7); weights are proven unchanged (AC-1, AC-8).
- **#4 Whole shares** — not touched. The record holds target *weights*; the
  whole-share orders stay in the trade ledger.
- **#5 Synthetic results are pipeline tests only** — tests use synthetic data purely
  to exercise the pipeline; no performance is reported.
- **#6 Local currency per sleeve** — preserved. Scores, ranks, vols and weights are
  unitless; any money value added to a record carries its currency code.
- Tests must isolate `MOMENTUM_STATE_DIR` to a temp directory — a synthetic run must
  never write into live books.
- No new dependency.

## Migration & rollback
- **New stored data, nothing existing changes shape** if Q-1 resolves to the
  separate file (recommended). Each record carries `"v": 1`; a future change bumps
  `v` and readers branch on it.
- **If Q-1 resolves to inside the book state instead:** add the key through
  `state_schema.migrate_state` (additive), bump `STATE_SCHEMA_VERSION` 2 → 3, and
  extend `validate_state` to check the new key only when present. Measured
  behaviour of the current code: when `schema_version != STATE_SCHEMA_VERSION` it
  only adds missing keys and re-stamps, so the previous release would keep (not
  drop) the new key on rollback.
- **Backup before deploy:** copy every live `paper_state_*.json` and
  `paper_books.db` from `origin/main` to a dated folder under
  `~/Library/Application Support/Momentum/backups/` (git history is a second copy).
- **Rollback:** revert the merge commit on `main`; the previous code ignores the
  record (AC-11). Records already written stay as an inert file (or inert key).
- **Restore:** replace the book files from the backup and run
  `python -m trading_algo.verify` — rehearsed once before merge (AC-12).

## Verification plan
```bash
pytest -q tests/test_decision_record.py tests/test_consistency.py
pytest -q

# synthetic smoke run, isolated from live books
export MOMENTUM_STATE_DIR="$(mktemp -d)"
python -m trading_algo.paper_trade --account dr_smoke --init --capital 100000 --synthetic
python -m trading_algo.paper_trade --account dr_smoke --synthetic --force-rebalance
ls -la "$MOMENTUM_STATE_DIR"

# AC-11 rollback rehearsal: previous main against a copy carrying records
PREV="$(git rev-parse origin/main)"          # capture BEFORE D0 is merged
PREV_DIR="$(mktemp -d)/prev" && git worktree add "$PREV_DIR" "$PREV"
(cd "$PREV_DIR" && MOMENTUM_STATE_DIR="$MOMENTUM_STATE_DIR" \
   python -m trading_algo.paper_trade --account dr_smoke --synthetic)
git worktree remove "$PREV_DIR"

# AC-12 backup + restore rehearsal
B="$HOME/Library/Application Support/Momentum/backups/$(date +%Y%m%d-%H%M)-pre-d0"
mkdir -p "$B" && git archive origin/main state/ | tar -x -C "$B"
python -m trading_algo.verify
```

## Open questions
- **Q-1** `TBD(user)` Where do records live? **Recommended:** a separate append-only
  file per book, `state/decisions_<account>.jsonl` — book state format untouched, git
  diffs are appended lines only, and the CI's existing `git add -A state/` commits it.
  Alternative: a `decision_log` list inside the book state (schema v3).
- **Q-2** `TBD(user)` What does each record cover? **Recommended:** every eligible
  candidate (US 126, FTSE 70, ASX 56, TSX 55 names) — the full cross-section is what
  lets project D later test whether scores predicted returns across all names, not
  just the ones bought. Estimated ~15 KB per sleeve per rebalance, ~0.5 MB a year per
  book. Alternative: only names selected, previously held, and the next best
  `2 × top_n` (near misses).
- **Q-3** Reconstructing past rebalances: in project D, not D0 (draft — confirm).
- **Q-4** Params snapshot: the full `StrategyParams` as a dict in every record, so a
  record never depends on code to interpret it (draft — confirm).

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-09 | D0 is built first, before the mirror and app (records must start accumulating). | Matt |
| 2026-10-09 | `sleeve_history` values are in base currency (A$): `paper_trade.py` writes `round(v[2], 2)` where `v = (eq_local, currency, eq_base)`. Closes design Q-2. | Claude (verified in code) |
