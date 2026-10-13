---
title: Breadth
type: concept
tags: [trading-algo, concept, statistics]
created: 2026-10-13
up: ["[[How It Works]]"]
---

# 🎲 Breadth

**Breadth** is the number of genuinely *separate* bets a strategy makes in a year. It matters because of one equation that explains most of the gap between this system and Renaissance's Medallion fund.

## The equation, in plain English

> **How good the strategy is ≈ how good each bet is × the square root of how many separate bets you make.**

The textbook name is the **Fundamental Law of Active Management** (Grinold, 1989): `IR ≈ IC × √breadth`.

- **IC (information coefficient)** — how well your ranking predicts what happens next. 0 means no better than random; 1 means perfect. Real strategies live around 0.01 to 0.05.
- **IR (information ratio)** — roughly the Sharpe ratio: return per unit of risk.
- **√** — the square root. To double the quality of the strategy you need *four times* as many separate bets, not twice as many.

A coin you can win 51% of the time is worthless if you flip it ten times — the luck swamps the edge. Flip it a million times and the edge becomes a near-certainty. That is the whole idea.

## Medallion versus us, on that equation

Robert Mercer, one of Medallion's leaders, put it like this: *"We're right 50.75 percent of the time ... but we're 100 percent right 50.75 percent of the time. You can make billions that way."* A US Senate investigation recorded Renaissance making **more than 100,000 trades a day**.

Measured on our own 2012–2026 prices (`docs/MEDALLION_CITADEL_RESEARCH.md`):

| | Edge per bet | Separate bets per year | Result |
|---|---|---|---|
| Medallion (reported) | right ~50.75% of the time | tens of thousands or more | Sharpe above 2 (Cornell 2019) |
| Our momentum picks | right **51–52%** of the time (IC 0.02–0.04) | about **350–400** across the whole equity book | Sharpe about 0.6 |

The surprise: **our edge per bet is about the same as Medallion's.** What we lack is the number of separate bets — by a factor of roughly a hundred, which by the square-root rule is roughly a factor of ten in quality. A rough check: 0.03 × √375 ≈ 0.6, which is about the Sharpe the book actually earns.

## Why "separate" is the hard part

Our US sleeve ranks 124 stocks every month, so it looks like 1,488 bets a year. But stocks move together — a tech rally lifts all the tech names at once — so most of those bets are really the same bet repeated. (Part of the shortfall is also the signal itself working better in some years than others; the measurement cannot split the two, and both have the same effect.) Measured, every region collapses to **about 14 to 20 truly separate bets a month**, whether it ranks 52 stocks or 124. A longer list of stocks from the same market buys almost nothing.

The same applies across regions. The four sleeves are about 0.4 correlated (they tend to rise and fall together), so the four are worth about **1.8** fully separate sleeves, not four.

## Where more breadth can come from

1. **A different kind of signal** — one that picks different stocks for different reasons (quality, value). This adds separate bets without trading more.
2. **More, less-related markets or asset classes** — another region, or a trend-following sleeve on bonds and commodities.
3. **Shorter horizons** — more bets per year. This is Medallion's route, and it is closed to us: costs grow with every trade, and on FTSE costs already eat 44% of the edge.

## Related
- [[Sharpe Ratio]] · [[12-1 Momentum]] · [[Survivorship Bias]] · [[Permutation Testing]] · [[Rebalance Timing Luck]]
