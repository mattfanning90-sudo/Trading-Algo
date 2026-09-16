# Result: does the cost-aware neural objective move net out-of-sample Sharpe?

**Short answer: no. The verdict is a credible null.**

Phase 2 of the cost-aware-objective work rebuilt the FX neural agent's training
objective end to end: a vol-normalised target, a loss that is the Sharpe of the
*portfolio* return series *net of turnover cost* (cost inside the loss, analytic
gradient verified against finite differences to 2.5e-10), per-fold panel indices,
a contiguous purged validation slice with real early stopping, seed-ensembled
grading, and a deployed artefact that follows the graded recipe. The universe
went from 10 symbols/2015 to 16 symbols/2003-12-01.

The question this document answers is the only one that matters: **does any of
that push net out-of-sample Sharpe across zero?** It does not. The cost-aware
objective reduces turnover by 13% and makes net Sharpe *worse*, not better — and
on gross, neither it nor the objective it replaced is distinguishable from a
correctly-matched null.

---

## 1. How this was measured

Every row below was re-run on the **same current data**. The earlier figures in
the plan (gross +0.60 / net −0.44) were measured on 10 symbols from 2015; pasting
a 16-symbol/2003 number beside them would credit a *data* change to the
*objective*. Those old numbers appear only in §8, clearly separated.

| | |
|---|---|
| universe | the 16 registered symbols (7 FX majors, 6 crosses, 3 crypto) |
| history | 2003-12-01 → 2026-09-16, 7,194 union-calendar bars |
| pooled dataset | 94,787 rows × 19 features, 6,690 unique timestamps |
| walk-forward | 6 anchored folds, purge = 1 bar label horizon, embargo = 5, `min_train` = 400 |
| validation | last 20% of each fold's training timestamps, contiguous, purged by the same gap |
| training | full batch, lr 1e-2, epoch cap 400, early-stopping patience 25 |
| ensemble | 3 seeds, averaged into one signal panel (the artefact is also a 3-seed ensemble) |
| scored window | **2010-02-19 → 2026-09-15** — the bars every configuration covers out-of-sample (80,931 of 94,787 rows; fold 0 is unscorable by construction), 5,575 daily bars |

Only the named thing varies between rows. Rows 1→2 differ **only** in the target
(raw forward return vs the same return divided by trailing realised vol); rows
2→3 differ **only** in the objective (`task="sharpe"`, the pooled per-row Sharpe,
vs `task="sharpe_net"`, the net portfolio Sharpe with turnover in the loss). Fold
geometry, seeds, features, capacity, learning rate, patience, batching and the
scoring function are identical throughout, and every row routes through the same
`ml_backtest.strategy_returns` (invariants #1, #2, #3). Real Yahoo data, not
`--synthetic` (invariant #5).

"Turnover" is the sum of |Δposition| over all pairs and all bars in the scored
window — the same quantity in every row, so it is comparable across rows even
though its absolute magnitude depends on the window length and the column count.

---

## 2. The null, computed first

A result is only readable against its null, so the null was computed before any
configuration was read.

**Null A — random signal, identical pipeline.** An i.i.d. Uniform(−1, 1) signal
placed on exactly the cells the model predicts out-of-sample, pushed through the
identical `strategy_returns` scorer, 200 draws:

> **gross Sharpe −0.01 ± 0.21** (5th–95th pct: −0.35 to +0.31)
> net Sharpe −4.12 ± 0.19 at turnover 53,953

Theory says a random signal earns zero gross Sharpe. It does. **The metric is
sound.** Its net Sharpe of −4.1 is the cost of trading randomly every bar and is
not a defect: it is the bill this whole exercise is about.

**Null B — turnover-matched phase randomisation.** Null A trades far more than
the model does, so it cannot say whether a given model's score is explained by
its *timing*. Null B takes each configuration's own out-of-sample signal panel,
circularly shifts each pair's column within the scored window, and flips its sign
with probability ½. Position sizes, autocorrelation and turnover are preserved
exactly; alignment with the returns is destroyed; the sign symmetry makes the
expected gross Sharpe exactly zero. 200 draws per configuration:

| configuration | its matched null, gross Sharpe | model gross Sharpe | z |
|---|---|---|---|
| raw target, pooled objective | +0.003 ± 0.362 | +0.200 | **+0.55** |
| vol-normalised, pooled objective | −0.001 ± 0.319 | +0.352 | **+1.11** |
| vol-normalised, cost-aware objective | −0.025 ± 0.284 | −0.469 | **−1.56** |

Three independent nulls, all centred within 0.03 of zero. The metric is sound at
each configuration's own turnover and position structure, not just in the
abstract.

**Null C — permuted labels through the full pipeline.** The strongest leakage
test: permute the target within each pair's own timeline and train the *identical*
cost-aware walk-forward on it (3 seeds, 6 folds). It scores gross −0.22 / net
−0.78. No configuration beats it on net. There is no leakage to find, and no
signal either.

---

## 3. The comparison table

All rows on the current data, same window, same geometry. **Sharpe is the
headline; see §7 before reading CAGR or MaxDD.**

| configuration | gross Sharpe | **net Sharpe** | turnover | ann. gross | ann. cost | ann. net |
|---|---|---|---|---|---|---|
| raw target, pooled objective | +0.20 | **−1.19** | 21,893 | +0.89% | 6.39% | −5.50% |
| vol-normalised, pooled objective | +0.35 | **−1.16** | 16,218 | +1.22% | 5.42% | −4.19% |
| vol-normalised, **cost-aware** objective | −0.47 | **−1.41** | 14,185 | −2.55% | 5.26% | −7.81% |
| permuted-label null (full pipeline) | −0.22 | −0.78 | 13,652 | — | 3.25% | — |
| random signal (Null A) | −0.01 ± 0.21 | −4.12 ± 0.19 | 53,953 | — | — | — |

PSR = 0.00 and DSR = 0.00 for every configuration: all three net Sharpes are
negative, so neither statistic has anything to deflate.

The repo's own end-to-end run agrees. `python -m trading_algo.forex.train
--seeds 3 --folds 6` scores the same model on the whole series rather than the
scored window (which dilutes a negative Sharpe toward zero by the flat pre-OOS
period) and reports:

```
=== FX strategy comparison (out-of-sample, costs on) ===
strategy        Sharpe     CAGR    MaxDD    PSR    DSR
breakout          0.24    1.57%  -27.08%   0.90   0.00
carry             0.13    0.17%   -6.41%   0.76   0.00
trend             0.12    0.44%  -32.05%   0.73   0.00
ens_equal         0.07    0.18%  -27.70%   0.64   0.00
momentum         -0.12   -1.12%  -62.15%   0.26   0.00
ens_hedge        -0.43   -1.56%  -46.30%   0.01   0.00
meta_oos         -0.75   -0.37%  -10.25%   0.00   0.00
neural_oos       -1.23   -5.96%  -83.66%   0.00   0.00
meanrev          -1.80   -2.09%  -46.00%   0.00   0.00

Strategies compared (N): 9
Probability of Backtest Overfitting (PBO): 0.34

Promotion floor: REFUSED — net out-of-sample Sharpe -1.23 does not clear the floor of +0.00
  The live books will run the 5 technical agents only.
```

The gate does its job: the model is refused and the live books run the five
hand-written technical agents. Four of those five score above the neural model.

---

## 4. What changed

**The objective did what it was asked to do.** Turnover fell monotonically as the
cost entered the problem — 21,893 (raw target) → 16,218 (vol-normalised target)
→ 14,185 (cost in the loss) — a 13% reduction from the objective change alone,
on top of the 26% the vol-normalised target already bought. Average absolute
position rose from 0.16 to 0.27 over the same step: the cost-aware model holds
**larger, more persistent** positions and rebalances them less often, which is
exactly the behaviour a turnover penalty is supposed to induce. The mechanism
works.

**The vol-normalised target improved gross Sharpe**, +0.20 → +0.35, and reduced
turnover 26%. It is the one change in this programme that moved the gross number
in the right direction — though at z = +1.11 against its own matched null it is
not significant either.

**Early stopping is live and is finding no descent phase.** The endorsed epoch
counts on real data are tiny and seed-dependent: per fold at seed 0, [1, 12, 1,
6, 18] for the cost-aware objective and [44, 8, 1, 2, 91] for the pooled one; the
deployed bundle's three seeds chose 35, 58 and 167. The 400-epoch cap is never
reached. Train/validation, averaged over the five scored folds, in Sharpe units
of each objective's own loss:

| objective | train @ stop | val @ stop | gap @ stop | train @ cap | val @ cap | gap @ cap |
|---|---|---|---|---|---|---|
| pooled, raw target | +0.70 | +0.21 | 0.50 | +3.05 | +0.07 | 2.98 |
| pooled, vol-normalised target | +0.99 | +0.24 | 0.75 | +3.09 | +0.40 | 2.69 |
| cost-aware | +0.31 | **−1.81** | 2.13 | +8.44 | −2.21 | 10.65 |

The validation split is doing real work: it collapses the train/validation gap
from ~2.7–3.0 to 0.5–0.75 on the pooled objective, and it is the reason the
reported number is honest rather than the 3.09/0.40 fantasy the uncapped fit
produces. It also states the result in advance — **on the cost-aware objective
the held-out net portfolio Sharpe is −1.81 at the best epoch that exists.** The
walk-forward's −1.41 is not a surprise; the validation curve already said there
is no epoch at which this model earns its costs.

## 5. What did not change

**Net Sharpe did not cross zero, and did not move toward it.** −1.16 → −1.41. The
cost-aware objective bought a 3% reduction in the annual cost bill (5.42% →
5.26%) and paid for it with the entire gross signal (+0.35 → −0.47 Sharpe, +1.22%
→ −2.55% p.a.). Trading less is not the same as trading better: the model
concentrated into fewer, longer-held positions, and those positions have no gross
edge.

**No configuration's timing is significant.** None clears 2σ on gross against its
own turnover-matched null (§2, Null B); the cost-aware one sits at −1.56σ, i.e.
*below* a phase-randomised version of itself (6th percentile of 200 draws). On
net, no configuration beats the permuted-label null. On the repo's own scale,
PSR = 0.00 and DSR = 0.00 everywhere.

**How much is the timing worth at all?** Hold the cost bill fixed at the model's
own and randomise only the timing (Null B's gross series minus the model's real
cost series): a no-timing signal would lose the full bill, 5.42% p.a., where the
vol-normalised pooled model loses 4.19% p.a. **That 1.22% p.a. is the entire
measurable value of this model's timing**, and it is the same +1.22% gross in the
§3 table. Stated as a Sharpe the same comparison reads −2.46 vs −1.16 (z = +2.4),
but roughly a third of that gap is the mean and two thirds is a volatility
difference — the model's gross is strongly *negatively* correlated with its own
cost, so its net series is more volatile than the counterfactual's and its ratio
flatters it. That is why the null test in §2 is stated on gross, where no such
interaction exists. For the cost-aware configuration the same comparison is
−1.58 vs −1.41, z = +0.38: no measurable timing value at all.

**The arithmetic, not the statistics, is the binding constraint.** The best gross
number any configuration produced is +1.22% p.a. The cost bill is 5.26–6.39% p.a.
The bill is four to five times the edge. No reweighting of a loss function closes
a gap of that shape.

---

## 6. Verdict

**The cost-aware objective does not move net out-of-sample Sharpe across zero.
It moves it from −1.16 to −1.41 — the wrong way — and the promotion floor
correctly refuses the model. This is recorded as a null result and no parameter
was tuned in pursuit of a positive number.**

The objective is correctly specified: the gradient is verified, the cost is the
one every other cost path in the project derives from, the validation block is
purged and contiguous, the grade is seed-ensembled, and the deployed artefact
follows the recipe its grade describes. A correctly-specified objective returning
a credible null is the result this repo is built to produce, and it is worth more
than a tuned positive would have been. The trial count for this comparison is
five configurations; deflating a Sharpe that never reached zero is moot.

What this does *not* say: it does not say the loss is wrong, it does not say
deep learning cannot work on FX, and it does not say the engineering of Phase 2
was wasted — the turnover response is clean, early stopping is real, and the
train/validation gap is now measured rather than assumed. It says that at a daily
bar, on this universe, with this cost model, a 19-feature MLP has no edge left
after turnover, and that the honest instrument for finding one is a change to the
*data or the horizon*, not another pass at the objective.

---

## 7. Caveats — read these before quoting any number above

**7.1 CAGR and MaxDD are structurally understated; only Sharpe is comparable.**
`strategy_returns` averages across all 16 columns after filling absent ones with
zero, so the book is scaled by the fraction of columns that exist yet: 12/16 in
2005, 13/16 to 2014, 14/16 to 2017, 15/16 to 2019, 16/16 from 2020. Sharpe is
invariant to *constant* scaling, but this scaling is time-varying, so it is only
approximately invariant — while CAGR and MaxDD are understated outright. **Do not
present a pre-2020 equity curve from this measurement as comparable to a
post-2020 one.** Every row shares the identical scaling, so the comparison between
rows is valid; the absolute CAGR/MaxDD figures are not.

**7.2 The scorer charges cost on a different denominator than gross.** In
`strategy_returns`, `gross` is `(pos * rets).mean(axis=1)` over NaN-free frames,
so it divides by 16 always; `cost` is `(turn * half_spread).mean(axis=1)` where
`half_spread` is NaN before a symbol lists, so pandas' skipna divides by the
number of *live* columns. Cost is therefore over-weighted relative to gross
whenever a column is missing. Measured: 5.259% p.a. as scored versus 4.712% on a
uniform ÷16, an 11.6% overstatement, which moves the cost-aware net Sharpe from
−1.407 to −1.315. Pre-existing, applies identically to every row, and does not
change any conclusion — but it should be fixed.

**7.3 Sixteen columns are not sixteen independent bets.** The six crosses are
combinations of the majors, so the effective sample size rises far less than the
row count suggests. Measured on the 2,350 bars where all 16 are live: six
principal components carry 90% of return variance, participation ratio 5.45. On
the 13 FX columns alone: six PCs for 90%, participation ratio 4.42, mean absolute
pairwise correlation 0.33. The panel is roughly **five to six independent bets**,
not sixteen. The 94,787 pooled rows should be read accordingly.

**7.4 Crypto has no pre-2010 history, so the early book is FX-only.** BTCUSD
starts 2014-09-17, ETHUSD 2017-11-09, SOLUSD 2020-04-10, and AUDUSD only
2006-05-16 on this feed. The 2010–2014 portion of every row is a 13-column FX
book wearing a 16-column denominator. There is no year in this measurement for
which the "16-symbol book" existed as described except 2020 onward.

**7.5 The cost bill is 96% crypto, and the crypto spread model is a fixed dollar
amount.** For the cost-aware configuration, the three crypto columns carry 953 of
14,185 turnover units (6.7%) and 4.52 of 4.71 percentage points of annual cost
(96%); the 13 FX columns carry 93% of the turnover and 4% of the cost. The reason
is that `Pair.spread_pips × pip` is a constant dollar spread — $120 for BTCUSD —
applied across a 400× price range, so the half-spread *as a fraction of price*
was 2,410 bps in 2015 and 5.8 bps in 2025. Annual cost by year for this book:
0.23–0.37% through 2015, then **45.5% in 2016 and 14.7% in 2017**, then back
under 5.6%. Two years supply roughly two thirds of the entire seventeen-year cost
bill. That is a modelling artefact, not a market fact, and it dominates the
magnitude of every net number in §3.

**7.6 …but the artefact does not rescue the verdict.** The same three signal
panels, scored as an equal-weight book over the 13 FX columns only — a
**diagnostic slice, not a configuration, and not to be quoted as performance** —
give net Sharpe −0.32 (raw/pooled), +0.12 (vol/pooled) and +0.10 (cost-aware), on
annual gross of −0.14%, +0.39% and +0.40% against annual cost of 0.36%, 0.26% and
0.24%. Removing the crypto cost artefact entirely moves the result from *clearly
negative* to *indistinguishable from zero* — inside a null band of ±0.3 Sharpe —
and the cost-aware objective still does not beat the pooled one (+0.10 vs +0.12).
The verdict is unchanged.

**7.7 Annualisation is at 252 on a 328-bar calendar.** The union calendar is
crypto's 7-day one, so FX columns are forward-filled across weekends and the
median year holds 328 bars, while `marks.periods_per_year` returns the project's
252 convention for daily spacing. Every Sharpe here is therefore about 0.88× a
calendar-time annualisation. This is a deliberate project convention, it applies
identically to every row *and to the null*, and it cannot move a number across
zero.

**7.8 The seed ensemble averages models at very different fit depths.** The three
deployed seeds stopped at 35, 58 and 167 epochs, and per-fold counts range from 1
to 91. This was flagged as a concern at the end of Task 7 and the real-data run
confirms it. It is a property of a validation curve with no descent phase, not a
wiring defect, but it means "the model" is an average over materially different
models.

---

## 8. Previously measured, on different data — not comparable

For the record only. These figures come from an earlier measurement on **10
symbols starting 2015**, and the two data regimes differ in universe, history
length, cost mix and column scaling. They may not be read against §3.

| configuration (10 symbols, from 2015) | gross Sharpe | net Sharpe | turnover |
|---|---|---|---|
| raw target, pooled objective | +0.45 | −0.65 | 7,934 |
| vol-normalised, pooled objective | +0.60 | −0.44 | 7,934 |

The direction of the difference is instructive even though the levels are not
comparable: adding six years of history, six crosses and three crypto columns made
the net number substantially worse, almost entirely through the crypto cost model
of §7.5. That is a data effect, not an objective effect, and separating the two is
the reason every row in §3 was re-run.

---

## 9. Reproducing this

```bash
# the repo's own end-to-end run — the numbers in the §3 code block and the floor verdict
python -m trading_algo.forex.train --seeds 3 --folds 6 --out after.md
```

The controlled four-row comparison is that same machinery with one knob changed
per row, all public functions, no new dependencies:

* dataset — `ml_agent.pooled_dataset(panel, p, label="sharpe", horizon=1)`, once,
  shared by every row. The raw-target row multiplies `y` back by the returned
  `trailing_vol`, which inverts the normalisation exactly and keeps the row set
  identical.
* walk-forward — `walkforward.walk_forward_predict(...)` with
  `n_folds=6, label_horizon=1, embargo=GRADED_EMBARGO, min_train=400,
  val_frac=GRADED_VAL_FRAC` and
  `fit_kwargs={"epochs": GRADED_EPOCHS, "batch_size": 10**9, "lr": GRADED_LR,
  "patience": GRADED_PATIENCE}`, run once per seed and averaged.
* objective — `ml_backtest._sharpe_factory(n_feat, seed, cost_aware=...)`;
  `cost_aware=True` additionally passes `index_factory` built from
  `panel_index.build_panel_index` with `ml_backtest._half_spreads(px, upto=<the
  block's own last bar>)`.
* scoring — `ml_backtest.strategy_returns`, decomposed into its `gross`, `cost`
  and `turnover` parts. The decomposition was asserted equal to
  `strategy_returns` to 1e-15 on every row before any number was read.

