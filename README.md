# Trading-Algo — Multi-Region Cross-Sectional Momentum

A monthly-rebalanced **12-1 cross-sectional momentum** system that runs three
independent regional sleeves — **FTSE (London)**, **US (stocks + ETFs)** and
**ASX (Australia)** — and reports combined performance in a single base currency
(**AUD**). Built to run unattended in the background: a backtester, a persistent
paper-trading simulator, an IBKR execution layer, and a timezone-aware scheduler.

It started life as an ASX-only sleeve (see `HANDOFF.md`) and was generalised so
every market-specific detail — universe, regime index, currency, fees, calendar,
broker routing — lives in one `Region` record.

---

## Why this strategy

- **12-1 momentum** is the most replicated anomaly in finance (Jegadeesh &
  Titman, 1993) and holds across developed equity markets. Skipping the most
  recent month avoids short-term reversal.
- **Dual trend filters**: each name must be above its 200-day MA, and the
  region's index (ASX 200 / S&P 500 / FTSE 100) must be above *its* 200-day MA,
  otherwise that sleeve de-risks to cash. This is what protects momentum books
  from crashes in sharp post-bear rebounds.
- **Inverse-vol weighting + a 12% vol target** keeps risk stable instead of
  letting the hottest names dominate.
- **Monthly rebalance** keeps turnover ~25–35%/month so the edge survives
  commissions, slippage and (in the UK) stamp duty.
- **Three uncorrelated-ish regional books** diversify the single-market timing
  risk. In the synthetic smoke test the combined book's volatility sits *below*
  every individual sleeve — diversification doing its job.

---

## Architecture

```
trading_algo/
  config.py            StrategyParams (the knobs) + portfolio settings (allocations, base ccy)
  regions.py           Region registry: universe, index, currency, fees, calendar, IBKR routing
  universes.py         the per-region ticker lists (FTSE 100 / S&P 500 + ETFs / ASX)
  signals.py           12-1 momentum, trend & regime filters, inverse-vol selection
  strategy.py          compute_targets() — THE single source of truth for weights
  fees.py              per-region commission floors + UK stamp duty (buys only)
  calendars.py         per-region market hours / timezones
  fx.py                FX conversion of each sleeve into the base currency
  metrics.py           CAGR / Sharpe / Sortino / MaxDD / Calmar
  backtest.py          per-sleeve no-lookahead walk-forward sim
  portfolio_backtest.py  combine sleeves in AUD (with FX P&L) + allocation rebalancing
  paper_trade.py       persistent multi-region paper-trading simulator
  execution_ibkr.py    ib_insync execution, per-region exchange/currency routing
  engine.py            background scheduler — runs each sleeve after its market close
tests/                 49 tests: invariants, FX, fees, calendars, end-to-end synthetic
```

### Regional configuration

| Region | Index | Currency | IBKR exch | Hours (local) | Commission | Stamp duty |
|--------|-------|----------|-----------|---------------|------------|------------|
| ASX    | ^AXJO | AUD      | ASX       | 10:00–16:00   | 8 bps, min A$5 | – |
| US     | ^GSPC | USD      | SMART     | 09:30–16:00   | 2 bps, min $1  | – |
| FTSE   | ^FTSE | GBP      | LSE       | 08:00–16:30   | 5 bps, min £1  | **50 bps on buys** |

LSE shares are quoted in pence; the FTSE sleeve scales prices to pounds
(`price_scale = 0.01`) so it is internally consistent in GBP.

---

## Quick start

```bash
pip install -r requirements.txt

# Backtests (real data downloads via yfinance)
python -m trading_algo.run_backtest                 # full AUD portfolio (all 3 sleeves)
python -m trading_algo.run_backtest --region US     # single sleeve, local currency
python -m trading_algo.run_backtest --synthetic     # offline pipeline test, no network

# Paper trading (persistent, no broker needed)
python -m trading_algo.paper_trade --account full --capital 100000 --init
python -m trading_algo.paper_trade --account full          # daily run (all sleeves)
python -m trading_algo.paper_trade --account full --status
python -m trading_algo.paper_trade --compare micro full

# Run it in the background
python -m trading_algo.engine --once --account full        # one pass (cron-friendly)
python -m trading_algo.engine --loop --account full        # daemon: wakes at each close

# Tests
pytest -q
```

### Scheduling (cron, UTC) — fire after each regional close

```cron
# ASX ~06:00 UTC, LSE ~15:30 UTC, US ~21:00 UTC (winter; engine handles DST)
0 6,15,21 * * 1-5  cd /path/to/Trading-Algo && \
    python -m trading_algo.engine --once --account full >> paper.log 2>&1
```

`run_daily` is idempotent within a day — each sleeve rebalances only on the
first run of a new month and marks to market otherwise — so running it after
every regional close is safe.

---

## Going live (paper first)

1. Run IB Gateway / TWS with the API enabled. Paper port = 7497, live = 7496.
2. Compute weights and preview orders per region:
   ```python
   from trading_algo import data, strategy
   from trading_algo.regions import get_region
   from trading_algo.execution_ibkr import rebalance

   region = get_region("US")
   prices, index_px = data.load_region(region, "2012-01-01")
   weights = strategy.compute_targets(prices, index_px, region.params)
   orders = rebalance("US", weights, dry_run=True)   # preview only
   ```
3. Set `dry_run=False` only after several rebalances of previews look sane.

---

## Invariants (do not break — enforced by tests)

1. **No lookahead.** Signals at *t* use data ≤ *t*; trades execute *t+1*.
   (`test_signals`, `test_strategy`, `test_backtest`)
2. **Costs always on.** Commission + slippage on turnover every rebalance, plus
   UK stamp duty on buys. (`test_fees`, `test_backtest`)
3. **One weight function.** Backtest and paper trading both call
   `strategy.compute_targets`. There is no second copy to drift. (`test_consistency`)
4. **Whole shares** in paper trading; per-region commission floor respected.
5. **Synthetic results are plumbing tests only** — never presented as performance.

---

## Base-currency handling (AUD)

Each sleeve trades in its local currency. For combined reporting an AUD investor
also earns/loses the currency move, so sleeve returns are converted as:

```
r_AUD = (1 + r_local) · (fx_t / fx_{t-1}) − 1      (fx = AUD per local unit)
```

- The **portfolio backtest** trues the three sleeves back to the target
  allocation (equal third) each period, charging an FX spread on cash that
  crosses currencies.
- The **paper simulator** funds each sleeve once and lets allocations drift
  (the realistic treasury model — periodic cross-border rebalancing is a manual
  operation). The two are intentionally different; both are documented.

---

## Known limitations (read these)

- **Survivorship bias.** Universes are *today's* liquid constituents, so the
  backtest is an upper bound on the live edge. Fix: point the data layer at
  point-in-time constituents (e.g. Norgate for ASX) before trusting absolute
  numbers.
- **yfinance data quirks.** Adjusted closes can carry split/dividend errors,
  especially on ASX/LSE names; LSE pence vs pounds is handled but spot-check
  anything that looks too good.
- **No tax / franking modelled.** Australian franking credits *help* a long-only
  ASX book (so omitting them is conservative); monthly turnover means most gains
  are short-term — run inside the right structure.
- **Calendars are weekday+hours only** (no public-holiday table). For a monthly
  strategy keyed off the data's last session this only affects *when* the
  scheduler wakes, never the trade decision.
- A backtest is a hypothesis, not a promise. **Paper trade for at least three
  months** before risking capital, and size it as one sleeve of a portfolio.

---

## Where to take it next

- Point-in-time constituents to kill survivorship bias (the big one).
- Walk-forward robustness sweep (TOP_N 8–15, lookback 6–12m): look for a flat
  performance surface, not a peak — a peak is curve-fitting.
- Per-region parameter tuning via `StrategyParams` overrides on each `Region`.
- Equity-curve plotting / monthly PDF reports.
- Plug in as the "Strategy agent" of a multi-agent stack: each sleeve emits
  target weights, a Decision agent gates execution (incl. a min-account-size check).
