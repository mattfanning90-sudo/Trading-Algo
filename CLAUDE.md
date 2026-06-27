# Multi-Region Momentum Trading System

## What this is
A monthly-rebalanced 12-1 cross-sectional momentum strategy run as three
independent regional sleeves — FTSE (London), US (stocks + ETFs) and ASX
(Australia) — with a portfolio layer that allocates capital (equal third each)
and reports combined equity in a base currency (AUD). Includes a no-lookahead
walk-forward backtester, a persistent multi-region paper-trading simulator, an
IBKR (ib_insync) execution layer, and a timezone-aware background scheduler.

Generalised from the original ASX-only sleeve — see `HANDOFF.md` for the design
history and reasoning.

There is also a **separate, self-contained FX subsystem** under
`trading_algo/forex/` — a low-latency, multi-agent foreign-exchange trader
(parallel technical agents → performance-weighted ensemble → vol-targeted
long/short book → isolated multi-account paper books, e.g. you + your partner).
It reuses this project's principles (no lookahead, costs always on, one shared
`compute_targets`) but is otherwise independent of the equity sleeves. Full tour:
`trading_algo/forex/README.md`.

## Architecture (everything region-specific lives in one `Region` record)
- `config.py` — `StrategyParams` (all strategy knobs) + portfolio settings
  (ALLOCATIONS, BASE_CURRENCY, FX rebalance cadence/spread, START, capital) +
  risk controls (drawdown circuit breaker, min-viable-size gate)
- `regions.py` — Region registry: universe, regime index, currency, fee
  schedule, market calendar, Yahoo suffix, IBKR exchange, price_scale, per-region
  param overrides
- `universes.py` — the per-region ticker lists
- `data.py` — `load_region()` (prices in local currency, LSE pence→pounds) +
  `synthetic_region()` for offline testing
- `signals.py` — momentum score, trend/regime filters, inverse-vol selection.
  Region-agnostic; takes a `StrategyParams`
- `strategy.py` — **`compute_targets()`: the single source of truth for target
  weights** (selection + vol targeting). Both backtest and paper trading call it
- `fees.py` — per-region commission floor + UK stamp duty (buys only)
- `calendars.py` — per-region hours/timezones for the scheduler
- `fx.py` — convert each sleeve into the base currency (incl. FX P&L)
- `backtest.py` — per-sleeve daily walk-forward sim
- `portfolio_backtest.py` — combine sleeves in AUD, allocation rebalancing
- `paper_trade.py` — persistent sub-books per region (`paper_state_{name}.json`)
- `execution_ibkr.py` — per-region exchange/currency routing; paper port 7497
- `engine.py` — background runner (`--once` for cron, `--loop` for a daemon)
- `constituents.py` — point-in-time index membership (survivorship-bias fix)
- `sweep.py` — walk-forward parameter robustness sweep (flat surface, not a peak)
- `trend.py` — **time-series (trend) momentum diversifier sleeve**: each ETF
  traded long/short on its own trend, vol-targeted, across equities/bonds/
  commodities/FX. Own `compute_trend_targets` (single source) + `run_trend_backtest`.
  A diversifier (low corr to equities, crisis alpha), not a return engine
- `carry.py` — **cross-asset carry diversifier sleeve**: long high-yield / short
  low-yield across yield-bearing ETFs (rates/credit/equity/real), income-yield
  carry from the total-vs-price-return gap (`data.load_carry_yields`). Own
  `compute_carry_targets` (single source) + `run_carry_backtest`. The 3rd premium
- `lsbacktest.py` — **shared long/short walk-forward engine** (`run_ls_backtest`):
  both trend and carry route through it (one L/S engine, no duplicated loop)
- `defensive_sweep.py` — compares what idle/risk-off capital earns (cash/T-bill/
  bonds/gold) per sleeve; `trend_report.py` — equity-vs-trend-vs-blend comparison
- `robust.py` — overfitting controls (Probabilistic/Deflated Sharpe, PBO via CSCV);
  `tradestats.py` — trade/period stats (win rate done right: profit factor, payoff,
  expectancy, breakeven, Wilson CI, Kelly); `stress.py` — stationary-bootstrap
  Monte-Carlo, regime-conditional stats, drawdown analytics, cost stress;
  `validate.py` — one report combining all of the above. See
  `docs/research/BACKTEST_VALIDATION.md`
- `multistrat.py` — **multi-strategy combiner**: reads N strategy return streams,
  sizes by risk (inverse-vol / equal-risk-contribution), vol-targets the book →
  an upside-taker + downside-mitigator. `multistrat_report.py` combines equity +
  trend + carry, scores it (up/down capture) vs SPY, and with `--validate` runs the
  overfitting gauntlet on the combined book (DSR/PBO deflated across the combiner's
  own knobs). The research conclusion: combine uncorrelated premia, don't chase one
  signal
- `dashboard/` — zero-dependency live web dashboard (stdlib server + vanilla SPA)

## Commands
```bash
python -m trading_algo.run_backtest                 # full AUD portfolio backtest
python -m trading_algo.run_backtest --region US     # single sleeve
python -m trading_algo.run_backtest --synthetic     # offline pipeline test
python -m trading_algo.run_backtest --point-in-time # survivorship-bias corrected
python -m trading_algo.sweep --region US            # parameter robustness sweep
python -m trading_algo.trend_report                 # equity vs trend vs blend (diversifier test)
python -m trading_algo.defensive_sweep --region US  # what idle capital should earn
python -m trading_algo.validate --region US         # win rate, Deflated Sharpe, PBO, regime, stress
python -m trading_algo.multistrat_report --validate  # equity+trend+carry book + overfitting gauntlet
python -m trading_algo.paper_trade --account full --init --capital 100000
python -m trading_algo.paper_trade --account full   # daily run (all sleeves)
python -m trading_algo.engine --once --account full # one scheduler pass
python -m trading_algo.dashboard --account full     # live web dashboard :8787
# --- FX subsystem (independent; see trading_algo/forex/README.md) ---
python -m trading_algo.forex.run_backtest --synthetic   # offline FX pipeline test
python -m trading_algo.forex.paper --init               # open matt + partner books
python -m trading_algo.forex.engine --once              # one FX decision cycle (all accts)
python -m trading_algo.forex.engine --once --ml         # ...including the deep-learning agent
python -m trading_algo.forex.engine --benchmark         # live cycle latency
python -m trading_algo.forex.train --synthetic          # train DL models + walk-forward report
python -m trading_algo.forex.dashboard --all --out-dir public  # candlestick dashboards + "why" callouts
python -m trading_algo.forex.research --synthetic       # quant-research search + Deflated-Sharpe/PBO
python -m trading_algo.forex.run_backtest --synthetic --bar 60m --profile intraday  # medium-freq
pytest -q                                           # full suite (equity + carry/multistrat + FX/ML)
```

The FX subsystem also has a **deep-learning layer** (pure-NumPy MLP with a
Sharpe-ratio loss, Hedge ensemble, meta-labeling, purged walk-forward,
Deflated-Sharpe/PBO validation). Design + citations: `docs/FX_DEEP_RESEARCH.md`.
It runs in the cloud via the **FX Deep-Learning Train & Evaluate** GitHub Action.

A **quant-research agent** (`research.py`) systematically searches candidate
edges (OU mean-reversion, trend/breakout variants, cross-sectional momentum,
stat-arb pairs) and judges each with the Deflated Sharpe + PBO. An **intraday /
medium-frequency mode** (`--bar 60m`, `intraday` profile) is supported — NOT HFT;
live intraday needs a real-time broker feed. The honest case against HFT here:
`docs/HFT_REALITY.md`.

## Invariants — do not break these
1. **No lookahead**: signals at t use data ≤ t; trades execute t+1. Any change to
   `signals.py`, `strategy.py` or `backtest.py` must preserve this.
2. **Costs always on**: never report backtest metrics without commission +
   slippage; UK stamp duty applies to FTSE buys.
3. **One weight function**: backtest and paper trading must both route through
   `strategy.compute_targets`. Do NOT add a second copy of the weight logic
   (this is what invariant #4 used to warn about — now enforced by
   `tests/test_consistency.py`).
4. **Whole shares** in paper trading; per-region commission floor respected.
5. **Synthetic-data results are pipeline tests only**; never present as performance.
6. **Each sleeve trades in its local currency**; only the portfolio/reporting
   layer converts to AUD via FX. Don't mix currencies inside a sleeve.

## Adding a region
Add one entry to `REGIONS` in `regions.py` (universe in `universes.py`, plus
index/currency/fees/calendar/routing) and include its key in
`config.ALLOCATIONS`. Everything else is parameterised.

## Environment notes
- Fresh containers do NOT ship numpy/pandas/yfinance — `pip install -r
  requirements.txt` first (a SessionStart hook in `.claude/settings.json` does
  this automatically on Claude Code web).
- Sandboxes may block outbound internet (Yahoo 403). Use `--synthetic` to
  smoke-test the pipeline offline; real backtests need network on your machine.
- Data is fetched via a pluggable provider chain (`providers.py`): yfinance
  (default), stooq (free fallback), polygon (`POLYGON_API_KEY`; US+FX+indices
  only), tiingo (`TIINGO_API_KEY`; US, **retains delisted tickers**). Set
  `MOMENTUM_DATA_PROVIDER` to pick the primary; fallbacks auto-append.
- **Survivorship-bias-free US**: `python -c "from trading_algo import constituents
  as c; c.download_constituents('US')"` fetches free point-in-time S&P 500 history
  (fja05680, incl. delisted) into the cache; then `validate --point-in-time` uses
  it + Tiingo delisted prices + `data.apply_delisting_returns`. See
  `docs/research/SURVIVORSHIP_FREE_DATA.md`. ASX/LSE delisted prices need a paid
  feed (Norgate/EODHD).

## Style
Python 3.11+, pandas/numpy, type hints, small testable modules, no heavy
frameworks. Money is always in a known currency — label it.
