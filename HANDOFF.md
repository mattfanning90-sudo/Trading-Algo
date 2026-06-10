# HANDOFF

## Original design session (10 June 2026) — ASX-only sleeve

The system began as a single ASX cross-sectional momentum book. Decisions and
reasoning from that conversation, preserved so nothing needs re-litigating:

1. **Strategy: 12-1 cross-sectional momentum, monthly rebalance.** Chosen over
   mean reversion and intraday ML because it's the most replicated anomaly,
   holds on the ASX, and survives retail transaction costs. Dual trend filters
   (per-stock 200d MA + index 200d MA regime filter) protect against momentum
   crashes.
2. **Execution target: IBKR** (existing broker choice). ib_insync layer,
   paper port 7497, dry_run defaults.
3. **Two paper accounts as a deliberate experiment:** `micro` (small, to
   demonstrate fee drag) and `full` (the realistic test).
4. **Backtest is an upper bound** due to survivorship bias — point-in-time
   constituents are the prerequisite before trusting absolute numbers.

## Continuation (10 June 2026) — multi-region expansion

The brief: take the ASX sleeve global across **FTSE (London) + US stocks/ETFs +
ASX**. Decisions taken this session:

| Decision | Choice |
|---|---|
| Architecture | **Separate regional sleeves** (each its own regime index, currency, fees, calendar) + a portfolio layer |
| Base currency | **AUD** (each sleeve trades local; combined equity via FX) |
| Universe | **Broad liquid names per region + major US ETFs** |
| Allocation | **Equal third each**, rebalanced to target (backtest); funded-once (paper) |

### What was built
- Generalised the package `asx_momentum` → `trading_algo`. Every market-specific
  detail now lives in a `Region` record (`regions.py`), so adding a 4th market
  is one entry.
- Added: `regions.py`, `universes.py`, `fx.py`, `fees.py`, `calendars.py`,
  `metrics.py`, `strategy.py`, `portfolio_backtest.py`, `engine.py`.
- **Refactored the shared target-weight logic into `strategy.compute_targets`**
  (was the #1 roadmap item / invariant #4). Backtest and paper trading now call
  one function — `tests/test_consistency.py` enforces it. The two slightly
  different copies of the vol-targeting math were unified into one correct
  constant-average-correlation estimate.
- Modelled **UK stamp duty** (0.5% on FTSE buys) and **LSE pence→pounds** scaling
  — both materially affect a UK momentum book and were previously absent.
- Added a **49-test suite** (was 0): no-lookahead, costs-on, fees/stamp duty,
  FX, calendars, region registry, and end-to-end synthetic backtest + paper runs.
- Added the **background scheduler** (`engine.py`) that wakes after each region's
  market close (DST-aware), plus a cron-friendly `--once` mode.

### State of the world
- Whole pipeline smoke-tested end-to-end on **synthetic data** (sandbox had no
  market data access — Yahoo 403). All 49 tests green. **Real-data backtest has
  NOT been run** — that's the first thing to do on a networked machine.
- Synthetic portfolio run sanity-checks out: combined vol < each sleeve's vol
  (diversification working); FTSE buys show stamp duty, US/ASX don't.

### Immediate task list (in order)
1. On a networked machine: `pip install -r requirements.txt`, then
   `python -m trading_algo.run_backtest` — sanity-check combined CAGR/Sharpe/
   maxDD and eyeball each sleeve's current top picks.
2. Init paper accounts (`--account full --capital 100000`, `--account micro
   --capital 100`) and do the first daily run for each.
3. Set up cron / `engine --once` after each regional close (see README).
4. Tackle survivorship bias (point-in-time constituents) before trusting
   absolute backtest numbers.

### Still open (roadmap)
- Point-in-time constituents (survivorship bias) — the big one.
- Walk-forward robustness sweep (flat surface, not a peak).
- Equity-curve plotting / monthly reports.
- Cross-border allocation rebalancing in the paper sim (currently funded-once).

## Bigger picture
This is the "Strategy agent" sleeve of a larger multi-agent hedge-fund
architecture: it emits target weights; a Decision agent should gate execution
(including a minimum-account-size pre-trade check). Keep interfaces clean.
