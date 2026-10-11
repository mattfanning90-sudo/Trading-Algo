# Dashboards — working rules

Loaded when you work in `trading_algo/dashboard/`. Root `CLAUDE.md` invariants
apply. Module map: `docs/ARCHITECTURE.md`.

## What lives here
- `server.py` — stdlib web server, **loopback-only and unauthenticated** (it
  exposes positions and P&L; never bind it to a public interface). Serves the
  terminal at `/`, the Mac home screen at `/home`, JSON under `/api/*`;
  `/api/home` exists only when a builder is passed to `create_server(home=...)`.
- `registry.py` — discovers every book from its saved JSON (`_peek`, cached by
  file time). A file that won't parse is **skipped silently** here; `verify`
  reports it as ERROR `unreadable-state`.
- `overview.py` — the all-books rollup and **reporting groups**: CORE is the
  headline; EXPERIMENTAL and FX each get a separate total. Cards also expose
  `off_peak` and `breaker` — the breaker's own figures.
- `api.py`, `fx_api.py` — one book's snapshot (equity / FX), `backtest_store.py`,
  `swarm.py`, `meta.py`.
- `export.py` — writes the static payloads published to GitHub Pages after each
  scheduled run (`scripts/build_site.sh`).
- `home.py` — `/api/home`: period changes, freshness, near-breaker, the
  act-now / look-soon / clear verdict.
- `static/app.js` — the old terminal (one 3,816-line file). `static/home.*` — the
  Midnight home screen.

## Rules
- **One source per displayed number.** Sharpe, drawdown, CAGR and annualisation
  come from `trading_algo/metrics.py` only (`tests/test_metrics_single_source.py`).
  Distance from peak comes from `overview` (the breaker's stored-peak figure).
  The front end formats and lays out; it never totals or divides money.
- **Equity and FX books stay at parity in money terms** (net P&L realised/open,
  costs, drawdown, open book at cost basis, fill blotter, FIFO closed trades).
- **The readers fix their state folder when first imported**
  (`paper_trade.STATE_DIR`, `fx_book.STATE_DIR`), and this package's
  `__init__.py` imports them. Anything that must choose the folder first lives
  outside this package (see `trading_algo/home/`).
- **Every screen must also work from exported files**, because the same screens
  are published to GitHub Pages for phone use.
- Status is never colour alone: shape + word (green and red differ only 1.4:1 in
  brightness in the Midnight palette).
