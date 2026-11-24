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

## Book screens (Overview + Positions)
Shared with the planned Railway website, not Mac-only: the screens and their
server answers are the same ones the site will serve. The Mac launcher
(`python -m trading_algo.home`) still works unchanged.

| File | Job |
|---|---|
| `book_inputs.py` | Reads one book's SAVED files (state, backtest, audit, luck test) into one inputs record. All I/O lives here |
| `book_rules.py` | The "Is it working?" rules, each a pure function with a threshold and its caveat |
| `book_summary.py` | `summarise(inputs)`: pure; builds the result line, tiles, chart, panel and ranked main problem |
| `book_positions.py` | Holdings grouped by region / asset class, each with a why-line |
| `live_marks.py` | Background price fetch, cached; only runs when the last sync was online |
| `book_service.py` | `BookService`: answers the three routes. It rebuilds each summary and positions answer on every request (the saved state is read through `registry._peek`, cached by file time); the only thing BookService itself caches is live prices, through one `LiveMarks` (a finished fetch is reused for 15 minutes; a failed one reads as an error for 60 s before it can retry) |
| `glossary.py`, `textfmt.py` | Plain-English glosses (first use only) and money / date / minus formats |
| `static/blocks.js`, `static/screens/book_overview.js`, `static/screens/book_positions.js` | Screen blocks and the two tabs; the book shell is in `static/home.js` |

- **Routes** (only when `create_server(books=...)` is passed; else 404):
  `/api/summary/<KEY>`, `/api/positions/<KEY>`, `/api/marks/<KEY>`. An unknown key
  is 404; a builder error is a generic 500 with no file path (logged).
- **Saved values for the book itself.** The screens show the book's last saved
  value and date; the book's own worth, gains and value line never use live prices.
  Once the background fetch is ready, live data reaches two places: Positions
  values holdings at today's prices, and the fetch's market index gives the
  result line's market clause, the "vs the market" tile, the chart's market line
  and the ahead/behind-the-market rules, each measured only up to the book's
  last saved date.
  The page never does money maths; it shows server-formatted text.
- **Changing a rule:** change its unit test first, then run the golden test with
  `UPDATE_GOLDEN=1` and read the diff before committing it
  (`tests/test_book_summary_golden.py`).
- **Browser tests:** `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py`
  (needs Playwright 1.62.0 + Chromium). Without the variable they skip; CI's `ui`
  job sets it, so a skip can never hide a broken screen there.

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
