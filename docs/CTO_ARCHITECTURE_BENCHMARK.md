# CTO / Architect / Chief-Engineer Review & Global Benchmark

*Whole-repo assessment as of commit `4de2528` (2026-07-05). Reviewed through three
lenses — CTO (strategy, risk, sustainability), Architect (coherence, extensibility),
Chief Engineer (invariant safety, correctness, build quality) — and benchmarked
against the open-source quant ecosystem and institutional research standards.*

This document is a point-in-time external benchmark. It complements — and does not
replace — the delivery-focused [`product/ARCHITECTURE_REVIEW.md`](../product/ARCHITECTURE_REVIEW.md),
which reviews the *backlog*. This one reviews the *system as built*.

---

## 1. Executive summary

**Overall grade: A− / "Institutional-grade discipline at retail scale."**

This is, unambiguously, in the **top few percent of independent/retail quant
repositories**. The things that sink most quant projects — lookahead leakage,
costs quietly switched off, a backtest that doesn't match live, and Sharpe ratios
uncorrected for the hundred strategies you secretly tried — are here treated as
**architecturally enforced invariants with tests that prove them**, not aspirations
in a README. The research-integrity layer (Deflated Sharpe, PBO, purged &
embargoed walk-forward) is genuine López-de-Prado methodology, correctly
implemented in pure NumPy, and *shared* between the equity and FX subsystems so it
cannot fork.

The codebase is ~18k lines of typed Python across two coherent subsystems (a
multi-region equity momentum book and an independent multi-agent FX trader), 400+
tests all green, a self-testing behavioural regression gate in CI, a reproducible
run manifest + experiment ledger, and a code-enforced product process.

The gaps are almost entirely in **two bands**: (a) *static* quality tooling that
funded teams take for granted (no type checker, no linter, no coverage, unpinned
dependencies), and (b) the **live-execution path** (`execution_ibkr.py`), which is
the least-mature and least-protected module in the repo and carries at least one
real correctness bug. Neither undermines the paper-trading system that exists
today; both must close before real capital.

### Scorecard vs global benchmarks

| Dimension | Grade | Benchmark reference | Verdict |
|---|---|---|---|
| **Research integrity** (no-lookahead, costs-on, DSR/PBO/PSR, purged CV) | **A** | López de Prado *AFML*; AQR practice | Beats nearly all OSS frameworks; matches institutional method |
| **Architecture & extensibility** (Region record, single weight fn) | **A−** | Zipline/LEAN plug-in models | Cleaner separation than most; a few currency-keyed side-tables leak |
| **Backtest/paper consistency** (one `compute_targets`, numeric guard) | **A** | vectorbt/bt; live-vs-sim parity | Structurally enforced + bit-identical test — rare anywhere |
| **Test & regression engineering** | **A−** | Testing pyramid; CI gating | Real invariant/regression suite; no coverage or property-based testing |
| **Reproducibility & observability** | **B+** | ML-repro / experiment-tracking norms | Manifest+ledger strong; deps unpinned undercuts it |
| **Static quality tooling** (types, lint, coverage) | **C** | Any funded eng org baseline | Rich hints but nothing checks them; no linter/coverage |
| **Live execution readiness** | **C−** | Broker-integration correctness | Cost-basis rebalancing bug, fills discarded, no risk gates |
| **Documentation & honesty** | **A+** | Industry norm (usually marketing) | Exceptional: HFT reality, survivorship caveats, unfunded-sleeve gating |

---

## 2. How it compares to the open-source quant ecosystem

| Capability | Zipline | Backtrader | QuantConnect LEAN | vectorbt | **This repo** |
|---|---|---|---|---|---|
| No-lookahead enforced *by test* | Partial | Manual | Engine-level | User's job | **Yes — regression gate + numeric consistency test** |
| Costs mandatory (can't report gross) | Configurable | Configurable | Configurable | Off by default | **Invariant #2 — always on, stamp duty modelled** |
| One weight fn for sim & live | N/A | N/A | Shared engine | N/A | **Yes — `compute_targets`, bit-identical guard** |
| Overfitting correction (DSR/PBO) built in | No | No | No (add-on) | No | **Yes — shared `validation.py`, in the report** |
| Purged + embargoed walk-forward | No | No | No | No | **Yes — equity `walkforward.py` + FX** |
| Survivorship-bias handling | Bundle-dependent | No | Yes (data) | No | **Point-in-time membership, labelled either way** |
| Multi-currency portfolio w/ FX P&L | Limited | No | Yes | No | **Yes — sleeve-local, converted at portfolio layer** |
| Reproducible run manifest + trial ledger | No | No | Backtest IDs | No | **Yes — git SHA + params fingerprint + n_trials** |
| Maturity of live-execution layer | High | High | High | N/A | **Low — the repo's weakest area** |

**Reading:** on *research method and correctness discipline* this repo meets or
exceeds every mainstream OSS framework — it bakes in the anti-overfitting controls
those frameworks leave to the user. Where the mature frameworks are years ahead is
**production execution plumbing** (order lifecycle, fills, reconciliation, broker
edge-cases), which here is a thin, buggy, paper-first stub.

---

## 3. What it does better than the global benchmark

1. **Invariants are enforced by construction, then proven by test.** `compute_targets`
   is the single source of truth for weights; `tests/test_consistency.py` asserts the
   backtest holds its output *bit-for-bit* and that paper trading invents no position
   outside it. This is the numeric upgrade of what most repos leave as a code-review
   convention. (`strategy.py:49`, `tests/test_consistency.py:55-136`)
2. **A self-testing regression gate.** `ci_regression.py` diffs a seeded synthetic
   backtest's headline metrics against a committed baseline with per-metric
   tolerances, and a *negative* test proves the gate actually fires on injected drift
   (`tests/test_backtest_regression.py:34-39`). A regression harness that tests itself
   catches accidental lookahead/cost/sizing regressions that unit tests miss.
3. **Honest statistics wired into the honest trial count.** The DSR/PBO deflation is
   fed by a real experiment ledger (`manifest.py`) rather than a guessed `n_trials`.
   Closing the p-hacking loop like this is something even funded teams routinely skip.
4. **The single most honest documentation set I have reviewed in this domain.**
   `docs/HFT_REALITY.md`, the survivorship caveats, invariant #5 ("synthetic results
   are pipeline tests, never performance"), and the *funded-vs-registered* sleeve gate
   (TSX ships fully backtestable but deliberately absent from `ALLOCATIONS` until a
   walk-forward earns it capital) are the opposite of the usual retin-quant hype.
5. **Fills-as-truth accounting.** Paper P&L is derived FIFO from a fills ledger, with
   `cost_basis`/`realized_pnl` explicitly *not* the source of truth (`paper_trade.py:164-169`,
   `pnl.py`) — this prevents the classic running-tally drift that corrupts most
   home-grown simulators.
6. **Crash-safe state.** SQLite in WAL mode + `atomic_write_json` (temp→fsync→replace),
   and a *fail-safe* state schema that raises on a corrupt-but-parseable file rather
   than silently resetting equity (`storage.py`, `state_schema.py:14-19`).

---

## 4. Where it falls short of the benchmark

### 4a. Live execution — the weakest module, and it is the one that touches money
- **Cost-basis vs market-value rebalancing bug (correctness).** Current positions
  are valued at `position × avgCost` (cost basis) and diffed against a market-value
  target `nav × weight`, so winners are under-sold and losers over-sold on every
  rebalance; sell quantities are derived from money not held shares
  (`execution_ibkr.py:45,61-66`). **This is a real, systematic error.**
- **Fills are discarded** — `ib.placeOrder()`'s `Trade` result is ignored
  (`execution_ibkr.py:74`); no decision-price-vs-fill reconciliation exists.
- **The live path has no risk gates** — the drawdown breaker and min-viable-size gate
  live only in the paper engine, so the real-money path is the *least* protected code
  in the repo, and models neither commission nor stamp duty in sizing.

### 4b. Static quality tooling — below any funded-org baseline
- **No type checker.** Rich hints everywhere, `from __future__ import annotations`
  throughout — and nothing verifies them. For a numerical codebase, silent
  `float`/`Series`/`None`/units bugs are the dominant defect class; this is the single
  biggest missing safety net.
- **No linter/formatter, no coverage measurement, no pre-commit.** 400 tests exist but
  nobody knows what fraction of `trading_algo/` they exercise.
- **Dependencies unpinned and drifting.** `requirements.txt` and `pyproject.toml` use
  `>=` floors only, no lockfile, and disagree (`ib_insync` is a hard dep in one, an
  optional extra in the other). The strong reproducibility story (manifest records the
  git SHA) is undercut because it does *not* capture the resolved dependency set — a
  transitive break can land silently in a scheduled run.

### 4c. Correctness & consistency nits found by reading the code
- **Cache keys omit identity.** `load_region` keys on ticker *count* not tickers
  (`data.py:83`); `load_fx` keys with no currency list (`fx.py:37`). Editing a universe
  while keeping the count constant can serve a stale cache in backtests (live is
  protected by `use_cache=False`).
- **Drawdown cooldown counts *runs*, not days.** The breaker decrements once per run
  (`paper_trade.py:305`) while the engine fires up to 3×/day, so `DRAWDOWN_COOLDOWN_DAYS
  = 21` ("~1 month") is really ~7 calendar days — config comment and behaviour disagree.
- **FX subsystem: the multiple-testing correction is weaker than advertised.** DSR's
  `n_trials` is the size of the *final* compared set, not the true search count
  (hyperparameters, profiles, prior iterations excluded) — so DSR is optimistically
  biased in the one place the system prides itself on honesty (`ml_backtest.py:157`,
  `research.py:114`).
- **Cost/annualisation logic has re-forked in the FX ML/research corner.** Three copies
  of "half the spread" (`fx_backtest.py:89`, `marks.py:50`, `ml_backtest.py:45`) and a
  hardcoded `252` annualisation in the ML/research reports (`ml_backtest.py:56`) that is
  wrong the moment they touch an intraday panel — even though `marks.periods_per_year`
  exists precisely to prevent both. `marks.py` was built to stop this; `ml_backtest`
  bypasses it.
- **Un-fenced ML lookahead boundary.** The deployable frozen model is fit on *all* rows
  (correct for true-forward live), but nothing prevents running it through `fx_backtest`,
  which would silently leak the whole window. Only the walk-forward evaluator is
  leakage-safe by construction.
- **`AgentPool` thread-pool reuse claim is false** — a fresh `ThreadPoolExecutor` is
  built per cycle (`agents.py:153`) despite the README/engine claiming a long-lived pool.

### 4d. Extensibility leaks (the "add a region = one entry" property is ~90%, not 100%)
`universes.UNIVERSES` duplicates the region→universe map already in `REGIONS`;
`fx._SYNTH_LEVEL`, `data_quality.JUMP_GBP`, and the dashboard's `registry` tables are
hand-maintained currency-keyed side-tables. A genuinely clean design folds these into
the `Region` record.

### 4e. Operational / infra
- Committed binary SQLite DB + mutable state JSON in git, updated via rebase-and-force
  from scheduled runners with an `-X theirs` conflict hack — clever but an anti-pattern
  that bloats history and can silently pick the wrong book on a genuine divergence.
- CI runs a single Python version (3.11) though `requires-python = ">=3.11"` claims
  3.12/3.13; no dependency vulnerability scanning (Dependabot/pip-audit/CodeQL).
- Dashboard is a single 2,441-line `app.js` (no modules/components) and binds localhost
  with no auth — fine for a local tool, a foot-gun if ever bound to `0.0.0.0`.

---

## 5. Top risks, consolidated and ranked

| # | Risk | Location | Severity | Fix |
|---|---|---|---|---|
| 1 | Cost-basis rebalancing + fills discarded + no risk gates on the live path | `execution_ibkr.py:45,61-74` | **High** (real capital) | Value positions at market; size sells from held shares; capture `Trade` fills; route live through the paper engine's breaker/min-viable/DQ gates |
| 2 | No type checker / lint / coverage; deps unpinned | repo-wide, `requirements.txt` ↔ `pyproject.toml` | **High** (silent defects) | Add mypy/pyright + ruff + coverage to CI; pin/lock deps; reconcile the two dep files |
| 3 | DSR `n_trials` undercounts the real search | `ml_backtest.py:157`, `research.py:114` | Med | Source `n_trials` from the F17 experiment ledger's cumulative trial count |
| 4 | Cost/annualisation re-forked in FX ML/research | `ml_backtest.py:45,56` | Med | Route through `marks.cost_fraction`/`marks.periods_per_year`; delete the copies |
| 5 | Cache keys omit ticker/currency identity | `data.py:83`, `fx.py:37` | Med | Version the cache key on a content hash of the ticker/currency set |
| 6 | Drawdown cooldown counts runs not days | `paper_trade.py:305` vs `config.py:91` | Med | Track wall-clock dates, or fix the docstring to "runs" |
| 7 | Un-fenced ML all-data model reachable from backtest | `walkforward.py:80`, `fx_book.py:81` | Med | Refuse the frozen bundle inside `fx_backtest`, or assert train-end < backtest-start |
| 8 | Git-as-database with committed binary DB | `.gitignore`, workflows | Low-Med | Move books to an artifact store / release asset; stop force-tracking the `.db` |

---

## 6. Prioritised recommendations

**Now — cheap, high-leverage, zero capital risk (a "Phase 0.5"):**
1. Add `mypy --strict` (or pyright), `ruff`, and `pytest-cov` to `ci.yml`; fail the
   build on regressions. This is the highest-ROI single change in the repo.
2. Pin dependencies (lockfile) and reconcile `requirements.txt` ↔ `pyproject.toml`;
   record the resolved dep set in the run manifest so reproducibility is complete.
3. Fix the FX `n_trials` to read the experiment ledger, and collapse the three cost
   formulas + the `252` annualisation onto `marks.py`. These restore the honesty the
   system already advertises.

**Before any live capital — harden the one path that touches money:**
4. Rewrite `execution_ibkr.rebalance()`: market-value positions, share-based exits,
   captured/reconciled fills, and route it through the same drawdown breaker,
   min-viable gate and data-quality gate the paper engine already has. Add a golden
   test that live and paper produce the same target book on identical inputs (extend
   invariant #3's numeric guard to the execution layer).

**Structural hygiene — protect the properties that make the repo special:**
5. Fold the currency-keyed side-tables (`UNIVERSES`, `_SYNTH_LEVEL`, `JUMP_GBP`,
   dashboard `registry`) into the `Region` record to make "add a region = one entry"
   literally true.
6. Version the data/FX cache keys; add a CI Python-version matrix (3.11/3.12/3.13) and
   Dependabot/pip-audit.
7. Move mutable books out of git into an artifact/object store.

**Longer term — depth where the method is already strong:**
8. Add `hypothesis` property-based tests for the numerical invariants (no-lookahead,
   whole-shares, costs-always-on) — the current "property" tests check one seeded point.
9. Modularise `dashboard/static/app.js`; add a docs warning against non-localhost binds.

---

## 7. Verdict

**As a research-and-paper-trading system, this is a model of how to do it right** — it
enforces the disciplines that separate a real edge from a curve-fit, and it is honest
about its own limits to a degree that is genuinely rare. Benchmarked against the OSS
quant ecosystem it leads on method and correctness discipline and trails only on
production execution maturity.

The distance between "excellent research system" and "system you can point capital at"
is concentrated in three closable gaps: **static quality tooling**, **dependency
reproducibility**, and a **hardened live-execution path**. None require rethinking the
architecture — the foundations are sound and the invariants are the right ones. Close
those three and this crosses from "top-percentile independent quant repo" into
"institutionally defensible."

*Nothing in this review should be read as performance validation: per invariant #5, no
real-money or non-synthetic track record was evaluated, and the live path should not be
funded until the Section 5 / #1 issues are resolved.*
