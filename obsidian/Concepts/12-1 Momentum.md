---
title: 12-1 Momentum
type: concept
tags: [trading, momentum, signal]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 📈 12-1 Momentum

Total return over the last ~12 months, **excluding the most recent ~1 month**:
$$\text{score}(t) = \frac{P_{t-21}}{P_{t-252}} - 1$$

- 252 days ≈ 12 months; 21 ≈ 1 month. Built from past prices only → [[No-Lookahead]].

> [!info] Why skip the last month?
> The most recent month tends to mean-revert; skipping it isolates the persistent
> trend instead of buying a short-term spike.

> [!quote] Why momentum?
> The most replicated anomaly in equities (Jegadeesh & Titman, 1993); holds across
> [[Multi-Region Momentum|FTSE, US and ASX]].

Top **N** by score that also pass the [[Regime & Trend Filters]] form the book.

#trading/momentum
