# Free intraday data feeds (the honest options)

You asked whether there are free open-source "terminals" we can use for intraday.
Short answer: **yes — but with one important distinction.**

> **Open-source *software* and free real-time *data* are two different things.**
> Open-source terminals/frameworks are abundant and excellent. Free real-time
> *data feeds* are the actual bottleneck — and they're only fully free (and fully
> open) for **crypto**. For FX and equities the realistic free route is a **free
> broker practice account** behind an open-source wrapper, not a pure open feed.

This system is **source-agnostic**: every feed returns the same panel shape
(`dict[symbol -> OHLC]`, aligned + forward-filled), so the agents, ensemble, risk
layer, paper book and backtest are identical no matter where the data comes from.
Pick a source with `--source` (and `--bar` for the interval).

## The sources

| `--source` | Asset class | Real-time? | Cost / access | Open source? |
|-----------|-------------|-----------|---------------|--------------|
| `yahoo` *(default)* | FX majors + crypto | ❌ delayed ~15 min, history-limited intraday | free, no key | data: no |
| `crypto` | BTC / ETH / SOL | ✅ yes | **free, no key** | **`ccxt` (MIT)** |
| `oanda` | FX majors | ✅ yes (streaming) | free **practice account** + token | wrapper `oandapyV20` (open) |
| `alpaca` | US equities | ✅ yes (IEX feed) | free account + keys | SDK `alpaca-py` (open) |
| `openbb` | equities / FX / crypto | ⚠️ mostly delayed | free (key per provider) | **`openbb` (open)** — research, not a live feed |

Each live source's library is an **optional dependency, imported lazily**, so the
package still works offline; every source also ships a synthetic generator, so the
whole pipeline is testable with no network and no keys.

```bash
pip install ".[feeds]"     # ccxt + oandapyV20 + alpaca-py  (or install one at a time)
pip install ".[openbb]"    # heavier; research only
```

## The honest bottom line per asset class

* **Crypto — fully solved.** `ccxt` + exchange WebSockets give real-time 1-minute
  bars, order books and funding rates for free, no key, fully open-source. This is
  *why* crypto is the honest home for "faster" (see `CRYPTO_HF.md`).
* **FX — free but not a pure open feed.** There is no central FX tape, so "free
  open-source FX data" doesn't really exist. The realistic free path is an
  **OANDA practice account**: real-time streaming, $0, open-source wrapper. That's
  the missing piece that makes *live* intraday FX possible (Yahoo intraday is
  delayed and history-limited).
* **US equities — free, but the free tier is a partial tape.** **Alpaca** gives
  free real-time bars + free paper trading + an open SDK, but the free feed is
  **IEX only** (a single venue ≈ a few % of consolidated volume). Genuinely usable
  for intraday research/paper; keep size and microstructure assumptions modest.
  Full consolidated (SIP) data is a paid subscription.
* **OpenBB — the open-source "terminal", but for research.** It aggregates many
  providers behind one SDK and is the closest thing to a free Bloomberg Terminal,
  but it is a **research/backtest** source, not a live execution feed (its free
  providers are mostly delayed). Use crypto/OANDA/Alpaca for live.

## Credentials (never commit these)

Set as environment variables; the loaders read them and refuse with a helpful
message if missing.

| Source | Env vars |
|--------|----------|
| `oanda` | `OANDA_API_TOKEN`, `OANDA_ENV` (`practice` default / `live`) |
| `alpaca` | `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` |
| `openbb` | configured per provider inside OpenBB (default `yfinance` needs none) |
| `crypto` | none for market data (a key is only needed for live order execution) |

## Running it

```bash
# Backtests (synthetic = offline pipeline check; drop it for real data + keys)
python -m trading_algo.forex.run_backtest --synthetic --source alpaca --bar 1h
python -m trading_algo.forex.run_backtest --source oanda  --bar 1h    # real FX (needs token)
python -m trading_algo.forex.run_backtest --source crypto --bar 1m    # real crypto

# Paper books on a given source (the book remembers its source)
python -m trading_algo.forex.paper --init --account fx   --source oanda  --profile intraday
python -m trading_algo.forex.paper --init --account usEq --source alpaca --profile intraday

# Run a cycle / loop on that source
python -m trading_algo.forex.engine --once --account fx   --source oanda  --bar 1h
python -m trading_algo.forex.engine --loop --account usEq --source alpaca --bar 1h --interval 60
```

A book opened with `--source X` stores it, so later runs don't need the flag
again; pass `--source` at run time only to override.

## Notes & caveats

* **AUD currency translation is on**: a pair settles in its quote currency (EURUSD
  in USD, USDJPY in JPY), so the AUD book now translates every position's
  quote-currency P&L back to AUD using the majors in the panel (AUDUSD as the hub)
  — AUD/USD moves are part of your real P&L. See `fxconv.py` and the "From AUD to a
  trade" flow on the dashboard's How page. Crypto/equity-only books (no AUDUSD in
  their panel) fall back to no translation until an AUD/USD rate is present.
* **US equities in an AUD book**: equity **borrow/financing carry is not modelled**
  (swap = 0); treat equity P&L as price-only. Fine for paper/research, not a
  financing model.
* **Costs still always on**: every source crosses half the dealing spread defined
  in `pairs.py`. Intraday turnover makes costs bite harder — believe the net line.
* **Market hours**: the engine idles outside the FX week for FX/equities but runs
  24/7 for crypto. (Equity sessions aren't separately gated yet — a daily/hourly
  cron is the simple fix; intraday-session gating is a future refinement.)
* **Open-source live frameworks** if you outgrow this: NautilusTrader and
  QuantConnect **Lean** do live trading with broker adapters; `ib_async` (the
  maintained fork of `ib_insync`, which we already mirror for execution) covers
  IBKR; Dukascopy has a large free FX **tick history** for backtests.
