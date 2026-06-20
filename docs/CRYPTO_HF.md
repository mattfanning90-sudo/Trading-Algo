# High-frequency *capable* crypto (the honest version)

You asked for high-frequency trading. [`docs/HFT_REALITY.md`](HFT_REALITY.md)
explains why genuine microsecond HFT — in FX or equities — is out of reach for
this stack and would be dishonest to fake. **Crypto is the one place where a
retail/prosumer system can legitimately move from once-a-day to second-to-minute
scale**, and this document is the straight story on why, what's built, and where
the real (small) edges actually are.

## Why crypto is different

Crypto is the one market that hands a small trader **institutional-grade data for
free**:

| | FX / equities | Crypto |
|---|---|---|
| Tick / 1-minute OHLCV | vendor fees, delayed (Yahoo ~15 min) | **free public API, real-time** |
| Order book depth | paid feed / membership | free public API |
| Funding / financing | broker-quoted, opaque | **published funding rate every 8 h** |
| Market hours | 24×5, weekend gap | **24/7**, no gap |
| Access | prime broker / exchange membership | a free API key (or none for data) |

So the *data* prerequisite that blocks live intraday FX (you need a paid
real-time feed) simply isn't there for crypto. `ccxt` gives you Binance/OKX/Bybit
1-minute bars and funding rates with no membership and no vendor bill.

## What this is NOT

This is **not** microsecond HFT. You will not beat Wintermute, Jump, or GSR on
latency — they colocate next to the matching engine and trade in nanoseconds.
On the pure speed race you are structurally the one being picked off.

What it **is**: fast, high-turnover *systematic* trading on the minute-to-second
scale, where the edge comes from **signal and structure, not from being the
fastest packet**. That band is real and accessible.

## The realistic (small) edges

1. **Minute-scale signals on rich free data.** The same agent ecosystem
   (trend / breakout / mean-reversion / momentum), just on 1-minute bars with
   shorter windows. Crypto's volatility and retail flow make short-horizon trend
   and mean-reversion *more* persistent than in G10 FX — but costs bite hard at
   this turnover, so the churn band and vol targeting matter more, not less.
2. **Funding-rate / cash-and-carry harvesting.** A perpetual swap that trades
   above spot pays a **funding rate** from longs to shorts (and vice versa). A
   delta-neutral book — long spot, short the perp — collects that funding with no
   directional exposure. It's the closest thing crypto has to a structural,
   capacity-bearing carry edge. `crypto_data.fetch_funding()` pulls the live
   rate; the data layer is wired, the delta-neutral execution leg is the next
   build (spot swap carry is 0 today, so the technical agents drive the current
   book — funding is reported, not yet traded).

Neither is a money printer. Funding compresses when everyone crowds it; minute
signals decay and are eaten by spread. Treat this as "honest fast", not "alpha
faucet" — the same rigour (Deflated Sharpe, PBO, costs always on) applies.

## What's built

* **`crypto_data.py`** — `ccxt` OHLCV panels (`load_ohlcv`), live funding rates
  (`fetch_funding`), and an offline `synthetic_crypto_panel` for tests. `ccxt` is
  an optional dependency, imported lazily, so the package still works offline.
* **`hf_crypto` profile** (`fx_config.py`) — short windows (12/48 EMA, 24-bar
  Donchian), crypto-sized risk (20% target vol, 0.40 per-pair cap, 15% drawdown
  stop), and a wider churn band (`rebalance_min_delta=0.05`) to keep 1-minute
  turnover — and cost — sane.
* **Crypto universe** — `BTCUSD / ETHUSD / SOLUSD`, already in `pairs.py` with
  realistic (~0.2% round-trip) crypto spreads. An `hf_crypto` book trades these
  only; the vol-targeting layer sizes their higher volatility down automatically.
* **End-to-end `--exchange` + `--bar` plumbing** through `run_backtest`, the
  paper book, and the engine — one source of truth, same `compute_targets`.

## Running it

```bash
pip install ccxt                                     # optional crypto extra

# Offline pipeline check (no network, synthetic minute bars):
python -m trading_algo.forex.run_backtest --synthetic --profile hf_crypto --bar 1m

# Real exchange data (needs internet; public data, no API key for OHLCV):
python -m trading_algo.forex.run_backtest --profile hf_crypto --bar 1m --exchange binance

# Open a high-frequency crypto paper book and run a cycle:
python -m trading_algo.forex.paper --init --account cryptohf --profile hf_crypto
python -m trading_algo.forex.engine --once --account cryptohf --bar 1m --exchange binance

# Run it continuously (crypto is 24/7, so the FX-week gate is skipped):
python -m trading_algo.forex.engine --loop --interval 60 --account cryptohf \
    --bar 1m --exchange binance
```

## Deployment: where it has to run

GitHub Actions and most shared cloud are **wrong** for this — scheduled runners
are minutes-granular and far from any exchange. To actually trade minute bars you
want:

* a small **VPS in the same region as the exchange** (e.g. Tokyo/AWS
  ap-northeast-1 for Binance) — not for nanosecond latency, but so a 1-minute
  bar is fetched and acted on well inside the minute;
* `engine --loop --interval 60` (or finer) running as a long-lived process
  (systemd / supervisor), not a cron one-shot;
* an exchange **API key with trade permission** for live execution (data needs
  none). Store it in the environment, never in the repo.

## Risks specific to crypto (read before funding real money)

* **Costs at turnover.** At 1-minute cadence you cross the spread constantly;
  a strategy that looks great gross is often negative net. The backtest keeps
  costs on — believe the net line, not the gross.
* **Exchange / counterparty risk.** Funds sit on the exchange; exchanges have
  failed (FTX). Withdrawal freezes, hacks, and de-pegs are real.
* **Funding flips.** The cash-and-carry trade can turn negative carry when
  sentiment flips; it is not riskless income.
* **Regulation & tax.** Crypto derivatives access and tax treatment vary by
  jurisdiction — check yours.
* **Leverage.** Perps offer huge leverage; the drawdown breaker and vol target
  are there for a reason. Do not override them to chase size.

## Bottom line

Crypto is the honest home for "faster" here: free real-time data removes the gate
that blocks live intraday FX, and there are genuine minute-scale and funding-rate
edges. It is **not** microsecond HFT, the edges are small and decay, and costs at
this turnover are brutal — so the system keeps every guardrail (costs on, vol
target, drawdown breaker, Deflated-Sharpe/PBO research) firmly in place.
