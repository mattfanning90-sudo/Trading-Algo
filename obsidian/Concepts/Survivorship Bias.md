---
title: Survivorship Bias
type: concept
tags: [trading-algo, concept, backtesting]
created: 2026-09-20
up: ["[[How It Works]]"]
---

# 🪦 Survivorship Bias

Test a strategy on the list of companies that exist **today**, and you have quietly excluded every company that went bust, was taken over, or fell out of the index along the way. The survivors are, by definition, the ones that did well. Any backtest on that list looks better than the real experience would have been.

## How big is it here

No region supplies an index-membership file, so every backtest picks from today's universe. The fingerprint: zero of 125 US names, zero of 56 ASX names and zero of 55 TSX names stopped trading before 2026. A real index loses 30 to 50% of its members over fourteen years.

A rough size: simply equal-weighting today's ASX list beats the ASX total-return index by 9.4 points a year. Today's TSX list beats its index by 5.4, the US list by 2.5, the FTSE list by roughly zero. That excess is mostly survivorship. The one sleeve without much of it (FTSE) is also the one where the momentum signal looks weakest, which is what you would expect if part of the other sleeves' "edge" is the bias.

## What it means

Every backtest number in this repo is an **upper bound** of unknown size. The mechanism to fix it (point-in-time constituents, `constituents.py`) is built; the data is not. Until a membership file exists, treat backtests as evidence the pipeline works, not as evidence of what the strategy will earn.

## Related
- [[Full System Review 2026-09]] · [[No-Lookahead]] · [[Sharpe Ratio]]
