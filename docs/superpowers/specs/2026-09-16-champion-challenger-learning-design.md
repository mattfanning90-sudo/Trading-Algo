# Champion/Challenger Learning Loop (Design)

**Status:** Design agreed, pending implementation plan
**Date:** 2026-09-16
**Scope:** FX subsystem only (`trading_algo/forex/`). Three learning lanes. The
equity sleeves run a fixed 12-1 formula with no learned component and are not
touched.

## 1. What this is

One **promotion path** shared by every learned component in the FX subsystem —
the neural model and the swarm's bred agents alike. A challenger only reaches a
live book by beating the **incumbent** on evidence it could not have been
selected against.

It replaces two mechanisms that both fail, in opposite directions:

| path today | gate | outcome |
|---|---|---|
| swarm → live | DSR ≥ 0.95 (needs ≈3.8 Sharpe) | promotes nothing, ever — and `--champions` is never passed, so the roster is not read either way |
| neural → live | none | promotes everything, including a measured −0.62 Sharpe model |

Neither is learning. The first cannot improve because nothing passes; the second
cannot improve because nothing is compared — each weekly run replaces the live
model unconditionally, so a worse model silently displaces a better one. That is
drift.

The loop's job is to make improvement **monotonic**: the live configuration can
only ever be replaced by something demonstrably better, and when nothing is
better, nothing changes.

## 2. Evidence this design is built on

Measured during the diagnosis, all reproducible:

- **The neural model is trained mostly on the wrong data.** Target is the raw
  forward return and the dataset pools 7 FX pairs with 3 crypto. Crypto is
  **24.0% of rows but 96.3% of the squared target the loss sees**; all seven FX
  pairs contribute 3.7% between them. Vol-normalising the target moves crypto to
  26.4%, matching its row share.
- **It overfits at 150 gradient steps.** Train loss improves monotonically with
  more training (−2.38 → −3.50 annualised Sharpe) while validation loss gets
  *worse* (−0.79 → −0.67). Best out-of-sample result is the least-trained
  configuration. Training longer is not the lever; 96× more gradient updates was
  strictly worse.
- **The gross edge is consumed by turnover.** Vol-normalised: gross OOS Sharpe
  **+0.60**, net after half-spread **−0.44**. Hit rate 46.7% — below a coin
  flip, positive gross only by magnitude.
- **The swarm's hold-out has been mined.** 424 genomes × 3 cycles scored against
  the same slice: 1,272 selections. It is not out-of-sample any more.
- **Training uses a fraction of available data.** 10 of 25 registered
  instruments, starting 2015. Six FX crosses (EURGBP, EURJPY, GBPJPY, AUDJPY,
  AUDNZD, EURAUD) are priced, registered, and never seen.

## 3. Decisions (locked during brainstorming)

- **D1 — Autonomy: models and roster only.** The loop may retrain the neural
  model and may promote/retire bred agents into the live roster. It may **not**
  change indicator windows, thresholds, holding policy, vol target, gross caps,
  the drawdown stop, capital, or which books exist. Knob-tuning is excluded
  deliberately: `policy_sweep` already demonstrated in-sample knob gains that
  failed out-of-sample, and a loop that tunes its own knobs is an overfitting
  machine with no human in the path.
- **D2 — Three lanes, split by what the data actually is.**
  | lane | books | bars | universe |
  |---|---|---|---|
  | `daily_fx` | matt, partner | 1d | DEFAULT_UNIVERSE + crosses |
  | `intraday_fx` | daytrader | 60m | DEFAULT_UNIVERSE + crosses |
  | `multiasset` | multiasset | 1d | locked equity/bond/AUDUSD |
  One model across all three would be learning three different games at once —
  the same pooling error that produced the crypto problem, one level up.
- **D3 — Promotion needs three things** (§5): an absolute floor, a paired
  relative win, and forward confirmation. Not one of them alone.
- **D4 — Qualify offline, confirm forward.** A mined hold-out cannot be the
  final word (§2). Forward shadow performance is the only evidence that cannot
  be selected against, because it does not exist when the choice is made. This
  mirrors the equity side's `MIN_PROMOTION_REBALANCES`.
- **D5 — "No champion" is a supported state.** When nothing clears the floor the
  lane runs the five hand-written technical agents and no learned component.
  `NeuralAgent` already returns a flat signal with no bundle, so this is today's
  working behaviour, not a failure mode.
- **D6 — Data expansion ships with this, not after.** The six unused crosses and
  history back to ~2004 enter the lanes from day one. Building promotion
  machinery around a model still overfitting on a quarter of the available data
  would measure the wrong thing.

## 4. Architecture

### New modules

| module | responsibility |
|---|---|
| `forex/lanes.py` | lane registry: which books, bars, universe, model path per lane |
| `forex/promotion.py` | the ONE gate: floor + paired test + shadow confirmation |
| `forex/shadow.py` | shadow books — run a challenger forward without capital |

`champions.py` and the ML loader both call `promotion.py`. That is the point:
one gate, two consumers, no second copy of the rule (the FX analogue of
invariant #3).

### Flow

```
                    ┌──────────── per lane ────────────┐
   history ──► train challenger ──► qualify (offline)  │
                                          │ pass       │
                                          ▼            │
                                    shadow book  ◄─── live bars, no capital
                                          │ N periods  │
                                          ▼            │
                                  confirm vs incumbent │
                                          │ pass       │
                                          ▼            │
                                    PROMOTE ──► live roster / model
                    └──────────────────────────────────┘
                         fail at any step -> incumbent unchanged
```

## 5. The promotion gate

A challenger is promoted only when **all three** hold:

1. **Absolute floor.** Net-of-cost out-of-sample Sharpe > `FLOOR` (default 0.0).
   Without this, "beat the incumbent" promotes the less-bad of two losing models
   — exactly how a −0.44 model gets promoted because the incumbent is −0.62.
2. **Paired relative win.** Scored on the *same* periods as the incumbent, the
   challenger's per-period return differences must be positive with significance
   (bootstrap on the paired difference series). Paired, because an unpaired
   Sharpe comparison across different windows is mostly a noise draw.
3. **Forward confirmation.** The challenger must hold conditions 1 and 2 across
   a shadow period of `SHADOW_PERIODS` live bars before promotion.

`SHADOW_PERIODS` defaults to **20 trading days** for the daily lanes. This is a
judgement, not a measurement, and it is the most consequential number in the
design: too short promotes noise, too long and the loop never improves anything.
It is a lane constant so it is cheap to change.

Costs are on in every number (invariant #2). `strategy_returns` already charges
the half-spread per unit turnover and is the shared scorer.

## 6. Making the model worth promoting

The gate is worthless if no challenger can ever clear the floor. Four changes,
each justified by §2:

- **Vol-normalise the target.** `y = forward_return / trailing_vol`. Measured:
  crypto's share of the loss 96.3% → 26.4%; net OOS Sharpe −0.65 → −0.44. A
  Sharpe-loss model *should* target risk-adjusted return — this is a correctness
  fix, not a tuning choice.
- **Portfolio-level Sharpe objective.** The current loss computes the Sharpe of
  a pooled `(pair, timestamp)` scatter. A strategy's Sharpe aggregates positions
  into a portfolio return *per timestamp*, then takes mean/std *over time*.
  These are different quantities and the present one is the wrong one. No amount
  of training fixes a misspecified target.
- **Training discipline.** Pass a validation split so the early-stopping and
  best-weight-restore logic in `fit()` actually runs (today no `X_val` is ever
  supplied, so training is blind). Seed-ensemble the *evaluation*, not just the
  live bundle — `_sharpe_factory` uses `seed=0` only, so every reported number
  is a single draw.
- **Less capacity, not more.** A 3.5-train / 0.67-val gap says the model is too
  flexible for the signal. Stronger L2 and/or a narrower hidden layer, chosen on
  the validation curve rather than by preference.

## 7. Persistence

- `state/lane_{lane}.json` — incumbent id, its qualifying scores, promoted-at,
  and the shadow challenger's running record.
- `models/{lane}/incumbent.json`, `models/{lane}/challenger.json` — frozen
  bundles. Written through `storage.atomic_write_json`.

Note: `trading_algo/forex/models/` does not currently exist; the weekly job
trains into it and the bundle is ephemeral per run. Persisting the incumbent is
what gives the loop memory at all.

## 8. Workflows

- `fx-learn.yml` (weekly) — train a challenger per lane, qualify, start or
  advance its shadow record, promote when confirmed.
- The existing `fx-paper.yml` stops passing `--ml` unconditionally. It reads the
  lane's promoted incumbent, or runs technical-only if there is none (D5).

## 9. Dashboard

A LEARNING panel per lane: incumbent id and promotion date, the challenger's
shadow progress (`day 7/20`, running paired difference), and the last decision
with its reason. A loop whose decisions are invisible is a loop nobody trusts.

## 10. Invariants preserved

- **#1 no lookahead** — challengers are scored by the existing purged/embargoed
  walk-forward; shadow scoring is forward by construction.
- **#2 costs always on** — every promotion number is net of the half-spread.
- **#3 one weight function** — untouched. Promotion changes *which agents are in
  the pool*, never how weights are computed from their signals.
- **#5 synthetic ≠ performance** — no promotion may ever be decided on synthetic
  data. The gate refuses to run in synthetic mode.

## 11. Non-goals (YAGNI)

- No knob tuning (D1).
- No equity lane. The equity sleeves have no learned component; adding one is a
  separate project and the interfaces here do not presume it.
- No online/continuous learning. Challengers are trained in discrete scheduled
  runs, frozen for evaluation, and frozen in live use.
- No automatic rollback on live underperformance. A promoted incumbent stays
  until a challenger beats it; live risk is already handled by the drawdown
  breaker.

## 12. Risks & open questions

- **The floor may never be cleared.** On present evidence (best net OOS Sharpe
  −0.44) no challenger passes today, and the honest outcome is that every lane
  runs technical-only indefinitely. That is a *success* of the gate, not a
  failure — it is what should have been happening for the last several weeks.
  The design must not be judged by whether it promotes anything.
- **Shadow periods cost time.** 20 days per candidate means at most ~12
  promotions a year per lane. Deliberate: the alternative is promoting on mined
  evidence.
- **Three lanes means three times the multiple testing.** Each lane's gate is
  independent, so running three lanes triples the chance of a lucky pass. The
  paired bootstrap in §5.2 must account for the number of challengers evaluated
  per lane, in the same spirit as the Deflated Sharpe.
- **More data is a hypothesis, not a guarantee.** The crosses and longer history
  are expected to reduce overfitting, but FX crosses are combinations of the
  majors already in the set, so their marginal information is well below their
  row count. The lane must report the train/validation gap before and after so
  this claim is checked rather than assumed.
- **`intraday_fx` ships disabled unless it can be qualified honestly.** At 60m
  bars a 20-day shadow is ~480 bars, a reasonable sample; but Yahoo intraday
  history is capped near 730 days, which may be too short to train a challenger
  on. Planning measures the available history first. If it cannot meet the same
  bar as the other lanes, the lane ships disabled — it does not ship with a
  weaker gate. `daily_fx` and `multiasset` do not depend on it.

## 13. Phasing (for the implementation plan)

The spec is deliberately larger than one sitting. The order is driven by risk,
not by convenience — the gate lands before anything is allowed to learn its way
into a live book.

**Phase 1 — close the open door.** `promotion.py` with the floor, and the ML
loader reading it. Nothing may reach a live book without clearing the floor.
This alone stops the −0.62 model trading and is worth shipping on its own; every
later phase makes challengers better, but only this one makes them *safe*.

**Phase 2 — make a challenger worth having.** Vol-normalised target, portfolio
Sharpe objective, validation split and early stopping, seed-ensembled
evaluation, capacity reduced against the validation curve, and the data
expansion (crosses + history from 2003-12; verified available). Report the
train/validation gap before and after, so §12's "more data helps" is checked
rather than assumed.

**Phase 3 — the loop.** `lanes.py`, `shadow.py`, paired relative test, forward
confirmation, `fx-learn.yml`, and `fx-paper.yml` reading the promoted incumbent
instead of `--ml`. This is what makes improvement monotonic.

**Phase 4 — visibility.** The LEARNING dashboard panel. Last because the loop is
correct without it, and wrong-but-visible is not better than wrong.

Phase 1 is independently valuable and independently shippable. Phases 2–4 each
assume the phase before it.

## 14. Testing strategy

Offline and deterministic (CI has no network), using committed fixtures:

- **The floor actually blocks.** A challenger with negative net OOS Sharpe is
  refused, and the lane falls back to technical-only.
- **A losing challenger cannot win on relative alone.** Incumbent −0.62,
  challenger −0.44: the relative test passes, the floor refuses. This is the
  exact scenario the floor exists for and is pinned as a test.
- **Paired, not unpaired.** Two series with identical means but different
  windows must not register as a win.
- **Shadow cannot be short-circuited.** A challenger that qualifies offline is
  not promoted until `SHADOW_PERIODS` have elapsed.
- **"No champion" is a working state.** With no bundle, the lane produces the
  same signals as the five technical agents alone — bit-for-bit.
- **Synthetic refuses to promote** (invariant #5).
- **Regression on the measured numbers.** The vol-normalisation result (crypto
  96.3% → 26.4% of the loss) is pinned, so a future change to the target that
  silently reintroduces the imbalance fails the suite.
