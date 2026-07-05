# Architecture & sustainability review

*Reviewers: the Architect and the Chief Engineer · as of 2026-07-04 · covers all
19 backlog items.*

This is the answer to "are we building this sustainably?" The full backlog
([`backlog/backlog.json`](backlog/backlog.json)) was run through two lenses — the
Architect (coherence, shared foundations, maintainability) and the Chief Engineer
(invariant safety, build-readiness, tech debt). The machine-readable output is
[`backlog/build_plan.json`](backlog/build_plan.json), enforced by
[`validate.py`](validate.py); this document is the narrative.

**Both reviewers converged on one headline finding:** shipping these 19 features
one-by-one would fork the codebase. Four of them are actually *platform
primitives*, three others touch sizing/cost and would each grow a second copy of
logic the invariants forbid, and two cost paths are *already* diverging today.
The sustainable move is **foundations first, then features, in dependency order.**

---

## 1. The shared foundations (build once)

Nine primitives that multiple features lean on. Building a feature without its
foundation is precisely how a second copy of the logic — an invariant-#3
violation — or a pile of tech debt gets created.

| Foundation | Enables | Why it must be shared |
|---|---|---|
| **P0-A** Volume/ADV in the data layer | F6, F15, F9 | `data.py` fetches only `Close`; two features need trailing volume |
| **P0-B** Fill capture in execution | F11, F3, F10 | `execution_ibkr.rebalance()` discards `placeOrder()`'s result today |
| **P0-C** One unified cost function | F6, F15 | Two cost paths already diverge (see §3) |
| **P0-D** Pre-signal data pipeline hook | F7, F14, F1 | One seam both engines call before `compute_targets` |
| **P0-E** Shared equity stats module | F2, F8, F19 | `metrics.py` and `forex/validation.py` already duplicate Sharpe math |
| **P0-F** Notification/telemetry channel | F12, F9, F10 | The unattended engine only logs to a file today |
| **P0-G** Run-manifest + experiment ledger | F2, F3, F10, F17 | F2's Deflated Sharpe needs a *real* trial count, not a guess |
| **P0-H** Schema-validated state files | F10, F4, F18 | `paper_state_*.json` is one flat untyped dict three features mutate |
| **P0-I** Capacity hook *inside* `compute_targets` | F15, F6 | The single biggest invariant-#3 risk in the backlog (see §2) |

Four of these are themselves backlog items — **F7 = P0-D, F11 = P0-B, F17 = P0-G,
F18 = P0-H** — so they are not extra work, just *sequenced first*.

## 2. Prerequisite refactors

Small, unglamorous changes that must land before the features that would
otherwise entrench divergence:

| # | Refactor | Before | Realizes |
|---|---|---|---|
| **R1** | Unify the two cost paths into one `fees.py` entrypoint | F6, F15 | P0-C |
| **R2** | Capture `ib_insync` fill/Trade results in `rebalance()` | F3, F10, F11 | P0-B |
| **R3** | Centralize ADV/volume ingestion in `data.py` | F6, F15 | P0-A |
| **R4** | Strengthen `test_consistency.py` beyond string-grep | F3, F9, F15 | invariant #3 guard |

**R4 is the keystone.** `tests/test_consistency.py` today only checks *source
text* — that `compute_targets` appears and `select_portfolio` doesn't. A second
sizing path (e.g. an ADV cap trimmed post-hoc in `paper_trade`) would satisfy
that grep while still bypassing the one weight function. The fix is numeric
golden-fixture equality: backtest and paper must produce **bit-identical** weights
and costs on a fixed-seed run. Everything that touches sizing (F3, F6, F15, F9)
sits behind this guard.

## 3. Where the current architecture strains

Named seams the reviewers found by reading the code, not assuming:

- **Two cost mechanics already diverge.** `backtest.py` charges cost as
  `turnover × rate`; `paper_trade.py` inlines slippage into the fill price then
  adds commission + stamp separately. F6 layered on top *without* unifying would
  be a third path — and would make F6's own "impact-off reproduces today's
  numbers exactly" acceptance criterion unstateable. → **R1 in Phase 0.**
- **Fills are thrown away.** `execution_ibkr.rebalance()` calls `placeOrder()`
  and discards the result, so there is no decision-price-vs-fill record for F3,
  F10 or F11 to consume. → **R2 ahead of F11.**
- **`compute_targets` has a closed signature.** No liquidity/cost channel, so an
  ADV cap added *outside* it in each caller reopens invariant #3. → **P0-I.**
- **Hardcoded, currency-mismatched thresholds.** `MIN_TRADE_VALUE`
  (`execution_ibkr.py`) and the micro threshold (`paper_trade.py`) are module
  constants, not per-`Region`, already mixing AUD/USD/GBP. Left alone, a 4th
  region silently misbehaves — breaking the "add a region is one entry" property.
  → move to `Region` fields before F6/F11/F15 add more thresholds.
- **Cache key ignores schema.** `data.py` caches parquet by ticker+date; adding a
  Volume column won't invalidate old Close-only caches. → version the cache key
  when P0-A lands.

## 4. The build order

Five dependency-ordered phases. `validate.py` asserts every item is placed
exactly once and no feature is scheduled before something it depends on.

```
Phase 0 — Foundations & safety        F7  F16 F17 F18   (+ R1 R2 R3 R4)
Phase 1 — Statistical validity        F8  F2  F19
Phase 2 — Data integrity              F1  F13 F14
Phase 3 — Execution realism & capacity F6 F15 F11 F12 F9
Phase 4 — Live readiness & reporting  F3  F10 F4  F5
```

- **Phase 0** carries no capital risk and unblocks everything: the data-quality
  gate, the CI regression net, the manifest/ledger, and state-file schemas.
- **Phase 1 before Phase 2 is deliberate.** F1 (point-in-time data) is blocked on
  external vendor procurement; the statistical work is pure code and unblocked,
  so it proceeds *in parallel* with data acquisition. The Architect's fair
  objection — "don't certify survivorship-biased data" — is handled by the
  continuous-backlog rule: **F2's DSR/PBO re-runs once F1 lands**, and every
  non-PIT result keeps its banner (invariant #5).
- **Phase 4 discipline:** F3 + F10 are the *sole* hard exit criteria for live
  capital. F4 and F5 are bundled in the phase but ship on their own schedule so
  they can't create a false "we're live-ready" signal.

> **Delivery in progress — Phase 0.** Landed so far:
> - **R4** — `test_consistency.py` now asserts numeric weight equality (backtest
>   holds `compute_targets`' output bit-for-bit; paper invents no names and
>   preserves relative sizing).
> - **F18** — `state_schema.py` validates and migrates `paper_state_*.json` on
>   load/save, failing safe behind `config.VALIDATE_STATE_FILES` (default-off).
> - **F17** — `manifest.py` records a reproducible manifest per run (git SHA,
>   params fingerprint, region set, data range, metrics) and an append-only
>   experiment ledger — the honest `n_trials` source F2's Deflated Sharpe needs.
> - **F16** — `ci_regression.py` compares the deterministic synthetic backtest to
>   a committed baseline; wired into CI (`ci.yml`) and the test suite so an
>   accidental lookahead / cost / sizing regression fails the build.
> - **F7** — `data_quality.py` is the shared pre-signal gate (foundation P0-D):
>   stale / gap / dead-price / region-aware impossible-move detection, no
>   lookahead, composing with PIT membership by intersection. Both engines filter
>   the candidate set through it identically; paper additionally *freezes* a held
>   flagged name (no trade on an untrusted price). A perfect no-op on clean data.
>
> **Phase 0 foundations complete.** Remaining Phase-0 work is refactors R1/R2/R3,
> built just-in-time before their Phase-3 consumers. All landed items carry no
> capital risk and harden guarantees every later phase leans on.

> **Delivery in progress — Phase 1 (Statistical validity).** Landed:
> - **P0-E** — the López de Prado stats (PSR/DSR/PBO) are promoted to one shared
>   `trading_algo/validation.py`; `forex/validation.py` re-exports them, so equity
>   and FX share identical, tested math (no third copy).
> - **F8** — `walkforward.py` builds a purged & embargoed (>=21d, >=6 folds)
>   out-of-sample return matrix over the parameter grid, reusing only the
>   purge/embargo discipline (the equity signal is a fixed formula, not a trained
>   model).
> - **F2** — `validation.overfitting_gate` deflates the in-sample-best Sharpe for
>   the real trial count and computes PBO; surfaced via `sweep --purged-cv`
>   (n_trials == grid size) and a PSR/DSR line on every `run_backtest` report
>   (deflated for the F17 ledger's cumulative trial count).
> - **F19** — `validation.sharpe_haircut` reports the expected live Sharpe after
>   deducting selection luck, shown next to the raw Sharpe.

> **Delivery in progress — Phase 2 (Data integrity).** Landed (code complete;
> F1's real survivorship numbers await vendor membership files):
> - **F1** — `run_backtest --compare-pit` quantifies survivorship bias as the
>   static-vs-point-in-time CAGR delta; PIT eligibility composes with the F7 gate
>   by intersection.
> - **F13** — `delisting.py` injects a Shumway replacement return on the day a
>   held name delists, behind `config.DELISTING_REPLACEMENT_RETURN` (default off),
>   applied only in the PIT backtest path.
> - **F14** — `data.py` gains a fallback-source registry; on a primary (Yahoo)
>   failure it tries `config.DATA_FALLBACK_SOURCE`, and the fallback's prices
>   still flow through the F7 quality gate. Perfect no-op when unset.

> **Delivery in progress — Phase 3 (Execution realism & capacity), slice 1.**
> Landed the low-risk, observability-only items (no ADV, no invariant-#3 change):
> - **P0-F** — `notifications.py`, the one shared alert channel (pluggable;
>   default "log"). `breaker_transition` classifies halt/resume so alerts fire
>   once per transition, never every halted day.
> - **F12** — `paper_trade.run_daily` alerts through P0-F on the drawdown
>   breaker's halt/resume transition.
> - **F9** — `crowding.py`, a returns-based momentum-crash / crowding monitor
>   (pairwise correlation, dispersion, vol-spike, bear-then-bounce setup), no
>   lookahead, read-only (a test asserts `compute_targets` is unchanged); surfaced
>   on the `run_backtest` report.
>
> **Slice 2 — started.** **R2 + F11** landed (measurement-only, no invariant/
> baseline risk): `execution_ibkr` now records the arrival (decision) price and
> captures the broker's average fill (completing the fill-capture #50 began);
> paper trades carry a `decision` price; new `tca.py` reports per-region
> implementation shortfall vs modelled slippage (`paper_trade --tca`) and alerts
> when realized materially exceeds modelled. The fill-capture piece (P0-B) also
> feeds Phase 4's F3/F10.
>
> Remaining in slice 2: **R1** (unify cost paths) → **F6** (market-impact model);
> **R3** (ADV ingestion) + **P0-I** (`compute_targets` capacity hook) → **F15**
> (pre-trade ADV cap).

## 5. Feature readiness at a glance

From [`build_plan.json`](backlog/build_plan.json) `feature_plan`:

- **Ready now (no blocker):** F5, F7, F12, F14, F16, F17, F18
- **Needs a foundation first:** F1, F2, F4, F9, F10, F13, F19
- **Needs a refactor first:** F3, F6, F8, F15
- **Scope change:** **F6 splits** — ship cost-unification + impact now, **defer
  the borrow-cost half** until a short-book epic exists (there is no short book
  today; building it now is speculative generality).

## 6. How the invariants stay held across the whole set

The `invariant_guards` block in `build_plan.json` records, per invariant, which
items put it at risk, how it's kept, and the test that proves it. The load-bearing
ones:

- **#3 one weight function** (F3, F6, F15, F9): all sizing/cost logic lives inside
  `compute_targets` (P0-I) or the single `fees.py` entrypoint (P0-C); R4 upgrades
  the guard from grep to numeric equality.
- **#1 no lookahead** (F1, F13, F7, F8, F3): PIT/delisting reveal info only at the
  effective date; data repairs use trailing data; attribution replays the logged
  `asof`/eligible set, never a hindsight refetch.
- **#2 costs always on** (F4, F6): the unified cost function fires regardless of
  the impact-model flag; FX spread charged on every transfer.

## 7. What this buys us

The point of the review is *sustainable* delivery, not a Gantt chart:

1. **No forked logic** — four features become shared foundations; R1–R4 collapse
   divergence that already exists before it spreads.
2. **The invariants get stronger, not just preserved** — R4 turns the weakest
   guard (a text grep) into numeric equality.
3. **Nothing is dropped or mis-ordered** — the plan is machine-checked, so it
   can't silently rot as items move.
4. **"Add a region" stays one entry** — the per-region-threshold and cache-key
   fixes protect the project's core extensibility property.

Read the machine-readable plan in
[`backlog/build_plan.json`](backlog/build_plan.json); it is validated in CI by
`tests/test_product_backlog.py`.
