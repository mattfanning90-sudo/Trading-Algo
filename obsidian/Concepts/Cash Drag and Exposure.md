---
title: Cash Drag and Exposure
type: concept
tags: [trading-algo, concept, risk]
created: 2026-09-20
up: ["[[How It Works]]"]
---

# 🪙 Cash Drag and Exposure

**Exposure** is how much of your money is actually at work. **Gross exposure** adds up the size of every position, long or short, as a fraction of the account: 0.4 means 40% invested and 60% in cash. **Cash drag** is the return you gave up because that 60% sat in cash instead of the strategy.

## The book is 40% invested on an average day

Measured on real 2012 to 2026 data, the four equity sleeves average 0.34 to 0.44 gross exposure. Two mechanisms explain it:

**1. The regime filter is an on/off switch.** When a region's index is below its 200-day average price, the whole sleeve goes to 100% cash. That was true on 23 to 35% of all days. See [[Regime & Trend Filters]].

**2. The sizing formula holds back even when it is on.** [[Volatility Targeting]] scales the book so its expected wobble is 12% a year. To estimate the wobble it assumes every pair of held stocks is 0.6 correlated. The actual correlation of the picks is 0.18 to 0.36. Because the formula thinks the stocks move together more than they do, it thinks the book is riskier than it is and deploys only 51 to 65% of the capital. The book then realises 8 to 10% volatility against its 12% target. And it can only shrink, never lever: the gross cap is 100%.

## What the cash costs

| Switch off | Change in CAGR (US sleeve) | Change in worst drawdown |
|---|---|---|
| Regime filter | +2.1 points | −13% becomes −22% |
| Correlation assumption 0.6 to 0.3 | +1.9 points | about the same |
| Both filters and vol targeting | +13 points | −13% becomes −34% |

The regime filter is insurance: it costs return in a rising market and pays out in a crash. The correlation assumption is just wrong, and fixing it costs nothing in risk.

## Interest on idle cash

A real broker pays interest on cash. Until September 2026 the backtest paid nothing on the idle 60% while still subtracting a 3.5% cash hurdle from the Sharpe, a double penalty worth 0.2 to 0.3 Sharpe. That credit is now in the backtest. It is not in the paper books, which is one reason paper will trail backtest.

> [!warning] Leverage is not the fix
> Return is edge times risk. With a Sharpe of 0.3 to 0.6, levering up multiplies drawdowns faster than it adds return. Put the idle money to work first (graded de-risking instead of a switch; the right correlation), then decide about the vol target.

## Related
- [[Full System Review 2026-09]] · [[Volatility Targeting]] · [[Regime & Trend Filters]] · [[Sharpe Ratio]]
