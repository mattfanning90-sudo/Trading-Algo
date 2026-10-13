---
title: Modern book screens, phase 1 (Overview + Positions)
slug: book-screens
status: draft            # draft → agreed → in-progress → done (or dropped)
created: 2026-10-13
last-updated: 2026-10-13
owner: Matt Fanning
---

# Modern book screens, phase 1 (Overview + Positions)

Phase 1 of §11 of `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`
(parts 1–3 approved by Matt on 2026-10-13). The approved look is the v2 mockup
(`.superpowers/brainstorm/*/content/book-overview-v2.html`, throwaway).

## Context
The home screen (B1, `docs/specs/home-screen.md`) answers "am I up, and is anything
wrong?" for all books at once. Going deeper into one book still means the old
terminal (`static/app.js`, 3,823 lines), which shows numbers without saying what
they mean. Matt asked for modern versions of every screen, most-used first. He
chose direction B for a book's Overview: numbers first, with **coherent text** (a
short plain-English summary, a caption under every number) and an **"Is it
working?"** panel (what's going right, what's going wrong, is the model any good).

Grounding on 2026-10-13 found that the summary and the panel need inputs no screen
serves today:
- the FX permutation result (`state/permtest_matt.json`, read only by
  `forex/champions.py`);
- average win and loss;
- hold length against the signal horizon (`verify.check_horizon`);
- each backtest's age;
- the reason a sleeve is in cash (`last_flat_reason` and `last_status` are in the
  saved books, but not in `/api/account`).

The page must not work these out itself, so there is one new server builder.

What the panel says "going wrong" is now also logged as fixes in the product
backlog, items F20–F26 (branch `docs/fix-backlog`, commit `6796786`). The panel
names the item that tracks each problem, so the screen and the backlog stay
connected.

**Approach (draft — confirm):**
- **`dashboard/book_summary.py`**, served at **`/api/summary/<KEY>`**. This renames
  the design's `book_verdict`, because it also builds the tiles and the "why" lines.
  For one book it returns:
  - the six KPI tiles (label, value, one-line caption);
  - the summary paragraph;
  - the "Is it working?" items (column, text, source, date, caveat, backlog id);
  - per-region (equity) or per-asset-group (FX) "why" lines for Positions;
  - month-by-month returns.

  It reads the existing payloads (`api.build_snapshot`, `fx_api.build_fx_snapshot`),
  `verify`, `backtest_store`, `state/permtest_<account>.json` and `metrics`. It
  calls no Claude and no network beyond what those already do.
- **`metrics.monthly_returns`**: one new function, so the month grid comes from the
  measurement single source. "Below its best" uses the existing `metrics.drawdown`.
- **Screen files**:
  - `static/blocks.js`: shared building blocks, extracted from `home.js` (escaping,
    money and percent formatting, period chips, line chart, tiles, tables, status
    pill).
  - `static/screens/book_overview.js` and `static/screens/book_positions.js`.

  They are plain scripts on one shared namespace, not ES modules, so the Pages
  export can inline them into one file. No framework.
- **Navigation:** picking a book in the home sidebar opens its tabs in place:
  - **Overview** and **Positions** are new.
  - **Backtest**, **Method** and, on FX books, **Swarm** open the old terminal on
    that book until phases 2–3 replace them.
  - Every book screen has a "Classic view" link.
  - The new Overview replaces B1's single-book view. It keeps that view's
    freshness line ("last update …") and its period chips (1D/1W/1M/ALL), which
    set the chart's window.
- **Phone:** `dashboard/export.py` gains an `--app` mode that writes one
  self-contained `public/app.html`. It holds the home screen plus both book screens,
  with every payload baked in behind the existing `fetch()` stand-in.
  `scripts/build_site.sh` publishes it, and the current front page gets a link to it.

## Goals
- **G-1**: On any book, within five seconds, Matt can say whether it is up or down
  since it started, how that compares, and the main thing going wrong.
- **G-2**: Every sentence and number on these screens is backed by a named source
  and date. Anything that cannot be measured yet says "not measured yet". Nothing is
  guessed.
- **G-3**: On Positions, for each region (equity) or asset group (FX), Matt can see
  whether it is investing or waiting, and why. He can also see what each holding
  cost, what it's worth in A$, and its gain or loss.
- **G-4**: The same screens work on the Mac and on the phone (Pages), from the same
  answers.
- **G-5**: Nothing old breaks. The classic terminal stays one click away, and no
  trading code or stored data changes.

## Non-goals
- **NG-1**: The Backtest, Method, Swarm and All-accounts screens (phases 2–3), and
  retiring the old terminal.
- **NG-2**: The "By region / By pair" chart view shown in the mockup's chips (phase
  2). Phase 1 has **Value** and **Below its best**.
- **NG-3**: Claude-written explanations or news (project C). The summary is built
  from fixed rules.
- **NG-4**: Measuring whether the equity ranking picks winners (needs D0a) or which
  FX agents are right (needs the agent scorecard, project D). The panel says these
  are not measured yet.
- **NG-5**: Fixing anything the panel finds. That is backlog items F20–F26, each
  with its own decision.
- **NG-6**: Making the new screens the Pages front page. They sit alongside it until
  Matt has used them for a week and decides.
- **NG-7**: Any change to trading code, `verify.py` behaviour or stored data.
  `metrics.py` gains one read-only function.

## The "Is it working?" rules (thresholds agreed 2026-10-13, Q-2)
The panel and the summary are built only from these rules. A rule shows a line only
when its condition holds. Each line carries its source and date, and its caveat
where the source has one. The **summary paragraph is 2–4 sentences drawn only from
lines that are on the panel**, so the summary never claims something the panel does
not back.

| Rule | Books | Column | Shows when | Says (shape) | Caveat carried | Backlog |
|---|---|---|---|---|---|---|
| winners-bigger | equity, FX | right | ≥ 5 closed trades and average win ≥ 1.5 × average loss | "Only W of N closed trades made money, but the average win (+A$a) is about k× the average loss (−A$b)" | "small sample" when N < 30 | — |
| filter-working | equity | right | a sleeve is flat with `last_flat_reason` = `regime-off` | "Australia has stayed in cash since 24 Jul while its market is below its long-term trend: the safety filter doing its job" | — | — |
| inside-breaker | equity, FX | right | the book has a breaker and its fall from peak is under half of it | "x% below its best, well inside the −y% breaker" | — | — |
| open-ahead | FX | right | open positions are in profit | "Open positions are A$x ahead" | — | — |
| ahead-of-market | equity | right | return since start is above the benchmark's over the same dates | "n points ahead of the market since …" | "the benchmark is price-only; dividends not counted" | — |
| costs-vs-result | equity, FX | wrong | costs > net gain, or the book is down and costs > 0 | "Costs (A$x) are bigger than the gain (A$y)" / "Costs (A$x) are about k% of the loss" | — | — |
| uk-costs | equity | wrong | FTSE cost per value traded ≥ 2 × the US figure | "UK trading has cost x bps of the amount traded (0.5% stamp duty on buys) vs y bps in the US" | — | F25 |
| behind-market | equity | wrong | return since start is more than 1 point below the benchmark's | "About n points behind the market since …" | price-only benchmark, as above | — |
| mostly-idle | equity | wrong | cash ≥ 50% of the book | "x% of the book is in cash, so even a right call moves the total little" | — | F23 |
| holds-too-short | FX | wrong | `verify` raises `hold-shorter-than-signal` | "Sells early: median hold n days vs an m-day signal; c of r positions closed before their signal could resolve" | "holding longer has not been shown to earn more (F20)" | F20 |
| frozen-prices | FX | wrong | `verify` raises `dead-price-turnover` | "n position changes since start at a frozen price, paying spread for nothing" | — | F21 |
| loses-more-often | FX | wrong | win rate < 50% and realised result < 0 | "x% of closed trades made money; realised trades are −A$y in total" | "small sample" when N < 30 | — |
| backtest | equity, FX | model | a saved backtest exists | "Backtest (start–end): x% a year, Sharpe s" (equity: vs the benchmark's) | the saved date always. Over 14 days old: "may not reflect recent changes". Equity: "survivorship-biased, so an upper bound; no sleeve passes a standalone Deflated-Sharpe test". Equity book with its own profile: "this is the standard strategy, not this book's settings". FX: the review notice's artefact caveat (`fx_config.REVIEW_NOTICE`) | — |
| luck-test | FX | model | `state/permtest_<account>.json` exists | "Permutation test (date, n shuffles): p = x, so a strategy with no edge would score this well about k in 10 times" | "no evidence of skill yet" when p ≥ 0.05 | F22 |
| not-measured | equity, FX | model | always | equity: "Does the ranking pick winners? Not measured yet: needs the decision record (D0a)". FX: "Which agents are right? Not measured yet: needs the agent scorecard (project D)" | — | — |

**Counting words (decided in drafting — confirm):** `fx_api` counts *closed trades*
(FIFO lots: 197 on matt today). `verify` counts *positions opened and closed* (77).
These are different things, so the screen never calls both "round trips". Win rate
uses closed trades. The hold-length line uses "positions". Hold length is said in
days only for daily-bar books, otherwise in bars.

**Tiles:**
- **Equity:** Worth · Since start · vs the market · Below its best · Costs so far ·
  In cash.
- **FX:** Worth · Since start · Below its best · Win rate · Costs so far · Turnover.

Each tile has a one-line caption (for example "of A$100,000 put in, at today's
prices"; "safety breaker trips at −25%"; "ASX all cash; US 72%, UK 40%").

**Positions "why" lines:**
- **Equity, per region,** from the saved sleeve and the engine's own fields:
  - waiting because the market is below trend (`regime-off`, with the date it began);
  - waiting until next month's rebalance (holds nothing and already rebalanced this
    month);
  - investing part of its money because positions are sized to a volatility target;
  - paused by the safety breaker (with the days left);
  - in cash because its price data failed checks (`data-quality`).
- **FX, per asset group** (`pairs.Pair.asset_class`: currencies, crypto, shares,
  bonds): a line when that market is shut right now ("frozen until it reopens", from
  `forex/sessions.py`).

## Acceptance criteria
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | `/api/summary/<KEY>` returns tiles, summary, panel items, why-lines and month returns for every discovered book; an unknown key gives 404; a builder error gives a generic 500 that leaks no file path. | `tests/test_book_summary_server.py` | ☐ |
| AC-2  | Every money figure in the summary equals the existing payload's figure for the same snapshot, to the cent (worth, since start, costs, closed-trade net, open P&L). | `tests/test_book_summary.py` on copies of real state files | ☐ |
| AC-3  | Each rule in the table shows its line when its condition holds and not otherwise. There is one test per rule, with a fixture on each side of its threshold. | `tests/test_book_summary_rules.py` | ☐ |
| AC-4  | Every panel item carries a source and a date; a rule whose input is missing yields "not measured yet" (or no line) and never an invented number. | `tests/test_book_summary_rules.py` | ☐ |
| AC-5  | Caveats travel: "small sample" under 30 closed trades; every backtest line shows its saved date and, past 14 days, "may not reflect recent changes"; profiled equity books say the backtest is the standard strategy; FX backtest lines carry the review notice's artefact caveat; behind/ahead-of-market lines say the benchmark is price-only. | `tests/test_book_summary_rules.py` | ☐ |
| AC-6  | Every sentence in the summary paragraph comes from a line shown on the panel (test: each summary clause maps to a panel item id). | `tests/test_book_summary_rules.py` | ☐ |
| AC-7  | Lines with a backlog id name it, and every id the builder can emit exists in `product/backlog/backlog.json`. | `tests/test_book_summary_rules.py` | ☐ |
| AC-8  | Win rate is labelled "closed trades" and the hold line "positions"; hold length is in days only for daily-bar books. | `tests/test_book_summary_rules.py` | ☐ |
| AC-9  | Month-by-month returns and "below its best" come from `metrics` (`monthly_returns`, `drawdown`); the single-source guard stays green. | `tests/test_metrics.py`, `tests/test_metrics_single_source.py` | ☐ |
| AC-10 | Positions why-lines: each equity reason in the list above and the FX "market shut" line render from fixtures, and a sleeve with no recorded reason says what it holds without inventing a reason. | `tests/test_book_summary_rules.py` | ☐ |
| AC-11 | Every new screen file and `blocks.js` parses, and none of them totals or divides money: the existing guard test is widened from `home.js` to all of them. | `tests/test_home_ui.py` | ☐ |
| AC-12 | In a real headless browser against fixture data, the Overview and Positions of FULL and FX·Matt load with no script errors, show their key text (the summary, the six tiles, the three panel columns, each region's why-line), and do not scroll sideways at 1280 px or 390 px. Runs where Playwright is installed; skipped otherwise, like the `node` check. | `tests/test_book_screens_browser.py` | ☐ |
| AC-13 | `export --app` writes one `app.html` that, opened from disk in the headless browser, shows the same screens and makes **no network request**. | `tests/test_export_app.py` (+ browser check) | ☐ |
| AC-14 | The home sidebar opens a book's Overview and Positions in place. Backtest, Method and Swarm open the old terminal on that book. Every book screen has a working "Classic view" link (Mac: the terminal on the same book; Pages: that book's existing terminal page). | `tests/test_book_screens_browser.py` + manual | ☐ |
| AC-15 | The existing terminal, export and home tests all stay green, and `export --site` output is unchanged apart from the added link to `app.html`. | `pytest` | ☐ |
| AC-16 | Keyboard: the tab bar follows the standard tab pattern (←/→ move between tabs when it has focus), B1's shortcuts still work (⌘0–9, ⌘R, Esc, ↑/↓ in the sidebar), visible focus ring, status shown as shape + word. | Manual checklist | ☐ |
| AC-17 | 5-second test: on FULL and FX·Matt, Matt says "up or down since start", "against what", and "the main thing going wrong", correctly, within 5 seconds each. | Manual (Matt) | ☐ Matt |
| AC-18 | Phone check: after the branch reaches `main`, `app.html` on the Pages site reads correctly on Matt's phone. | Manual (Matt) | ☐ Matt |
| AC-19 | Full suite green. | `pytest` | ☐ |

## Constraints & invariants
- **#1–#4** (no lookahead, costs always on, one weight function, whole shares):
  not touched. Phase 1 only reads saved state.
- **#5 Synthetic is test-only:** fixtures are copies of real state files. A synthetic
  book keeps its SYNTHETIC banner, and no synthetic number appears as performance.
- **#6 Local currency:** every money figure is labelled. Book totals are A$ as the
  existing payloads report them. Positions show local price and A$ value side by
  side.
- **Measurement single source:** no drawdown, Sharpe, annualisation or monthly
  return outside `metrics.py`; the guard test stays green.
- **No money maths in the page:** the page formats and slices; the server computes.
- **Carry the source's caveat with its number** (CLAUDE.md "Checking your own
  work" #4) is a tested rule here (AC-5), not a style hope.
- **Dependencies:** none new to run the app or CI. Playwright is used only by the
  local browser tests and is skipped where absent.
- **Public data:** Pages already publishes every book's figures; `app.html` adds
  the panel's sentences and backlog ids, which are already public in the repo.
  Nothing private is added.

## Migration & rollback
- Nothing stored changes: there are no new state files, no format changes and no
  App Support files beyond B1's. So no migration is needed.
- Rollback: revert the commits, or use "Classic view". On Pages, stop publishing
  `app.html` and remove the link (one line each in `build_site.sh` and `export.py`).
- The migration-and-backup rule applies again the moment a later phase stores
  anything (for example, if the phone later gets a stored first-seen record — Q-1 option (a)).

## Verification plan
```bash
pytest tests/test_book_summary.py tests/test_book_summary_rules.py \
       tests/test_book_summary_server.py tests/test_metrics.py \
       tests/test_metrics_single_source.py tests/test_home_ui.py \
       tests/test_export_app.py tests/test_book_screens_browser.py
pytest                                              # full suite
python -m trading_algo.home                         # Mac: sync, open home → a book
python -m trading_algo.dashboard.export --app -o /tmp/app.html   # phone build, open from disk
```

## Open questions
None open. Q-1–Q-3 were settled on 2026-10-13 (see the Decision log).

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-11 | All screens, in phases, most-used first; Mac and Pages; one small file per screen | Matt |
| 2026-10-13 | Overview = direction B + coherent text + "Is it working?" panel; v2 mockup approved ("i like it") | Matt |
| 2026-10-13 | §11 parts 1 and 3 approved, including the new server builder | Matt |
| 2026-10-13 | Findings go into a fix backlog (F20–F26); the panel names the item that tracks each | Matt |
| 2026-10-13 | Q-1: on the phone, problems show red when serious and are listed as known otherwise; no amber "new" (no stored first-seen record in phase 1) | Matt (recommended) |
| 2026-10-13 | Q-2: the draft thresholds stand — win ≥ 1.5× loss, behind = > 1 point, idle = ≥ 50% cash, inside breaker = < half used, UK costs ≥ 2× US | Matt (recommended) |
| 2026-10-13 | Q-3: Classic view opens the old terminal on the same book, via a small change to `static/app.js` | Matt (recommended) |
| 2026-10-13 | Spec to be reviewed adversarially before the plan | Matt |
