# Backlog — Regime-adaptive de-risk: the decisive experiment (CROWD-DERISK)

Status: **pre-registered experiment, NOT built.** Calibrated prior from a fair
design panel: **~0.33** probability of clearing the repo's own out-of-sample
overfitting gate. This doc exists so the experiment can be run the moment we're
on a networked box, with the pass/kill criteria fixed *in advance* so we can't
p-hack a null result into a win.

## Why this exists (the honest trail)

1. A 14y real backtest showed the equity book is defensive but lags buy-and-hold
   (Sharpe 0.28 vs 0.51); **US is the only sleeve with edge** (Sharpe 0.39; ASX
   0.02; FTSE −0.19). See [performance-and-allocation notes] below.
2. Asked "how do we go aggressive in bulls / defensive in bears," a first design
   panel refuted every idea. That panel was **biased**: it forced designs to
   reuse the 200-day-MA gate, and told the judges to default-reject.
3. A **fair** re-run (signals *decoupled* from the 200-MA; *balanced* judges)
   changed the verdict from "impossible" to a calibrated **~1-in-3** — a real
   long shot, not zero. The lever-up-in-bulls half stayed dead (levering into low
   realized vol re-adds the tail); the surviving idea is **defensive-only**.

The binding constraint is **statistical power**, not mechanism: the defensive
edge concentrates on ~4–6 independent momentum-crash episodes in 14y, and the
design has ~6–7 free params — exactly what Deflated-Sharpe / PBO punish. That is
why this is a *single pre-registered* test, not a sweep.

## The design under test — CROWD-DERISK (defensive-only)

A graduated defensive multiplier `d ∈ [d_floor, 1.0]` that trims exposure when
the winner book is **crowding** and/or index vol is **spiking** — signals that
fire *while the index is still above its 200-MA*, i.e. where the existing cash
gate is blind (Feb-2018, Jan–Mar-2020).

- **Signal (decoupled).** The two returns-only metrics already computed in
  [crowding.py](../../trading_algo/crowding.py) `crowding_report` (`crowding.py:33`):
  `avg_correlation` (mean pairwise corr of the top-N momentum book, 63d) and
  `vol_ratio` (60d index vol / 756d index vol). **Neither references
  `index > 200MA`.** `crash_setup` is NOT used as a trigger — as coded
  (`below_ma < -0.10`, `crowding.py:70`) it fires only ≥10% *below* the MA, where
  the book is already 100% cash ([signals.py:65-66](../../trading_algo/signals.py#L65)),
  so it is redundant with the gate. (Verified independently by code-read and by
  both fair-panel judges.)
- **Mechanism.** Severity `S = clip(w_v·s_vol + w_c·s_corr, 0, 1)` from continuous
  ramps on `vol_ratio` and `avg_correlation`; `d = 1 − max_cut·S`, floored at
  `d_floor`. `d` is clipped ≤ 1.0 → **defensive-only, never re-levers.**
- **Integration (single weight path, invariant #3).** In
  [strategy.py](../../trading_algo/strategy.py) `compute_targets` (`strategy.py:52`),
  behind a new `StrategyParams.crash_derisk` flag (**default False**): build
  `p_eff = p.with_overrides(target_vol=p.target_vol*d, max_gross=p.max_gross*d)`
  and pass `p_eff` to the existing `vol_target` calls (`strategy.py:92,112`).
  Selection still uses the original `p` (picks unchanged). Refactor the shared
  metric block out of `crowding_report` into one pure
  `crowding.derisk_scale(...)` so observability and sizing read ONE computation.
  Still exactly one `vol_target` / one weight path — `tests/test_consistency.py`
  must stay green.

## Pre-registered configuration (fix BEFORE running; do not tune first)

| knob | fixed value |
|---|---|
| `max_cut` | 0.50 (so `d_floor` = 0.50) |
| `CORR_LO` → `CORR_MAX` | 0.55 → 0.70 (`CORR_MAX` reuses `crowding.py:27`) |
| `VOL_SPIKE` | 2.0 (`crowding.py:28`) |
| `w_v` / `w_c` | 0.55 / 0.45 |
| `derisk_smooth_days` | 21 (EWMA of `S`) |
| sleeve | **US only** (the one with edge worth protecting) |
| universe | point-in-time (survivorship-corrected) |
| costs | on (always) |

## The decisive experiment (run on a networked machine)

```bash
python -m trading_algo.run_backtest --region US --point-in-time   # crash_derisk OFF (baseline)
# then the same with crash_derisk ON (the pre-registered config above)
```

Report **two numbers**, computed against the plain `vol_target` baseline:

1. **Decoupling fraction** — share of de-risk-active days (`d < 1`) on which
   `index_risk_on == True`. This is the mechanism test. **Pass if high (>0.7):**
   the leg does work the 200-MA gate cannot. **Fail if near 0:** it collapses to
   the inert case and is rejected regardless of Sharpe.
2. **Deflated Sharpe + PBO** of ON vs the baseline, using the *full* count of
   thresholds we would ever tune as the trials penalty (not just this run's).
   Gate: **DSR ≥ `PROMOTION_DSR_MIN` (0.95)** and **PBO ≤ `PROMOTION_PBO_MAX`
   (0.5)** — [config.py:215-216](../../trading_algo/config.py#L215).

Also required: tail improvement (lower max-drawdown and left-tail CVaR / worst
month) **without cutting full-sample Sharpe by more than ~0.05**.

## Pass / kill

- **PASS** = decoupling fraction high **AND** DSR ≥ 0.95 / PBO ≤ 0.5 on this
  *single* config **AND** tail improves without >0.05 Sharpe loss. Only then
  consider a small robustness sweep and, if that holds, funding.
- **KILL** = decoupling fraction near 0; OR Sharpe falls >0.05 without a material
  maxDD/CVaR gain; OR DSR < 0.95 / PBO > 0.5. If the honest single shot fails,
  **no sweep should rescue it** — stop.

## Explicitly NOT in scope

- Any **lever-up-in-bulls** path (raising `target_vol`/`max_gross` in calm/uptrend).
  Unsupported (low realized vol ≠ low forward risk); `max_gross=1.0`
  ([config.py:30](../../trading_algo/config.py#L30)) gives no headroom anyway.
- Using **`crash_setup`** as the trigger (redundant with the cash gate).
- Any overlay that **reuses the 200-MA** to define its regime (structurally inert
  — proven by the fair panel; treat as a permanent design rule).

## Related backlog (not this spec, but same investigation)

- **Trim FTSE → US/ASX 50/50.** The one statistically robust allocation signal is
  FTSE's negative excess return; remove `"FTSE"` from `config.ALLOCATIONS`
  (stays registered/backtestable), renormalise US/ASX. Itself a parameter change
  → same OOS + DSR/PBO gate before funding. Do NOT concentrate into US-only
  (its 0.39 Sharpe is only ~1.4 t-stat; overfit by construction).
- **Trend-gate hysteresis + equity no-churn dead-band** (mirroring FX
  `rebalance_min_delta`) — cut whipsaw cost on the *existing* gate; research
  toggle, off by default.
