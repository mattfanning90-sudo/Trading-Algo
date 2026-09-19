---
title: Sharpe Ratio
type: concept
tags: [trading, statistics, validation]
created: 2026-09-19
up: ["[[How It Works]]"]
---

# 📏 Sharpe Ratio

**Return per unit of worry.** How much you earned above cash, divided by how
much the ride bounced around. It is the number the whole industry quotes, and the
number most often quoted without the two things that give it meaning: how long
you measured, and how many times you looked.

$$SR = \frac{\text{average return} - \text{cash rate}}{\text{standard deviation of returns}}$$

## The one-sentence version

A Sharpe of 1.0 means a year's worth of profit is about the size of a year's
worth of wobble. Below ~0.5 the wobble dominates and you will not be able to tell
skill from luck in your lifetime. Our four-sleeve portfolio measures **0.81** on
15 years of history (before subtracting cash) — see `docs/SHARPE_RESEARCH.md`.

## It's secretly a t-statistic

This is the most useful thing to know about it. Multiply a Sharpe by the square
root of the number of years and you get, near enough, the **t-statistic** —
statistics' standard measure of "is this distinguishable from nothing?"

$$t \approx SR \times \sqrt{\text{years}}$$

So a Sharpe of 0.5 over 4 years gives t = 1.0 — which any statistician would call
*no evidence at all*. You need t ≈ 2 before anyone raises an eyebrow. That single
equation explains why:

> [!warning] Short track records cannot contain information
> Our live paper books have run about **3 months**. At that length the standard
> error on their Sharpe is roughly **±2**. Seven of our eight books have a
> confidence interval that comfortably contains zero — which means their measured
> Sharpe, good or bad, is literally uninformative. Not "weak evidence". None.

## Terms worth knowing

**Annualising.** A daily Sharpe is tiny; convention multiplies it by √252 (the
number of trading days) to make it comparable. This assumes each day is
independent of the last. **Lo's correction** (Andrew Lo, 2002) fixes the factor
when they aren't. Hedge funds usually have *positive* autocorrelation — good
months cluster, which flatters their Sharpe. Ours is mildly *negative* (our book
mean-reverts day to day), so √252 slightly **understates** us. We measured it,
then tested it against a shuffled null and found it mostly isn't significant — so
we left it alone. Worth knowing the direction, not worth changing the metric.

**Standard error.** The ± on the number. Fifteen years of daily data buys us
**±0.26**. That is large enough that our best sleeve (TSX, 0.95) and our
second-worst (ASX, 0.65) are not statistically distinguishable.

**MinTRL — Minimum Track Record Length.** Run the standard error backwards: how
long must a record be before a claimed Sharpe is provable? For our portfolio,
proving it beats **0.5** would take **30 years**. We have 15. This is the honest
answer to "is the strategy good?" — *not yet knowable*.

**Haircut.** If you tried 20 variations and kept the best, the winner's Sharpe is
flattered by the search itself, the way the tallest of 20 people is taller than
average height. The haircut subtracts what pure luck would have produced. Our TSX
sleeve reads raw **0.948**; charged for 6 attempts that becomes **0.61**, and for
20 attempts **0.45**. Which is right depends on how hard we searched — and we
never wrote that down, which is the actual problem. See [[Permutation Testing]]
for the same idea done by brute force, and [[Monte Carlo]] for how the null gets
built.

**Sortino / Calmar.** Cousins. Sortino divides by *downside* wobble only (upside
volatility isn't a problem). Calmar divides return by worst drawdown instead of by
volatility — closer to what actually hurts to live through.

## What it hides

> [!danger] Three blind spots
> **1. It can be gamed.** Sell disaster insurance and you collect small steady
> premiums with low measured volatility — a beautiful Sharpe, right up until the
> disaster. Goetzmann and co-authors (2007) showed most performance measures can
> be manipulated this way even with realistic trading costs.
>
> **2. It says nothing about the path.** Two books with identical Sharpe can
> feel completely different. We simulated 5,000 alternative histories with our
> *own* Sharpe and volatility: the typical worst drawdown was **−16%**, and the
> unlucky 5% saw **−26%**. We actually experienced **−11%**. Our smooth backtest
> was a *lucky draw* from its own Sharpe, not a feature of it.
>
> **3. Leverage looks free to it.** Double your position and both the numerator
> and denominator double, so the Sharpe is unchanged. But your *compounding*
> gets worse — volatility eats returns at roughly σ²/2 per year. At our `ultra`
> book's 35% volatility target that drag is about **0.175 of Sharpe** invisible
> to the metric. See [[Volatility Targeting]].

## Which Sharpe? There are four

Subtle and it bites. Do you subtract the cash rate or not? Is the top line an
average or a compound growth rate? This repo computes it **both** ways in
different modules, and on the same series the answers are **0.81** and **0.38** —
the difference being a 3.5% cash rate. Neither is wrong; quoting one without
saying which is.

> [!tip] The reading discipline
> Never accept a Sharpe alone. Always ask for four things with it:
> **how long** (years of data), **how many** (variants tried before this one won),
> **net of what** (costs and cash in or out), and **± what** (the confidence
> interval). A Sharpe without those is a rumour.

## In this system

- The single implementation lives in `trading_algo/validation.py` — PSR, Deflated
  Sharpe, PBO and the haircut, all in one place so there is no second copy to
  drift.
- `trading_algo/metrics.py` computes the reporting version (net of a 3.5% cash
  rate) for the dashboard and backtest reports.
- The full measured study, including a data bug the kurtosis exposed, is
  `docs/SHARPE_RESEARCH.md`.

#trading/statistics
