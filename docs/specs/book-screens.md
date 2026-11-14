---
title: Modern book screens, phase 1 (Overview + Positions)
slug: book-screens
status: draft            # draft → agreed → in-progress → done (or dropped)
created: 2026-10-13
last-updated: 2026-10-14
owner: Matt Fanning
---

# Modern book screens, phase 1 (Overview + Positions)

Phase 1 of §11 of `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`
(parts 1–3 approved by Matt on 2026-10-13). The approved look is the v2 mockup
(`.superpowers/brainstorm/*/content/book-overview-v2.html`, throwaway), as amended
by the adversarial review recorded at the end of this spec (2026-10-14).

**Phase 1 ships in three steps:**
- **1a**: the server answers and their rules. Pure code, all tested in CI.
- **1b**: the Mac screens.
- **1c**: the phone (Pages).

The first implementation plan covers 1a and 1b. 1c gets its own plan after 1b,
because:
- it changes the robots' publishing step, which needs its own care;
- the phone cannot get anything until this branch reaches `main` anyway.

## Context
The home screen (B1, `docs/specs/home-screen.md`) answers "am I up, and is anything
wrong?" for all books at once. Going deeper into one book still means the old
terminal (`static/app.js`), which shows numbers without saying what they mean. Matt
asked for modern versions of every screen, most-used first. He chose direction B for
a book's Overview: numbers first, with **coherent text** and an **"Is it working?"**
panel. He also asked that what the panel finds be logged as fixes. They are backlog
items F20–F26 (PR #102), and the panel names the item that tracks each problem.

**Measured 2026-10-14, which shapes everything below:**
- Building FULL's existing screen answer (`api.build_snapshot`) takes **about 50
  seconds** every time, because it downloads prices back to 2012 with no cache
  (`paper_trade.py:169,193`, `use_cache=False`).
- Building from the books' saved values (`overview.build_overview`) takes **under
  0.1 s for all eight books**.
- Live and saved values disagree on screen. FULL is +0.19% on live prices and −0.05%
  on its last saved value. So **the Overview uses saved values only**, the same ones
  the home screen uses, and says which date they are from.

**Approach:**
- **`dashboard/book_summary.py`**: a pure function (no file, network or clock
  reads) that turns one book's saved state, overview card, audit findings, saved
  backtest and permutation report into the Overview's answer. A thin loader beside
  it gathers those inputs. Served at **`/api/summary/<KEY>`**; it needs no network.
- **Positions** comes from **`/api/positions/<KEY>`**, in two parts:
  - holdings, cost and the "why" lines come from saved state at once;
  - today's prices are fetched separately, only for the names held, and their time
    is shown.

  Offline, Positions says the holdings are valued at cost. It never waits on the
  network before showing anything.
- **`metrics`** gains `monthly_returns`. The chart's "below its best" uses
  `metrics.drawdown`. The **tile** uses the safety breaker's own figure from the
  overview card, the same figure the home screen shows.
- **Screen files**:
  - `static/blocks.js`: shared building blocks and the formatting rules, on one
    namespace object, moved out of `home.js`;
  - `static/screens/book_overview.js` and `static/screens/book_positions.js`.

  They are plain scripts with no top-level names that could collide. No framework.
- **Navigation:** picking a book in the home sidebar opens its view in place.
  - **Overview** and **Positions** are tabs.
  - **Backtest**, **Method** and **Swarm** (FX books only) are links marked ↗ that
    open the old terminal on that book and that tab, until phases 2–3 replace them.
  - A "Classic view ↗" link opens the old terminal on the same book. This needs a
    small change to the old terminal's `boot()`: `?account=` and `?tab=`.

## Goals
- **G-1**: On any book, within five seconds, Matt can say:
  - whether it is up or down since it started, and as of when;
  - whether that is out of the ordinary against its market;
  - the **main problem**, which is a single defined item on screen.
- **G-2**: Every sentence and number is backed by a named source and date, with its
  caveat, everywhere it appears: summary, tiles and panel. Anything that cannot be
  measured says "not measured yet" or "couldn't measure". Nothing is guessed, and
  every term is explained in plain words.
- **G-3**: Nothing that needs Matt is hidden. A halted book, a book near its
  breaker, stale data or an audit problem is at the top, before anything else.
- **G-4**: On Positions, for each region (equity) or asset group (FX), Matt can see:
  - whether it is investing or waiting, and why;
  - what each holding cost, what it's worth and its gain or loss;
  - how old the prices are.
- **G-5**: Nothing old breaks, and the new screens are fast and work offline.
  - The classic terminal stays one click away.
  - No trading code or stored data changes.
  - Every Mac screen shows within 2 seconds from saved values.

## Non-goals
- **NG-1**: Backtest, Method, Swarm and All-accounts screens (phases 2–3);
  retiring the old terminal.
- **NG-2**: The "By region / By pair" chart view (phase 2). Phase 1 has **Value**
  and **Below its best**.
- **NG-3**: Claude-written explanations or news (project C). Every sentence comes
  from a fixed rule.
- **NG-4**: Measuring whether the equity ranking picks winners (needs D0a) or which
  FX agents are right (needs the agent scorecard, project D). The panel says these
  are not measured yet.
- **NG-5**: Fixing what the panel finds. That is backlog items F20–F26.
- **NG-6**: Splitting each holding's gain into the price move and the currency
  move (phase 2). Phase 1 labels the figure honestly instead.
- **NG-7**: Any change to trading code, `verify.py` behaviour or stored data.
  `metrics.py` gains one read-only function, and `app.js` gains the two link
  parameters.

## How a book's Overview reads, top to bottom
1. **Status pill** (shape + word, kept from B1): ■ Halted · ▲ Near its safety
   breaker · ▲ Data stale · ● Running normally. Then the book's urgent and warning
   audit items, if any (B1's "Problems in this book").
2. **Result line**, always present. It is built from the last saved value and gives
   that value's date. Example: "▲ Up A$X since 11 Jun 2026 · as of 13 Oct · about
   n points behind the market (within its normal ups and downs)". Long/short books
   are not compared with a market; they say "aims to make money whichever way the
   market moves".
3. **Main problem**, one line: the top-ranked item from "Holding it back" (ranking
   below), or "No standout problem".
4. **Summary**, 2–4 sentences, built in a fixed order:
   1. the result line in words;
   2. the main problem;
   3. the strongest "going right" line, if any;
   4. the model line.

   Each sentence carries its caveat in short form: the first sentence of the
   item's caveat (the full caveat is on the panel). **The summary never has more
   "going right" sentences than problems.**
5. **Six tiles** with captions.
6. **The "Is it working?" panel**: Going right · Holding it back · Is the model any
   good?
7. The **chart** (Value / Below its best, period chips 1D–ALL from B1), the
   **month-by-month grid**, and the **last closed trades**.

The panel heading is **"Holding it back"**, not "Going wrong", and it is not red. As
on the home screen, red is kept for things that need action: halted, audit errors,
failed jobs.

**Ranking of the main problem** (first match wins):
1. halted;
2. an audit ERROR for this book;
3. near the breaker (the home screen's `NEAR_BREAKER`, one constant for both);
4. stale data;
5. costs flipped the result, took more than half of a gain, or are more than half of a loss (costs-flip);
6. no safety breaker on a leveraged book;
7. sells long before its signal resolves;
8. loses more often than it wins;
9. behind the market by more than its normal ups and downs;
10. mostly idle;
11. any other audit WARN;
12. trades at frozen prices.

## The rules (agreed thresholds where measured; reviewed 2026-10-14)
A rule shows a line only when its condition holds. Every line carries its **source,
date and caveat**, and a backlog id where one tracks it ("tracked as fix F20"). A
rule whose input is missing gives "not measured yet". A rule whose input fails to
load gives "couldn't measure <thing>", and the rest of the screen still shows.

**Always on**
| Rule | Says | Notes |
|---|---|---|
| result | up/down A$ and % since start, as of the last saved date; vs the market | Book and benchmark are both measured to the same last saved date. "Ahead" or "behind" only when the gap is more than 2 × the book's normal spread (tracking error from `metrics.benchmark_stats` × √years); otherwise "within its normal ups and downs". The benchmark is price-only, so the line says "the market figure leaves out dividends, which flatters this book". Long/short books: no market comparison. Needs at least 20 aligned trading days (about a month); before that the line says it's too early to compare with the market. |
| not-measured | equity: "Does the ranking pick winners? Not measured yet (needs the decision record)"; FX: "Which agents are right? Not measured yet (needs the agent scorecard)" | — |

**Needs attention** (top of screen, and first in "Holding it back")
| Rule | Shows when | Backlog |
|---|---|---|
| halted | the book's breaker has paused it (days left shown) | — |
| audit-error | any audit ERROR for this book (its own text, plain-worded) | — |
| near-breaker | fall from peak ≥ `NEAR_BREAKER` × breaker (same constant as the home screen) | — |
| stale | the home screen's freshness rule says late or stale | — |
| no-breaker | the book has no breaker and is leveraged (ULTRA) | — |
| audit-warn | **every** audit WARN for this book not already covered by a rule below, shown in plain words (nothing hidden) | mapped where an item exists |

**Going right** (results only; how the machinery works goes on Positions, not here)
| Rule | Shows when | Caveat |
|---|---|---|
| winners-pay | profit factor (total won ÷ total lost on closed positions, after costs) > 1 **and** closed result > 0, **excluding closes forced by a recorded bug** (the state's `corrections` block, e.g. FULL's 27 Aug liquidation, which says how many were left out) | sample size counted in positions and decision dates; "small sample" under 30 positions or under 6 decision dates |
| ahead-of-market | the result rule finds the book ahead by more than its normal spread | price-only benchmark |

If nothing qualifies: "Nothing clearly going right yet."

**Holding it back**
| Rule | Shows when | Caveat | Backlog |
|---|---|---|---|
| costs-flip | costs turned a gain into a loss, took more than half of a gain (or all of it), or are more than half of a loss; the line states the share | Names what "costs" includes: equity = commission + stamp duty, with slippage inside the fill prices; FX = the spread and financing `fx_pnl` charges. Names the biggest contributor, e.g. "mostly UK stamp duty: A$292.51 of A$449.17". FX adds a note when `fx_pnl`'s unexplained residual is over 10% of the result. | F25 when the FTSE share of costs > 50% |
| holds-too-short | the audit raises `hold-shorter-than-signal` | "Holding longer has not been shown to earn more"; units stated exactly ("5 days against a signal of 57 trading days, about 11 weeks") | F20 |
| loses-more-often | the 95% interval for the share of winning positions (after costs) is entirely below 50% | the interval is shown | — |
| behind-market | the result rule finds the book behind by more than its normal spread | price-only benchmark, as above | — |
| mostly-idle | long-only book with gross exposure < 50%, not counting cash a staged buy is about to invest at the next close | Split by cause: waiting below trend (by design), opening positions at the next close, pinned until next month, micro mode, or sized down (volatility target / whole-share rounding) | F23 / F24 for the sized-down part only |
| frozen-prices | ≥ 10 position changes at a frozen price in the last 30 days | — | F21 |

**Is the model any good?**
| Rule | Says | Caveats |
|---|---|---|
| backtest | "Backtest (start–end): x% a year, Sharpe s" (equity: vs the benchmark's). The saved backtests don't record which cash rate their Sharpe assumes, so the line doesn't state one rather than guess. | Always shows the saved date; over 14 days old, "may not reflect changes since". Equity: "survivorship-biased, so an upper bound; no sleeve passes a standalone Deflated-Sharpe test"; "the benchmark here leaves out dividends, which flatters the strategy". If the book's allocations, profile settings or micro mode differ from the backtest's: "this is the standard strategy, not this book's settings". FX: the review notice's artefact sentence only (not its hard-coded Sharpes). A synthetic backtest is never shown as performance. |
| luck-test | FX books with a permutation report (read through `permtest.load_report`): "We tested whether the search for better agent settings finds anything real. On shuffled, patternless data it finds results this good about 1 in 2 times (p = 0.47), so its best finds look like luck." | "This tests the settings search, not this book's trades: the book currently uses none of the search's finds." States the null (bar shuffles, n, data window) and the date; over 30 days old, says so. p under 0.1 reads "rarely (p = x)", never "0 in 10". Tracked as fix F22. |

**Counting words:** "closed trades" are the parts of positions that the ledger
closes (FIFO lots). "Positions" are opened and then fully closed. Win rates and
sample sizes use **positions, after costs**. If a figure has to use closed trades,
it says "closed parts of positions". FX averages use the full trade history, not
the payload's last 400 rows.

**Plain words:** a server-side glossary gives each term a plain gloss on first use
on a screen. The terms are: Sharpe, drawdown/below its best, breaker, volatility
target, regime/below trend, stamp duty, spread, swap/financing, signal horizon,
backtest, survivorship bias, Deflated Sharpe, permutation test, p-value,
benchmark, tracking error, profit factor and bps. Plain words come first and the
term second, e.g. "UK trades cost 0.46% of the money traded (46 basis points)". The status pill is a
label, not a sentence, so it isn't glossed; the first sentence that uses a term is.

**Tiles:**
- **Equity:** Worth · Since start · vs the market · Below its best · Costs so far
  · Invested.
- **FX:** Worth · Since start · Below its best · Win rate · Costs so far · Turnover.

All tiles use the same saved value. Every caption is server-written; captions
include "as of <date>" where it matters, the breaker level, and what "costs"
includes. A book with no closed positions shows "No closed positions yet" instead
of a win rate.

**Positions "why" lines**, one per region, chosen in this order (specific reasons
before the generic "waiting until next month", or they would never show):
1. paused by the breaker (days left);
2. price data failed checks (`data-quality` / `cash:stale-data`);
3. exchange rate unavailable (`cash:fx-unavailable`);
4. too small to trade (`cash:below-min`, SMALL's gate);
5. waiting below trend (`regime-off`): "in cash since the book began" (or since
   its last sale, read from the trade record), "at the last decision (6 Oct) the
   index was below its long-term trend", with the review's caveat that this costs
   some growth but limits the worst falls;
6. nothing passed the momentum and trend tests (`no-eligible-names`);
7. a long/short book couldn't form both sides (`insufficient-names`);
8. opening positions at the next close (flat, with a staged target that holds
   something: the book decides at one close and fills at the next, which the
   audit reads as `awaiting-fill`), with the decision date;
9. holding nothing until next month's rebalance (flat, already rebalanced this
   month, no recorded reason);
10. invested, with the share held in cash and why ("sized to a volatility target;
    whole shares round down").

A flat region with a reason also says "next decision: the start of next month"
when it has already been decided this month.

Regions needing attention come first. Shorts read "sold at → now" and their gain
has the right sign. Cash above 100% on a long/short book is explained ("cash
includes the proceeds of short sales"). A holding with no price today reads "no
price today, held at cost". Each holding's A$ gain is labelled "price move, at
today's exchange rate". FX positions are grouped by asset class, and a group whose
market is shut says "frozen until it reopens". The header counts holdings, not
shares.

## Acceptance criteria
**1a: server answers (all in CI)**
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | `/api/summary/<KEY>` and `/api/positions/<KEY>` (holdings part) answer for every discovered book in < 2 s with the network stubbed off; unknown key → 404; builder error → generic 500 with no file path, logged. | `tests/test_book_screens_server.py` | ✅ pass — `tests/test_book_screens_server.py` (every book < 2 s offline, 404, generic 500, routes off without `books=`) |
| AC-2  | `book_summary.summarise(...)` is pure: same inputs → same output, and it does no file, network or clock reads (enforced by test). | `tests/test_book_summary.py` | ✅ pass — `tests/test_book_summary.py::test_summarise_is_pure` |
| AC-3  | One valuation point: the result line, tiles, ALL chip, summary and market gap all use the book's last saved value and the same date; worth and since-start equal the overview card to the cent. | `tests/test_book_summary.py` (copies of the current synced snapshot) | ✅ pass — `tests/test_book_summary.py` (`test_one_valuation_point_across_result_tiles_and_chart`; `test_all_chip_and_since_start_tile_equal_the_overview_card_on_the_real_book` on the real FULL book; `test_market_gap_is_measured_to_the_results_as_of_date`) |
| AC-4  | Each rule fires when its condition holds and not otherwise: one fixture each side of every threshold, including the two-sided market rule, the profit-factor rule with and without a corrections block, the 95% interval rule, and mostly-idle on a long/short book (must not fire). | `tests/test_book_rules_attention.py`, `tests/test_book_rules_back.py`, `tests/test_book_rules_model.py` | ⚠️ partial — `tests/test_book_rules_attention.py`, `tests/test_book_rules_back.py`, `tests/test_book_rules_model.py` cover each rule on both sides (two-sided market, mostly-idle on long/short, small-costs and zero-result edges); not audited threshold by threshold for every rule |
| AC-5  | Caveats travel: every line, tile caption and summary sentence that uses a number carries that number's caveat (price-only benchmark, sample size, backtest date and staleness, settings mismatch, forced closes excluded, luck-test scope and null, costs definition, F20's counter-evidence). | `tests/test_book_rules_attention.py`, `tests/test_book_rules_back.py`, `tests/test_book_rules_model.py`, `tests/test_book_summary.py` | ⚠️ partial — caveats asserted in `tests/test_book_rules_attention.py`, `tests/test_book_rules_back.py`, `tests/test_book_rules_model.py` and `tests/test_book_summary.py::test_every_summary_sentence_maps_to_a_shown_item_and_carries_its_caveat`; not every caveat on the spec's list has its own assertion |
| AC-6  | The summary is a list of `{text, item_id}`; every sentence maps to a shown item; the fixed order holds; never more "right" sentences than problems; and **the expected summaries for all eight books on the current snapshot are pinned** in a test. | `tests/test_book_summary.py`, `tests/test_book_summary_golden.py` | ✅ pass — `tests/test_book_summary.py` (order, item mapping, no extra good news) and `tests/test_book_summary_golden.py::test_pinned_summaries` (pinned for the fixture books) |
| AC-7  | Every audit WARN or ERROR for a book appears on its Overview, either through a rule or in plain words; none is dropped. | `tests/test_book_summary_golden.py` | ✅ pass — `tests/test_book_summary_golden.py::test_every_audit_warning_or_error_appears` |
| AC-8  | The main problem follows the ranking; a halted, ERROR, near-breaker or stale book always leads with it. | `tests/test_book_summary.py`, `tests/test_book_rules_attention.py` | ✅ pass — `tests/test_book_summary.py::test_halted_outranks_everything_and_leads_the_summary`; `tests/test_book_rules_attention.py` |
| AC-9  | Backlog ids named by the builder all exist in `product/backlog/backlog.json` and are not `done` or `rejected`. PR #102 is merged and in this branch's base, so the test asserts directly. | `tests/test_book_summary_golden.py` | ✅ pass — `tests/test_book_summary_golden.py::test_backlog_ids_exist_and_are_open` (scans `book_rules.py`, `book_summary.py` and `book_positions.py` for every fix id the builder can name, `backlog=` values and F-numbers in texts; each must exist and be open; a plain test, no xfail needed) |
| AC-10 | Every glossary term used in a screen's text carries its plain gloss on first use. | `tests/test_textfmt_glossary.py`, `tests/test_book_summary.py`, `tests/test_book_summary_golden.py` | ✅ pass — `tests/test_textfmt_glossary.py`, `tests/test_book_summary.py::test_glossary_explains_each_term_once_on_the_screen`, `tests/test_book_summary_golden.py::test_attention_is_glossed_first_because_it_renders_first` |
| AC-11 | Month returns come from `metrics.monthly_returns` and the chart's fall-from-best from `metrics.drawdown`, proven by a spy test; the tile's fall-from-best equals the overview card's; the single-source guard stays green. | `tests/test_metrics_screens.py`, `tests/test_book_summary.py`, `tests/test_metrics_single_source.py` | ✅ pass — `tests/test_metrics_screens.py`, `tests/test_book_summary.py::test_tile_below_best_is_the_breaker_figure_and_months_come_from_metrics`, `tests/test_metrics_single_source.py` |
| AC-12 | Positions why-lines: each reason in the list, the precedence order, shorts, cash > 100%, an unpriced holding, and the FX "market shut" line, each from a fixture. | `tests/test_book_positions.py` | ✅ pass — `tests/test_book_positions.py`, `tests/test_book_rules_back.py::test_sleeve_reason_precedence` and `::test_a_flat_sleeve_with_a_staged_buy_says_it_buys_at_the_next_close` (a staged buy reads as opening positions, never "until next month") |
| AC-13 | States: a brand-new book ("too new to judge"), no closed positions, one input that throws ("couldn't measure", rest of the screen intact), a synthetic backtest (never shown as performance; the saved books themselves carry no synthetic marker). | `tests/test_book_rules_attention.py`, `tests/test_book_summary.py`, `tests/test_book_rules_model.py` | ✅ pass — `tests/test_book_rules_attention.py::test_a_brand_new_book_is_too_new_to_judge`, `tests/test_book_summary.py` (throwing input, FX without closed positions), `tests/test_book_rules_model.py::test_missing_and_synthetic_backtests_are_never_performance` |

**1b: the Mac screens**
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-14 | Every screen file and `blocks.js` parses; no two scripts declare the same top-level name; the page receives money as server-formatted display text and computes nothing from money (guard widened to every screen file). | `tests/test_home_ui.py` | ✅ pass — `tests/test_home_ui.py` (parse, no duplicate names, no money maths, screen files use server text) |
| AC-15 | In a real headless browser, against fixtures: the Overview and Positions of FULL, FX·Matt, EXPERIMENTAL and SMALL load with no script errors. The status pill, result line, main problem, six tiles and three panel columns are present, and so is each region's why-line. Positions shows holdings with the network off. Runs in a CI `ui` job that installs Playwright and Chromium; locally, `REQUIRE_BROWSER=1` turns a skip into a failure. | `tests/test_book_screens_browser.py` + `.github/workflows/ci.yml` | ✅ pass — `tests/test_book_screens_browser.py` (FULL, MATT, EXPERIMENTAL, SMALL; Positions holdings offline) + `ui` job in `.github/workflows/ci.yml` |
| AC-16 | Navigation: the sidebar opens a book's Overview; Overview and Positions are tabs (←/→ when the tab bar has focus); Backtest, Method, Swarm and Classic view are ↗ links that open the old terminal on that book and tab (relative links, never `/`); B1's shortcuts still work (⌘0–9, ⌘R, Esc, ↑/↓). | `tests/test_book_screens_browser.py` + manual | ⚠️ partial — browser: ↗ links and tab arrow keys (`test_links_open_the_old_terminal_on_this_book_and_tab`, `test_tabs_follow_the_keyboard_pattern`, `test_classic_view_opens...`); B1 shortcuts (⌘0–9, ⌘R, Esc, ↑/↓) not exercised here, still manual |
| AC-17 | Accessibility and formats:<br>• ▲/▼ on every money figure, and one minus sign (−);<br>• one date format ("13 Oct 2026");<br>• text contrast ≥ 4.5:1 in the month grid and tiles (colour is never the only signal);<br>• the chart's benchmark line differs by pattern as well as colour, and has a text alternative;<br>• keyboard focus survives a refresh;<br>• only the status region is a live region. | Browser test (contrast, focus) + manual checklist | ⚠️ partial — browser (FULL book): contrast >= 4.5:1 on tiles and month grid, ▲/▼ on the Since start tile, each closed result and the result line, chart text alternative and dotted benchmark pattern, focus kept across refresh, only the pill is live, day-month-year dates (`test_chart_dates_read_day_month_year`), U+2212 minus (`tests/test_textfmt_glossary.py`). Other books and a visual pass are manual |
| AC-18 | The old terminal, export and home tests all stay green, and the B1 home screen tests still pass with the new book view. | `pytest` | ✅ pass — full suite green (`.superpowers/sdd/2026-10-14-book-screens-phase1/task-15-suite.txt`), including the B1 home tests |
| AC-19 | 5-second test, on fixtures Matt hasn't seen (a quiet day and a red day): he names "up or down, as of when", "against what" and the main problem, matching the screen's defined main problem, within 5 seconds each. | Manual (Matt) | ☐ Matt |
| AC-20 | Full suite green. | `pytest` | ✅ pass — 1755 passed, 1 xfailed (the existing strict xfail in `tests/test_live_path_equivalence.py`), 2026-11-14 |

**1c: the phone (its own plan, after 1b).** These requirements come from the
review, so they are fixed now:
- **The robots' runs must never break.** `export --app` runs as
  `… || { warning; write a "build failed at <time>" stub }`. The front page links
  to `app.html` only if it exists. All three Pages publishers are covered
  (`day-paper.yml`, `fx-paper.yml`, `fx-swarm.yml`). A CI test forces a failure and
  checks that the script exits 0 with every other page present. A CI test also runs
  `build_site.sh` with `SYNTH=--synthetic` on temporary copies of state.
- **Built like `--site`.** `app.html` is built by the same process and payload map
  as `--site`. Export walks every script tag in order (`blocks.js`, the screens,
  then `home.js`). A book that fails becomes a visible error stub plus a
  `::warning::`, never a silent drop. The page has a size budget, measured against
  today's 1.74 MB `index.html`.
- **The phone home answer is pure.** It is built with no seen-file and no App
  Support paths, so nothing is written but the output file (test with HOME and
  `MOMENTUM_HOME_DIR` pointed at a temp folder, and `state/` checksums unchanged).
  The snapshot comes from `GITHUB_SHA` only.
- **Honest about age.**
  - `published_at` is baked in, and the page works out its age when opened.
  - After 26 hours, any baked "fresh" or "All clear" becomes "may be out of date".
  - Time-sensitive lines say "at publish time".
  - Keyboard hints are hidden on touch screens.
- **Q-1 made honest.** While audit warnings exist, the phone shows "No action ·
  n warnings (this page can't tell new from old)", never "All clear". Near-breaker
  and stale stay amber.
- **Job health.** The publishing run is left out (`GITHUB_RUN_ID`), and each job is
  judged on its last completed run. The panel is labelled "as of publish". Calls use
  `GITHUB_TOKEN` with `actions: read`.
- **Kill switch.** A repo variable read by `build_site.sh`, plus a publish-only
  `workflow_dispatch`. With the switch off, `public/` matches the file list before
  phase 1c.
- **Public page hygiene.** No `/Users/` text, no root-relative links, no raw sync
  errors.
- **Phone layout.**
  - The sidebar becomes a book list with a "‹ All books" back bar.
  - Tabs scroll sideways.
  - Tiles go 2 across.
  - The panel stacks, with "Holding it back" first.
  - Positions rows become cards.
  - Text is at least 12 px.
  - The status, result line and main problem fit on the first screen at 390 px.
- **Done.** Matt checks it on his phone after the branch reaches `main`.

## Constraints & invariants
- **#1–#4:** not touched; phase 1 only reads saved state.
- **#5:** fixtures are copies of the current synced snapshot. A synthetic book or
  backtest keeps its banner and is never described as performance.
- **#6:** every money figure is labelled. Positions shows local price and A$ value
  side by side, and the A$ gain says it is at today's exchange rate.
- **Measurement single source:** no drawdown, Sharpe, annualisation, tracking
  error or monthly return outside `metrics.py`, proven by spy tests, not only the
  pattern guard.
- **One figure per idea across screens:** fall from best and near-breaker use the
  home screen's figures and its `NEAR_BREAKER` constant.
- **Carry the caveat with the number** (CLAUDE.md, "Checking your own work" #4):
  tested (AC-5), including in the summary and the tiles.
- **Dependencies:** none new to run the app. CI adds Playwright and Chromium in
  its own `ui` job.

## Migration & rollback
- 1a and 1b store nothing new: no state files, no format changes, no App Support
  files beyond B1's. No migration is needed. Rollback is to revert the commits, or
  use Classic view.
- 1c changes the robots' publishing step. Its rollback is the kill switch above,
  tested by the file-list check; "rollback complete" means `public/` matches the
  list from before phase 1c.
- If any later step stores something (e.g. a first-seen record for the phone), the
  migration-and-backup rule applies.

## Verification plan
```bash
pytest tests/test_book_summary.py tests/test_book_rules_attention.py tests/test_book_rules_back.py tests/test_book_rules_model.py tests/test_book_summary_golden.py \
       tests/test_book_screens_server.py tests/test_book_positions.py \
       tests/test_metrics.py tests/test_metrics_single_source.py tests/test_home_ui.py
REQUIRE_BROWSER=1 pytest tests/test_book_screens_browser.py
pytest                                              # full suite
python -m trading_algo.home                         # Mac: open home → a book → its tabs
```

## Open questions
None open. Q-1–Q-3 were settled on 2026-10-13, and the review's changes are below.

## Adversarial review (2026-10-14)
Four independent reviewers attacked the 2026-10-13 draft: quant validity on the
real books, engineering, product design, and operations. Claude checked each claim
in code or data before adopting it.

**Changed the spec:**
- The Overview was going to be built on a live price download of about 50 s with
  no cache, and broke offline. It now uses saved values only, with one valuation
  point per screen (FULL showed +0.19% live and −0.05% saved).
- The draft dropped B1's status pill and problem list. Halted, ERROR, near-breaker
  and stale books could have read as fine. Now: a "Needs attention" block, every
  audit WARN/ERROR shown, and a ranked **main problem**.
- G-1's "main thing going wrong" was never defined, and the summary could
  cherry-pick. Now: a fixed ranking, a fixed summary order, and pinned summaries for
  all eight books.
- **FULL's "winners are bigger than losers" rested on a bug.** 16 of its 37 closed
  trades, including the biggest win, were the 27 Aug forced liquidation. Without
  them, closed trades net +A$4.50, not +A$387.65. Forced closes are now left out
  and the screen says so.
- **The luck test was described wrongly.** It tests the swarm's search for
  settings, not matt's trades (matt uses none of its finds; `champions_matt.json`
  roster is empty). The wording is corrected, and backlog F22 was corrected in PR
  #102.
- **Thresholds that sat inside the noise.**
  - The ±1-point market rule is now two-sided and based on each book's normal
    spread.
  - "Small sample < 30" is replaced by an interval. matt's win rate is 44%
    [37%, 51%]; by position it is 38%.
  - "Mostly idle" now uses gross exposure, skips long/short books (EXPERIMENTAL
    read "110% in cash") and splits cash by cause.
  - Costs fire only when they matter, and say what they include. Daytrader's
    "costs" range from 63% to 99.5% of its loss depending on the source.
- The "safety filter doing its job" line had the wrong date. Its "since 24 Jul" is
  when that field was added; ASX has never traded. It is also a mechanism, not a
  result, so it moved to Positions with the review's caveat.
- Missing: ULTRA's lack of a breaker; the engine's other cash reasons; shorts;
  cash over 100%; unpriced holdings; the per-holding A$ gain ignoring currency
  moves (now labelled).
- Plain words: a glossary, plain words first, a neutral "Holding it back" heading,
  and red kept for action.
- Tests: CI now runs the browser tests, a skip can't hide locally, spy tests prove
  the single source, and the summary has a testable shape.
- Phone: split out as 1c, with the operations reviewer's requirements fixed above.
  An unguarded export failure would have skipped the robots' state commit.

**Checked and not adopted:**
- "FULL's backtest is a different portfolio": not so. The saved backtest's
  allocations are ASX/US/FTSE in thirds, the same as FULL. The general
  settings-mismatch caveat is kept.
- "Show a 'known restatement' figure on the backtest line": not adopted. The
  measurement-truth block regenerates the backtest. Until then the line shows its
  date and "may not reflect changes since", rather than a hand-entered figure that
  would itself go stale.

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-11 | All screens, in phases, most-used first; Mac and Pages; one small file per screen | Matt |
| 2026-10-13 | Overview = direction B + coherent text + "Is it working?" panel; v2 mockup approved ("i like it") | Matt |
| 2026-10-13 | §11 parts 1 and 3 approved, including the new server builder | Matt |
| 2026-10-13 | Findings go into a fix backlog (F20–F26, PR #102); the panel names the item that tracks each | Matt |
| 2026-10-13 | Q-1: on the phone, no amber "new" (no stored first-seen record in phase 1); made honest in 1c ("can't tell new from old", never "All clear" while warnings exist) | Matt (recommended); wording from review |
| 2026-10-13 | Q-2: draft thresholds accepted; the review then replaced the market, sample, idle and costs thresholds with measured or interval-based rules | Matt (recommended); review 2026-10-14 |
| 2026-10-13 | Q-3: Classic view opens the old terminal on the same book via `?account=`/`?tab=` in `app.js` `boot()` | Matt (recommended) |
| 2026-10-14 | Adversarial review adopted as above; phase 1 split into 1a/1b (first plan) and 1c (phone, own plan) | Claude, under Matt's "continue with recommended but adverse review" |
