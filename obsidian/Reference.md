---
title: Reference
type: reference
tags: [trading, reference, generated]
created: 2026-06-11
up: ["[[Multi-Region Momentum]]"]
---

# 📓 Reference — settings, costs, commands

> [!note] Generated from code
> The tables below are produced by `tools/build_obsidian_vault.py` from
> `trading_algo/regions.py` and `config.py`. Re-run `make obsidian` after
> changing settings so this note stays truthful.

## Region settings & cost schedules

| Region | Index | Ccy | IBKR | Hours (local) | Comm | Min | Slip | Stamp duty |
|--------|-------|-----|------|---------------|------|-----|------|------------|
| ASX | ^AXJO | AUD | ASX | 10:00–16:00 | 8 bps | 5 | 10 bps | – |
| US | ^GSPC | USD | SMART | 09:30–16:00 | 2 bps | 1 | 5 bps | – |
| FTSE | ^FTSE | GBP | LSE | 08:00–16:30 | 5 bps | 1 | 8 bps | **50 bps (buys)** |

> [!info] LSE pence → pounds
> LSE shares quote in pence; the FTSE sleeve scales prices by `0.01` so it's
> internally consistent in GBP.

## Strategy parameters (defaults)

| Param | Value | Meaning |
|-------|-------|---------|
| `lookback_days` | 252 | 12-month momentum window ([[12-1 Momentum]]) |
| `skip_days` | 21 | days skipped (recent-month reversal) |
| `top_n` | 10 | names held per sleeve |
| `max_weight` | 15% | single-name cap |
| `target_vol` | 12% | annualised vol target ([[Volatility Targeting]]) |
| `vol_lookback` | 63 | realised-vol window (days) |
| `stock_trend_ma` | 200 | per-stock trend MA ([[Regime & Trend Filters]]) |
| `index_trend_ma` | 200 | index regime MA |
| allocation | ASX 33% / US 33% / FTSE 33% | capital split |
| base currency | AUD | reporting unit |

## Commands

```bash
python -m trading_algo.run_backtest --synthetic     # full AUD portfolio, offline
python -m trading_algo.run_backtest --region US     # one sleeve
python -m trading_algo.sweep --region US            # robustness sweep
python -m trading_algo.paper_trade --account full --init --capital 100000
python -m trading_algo.dashboard --account full     # live web dashboard :8787
make obsidian                                       # regenerate this vault
pytest -q                                           # tests
```

Related: [[Multi-Region Momentum]] · [[How It Works]]

#trading/reference
