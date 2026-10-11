# Architecture — where everything lives

The single home for the module map. Other docs link here instead of copying it
(see `docs/README.md`). The rules that must never break are in `CLAUDE.md`
("Invariants"); the plain-English walkthrough of the algorithm is
`docs/HOW_IT_WORKS.md`.

**Plain-English key:** a *sleeve* is one country's slice of a book (US, UK/FTSE,
Australia/ASX, Canada/TSX); a *book* is one paper-trading account; *paper
trading* means real prices, pretend money.

## The equity system (`trading_algo/`)

Everything region-specific lives in one `Region` record, so adding a country is
one registry entry.

| Module | What it does |
|---|---|
| `config.py` | `StrategyParams` (every strategy knob, incl. `long_short` / `short_n` for a dollar-neutral book), portfolio settings (`ALLOCATIONS` — four sleeves at 25% each by default — `BASE_CURRENCY` = AUD, FX rebalance cadence/spread, `START`, capital) and risk controls (drawdown circuit breaker, min-viable-size gate). |
| `profiles.py` | Named paper-book presets (`ultra`, `experimental`): per-book knob overrides, a breaker override, and a reporting `group`. A profile changes the *knobs* only — weights still go through the one `strategy.compute_targets` (invariant 3). |
| `regions.py` / `universes.py` | The region registry (universe, regime index, currency, fees, calendar, Yahoo suffix, IBKR exchange, `price_scale`, per-region overrides) and the per-region ticker lists. |
| `data.py` | `load_region()` — prices in local currency (LSE pence → pounds) — and `synthetic_region()` for offline tests. |
| `signals.py` | Momentum score, trend/regime filters, inverse-volatility selection. Region-agnostic. |
| `strategy.py` | **`compute_targets()` — the single source of truth for target weights** (`precompute` builds the signal panel once; `targets_at` is "the one weight formula"). Backtest and paper trading both call it. |
| `fees.py` | Per-region commission floor + UK stamp duty (buys only). |
| `calendars.py` | Per-region market hours and timezones for the scheduler. |
| `fx.py` | Converts each sleeve into AUD, including FX profit and loss. |
| `metrics.py` | **The measurement layer: one module owns every number a human reads** (Sharpe, drawdown, CAGR, annualisation). `tests/test_metrics_single_source.py` fails if any other module computes one for display. |
| `backtest.py` / `portfolio_backtest.py` | Per-sleeve daily walk-forward simulation; combining sleeves in AUD with allocation rebalancing. |
| `paper_trade.py` | Persistent paper books (`paper_state_{account}.json` + `paper_books.db`). Decides at one close, fills at the next. |
| `execution_ibkr.py` | Per-region exchange/currency routing for Interactive Brokers (paper port 7497). |
| `engine.py` | Background runner (`--once` for cron, `--loop` for a daemon). |
| `constituents.py` | Point-in-time index membership (the survivorship-bias fix). |
| `sweep.py` | Walk-forward parameter robustness sweep (looking for a flat surface, not a peak). |
| `tax.py` | **After-tax reporting** — not tax advice and not a trading input. Australian CGT over the FIFO round-trips plus the dividend-withholding drag the total-return series hides. The finding that motivated it: across the four equity books, 0 of 50 realised round-trips cleared the 12 months a CGT discount needs, so holding period is a strategy parameter, not just a cost. |
| `verify.py` | **End-to-end audit of the live books.** Re-derives each saved book from its own trade ledger and flags what a real broker statement would contradict (reconciliation drift, fills on a closed market, trades at a stale price, sleeves silently in cash, holds shorter than the signal's horizon). Findings are graded (ERROR / WARN / INFO); old realism findings age out to INFO after `HISTORICAL_CUTOFF_DAYS`. Sleeve- and pair-level findings carry `detail["subject"]`. Runs after every scheduled job: a report step that can't fail the job, then a strict gate. |

### Reporting groups
`dashboard/overview.py` puts every book in a group. **CORE** (the funded equity
books) is the headline total. **EXPERIMENTAL** (profiled equity books such as
`ultra`, `experimental`) and **FX** (every FX book, which back-test negative) each
get their own separate total and never move the headline.

### Regions: registered vs funded (the backtest gate)
Being in `REGIONS` makes a sleeve backtestable and sweepable; it receives live
capital only once its key is in `config.ALLOCATIONS`. Flow: register → backtest →
*then* fund. **TSX (Canada, CAD)** went through the whole gate (backtested
2026-09-19, funded at 25%). Its gate number restated accurately is SR 0.742 /
DSR 0.82 at N=20, and **no sleeve passes a standalone DSR gate**, so TSX is held
on its measured portfolio contribution (+0.064 Sharpe, vol −0.52pp,
P(Δ>0)=0.885). Still survivorship-biased, so an upper bound — see
`docs/SHARPE_RESEARCH.md` §9b. An *existing* paper book keeps the allocations
baked in at `--init`; adding a region doesn't retrofit it.

## The FX subsystem (`trading_algo/forex/`)
Self-contained foreign-exchange (and crypto) trader: parallel technical agents →
performance-weighted ensemble → volatility-targeted long/short book → isolated
paper books (e.g. `matt`, `partner`). Shares the equity system's principles (no
lookahead, costs on, one weight function) but is otherwise independent. Working
rules: `trading_algo/forex/CLAUDE.md`; full tour: `trading_algo/forex/README.md`;
data sources: `docs/DATA_FEEDS.md`; deep-learning layer: `docs/FX_DEEP_RESEARCH.md`.

## Dashboards (`trading_algo/dashboard/`)
- **Terminal** — stdlib web server + a single-page app (`static/app.js`): every
  book behind one switcher, OVERVIEW / POSITIONS / BACKTEST / METHOD tabs (+ SWARM
  on FX books). Equity and FX books are at parity in money terms. Also exported
  as static files and published to GitHub Pages after each scheduled run.
- **Home screen API** — `dashboard/home.py` builds `/api/home` ("am I up, and is
  anything wrong?") from `overview`, `verify` and the home package.
Working rules: `trading_algo/dashboard/CLAUDE.md`.

## The Mac home screen (`trading_algo/home/`)
`python -m trading_algo.home` fetches `main`'s saved book state from GitHub into a
private, read-only snapshot, judges the scheduled jobs, remembers which problems
you've seen, and opens the Midnight home screen. Working rules:
`trading_algo/home/CLAUDE.md`; design: `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`;
spec: `docs/specs/home-screen.md`.

## Where the live books run
Three GitHub Actions jobs run the books and commit `state/` to `main`:
`paper-trade.yml` (equity, weekdays 21:30 UTC), `fx-paper.yml` (FX, weekdays 23:00
UTC) and `day-paper.yml` (DAYTRADER, hourly on weekdays). **Only these jobs write
`state/`.** Measured 2026-10-10: the daily jobs start a median ~80 min late
(worst ~8 h), and GitHub skips most of the hourly runs (2–6 of 23 a day since
early September).
