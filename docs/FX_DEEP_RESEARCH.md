# Deep Research → Design: the FX Deep-Learning Layer

This document records the research that shaped the FX deep-learning subsystem
(`trading_algo/forex/`) and maps each finding to a concrete implementation
decision. It synthesises five parallel literature reviews (deep architectures,
robust validation, ensembles & RL, FX edges, and model-design/hyperparameters).
Every design choice below is deliberately the *boring, robust* one the evidence
supports — the dominant failure mode in this field is overfitting, not
under-modelling.

> **Honesty note.** Daily G10 FX is close to a random walk (Meese–Rogoff), and
> the classic style-factor Sharpes collapsed after 2008 (carry ~1.08 → ~0.25;
> combined carry/momentum/value out-of-sample +0.39 → −0.32). Expect *thin,
> regime-dependent* edges. This system is built to *measure honestly* whether a
> learned edge exists (deflated Sharpe, PBO), not to manufacture one.

---

## 1. Architecture: why a regularized MLP, not an LSTM/Transformer

**Finding.** On noisy, data-limited, daily financial series, simpler models
match or beat deep sequence models, which overfit temporal noise:

- A single linear layer beats Informer/Autoformer/FEDformer by 20–50% on the
  standard long-horizon benchmarks (DLinear, *Are Transformers Effective for Time
  Series Forecasting?*, AAAI 2023, [arXiv:2205.13504](https://arxiv.org/abs/2205.13504)).
- A vanilla LSTM beats multi-head attention, the Temporal Fusion Transformer,
  Informer and TCN on **daily-resolution, data-limited stock prediction** — and
  gives steadier signals (*StockBot 2.0*, Jan 2026, [arXiv:2601.00197](https://arxiv.org/abs/2601.00197)).
- A well-regularized **MLP** beats XGBoost/TabNet/NODE across many *tabular*
  datasets (Kadra et al., *Regularization is all you Need*, [arXiv:2106.11189](https://arxiv.org/abs/2106.11189));
  shallow nets (~3 layers) beat deeper ones on noisy asset-pricing data
  (Gu, Kelly & Xiu, *Empirical Asset Pricing via Machine Learning*, RFS 2020,
  [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3159577)).

**Decision.** `nn.py` is a compact **MLP** (1–2 hidden layers, ~16–32 units) on
*engineered tabular features*. Pure NumPy — no torch/tensorflow — so it runs in
CI and offline, honouring the project's zero-heavy-dependency invariant. Sequence
models were explicitly *not* adopted: the evidence says they would add overfitting
risk, not edge, in this regime.

## 2. The objective: a differentiable Sharpe loss (output a position, not a forecast)

**Finding.** Training a network to *output a position* and maximising a
differentiable **Sharpe-ratio loss** beats both MSE-regression of returns and
binary direction-classification:

- Lim, Zohren & Roberts, *Deep Momentum Networks* (J. Financial Data Science
  2019, [arXiv:1904.04912](https://arxiv.org/abs/1904.04912)): Sharpe-loss LSTM ≫
  MSE-loss ≫ classification-loss; the net outputs a trend/position directly and
  embeds vol scaling, with a turnover penalty for costs.
- Moody & Saffell, *Learning to Trade via Direct Reinforcement* (NeurIPS 1998 /
  IEEE TNN 2001): the **differential Sharpe ratio** as a differentiable utility;
  position-output beats MSE-forecasting.

**Decision.** `MLP` has a first-class **`task="sharpe"`**: a `tanh` position
output trained on `loss = −Sharpe(positionₜ · forward_returnₜ)`. The output-layer
gradient is derived analytically and pinned by a finite-difference gradient check
(`tests/test_fx_nn.py`). The `NeuralAgent` uses this — it emits a position in
[−1, 1] exactly like the five technical agents, so it drops straight into the
ensemble. (Turnover regularization is handled by the cost-aware backtest and the
no-churn band rather than inside the loss, to keep the loss convex-ish and
gradient-checkable.)

## 3. Features: few, economically grounded, leakage-safe

**Finding.** Daily FX ≈ random walk; standard oscillators (RSI/MACD/stochastics)
are largely *reparameterizations of returns* and add overfitting surface, not
orthogonal signal (Meese–Rogoff, [VoxEU](https://cepr.org/voxeu/columns/can-we-predict-exchange-rates-economic-evidence-against-random-walk-model);
FX-ML reviews). The durable, economically-grounded predictors are **time-series
momentum (esp. 12-month)**, **carry (rate differential)**, **value (PPP, slow)**,
and **realized vol as a risk feature**. Normalization must be fit on training data
only; global z-scoring leaks the future (RevIN, ICLR 2022,
[OpenReview](https://openreview.net/forum?id=cGDAkQo1C0p); leakage guides).

**Decision.** `features.py` builds a lean (~20) causal set: multi-horizon returns
(1d…**252d**), two MA-distance trend proxies, a **value/PPP** long-window z-score,
a per-pair **carry** proxy (informative once pairs are pooled), realized vol, and
a few regime indicators (ADX/ATR/Bollinger/Donchian). Every value at t uses data
≤ t (causality test in `tests/test_fx_ml.py`). The `StandardScaler` is fit on
each walk-forward fold's training rows only (`walkforward.py`).

## 4. Pooling: one global cross-pair model

**Finding.** Global models trained across many related series (DeepAR,
[arXiv:1704.04110](https://arxiv.org/abs/1704.04110)) and cross-sectional
asset-pricing nets (Gu–Kelly–Xiu) exploit far more data than per-series models —
critical when per-series signal is tiny.

**Decision.** `ml_agent.pooled_dataset` stacks all seven pairs into one training
set; the carry feature lets the single model distinguish pairs cross-sectionally.
Seed-ensembling (several MLPs from different inits, averaged) cuts the
high-variance of NN training (`ModelBundle`).

## 5. Validation: purge, embargo, walk-forward, and *deflated* Sharpe

**Finding.** Standard k-fold leaks in time series (overlapping forward-looking
labels); you must **purge** overlapping train rows and **embargo** a gap after
each test fold. A single backtest Sharpe is meaningless without correcting for
track length, non-normality and *how many strategies you tried*:

- López de Prado, *Advances in Financial Machine Learning* (2018) — purging,
  embargo, CPCV, meta-labeling, triple-barrier, sample uniqueness.
- Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014,
  [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)) and
  *The Probability of Backtest Overfitting* (2015,
  [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)).
- *Minimum Backtest Length*: with ~5y of data, trying >~45 configs almost
  guarantees a spurious in-sample Sharpe of 1.

**Decision.** `walkforward.walk_forward_predict` does expanding walk-forward with
purge (= label horizon) + embargo; predictions are strictly out-of-sample (a
no-lookahead test perturbs late rows and asserts earlier predictions are
unchanged). `validation.py` implements **PSR**, the **Deflated Sharpe Ratio**
(deflated by the expected-max-Sharpe across N trials), and **PBO** via CSCV.
`ml_backtest.py` reports all of them for every strategy, so "the DL found an edge"
is a falsifiable claim, not a hope.

## 6. Ensemble: Hedge (multiplicative weights), not a heavy meta-learner

**Finding.** The robust way to combine experts adaptively is the **Hedge /
multiplicative-weights** algorithm (Cesa-Bianchi & Lugosi, *Prediction, Learning,
and Games*): `wᵢ ∝ exp(−η·cumulative_lossᵢ)`, with regret `O(√(T·ln N))`
— logarithmic in the number of agents, minimal parameters, hard to overfit. The
1/N portfolio is famously hard to beat (DeMiguel, Garlappi & Uppal 2009), so
anchor to it (a fixed-share floor). Heavy meta-learner *stacking* over agent
outputs is expressive but overfit-prone on limited FX data.

**Decision.** `ensemble.py` adds `agent_weighting="hedge"` (now the default):
multiplicative weights over a trailing window of bounded agent losses, with a
**fixed-share floor** so a temporarily-bad agent recovers, and a causal rolling
scale (a full-sample scale would leak — caught and fixed during development).

## 7. Meta-labeling: separate *side* from *size*

**Finding.** Meta-labeling (López de Prado) lets a secondary classifier decide
*whether to act* on the primary signal and *how big*, trained on **triple-barrier**
outcomes (which barrier — profit/stop/time — is hit first, ATR-scaled). Bet size
maps the meta-probability through `size = 2·Φ((p−0.5)/√(p(1−p))) − 1`. It raises
precision but **cannot manufacture alpha** if the primary has none.

**Decision.** `features.triple_barrier_labels`, the binary meta-model, and
`MetaLabeler` (with `validation.bet_size_from_prob`) size the ensemble's side
without flipping it. Evaluated walk-forward in `ml_backtest` (`meta_oos`).

## 8. Sizing: volatility targeting, not reinforcement learning

**Finding.** For *sizing* a blend of a few agents, vol-targeting is a strong,
near-zero-overfitting baseline that RL rarely beats; deep RL for trading suffers
sample-inefficiency, non-stationarity and reward-hacking, and its published wins
are mostly optimistic single-path backtests (FinRL [arXiv:2011.09607];
CFA Institute 2025 RL chapter; DRL overfitting [arXiv:2209.05559]).

**Decision.** Sizing stays the volatility-targeting risk layer (`risk.py`) with
per-pair and gross-leverage caps and a drawdown breaker. **RL was deliberately
not added** — the honest, evidence-based call.

## 9. Costs & risk realism

**Finding.** Conservative all-in spreads ~0.8–2.5 pips by pair; **swap/financing
dominates multi-week P&L**; model costs pessimistically and report net, with
turnover. Use vol-targeting + fractional Kelly + a drawdown breaker.

**Decision.** Costs are always on (half-spread per unit turnover + daily carry);
the comparison report is net-of-cost; the backtest has a drawdown circuit breaker.

---

## How to reproduce

```bash
python -m trading_algo.forex.train --synthetic     # offline pipeline check
python -m trading_algo.forex.train --out report.md # real Yahoo FX (needs internet)
```
Or run the **FX Deep-Learning Train & Evaluate** GitHub Action (real data in the
cloud; report → run Summary, models → artifact).

## Key sources

Deep architectures: DLinear (AAAI 2023, arXiv:2205.13504); PatchTST (ICLR 2023,
arXiv:2211.14730); StockBot 2.0 (arXiv:2601.00197); Gu/Kelly/Xiu (RFS 2020);
Kadra et al. (arXiv:2106.11189); DeepAR (arXiv:1704.04110); RevIN (ICLR 2022).
Objective: Lim/Zohren/Roberts (arXiv:1904.04912); Moody/Saffell (NeurIPS 1998).
Validation: López de Prado, *AFML* (2018); Bailey & LdP, Deflated Sharpe (SSRN
2460551) and PBO (SSRN 2326253). Ensembles: Cesa-Bianchi & Lugosi (2006);
DeMiguel/Garlappi/Uppal (2009); OLPS survey (arXiv:1212.2129). FX edges:
Menkhoff et al. (JFE 2012); Moskowitz/Ooi/Pedersen (JFE 2012); Asness/Moskowitz/
Pedersen (JF 2013); Brunnermeier/Nagel/Pedersen (NBER 2008); Meese–Rogoff.

*(URLs and the full per-stream findings are in the PR description / commit
history. WebFetch was network-restricted during research, so specific decimal
figures were taken from search summaries and should be re-verified against the
primary PDFs before being quoted as performance.)*
