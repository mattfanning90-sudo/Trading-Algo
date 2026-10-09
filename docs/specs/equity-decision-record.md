---
title: Equity decision record
slug: equity-decision-record
status: draft            # draft → agreed → in-progress → done (or dropped)
created: 2026-10-09
last-updated: 2026-10-09
owner: Matt Fanning
---

# Equity decision record

Project **D0** of `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md` (v2),
split into **D0a — recording** and **D0b — backfill**.

**Blocked:** D0a touches `paper_trade.py:814-873`, the block measurement-truth
**Task 10** rewrites (decision date split from fill date via
`sleeve["pending_target"]`). D0a is built on top of Task 10 and this spec is
re-agreed once Task 10 has landed. D0b runs later, with project D.

## Context
Equity trades record *what* was traded — prices and costs — but not *why*. FX
trades already record their bet (`agents`, `indicators`, `regime`, `why`). Two
facts from the 2026-10-09 expert review shape this spec:

1. **The weight formula's output is not the bet.** `strategy.targets_at` produces
   target weights, then `rebalance_sleeve` reshapes them: micro mode
   (`paper_trade.py:552-559`), long/short lot fitting (`fit_long_short_to_lots`),
   whole-share rounding (`int((equity * w) / price)`), frozen names and dust trades.
   EXPERIMENTAL is configured for 6 long + 6 short and holds 10 positions; SMALL's
   1 Oct rebalance produced targets and traded nothing. A record must carry both the
   **signal** and the **execution**.
2. **The run fails unsafe in CI.** The account loop runs under `bash -e`, and "Commit
   updated paper state" has no `if: always()` (`paper-trade.yml`), while
   `engine.run_once` has no error handling. A recorder exception would cost all four
   equity books their day, and repeat daily because the month stamp is never saved.

**Approach (draft — confirm):**
- `strategy.targets_at` (and `compute_targets`, passing it through) gains an optional
  write-only `trace` dict: the as-of row values it read, the raw selection, the vol
  scale (per leg for long/short — a sum ratio is undefined when the book nets to ~0),
  and the final weights. `trace=None` is the default and what the backtester uses.
- `signals.passes_filters(...)` is extracted from `select_portfolio` and called by it,
  so "why wasn't this name picked" has one definition.
- `rebalance_sleeve` returns an `execution` dict (existing callers ignore it): mode
  (`normal` / `micro` / `ls-fitted` / `ls-flattened` / `no-affordable`), sized weights,
  desired shares, frozen and dust skips, and the reason for each divergence.
- New `trading_algo/decision_record.py` builds the record from trace + execution
  (calling no weight primitive), and owns reading and writing the files.
- Records are buffered during the run and appended **after `save_state`**, inside the
  account lock, wrapped so any failure alerts and carries on.

## Goals
- **G-1**: Every equity rebalance after Task 10 leaves a two-layer record — the signal
  (every candidate in the universe, with score, rank, vol, trend, data-quality and
  filter status, raw and final weights, regime, params) and the execution (how and why
  the traded book differs from the targets). Halt liquidations are recorded as their
  own kind.
- **G-2**: The signal layer comes from the one weight formula itself (its final weights
  equal `targets_at`'s output bit for bit); the execution layer accounts for every
  difference between targets and trades.
- **G-3**: Trading is unchanged, and recording can never break a run.
- **G-4** (D0b): Every past rebalance of the four equity books is reconstructed,
  labelled as reconstructed, replayed on the code that ran it, and validated against
  what the book actually opened.
- **G-5**: Rollout and rollback are safe and rehearsed.

## Non-goals
- **NG-1**: Making reconstructed records match history exactly; differences are
  measured and classified, not hidden.
- **NG-2**: FX books (already recorded per trade).
- **NG-3**: Bet horizons, outcomes and labels (project D).
- **NG-4**: Any change to selection, sizing, costs or timing. (The SMALL micro-mode bug
  is fixed separately, before this.)
- **NG-5**: Records from the backtester.
- **NG-6**: Displaying records (projects B and C).
- **NG-7**: The `use_value` shape — no live book uses it (`config.py`
  `use_value: bool = False`, no override).

## Acceptance criteria

### D0a — recording
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | With `trace=None`, `targets_at` and `compute_targets` outputs are identical to pre-change for the three live shapes: long-only with regime gate, regime off (`ultra`), long/short (`experimental`). | `tests/test_decision_record.py::test_trace_off_is_bit_identical` | ☐ |
| AC-2  | With a trace, the signal layer's final weights equal the returned weights exactly, for the same three shapes. | `…::test_signal_weights_equal_targets` | ☐ |
| AC-3  | `select_portfolio` selects through `signals.passes_filters`; selection is unchanged. | `…::test_passes_filters_is_the_selection_rule` + existing suite | ☐ |
| AC-4  | A rebalance record contains: `v`, `kind: "rebalance"`, `source: "recorded"`, `data_source`, run id (`GITHUB_RUN_ID` when present), account, region, decision date and fill date (Task 10), as-of bar, status, full params, `risk_on`; per candidate across the **whole universe** (frozen names included with their data-quality reason): `ticker`, `score`, `rank_pct`, `vol`, `trend_ok`, `dq_ok`, `dq_reason`, `passes_filters`, `raw_weight`, `final_weight`; and the execution layer (mode, sized weights, desired shares, frozen/dust skips, reasons). | `…::test_rebalance_record_contents` | ☐ |
| AC-5  | Execution explains divergence: for a micro-mode sleeve and a long/short sleeve, every target name absent from the traded book carries a reason. | `…::test_execution_explains_every_divergence` | ☐ |
| AC-6  | A flat rebalance (regime risk-off; nothing eligible; below minimum size) is recorded with its reason; a halt liquidation is recorded as `kind: "liquidation"`, `reason: "halted"`; held and idle days write nothing. | `…::test_flat_and_liquidation_records`, `…::test_no_record_without_a_decision` | ☐ |
| AC-7  | **Fail-safe:** with the writer monkeypatched to raise, trades, book state and exit code are identical to a run with recording off, and one alert is sent via `notifications.notify`. | `…::test_recorder_failure_never_breaks_the_run` | ☐ |
| AC-8  | Records are appended only after `save_state` succeeds, inside the account lock; a truncated final line is skipped by the reader, the next append starts on a new line, and the next record reads back. | `…::test_append_after_save_and_crash_tolerant` | ☐ |
| AC-9  | Readers de-duplicate on (account, region, decision date, run id), last wins. | `…::test_reader_dedupes` | ☐ |
| AC-10 | A synthetic run never appends to a live book's file: records with `data_source: "synthetic"` go to `decisions_<account>.synthetic.jsonl`. | `…::test_synthetic_records_quarantined` | ☐ |
| AC-11 | `decision_record` calls no weight primitive. | `tests/test_consistency.py` (guard extended; spy signature updated for `trace`) | ☐ |
| AC-12 | Recording on vs off produces identical trades, equity history and book state. | `…::test_recording_does_not_change_trading` | ☐ |
| AC-13 | Every number in a record is finite or `null`. | `…::test_record_is_strict_json` | ☐ |
| AC-14 | One record for a 126-name US sleeve is ≤ 60 KB (draft — confirm). | `…::test_record_size_budget` | ☐ |
| AC-15 | The live-book audit flags a rebalance with no matching record (WARN; halt liquidations exempt). | `tests/test_verify.py::test_rebalance_without_record` | ☐ |
| AC-16 | `.gitattributes` `state/decisions_*.jsonl merge=union` keeps both sides' lines under the bots' `git rebase -X theirs` (throwaway repo: two branches append, rebase, both lines survive). | `tests/test_decision_record_merge.py` | ☐ |
| AC-17 | Real-data dry run before merge: on a copy of `origin/main` `state/`, `--force-rebalance` for full/small/ultra/experimental with recording on and off; the books are identical and every record validates. | Manual — Verification plan | ☐ |
| AC-18 | Rollback rehearsal: with record files present, the previous `main` commit runs a synthetic day and saves every equity book. | Manual — Verification plan | ☐ |
| AC-19 | Restore runbook rehearsed against a local bare remote, including a simulated bot push during the restore. | Manual — Verification plan | ☐ |
| AC-20 | Full suite green; the live-book audit reports no new findings. | `pytest -q`; `python -m trading_algo.verify` | ☐ |

### D0b — backfill
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-21 | Decision dates come from the git history of each book: per commit, `last_rebalance_date`, `last_status`, `last_flat_reason`, `risk_halted`. Halt liquidations and halted days are not treated as decisions. Each record says how its date was found. On FULL this yields 10–11 Jun (opening), 1 Jul, 3 Aug, 16 Sep (resume) and 6 Oct — not 27 Aug (liquidation). | `…::test_backfill_dates_from_history` (fixture from real commits) | ☐ |
| AC-22 | Each decision is replayed on the code at the commit that made it, with that commit's as-of bars; adding later price rows changes nothing (no lookahead). | `…::test_backfill_ignores_future_prices` | ☐ |
| AC-23 | Books are read read-only (JSON, or SQLite with `?mode=ro`); every book file is byte-identical afterwards; no alert is ever sent (notifications stubbed). | `…::test_backfill_is_read_only_and_silent` | ☐ |
| AC-24 | Validity headline is **precision**: every name the book opened on that date is in the reconstructed targets; missing names are classified as sizing, frozen or unexplained. | `…::test_backfill_precision_and_classification` | ☐ |
| AC-25 | Provenance on each record: `source: "reconstructed"`, code commit, price source, `fetched_at`, universe hash, params hash. | `…::test_reconstructed_record_provenance` | ☐ |
| AC-26 | Two runs on the same prices produce byte-identical files. | `…::test_backfill_is_deterministic` | ☐ |
| AC-27 | The backfill report (per book and sleeve: decisions found, precision, classified misses) is reviewed by Matt before any file is committed. | Manual — Verification plan | ☐ |

## Constraints & invariants
- **#1 No lookahead** — preserved. The trace holds only `.loc[asof]` values; records
  carry decision date, fill date and as-of bar. Backfill cuts prices at the as-of bar
  (AC-22).
- **#2 Costs always on** — not touched.
- **#3 One weight function** — preserved. The trace is filled inside `targets_at`;
  `passes_filters` is extracted *from* `select_portfolio`, not duplicated; the builder
  calls no primitive (AC-11); weights proven unchanged (AC-1, AC-12).
- **#4 Whole shares** — not touched; the execution layer *records* the whole-share
  outcome, it doesn't change it.
- **#5 Synthetic results are pipeline tests only** — and synthetic records are
  quarantined (AC-10).
- **#6 Local currency per sleeve** — preserved; scores, ranks, vols and weights are
  unitless; desired shares and any money carry the sleeve's currency.
- Recording must never break the run (AC-7); tests isolate `MOMENTUM_STATE_DIR`.
- No new dependency.

## Migration & rollback
- **Book state format unchanged.** Records live in new files with a per-line `"v": 1`.
  Existing readers only glob `paper_state_*.json` / `fx_state_*.json`, so old code never
  sees them.
- **Merge safety:** `.gitattributes` union merge for `state/decisions_*.jsonl` (AC-16);
  runbook rule — never edit or delete decision files on `main` while `paper-trade` is
  enabled.
- **Backup:** git is the backup. Before merge, record the `origin/main` SHA and sha256
  checksums of every equity book file (`paper_state_*.json`, `paper_books.db`) in a
  manifest under `~/Library/Application Support/Momentum/backups/books/`.
- **Rollback:** revert the D0a merge. The previous code ignores the files (AC-18).
  Rollback is complete when the first `paper-trade` run after the revert finishes
  green. Re-landing later is a revert of the revert.
- **Restore (book state):** `gh workflow disable paper-trade.yml` → wait for in-flight
  runs → `git checkout <sha> -- state/paper_state_*.json state/paper_books.db` → delete
  `state/*.db-wal state/*.db-shm` → `MOMENTUM_STATE_DIR=<restored dir> python -m
  trading_algo.verify` → compare to the manifest → commit → re-enable → dispatch a run.
  Rehearsed (AC-19).

## Verification plan
```bash
pytest -q tests/test_decision_record.py tests/test_decision_record_merge.py tests/test_consistency.py tests/test_verify.py
pytest -q

# AC-17 real-data dry run (recording on vs off), isolated from live books
ON="$(mktemp -d)"; OFF="$(mktemp -d)"
git fetch -q origin main
for d in "$ON" "$OFF"; do git archive origin/main state/ | tar -x -C "$d"; done
for a in full small ultra experimental; do
  MOMENTUM_STATE_DIR="$ON/state"  python -m trading_algo.paper_trade --account "$a" --force-rebalance
  MOMENTUM_STATE_DIR="$OFF/state" DECISION_RECORD=off python -m trading_algo.paper_trade --account "$a" --force-rebalance
done
diff <(cd "$ON/state" && cat paper_state_*.json) <(cd "$OFF/state" && cat paper_state_*.json)

# AC-18 rollback rehearsal: previous main against a copy carrying record files
PREV="$(git rev-parse origin/main)"          # capture BEFORE D0a is merged
PREV_DIR="$(mktemp -d)/prev" && git worktree add "$PREV_DIR" "$PREV"
(cd "$PREV_DIR" && MOMENTUM_STATE_DIR="$ON/state" python -m trading_algo.paper_trade --account full --synthetic)
git worktree remove "$PREV_DIR"

# AC-27 backfill report on a fresh copy (needs network for prices)
LIVE="$(mktemp -d)" && git archive origin/main state/ | tar -x -C "$LIVE"
for a in full small ultra experimental; do
  MOMENTUM_STATE_DIR="$LIVE/state" python -m trading_algo.decision_record backfill --account "$a" --report
done
```
(The AC-17 dry run's same-day prices make the two runs comparable; `DECISION_RECORD=off`
is the test switch the implementation provides — draft — confirm.)

## Open questions
- **Q-4** Full `StrategyParams` dict in every record (draft — confirm).
- **Q-5** Reconstructed files reach `main` via the D0b pull request after Matt reviews
  the report (draft — confirm).
- **Q-7** Historical code doesn't expose per-candidate signal values (the `trace` is
  new; older commits may predate `precompute`/`targets_at`). **Proposed:** the
  *decision* (targets) comes from the historical code; the per-candidate *signal layer*
  is computed with today's code on the same as-of-truncated prices, and any name where
  the two disagree is flagged. Draft — confirm.
- **Q-8** Re-agree this spec after measurement-truth Task 10 lands (decision/fill date
  semantics, AC-4, AC-6).

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-09 | D0 is the equity decision record; built before the bets layer. | Matt |
| 2026-10-09 | `sleeve_history` values are in A$. | Claude (verified in code) |
| 2026-10-09 | Records live in a separate append-only file per book; book state format unchanged. | Matt |
| 2026-10-09 | Records cover every candidate in the universe. | Matt |
| 2026-10-09 | Reconstruct past rebalances, replayed on the code that ran each decision. | Matt |
| 2026-10-09 | After expert review: measurement truth stays #1; D0a follows Task 10. | Matt |
| 2026-10-09 | After expert review: records carry a signal layer and an execution layer; recording is fail-safe; decision dates come from git history, not a calendar rule. | Claude (verified findings) |
