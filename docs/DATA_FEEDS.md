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
| `frankfurter` | fiat FX (~30 ECB currencies) | ❌ **one fixing per working day** | **free, no key** | **`frankfurter` (open)**, data = ECB |
| `auto` | *whatever each symbol needs* | per leg | per leg | routing, not a provider |

`auto` is not a feed — it is **per-symbol routing**. A book like `matt` holds FX
majors *and* crypto in one universe, and no single provider serves both (an FX
venue has no BTCUSD; a crypto exchange has no EURUSD). `auto` groups the request
by each symbol's asset class, fetches each group from its own provider, and
merges them through the same align/forward-fill every single-source load uses.
Nothing is dropped silently: a symbol nobody can serve is reported.

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

## Close-only sources (ECB reference rates) — read this before using `frankfurter`

`frankfurter` serves the **European Central Bank's daily reference rates** —
free, keyless, open-source. It is the only FX source here that is a *citable
primary publication* rather than a scraped consumer quote (`yahoo`) or a broker's
own book behind a token (`oanda`). It is also the only source that does **not**
give you bars, and that distinction decides everything below.

**What the ECB actually publishes:** one reference rate per currency per *working
day*, fixed from a ~14:10 CET concertation and published around **16:00 CET**.
That is a single price. So:

* **No OHLC.** There is no open, no high, no low. Frames come back with
  `open == high == low == close`. That is not a fabricated candle — it is the
  honest statement that the bar has exactly one observed price — but it means
  the *intrabar range is unobserved*, not zero.
* **No intraday.** Daily only. The `daytrader` book (60m bars) **cannot** be fed
  from here at all; asking for any non-daily interval raises immediately.
* **Fiat only.** ~30 ECB currencies. No BTC/ETH/SOL (route those to `crypto`),
  no equities, and no RUB (ECB suspended it in 2022) or HRK (withdrawn 2023).
* **Not a tradable quote.** It is a reference *fixing*, not a bid/ask you could
  have dealt on, and it is only knowable after ~16:00 CET on the day it dates.
  Fine for the system's signal-at-t / execute-at-t+1 contract; do **not** treat a
  same-day fixing as available at that day's open.
* **Charts.** A close-only series has no candles to draw. Rendering it as
  candlesticks produces a doji on every bar — a bare tick — and any chart that
  plots trades against it is showing marks, not the range they traded inside.
  Draw it as a line and label the provenance.

### Why this is a *strategy* question, not a plumbing question

`indicators.py`'s range family does not fail on range-less bars. It computes a
**different statistic under the same name**. Measured on this project's own code
(the same closes, with and without real highs/lows):

| indicator | on close-only bars | effect |
|-----------|--------------------|--------|
| `true_range` | degenerates to exactly `abs(Δclose)` | ATR reads ≈ **0.53×** |
| `adx` | `DI+ + DI− ≡ 100`; ADX = 100 × smoothed efficiency ratio of closes | median 21.6 → 17.2; share of bars called "trending" 57% → **36%** |
| `donchian` | channel of *closes*, strictly narrower | **~1.4×** as many breakouts, triggered earlier |

ADX does **not** collapse to zero (the intuitive guess) — it stays plausible,
which is what makes it dangerous. Run through the real agent → ensemble → risk
path, that moves **75–85% of target weights** and puts **16–21% of bars on the
opposite side**, while gross leverage, turnover and the spread bill are
essentially unchanged. No risk or P&L report would show that anything happened.

Everything that reads only closes is **bit-identical**: `ema`, `rsi`, `roc`,
`bollinger_z`, `macd`, `realized_vol`, the vol-targeting in `risk.pair_vols`, and
the entire money layer (`marks`, `fx_pnl`, `fxconv` never read high/low). **A
fixing is a perfectly good mark. It is not a usable signal input.**

### How the system enforces that

1. `bar_quality.py` decides, *structurally*, whether a frame has a real range
   (`high != low` on at least one observed bar). Metadata may corroborate that;
   it is never required, so the answer survives a parquet round-trip.
2. The range indicators themselves refuse — `true_range`/`donchian` (and so
   `atr`/`adx`) raise `CloseOnlyBarsError`. The guard sits at the primitives so
   no consumer can forget to ask.
3. Each book chooses a policy once, in the open — `FXParams.close_only_signals`,
   overridable per book at `--init` with `--close-only-signals`:

   | policy | behaviour |
   |--------|-----------|
   | `refuse` *(default)* | The pre-signal gate raises, naming the instruments. The book does **not** trade that cycle — nothing is marked or persisted. A misconfigured book dies on its **first** cycle, and under `run_all` the other books still run. |
   | `exclude` | Those instruments are frozen out of the candidate universe exactly like a stale feed: printed, notified, persisted to `state["data_quality"]`, shown on the dashboard. **They go flat** — a real change to what the book holds. They are excluded from *scoring only*: unlike a stale or dead price, the fixing is a good mark, so it still marks any residual weight. |
   | `allow` | Trade on the degraded reading, deliberately. Printed and notified every cycle, and recorded in `state["data_quality"]["close_only"]`. The ADX/ATR/Donchian numbers in the decision book are close-derived. |

4. `feeds.ROUTES["fx"]` is `("yahoo",)` — FX signals are **not** routed to the
   ECB fixing by default. Opting in is one explicit line:
   `load_routed(syms, routes={**feeds.ROUTES, "fx": ("frankfurter", "yahoo")})`.

### What `frankfurter` is genuinely good for

Marking and AUD translation, a keyless daily close series (`--source
frankfurter`), anything that consumes closes only, and cross-checking another
feed's FX closes against a citable central-bank reference — which would catch a
bad Yahoo tick that nothing else here would.

What it is *not* good for, including in research: the FX **backtest** runs the
same agents and therefore the same range indicators, so
`run_backtest --source frankfurter` refuses too, by design. A backtest on
close-only bars would measure a different strategy from the one the books run.

## Which source each paper book uses

`source` lives in `fx_config.ACCOUNTS` (the default a **new** book is opened
with) and then in the book's own state. An existing book keeps its stored source
until a human overrides it once — `--source` is remembered:

```bash
python -m trading_algo.forex.paper --account matt --source auto   # switches, and sticks
```

| book | bar | source | why |
|------|-----|--------|-----|
| `matt`, `partner` | daily | **`auto`** | one universe of FX majors **and** crypto: crypto legs come from a real exchange via `ccxt` (free, keyless, genuine OHLCV — a real upgrade over Yahoo's crypto), FX legs from Yahoo bars. FX is deliberately **not** on `frankfurter`: those bars have no range, and the table above is the price of pretending otherwise. |
| `daytrader` | 60m | `yahoo` | `frankfurter` is daily-only and **cannot serve this book at all**. Routing intraday would also splice `ccxt` bar edges onto Yahoo's, which is unverified — so it stays on one intraday provider. Yahoo 60m is ~15-min delayed: a paper cadence, not a live feed. |
| `multiasset` | daily | `yahoo` | US equities + bond ETFs + an AUDUSD overlay; universe-locked. No FX-fixing or crypto source serves equities, so none of this touches it. |

If `ccxt` is missing or an exchange is down, `auto` falls back to Yahoo for the
crypto legs and says which provider failed — the FX legs are unaffected.

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

# Per-symbol routing (the FX+crypto books): real crypto via ccxt, FX bars via yahoo
python -m trading_algo.forex.paper --account matt --source auto

# The keyless ECB fixings. CLOSE-ONLY, so the default policy refuses to score
# them — this raises, names the pairs and does not trade:
python -m trading_algo.forex.paper --account matt --source frankfurter
# A book that has deliberately accepted the degraded ADX/ATR/Donchian reading:
python -m trading_algo.forex.paper --init --account ecb --source frankfurter \
    --close-only-signals allow
```

A book opened with `--source X` stores it, so later runs don't need the flag
again; pass `--source` at run time only to override — note that a run-time
`--source` is also *remembered*, which is the intended (visible, reversible) way
to switch an existing book. `--close-only-signals` is `--init`-only on purpose: a
risk policy should not be changeable by a stray flag on one run.

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
