---
title: Regime & Trend Filters
type: concept
tags: [trading, risk, filter]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🛡️ Regime & Trend Filters

## Per-stock trend
Eligible only if **price > 200-day MA** — keeps you out of falling knives that
merely fall slower than peers.

## Index regime (crash protection)
The index (ASX 200 / S&P 500 / FTSE 100) must be **above its own 200d MA**, else
the sleeve goes **100% cash** (`RISK_OFF`).

> [!warning] Why it matters most
> Momentum books blow up in the violent rebound *after* a bear market. Sitting in
> cash while the index is below trend sidesteps exactly that.

> [!example] Seen live (2026-06-11)
> ASX 200 below its 200d MA → ASX sleeve 100% cash, while US & FTSE stayed invested.

Eligibility = momentum > 0 **AND** above 200d MA **AND** regime risk-on.

Related: [[How It Works]] · [[12-1 Momentum]] · [[Volatility Targeting]]

#trading/risk
