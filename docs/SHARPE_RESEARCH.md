# Deep Research → Design: The Sharpe Ratio in This System

This document records the research behind how this repo measures risk-adjusted
return, and maps each finding to a concrete decision. Companion to
`obsidian/Concepts/Sharpe Ratio.md` (the plain-English explainer),
`docs/MONTE_CARLO_RESEARCH.md` (significance vs path risk) and
`docs/PERMUTATION_TESTING.md` (the null).

Every number below was measured on **our own** return series before being
written: the four funded sleeves run offline against the cached 2012–2026 price
panel (`trading_algo/.cache`, 3,797 daily observations, 15.1 years), plus the
eight live paper books read off their own state files. Nothing here is quoted
from the literature as if it were our result.

> **This document spans a change it caused.** §1b found that the simulator paid
> 0% on idle cash while the metric charged a full cash-rate hurdle, and that is
> now fixed in `backtest.py`. So the tables below split two ways. **Level-dependent
> sections are restated post-change**: §3 (precision) and §9 (the gate). **Structure-
> dependent sections are unaffected and remain as first measured**: §2
> (autocorrelation), §5 (the FX tick), §6 (cross-sleeve correlation), §8 (costs) —
> crediting a constant on idle cash shifts means, not correlations, serial
> dependence or cost ratios. §1's four-convention table is deliberately left
> pre-change: it is the evidence for making the fix.

> **Honesty note.** The Sharpe ratio is the most over-trusted number in this
> field. It is a *t*-statistic in a costume — and like any *t*-statistic, it is
> worthless without its standard error, its sample size, and a count of how many
> times you looked. Three of the findings below are the arithmetic saying our
> own headline numbers are less certain than they look. One is a data bug the
> statistic exposed by accident. That is the expected yield.

---

## 1. Which Sharpe? This repo computes four, and they disagree by 0.45

**Finding.** "The Sharpe ratio" is not one definition. Sharpe's own 1994
revision defines it against a *benchmark* return, with the ex-ante and ex-post
forms sharing an equation but not an interpretation. Four choices are live in any
implementation: whether to subtract a risk-free rate, whether the numerator is an
arithmetic or geometric mean, `ddof=0` vs `ddof=1`, and the annualisation factor.

This repo makes those choices **differently in two places**:

- [validation.py:79](../trading_algo/validation.py#L79) — `mean/std`, no
  risk-free, numpy `.std()` (`ddof=0`). This feeds PSR, DSR and the haircut.
- [metrics.py:33](../trading_algo/metrics.py#L33) — arithmetic `mean × 252`
  minus `RISK_FREE`, over pandas `.std()` (`ddof=1`). This feeds the dashboard,
  the tearsheet and every backtest report.

**Measured on the real 2012–2026 panel** (annualised, same series each row):

| series | n | `validation` (no rf) | `ddof=1` | `metrics` (−3.5%) | geometric (−3.5%) | spread |
|---|---|---|---|---|---|---|
| **PORTFOLIO (AUD)** | 3797 | **0.806** | 0.806 | **0.376** | 0.360 | **0.446** |
| ASX (local) | 3725 | 0.652 | 0.652 | 0.142 | 0.121 | 0.531 |
| US (local) | 3698 | 0.792 | 0.792 | 0.425 | 0.404 | 0.388 |
| FTSE (local) | 3715 | 0.304 | 0.304 | −0.138 | −0.175 | 0.478 |
| TSX (local) | 3692 | **0.948** | 0.948 | **0.484** | 0.478 | 0.470 |

**Three things fall out of that table.**

1. **The risk-free rate is the whole argument.** At `RISK_FREE = 0.035` and ~8%
   vol, subtracting cash costs ~0.43 Sharpe. The FTSE sleeve is *positive* under
   one convention and *negative* under the other. Same returns, opposite verdict.
2. **`ddof` is a non-issue.** Identical to three decimals at n≈3,700, as it must
   be — the correction is `√(n/(n−1))`. Worth recording as a measured **null**:
   do not spend effort unifying it.
3. **`CLAUDE.md`'s TSX figure is the no-rf number.** "raw Sharpe 0.948" is the
   `validation` convention; the dashboard would print **0.484** for the same
   sleeve. Both are defensible, neither is labelled.

### 1b. Why we could not simply standardise, and what we fixed first

The obvious answer is to adopt the canonical definition everywhere — Sharpe's
1994 revision, `SR = E[Rₐ − R_b] / σ(Rₐ − R_b)`, excess return over a benchmark
with σ taken of the *differential*. Two of the four choices turn out not to be
choices at all:

- **`ddof` is immaterial** (measured above).
- **The denominator question is moot while the rate is a constant.** Subtracting
  a constant cannot change a standard deviation; measured difference on all four
  sleeves is exactly `0.0e+00`. It only bites with a rate *series*.

So there was only one real decision — subtract a cash rate or not — and it was
blocked by a simulator asymmetry: **`RISK_FREE` appeared only in reporting
(`metrics.py`, `tearsheet.py`, `dashboard.py`) and never in the simulation.
Idle cash earned 0%.** And these sleeves are mostly idle:

| sleeve | mean gross | idle | fully-cash days | Sharpe −rf (cash at 0%) | …with idle cash paid | penalty |
|---|---|---|---|---|---|---|
| ASX | 0.340 | 66% | 34.6% | 0.142 | 0.479 | **+0.336** |
| US | 0.407 | 59% | 23.0% | 0.425 | 0.642 | **+0.217** |
| FTSE | 0.406 | 59% | 34.4% | −0.138 | 0.124 | **+0.262** |
| TSX | 0.444 | 56% | 32.3% | 0.484 | 0.742 | **+0.258** |

The regime filter and vol targeting hold gross at 0.34–0.44, so charging a full
3.5% hurdle against a book that is ~60% in cash earning nothing penalised it
twice — worth **+0.22 to +0.34 Sharpe**. A real broker pays interest on idle
balances, so the 0% was a simulator artifact, not prudence.

**Decision (implemented).** Credit interest on uninvested cash in the simulator,
then standardise the metric — in that order, because the reverse makes every
sleeve look ~0.25 Sharpe worse for an accounting reason.

- `config.CREDIT_IDLE_CASH` / `CASH_RATE_ANNUAL`, and
  [fees.idle_cash_credit](../trading_algo/fees.py) wired into `backtest.py`'s
  daily loop. ACT/365 on calendar days, accrued on the **net** exposure
  `1 − Σw`, never `gross − 1` — longs consume cash, shorts generate it, so a
  dollar-neutral book has its whole equity on deposit. That signing is the same
  trap the FX financing model hit twice.
- The property this buys: a 100%-cash book now earns the cash rate, so its excess
  return is 0 and its Sharpe is 0. **That is the only coherent null for PSR/DSR**,
  which test against "no skill".
- The synthetic regression baseline moved with it, deliberately: portfolio CAGR
  0.0673 → 0.0911, Sharpe 0.61 → 1.05, maxDD −11.31% → −8.64%
  (`python -m trading_algo.ci_regression --update`). Synthetic is a drift detector,
  not a performance claim — invariant #5.

**Measured effect on the real 2012–2026 panel**, which is the number that counts:

| sleeve | interest earned | per yr | SR (excess) before | after | CAGR | maxDD |
|---|---|---|---|---|---|---|
| ASX | 33.96% | 2.30% | 0.142 | **0.477** | 6.75% | −9.57% |
| US | 30.58% | 2.08% | 0.425 | **0.643** | 9.62% | −13.20% |
| FTSE | 30.50% | 2.07% | −0.138 | **0.113** | 4.17% | −15.54% |
| TSX | 28.64% | 1.96% | 0.484 | **0.743** | 9.21% | −14.27% |
| **PORTFOLIO** | — | — | 0.376 | **0.625** | 8.60% | −9.76% |

The realised credit is ~2.0–2.3%/yr per sleeve, which is 3.5% on the 56–66% that
was idle — i.e. exactly the arithmetic, arriving day by day rather than as an
average. FTSE crosses from negative to positive vs cash on this alone.

> **This change flatters reported performance, and it introduced a new
> asymmetry.** It is a credit, not a cost. Its mirror — the margin *debit* on
> borrowed money — is still not modelled on the equity stack (§7). So today a
> flat book is paid for sitting out while `ultra` at 3× gross is charged nothing
> for gearing. Wiring the debit is the matching change and it is now the more
> urgent half.

**Still open on the convention itself:** `validation.py` remains on raw returns
(see §9 for why that is the one with a correctness argument), and `RISK_FREE` is
a single 3.5% applied to AUD, USD, GBP *and* CAD returns — which cuts against
invariant #6, since those policy rates spanned roughly 0.05%–5.5% over the
sample. A per-currency rate series is the remaining piece.

## 2. Annualisation: √252 assumes IID, and our returns are not

**Finding.** Multiplying a per-period Sharpe by `√252` is only valid for
serially independent returns. Lo (2002) gives the general factor for stationary
returns:

$$\eta(q) = \frac{q}{\sqrt{q + 2\sum_{k=1}^{q-1}(q-k)\rho_k}}$$

Lo's headline case is hedge funds, where *positive* autocorrelation from stale
marks **inflates** the naive Sharpe by up to 65%. **Ours goes the other way.**

**Measured** (ρ from our own daily returns, truncated at lag 21 ≈ one month):

| series | ρ₁ | ρ₂ | ρ₅ | Σρ(1..21) | √252 | η_Lo | SR naive | SR Lo |
|---|---|---|---|---|---|---|---|---|
| PORTFOLIO | −0.116 | −0.010 | −0.010 | −0.125 | 15.87 | 18.40 | 0.806 | 0.935 |
| ASX | −0.007 | −0.020 | −0.051 | −0.131 | 15.87 | 18.38 | 0.652 | 0.755 |
| US | −0.043 | −0.050 | −0.053 | −0.169 | 15.87 | 19.46 | 0.792 | 0.970 |
| FTSE | +0.023 | −0.024 | −0.013 | −0.255 | 15.87 | 22.19 | 0.304 | 0.425 |
| TSX | +0.034 | −0.021 | −0.046 | −0.135 | 15.87 | 18.50 | 0.948 | 1.105 |

A monthly-rebalanced book of liquid large-caps **mean-reverts at the daily
scale**, so `√252` is *conservative* here by 14–28%. That is the opposite of the
standard warning, and it is the more comfortable direction to be wrong in.

**But we computed the null before believing it** (2,000 permutations per series,
which destroys serial dependence while preserving the marginal distribution):

| series | η observed | null mean | null 2.5–97.5% | p (2-sided) |
|---|---|---|---|---|
| PORTFOLIO | 18.40 | 16.05 | [14.05, 18.68] | 0.070 |
| ASX | 18.38 | 16.13 | [13.94, 18.75] | 0.080 |
| US | 19.46 | 16.13 | [14.12, 18.68] | **0.014** |
| FTSE | 22.19 | 16.13 | [14.00, 18.74] | **0.000** |
| TSX | 18.50 | 16.05 | [13.95, 18.63] | 0.060 |

Two results, both important:

- **The null mean is 16.05, not 15.87.** The truncated estimator is biased
  upward ~1.2% even on IID data. Part of every "correction" above is estimator
  bias, not signal.
- **Only FTSE and US clear 5%.** For the portfolio, ASX and TSX the negative
  autocorrelation is *indistinguishable from noise* at 15 years. A 21-term sum of
  ρ's each carrying SE ≈ 1/√n ≈ 0.016 is simply not a precise object.

**Decision.** Do **not** adopt a Lo-corrected Sharpe as a reported metric. It
would raise our headline numbers on evidence that mostly fails its own null, and
it introduces a truncation lag and an estimator bias as new free parameters. Keep
`√252`, and record here that it is conservative rather than optimistic for this
strategy. Revisit only if a sleeve's Σρ becomes significant in its own right.

## 3. Precision: the number we can compute, and the number we can prove

**Finding.** The standard error of a Sharpe estimate depends on sample length
*and* the third and fourth moments (Mertens 2002; Bailey & López de Prado 2012):

$$\widehat{SE}(\widehat{SR}) = \sqrt{\frac{1 - \gamma_3\widehat{SR} + \frac{\gamma_4-1}{4}\widehat{SR}^2}{T}}$$

and inverting it for a confidence level gives the **Minimum Track Record
Length** — how long a record must be before a claimed Sharpe is distinguishable
from a threshold:

$$\widehat{MinTRL}(c) = \left(1 - \gamma_3\widehat{SR} + \frac{\gamma_4-1}{4}\widehat{SR}^2\right)\left(\frac{z_{1-\alpha}}{\widehat{SR}-c}\right)^2$$

**Measured on 15 years of our own daily returns** (95% confidence):

| series | years | skew | kurt | SR | SE | 95% CI | MinTRL SR>0 | MinTRL SR>0.5 |
|---|---|---|---|---|---|---|---|---|
| PORTFOLIO | 15.1 | −0.03 | 45.21 | 0.806 | 0.261 | [+0.29, +1.32] | 4.3y | **29.7y** |
| ASX | 14.8 | −0.44 | 6.97 | 0.652 | 0.263 | [+0.14, +1.17] | 6.5y | 119.3y |
| US | 14.7 | −0.51 | 6.62 | 0.792 | 0.265 | [+0.27, +1.31] | 4.4y | 32.8y |
| FTSE | 14.7 | −0.29 | 8.24 | 0.304 | 0.261 | [−0.21, +0.82] | 29.5y | never |
| TSX | 14.7 | −0.63 | 7.62 | 0.948 | 0.267 | [+0.42, +1.47] | 3.1y | 14.1y |

**Restated on excess returns, post-change** — the coherent version, and a
*harsher* one than the raw numbers above, because a 3.5% hurdle costs more than
crediting idle cash returns:

| series | SR | SE | 95% CI | MinTRL SR>0 | MinTRL SR>0.5 |
|---|---|---|---|---|---|
| PORTFOLIO | 0.625 | 0.260 | [+0.12, +1.13] | 7.1y | **177.0y** |
| ASX | 0.477 | 0.262 | **[−0.04, +0.99]** | 12.1y | never |
| US | 0.643 | 0.264 | [+0.13, +1.16] | 6.7y | 135.3y |
| FTSE | 0.113 | 0.261 | [−0.40, +0.62] | 212.3y | never |
| TSX | 0.743 | 0.266 | [+0.22, +1.26] | 5.1y | 47.4y |

Note the standard error is **unchanged at ±0.26** — it depends on sample length
and the higher moments, not on the level of the Sharpe. And ASX's interval now
straddles zero. Proving the portfolio beats 0.5 goes from 30 years to **177**.

**This is the single most useful table in the document.** Fifteen years of daily
data buys a standard error of **±0.26**. So:

- Every sleeve Sharpe is ±0.5 at 95%. TSX's 0.948 and ASX's 0.652 are **not
  distinguishable from each other**, and FTSE's 0.304 is not distinguishable
  from zero.
- Proving the portfolio clears **0.5** would take **29.7 years** on raw returns,
  **177** on excess returns. We have 15.
- Ranking sleeves by backtest Sharpe and funding accordingly is therefore
  ranking on noise. The current 25/25/25/25 equal split is the right answer
  *because* the differences are unprovable — it should stay that way, and this
  table is the reason to put in the record.

**Decision.** Ship `MinTRL` alongside every reported Sharpe. It costs nothing
(the moments are already computed in
[validation.py:88](../trading_algo/validation.py#L88)) and it converts an
unfalsifiable number into a falsifiable claim. Add it to `deflation_summary`.

## 4. The live books can prove nothing yet — and one is significantly negative

**Measured** off each book's own `equity_history`, annualised with the book's
own `marks.periods_per_year` (calendar-based, so the hourly book is not
mis-scaled by 252 — the FX side already gets this right):

| book | obs | span | ppy | SR | SE | 95% CI |
|---|---|---|---|---|---|---|
| paper `experimental` | 54 | 78d | 252 | −2.84 | 2.07 | [−6.90, +1.22] |
| paper `full` | 62 | 99d | 252 | −0.40 | 2.03 | [−4.37, +3.57] |
| paper `small` | 65 | 98d | 252 | −2.47 | 1.85 | [−6.09, +1.15] |
| paper `ultra` | 54 | 78d | 252 | −0.20 | 2.17 | [−4.46, +4.05] |
| fx `daytrader` | 592 | 78d | 4383 | **−5.79** | 2.65 | **[−10.98, −0.59]** |
| fx `matt` | 66 | 91d | 252 | −3.05 | 1.81 | [−6.60, +0.49] |
| fx `multiasset` | 48 | 78d | 252 | +0.55 | 2.31 | [−3.97, +5.07] |
| fx `partner` | 66 | 91d | 252 | −3.28 | 1.83 | [−6.88, +0.31] |

At 2–3 months the standard error is **±2**, so seven of eight confidence
intervals span zero and carry no information whatsoever. Quoting a live Sharpe at
this sample length is meaningless in either direction — including the negative
ones.

The exception matters: **`fx_daytrader`'s interval excludes zero** (−10.98 to
−0.59). Over 592 hourly bars the intraday book is losing at a rate that luck
alone does not comfortably explain. That is a signal worth acting on, and it is
the *only* per-book Sharpe in this table that currently says anything.

**Decision.** (a) The dashboard should **suppress or grey a book's Sharpe until
its CI excludes zero**, showing the interval rather than the point estimate —
a point Sharpe on a 54-observation book is an invitation to over-read noise.
(b) Open a separate investigation into `fx_daytrader`; its cost model at hourly
turnover is the first suspect (see §8).

## 5. Non-normality caught a data bug: a 52% one-day FX tick

**Finding.** The portfolio return series has **kurtosis 45.2** while every
sleeve sits at 6.6–8.2. A four-way average of fat-tailed series cannot be six
times fatter than its inputs. Chasing that anomaly found a real bug.

The two largest standardised days in 15 years are **consecutive**:

| date | portfolio return | z | ASX | US | FTSE | TSX |
|---|---|---|---|---|---|---|
| 2014-12-29 | **−8.55%** | −16.7 | — | — | — | — |
| 2014-12-30 | **+8.69%** | +16.9 | +0.00% | −0.52% | −0.55% | +0.00% |

No sleeve moved more than 0.6% on either day. The cause is in the FX panel:

```
Ticker      AUDGBP=X     pct change
2014-12-26   0.52100
2014-12-29   0.79170       +51.96%     <-- bad Yahoo print
2014-12-30   0.52424       -33.78%
```

A single corrupt `AUDGBP=X` close propagates through [fx.py](../trading_algo/fx.py)
into the FTSE sleeve's AUD translation and out into the portfolio series.

**What it costs:** kurtosis 45.21 → 27.76 dropping that one day → **4.05**
dropping the top three. Portfolio Sharpe 0.806 → 0.765. So two bad ticks
generate ~90% of the apparent tail risk in the headline series and flatter the
Sharpe by +0.04.

**The screen already exists and is not wired to this panel.**
[data_quality.py:56](../trading_algo/data_quality.py#L56) defines
`JUMP_DEFAULT = 0.50` — "|1-day return| above this is impossible" — exactly this
case. It is applied to *equity universe* panels
([backtest.py:89](../trading_algo/backtest.py#L89),
[paper_trade.py:723](../trading_algo/paper_trade.py#L723)) and the FX subsystem
screens its own panel (`forex/fx_data_quality.py`). The **equity portfolio's FX
translation panel has no quality screen at all** — `fx.py` contains no reference
to `data_quality`, spikes, or sanity bounds.

**Decision.** Wire `data_quality`'s jump check into `fx.py`'s panel load. An FX
cross is a far better candidate for a hard jump bound than an equity (no splits,
no corporate actions, and a real 52% daily move in AUDGBP would be historic).
This is a **correctness fix, not a metric change** — every AUD-reported number
since 2012 is affected, and it is the most actionable finding in this document.

## 6. Diversification: where the portfolio Sharpe actually comes from

**Finding.** Sharpe ratios do not average. For N equal-weighted sleeves of
similar Sharpe and mean pairwise correlation ρ:

$$SR_{portfolio} \approx \overline{SR} \cdot \sqrt{\frac{N}{1 + (N-1)\rho}}$$

**Measured** cross-sleeve daily correlation (local returns):

| | ASX | US | FTSE | TSX |
|---|---|---|---|---|
| **ASX** | 1.000 | 0.058 | 0.192 | 0.086 |
| **US** | | 1.000 | 0.320 | **0.511** |
| **FTSE** | | | 1.000 | 0.392 |
| **TSX** | | | | 1.000 |

- mean pairwise ρ = **+0.260**
- mean sleeve Sharpe = **0.674** (ASX 0.652, US 0.792, FTSE 0.304, TSX 0.948)
- theoretical equal-weight portfolio SR = **1.010**
- **measured** portfolio SR (AUD) = **0.806**

Two readings. First, the multi-region design is doing real work: the portfolio
beats its own average sleeve by +0.13 Sharpe, and it does so *because* ASX–US
correlation is 0.058, not because any sleeve is good. Second — and this is the
new number — **the portfolio layer leaks 0.204 Sharpe** against the frictionless
diversification benchmark. That gap is FX translation and allocation rebalancing.
Note also that US–TSX at 0.511 means the two North American sleeves are
substantially one bet; adding a fifth North American region would buy much less
than the ASX slot does.

**Decision.** Track `theoretical_ew_sr − measured_sr` as a portfolio-layer
efficiency metric. It is currently 0.204 and 0.041 of that is the FX bug in §5 —
fix that first, then re-measure before attributing the rest to rebalancing.

## 7. Leverage: Sharpe is invariant, wealth is not

**Finding.** In frictionless theory leverage cancels out of the Sharpe ratio.
Two real frictions break that, and they pull in opposite directions as vol rises:

$$SR(L) = SR(1) - \frac{L-1}{L}\cdot\frac{c - r_f}{\sigma} \qquad\text{[financing]}$$
$$SR_{geom} \approx SR_{arith} - \frac{\sigma}{2} \qquad\text{[compounding]}$$

**Projected** on the real portfolio series (8.14% vol, SR 0.376 vs cash), using
`MARGIN_RATE_ANNUAL = 5.5%` from
[fx_config.py:232](../trading_algo/forex/fx_config.py#L232) against
`RISK_FREE = 3.5%`. *Projected*, not measured, for the reason in the finding
below — the equity stack does not charge this today:

| L | vol | SR financed | ΔSR financing | geom drag σ²/2 | SR on geometric | Δ arith−geom |
|---|---|---|---|---|---|---|
| 1 | 8.1% | 0.376 | +0.000 | 0.33% | 0.336 | 0.041 |
| 2 | 16.3% | 0.254 | −0.123 | 1.33% | 0.172 | 0.081 |
| 3 | 24.4% | 0.213 | −0.164 | 2.98% | 0.090 | 0.122 |
| 5 | 40.7% | 0.180 | −0.197 | 8.29% | −0.024 | 0.204 |

At 3× the two frictions together take a 0.376 Sharpe to **0.090 on a compounding
basis** — they remove three quarters of it. And the mix flips with vol target:

| target_vol | financing cost at L=3 | compounding gap |
|---|---|---|
| 12% (core) | 0.111 | 0.060 |
| 35% (`ultra`) | **0.038** | **0.175** |

**The intuition is backwards from the usual complaint.** For the `ultra` profile
(`target_vol = 0.35`, `max_gross = 3.0`,
[profiles.py](../trading_algo/profiles.py)) the borrowing spread is nearly
irrelevant — 0.038 Sharpe — because a 2% spread on a 35%-vol book is noise. What
kills it is **variance drag**: at 35% vol the arithmetic Sharpe overstates the
compounding reality by 0.175, nearly 3× the core book's 0.060.

**The finding this exposes: the equity stack charges no financing at all.**
`backtest.py`, `paper_trade.py` and `fees.py` contain no margin, borrow or
interest term. [config.py:324](../trading_algo/config.py#L324) says so out loud —
"Borrow/short-financing cost is intentionally deferred until a short book
exists." **Two short/levered books now exist and are live:** `ultra` at
`max_gross = 3.0` and `experimental` at `long_short = True`. Only the FX
subsystem charges financing (`marks.financing_fraction`, added 2026-09-19), and
its rates are assumptions rather than measurements. So both experimental equity
books report a Sharpe with a real cost missing — on the table above, ~0.16 for a
3× book at 8% vol, ~0.04 at `ultra`'s 35% vol target.

**One trap to avoid when wiring it up:** charge the **long debit**
`max(0, L − 1)`, never `gross − 1`. A short position *generates* cash rather than
consuming it, so billing the short leg as borrowed money double-counts — the same
error inflated a `multiasset` financing estimate by ~9×. For the dollar-neutral
`experimental` book the long debit is ≈0 and the correct charge is the stock-loan
fee on short notional, not margin interest on 2× gross.

**Decision.** (a) Extend the deferred financing cost to the equity stack, gated on
`max(0, L−1)` for margin and short notional for borrow; until then, tag `ultra`
and `experimental` Sharpes as **financing-free** on the dashboard METHOD tab.
(b) Separately: since `metrics.compute_metrics` uses an **arithmetic** numerator
([metrics.py:33](../trading_algo/metrics.py#L33)) while reporting CAGR
geometrically in the same dict, `ultra`'s Sharpe is structurally ~0.175 more
flattering than its own equity curve. Report a geometric-numerator Sharpe
alongside for any book with `target_vol > 0.20`.

## 8. Costs in Sharpe units — and why FTSE is the weak sleeve

**Measured** from the backtest's own cost ledger (`total_cost_fraction`):

| sleeve | total cost (14.7y) | cost/yr | ann vol | ΔSharpe | SR net | SR gross |
|---|---|---|---|---|---|---|
| ASX | 8.56% | 0.58% | 6.86% | 0.084 | 0.652 | 0.736 |
| US | 4.07% | 0.28% | 9.55% | 0.029 | 0.792 | 0.821 |
| **FTSE** | **24.71%** | **1.68%** | 7.93% | **0.211** | **0.304** | 0.515 |
| TSX | 6.97% | 0.48% | 7.54% | 0.063 | 0.948 | 1.011 |

Costs remove **41% of the FTSE sleeve's gross Sharpe** — 0.211 of 0.515 — driven
by the 50bp UK stamp duty on buys. FTSE is not a bad signal; it is a decent
signal being taxed to death by a monthly rebalance. Gross 0.515 is within noise
of ASX's 0.736; net it is the only sleeve that loses to cash.

**Decision.** This reframes FTSE from "weak sleeve, consider defunding" to
"turnover problem". The highest-value experiment is a **lower-turnover FTSE
variant** (wider rebalance band, longer holding period, or a stamp-duty-aware
no-trade zone inside `compute_targets`' tolerance), not a different signal. Note
this is also the standing hypothesis for `fx_daytrader` (§4): at hourly turnover,
cost per unit of vol is the first thing to check.

## 9. Multiple testing: nobody can reconstruct where our N came from

**Finding.** A Sharpe selected as the best of N attempts is biased upward, and
the correction is nonlinear in the level of the Sharpe. Harvey & Liu's worked
example: an annual Sharpe of 0.75 over 240 monthly observations with N=200 tests
haircuts to **0.32** — a 58% cut. Harvey, Liu & Zhu put the *t*-statistic hurdle
for a new factor at **2.8–3.18**, not 2.0.

**Measured on our own TSX series** via
[validation.sharpe_haircut](../trading_algo/validation.py#L197):

| trials N | E[max SR] under null | haircut SR | % cut | DSR |
|---|---|---|---|---|
| 1 | 0.000 | 0.948 | 0% | 0.9998 |
| 5 | 0.312 | 0.636 | 33% | 0.9914 |
| **6** *(as documented)* | — | **0.608** | 36% | **0.99** |
| 20 | 0.497 | 0.451 | 52% | 0.9544 |
| 100 | 0.662 | 0.286 | 70% | 0.8581 |
| 200 | 0.723 | 0.225 | 76% | 0.8001 |
| 424 | 0.785 | 0.163 | 83% | 0.7289 |

The curve is steep exactly where our numbers live: **N=6 and N=20 are the
difference between a haircut Sharpe of 0.61 and 0.45**, and between a DSR of 0.99
and 0.95. Which means the trial count is not a footnote — it is as material to
the verdict as the returns are.

**So what is our N?** `config.py:169`, `CLAUDE.md`, `tests/test_regions.py` and
`docs/FORENSIC_AUDIT_2026-09.md` all record the TSX gate as "raw 0.948, haircut
0.608, DSR 0.99 **(N=6)**". Checking whether 6 is right:

- **In TSX's favour:** the TSX region entry
  ([regions.py:120](../trading_algo/regions.py#L120)) sets **no `params`** — it
  inherits `DEFAULT_PARAMS` wholesale. So there was no TSX-specific parameter
  search, and the 5×4 = 20-configuration sweep grid
  ([sweep.py:26](../trading_algo/sweep.py#L26)) does **not** apply to it. N=6 is
  not refuted.
- **Against it:** the shared defaults TSX inherits (`top_n = 10`,
  `lookback_days = 252`) were themselves chosen by searching — that is what
  `sweep.py` is for. A sleeve that inherits a tuned parameter set inherits its
  **selection cost** too. TSX's honest N is therefore not 1, probably not 6, and
  nobody can say what it is, because **the search that produced the defaults was
  never recorded**.

**This is the same missing discipline `docs/MONTE_CARLO_RESEARCH.md` §2
documents in the opposite direction.** There,
[champions.py:129](../trading_algo/forex/champions.py#L129) passes the *raw*
424-genome count where the *effective* count is ~9–11, over-deflating and
discarding real edges. Here a registration gate passes 6 with no derivation at
all. Both are the same root cause: **N is a property of the search you actually
ran, and no artifact in this repo records it.**

### 9b. The gate is the wrong instrument — not TSX the wrong sleeve

Applying the accurate convention to the *gate* rather than just to TSX settles
this. **No sleeve passes a standalone DSR ≥ 0.95** once returns are excess and
idle cash is credited:

| sleeve | SR (excess, cash credited) | DSR N=6 | DSR N=20 | verdict |
|---|---|---|---|---|
| ASX | 0.479 | 0.7038 | 0.4757 | FAIL |
| US | 0.642 | 0.8743 | 0.7098 | FAIL |
| FTSE | 0.124 | 0.2056 | 0.0776 | FAIL |
| **TSX** | **0.742** | **0.9349** | 0.8218 | FAIL (closest of the four) |

So enforcing a standalone DSR gate consistently would defund *everything*. That
is the reductio: it is a single-strategy significance test being used for a
portfolio-construction decision. And TSX is the **best** sleeve on every
convention — the ranking TSX > US > ASX > FTSE is convention-invariant.

**What TSX actually contributes** (rebuilt 3-sleeve vs 4-sleeve portfolio, same
15.1-year overlapping window, equal weights both sides):

| | 3 sleeves | 4 (+TSX) | Δ |
|---|---|---|---|
| Sharpe, raw | 0.726 | 0.806 | **+0.080** |
| Sharpe, excess (cash 0%) | 0.322 | 0.376 | +0.055 |
| Sharpe, excess (idle cash paid) | 0.570 | 0.635 | +0.064 |
| CAGR | 6.09% | 6.43% | +0.34pp |
| ann vol | 8.66% | **8.14%** | −0.52pp |
| max drawdown | −11.32% | **−11.18%** | better |

It adds return *and* cuts volatility *and* improves drawdown — a real
diversification gain, not a return chase. Tested with the paired stationary block
bootstrap this document recommends in §10 (same days resampled for both books, so
the three shared sleeves' noise cancels): **ΔSharpe +0.080, 95% CI [−0.048,
+0.192], P(Δ>0) = 0.885.** Suggestive, not proven — but positive under all three
conventions.

**Decision on TSX: keep it funded at 25%, and change the claim.** Retire "TSX
cleared the gate, DSR 0.99", which is false under an accurate convention. Record
instead: *best of four sleeves, none individually provable (SE ±0.26, §3), funded
for a measured +0.064 Sharpe portfolio contribution at 89% confidence.* Weaker
sounding, better founded. The one genuine caveat is not the Sharpe at all —
US–TSX ρ = 0.511 is the highest pair in the matrix (§6), so TSX is the *least*
diversifying of the four and a fifth North American sleeve would add close to
nothing.

**Decision.** Make the trial count a recorded artifact rather than a remembered
one. Concretely: (a) `sweep.py` should emit the configuration count it evaluated
alongside its grid, so a parameter choice carries its own search cost; (b) the
register → backtest → fund gate should require an N *with a derivation* — "6
candidate regions", "20 sweep configs", whichever it is — not a bare integer;
(c) leave the TSX line at N=6 for now, but annotate it as **unverified
provenance**, because the two credible answers sit either side of the DSR 0.95
threshold and we cannot presently tell which applies.

## 10. What Sharpe cannot see

Recorded so the metric is not asked to do work it cannot do.

- **It is gameable.** Goetzmann, Ingersoll, Spiegel & Welch (2007) show the
  Sharpe ratio can be manipulated substantially even with high transaction
  costs — typically by selling tail risk, which raises the mean and suppresses
  measured vol until the tail arrives. Our `experimental` long/short book and any
  future option-like payoff are exactly the shapes where this bites. The
  manipulation-proof alternative (MPPM) is a power-utility average over the
  return history.
- **It says nothing about the path.** Measured: under an IID null with **our
  own** μ and σ over 15.1 years (5,000 paths), the maximum drawdown distribution
  is median **−16.0%**, 5–95% **[−25.8%, −10.8%]**, worst −46.2%. We observed
  **−11.2%** — the **93rd percentile of luck**. The backtest's comfortable ride
  is a lucky draw from its own Sharpe, not a property of it. A book with this
  Sharpe should *expect* ~16% peak-to-trough.
- **It is silent on capacity.** A Sharpe computed at any size assumes the fills
  happened. `docs/HFT_REALITY.md` covers this for the intraday case.
- **Comparing two Sharpes needs its own test.** Ledoit & Wolf (2008): use a
  studentised time-series (block) bootstrap for the *difference* of Sharpes, not
  two overlapping confidence intervals. Relevant the moment we rank sleeves or
  champions against each other — which §3 says we should stop doing on point
  estimates anyway.
- **Higher frequency is the only reliable way to raise it.** Grinold's
  fundamental law, `IR ≈ IC·√breadth`, says Sharpe scales with the square root of
  the number of *independent* bets. Our breadth is four sleeves × ~10 names ×
  12 rebalances; the US–TSX ρ of 0.511 in §6 is a direct measurement of breadth
  being lower than the name count suggests.

## Decisions, in priority order

| # | Change | Why | Where |
|---|---|---|---|
| ✅ | **DONE** — credit interest on idle cash in the backtest | Sleeves are 56–66% idle; charging the hurdle anyway cost 0.22–0.34 Sharpe (§1b) | `fees.py`, `backtest.py`, `config.py` |
| 1 | Charge the margin **debit** on the equity stack | The credit above landed without its mirror; `ultra` gears free (§1b, §7) | `fees.py`, `backtest.py` |
| 2 | Screen the FX translation panel with `data_quality`'s jump check | A 52% bad tick corrupts every AUD number since 2012 (§5) | `fx.py` |
| 3 | Stop using standalone DSR as a funding gate; record N with its derivation | No sleeve passes it; it is the wrong instrument (§9b) | `sweep.py`, `config.py:169` |
| 4 | Report `MinTRL` beside every Sharpe | Makes an unfalsifiable number falsifiable (§3) | `validation.deflation_summary` |
| 5 | Show CI, not point Sharpe, until it excludes zero | Seven of eight live books are pure noise (§4) | `dashboard/` |
| 6 | Investigate `fx_daytrader`'s significantly negative Sharpe | Only live book whose CI excludes zero (§4, §8) | FX subsystem |
| 7 | Credit idle cash in the **paper books** too (needs an `interest` ledger row, else `verify` reports cash-drift) | Backtest and live books now use different cash conventions (§1b) | `paper_trade.py`, `verify.py` |
| 8 | Geometric-numerator Sharpe for `target_vol > 0.20` books | `ultra` reads 0.175 better than it compounds (§7) | `metrics.py` |
| 9 | Switch `validation.py` to excess returns; per-currency rate series | DSR's null is SR=0 only in excess terms (§9); one rate for four currencies breaks invariant #6 (§1b) | `validation.py`, `config.py` |
| 10 | Low-turnover FTSE variant | Costs take 41% of its gross Sharpe (§8) | `regions.py` params |
| 11 | Do **not** adopt a Lo-corrected Sharpe | Fails its own null on 3 of 5 series (§2) | — |
| 12 | Do **not** unify `ddof` | Measured null: identical to 3 d.p. (§1) | — |

## References

- Sharpe, W. F. (1966). "Mutual Fund Performance." *Journal of Business*.
- Sharpe, W. F. (1994). "The Sharpe Ratio." *Journal of Portfolio Management*
  21(1), 49–58. — the ex-ante/ex-post benchmark-relative revision.
- Lo, A. W. (2002). "The Statistics of Sharpe Ratios." *Financial Analysts
  Journal* 58(4). — autocorrelation-corrected annualisation; overstatement of up
  to 65% in the positive-ρ case.
- Mertens, E. (2002). "Comments on Variance of the IID Estimator in Lo (2002)."
  — the skew/kurtosis standard error used in §3.
- Bailey, D. H. & López de Prado, M. (2012). "The Sharpe Ratio Efficient
  Frontier." *Journal of Risk*. — PSR and Minimum Track Record Length.
- Bailey, D. H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio."
  *Journal of Portfolio Management* 40(5). — false-strategy theorem, E[max SR].
- Harvey, C. R. & Liu, Y. (2015). "Backtesting." — haircut Sharpe, Bonferroni /
  Holm / BHY; the 0.75 → 0.32 worked example at N=200.
- Harvey, C. R., Liu, Y. & Zhu, H. (2016). "…and the Cross-Section of Expected
  Returns." *RFS*. — the 2.8–3.18 *t*-statistic hurdle.
- Ledoit, O. & Wolf, M. (2008). "Robust Performance Hypothesis Testing with the
  Sharpe Ratio." *Journal of Empirical Finance*. — HAC / studentised bootstrap
  for the difference of two Sharpes.
- Goetzmann, W., Ingersoll, J., Spiegel, M. & Welch, I. (2007). "Portfolio
  Performance Manipulation and Manipulation-Proof Performance Measures." *RFS*
  20(5), 1503–1546.
- Grinold, R. (1989) and Grinold & Kahn, *Active Portfolio Management*;
  Clarke, de Silva & Thorley (2002) on the transfer coefficient. — `IR = IC·√BR`.
- Magdon-Ismail, M. & Atiya, A. (2004). "An Analysis of the Maximum Drawdown
  Risk Measure." *Risk*. — drawdown scaling laws analogous to √T for Sharpe.

---

*Measurements reproducible offline from `trading_algo/.cache` (no network):
`python -m trading_algo.run_backtest` for the panel, `state/paper_state_*.json`
and `state/fx_state_*.json` for the live books. Permutation nulls used 2,000
draws (§2) and 5,000 paths (§10), seeded.*
