---
title: Permutation Testing
type: concept
tags: [trading, statistics, validation]
created: 2026-09-19
up: ["[[How It Works]]"]
---

# 🔀 Permutation Testing

A backtest gives you **one number**. On its own that number is worthless,
because you have nothing to compare it to. Permutation testing builds the
comparison **out of your own data**: shuffle history into thousands of
fake-but-realistic alternative markets, run the identical strategy on each one,
and see how often pure luck matched or beat you.

> [!abstract] In one sentence
> Shuffle the past so the *character* of the market survives but the *order*
> dies, re-run the strategy, and count how often randomness wins.

## The idea, with no maths

Imagine you played 100 hands of poker and won $500. Is that skill? You cannot
tell — until you know what a **random** player wins at the same table. So you sit
a thousand random players down, let each play 100 hands, and look at the spread
of their results. If only 12 of the 1,000 made $500 or more, you probably have
skill. If 600 of them did, you got lucky.

Permutation testing does exactly this, and the "random players" are built by
**shuffling the real market's own daily moves into a different order**.

## Why shuffling is the right kind of random

You could invent random prices from scratch, but they would not look like a real
market, and beating a fake market proves nothing. Shuffling the *actual* daily
moves keeps everything real except the sequence:

| Survives the shuffle | Destroyed by the shuffle |
|---|---|
| Each day's size of move (**volatility** — how much prices jump around) | Trends |
| Fat tails (**kurtosis** — crashes are more common than a bell curve says) | Momentum and mean reversion |
| How names move together (**correlation**) | **Volatility clustering** — calm weeks following calm weeks |
| Each name's total rise over the whole period (**drift**) | Everything a strategy could learn from |

So the shuffled market is a market where **no strategy can work**, built out of
real market ingredients. That is exactly what a **null** needs to be.

> [!info] Null hypothesis
> The boring explanation you have to rule out before claiming anything: *"there
> is no edge here — I just got lucky."* A **null** (or null distribution) is the
> range of results that boring explanation produces. Your backtest only means
> something relative to it.

## The p-value

Run $m$ shuffles. Count $k$ = how many matched or beat your real result.

$$p = \frac{k+1}{m+1}$$

Read it as: *"if my strategy were worthless, there is a $p$ chance of a result
this good by luck alone."* Conventional readings: **p < 0.05** is acceptable
evidence, **p < 0.01** is strong, **p > 0.10** is probably noise.

> [!warning] The +1s are not decoration
> Without them, a strategy that beat all 1,000 shuffles scores p = 0 — "literally
> impossible by chance". A thousand shuffles cannot support that claim. The +1
> effectively counts the real result as one more draw, which is the honest floor.

## The four steps

The method is usually taught as a ladder. Each rung kills a different way of
fooling yourself, and you only climb if the rung below held.

| # | Step | What you do | What it kills |
|---|---|---|---|
| 1 | **In-sample excellence** | Optimise hard on history. No test, just a filter. | Ideas not worth the compute |
| 2 | **In-sample permutation** | Re-run the *whole parameter search* on each shuffle. Compare your best to the best found on noise. | **Selection bias** |
| 3 | **Walk-forward** | Re-fit on a rolling window, trade the period after, repeat. | Parameter instability, regime decay |
| 4 | **Walk-forward permutation** | Re-run the whole rolling process on each shuffle. | **Process overfitting** |

> [!info] In-sample / out-of-sample
> **In-sample** is the data you were allowed to look at while designing. **Out-of-sample**
> is data held back, that the strategy has never seen. Testing on data you
> designed against is like marking your own exam with the answers in front of you.

> [!tip] Why step 2 is the clever one
> If you try 157 lookback settings and keep the best, of course it looks good —
> the best of 157 tries at *anything* looks good. Step 2 fixes this by trying all
> 157 on the shuffled data too, and recording the **best** result there. You then
> compare best-against-best. The advantage you got from searching hard is priced
> into the null automatically, so you never have to estimate how much to penalise
> yourself for searching.

Step 4 shuffles only the data **from the walk-forward start onwards** — real
history before it, noise after — then re-runs the entire re-fitting process. It
tests your *method*, not a model: if your habit of re-optimising every month
makes money on noise, the habit is the problem.

## ⚠️ The catch for this system

The published method shuffles the **calendar**, which works for a strategy that
trades one instrument on its own trend. This repo runs [[12-1 Momentum]] —
**cross-sectional**, meaning names are ranked *against each other* and we buy the
winners. For that, shuffling the calendar is **not** a valid null.

The reason: the shuffle preserves each name's total rise over the whole period.
So in the shuffled world, "what went up over the last 12 months" partly reveals
"what goes up over the whole sample" — a peek at the future the strategy gets for
free. Measured on our own US panel, the supposedly-edgeless null earned a
**+0.33 Sharpe and was positive in 92% of 1,000 shuffles**. A null that makes money is
not a null, and testing against it buries a real edge.

> [!check] The fix — shuffle the signal, not the prices
> Leave prices completely alone. At each rebalance, shuffle **which name each
> momentum score is attached to**. That breaks the single thing momentum claims
> ("this name's past predicts this name's future") and disturbs nothing else. On
> our panel this null sits on zero (−0.002, positive in 48% of 1,000 shuffles) —
> which is what a null is supposed to do.

> [!note] Going deeper
> `docs/PERMUTATION_TESTING.md` is the researched companion to this note — the
> four steps in full, the two nulls that *look* like fixes but are not, what all
> of it measured on our own US panel, and how this relates to the Deflated Sharpe
> gate we already run.

Reproduce every number in this note with:

```bash
python scripts/measure_permutation_null.py --permutations 1000
```

## Glossary

| Term | Plain English |
|---|---|
| **Permutation** | A reshuffle. Same values, new order. |
| **Monte Carlo** | Answering a hard question by playing a random game thousands of times — see [[Monte Carlo]]. |
| **Null / null distribution** | The spread of results you'd get with no skill at all. |
| **p-value** | The chance luck alone beats you. Smaller is better. |
| **In-sample** | Data you were allowed to look at while designing. |
| **Out-of-sample** | Data held back, never seen during design. |
| **Selection bias** | Looking good because you kept the best of many tries. |
| **Overfitting** | Memorising the noise in history instead of learning a rule. |
| **Sharpe ratio** | Return divided by risk. Reward per unit of stomach-churn. |
| **Profit factor** | Money made ÷ money lost. Above 1 = made more than lost. |
| **Drift** | A market's general tendency to rise over long periods. |
| **Volatility** | How much prices jump around. |
| **Volatility clustering** | Wild days follow wild days; calm follows calm. |
| **Correlation** | How closely two things move together (−1 to +1). |
| **Cross-sectional** | Comparing names *against each other* at one moment. |
| **Time-series** | Comparing one thing against *its own past*. |
| **Walk-forward** | Re-fit on the past, trade the next slice, roll, repeat. |

Related: [[Monte Carlo]] · [[12-1 Momentum]] · [[No-Lookahead]] · [[How It Works]]

#trading/statistics
