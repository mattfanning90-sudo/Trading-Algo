# Backtest Validation: Curve-Fitting, Regime Change, Look-Ahead, Win Rate & Stress Testing

Research synthesis behind the `validate.py` / `robust.py` / `tradestats.py` /
`stress.py` modules. The goal is to close the gap between a backtest and live P&L —
the gap where most strategies die. Every claim below is cited; formulas are the
ones actually implemented in this repo.

> The harsh base rate: a study of alternative-beta strategies found a **median
> ~73% deterioration in Sharpe** between backtest and live trading. Large
> in-sample/out-of-sample gaps are the signature of overfitting, not edge.
> (Alpha Architect, *Avoid Complexity and Magical Backtests*.)

---

## 1. Curve-fitting / overfitting — quantify it, don't eyeball it

Modern compute lets you test millions of configs, so "best backtest" is almost
always luck. Controls, strongest first:

- **Deflated Sharpe Ratio (DSR)** — Bailey & López de Prado (2014), SSRN 2460551.
  PSR benchmarked against the *expected maximum* Sharpe achievable by luck across
  N trials: `SR₀ = √V·[(1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e))]` (γ = Euler-Mascheroni
  0.5772), then `DSR = Φ((SR̂−SR₀)·√(T−1)/√(1−skew·SR̂+((kurt−1)/4)·SR̂²))`. Accept
  only DSR ≳ 0.95. Implemented in `robust.deflated_sharpe_ratio`.
- **Probabilistic Sharpe Ratio + Min Track Record Length** — same authors. PSR =
  `Φ((SR̂−SR*)·√(n−1)/√factor)`; MinTRL tells you whether your sample is even long
  enough to trust the Sharpe. `robust.probabilistic_sharpe_ratio`, `…min_track_record_length`.
- **Probability of Backtest Overfitting (PBO) via CSCV** — Bailey, Borwein, LdP &
  Zhu (2017), SSRN 2326253. Over all ways to split the T×N performance matrix into
  in-sample/out-of-sample halves, PBO = fraction where the IS-best config lands
  below the OOS median. PBO ≳ 0.5 ⇒ selection is a coin-flip. `robust.pbo_cscv`.
- **Minimum Backtest Length**: `MinBTL < 2·ln(N)/E[max]²` — the more configs N you
  try, the longer the history must be or overfitting is guaranteed.
- **Harvey & Liu (2015)**: with a "factor zoo," a new edge needs **t > 3.0**, not
  2.0; the naive "50% haircut" is wrong (haircut is non-linear in SR and N). Use
  Bonferroni/Holm/BHY multiple-testing adjustments.
- **Robustness over optimization** (this repo's `sweep.py`): a broad *plateau* of
  decent Sharpe across neighbouring parameters = robust; a lone *peak* = fitted
  noise.

## 2. Market regime change — adapt automatically, test per regime

- **Don't predict regimes — build mechanisms that adapt.** Constant-volatility
  targeting "approximately doubles the alpha and Sharpe" of momentum and tames its
  bear-rebound crashes (Daniel & Moskowitz, *Momentum Crashes*, NBER w20439). This
  repo vol-targets in `strategy.vol_target`.
- **Momentum's regime fragility is structural**: in bear-market rebounds momentum
  behaves like a written call (−91% in 1932, −73% in 2009), because past losers
  surge. A long-only momentum sleeve is most fragile to the V-shaped recovery.
- **Trend-following is the negatively-correlated diversifier** (crisis alpha in
  2008/2022) — see `trend.py` and `docs` trend report.
- **No-lookahead regime labels only.** Fitting an HMM/GMM on the *full sample* and
  testing within those labels is itself look-ahead. Use contemporaneous rules —
  200-day MA, realised-vol terciles — computable at t. `stress.regime_conditional`
  uses exactly these.
- **Attribute performance per regime.** A strategy whose entire edge lives in one
  regime is fragile. Refs: QuantStart HMM regime articles; Macrosynergy,
  *Classifying market regimes*; Hamilton (1989).

## 3. Look-ahead bias — the subtle forms and a mechanical audit

Look-ahead routinely manufactures Sharpe > 1.5 that evaporates live (Harris,
*Look-Ahead Bias in Backtests*). Beyond the obvious:

- **Point-in-time data**: restatements, reporting lags, and **retroactive index
  membership** (current S&P 500 applied backward captures pre-addition
  outperformance — ~+1–2%/yr inflation; additions earned ~8.8% abnormal pre-event).
  S&P Global, *PIT vs Lagged Fundamentals*.
- **Survivorship/delisting**: excluding dead names inflates returns ~1–4%/yr;
  Shumway's corrected delisting return is **−55%**, and correcting it erased the
  Nasdaq size effect entirely (Shumway 1997). This repo's `constituents.py` +
  `--point-in-time` address membership; current universes are flagged survivorship-biased.
- **Coding traps**: full-sample normalization/winsorization, `bfill`/future-peeking
  `fillna`, resample-`.last()` misalignment, trading the same bar's close, scalers
  fit before the train/test split. (LuxAlgo; scikit-learn *Common Pitfalls*.)
- **Audit checklist** (run on any backtest):
  1. **Shift test** — lag every signal one extra bar; a real edge degrades
     gracefully, look-ahead vanishes.
  2. **Causality/truncation test** — weights for dates ≤ T must be bit-identical
     when future data is removed. *(This repo enforces it: `tests/test_strategy.py::
     test_compute_targets_no_lookahead`, plus invariant #1 and the single
     `compute_targets`.)*
  3. Execution timing: signal at t → fill at t+1 (enforced in `backtest.py`).
  4. Preprocessing uses only data ≤ t (rolling, never full-sample stats).
  5. PIT fundamentals & index membership; include the delisted graveyard.
  6. Record # trials; compute DSR; use purged + embargoed CV when labels overlap.

## 4. Win rate — necessary but nowhere near sufficient

Win rate trades off against payoff size, so report the **whole panel**
(`tradestats.trade_stats`):

- **Expectancy** = `p·avgWin − (1−p)·avgLoss` — the per-bet edge; the spine.
- **Profit factor** = gross win / gross loss (target > 1.75; **> 3.0 in-sample is
  a curve-fit smell**). Linked identity: `PF = p·R/(1−p)`.
- **Payoff ratio** `R = avgWin/avgLoss`; **breakeven win rate = 1/(1+R)** — show it
  next to the actual win rate to prove an edge exists.
- **Win rate is an estimate** → report **n** (≥30 to mean anything, 100+ to trust)
  and a **Wilson confidence interval**. At n=100 the 95% half-width is ~±7–9pp.
- **Cash isn't a bet**: exclude flat (in-cash) periods from win/loss counts and
  report `% time in market` separately, or the win rate is meaningless.
- Sizing: **fractional Kelly** `f* = p − (1−p)/R`, halved/quartered for estimation
  noise. Also report max consecutive losses, worst period, avg win/loss.
- Refs: Bailey/LdP (PSR for return-level); standard trade-stat literature;
  Wilson (1927) score interval.

## 5. Stress testing — one historical path is one draw

- **Stationary bootstrap** (Politis & Romano 1994, JASA 89:1303) is the primary
  engine: resample in random geometric-length blocks (`p = 1/meanblock`) so
  volatility clustering survives. **IID bootstrap understates drawdown risk** by
  destroying that clustering — never size limits from it. `stress.stationary_bootstrap`.
- **Report distributions, not point estimates**: P5/P50/P95 of CAGR/Sharpe/MaxDD
  over thousands of paths; "95% confident drawdown < X%". `stress.mc_summary`.
- **Drawdown analytics**: Ulcer Index (RMS drawdown — penalizes depth *and*
  duration), time-underwater, **CVaR/Expected Shortfall** = mean loss beyond VaR,
  and the **Magdon-Ismail** zero-drift benchmark `E[MaxDD] ≈ √(π/2)·σ·√T` to judge
  whether observed drawdown is even abnormal. `stress.drawdown_analytics`.
- **Parametric shocks**: scale vol ×2–3, push correlations → 1 (kills
  cross-sleeve diversification in crises), widen costs/slippage. `stress.cost_stress`
  re-derives CAGR/Sharpe at 1×/2×/3× costs exactly — an edge that dies at 2× is
  fragile.
- **Historical replay**: 2008/2020/2022 through the live logic (see trend report's
  crisis-year rows).
- **Forward paper-trading is the only true OOS** — the ultimate check before real
  capital (this repo has a persistent paper-trading engine).

---

## How this maps onto the repo

| Concern | Module | CLI |
|---|---|---|
| Win rate done right | `tradestats.py` | `validate` §1 |
| Deflated/Probabilistic Sharpe, PBO | `robust.py` | `validate` §2 |
| Regime-conditional (no-lookahead) | `stress.regime_conditional` | `validate` §3 |
| Bootstrap MC, drawdown, cost stress | `stress.py` | `validate` §4 |
| Robustness plateau vs peak | `sweep.py` | `sweep` |
| No-lookahead, costs-on | invariants #1–#2 | enforced in tests |

Run: `python -m trading_algo.validate --region US` (real data) or `--synthetic`.

### Key sources
- Bailey & López de Prado, *The Deflated Sharpe Ratio* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest Overfitting* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Harvey & Liu, *Backtesting* / *…and the Cross-Section of Expected Returns* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489 , https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314
- Daniel & Moskowitz, *Momentum Crashes* (NBER w20439) — https://www.nber.org/papers/w20439
- Shumway, *The Delisting Bias in CRSP Data* — https://www.tylergshumway.org/Shumway-DelistingBiasCRSP-1997.pdf
- Politis & Romano, *The Stationary Bootstrap* (JASA 1994) — https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870
- Magdon-Ismail & Atiya, *Maximum Drawdown* — https://www.cs.rpi.edu/~magdon/ps/journal/drawdown_journal.pdf
- Harris, *Look-Ahead Bias In Backtests And How To Detect It* — https://mikeharrisny.medium.com/look-ahead-bias-in-backtests-and-how-to-detect-it-ad5e42d97879
- S&P Global, *Point-In-Time vs. Lagged Fundamentals* — https://www.spglobal.com/content/dam/spglobal/mi/en/documents/general/sp-capitaliq-quantamental-point-in-time-vs-lagged-fundamentals.pdf
