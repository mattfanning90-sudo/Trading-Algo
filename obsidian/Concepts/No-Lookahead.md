---
title: No-Lookahead
type: concept
tags: [trading, backtesting, invariant]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🕰️ No-Lookahead

A decision at time *t* uses only data available at *t*, and is acted on at *t+1*.
Weights are decided on the month's last trading day from data ≤ t, applied the
next day.

> [!danger] Why it's sacred
> The easiest way to fake a brilliant backtest is to peek at tomorrow's price.
> Every signal here is built from `shift`ed past prices.

> [!check] Enforced in code
> `tests/test_signals.py` and `tests/test_strategy.py` assert a signal at *t* is
> identical whether computed on full history or only data up to *t*.

Related: [[How It Works]] · [[12-1 Momentum]]

#trading/backtesting
