# Book Screens Phase 1 (1a + 1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every paper book a modern Overview and Positions screen inside the Mac home screen. Both are built only from saved values, with a ranked "main problem" and an "Is it working?" panel whose every sentence carries its source and caveat.

**Architecture:**
- A thin loader (`book_inputs.py`) gathers one book's saved data with no network.
- Pure functions (`book_rules.py`, `book_summary.py`, `book_positions.py`) turn it into display-ready JSON, served at `/api/summary/<KEY>` and `/api/positions/<KEY>`.
- A background fetch (`live_marks.py`, started by `/api/marks/<KEY>`) adds today's prices and the market comparison for equity books when online.
- The page (`blocks.js`, `screens/*.js`, `home.js`) only lays out text the server wrote.

**Tech Stack:** Python 3.11+ stdlib server, pandas, the repo's `metrics.py`, vanilla JS (classic scripts on one namespace), pytest, and Playwright (browser tests only).

**Spec:** `docs/specs/book-screens.md` (revised 2026-10-14 after the adversarial review). Umbrella design: `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md` §11. Phase **1c (the phone)** is out of scope here.

## Global Constraints

- Work in the worktree `~/Trading-Algo-mac-dashboard` on branch `feat/mac-dashboard`. Never write to any live `state/` directory.
- **Saved values only.** Nothing in `book_inputs`, `book_rules`, `book_summary` or `book_positions` may download prices. `api.build_snapshot` (about 50 s, live) is called only by `live_marks`, in a background thread.
- **One valuation point per screen:** the book's last saved value (`overview._card`), with its date shown.
- **Fall from best and near-breaker** use the home screen's figures: `card["off_peak"]`, `card["breaker"]` and `home._book(...)["near_breaker"]`, which uses `home.NEAR_BREAKER`.
- **Measurement single source:**
  - no drawdown, Sharpe, annualisation, tracking error, interval or monthly return outside `trading_algo/metrics.py`;
  - `tests/test_metrics_single_source.py` stays green.
- **No money maths in the page.** The server sends money as display text, made by `dashboard/textfmt.py`.
  - The minus sign is `−` (U+2212).
  - Money moves carry ▲/▼.
  - Dates read "13 Oct 2026".
- Every item carries `source`. Caveats travel with their numbers. A summary sentence carries the first sentence of its item's caveat.
- **Thresholds**, named once in `book_rules.py`:

  | Name | Value | Meaning |
  |---|---|---|
  | `SMALL_POSITIONS` | 30 | below this, the sample is small |
  | `SMALL_DATES` | 6 | below this many decision dates, the sample is small |
  | `IDLE_BELOW` | 0.5 | "mostly idle" below this invested share |
  | `COSTS_HALF` | 0.5 | costs above this share of the result before costs |
  | `FTSE_SHARE` | 0.5 | UK share of costs that triggers the UK line |
  | `RESIDUAL_SHARE` | 0.10 | FX residual share that triggers its note |
  | `FROZEN_RECENT` | 10 | frozen-price trades in 30 days that trigger the line |
  | `GAP_SPREADS` | 2.0 | market gap, in normal spreads, that counts as "beyond" |
  | `BACKTEST_STALE_DAYS` | 14 | backtest age that adds "may not reflect changes since" |
  | `LUCK_STALE_DAYS` | 30 | permutation report age that adds "over 30 days old" |

- **Main-problem ranking** (`book_rules.RANK`):

  | Rank | Rule |
  |---|---|
  | 1 | halted |
  | 2 | audit-error |
  | 3 | near-breaker |
  | 4 | stale |
  | 5 | costs-flip |
  | 6 | no-breaker |
  | 7 | holds-too-short |
  | 8 | loses-more-often |
  | 9 | behind-market |
  | 10 | mostly-idle |
  | 11 | audit-warn |
  | 12 | frozen-prices |

- **Backlog ids the builder may emit:** F20 (holds-too-short), F21 (frozen-prices), F22 (luck-test), F23 (mostly-idle, sized-down part), F25 (costs-flip when UK > 50% of costs). These come from PR #102. Until it is in this branch's base, the backlog test is `xfail(strict=True)`.
- **Scripts:** plain scripts, no ES modules, no framework. Only `blocks.js` defines a global (`MB`). The screen files register on `MB.screens`. Links are relative (`index.html?...`), never `/`.
- **Dependencies:** no new runtime dependency. Playwright and Chromium appear only in the CI `ui` job and locally. `REQUIRE_BROWSER=1` turns a browser-test skip into a failure.
- **Fixtures:** `tests/fixtures/books_20261013/` is copied from `git show d573791:state/<file>`. `NOW = 2026-10-13 12:00 UTC`.
- Commit after every task with the `Co-Authored-By` line the session uses. Ask before pushing.

## Review Focus

The five input classes the spec implies but no task's main path exercises. Each has a test in the task named.

1. **A book whose saved history has an unvaluable (`None`/`NaN`) mark** (FULL had four). Expected: the curve, chart, months and result skip it, and the JSON stays valid. *Test:* Task 9, `test_summary_survives_an_unvaluable_mark`.
2. **An equity book with no exchange rate saved for one sleeve's currency.** Expected: that sleeve's costs and cash count as 0 A$, and the screen says the rate is missing (audit `no-fx-rate` appears). No crash, no NaN. *Test:* Task 7, `test_missing_rate_never_crashes_costs_or_idle`.
3. **The background price fetch failing or hanging** (Yahoo 403 or offline). Expected: Positions still shows holdings at cost, says why, and the page stops polling. *Test:* Task 11, `test_failed_fetch_reports_error_and_retries_later`.
4. **Two audit findings with the same rule id on one book** (two `audit-warn`s). Expected: distinct `uid`s, so the summary can't map to the wrong one. *Test:* Task 9, `test_uids_are_unique_when_a_rule_fires_twice`.
5. **A key with unusual characters in the URL** (`/api/summary/FX%C2%B7MATT` or `../`). Expected: 404, no traceback, no file read. *Test:* Task 11, `test_unknown_or_odd_keys_are_404`.

---

## File structure

| File | Responsibility |
|---|---|
| `trading_algo/metrics.py` (modify) | + `monthly_returns`, `wilson_interval`, `expected_gap` |
| `trading_algo/verify.py` (modify) | `check_dead_price` adds `detail["recent"]` (additive; level and message unchanged) |
| `trading_algo/dashboard/api.py` (modify) | extract `fees_by_currency(trades)`; `build_snapshot` uses it |
| `trading_algo/dashboard/textfmt.py` (new) | number → display text, the only place |
| `trading_algo/dashboard/glossary.py` (new) | plain glosses on first use |
| `trading_algo/dashboard/episodes.py` (new) | closed **positions** (open → flat) and their results after costs |
| `trading_algo/dashboard/book_inputs.py` (new) | `BookInputs` + `load_inputs()`: saved data only |
| `trading_algo/dashboard/book_rules.py` (new) | every rule → items; `sleeve_reason`; thresholds; ranking |
| `trading_algo/dashboard/book_summary.py` (new) | `summarise(inp)`: the Overview answer |
| `trading_algo/dashboard/book_positions.py` (new) | `positions_view(inp)`: the Positions answer |
| `trading_algo/dashboard/live_marks.py` (new) | background live fetch + memo per book |
| `trading_algo/dashboard/book_service.py` (new) | glue: resolve key → inputs → answers |
| `trading_algo/dashboard/server.py` (modify) | `/api/summary`, `/api/positions`, `/api/marks`; `books=` |
| `trading_algo/home/__main__.py` (modify) | wire a `BookService` into `make_app` |
| `trading_algo/dashboard/static/blocks.js` (new) | shared building blocks on `window.MB` |
| `trading_algo/dashboard/static/screens/book_overview.js` (new) | Overview renderer |
| `trading_algo/dashboard/static/screens/book_positions.js` (new) | Positions renderer |
| `trading_algo/dashboard/static/home.js` (rewrite) | home + book shell (tabs, links, loading, polling) |
| `trading_algo/dashboard/static/home.html`, `home.css` (modify) | script order; styles |
| `trading_algo/dashboard/static/app.js` (modify) | `boot()` reads `?account=` and `?tab=` |
| `tests/book_fixtures.py` (new) | shared test helpers |
| `tests/fixtures/books_20261013/` (new) | real saved books, frozen |
| `.github/workflows/ci.yml` (modify) | `ui` job |

---

### Task 1: Measurement additions (`metrics.py`)

**Files:**
- Modify: `trading_algo/metrics.py` (add after `max_drawdown`)
- Test: `tests/test_metrics_screens.py` (new)

**Interfaces:**
- Produces:
  - `metrics.monthly_returns(equity: pd.Series) -> pd.Series`, with an index of `'YYYY-MM'` strings;
  - `metrics.wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]`;
  - `metrics.expected_gap(tracking_error: float, years: float) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
"""The three measurements the book screens add — kept in metrics.py, the one
source (CLAUDE.md; tests/test_metrics_single_source.py)."""
import pandas as pd
import pytest

from trading_algo import metrics


def test_monthly_returns_month_end_against_previous_month_end():
    idx = pd.to_datetime(["2026-06-11", "2026-06-30", "2026-07-15", "2026-07-31", "2026-08-03"])
    eq = pd.Series([100.0, 110.0, 99.0, 104.5, 104.5], index=idx)
    out = metrics.monthly_returns(eq)
    assert list(out.index) == ["2026-06", "2026-07", "2026-08"]
    assert out["2026-06"] == pytest.approx(0.10)              # vs the first mark
    assert out["2026-07"] == pytest.approx(104.5 / 110 - 1)
    assert out["2026-08"] == pytest.approx(0.0)


def test_monthly_returns_skips_unvaluable_marks():
    idx = pd.to_datetime(["2026-06-11", "2026-06-20", "2026-06-30"])
    eq = pd.Series([100.0, float("nan"), 105.0], index=idx)
    assert metrics.monthly_returns(eq)["2026-06"] == pytest.approx(0.05)


def test_monthly_returns_empty():
    assert metrics.monthly_returns(pd.Series(dtype=float)).empty


@pytest.mark.parametrize("wins,n,lo,hi", [(29, 77, 0.277, 0.488), (87, 197, 0.374, 0.511)])
def test_wilson_interval(wins, n, lo, hi):
    a, b = metrics.wilson_interval(wins, n)
    assert a == pytest.approx(lo, abs=0.001)
    assert b == pytest.approx(hi, abs=0.001)


def test_wilson_interval_empty_sample_says_nothing():
    assert metrics.wilson_interval(0, 0) == (0.0, 1.0)


def test_expected_gap_scales_with_root_years():
    assert metrics.expected_gap(0.10, 4.0) == pytest.approx(0.20)
    assert metrics.expected_gap(0.0, 4.0) == 0.0
    assert metrics.expected_gap(0.1, 0.0) == 0.0
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest tests/test_metrics_screens.py`
Expected: FAIL with `AttributeError: module 'trading_algo.metrics' has no attribute 'monthly_returns'`

- [ ] **Step 3: Implement** (append after `max_drawdown` in `trading_algo/metrics.py`; `np` and `pd` are already imported there)

```python
def monthly_returns(equity: pd.Series) -> pd.Series:
    """Calendar-month return of an equity curve, keyed 'YYYY-MM': each month's
    last mark against the previous month's last mark, the first month against
    the curve's first mark. THE month-by-month definition the book screens
    show — one source, like `drawdown`."""
    s = equity.dropna().sort_index()
    if s.empty:
        return pd.Series(dtype=float)
    month_end = s.groupby(s.index.to_period("M")).last()
    prev = month_end.shift(1)
    prev.iloc[0] = s.iloc[0]
    out = month_end / prev - 1.0
    out.index = [str(p) for p in out.index]
    return out


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (95% at z=1.96). Honest at small
    n, where the plain ± formula runs outside [0, 1]. (0, 1) when n == 0."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * float(np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return float(max(0.0, centre - half)), float(min(1.0, centre + half))


def expected_gap(tracking_error: float, years: float) -> float:
    """How far a book's cumulative return normally strays from its benchmark
    over `years`: annualised tracking error × √years (a random walk's spread).
    A gap inside about twice this is ordinary noise, not a verdict."""
    if not tracking_error or years <= 0:
        return 0.0
    return float(tracking_error * np.sqrt(years))
```

- [ ] **Step 4: Run to see them pass, plus the single-source guard**

Run: `python3 -m pytest tests/test_metrics_screens.py tests/test_metrics_single_source.py`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/metrics.py tests/test_metrics_screens.py
git commit -m "feat(metrics): month returns, Wilson interval and expected benchmark gap for the book screens"
```

---

### Task 2: Display text and plain words (`textfmt.py`, `glossary.py`)

**Files:**
- Create: `trading_algo/dashboard/textfmt.py`, `trading_algo/dashboard/glossary.py`
- Test: `tests/test_textfmt_glossary.py`

**Interfaces:**
- Produces:
  - `textfmt`:
    - `MINUS`, `CCY`;
    - `sign_char(v)`, `direction(v) -> "up"|"down"|"flat"`, `arrow(v)`;
    - `money(v, ccy="AUD", *, signed=False)`, `money_move(v, ccy="AUD")`;
    - `pct(v, dp=1, *, signed=True)`, `points(v)`, `day(stamp)`.
  - `glossary.TERMS: list[tuple[str, str, str]]` and `glossary.gloss(texts: list[str]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime

from trading_algo.dashboard import glossary, textfmt as t


def test_money_and_moves():
    assert t.money(-47.62) == "−A$47.62"
    assert t.money(1234.5, signed=True) == "+A$1,234.50"
    assert t.money(5, "GBP") == "£5.00"
    assert t.money_move(-47.62) == "▼ −A$47.62"
    assert t.money_move(0.0) == "▶ A$0.00"


def test_percent_points_and_days():
    assert t.pct(0.0019) == "+0.2%"
    assert t.pct(-0.000476, 2) == "−0.05%"
    assert t.pct(0.71, 0, signed=False) == "71%"
    assert t.points(-0.038) == "3.8 points"
    assert t.day("2026-10-13 03:00") == "13 Oct 2026"
    assert t.day(datetime(2026, 6, 11)) == "11 Jun 2026"
    assert t.direction(None) == "flat" and t.direction(-1) == "down"


def test_first_use_is_glossed_once_and_only_once():
    out = glossary.gloss(["Backtest Sharpe 0.32", "Sharpe again", "no terms"])
    assert out[0].startswith("Backtest (")                    # backtest glossed
    assert out[0].count("(") == 2                             # …and Sharpe
    assert out[1] == "Sharpe again"                           # second use: plain
    assert out[2] == "no terms"


def test_sharpe_inside_deflated_sharpe_is_not_glossed_separately():
    out = glossary.gloss(["no Deflated-Sharpe test passes"])
    assert out[0].count("(") == 1


def test_a_term_inside_a_gloss_is_never_glossed():
    gl = dict((term, g) for term, _, g in glossary.TERMS)
    out = glossary.gloss(["Deflated-Sharpe"])
    assert out[0] == f"Deflated-Sharpe ({gl['Deflated-Sharpe']})"


def test_every_term_has_a_plain_gloss():
    for term, pattern, g in glossary.TERMS:
        assert g and g[0].islower() and not g.endswith("."), term
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_textfmt_glossary.py`
Expected: FAIL with `ImportError` (no `textfmt`)

- [ ] **Step 3: Implement `textfmt.py`**

```python
"""Display text for the book screens — the ONE place numbers become words, so
the page never formats (or computes) money itself. Minus is U+2212; a money
move carries ▲/▼ so its direction never depends on colour alone."""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime

MINUS = "−"
CCY = {"AUD": "A$", "USD": "US$", "GBP": "£", "CAD": "C$", "EUR": "€", "JPY": "¥"}


def sign_char(v: float) -> str:
    return "+" if v > 0 else MINUS if v < 0 else ""


def direction(v: float | None) -> str:
    if v is None:
        return "flat"
    return "up" if v > 0 else "down" if v < 0 else "flat"


def arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "▶"


def money(v: float, ccy: str = "AUD", *, signed: bool = False) -> str:
    sym = CCY.get(ccy, f"{ccy} ")
    sgn = sign_char(v) if signed else (MINUS if v < 0 else "")
    return f"{sgn}{sym}{abs(v):,.2f}"


def money_move(v: float, ccy: str = "AUD") -> str:
    """A change in money: arrow + sign, e.g. '▼ −A$47.62'."""
    return f"{arrow(v)} {money(v, ccy, signed=True)}"


def pct(v: float, dp: int = 1, *, signed: bool = True) -> str:
    sgn = sign_char(v) if signed else (MINUS if v < 0 else "")
    return f"{sgn}{abs(v) * 100:.{dp}f}%"


def points(v: float) -> str:
    return f"{abs(v) * 100:.1f} points"


def day(stamp) -> str:
    """'2026-10-13' / '2026-10-13 03:00' / ISO / datetime -> '13 Oct 2026'."""
    d = stamp if isinstance(stamp, (datetime, _date)) else datetime.strptime(str(stamp)[:10], "%Y-%m-%d")
    return f"{d.day} {d.strftime('%b %Y')}"
```

- [ ] **Step 4: Implement `glossary.py`**

```python
"""Plain words for the terms the book screens can't avoid (Matt's standing
rule: plain English first, the term second). Each term is explained the first
time it appears on a screen. Matching runs on the original text only, so a
gloss is never itself glossed."""
from __future__ import annotations

import re

# (term, pattern, gloss) — longer terms first.
TERMS: list[tuple[str, str, str]] = [
    ("Deflated-Sharpe", r"\bDeflated-Sharpe\b",
     "a Sharpe score marked down for how many versions of the strategy were tried, so luck isn't mistaken for skill"),
    ("Sharpe", r"(?<![-\w])Sharpe\b",
     "return per unit of ups and downs after the return on cash; above 1 is strong, below 0 did worse than cash"),
    ("permutation test", r"\bpermutation test\b",
     "re-running the search on shuffled, patternless prices to see how often luck alone does as well"),
    ("p-value", r"\bp-value\b", "the share of shuffled runs that did at least as well"),
    ("profit factor", r"\bprofit factor\b",
     "money won on winning positions divided by money lost on losing ones; above 1 means winners paid for losers"),
    ("survivorship-biased", r"\bsurvivorship-biased\b",
     "tested only on companies that still exist today, which flatters the result"),
    ("signal horizon", r"\bsignal horizon\b", "how far ahead the signal that opens a position is looking"),
    ("volatility target", r"\bvolatility target\b",
     "positions are sized so the book's expected ups and downs stay near a set level"),
    ("long-term trend", r"\blong-term trend\b",
     "the market's own average price over a long look-back; below it, the strategy holds cash"),
    ("safety breaker", r"\bsafety breaker\b", "a rule that pauses trading if the book falls too far from its peak"),
    ("below its best", r"\bbelow its best\b", "how far the book has fallen from its highest value"),
    ("stamp duty", r"\bstamp duty\b", "a 0.5% UK tax on buying shares"),
    ("spread", r"\bspread\b", "the gap between the buying and selling price, paid on every trade"),
    ("financing", r"\bfinancing\b", "the overnight interest paid or earned on currency positions"),
    ("backtest", r"\bbacktest\b", "a replay of the strategy on past prices, costs included"),
    ("benchmark", r"\bbenchmark\b", "what simply holding the market's index funds would have done"),
    ("tracking error", r"\btracking error\b", "how much the book's return normally wanders from the market's"),
    ("basis points", r"\bbasis points\b", "hundredths of a percent; 46 basis points is 0.46%"),
]
_COMPILED = [(term, re.compile(pattern, re.IGNORECASE), g) for term, pattern, g in TERMS]


def gloss(texts: list[str]) -> list[str]:
    """`texts` in display order, each term's FIRST occurrence followed by
    ' (gloss)'. Later occurrences stay plain."""
    pending = list(_COMPILED)
    out: list[str] = []
    for text in texts:
        if not text:
            out.append(text)
            continue
        inserts: list[tuple[int, str]] = []
        taken: list[tuple[int, int]] = []
        for entry in list(pending):
            term, rx, g = entry
            for m in rx.finditer(text):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                inserts.append((m.end(), g))
                taken.append((m.start(), m.end()))
                pending.remove(entry)
                break
        for end, g in sorted(inserts, reverse=True):
            text = f"{text[:end]} ({g}){text[end:]}"
        out.append(text)
    return out
```

- [ ] **Step 5: Run to see them pass**

Run: `python3 -m pytest tests/test_textfmt_glossary.py`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add trading_algo/dashboard/textfmt.py trading_algo/dashboard/glossary.py tests/test_textfmt_glossary.py
git commit -m "feat(dashboard): display text and first-use glossary for the book screens"
```

---

### Task 3: Closed positions, not lots (`episodes.py`)

**Files:**
- Create: `trading_algo/dashboard/episodes.py`
- Test: `tests/test_episodes.py`

**Interfaces:**
- Produces:
  - `equity_events(trades) -> list[tuple[str, tuple[str, str], float]]`, as `(date, (region, ticker), shares held after the fill)`;
  - `fx_events(trades) -> list[tuple[str, str, float]]`, as `(date, pair, target weight after the trade)`;
  - `episodes(events) -> list[dict]`, each `{symbol, opened, closed|None, side}`;
  - `closed_positions(events, lots, *, symbol_of, net_of, exclude_dates=frozenset()) -> tuple[list[dict], int]`. The list holds `{symbol, opened, closed, side, net (float|None), lots}`; the int is how many were excluded;
  - `forced_dates(state) -> set[str]`.

- [ ] **Step 1: Write the failing tests**

```python
from trading_algo.dashboard import episodes as E

NET = dict(symbol_of=lambda lot: lot["sym"], net_of=lambda lot: lot["net"])


def test_a_trimmed_position_is_one_position():
    events = [("2026-07-01", "AAPL", 10), ("2026-07-15", "AAPL", 5), ("2026-08-01", "AAPL", 0)]
    lots = [{"date": "2026-07-15", "sym": "AAPL", "net": 20.0},
            {"date": "2026-08-01", "sym": "AAPL", "net": -5.0}]
    out, excluded = E.closed_positions(events, lots, **NET)
    assert excluded == 0
    assert out == [{"symbol": "AAPL", "opened": "2026-07-01", "closed": "2026-08-01",
                    "side": "LONG", "net": 15.0, "lots": 2}]


def test_a_flip_closes_one_position_and_opens_another():
    events = [("2026-07-01", "EURUSD", 0.2), ("2026-07-05", "EURUSD", -0.1),
              ("2026-07-09", "EURUSD", 0.0)]
    lots = [{"date": "2026-07-05", "sym": "EURUSD", "net": 3.0},
            {"date": "2026-07-09", "sym": "EURUSD", "net": -1.0}]
    out, _ = E.closed_positions(events, lots, **NET)
    assert [(p["side"], p["net"]) for p in out] == [("LONG", 3.0), ("SHORT", -1.0)]


def test_an_open_position_is_not_counted():
    out, _ = E.closed_positions([("2026-07-01", "X", 5)], [], **NET)
    assert out == []


def test_forced_close_dates_are_left_out_and_counted():
    events = [("2026-07-01", "A", 1), ("2026-08-27", "A", 0),
              ("2026-07-02", "B", 1), ("2026-09-01", "B", 0)]
    lots = [{"date": "2026-08-27", "sym": "A", "net": 276.0},
            {"date": "2026-09-01", "sym": "B", "net": -3.0}]
    out, excluded = E.closed_positions(events, lots, exclude_dates={"2026-08-27"}, **NET)
    assert excluded == 1 and [p["symbol"] for p in out] == ["B"]


def test_a_lot_with_no_known_result_makes_the_position_unmeasured():
    events = [("2026-07-01", "A", 1), ("2026-07-03", "A", 0)]
    out, _ = E.closed_positions(events, [{"date": "2026-07-03", "sym": "A", "net": None}], **NET)
    assert out[0]["net"] is None


def test_equity_events_follow_buys_and_sells_and_skip_cash_rows():
    trades = [{"date": "2026-07-01", "region": "US", "ticker": "AAPL", "side": "BUY", "shares": 10},
              {"date": "2026-07-02", "region": "US", "ticker": "AAPL", "side": "DIV", "amount": 1.0},
              {"date": "2026-07-03", "region": "US", "ticker": "AAPL", "side": "SELL", "shares": 4},
              {"date": "2026-07-04", "region": "US", "ticker": "AAPL", "side": "SELL", "shares": 6}]
    assert [q for _, _, q in E.equity_events(trades)] == [10, 6, 0]


def test_forced_dates_read_the_corrections_block():
    state = {"corrections": [{"rows": [{"date": "2026-08-27"}, {"date": "2026-08-28 00:00"}]}]}
    assert E.forced_dates(state) == {"2026-08-27", "2026-08-28"}
    assert E.forced_dates({}) == set()
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_episodes.py`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
"""Closed POSITIONS (opened from flat, then back to flat or flipped) and their
results after costs, from a ledger of signed holdings plus the FIFO lots that
closed them. A position, not a lot: one name trimmed five times is one
position, not five wins (adversarial review 2026-10-14: matt's 197 closed lots
are 77 positions, and the win rate differs — 44% vs 38%)."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .. import pnl

_DUST = 1e-9


def _sign(q: float) -> int:
    return 1 if q > _DUST else -1 if q < -_DUST else 0


def equity_events(trades: list[dict]) -> list[tuple[str, tuple[str, str], float]]:
    held: dict[tuple[str, str], float] = {}
    out = []
    for t in sorted((t for t in trades if pnl.is_fill(t)), key=lambda t: str(t["date"])):
        sym = (t["region"], t["ticker"])
        qty = float(t["shares"]) if t["side"] == "BUY" else -float(t["shares"])
        held[sym] = held.get(sym, 0.0) + qty
        out.append((str(t["date"]), sym, held[sym]))
    return out


def fx_events(trades: list[dict]) -> list[tuple[str, str, float]]:
    return [(str(t["date"]), t["pair"], float(t.get("target_weight", 0.0)))
            for t in sorted((t for t in trades if t.get("pair") and t.get("date")),
                            key=lambda t: str(t["date"]))]


def episodes(events) -> list[dict]:
    cur: dict = {}
    open_: dict = {}
    out: list[dict] = []
    for date, sym, q in events:
        prev = cur.get(sym, 0.0)
        cur[sym] = q
        sp, sq = _sign(prev), _sign(q)
        if sp != 0 and sq != sp and sym in open_:
            ep = open_.pop(sym)
            ep["closed"] = date
            out.append(ep)
        if sq != 0 and sq != sp:
            open_[sym] = {"symbol": sym, "opened": date, "closed": None,
                          "side": "LONG" if sq > 0 else "SHORT"}
    return out + list(open_.values())


def closed_positions(events, lots, *, symbol_of: Callable, net_of: Callable,
                     exclude_dates=frozenset()) -> tuple[list[dict], int]:
    closed = [e for e in episodes(events) if e["closed"] is not None]
    by_sym: dict = defaultdict(list)
    for e in closed:
        e["net"], e["lots"] = 0.0, 0
        by_sym[e["symbol"]].append(e)
    for lot in lots:
        d = str(lot["date"])
        for e in by_sym.get(symbol_of(lot), ()):
            if e["opened"] <= d <= e["closed"]:
                v = net_of(lot)
                e["net"] = None if (v is None or e["net"] is None) else e["net"] + float(v)
                e["lots"] += 1
                break
    kept = [e for e in closed if e["closed"][:10] not in exclude_dates]
    for e in kept:
        if e["net"] is not None:
            e["net"] = round(e["net"], 2)
    return kept, len(closed) - len(kept)


def forced_dates(state: dict) -> set[str]:
    """Dates a recorded correction says a bug forced trades (e.g. FULL's
    2026-08-27 phantom-breaker liquidation)."""
    return {str(r["date"])[:10] for c in state.get("corrections") or []
            for r in c.get("rows") or [] if r.get("date")}
```

- [ ] **Step 4: Run to see them pass**

Run: `python3 -m pytest tests/test_episodes.py`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/episodes.py tests/test_episodes.py
git commit -m "feat(dashboard): closed positions (not lots) and forced-close dates"
```

---

### Task 4: Frozen fixtures and the saved-data loader (`book_inputs.py`)

**Files:**
- Create: `tests/fixtures/books_20261013/` (copies), `tests/book_fixtures.py`, `trading_algo/dashboard/book_inputs.py`
- Modify: `trading_algo/dashboard/api.py`: extract `fees_by_currency`; `build_snapshot` calls it
- Test: `tests/test_book_inputs.py`

**Interfaces:**
- Consumes: `episodes.*` (Task 3).
- Produces:
  - `api.fees_by_currency(trades) -> tuple[dict[str, float], dict[str, float]]`;
  - `book_inputs.BookInputs`, a dataclass with fields `entry, state, card, book, now, findings, backtest, permtest, roster, costs, fx_book, closed_rows, positions_closed, excluded_forced, live, market, errors` and properties `kind`, `account`;
  - `book_inputs.load_inputs(entry, now, *, live=None, market="n/a") -> BookInputs | None`;
  - `tests/book_fixtures.py`: `FIXTURE`, `NOW`, `BOOK_FILES`, `use_snapshot(tmp_path, monkeypatch) -> Path`, `no_network(monkeypatch)`, `make_inp(...)`.
  - `costs` shape:
    - equity `{"total", "by_ccy", "stamp", "includes"}`;
    - FX `{"total", "includes"}`.

- [ ] **Step 1: Create the frozen fixtures from `main`'s 13 Oct state**

```bash
mkdir -p tests/fixtures/books_20261013
for f in paper_state_full.json paper_state_ultra.json paper_state_experimental.json \
         paper_state_small.json fx_state_matt.json fx_state_partner.json \
         fx_state_multiasset.json fx_state_daytrader.json backtest_equity.json \
         fx_backtest_matt.json fx_backtest_partner.json fx_backtest_multiasset.json \
         fx_backtest_daytrader.json permtest_matt.json champions_matt.json \
         champions_partner.json champions_multiasset.json champions_daytrader.json; do
  git show "d573791:state/$f" > "tests/fixtures/books_20261013/$f"
done
ls tests/fixtures/books_20261013 | wc -l
```
Expected: `18`

- [ ] **Step 2: Write `tests/book_fixtures.py`**

```python
"""Shared helpers for the book-screen tests: the frozen 13 Oct books, a
network tripwire, and a minimal hand-built BookInputs."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "books_20261013"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
BOOK_FILES = sorted(p.name for p in FIXTURE.glob("*_state_*.json"))


def use_snapshot(tmp_path, monkeypatch) -> Path:
    """Copy the frozen books to a temp dir and point every reader at it — the
    module constants (fixed at import) AND the env vars (read at call time)."""
    import trading_algo.paper_trade as pt
    from trading_algo.forex import fx_book

    dst = tmp_path / "state"
    shutil.copytree(FIXTURE, dst)
    monkeypatch.setattr(pt, "STATE_DIR", str(dst))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(dst))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(dst))
    monkeypatch.setenv("FX_STATE_DIR", str(dst))
    return dst


def no_network(monkeypatch) -> None:
    """Any price or FX download fails the test loudly."""
    from trading_algo import data, fx, paper_trade

    def boom(*_a, **_k):
        raise AssertionError("network access during a saved-values build")

    monkeypatch.setattr(data, "_download_primary", boom)
    monkeypatch.setattr(paper_trade, "latest_region_data", boom)
    monkeypatch.setattr(fx, "load_fx", boom)


def make_inp(kind: str = "equity", *, state=None, card=None, book=None, findings=(),
             costs=None, fx_book=None, positions_closed=(), excluded=0, backtest=None,
             permtest=None, roster=None, live=None, market="n/a", entry=None, now=NOW):
    from trading_algo.dashboard.book_inputs import BookInputs

    card = {"key": "T", "name": "T", "kind": kind, "group": "CORE", "equity": 100_000.0,
            "initial": 100_000.0, "since": "2026-06-11", "off_peak": 0.0,
            "breaker": 0.25, "halted": False, "status": "CLEAR", **(card or {})}
    marks = [(datetime(2026, 6, 11, tzinfo=timezone.utc), card["initial"]),
             (datetime(2026, 10, 13, tzinfo=timezone.utc), card["equity"])]
    book = {"halted": card["halted"], "near_breaker": False, "freshness": "fresh",
            "as_of": "2026-10-13T00:00:00+00:00", "_marks": marks, **(book or {})}
    state = {"sleeves": {}, "allocations": {}, "trades": [], "fx_snapshot": {},
             "param_overrides": {}, **(state or {})}
    entry = entry or {"key": "T", "account": "t", "kind": kind, "label": "T", "micro": False}
    return BookInputs(
        entry=entry, state=state, card=card, book=book, now=now, findings=list(findings),
        backtest=backtest or {"available": False}, permtest=permtest, roster=roster,
        costs=costs if costs is not None else {"total": 0.0, "by_ccy": {}, "stamp": {},
                                               "includes": "commission and stamp duty"},
        fx_book=fx_book, positions_closed=list(positions_closed), excluded_forced=excluded,
        live=live, market=market)
```

- [ ] **Step 3: Write the failing tests**

```python
"""book_inputs reads only saved data — never a price download — and one bad
input becomes an error entry, not a blank screen."""
import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import api, book_inputs, registry


def test_fees_by_currency_sums_commission_and_stamp():
    trades = [{"currency": "GBP", "commission": 3.0, "stamp_duty": 5.0},
              {"currency": "USD", "commission": 1.0},
              {"currency": "GBP", "commission": 2.0, "stamp_duty": 0.0}]
    fees, stamp = api.fees_by_currency(trades)
    assert fees == {"GBP": 10.0, "USD": 1.0} and stamp == {"GBP": 5.0}


def test_every_real_book_loads_with_no_network(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    entries = registry.discover_accounts()
    assert {e["key"] for e in entries} >= {"FULL", "ULTRA", "EXPERIMENTAL", "SMALL",
                                           "MATT", "PARTNER", "MULTI", "DAY"}
    for e in entries:
        inp = book_inputs.load_inputs(e, NOW)
        assert inp is not None and inp.errors == {}, (e["key"], inp and inp.errors)


def test_full_reads_its_saved_value_and_excludes_the_forced_closes(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW)
    assert inp.card["equity"] == pytest.approx(99_952.38)
    assert inp.excluded_forced > 0                       # 27 Aug liquidation
    assert inp.costs["total"] > 0 and "GBP" in inp.costs["stamp"]
    assert inp.backtest["available"] is True


def test_matt_has_its_luck_test_and_an_empty_roster(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    inp = book_inputs.load_inputs(registry.resolve("MATT"), NOW)
    assert inp.permtest["p_value"] == pytest.approx(0.4726, abs=1e-3)
    assert inp.roster == 0
    assert inp.fx_book is not None and len(inp.positions_closed) > 0


def test_one_broken_input_is_reported_not_raised(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    from trading_algo import verify

    def broken(*_a, **_k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(verify, "verify_book", broken)
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW)
    assert "audit" in inp.errors and inp.findings == []
```

- [ ] **Step 4: Run to see them fail**

Run: `python3 -m pytest tests/test_book_inputs.py`
Expected: FAIL. `fees_by_currency` is missing (AttributeError) and `book_inputs` is missing (ImportError).

- [ ] **Step 5: Extract `fees_by_currency` in `api.py`**

Add this function above `build_snapshot`:

```python
def fees_by_currency(trades: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """(commission + stamp duty, stamp duty alone) per currency, from the fills
    ledger — the one place the screens' cost figures come from."""
    fees: dict[str, float] = {}
    stamp: dict[str, float] = {}
    for t in trades:
        fees[t["currency"]] = fees.get(t["currency"], 0.0) \
            + t.get("commission", 0.0) + t.get("stamp_duty", 0.0)
        if t.get("stamp_duty"):
            stamp[t["currency"]] = stamp.get(t["currency"], 0.0) + t["stamp_duty"]
    return fees, stamp
```

Then in `build_snapshot`, replace the block that starts `# fees grouped by currency, and totalled into the base currency`, up to but not including the `fees_base = ...` line, with:

```python
    # fees grouped by currency, and totalled into the base currency
    fees, stamp = fees_by_currency(state["trades"])
```

- [ ] **Step 6: Implement `book_inputs.py`**

```python
"""Everything one book's screens need, gathered from SAVED data only — no
price download, no network (measured 2026-10-14: the live re-mark took ~50 s
per equity book and disagreed with the home screen). `book_summary` and
`book_positions` are pure functions of what this returns; the only optional
live part is `live`, a payload the background fetch (`live_marks`) finished."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import config as cfg
from .. import fx, verify
from ..forex import fx_book as fxb
from ..forex import fx_pnl, permtest
from . import api, backtest_store, episodes, home, overview


@dataclass
class BookInputs:
    entry: dict
    state: dict
    card: dict
    book: dict
    now: datetime
    findings: list = field(default_factory=list)
    backtest: dict = field(default_factory=lambda: {"available": False})
    permtest: dict | None = None
    roster: int | None = None
    costs: dict = field(default_factory=dict)
    fx_book: dict | None = None
    closed_rows: list = field(default_factory=list)
    positions_closed: list = field(default_factory=list)
    excluded_forced: int = 0
    live: dict | None = None
    market: str = "n/a"
    errors: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.entry["kind"]

    @property
    def account(self) -> str:
        return self.entry["account"]


def _safe(errors: dict, name: str, fn, default):
    try:
        return fn()
    except Exception as exc:            # one bad input must not blank the screen
        errors[name] = repr(exc)
        return default


def _equity_costs(state: dict) -> dict:
    base = state.get("base_currency", cfg.BASE_CURRENCY)
    snap = state.get("fx_snapshot") or {}
    fees, stamp = api.fees_by_currency(state.get("trades") or [])

    def conv(ccy, v):
        return v * (fx.base_per_local(snap, ccy, base) or 0.0)

    by_ccy = {c: round(conv(c, v), 2) for c, v in fees.items()}
    return {"total": round(sum(by_ccy.values()), 2), "by_ccy": by_ccy,
            "stamp": {c: round(conv(c, v), 2) for c, v in stamp.items()},
            "includes": "commission and stamp duty"}


def _roster(account: str) -> int | None:
    try:
        with open(os.path.join(fxb.STATE_DIR, f"champions_{account}.json")) as fh:
            return len(json.load(fh).get("roster") or [])
    except (OSError, ValueError):
        return None


def load_inputs(entry: dict, now: datetime, *, live: dict | None = None,
                market: str = "n/a") -> BookInputs | None:
    state = overview._load(entry)
    if state is None:
        return None
    card = overview._card(entry, state)
    if card is None:
        return None
    errors: dict = {}
    inp = BookInputs(entry=entry, state=state, card=card,
                     book=home._book(entry, state, card, now), now=now,
                     live=live, market=market, errors=errors)
    today = now.astimezone(timezone.utc).replace(tzinfo=None)
    inp.findings = _safe(errors, "audit", lambda: verify.verify_book(
        entry["kind"], entry["account"], state, today), [])
    inp.backtest = _safe(errors, "backtest", lambda: backtest_store.load_backtest(
        entry["kind"], entry["account"]), {"available": False})
    forced = episodes.forced_dates(state)
    trades = state.get("trades") or []
    if entry["kind"] == "fx":
        inp.permtest = _safe(errors, "luck test", lambda: permtest.load_report(entry["account"]), None)
        inp.roster = _roster(entry["account"])
        inp.fx_book = _safe(errors, "costs", lambda: fx_pnl.book_pnl(state), None)
        if inp.fx_book is not None:
            inp.costs = {"total": inp.fx_book["costs"], "includes": "the spread paid on trades"}
            inp.closed_rows = inp.fx_book["closed"]
            inp.positions_closed, inp.excluded_forced = _safe(
                errors, "closed positions", lambda: episodes.closed_positions(
                    episodes.fx_events(trades), inp.closed_rows,
                    symbol_of=lambda r: r["pair"], net_of=lambda r: r["net"],
                    exclude_dates=forced), ([], 0))
    else:
        inp.costs = _safe(errors, "costs", lambda: _equity_costs(state), {})
        base = state.get("base_currency", cfg.BASE_CURRENCY)
        closed = _safe(errors, "closed trades", lambda: api.closed_trades(
            trades, state.get("fx_snapshot") or {}, base), {"rows": []})
        inp.closed_rows = closed["rows"]
        inp.positions_closed, inp.excluded_forced = _safe(
            errors, "closed positions", lambda: episodes.closed_positions(
                episodes.equity_events(trades), inp.closed_rows,
                symbol_of=lambda r: (r["region"], r["ticker"]),
                net_of=lambda r: r["net_base"], exclude_dates=forced), ([], 0))
    return inp
```

- [ ] **Step 7: Run to see them pass, plus the existing API tests**

Run: `python3 -m pytest tests/test_book_inputs.py tests/test_dashboard_api_book.py tests/test_dashboard.py`
Expected: PASS (all). If `test_every_real_book_loads_with_no_network` reports an `errors` entry, fix the loader. **Never** loosen the assertion.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/books_20261013 tests/book_fixtures.py tests/test_book_inputs.py \
        trading_algo/dashboard/book_inputs.py trading_algo/dashboard/api.py
git commit -m "feat(dashboard): load one book's screen inputs from saved data only"
```

---

### Task 5: Rules I: attention, result, market, going right (`book_rules.py`)

**Files:**
- Create: `trading_algo/dashboard/book_rules.py`
- Test: `tests/test_book_rules_attention.py`

**Interfaces:**
- Consumes: `BookInputs` (Task 4), `textfmt` (Task 2), `metrics.expected_gap` (Task 1).
- Produces (later tasks rely on these exact names):
  - constants: the thresholds from Global Constraints, plus `RANK` and `COVERED_BY`;
  - `item(rule, column, text, *, source, as_of=None, caveat=None, backlog=None, level=None, dir=None) -> dict`, with keys `id, column, text, caveat, source, as_of, backlog, level, rank, dir`;
  - `as_of(inp) -> str|None`, `long_short(inp) -> bool`, `max_gross(inp) -> float`, `daily_values(inp) -> pd.Series`;
  - `market(inp) -> dict|None`, with keys `gap, spread, bench_ret, beyond, last`;
  - `MARKET_TEXT: dict[str, str]`;
  - `plain_finding(f) -> str`;
  - `closed_stats(inp) -> tuple[list, float, float, int, int]`, as `(positions, won, lost, wins, dates)`;
  - `result(inp) -> dict`, `attention(inp) -> list[dict]`, `going_right(inp) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timedelta, timezone

import pytest

from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.verify import ERROR, WARN, Finding


def ids(items):
    return [i["id"] for i in items]


def test_halted_is_act_and_says_days_left():
    inp = make_inp(card={"halted": True}, state={"halt_cooldown": 4})
    out = R.attention(inp)
    assert out[0]["id"] == "halted" and out[0]["level"] == "act" and "4 trading days" in out[0]["text"]


def test_an_audit_error_is_shown_in_plain_words():
    f = Finding(ERROR, "t", "position-mismatch", "sleeve US: ledger replay …", {"subject": "US"})
    out = R.attention(make_inp(findings=[f]))
    assert ids(out) == ["audit-error"] and "US" in out[0]["text"] and "ledger replay" not in out[0]["text"]


def test_near_breaker_follows_the_home_screen_flag():
    assert ids(R.attention(make_inp(book={"near_breaker": True}, card={"off_peak": -0.16}))) == ["near-breaker"]
    assert R.attention(make_inp()) == []


@pytest.mark.parametrize("fresh,fires", [("fresh", False), ("late", True), ("stale", True)])
def test_late_or_stale_data_needs_attention(fresh, fires):
    assert ("stale" in ids(R.attention(make_inp(book={"freshness": fresh})))) is fires


def test_no_breaker_only_on_a_geared_book():
    geared = make_inp(card={"breaker": None}, state={"param_overrides": {"max_gross": 3.0}})
    plain = make_inp(card={"breaker": None})
    assert ids(R.attention(geared)) == ["no-breaker"] and R.attention(plain) == []


def test_a_brand_new_book_is_too_new_to_judge():
    inp = make_inp(book={"_marks": [(datetime(2026, 10, 13, tzinfo=timezone.utc), 1000.0)]})
    assert "too new to judge" in R.result(inp)["text"]


def test_result_reads_the_saved_value_with_its_date():
    r = R.result(make_inp(card={"equity": 99_952.38}, market="offline"))
    assert r["text"].startswith("Down A$47.62 (−0.05%) since 11 Jun 2026, as of 13 Oct 2026")
    assert r["dir"] == "down" and "offline" in r["text"]


def test_long_short_and_fx_books_are_not_compared_with_a_market():
    ls = make_inp(state={"param_overrides": {"long_short": True}}, market="ready", live={})
    assert "isn't compared" in R.result(ls)["text"]
    assert "isn't compared" in R.result(make_inp("fx"))["text"]


def _with_market(book_vals, bench_vals):
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    marks = [(start + timedelta(days=i), v) for i, v in enumerate(book_vals)]
    bench = [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "value": v}
             for i, v in enumerate(bench_vals)]
    return make_inp(card={"equity": book_vals[-1], "initial": book_vals[0]},
                    book={"_marks": marks, "as_of": marks[-1][0].isoformat()},
                    live={"benchmark_curve": bench}, market="ready")


def test_a_steady_gap_is_beyond_normal_and_counts_as_behind():
    inp = _with_market([100_000.0] * 60, [100_000.0 * (1.001 ** i) for i in range(60)])
    m = R.market(inp)
    assert m["beyond"] and m["gap"] < 0
    assert "more than its normal" in R.result(inp)["text"]
    assert "dividends" in R.result(inp)["caveat"]


def test_a_small_gap_in_a_noisy_market_is_within_normal():
    bench = [100_000.0 * (1.01 if i % 2 else 0.99) for i in range(59)] + [100_500.0]
    inp = _with_market([100_000.0] * 60, bench)
    assert not R.market(inp)["beyond"] and "within its normal" in R.result(inp)["text"]
    assert R.going_right(inp) == []


def test_winners_pay_needs_a_positive_closed_result():
    pos = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG", "net": n, "lots": 1}
           for n in (100.0, -40.0, 10.0)]
    out = R.going_right(make_inp(positions_closed=pos))
    assert ids(out) == ["winners-pay"] and "profit factor 2.8" in out[0]["text"]
    assert "small sample" in out[0]["caveat"].lower()
    lose = [dict(p, net=n) for p, n in zip(pos, (10.0, -40.0, 5.0))]
    assert R.going_right(make_inp(positions_closed=lose)) == []


def test_winners_pay_says_what_it_left_out():
    pos = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG", "net": 5.0, "lots": 1}]
    out = R.going_right(make_inp(positions_closed=pos, excluded=16))
    assert "16" in out[0]["caveat"] and "since-fixed bug" in out[0]["caveat"]
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_book_rules_attention.py`
Expected: FAIL with `ImportError` (no `book_rules`)

- [ ] **Step 3: Implement the first half of `book_rules.py`**

```python
"""The "Is it working?" rules (docs/specs/book-screens.md). Each rule reads a
BookInputs and returns items; nothing here reads a file, the network or the
clock. Thresholds are the spec's, named once here."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .. import config as cfg
from .. import fx, metrics, pnl
from ..forex import fx_config
from ..regions import get_region
from .textfmt import day, direction, money, pct, points

SMALL_POSITIONS = 30
SMALL_DATES = 6
IDLE_BELOW = 0.5
COSTS_HALF = 0.5
FTSE_SHARE = 0.5
RESIDUAL_SHARE = 0.10
FROZEN_RECENT = 10
GAP_SPREADS = 2.0
BACKTEST_STALE_DAYS = 14
LUCK_STALE_DAYS = 30

RANK = {"halted": 1, "audit-error": 2, "near-breaker": 3, "stale": 4,
        "costs-flip": 5, "no-breaker": 6, "holds-too-short": 7,
        "loses-more-often": 8, "behind-market": 9, "mostly-idle": 10,
        "audit-warn": 11, "frozen-prices": 12}
# An audit WARN a rule already explains is shown once, by that rule.
COVERED_BY = {"idle-sleeve": "mostly-idle", "pinned-flat": "mostly-idle",
              "hold-shorter-than-signal": "holds-too-short",
              "dead-price-turnover": "frozen-prices"}
MARKET_TEXT = {
    "pending": "market comparison loading",
    "idle": "market comparison loading",
    "n/a": "market comparison not loaded",
    "offline": "the market comparison needs today's prices, which aren't available offline",
    "error": "couldn't load the market comparison",
}
DIVIDENDS = "The market figure leaves out dividends, which flatters this book."
PLAIN = {
    "no-fx-rate": "No exchange rate is saved for {subject}, so its money can't be checked",
    "position-mismatch": "The holdings saved for {subject} don't match its trade record",
    "cash-drift": "The cash saved for {subject} doesn't match its trade record",
    "missing-position": "The saved position in {subject} doesn't match its trade record",
    "weight-mismatch": "The saved position in {subject} doesn't match its trade record",
    "closed-market-trade": "Some trades were booked while their market was shut",
    "never-traded": "{subject} has never traded and its price data looks broken",
    "idle-sleeve": "{subject} has been sitting in cash for a long time",
    "pinned-flat": "{subject} holds nothing until next month's rebalance",
    "dead-price-turnover": "Some trades changed a position at a frozen price, paying costs for nothing",
    "hold-shorter-than-signal": "Positions close long before their signal horizon",
    "unreadable-state": "The saved book couldn't be read",
}


def item(rule: str, column: str, text: str, *, source: str, as_of: str | None = None,
         caveat: str | None = None, backlog: str | None = None,
         level: str | None = None, dir: str | None = None) -> dict:
    return {"id": rule, "column": column, "text": text, "caveat": caveat,
            "source": source, "as_of": as_of, "backlog": backlog, "level": level,
            "rank": RANK.get(rule), "dir": dir}


def as_of(inp) -> str | None:
    return (inp.book.get("as_of") or "")[:10] or None


def long_short(inp) -> bool:
    return bool((inp.state.get("param_overrides") or {}).get("long_short"))


def max_gross(inp) -> float:
    return float((inp.state.get("param_overrides") or {}).get("max_gross") or 1.0)


def daily_values(inp) -> pd.Series:
    """The saved value, last mark per day (unvaluable marks were already
    dropped by home._marks)."""
    daily: dict[str, float] = {}
    for t, v in inp.book.get("_marks") or []:
        daily[t.date().isoformat()] = float(v)
    keys = sorted(daily)
    return pd.Series([daily[k] for k in keys], index=pd.to_datetime(keys), dtype=float)


def _src_saved(inp) -> str:
    d = as_of(inp)
    return f"saved book, {day(d)}" if d else "saved book"


def _src_audit(inp) -> str:
    return f"live audit, {day(inp.now)}"


def plain_finding(f) -> str:
    subject = (f.detail or {}).get("subject") or "One part of the book"
    tmpl = PLAIN.get(f.code)
    return tmpl.format(subject=subject) if tmpl else f"Audit finding: {f.message}"


def market(inp) -> dict | None:
    """The book against its benchmark to the last SAVED date, and how far the
    two normally stray. None when there's nothing fair to compare."""
    if inp.kind != "equity" or long_short(inp) or inp.market != "ready" or not inp.live:
        return None
    bench = {r["date"]: float(r["value"]) for r in inp.live.get("benchmark_curve") or []
             if r.get("value") is not None}
    vals = daily_values(inp)
    days = [d for d in vals.index if d.strftime("%Y-%m-%d") in bench]
    if len(days) < 3:
        return None
    s = vals.loc[days]
    b = pd.Series([bench[d.strftime("%Y-%m-%d")] for d in days], index=days, dtype=float)
    initial = float(inp.card["initial"])
    gap = (s.iloc[-1] / initial - 1.0) - (b.iloc[-1] / initial - 1.0)
    stats = metrics.benchmark_stats(s.pct_change().dropna(), b.pct_change().dropna())
    spread = metrics.expected_gap(stats.get("TrackingError") or 0.0,
                                  metrics.years_between(days[0], days[-1]))
    return {"gap": float(gap), "spread": spread, "bench_ret": float(b.iloc[-1] / initial - 1.0),
            "beyond": abs(gap) > GAP_SPREADS * spread, "last": days[-1].strftime("%Y-%m-%d")}


def result(inp) -> dict:
    marks = inp.book.get("_marks") or []
    since = inp.card.get("since")
    if len(marks) < 2:
        text = f"Started {day(since)}; too new to judge" if since else "No saved values yet; too new to judge"
        return item("result", "result", text, source=_src_saved(inp))
    eq, init = float(inp.card["equity"]), float(inp.card["initial"])
    chg = eq - init
    word = "Up" if chg > 0 else "Down" if chg < 0 else "Flat"
    text = (f"{word} {money(abs(chg))} ({pct(chg / init, 2)}) since {day(since)}, "
            f"as of {day(as_of(inp))}")
    caveat = None
    m = market(inp)
    if m is not None:
        side = "ahead of" if m["gap"] > 0 else "behind"
        text += (f"; {points(m['gap'])} {side} the market, "
                 + ("more than its normal ups and downs" if m["beyond"]
                    else "within its normal ups and downs"))
        caveat = DIVIDENDS
    elif inp.kind == "fx" or long_short(inp):
        text += "; it aims to make money whichever way markets move, so it isn't compared with one"
    else:
        text += f"; {MARKET_TEXT.get(inp.market, MARKET_TEXT['n/a'])}"
    return item("result", "result", text, source=_src_saved(inp), as_of=as_of(inp),
                caveat=caveat, dir=direction(chg))


def attention(inp) -> list[dict]:
    out: list[dict] = []
    if inp.book.get("halted"):
        left = inp.state.get("halt_cooldown")
        tail = f": {left} trading days left" if left else ""
        out.append(item("halted", "attention", f"Paused by its safety breaker{tail}",
                        source=_src_saved(inp), level="act"))
    for f in inp.findings:
        if f.level == "ERROR":
            out.append(item("audit-error", "attention", plain_finding(f),
                            source=_src_audit(inp), level="act"))
    if inp.book.get("near_breaker"):
        out.append(item("near-breaker", "attention",
                        f"{pct(inp.card['off_peak'])} below its best; the safety breaker "
                        f"pauses it at {pct(-inp.card['breaker'], 0)}",
                        source=_src_saved(inp), level="look"))
    fresh = inp.book.get("freshness")
    if fresh in ("late", "stale"):
        d = as_of(inp)
        out.append(item("stale", "attention",
                        f"No new value since {day(d)}; the book is behind its schedule ({fresh})"
                        if d else "No saved values yet", source="saved book", level="look"))
    if inp.card.get("breaker") is None and max_gross(inp) > 1.0:
        out.append(item("no-breaker", "attention",
                        f"No safety breaker on this book, though it is geared up to "
                        f"{max_gross(inp):.0f}× its capital", source="the book's profile",
                        level="look"))
    return out


def closed_stats(inp):
    pos = [p for p in inp.positions_closed if p.get("net") is not None]
    won = sum(p["net"] for p in pos if p["net"] > 0)
    lost = -sum(p["net"] for p in pos if p["net"] < 0)
    wins = sum(1 for p in pos if p["net"] > 0)
    dates = len({str(p["closed"])[:10] for p in pos})
    return pos, won, lost, wins, dates


def _sample_caveat(n: int, dates: int, excluded: int) -> str | None:
    parts = []
    if n < SMALL_POSITIONS or dates < SMALL_DATES:
        parts.append(f"Small sample: {n} positions closed on {dates} dates.")
    if excluded:
        parts.append(f"Leaves out {excluded} positions closed by a since-fixed bug "
                     "(see the book's corrections).")
    return " ".join(parts) or None


def going_right(inp) -> list[dict]:
    out: list[dict] = []
    m = market(inp)
    if m and m["beyond"] and m["gap"] > 0:
        out.append(item("ahead-of-market", "right",
                        f"Ahead of the market by {points(m['gap'])} since {day(inp.card['since'])}, "
                        "more than its normal ups and downs", source=_src_saved(inp),
                        caveat=DIVIDENDS))
    pos, won, lost, wins, dates = closed_stats(inp)
    if pos and won - lost > 0:
        pf = f" (profit factor {won / lost:.1f})" if lost else ""
        out.append(item("winners-pay", "right",
                        f"Closed positions made more than they lost: {money(won)} won against "
                        f"{money(lost)} lost{pf}, over {len(pos)} positions",
                        source=f"trade record, {day(inp.now)}",
                        caveat=_sample_caveat(len(pos), dates, inp.excluded_forced)))
    return out
```

Notes for the implementer:
- `pct_change()` here is a one-period simple return. It is not annualised and not a drawdown, so the single-source guard allows it. Tracking error and the expected gap come from `metrics`.
- `make_inp`'s `card["since"]` is `"2026-06-11"`.

- [ ] **Step 4: Run to see them pass**

Run: `python3 -m pytest tests/test_book_rules_attention.py tests/test_metrics_single_source.py`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/book_rules.py tests/test_book_rules_attention.py
git commit -m "feat(dashboard): attention, result, market and going-right rules"
```

---

### Task 6: Recent frozen-price count in the audit (`verify.py`)

**Files:**
- Modify: `trading_algo/verify.py`: `check_dead_price` gains `today=None` and `detail["recent"]`; `verify_book` passes `today`
- Test: `tests/test_verify.py` (append)

**Interfaces:**
- Produces: the `dead-price-turnover` finding's `detail["recent"]`, an int: frozen-price changes within `HISTORICAL_CUTOFF_DAYS` of `today`. The level, code, message and `by_symbol` are unchanged.

- [ ] **Step 1: Write the failing test** (append to `tests/test_verify.py`; it already imports `verify` and `datetime`)

```python
def test_dead_price_counts_recent_changes_without_changing_the_finding():
    trades = [
        {"date": "2026-08-01", "pair": "EURUSD", "price": 1.1, "delta_weight": 0.1},
        {"date": "2026-08-02", "pair": "EURUSD", "price": 1.1, "delta_weight": 0.1},   # old
        {"date": "2026-10-10", "pair": "EURUSD", "price": 1.2, "delta_weight": 0.1},
        {"date": "2026-10-11", "pair": "EURUSD", "price": 1.2, "delta_weight": -0.1},  # recent
    ]
    found = verify.check_dead_price("t", {"trades": trades}, datetime(2026, 10, 13))
    assert len(found) == 1
    f = found[0]
    assert f.level == verify.WARN and f.code == "dead-price-turnover"
    assert f.detail["by_symbol"] == {"EURUSD": 2} and f.detail["recent"] == 1
    assert f.message.startswith("2 position changes")
```

- [ ] **Step 2: Run to see it fail**

Run: `python3 -m pytest tests/test_verify.py -k recent_changes`
Expected: FAIL with `TypeError: check_dead_price() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Implement**

Change the signature and the loop of `check_dead_price`. Use `timedelta` from the module's existing `datetime` import; add `timedelta` to that import if it isn't there.

```python
def check_dead_price(account: str, state: dict, today: datetime | None = None) -> list[Finding]:
    """Flag position changes made at a price identical to the previous change.

    `fx_data._align` forward-fills a symbol that did not print, so a frozen feed
    keeps serving its last close. If the book keeps re-weighting a symbol while
    its price never moves, it is paying spread on every turn for an exposure
    change that cannot possibly capture a price move. `detail["recent"]` counts
    the ones inside HISTORICAL_CUTOFF_DAYS of `today`, so a reader can tell a
    live defect from history without the finding itself changing.
    """
    cutoff = today - timedelta(days=HISTORICAL_CUTOFF_DAYS) if today is not None else None
    last_px: dict[str, float] = {}
    frozen: dict[str, int] = {}
    recent = 0
    for t in sorted(state.get("trades") or [], key=lambda x: _parse(x["date"])):
        sym, px = t.get("pair"), float(t.get("price", 0.0))
        if sym in last_px and px == last_px[sym] and abs(float(
                t.get("delta_weight", 0.0))) > 1e-9:
            frozen[sym] = frozen.get(sym, 0) + 1
            if cutoff is None or _parse(t["date"]) >= cutoff:
                recent += 1
        last_px[sym] = px
    if not frozen:
        return []
    total = sum(frozen.values())
    return [Finding(
        WARN, account, "dead-price-turnover",
        f"{total} position changes made at an unchanged (forward-filled) price "
        f"across {len(frozen)} symbols — spread paid, no price exposure gained",
        {"by_symbol": dict(sorted(frozen.items(), key=lambda x: -x[1])),
         "recent": recent})]
```

In `verify_book`, change `found += check_dead_price(account, state)` to `found += check_dead_price(account, state, today)`.

(`_parse` returns a naive UTC datetime, and `verify_book`'s `today` is naive UTC, so they compare directly.)

- [ ] **Step 4: Run the audit tests**

Run: `python3 -m pytest tests/test_verify.py`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/verify.py tests/test_verify.py
git commit -m "feat(verify): dead-price finding says how many were in the last 30 days"
```

---

### Task 7: Rules II: holding it back, and why each region is where it is

**Files:**
- Modify: `trading_algo/dashboard/book_rules.py` (append)
- Test: `tests/test_book_rules_back.py`

**Interfaces:**
- Consumes: Task 5 helpers; `detail["recent"]` (Task 6).
- Produces:
  - `costs_flip(inp)`, `holds_too_short(inp)`, `loses_more_often(inp)`, `behind_market(inp)`, `mostly_idle(inp)`, `frozen_prices(inp)`, each returning `list[dict]`;
  - `holding_back(inp) -> list[dict]`;
  - `invested_share(inp) -> float|None`;
  - `sleeve_reason(inp, key, sleeve) -> tuple[str, str, str|None]`, as `(code, text, level)`. The codes are `paused|data|fx|below-min|regime-off|no-eligible|insufficient|pinned|empty|invested`;
  - `REGIME_CAVEAT`, `SHORT_WHY`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.verify import WARN, Finding

EQ_COSTS = {"total": 449.17, "by_ccy": {"GBP": 396.16, "USD": 53.01},
            "stamp": {"GBP": 292.51}, "includes": "commission and stamp duty"}


def ids(items):
    return [i["id"] for i in items]


def test_costs_that_flip_a_gain_into_a_loss_name_the_uk():
    out = R.costs_flip(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    assert out[0]["text"].startswith("Costs (A$449.17) turned a A$401.55 gain into a A$47.62 loss")
    assert "mostly UK trading (A$396.16, of which A$292.51 stamp duty)" in out[0]["text"]
    assert out[0]["backlog"] == "F25" and "slippage" in out[0]["caveat"]


def test_small_costs_on_a_loss_say_nothing():
    small = dict(EQ_COSTS, total=45.84, by_ccy={"USD": 45.84}, stamp={})
    assert R.costs_flip(make_inp(card={"equity": 9_496.51, "initial": 10_000.0}, costs=small)) == []


def test_fx_costs_near_the_whole_loss_and_the_residual_note():
    fxb = {"net_pnl": -902.15, "residual": 277.19, "costs": 897.25}
    inp = make_inp("fx", card={"equity": 9_097.85, "initial": 10_000.0},
                   costs={"total": 897.25, "includes": "the spread paid on trades"}, fx_book=fxb)
    out = R.costs_flip(inp)
    assert "are most of the A$902.15 loss" in out[0]["text"]
    assert "unexplained" in out[0]["caveat"] and out[0]["backlog"] is None


def test_holds_too_short_states_units_and_carries_the_counter_evidence():
    f = Finding(WARN, "t", "hold-shorter-than-signal", "…",
                {"median_bars": 5.0, "horizon_bars": 57, "cut_short": 59, "round_trips": 77})
    out = R.holds_too_short(make_inp("fx", findings=[f], state={"bar": "1d"}))
    assert "5 days" in out[0]["text"] and "57 trading days (about 11 weeks)" in out[0]["text"]
    assert "59 of 77" in out[0]["text"] and out[0]["backlog"] == "F20"
    assert "not been shown to earn more" in out[0]["caveat"]
    hourly = R.holds_too_short(make_inp("fx", findings=[f], state={"bar": "60m"}))
    assert "57 bars" in hourly[0]["text"]


def _positions(wins, n):
    return [{"symbol": "A", "opened": "2026-07-01", "closed": f"2026-08-{(i % 28) + 1:02d}",
             "side": "LONG", "net": 1.0 if i < wins else -1.0, "lots": 1} for i in range(n)]


def test_loses_more_often_only_when_the_whole_range_is_below_half():
    assert ids(R.loses_more_often(make_inp(positions_closed=_positions(29, 77)))) == ["loses-more-often"]
    assert R.loses_more_often(make_inp(positions_closed=_positions(36, 77))) == []


def _sleeve(cash, positions=None, **extra):
    return {"currency": "AUD", "cash": cash, "positions": positions or {}, **extra}


def test_mostly_idle_splits_cash_by_cause_and_tracks_only_the_sized_down_part():
    state = {"allocations": {"ASX": 0.34, "US": 0.33, "FTSE": 0.33},
             "fx_snapshot": {"AUD": 1.0},
             "sleeves": {"ASX": _sleeve(33_000.0, last_flat_reason="regime-off",
                                        last_rebalance_month="2026-10",
                                        last_rebalance_date="2026-10-06"),
                         "US": _sleeve(25_000.0, {"AAPL": 10}),
                         "FTSE": _sleeve(14_000.0, {"BP.L": 100})}}
    out = R.mostly_idle(make_inp(state=state))
    assert out[0]["text"].startswith("Mostly idle: 72% of the book is in cash")
    assert "below its long-term trend, by design" in out[0]["text"]
    assert "sized down" in out[0]["text"] and out[0]["backlog"] == "F23"


def test_mostly_idle_never_fires_on_a_long_short_book():
    state = {"param_overrides": {"long_short": True}, "fx_snapshot": {"AUD": 1.0},
             "sleeves": {"US": _sleeve(110_000.0)}}
    assert R.mostly_idle(make_inp(state=state)) == []


def test_missing_rate_never_crashes_costs_or_idle():
    state = {"fx_snapshot": {}, "sleeves": {"US": {"currency": "USD", "cash": 5_000.0, "positions": {}}}}
    inp = make_inp(state=state, costs={"total": 0.0, "by_ccy": {}, "stamp": {}, "includes": "x"})
    assert R.costs_flip(inp) == []
    assert R.invested_share(inp) == pytest.approx(1.0)       # unpriced cash counts as 0 A$


def test_frozen_prices_needs_ten_recent_and_otherwise_shows_as_an_audit_warning():
    f10 = Finding(WARN, "t", "dead-price-turnover", "100 position changes …", {"recent": 10})
    f9 = Finding(WARN, "t", "dead-price-turnover", "3 position changes …", {"recent": 9})
    assert ids(R.holding_back(make_inp("fx", findings=[f10]))) == ["frozen-prices"]
    assert ids(R.holding_back(make_inp("fx", findings=[f9]))) == ["audit-warn"]


def test_a_covered_warning_is_shown_once_by_its_rule():
    state = {"fx_snapshot": {"AUD": 1.0},
             "sleeves": {"ASX": _sleeve(90_000.0, last_flat_reason="regime-off")}}
    f = Finding(WARN, "t", "idle-sleeve", "sleeve ASX …", {"subject": "ASX"})
    got = ids(R.holding_back(make_inp(state=state, findings=[f])))
    assert got.count("mostly-idle") == 1 and "audit-warn" not in got


@pytest.mark.parametrize("sleeve,state,code", [
    (_sleeve(1.0), {"risk_halted": True, "halt_cooldown": 3}, "paused"),
    (_sleeve(1.0, last_flat_reason="data-quality"), {}, "data"),
    (_sleeve(1.0, last_status={"status": "cash:fx-unavailable"}), {}, "fx"),
    (_sleeve(1.0, last_status={"status": "cash:below-min"}), {}, "below-min"),
    (_sleeve(1.0, last_flat_reason="regime-off", last_rebalance_month="2026-10"), {}, "regime-off"),
    (_sleeve(1.0, last_flat_reason="no-eligible-names", last_rebalance_month="2026-10"), {}, "no-eligible"),
    (_sleeve(1.0, last_flat_reason="insufficient-names"), {}, "insufficient"),
    (_sleeve(1.0, last_rebalance_month="2026-10"), {}, "pinned"),
    (_sleeve(1.0), {}, "empty"),
    (_sleeve(1.0, {"AAPL": 3}), {}, "invested"),
])
def test_sleeve_reason_precedence(sleeve, state, code):
    assert R.sleeve_reason(make_inp(state=state), "US", sleeve)[0] == code


def test_regime_off_says_since_the_book_began_when_the_region_never_traded():
    text = R.sleeve_reason(make_inp(), "ASX",
                           _sleeve(1.0, last_flat_reason="regime-off",
                                   last_rebalance_date="2026-10-06",
                                   last_rebalance_month="2026-10"))[1]
    assert "since the book began" in text and "6 Oct 2026" in text and "next month" in text
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_book_rules_back.py`
Expected: FAIL with `AttributeError: module 'trading_algo.dashboard.book_rules' has no attribute 'costs_flip'`

- [ ] **Step 3: Implement** (append to `book_rules.py`)

```python
REGIME_CAVEAT = ("Holding cash below the trend gives up some growth but limits the worst "
                 "falls (system review, section 15).")
SHORT_WHY = {
    "paused": "paused by the safety breaker",
    "data": "price data failed checks",
    "fx": "no exchange rate",
    "below-min": "too small to trade",
    "regime-off": "waiting: its market is below its long-term trend, by design",
    "no-eligible": "nothing passed the tests",
    "insufficient": "couldn't form both sides",
    "pinned": "waiting for next month's rebalance",
    "empty": "holding nothing",
    "invested": "sized down: volatility target and whole-share rounding",
}


def _base_per_local(inp, ccy: str | None) -> float:
    base = inp.state.get("base_currency", cfg.BASE_CURRENCY)
    return fx.base_per_local(inp.state.get("fx_snapshot") or {}, ccy, base) or 0.0


def _region_name(key: str) -> str:
    try:
        return get_region(key).name
    except Exception:          # a key the registry doesn't know (tests, a retired region)
        return key


def invested_share(inp) -> float | None:
    if inp.kind != "equity":
        return None
    eq = float(inp.card.get("equity") or 0.0)
    if eq <= 0:
        return None
    cash = sum(float(s.get("cash") or 0.0) * _base_per_local(inp, s.get("currency"))
               for s in (inp.state.get("sleeves") or {}).values())
    return 1.0 - cash / eq


def _last_fill(inp, key: str) -> str | None:
    dates = [str(t["date"]) for t in inp.state.get("trades") or []
             if t.get("region") == key and pnl.is_fill(t)]
    return max(dates) if dates else None


def sleeve_reason(inp, key: str, sleeve: dict) -> tuple[str, str, str | None]:
    status = (sleeve.get("last_status") or {}).get("status") or ""
    reason = sleeve.get("last_flat_reason")
    holds = bool(sleeve.get("positions"))
    decided_this_month = sleeve.get("last_rebalance_month") == inp.now.strftime("%Y-%m")
    nxt = " Next decision: the start of next month." if (not holds and decided_this_month) else ""
    if inp.state.get("risk_halted"):
        left = inp.state.get("halt_cooldown")
        return "paused", f"Paused by the safety breaker{f': {left} trading days left' if left else ''}.", "act"
    if status == "cash:stale-data" or reason == "data-quality":
        return "data", "Kept in cash: its price data failed the quality checks." + nxt, "act"
    if status == "cash:fx-unavailable":
        return "fx", "Kept in cash: no exchange rate was available." + nxt, "act"
    if status == "cash:below-min":
        return "below-min", "Too small to trade yet: below the minimum size the strategy can hold." + nxt, None
    if not holds and reason == "regime-off":
        last = _last_fill(inp, key)
        since = "since the book began" if last is None else f"since {day(last)}"
        when = sleeve.get("last_rebalance_date")
        at = f"at the last decision ({day(when)}) " if when else ""
        return ("regime-off", f"Waiting in cash {since}: {at}its market was below its "
                f"long-term trend.{nxt}", None)
    if not holds and reason == "no-eligible-names":
        return "no-eligible", "Holding nothing: no share passed the momentum and trend tests at the last decision." + nxt, None
    if not holds and reason == "insufficient-names":
        return "insufficient", "Holding nothing: it couldn't form both the long and the short side." + nxt, None
    if not holds and decided_this_month:
        return "pinned", "Holding nothing until next month's rebalance.", None
    if not holds:
        return "empty", "Holding nothing.", None
    n = len(sleeve["positions"])
    return ("invested", f"Investing in {n} holding{'s' if n != 1 else ''}; part of its money "
            "stays in cash because positions are sized to a volatility target and whole "
            "shares round down.", None)


def costs_flip(inp) -> list[dict]:
    costs = float((inp.costs or {}).get("total") or 0.0)
    if costs <= 0:
        return []
    if inp.kind == "fx" and inp.fx_book:
        res = float(inp.fx_book["net_pnl"])
    else:
        res = float(inp.card["equity"]) - float(inp.card["initial"])
    before = res + costs
    if not ((before > 0 > res) or costs > COSTS_HALF * abs(before)):
        return []
    if before > 0 > res:
        text = f"Costs ({money(costs)}) turned a {money(before)} gain into a {money(-res)} loss"
    elif res >= 0:
        text = f"Costs ({money(costs)}) took more than half of the {money(before)} made before costs"
    else:
        text = (f"Costs ({money(costs)}) are most of the {money(-res)} loss; before costs "
                f"the book was {money(before, signed=True)}")
    backlog = None
    if inp.kind == "equity":
        caveat = f"Costs here are {inp.costs['includes']}; slippage is already inside the fill prices."
        gbp = float((inp.costs.get("by_ccy") or {}).get("GBP") or 0.0)
        if gbp > FTSE_SHARE * costs:
            text += (f", mostly UK trading ({money(gbp)}, of which "
                     f"{money(float((inp.costs.get('stamp') or {}).get('GBP') or 0.0))} stamp duty)")
            backlog = "F25"
    else:
        caveat = f"Costs here are {inp.costs['includes']}; financing is counted separately."
        resid = float((inp.fx_book or {}).get("residual") or 0.0)
        if res and abs(resid) > RESIDUAL_SHARE * abs(res):
            caveat += (f" The trade-by-trade figures leave {money(resid, signed=True)} of the "
                       "result unexplained (compounding and currency translation).")
    return [item("costs-flip", "wrong", text, source=f"trade record, {day(inp.now)}",
                 caveat=caveat, backlog=backlog)]


def holds_too_short(inp) -> list[dict]:
    f = next((f for f in inp.findings if f.code == "hold-shorter-than-signal"), None)
    if f is None:
        return []
    d = f.detail or {}
    tail = f"; {d['cut_short']} of {d['round_trips']} positions closed before a quarter of it"
    if str(inp.state.get("bar") or "1d") == "1d":
        text = (f"Sells early: half its positions close within {d['median_bars']:.0f} days, "
                f"but its signal horizon is {d['horizon_bars']} trading days "
                f"(about {d['horizon_bars'] / 5:.0f} weeks){tail}")
    else:
        text = (f"Sells early: half its positions close within {d['median_bars']:.0f} bars, "
                f"but its signal horizon is {d['horizon_bars']} bars{tail}")
    return [item("holds-too-short", "wrong", text, source=_src_audit(inp),
                 caveat="Holding longer has not been shown to earn more (tracked as fix F20).",
                 backlog="F20")]


def loses_more_often(inp) -> list[dict]:
    pos, _, _, wins, _ = closed_stats(inp)
    if not pos:
        return []
    lo, hi = metrics.wilson_interval(wins, len(pos))
    if hi >= 0.5:
        return []
    caveat = "Counts whole positions after costs."
    if inp.excluded_forced:
        caveat += f" Leaves out {inp.excluded_forced} closed by a since-fixed bug."
    return [item("loses-more-often", "wrong",
                 f"Loses more often than it wins: {wins} of {len(pos)} closed positions made "
                 f"money ({pct(wins / len(pos), 0, signed=False)}); the likely range is "
                 f"{pct(lo, 0, signed=False)} to {pct(hi, 0, signed=False)}",
                 source=f"trade record, {day(inp.now)}", caveat=caveat)]


def behind_market(inp) -> list[dict]:
    m = market(inp)
    if not m or not m["beyond"] or m["gap"] >= 0:
        return []
    return [item("behind-market", "wrong",
                 f"Behind the market by {points(m['gap'])} since {day(inp.card['since'])}, "
                 "more than its normal ups and downs", source=_src_saved(inp), caveat=DIVIDENDS)]


def mostly_idle(inp) -> list[dict]:
    if inp.kind != "equity" or long_short(inp):
        return []
    share = invested_share(inp)
    if share is None or share >= IDLE_BELOW:
        return []
    eq = float(inp.card["equity"])
    parts, sized_down = [], False
    for key, sleeve in (inp.state.get("sleeves") or {}).items():
        cash_share = float(sleeve.get("cash") or 0.0) * _base_per_local(inp, sleeve.get("currency")) / eq
        if cash_share < 0.01:
            continue
        code = sleeve_reason(inp, key, sleeve)[0]
        sized_down = sized_down or code == "invested"
        parts.append(f"{_region_name(key)} {pct(cash_share, 0, signed=False)} ({SHORT_WHY[code]})")
    text = f"Mostly idle: {pct(1 - share, 0, signed=False)} of the book is in cash; " + "; ".join(parts)
    return [item("mostly-idle", "wrong", text, source=_src_saved(inp),
                 backlog="F23" if sized_down else None,
                 caveat=("Only the sized-down part is tracked as a fix (F23 volatility target, "
                         "F24 whole-share rounding).") if sized_down else None)]


def frozen_prices(inp) -> list[dict]:
    f = next((f for f in inp.findings if f.code == "dead-price-turnover"), None)
    recent = int(((f.detail or {}).get("recent") or 0)) if f else 0
    if recent < FROZEN_RECENT:
        return []
    return [item("frozen-prices", "wrong",
                 f"{recent} position changes in the last 30 days were made at a frozen price, "
                 "paying the spread for nothing", source=_src_audit(inp), backlog="F21")]


def holding_back(inp) -> list[dict]:
    out: list[dict] = []
    for rule in (costs_flip, holds_too_short, loses_more_often, behind_market,
                 mostly_idle, frozen_prices):
        out.extend(rule(inp))
    fired = {i["id"] for i in out}
    for f in inp.findings:
        if f.level == "WARN" and COVERED_BY.get(f.code) not in fired:
            out.append(item("audit-warn", "wrong", plain_finding(f), source=_src_audit(inp)))
    return out
```

Note: `_region_name` gives "Australia (ASX)", "United States" or "United Kingdom (LSE)", and falls back to the key itself for a region the registry doesn't know.

- [ ] **Step 4: Run to see them pass**

Run: `python3 -m pytest tests/test_book_rules_back.py tests/test_book_rules_attention.py`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/book_rules.py tests/test_book_rules_back.py
git commit -m "feat(dashboard): holding-it-back rules and each region's reason"
```

---

### Task 8: Rules III: is the model any good?

**Files:**
- Modify: `trading_algo/dashboard/book_rules.py` (append)
- Test: `tests/test_book_rules_model.py`

**Interfaces:**
- Produces: `backtest_line(inp)`, `luck_test(inp)`, `not_measured(inp)` and `model(inp)`, each returning `list[dict]`; plus `FX_ARTEFACTS: str`, a slice of `fx_config.REVIEW_NOTICE`.

- [ ] **Step 1: Write the failing tests**

```python
from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.forex import fx_config

EQ_BT = {"available": True, "synthetic": False, "start": "2012-01-01", "end": "2026-09-18",
         "generated_at": "2026-09-19T08:08:26Z", "allocations": {"ASX": 0.3333, "US": 0.3333, "FTSE": 0.3333},
         "metrics": {"cagr": 0.0609, "sharpe": 0.32},
         "benchmark_metrics": {"cagr": 0.0958, "sharpe": 0.5}}
THIRDS = {"allocations": {"ASX": 1 / 3, "US": 1 / 3, "FTSE": 1 / 3}}


def test_equity_backtest_line_and_its_caveats():
    out = R.backtest_line(make_inp(backtest=EQ_BT, state=THIRDS))[0]
    assert out["text"] == ("Backtest (2012–2026): +6.1% a year, Sharpe 0.32, against the "
                           "benchmark's +9.6% and 0.50")
    c = out["caveat"]
    assert c.startswith("Saved 19 Sep 2026, 24 days ago; may not reflect changes since.")
    assert "survivorship-biased" in c and "dividends" in c
    assert "not this book's settings" not in c                 # FULL's thirds match


def test_a_book_with_other_settings_says_so():
    small = make_inp(backtest=EQ_BT, state={"allocations": {"US": 1.0}},
                     entry={"key": "SMALL", "account": "small", "kind": "equity", "micro": True})
    assert "not this book's settings" in R.backtest_line(small)[0]["caveat"]


def test_missing_and_synthetic_backtests_are_never_performance():
    assert "No saved backtest" in R.backtest_line(make_inp())[0]["text"]
    syn = R.backtest_line(make_inp(backtest=dict(EQ_BT, synthetic=True)))[0]
    assert "made-up test data" in syn["text"] and "%" not in syn["text"]


def test_fx_backtest_carries_the_review_artefact_sentence_only():
    bt = {"available": True, "synthetic": False, "start": "2003-12-02", "end": "2026-10-09",
          "generated_at": "2026-10-13T03:30:08Z", "metrics": {"cagr": -0.0947, "sharpe": -2.14}}
    out = R.backtest_line(make_inp("fx", backtest=bt))[0]
    assert R.FX_ARTEFACTS in fx_config.REVIEW_NOTICE
    assert R.FX_ARTEFACTS in out["caveat"] and "−2.06" not in out["caveat"]


PERM = {"p_value": 0.472636815920398, "n_permutations": 200, "created": "2026-09-19T21:26:21",
        "window": {"start": "2020-04-10", "end": "2026-09-19"}}


def test_luck_test_describes_the_search_not_the_book():
    out = R.luck_test(make_inp("fx", permtest=PERM, roster=0))[0]
    assert "about 1 in 2 times" in out["text"] and "p-value 0.47" in out["text"]
    assert "look like luck" in out["text"]
    assert "none of the search's finds" in out["caveat"] and "200 bar shuffles" in out["caveat"]
    assert out["backlog"] == "F22"


def test_a_small_p_reads_rarely_never_zero_in_ten():
    out = R.luck_test(make_inp("fx", permtest=dict(PERM, p_value=0.04), roster=2))[0]
    assert "rarely" in out["text"] and "0 in" not in out["text"]
    assert "unlikely to be luck alone" in out["text"]


def test_an_old_luck_test_says_so_and_equity_has_none():
    old = R.luck_test(make_inp("fx", permtest=dict(PERM, created="2026-08-01T00:00:00"), roster=0))[0]
    assert "Over 30 days old" in old["caveat"]
    assert R.luck_test(make_inp(permtest=PERM)) == []


def test_not_measured_names_what_is_missing():
    assert "decision record" in R.not_measured(make_inp())[0]["text"]
    assert "agent scorecard" in R.not_measured(make_inp("fx"))[0]["text"]
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_book_rules_model.py`
Expected: FAIL with `AttributeError: ... 'backtest_line'`

- [ ] **Step 3: Implement** (append to `book_rules.py`)

```python
_N = fx_config.REVIEW_NOTICE
FX_ARTEFACTS = _N[_N.index("Two known"):_N.index("near zero.") + len("near zero.")]


def _days_old(stamp, now: datetime) -> int | None:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).days


def _settings_differ(inp, bt: dict) -> bool:
    mine = {k: round(float(v), 2) for k, v in (inp.state.get("allocations") or {}).items()}
    theirs = {k: round(float(v), 2) for k, v in (bt.get("allocations") or {}).items()}
    return mine != theirs or bool(inp.state.get("param_overrides")) or bool(inp.entry.get("micro"))


def backtest_line(inp) -> list[dict]:
    bt = inp.backtest or {}
    if not bt.get("available"):
        return [item("backtest", "model", "No saved backtest for this book yet",
                     source="backtest store")]
    gen = bt.get("generated_at")
    src = f"saved backtest, {day(gen)}" if gen else "saved backtest"
    if bt.get("synthetic"):
        return [item("backtest", "model", "The saved backtest ran on made-up test data, so it "
                     "says nothing about real performance", source=src)]
    m = bt.get("metrics") or {}
    if m.get("cagr") is None or m.get("sharpe") is None:
        return [item("backtest", "model", "Couldn't read the saved backtest", source=src)]
    text = (f"Backtest ({str(bt.get('start'))[:4]}–{str(bt.get('end'))[:4]}): "
            f"{pct(m['cagr'])} a year, Sharpe {m['sharpe']:.2f}")
    caveats: list[str] = []
    age = _days_old(gen, inp.now) if gen else None
    if gen:
        caveats.append(f"Saved {day(gen)}" + (f", {age} days ago; may not reflect changes since."
                                              if age is not None and age > BACKTEST_STALE_DAYS else "."))
    if inp.kind == "equity":
        bm = bt.get("benchmark_metrics") or {}
        if bm.get("cagr") is not None and bm.get("sharpe") is not None:
            text += f", against the benchmark's {pct(bm['cagr'])} and {bm['sharpe']:.2f}"
        caveats.append("It is survivorship-biased, so an upper bound; no sleeve passes a "
                       "standalone Deflated-Sharpe test.")
        caveats.append("The benchmark leaves out dividends, which flatters the strategy.")
        if _settings_differ(inp, bt):
            caveats.append("This is the standard strategy, not this book's settings.")
    else:
        caveats.append(FX_ARTEFACTS)
    return [item("backtest", "model", text, source=src, as_of=str(gen)[:10] if gen else None,
                 caveat=" ".join(caveats))]


def luck_test(inp) -> list[dict]:
    r = inp.permtest
    if inp.kind != "fx" or not r or r.get("p_value") is None:
        return []
    p = float(r["p_value"])
    how = f"about 1 in {max(1, round(1 / p))} times" if p >= 0.1 else "rarely"
    verdict = "so its best finds look like luck" if p >= 0.05 else "so its best finds are unlikely to be luck alone"
    text = (f"A permutation test of the search for better agent settings: on shuffled, "
            f"patternless prices it finds results this good {how} (p-value {p:.2f}), {verdict}")
    used = "none" if inp.roster == 0 else (str(inp.roster) if inp.roster is not None else "an unknown number")
    w = r.get("window") or {}
    span = f" of {day(w['start'])}–{day(w['end'])} data" if w.get("start") and w.get("end") else ""
    caveat = (f"This tests the settings search, not this book's trades: the book uses {used} "
              f"of the search's finds. Null: {r.get('n_permutations')} bar shuffles{span}; "
              f"run {day(r['created'])}.")
    age = _days_old(r.get("created"), inp.now)
    if age is not None and age > LUCK_STALE_DAYS:
        caveat += f" Over {LUCK_STALE_DAYS} days old."
    return [item("luck-test", "model", text, source=f"permutation report, {day(r['created'])}",
                 as_of=str(r["created"])[:10], caveat=caveat, backlog="F22")]


def not_measured(inp) -> list[dict]:
    text = ("Does the ranking pick winners? Not measured yet; it needs the decision record"
            if inp.kind == "equity" else
            "Which agents are right? Not measured yet; it needs the agent scorecard")
    return [item("not-measured", "model", text, source="not yet built")]


def model(inp) -> list[dict]:
    return backtest_line(inp) + luck_test(inp) + not_measured(inp)
```

- [ ] **Step 4: Run to see them pass**

Run: `python3 -m pytest tests/test_book_rules_model.py`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/book_rules.py tests/test_book_rules_model.py
git commit -m "feat(dashboard): model-quality rules — backtest caveats and the luck test, described honestly"
```

---

### Task 9: The Overview answer (`book_summary.py`)

**Files:**
- Create: `trading_algo/dashboard/book_summary.py`
- Test: `tests/test_book_summary.py`, `tests/test_book_summary_golden.py`, `tests/fixtures/book_summaries_20261013.json` (generated and reviewed)

**Interfaces:**
- Consumes: everything in `book_rules` (Tasks 5, 7, 8), `metrics` (Task 1), `glossary` and `textfmt` (Task 2), and `load_inputs` (Task 4).
- Produces: `status(inp) -> dict` and `summarise(inp) -> dict`. The payload keys are:
  - `v, key, name, kind, as_of, status{level,shape,word}`;
  - `result` (an item with `dir`), `main_problem`, `attention[]`, `summary[{text,item_id}]`;
  - `tiles[{label,value,dir,caption}]`, `panel{right[],wrong[],model[]}`, `right_empty`;
  - `chart{value,below_best,benchmark,value_desc,below_desc}`, `months[{month,label,text,dir}]`;
  - `closed[{date,symbol,held,net,dir}]`, `market`, `errors[]`.

  Every item has a unique `uid`.

- [ ] **Step 1: Write the failing unit tests** (`tests/test_book_summary.py`)

```python
import json
from datetime import datetime, timezone

import pytest

from book_fixtures import NOW, make_inp, no_network, use_snapshot
from trading_algo import metrics
from trading_algo.dashboard import book_inputs, book_rules as R, book_summary as S, glossary, registry
from trading_algo.verify import WARN, Finding

EQ_COSTS = {"total": 449.17, "by_ccy": {"GBP": 396.16}, "stamp": {"GBP": 292.51},
            "includes": "commission and stamp duty"}


def test_halted_outranks_everything_and_leads_the_summary():
    s = S.summarise(make_inp(card={"halted": True, "equity": 99_952.38}, costs=EQ_COSTS))
    assert s["main_problem"]["id"] == "halted" and s["status"]["level"] == "act"
    assert s["summary"][1]["text"].startswith("Main problem: Paused by its safety breaker")


def test_summary_order_and_never_more_good_news_than_problems():
    good = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG",
             "net": 50.0, "lots": 1}]
    with_problem = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS,
                                        positions_closed=good))
    kinds = [x["item_id"].split("#")[0] for x in with_problem["summary"]]
    assert kinds == ["result", "costs-flip", "winners-pay", "backtest"]
    no_problem = S.summarise(make_inp(positions_closed=good))
    kinds = [x["item_id"].split("#")[0] for x in no_problem["summary"]]
    assert kinds == ["result", "no-problem", "backtest"] and "winners-pay" not in kinds


def test_every_summary_sentence_maps_to_a_shown_item_and_carries_its_caveat():
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    shown = {s["result"]["uid"]: s["result"], s["main_problem"]["uid"]: s["main_problem"]}
    for col in s["panel"].values():
        shown.update({i["uid"]: i for i in col})
    for sent in s["summary"]:
        assert sent["item_id"] in shown
        cav = shown[sent["item_id"]].get("caveat")
        if cav:
            assert cav.split(". ")[0].rstrip(".") in sent["text"]


def test_uids_are_unique_when_a_rule_fires_twice():
    fs = [Finding(WARN, "t", "cash-drift", "…", {"subject": "US"}),
          Finding(WARN, "t", "cash-drift", "…", {"subject": "FTSE"})]
    s = S.summarise(make_inp(findings=fs))
    uids = [i["uid"] for i in s["panel"]["wrong"]]
    assert len(uids) == len(set(uids)) == 2


def test_a_rule_that_throws_becomes_one_couldnt_measure_line(monkeypatch):
    monkeypatch.setattr(R, "going_right", lambda inp: 1 / 0)
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    assert [i["id"] for i in s["panel"]["right"]] == ["couldnt-measure"]
    assert s["main_problem"]["id"] == "costs-flip" and "going right" in s["errors"][0]


def test_glossary_explains_each_term_once_on_the_screen():
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS,
                             backtest={"available": True, "synthetic": False, "start": "2012",
                                       "end": "2026", "generated_at": "2026-10-10T00:00:00Z",
                                       "metrics": {"cagr": 0.06, "sharpe": 0.3}}))
    blob = json.dumps(s, ensure_ascii=False)
    for term, _, g in glossary.TERMS:
        assert blob.count(f"({g})") <= 1, term
    assert blob.count("(a replay of the strategy on past prices, costs included)") == 1


def test_six_tiles_and_fx_without_closed_positions():
    assert len(S.summarise(make_inp())["tiles"]) == 6
    fx = S.summarise(make_inp("fx", fx_book={"net_pnl": 0.0, "residual": 0.0, "costs": 0.0,
                                             "turnover": 0.0}))
    win = next(t for t in fx["tiles"] if t["label"] == "Win rate")
    assert win["value"] == "—" and "No closed positions yet" in win["caption"]


def test_one_valuation_point_across_result_tiles_and_chart():
    s = S.summarise(make_inp(card={"equity": 99_952.38}))
    since = next(t for t in s["tiles"] if t["label"] == "Since start")
    assert since["value"] == "▼ −A$47.62" and "A$47.62" in s["result"]["text"]
    assert s["chart"]["value"][-1][1] == pytest.approx(-47.62)


def test_tile_below_best_is_the_breaker_figure_and_months_come_from_metrics(monkeypatch):
    calls = {"m": 0, "d": 0}
    real_m, real_d = metrics.monthly_returns, metrics.drawdown
    monkeypatch.setattr(metrics, "monthly_returns", lambda e: (calls.__setitem__("m", calls["m"] + 1), real_m(e))[1])
    monkeypatch.setattr(metrics, "drawdown", lambda e: (calls.__setitem__("d", calls["d"] + 1), real_d(e))[1])
    s = S.summarise(make_inp(card={"off_peak": -0.0112}))
    tile = next(t for t in s["tiles"] if t["label"] == "Below its best")
    assert tile["value"] == "−1.1%" and calls == {"m": 1, "d": 1}


def test_summary_survives_an_unvaluable_mark(tmp_path, monkeypatch):
    dst = use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    p = dst / "paper_state_full.json"
    state = json.loads(p.read_text())
    state["equity_history"].insert(-1, ["2026-10-10", None])
    p.write_text(json.dumps(state))
    s = S.summarise(book_inputs.load_inputs(registry.resolve("FULL"), NOW))
    json.dumps(s, allow_nan=False)                      # valid JSON, no NaN
    assert all(v is not None for _, v in s["chart"]["value"])
```

- [ ] **Step 2: Write the failing golden and facts test** (`tests/test_book_summary_golden.py`)

```python
"""All eight real books on the frozen 13 Oct snapshot: the facts the review
established, plus every word pinned (AC-6). Regenerate the golden only on
purpose: UPDATE_GOLDEN=1 pytest tests/test_book_summary_golden.py — then read
the diff before committing."""
import json
import os
from pathlib import Path

import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import book_inputs, book_rules as R, book_summary as S, registry

GOLDEN = Path(__file__).parent / "fixtures" / "book_summaries_20261013.json"


@pytest.fixture()
def summaries(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    out = {}
    for e in registry.discover_accounts():
        market = "offline" if e["kind"] == "equity" else "n/a"
        out[e["key"]] = S.summarise(book_inputs.load_inputs(e, NOW, market=market))
    return out


def _ids(s, col):
    return [i["id"] for i in s["panel"][col]]


def test_the_facts_the_review_established(summaries):
    full = summaries["FULL"]
    assert full["status"]["level"] == "clear"
    assert full["main_problem"]["id"] == "costs-flip" and full["main_problem"]["backlog"] == "F25"
    assert "mostly-idle" in _ids(full, "wrong")
    assert summaries["ULTRA"]["main_problem"]["id"] == "no-breaker"
    exp = summaries["EXPERIMENTAL"]
    assert "mostly-idle" not in _ids(exp, "wrong") and "isn't compared" in exp["result"]["text"]
    small = summaries["SMALL"]
    assert small["main_problem"]["id"] == "mostly-idle"
    assert "not this book's settings" in next(i for i in small["panel"]["model"] if i["id"] == "backtest")["caveat"]
    for key in ("MATT", "PARTNER", "MULTI"):
        assert summaries[key]["main_problem"]["id"] == "holds-too-short", key
    matt_luck = next(i for i in summaries["MATT"]["panel"]["model"] if i["id"] == "luck-test")
    assert matt_luck["backlog"] == "F22" and "none of the search's finds" in matt_luck["caveat"]
    assert "luck-test" not in _ids(summaries["PARTNER"], "model")
    assert summaries["DAY"]["main_problem"]["id"] == "costs-flip"


def test_every_audit_warning_or_error_appears(summaries):
    # the `summaries` fixture has already pointed every reader at the snapshot
    for e in registry.discover_accounts():
        inp = book_inputs.load_inputs(e, NOW)
        s = summaries[e["key"]]
        shown = set(_ids(s, "wrong")) | {i["id"] for i in s["attention"]}
        for f in inp.findings:
            if f.level in ("WARN", "ERROR"):
                assert (R.COVERED_BY.get(f.code) in shown or "audit-warn" in shown
                        or "audit-error" in shown), (e["key"], f.code)


def test_backlog_ids_exist_and_are_open():
    backlog = json.loads((Path(__file__).parents[1] / "product/backlog/backlog.json").read_text())
    status = {i["id"]: i["status"] for i in backlog["items"]}
    if "F20" not in status:
        pytest.xfail("PR #102 (F20–F26) is not in this branch's base yet")
    for fid in ("F20", "F21", "F22", "F23", "F25"):
        assert status[fid] not in ("done", "rejected"), fid


def test_pinned_summaries(summaries):
    text = json.dumps(summaries, indent=1, sort_keys=True, ensure_ascii=False)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(text + "\n")
    assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1, read it, then commit"
    assert json.loads(GOLDEN.read_text()) == json.loads(text)
```

`test_backlog_ids_exist_and_are_open` uses `pytest.xfail` (strict in effect: the moment F20 lands, the assertions run). Once PR #102 is in the base, the xfail line is dead code and should be deleted.

- [ ] **Step 3: Run to see them fail**

Run: `python3 -m pytest tests/test_book_summary.py tests/test_book_summary_golden.py`
Expected: FAIL with `ImportError` (no `book_summary`)

- [ ] **Step 4: Implement `book_summary.py`**

```python
"""One book's Overview answer, built from BookInputs by fixed rules — no I/O.
Screen order (docs/specs/book-screens.md): status · result · main problem ·
summary · tiles · panel · chart · months · last closed positions."""
from __future__ import annotations

from datetime import datetime

from .. import metrics
from . import book_rules as R
from . import glossary
from .textfmt import day, direction, money, money_move, pct, points


def status(inp) -> dict:
    if inp.book.get("halted"):
        return {"level": "act", "shape": "■", "word": "Halted by its safety breaker"}
    if any(f.level == "ERROR" for f in inp.findings):
        return {"level": "act", "shape": "■", "word": "Audit problem"}
    if inp.book.get("near_breaker"):
        return {"level": "look", "shape": "▲", "word": "Near its safety breaker"}
    fresh = inp.book.get("freshness")
    if fresh != "fresh":
        return {"level": "look", "shape": "▲", "word": f"Data {fresh}"}
    return {"level": "clear", "shape": "●", "word": "Running normally"}


def _guard(inp, name: str, fn, column: str) -> list[dict]:
    try:
        return fn(inp)
    except Exception as exc:            # one rule's failure is one line, not a blank screen
        inp.errors[name] = repr(exc)
        return [R.item("couldnt-measure", column, f"Couldn't measure {name} right now",
                       source="—")]


def _sentence(it: dict, prefix: str = "") -> str:
    text = f"{prefix}{it['text']}."
    cav = it.get("caveat")
    if cav:
        first = cav.split(". ")[0].rstrip(".")
        text += f" ({first}.)"
    return text


def _tiles(inp, m) -> list[dict]:
    eq, init = float(inp.card["equity"]), float(inp.card["initial"])
    chg = eq - init
    d = R.as_of(inp)
    since = inp.card.get("since")
    breaker = inp.card.get("breaker")
    tiles = [
        {"label": "Worth", "value": money(eq), "dir": None,
         "caption": f"of {money(init)} put in" + (f", as of {day(d)}" if d else "")},
        {"label": "Since start", "value": money_move(chg), "dir": direction(chg),
         "caption": f"{pct(chg / init, 2)} since {day(since)}" if since and init else "—"},
    ]
    below = {"label": "Below its best", "value": pct(float(inp.card.get("off_peak") or 0.0)),
             "dir": "down" if (inp.card.get("off_peak") or 0) < 0 else None,
             "caption": (f"the safety breaker pauses it at {pct(-breaker, 0)}"
                         if breaker is not None else "no safety breaker on this book")}
    costs = float((inp.costs or {}).get("total") or 0.0)
    if inp.kind == "equity":
        if m is not None:
            vs = {"label": "vs the market",
                  "value": f"{'▲' if m['gap'] > 0 else '▼'} {points(m['gap'])} {'ahead' if m['gap'] > 0 else 'behind'}",
                  "dir": direction(m["gap"]),
                  "caption": (f"market {pct(m['bench_ret'])}; "
                              f"{'beyond' if m['beyond'] else 'within'} its normal ups and downs; "
                              "leaves out dividends")}
        elif R.long_short(inp):
            vs = {"label": "vs the market", "value": "—", "dir": None,
                  "caption": "not compared: it aims to make money whichever way markets move"}
        else:
            vs = {"label": "vs the market", "value": "—", "dir": None,
                  "caption": R.MARKET_TEXT.get(inp.market, R.MARKET_TEXT["n/a"])}
        share = R.invested_share(inp)
        if R.long_short(inp):
            inv = {"label": "Cash", "value": pct(1 - share, 0, signed=False) if share is not None else "—",
                   "dir": None, "caption": "includes the proceeds of short sales"}
        else:
            inv = {"label": "Invested", "value": pct(share, 0, signed=False) if share is not None else "—",
                   "dir": None, "caption": "the rest is in cash; Positions says why"}
        cost = {"label": "Costs so far", "value": money(costs), "dir": None,
                "caption": f"{(inp.costs or {}).get('includes', 'costs')}; slippage is inside the fill prices"}
        return tiles + [vs, below, cost, inv]
    pos, _, _, wins, _ = R.closed_stats(inp)
    if pos:
        lo, hi = metrics.wilson_interval(wins, len(pos))
        win = {"label": "Win rate", "value": pct(wins / len(pos), 0, signed=False), "dir": None,
               "caption": (f"{wins} of {len(pos)} closed positions; likely "
                           f"{pct(lo, 0, signed=False)} to {pct(hi, 0, signed=False)}")}
    else:
        win = {"label": "Win rate", "value": "—", "dir": None, "caption": "No closed positions yet"}
    turnover = float((inp.fx_book or {}).get("turnover") or 0.0)
    return tiles + [
        below, win,
        {"label": "Costs so far", "value": money(costs), "dir": None,
         "caption": "the spread paid on trades; financing is separate"},
        {"label": "Turnover", "value": f"{turnover:.0f}×", "dir": None,
         "caption": f"traded {turnover:.0f}× its capital since {day(since)}" if since else "—"},
    ]


def _chart(inp, m) -> dict:
    vals = R.daily_values(inp)
    init = float(inp.card["initial"])
    value = [[d.strftime("%Y-%m-%d"), round(float(v) - init, 2)] for d, v in vals.items()]
    dd = metrics.drawdown(vals) if len(vals) else vals
    below = [[d.strftime("%Y-%m-%d"), round(float(x), 6)] for d, x in dd.items()]
    bench = None
    if m is not None and inp.live:
        last = R.as_of(inp) or ""
        bench = [[r["date"], round(float(r["value"]) - init, 2)]
                 for r in inp.live.get("benchmark_curve") or []
                 if r.get("value") is not None and r["date"] <= last]
    value_desc = below_desc = "Not enough history yet."
    if len(value) >= 2:
        lo = min(value, key=lambda p: p[1])
        hi = max(value, key=lambda p: p[1])
        value_desc = (f"Profit and loss from {day(value[0][0])} to {day(value[-1][0])}: lowest "
                      f"{money_move(lo[1])} on {day(lo[0])}, highest {money_move(hi[1])} on "
                      f"{day(hi[0])}, now {money_move(value[-1][1])}.")
        deep = min(below, key=lambda p: p[1])
        below_desc = (f"Fall from the highest saved value: deepest {pct(deep[1])} on "
                      f"{day(deep[0])}; now {pct(below[-1][1])}.")
    return {"value": value, "below_best": below, "benchmark": bench,
            "value_desc": value_desc, "below_desc": below_desc}


def _months(inp) -> list[dict]:
    out = []
    for month, r in metrics.monthly_returns(R.daily_values(inp)).items():
        label = datetime.strptime(f"{month}-01", "%Y-%m-%d").strftime("%b %Y")
        out.append({"month": month, "label": label, "text": pct(float(r)),
                    "dir": direction(float(r))})
    return out


def _closed(inp) -> list[dict]:
    rows = sorted((p for p in inp.positions_closed if p.get("net") is not None),
                  key=lambda p: str(p["closed"]))[-5:]
    out = []
    for p in reversed(rows):
        sym = p["symbol"][1] if isinstance(p["symbol"], (tuple, list)) else p["symbol"]
        if inp.kind == "fx":
            sym = f"{sym} {p['side'].lower()}"
        held = (datetime.strptime(str(p["closed"])[:10], "%Y-%m-%d")
                - datetime.strptime(str(p["opened"])[:10], "%Y-%m-%d")).days
        out.append({"date": day(p["closed"]), "symbol": sym, "held": f"{held} days",
                    "net": money_move(p["net"]), "dir": direction(p["net"])})
    return out


def _number(items: list[dict]) -> None:
    seen: dict[str, int] = {}
    for it in items:
        n = seen.get(it["id"], 0)
        seen[it["id"]] = n + 1
        it["uid"] = f"{it['id']}#{n}"


def summarise(inp) -> dict:
    res = _guard(inp, "the result", lambda i: [R.result(i)], "result")[0]
    att = _guard(inp, "warnings", R.attention, "attention")
    right = _guard(inp, "what's going right", R.going_right, "right")
    back = _guard(inp, "what's holding it back", R.holding_back, "wrong")
    model = _guard(inp, "the model", R.model, "model")
    wrong = sorted(att + back, key=lambda i: (i["rank"] is None, i["rank"] or 0))
    _number([res] + wrong + right + model)
    ranked = [i for i in wrong if i["rank"] is not None]
    if ranked:
        main = dict(ranked[0])
    else:
        main = R.item("no-problem", "main", "No standout problem", source="the rules below")
        main["uid"] = "no-problem#0"
    summary = [{"text": _sentence(res), "item_id": res["uid"]},
               {"text": _sentence(main, "Main problem: " if ranked else ""), "item_id": main["uid"]}]
    if ranked and right:
        summary.append({"text": _sentence(right[0]), "item_id": right[0]["uid"]})
    mline = (next((i for i in model if i["id"] == "luck-test"), None)
             or next((i for i in model if i["id"] == "backtest"), None))
    if mline:
        summary.append({"text": _sentence(mline), "item_id": mline["uid"]})
    m = None
    try:
        m = R.market(inp)
    except Exception as exc:
        inp.errors["the market comparison"] = repr(exc)
    tiles = _tiles(inp, m)
    order = [(res, "text"), (main, "text")] + [(s, "text") for s in summary]
    order += [(t, "caption") for t in tiles]
    for it in right + wrong + model:
        order += [(it, "text"), (it, "caveat")]
    for (obj, key), text in zip(order, glossary.gloss([o[k] or "" for o, k in order])):
        if obj[key]:
            obj[key] = text
    return {
        "v": 1, "key": inp.entry["key"], "name": inp.card["name"], "kind": inp.kind,
        "as_of": R.as_of(inp), "status": status(inp), "result": res,
        "main_problem": main, "attention": [i for i in wrong if i["column"] == "attention"],
        "summary": summary, "tiles": tiles,
        "panel": {"right": right, "wrong": wrong, "model": model},
        "right_empty": None if right else "Nothing clearly going right yet.",
        "chart": _chart(inp, m), "months": _months(inp), "closed": _closed(inp),
        "market": inp.market, "errors": sorted(inp.errors),
    }
```

- [ ] **Step 5: Run the unit tests**

Run: `python3 -m pytest tests/test_book_summary.py`
Expected: PASS (all)

- [ ] **Step 6: Generate the golden, read it, then pin it**

```bash
UPDATE_GOLDEN=1 python3 -m pytest tests/test_book_summary_golden.py -k pinned
python3 - <<'EOF'
import json
g = json.load(open("tests/fixtures/book_summaries_20261013.json"))
for k, s in g.items():
    print(f"\n== {k}: {s['status']['word']} · main: {s['main_problem']['text'][:110]}")
    for x in s["summary"]:
        print("  ·", x["text"][:160])
EOF
```
Expected:
- Eight books are printed.
- FULL's main problem starts "Costs (A$".
- ULTRA's starts "No safety breaker".
- MATT, PARTNER and MULTI start "Sells early".
- DAY starts "Costs (".
- Every summary has 2–4 sentences.
- No text contains `nan`, `None` or a bare `-` before a number.

Read every sentence as Matt would. If one is false or unclear, fix the rule and its unit test, then regenerate.

- [ ] **Step 7: Run all summary tests**

Run: `python3 -m pytest tests/test_book_summary.py tests/test_book_summary_golden.py`
Expected: PASS. `test_backlog_ids_exist_and_are_open` is XFAIL until PR #102 is in the base.

- [ ] **Step 8: Commit**

```bash
git add trading_algo/dashboard/book_summary.py tests/test_book_summary.py \
        tests/test_book_summary_golden.py tests/fixtures/book_summaries_20261013.json
git commit -m "feat(dashboard): one book's Overview answer — ranked main problem, summary, tiles, panel"
```

---

### Task 10: The Positions answer (`book_positions.py`)

**Files:**
- Create: `trading_algo/dashboard/book_positions.py`
- Test: `tests/test_book_positions.py`

**Interfaces:**
- Consumes: `R.sleeve_reason`, `R.REGIME_CAVEAT`, `R.long_short`, `R.as_of`; `textfmt`; `glossary`.
- Produces: `positions_view(inp) -> dict`, with keys:
  - `v, key, name, kind, count, prices{status,text}, notes[], market`;
  - `groups[{key, name, currency, level, why, caveat, cash, rows[{symbol, side, qty, bought, now, worth, gain, gain_pct, dir, note}]}]`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone

from book_fixtures import NOW, make_inp, no_network, use_snapshot
from trading_algo.dashboard import book_inputs, book_positions as P, registry


def test_full_groups_regions_with_their_reasons(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    v = P.positions_view(book_inputs.load_inputs(registry.resolve("FULL"), NOW, market="offline"))
    names = [g["key"] for g in v["groups"]]
    assert set(names) == {"ASX", "US", "FTSE"}
    asx = next(g for g in v["groups"] if g["key"] == "ASX")
    assert "since the book began" in asx["why"] and asx["caveat"] and asx["rows"] == []
    us = next(g for g in v["groups"] if g["key"] == "US")
    assert us["rows"] and us["rows"][0]["bought"].startswith("Bought at US$")
    assert v["prices"]["status"] == "offline" and "at cost" in v["prices"]["text"]
    assert v["count"].endswith("holdings")


def _live(price=500.0, priced=True):
    return {"as_of": "2026-10-13", "sleeves": [{"key": "US", "positions": [
        {"ticker": "AAPL", "priced": priced, "price": price, "value_base": 7_000.0,
         "unrealized_base": 350.0, "unrealized_pct": 0.05}]}]}


def _equity_state(shares=10):
    return {"allocations": {"US": 1.0}, "fx_snapshot": {"USD": 1.43},
            "sleeves": {"US": {"currency": "USD", "cash": 100.0, "positions": {"AAPL": shares}}},
            "trades": [{"date": "2026-07-01", "region": "US", "ticker": "AAPL", "currency": "USD",
                        "side": "BUY" if shares > 0 else "SELL", "shares": abs(shares),
                        "fill": 476.19, "commission": 1.0}]}


def test_live_prices_fill_in_worth_and_gain():
    v = P.positions_view(make_inp(state=_equity_state(), live=_live(), market="ready"))
    row = v["groups"][0]["rows"][0]
    assert row["worth"] == "A$7,000.00" and row["gain"] == "▲ +A$350.00" and row["dir"] == "up"
    assert v["prices"]["text"] == "Prices as of 13 Oct 2026."


def test_a_short_reads_sold_at():
    v = P.positions_view(make_inp(state=_equity_state(-10)))
    row = v["groups"][0]["rows"][0]
    assert row["side"] == "SHORT" and row["bought"].startswith("Sold at") and "short" in row["qty"]


def test_an_unpriced_holding_says_held_at_cost():
    v = P.positions_view(make_inp(state=_equity_state(), live=_live(priced=False), market="ready"))
    assert v["groups"][0]["rows"][0]["note"] == "No price today; held at cost."


def test_long_short_cash_is_explained_and_paused_regions_come_first():
    state = _equity_state()
    state["param_overrides"] = {"long_short": True}
    state["allocations"] = {"US": 0.5, "FTSE": 0.5}
    state["sleeves"]["FTSE"] = {"currency": "GBP", "cash": 1.0, "positions": {},
                                "last_flat_reason": "data-quality"}
    v = P.positions_view(make_inp(state=state))
    assert v["groups"][0]["key"] == "FTSE" and v["groups"][0]["level"] == "act"
    assert any("short sales" in n for n in v["notes"])


def test_fx_positions_group_by_asset_class_and_say_when_the_market_is_shut():
    sat = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    fxb = {"open_rows": [
        {"pair": "EURUSD", "weight": 0.2, "avg_entry": 1.1, "price": 1.12, "priced": True,
         "notional": 1_000.0, "unrealized": 20.0, "entry_cost": 1.0},
        {"pair": "BTCUSD", "weight": -0.05, "avg_entry": 60_000.0, "price": 59_000.0,
         "priced": True, "notional": 250.0, "unrealized": 4.0, "entry_cost": 0.5}]}
    v = P.positions_view(make_inp("fx", fx_book=fxb, state={"bar": "1d"}, now=sat))
    by = {g["key"]: g for g in v["groups"]}
    assert set(by) == {"fx", "crypto"}
    assert "Market shut" in by["fx"]["why"] and "Market shut" not in by["crypto"]["why"]
    assert by["crypto"]["rows"][0]["side"] == "SHORT"
    assert by["fx"]["rows"][0]["gain"] == "▲ +A$19.00"            # after the entry spread
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest tests/test_book_positions.py`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
"""One book's Positions answer: what it holds, what each holding cost and is
worth, and — per region or asset group — whether it's investing or waiting and
why. Holdings and cost come from the saved book (instant, offline); today's
prices come only from a finished background fetch, and their age is shown."""
from __future__ import annotations

from datetime import timezone

from .. import pnl
from ..forex import sessions
from ..forex.pairs import get_pair
from ..regions import get_region
from . import book_rules as R
from . import glossary
from .textfmt import day, direction, money, money_move, pct

ASSET_GROUP = {"fx": "Currencies", "crypto": "Crypto", "equity": "Shares", "bond": "Bonds"}
_PRICE_TEXT = {"pending": "Fetching today's prices…", "idle": "Fetching today's prices…",
               "offline": "Valued at cost: today's prices aren't available offline.",
               "error": "Couldn't fetch today's prices; valued at cost.",
               "n/a": "Valued at cost."}


def _prices(inp) -> dict:
    if inp.kind == "fx":
        d = R.as_of(inp)
        return {"status": "saved", "text": f"Valued at the book's last saved prices ({day(d)})." if d
                else "Valued at the book's last saved prices."}
    if inp.market == "ready" and inp.live:
        return {"status": "ready", "text": f"Prices as of {day(inp.live.get('as_of'))}."}
    return {"status": inp.market, "text": _PRICE_TEXT.get(inp.market, _PRICE_TEXT["n/a"])}


def _equity_groups(inp) -> list[dict]:
    state = inp.state
    basis = pnl.open_basis(pnl.build_lots(state.get("trades") or [])[0])
    live = ({s["key"]: s for s in inp.live.get("sleeves") or []}
            if inp.market == "ready" and inp.live else {})
    groups = []
    for key in state.get("allocations") or (state.get("sleeves") or {}):
        sleeve = (state.get("sleeves") or {}).get(key) or {}
        ccy = sleeve.get("currency") or get_region(key).currency
        code, why, level = R.sleeve_reason(inp, key, sleeve)
        marks = {p["ticker"]: p for p in (live.get(key) or {}).get("positions") or []}
        rows = []
        for ticker, shares in (sleeve.get("positions") or {}).items():
            short = shares < 0
            avg = basis.get((key, ticker))
            row = {"symbol": ticker, "side": "SHORT" if short else "LONG",
                   "qty": f"{abs(int(shares))} shares{' short' if short else ''}",
                   "bought": (f"{'Sold' if short else 'Bought'} at {money(avg, ccy)}"
                              if avg else "Cost unknown"),
                   "now": None, "worth": None, "gain": None, "gain_pct": None,
                   "dir": "flat", "note": None}
            lp = marks.get(ticker)
            if lp and lp.get("priced"):
                row.update({"now": f"now {money(lp['price'], ccy)}",
                            "worth": money(lp["value_base"]),
                            "gain": money_move(lp["unrealized_base"]),
                            "gain_pct": pct(lp["unrealized_pct"]),
                            "dir": direction(lp["unrealized_base"])})
            elif lp:
                row.update({"now": "no price today", "note": "No price today; held at cost."})
            rows.append(row)
        groups.append({"key": key, "name": get_region(key).name, "currency": ccy,
                       "level": level, "why": why,
                       "caveat": R.REGIME_CAVEAT if code == "regime-off" else None,
                       "cash": f"{money(float(sleeve.get('cash') or 0.0), ccy)} in cash",
                       "rows": rows})
    return sorted(groups, key=lambda g: 0 if g["level"] == "act" else 1)


def _fx_groups(inp) -> list[dict]:
    by_class: dict[str, list[dict]] = {}
    for r in (inp.fx_book or {}).get("open_rows") or []:
        short = r["weight"] < 0
        net = float(r["unrealized"]) - float(r["entry_cost"])
        notional = float(r.get("notional") or 0.0)
        avg, now = r.get("avg_entry"), r.get("price")
        by_class.setdefault(get_pair(r["pair"]).asset_class, []).append({
            "symbol": r["pair"], "side": "SHORT" if short else "LONG",
            "qty": f"{pct(abs(r['weight']), 0, signed=False)} of the book {'short' if short else 'long'}",
            "bought": f"{'Sold' if short else 'Bought'} at {avg:.5g}" if avg else "Cost unknown",
            "now": f"now {now:.5g}" if r.get("priced") and now else "no price today",
            "worth": money(notional), "gain": money_move(net),
            "gain_pct": pct(net / notional) if notional else None, "dir": direction(net),
            "note": None if r.get("priced") else "No price today; held at cost."})
    ts = inp.now.astimezone(timezone.utc).replace(tzinfo=None)
    symbols = [row["symbol"] for rows in by_class.values() for row in rows]
    shut = sessions.closed_symbols(symbols, ts, str(inp.state.get("bar") or "1d"))
    groups = []
    for cls in ("fx", "crypto", "equity", "bond"):
        rows = by_class.get(cls)
        if not rows:
            continue
        frozen = all(r["symbol"] in shut for r in rows)
        n = len(rows)
        groups.append({"key": cls, "name": ASSET_GROUP[cls], "currency": "AUD", "level": None,
                       "why": ("Market shut now: positions frozen until it reopens." if frozen
                               else f"{n} open position{'s' if n != 1 else ''}."),
                       "caveat": None, "cash": None, "rows": rows})
    return groups


def positions_view(inp) -> dict:
    groups = _fx_groups(inp) if inp.kind == "fx" else _equity_groups(inp)
    n = sum(len(g["rows"]) for g in groups)
    notes = (["Gains are the price move, at today's exchange rate."] if inp.kind == "equity"
             else ["Gains are after the spread paid to get in."])
    if R.long_short(inp):
        notes.append("Cash includes the proceeds of short sales, so it can be more than "
                     "the book is worth.")
    order = [(g, "why") for g in groups] + [(g, "caveat") for g in groups]
    texts = glossary.gloss([g[k] or "" for g, k in order] + notes)
    for (g, k), t in zip(order, texts):
        if g[k]:
            g[k] = t
    notes = texts[len(order):]
    return {"v": 1, "key": inp.entry["key"], "name": inp.card["name"], "kind": inp.kind,
            "count": f"{n} holding{'s' if n != 1 else ''}", "prices": _prices(inp),
            "notes": notes, "groups": groups, "market": inp.market}
```

- [ ] **Step 4: Run to see them pass**

Run: `python3 -m pytest tests/test_book_positions.py`
Expected: PASS (6 tests). If the shut-market assertion fails, read `sessions.is_open` for a daily FX bar on a Saturday before changing either side. The test's expectation (FX shut at the weekend, crypto open) comes from the `forex/sessions.py` docstring.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/book_positions.py tests/test_book_positions.py
git commit -m "feat(dashboard): one book's Positions answer, saved holdings first"
```

---

### Task 11: Background prices, the service, and the server routes

**Files:**
- Create: `trading_algo/dashboard/live_marks.py`, `trading_algo/dashboard/book_service.py`
- Modify:
  - `trading_algo/dashboard/server.py`: `make_handler(..., books=None)`, `create_server(..., books=None)`, and the routes;
  - `trading_algo/home/__main__.py`: `make_app(..., live_build=None)` builds a `BookService`.
- Test: `tests/test_live_marks.py`, `tests/test_book_screens_server.py`

**Interfaces:**
- Produces:
  - `LiveMarks(build, *, online, ttl=900.0, clock=time.monotonic)`, with methods:
    - `.status(account) -> tuple[str, dict|None]`, where the status is one of `ready|pending|idle|offline|error`;
    - `.start(account) -> str`.
  - `BookService(*, now=None, online=None, live_build=None)`, with methods:
    - `.summary(key) -> dict|None`;
    - `.positions(key) -> dict|None`;
    - `.marks(key) -> dict|None`, returning `{"status": ...}`; FX books return `"n/a"`.
  - Routes, all GET:
    - `/api/summary/<KEY>`, `/api/positions/<KEY>`, `/api/marks/<KEY>`;
    - each returns 404 when `books` is None or the key is unknown, and a generic 500 on error.

- [ ] **Step 1: Write the failing tests** (`tests/test_live_marks.py`)

```python
import threading

from trading_algo.dashboard.live_marks import LiveMarks


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_idle_then_pending_then_ready_then_expired():
    gate, clock = threading.Event(), Clock()
    lm = LiveMarks(lambda acct: (gate.wait(5), {"as_of": "2026-10-13"})[1],
                   online=lambda: True, ttl=60, clock=clock)
    assert lm.status("full") == ("idle", None)
    assert lm.start("full") == "pending" and lm.status("full")[0] == "pending"
    gate.set()
    lm.wait("full", 5)
    assert lm.status("full") == ("ready", {"as_of": "2026-10-13"})
    clock.t = 61
    assert lm.status("full")[0] == "idle"


def test_offline_never_starts_a_fetch():
    called = []
    lm = LiveMarks(lambda a: called.append(a), online=lambda: False)
    assert lm.start("full") == "offline" and lm.status("full")[0] == "offline" and called == []


def test_failed_fetch_reports_error_and_retries_later():
    clock = Clock()
    lm = LiveMarks(lambda a: 1 / 0, online=lambda: True, clock=clock)
    lm.start("full")
    lm.wait("full", 5)
    assert lm.status("full")[0] == "error"
    clock.t = 61
    assert lm.status("full")[0] == "idle"
```

- [ ] **Step 2: Write the failing tests** (`tests/test_book_screens_server.py`)

```python
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import book_summary, server
from trading_algo.dashboard.book_service import BookService

KEYS = ["FULL", "ULTRA", "EXPERIMENTAL", "SMALL", "MATT", "PARTNER", "MULTI", "DAY"]


@pytest.fixture()
def base(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    books = BookService(now=lambda: NOW, online=lambda: False)
    httpd = server.create_server("full", False, "127.0.0.1", 0, books=books)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


@pytest.mark.parametrize("key", KEYS)
def test_every_book_answers_fast_with_no_network(base, key):
    for what in ("summary", "positions"):
        t0 = time.monotonic()
        code, body = get(f"{base}/api/{what}/{key}")
        assert code == 200 and body["key"] == key, (what, body)
        assert time.monotonic() - t0 < 2.0, (what, key)


def test_equity_marks_say_offline_and_fx_says_not_needed(base):
    assert get(f"{base}/api/marks/FULL") == (200, {"status": "offline"})
    assert get(f"{base}/api/marks/MATT") == (200, {"status": "n/a"})


@pytest.mark.parametrize("key", ["NOPE", "FX%C2%B7MATT", "..%2F..%2Fetc"])
def test_unknown_or_odd_keys_are_404(base, key):
    code, body = get(f"{base}/api/summary/{key}")
    assert code == 404 and "Traceback" not in json.dumps(body)


def test_a_builder_error_is_a_generic_500(base, monkeypatch):
    monkeypatch.setattr(book_summary, "summarise", lambda inp: 1 / 0)
    code, body = get(f"{base}/api/summary/FULL")
    assert code == 500 and body == {"error": "internal server error"}


def test_routes_are_404_when_book_screens_are_not_enabled(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    httpd = server.create_server("full", False, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert get(f"http://127.0.0.1:{httpd.server_address[1]}/api/summary/FULL")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
```

Append to `tests/test_home_app.py`:

```python
def test_make_app_serves_book_summaries(tmp_path):
    done = run_py(f"""
        import json, shutil, threading, urllib.request
        from pathlib import Path
        from trading_algo.home import __main__ as launcher, jobs, sync
        from trading_algo.home.paths import AppPaths
        paths = AppPaths(Path({str(tmp_path / 'app')!r}))
        paths.snapshots.mkdir(parents=True)
        shutil.copytree({str(REPO_ROOT / 'tests/fixtures/books_20261013')!r}, paths.snapshots / "fx")
        paths.current.symlink_to(paths.snapshots / "fx")
        launcher.point_readers_at(paths)
        jobs.fetch_runs = lambda *a, **k: []
        first = sync.SyncResult("fx", "2026-10-13T06:45:11Z", False, True, "test")
        httpd = launcher.make_app(paths, first, port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{{httpd.server_address[1]}}/api/summary/MATT"
        print(json.load(urllib.request.urlopen(url, timeout=20))["key"])
        httpd.shutdown()
    """, tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "MATT"
```

(Check `sync.SyncResult`'s field order with `grep -n "class SyncResult" -A8 trading_algo/home/sync.py`. The summary of the B1 build gives `(sha, committed_at, changed, offline, error)`. If it differs, pass keyword arguments.)

- [ ] **Step 3: Run to see them fail**

Run: `python3 -m pytest tests/test_live_marks.py tests/test_book_screens_server.py tests/test_home_app.py`
Expected: FAIL. `live_marks` and `book_service` don't exist yet (ImportError), and `create_server` rejects `books=` (TypeError).

- [ ] **Step 4: Implement `live_marks.py`**

```python
"""Today's prices for an equity book, fetched in the background and kept for
a while. The live re-mark (`api.build_snapshot`) takes ~50 s and can hang on a
failing download, so no request ever waits on it: the page asks, gets
"pending", and polls. Offline (the last sync failed), nothing is fetched."""
from __future__ import annotations

import sys
import threading
import time
import traceback

ERROR_RETRY = 60.0


class LiveMarks:
    def __init__(self, build, *, online, ttl: float = 900.0, clock=time.monotonic):
        self._build, self._online, self._ttl, self._clock = build, online, ttl, clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._running: dict[str, threading.Thread] = {}
        self._errors: dict[str, float] = {}

    def status(self, account: str) -> tuple[str, dict | None]:
        with self._lock:
            hit = self._cache.get(account)
            if hit and self._clock() - hit[0] < self._ttl:
                return "ready", hit[1]
            if account in self._running:
                return "pending", None
            err = self._errors.get(account)
            if err is not None and self._clock() - err < ERROR_RETRY:
                return "error", None
        return ("idle", None) if self._online() else ("offline", None)

    def start(self, account: str) -> str:
        state, _ = self.status(account)
        if state != "idle":
            return state
        with self._lock:
            if account in self._running:
                return "pending"
            t = threading.Thread(target=self._run, args=(account,), daemon=True)
            self._running[account] = t
            t.start()
        return "pending"

    def wait(self, account: str, timeout: float) -> None:
        """Tests only: block until a running fetch finishes."""
        with self._lock:
            t = self._running.get(account)
        if t is not None:
            t.join(timeout)

    def _run(self, account: str) -> None:
        try:
            payload = self._build(account)
        except Exception as exc:
            print(f"[dashboard] live prices for {account!r} failed: {exc!r}", file=sys.stderr)
            traceback.print_exc()
            with self._lock:
                self._errors[account] = self._clock()
        else:
            with self._lock:
                self._cache[account] = (self._clock(), payload)
                self._errors.pop(account, None)
        finally:
            with self._lock:
                self._running.pop(account, None)
```

- [ ] **Step 5: Implement `book_service.py`**

```python
"""Glue for the book screens: a display key → saved inputs → the Overview or
Positions answer, plus the background price fetch for equity books."""
from __future__ import annotations

from datetime import datetime, timezone

from . import api, book_inputs, book_positions, book_summary, registry
from .live_marks import LiveMarks


class BookService:
    def __init__(self, *, now=None, online=None, live_build=None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.marks = LiveMarks(live_build or (lambda acct: api.build_snapshot(acct, False)),
                               online=online or (lambda: True))

    def _inputs(self, key: str):
        entry = registry.resolve(key)
        if entry is None:
            return None
        live, market = None, "n/a"
        if entry["kind"] == "equity":
            market, live = self.marks.status(entry["account"])
        return book_inputs.load_inputs(entry, self._now(), live=live, market=market)

    def summary(self, key: str) -> dict | None:
        inp = self._inputs(key)
        return api._nan_free(book_summary.summarise(inp)) if inp else None

    def positions(self, key: str) -> dict | None:
        inp = self._inputs(key)
        return api._nan_free(book_positions.positions_view(inp)) if inp else None

    def marks(self, key: str) -> dict | None:
        entry = registry.resolve(key)
        if entry is None:
            return None
        if entry["kind"] != "equity":
            return {"status": "n/a"}
        return {"status": self.marks.start(entry["account"])}
```

Note: `registry.resolve` upper-cases the key and matches display keys or account names exactly. So `FX%C2%B7MATT` (`FX·MATT`) and `../..` resolve to `None` and give a 404, and no file path is ever built from the URL.

- [ ] **Step 6: Wire the routes in `server.py`**

Change the signature to `def make_handler(account: str, synthetic: bool, home=None, books=None):`. Inside `Handler`, add this method after `_equity`:

```python
        def _book_api(self, path: str) -> None:
            if books is None:
                self._json(404, {"error": "book screens not enabled"})
                return
            what, _, key = path[len("/api/"):].partition("/")
            key = unquote(key)
            try:
                out = {"summary": books.summary, "positions": books.positions,
                       "marks": books.marks}[what](key)
            except Exception as exc:  # surface server-side, never leak
                print(f"[dashboard] error building {what} for {key!r}: {exc!r}", file=sys.stderr)
                traceback.print_exc()
                self._json(500, {"error": "internal server error"})
                return
            if out is None:
                self._json(404, {"error": "no such book"})
            else:
                self._json(200, out)
```

In `do_GET`, add this before the `if path.startswith("/api/"):` line:

```python
            if path.startswith(("/api/summary/", "/api/positions/", "/api/marks/")):
                self._book_api(path)
                return
```

Add `unquote` to the existing `urllib.parse` import (`from urllib.parse import parse_qs, unquote, urlsplit`). Change `create_server` to:

```python
def create_server(account: str = "main", synthetic: bool = False,
                  host: str = "127.0.0.1", port: int = 8787,
                  home=None, books=None) -> ThreadingHTTPServer:
    """… (keep the docstring) `books`, if given, serves the book screens'
    /api/summary, /api/positions and /api/marks."""
    return ThreadingHTTPServer((host, port), make_handler(account, synthetic, home, books))
```

- [ ] **Step 7: Wire `make_app` in `trading_algo/home/__main__.py`**

Change the signature to `def make_app(paths: AppPaths, first: sync.SyncResult, *, port: int = 0, live_build=None):`. Add `from ..dashboard.book_service import BookService` beside the two late imports. Replace the `return` line with:

```python
    books = BookService(online=lambda: not state["sync"].offline, live_build=live_build)
    return server.create_server("full", False, "127.0.0.1", port, home=build, books=books)
```

- [ ] **Step 8: Run to see them pass**

Run: `python3 -m pytest tests/test_live_marks.py tests/test_book_screens_server.py tests/test_home_app.py tests/test_home_server.py`
Expected: PASS (all). If a book takes ≥ 2 s, profile it with `python3 -m cProfile` before touching the threshold. The spec's 2 s is a requirement, not a tolerance to raise.

- [ ] **Step 9: Commit**

```bash
git add trading_algo/dashboard/live_marks.py trading_algo/dashboard/book_service.py \
        trading_algo/dashboard/server.py trading_algo/home/__main__.py \
        tests/test_live_marks.py tests/test_book_screens_server.py tests/test_home_app.py
git commit -m "feat(dashboard): serve the book screens; prices fetched in the background"
```

---

### Task 12: Shared building blocks (`blocks.js`) without changing the home screen

**Files:**
- Create: `trading_algo/dashboard/static/blocks.js`
- Modify:
  - `trading_algo/dashboard/static/home.js`: use `window.MB` and drop its own copies;
  - `trading_algo/dashboard/static/home.html`: script order, and no `aria-live` on `<main>`.
- Test: `tests/test_home_ui.py` (extend)

**Interfaces:**
- Produces: `window.MB` with:
  - constants `PERIODS, SINCE, CHART_DAYS, VERDICT`;
  - helpers `esc, fmt, dir, arrow, arrowFor, signed, pct, when`;
  - `chips(period)`, `chart(series, title, period, opts)` (`opts = {bench, desc, percent, benchLabel}`), `list(items)`, `pill(level, shape, word)`;
  - `screens: {}`.

- [ ] **Step 1: Extend the UI test (it fails now)**

Replace the body of `tests/test_home_ui.py` with:

```python
"""The page's assets exist, load in order, parse, never collide, and never do
money arithmetic of their own."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "trading_algo" / "dashboard" / "static"
SCRIPTS = ["blocks.js", "screens/book_overview.js", "screens/book_positions.js", "home.js"]


def test_assets_exist_and_load_in_order():
    html = (STATIC / "home.html").read_text()
    srcs = re.findall(r'<script src="([^"]+)"></script>', html)
    assert srcs == SCRIPTS
    assert 'href="home.css"' in html and (STATIC / "home.css").stat().st_size > 0
    assert "aria-live" not in html.split("<main", 1)[1].split(">", 1)[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses(name):
    done = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_no_two_scripts_declare_the_same_top_level_name():
    seen = {}
    for name in SCRIPTS:
        for m in re.finditer(r"^(?:const|let|var|function|async function|class)\s+([A-Za-z_$][\w$]*)",
                             (STATIC / name).read_text(), re.M):
            assert m.group(1) not in seen, f"{m.group(1)} in {name} and {seen.get(m.group(1))}"
            seen[m.group(1)] = name


def test_js_takes_numbers_from_the_api_not_its_own_maths():
    """No script totals or divides money (the server does, G-4 / AC-14)."""
    for name in SCRIPTS:
        js = (STATIC / name).read_text()
        assert not re.search(r"\.reduce\(", js), name
        assert "/ start" not in js and "- start" not in js, name
        assert "- b.initial" not in js and "- g.initial" not in js, name


def test_screen_files_only_use_server_text_for_money():
    for name in SCRIPTS[1:3]:
        js = (STATIC / name).read_text()
        assert not re.search(r"\.(equity|initial|net_pnl|value_base|unrealized\w*|costs)\b", js), name


def test_links_are_relative_never_root():
    for name in SCRIPTS:
        assert 'href="/' not in (STATIC / name).read_text(), name
```

Run: `python3 -m pytest tests/test_home_ui.py`
Expected: FAIL (`blocks.js` missing; the script list doesn't match)

- [ ] **Step 2: Create `blocks.js`**

```javascript
// Momentum screens: shared building blocks on one namespace (MB) and no other
// top-level names, so a later export can inline every script into one page
// without collisions. Money on the book screens arrives as server-written
// text; numbers here only place points on a chart or format the home
// screen's own figures (dashboard/home.py computes them).
"use strict";
(function (root) {
  const PERIODS = ["1D", "1W", "1M", "ALL"];
  const SINCE = {"1D": "in the last session", "1W": "over the last week",
                 "1M": "over the last month", "ALL": "since you started"};
  const CHART_DAYS = {"1D": 7, "1W": 7, "1M": 31, "ALL": Infinity};
  const VERDICT = {
    act: {shape: "■", word: "Action needed"},
    look: {shape: "▲", word: "Look soon"},
    clear: {shape: "●", word: "All clear"},
    nodata: {shape: "◆", word: "No data yet"},
  };
  const ARROWS = {up: "▲", down: "▼", flat: "▶"};

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (v) => Math.abs(v).toLocaleString("en-AU",
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
  const arrow = (v) => ARROWS[dir(v)];
  const arrowFor = (d) => ARROWS[d] || "";
  const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}A$${fmt(v)}`;
  const pct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(2)}%`;
  const when = (iso) => (iso ? new Date(iso).toLocaleString("en-AU",
    {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})
    : "never");

  function chips(period) {
    return `<div class="chips" role="group" aria-label="Period">${PERIODS.map((p) =>
      `<button class="chip" data-period="${p}" data-focus="chip:${p}" aria-pressed="${p === period}">${p}</button>`).join("")}</div>`;
  }

  function windowed(series, period) {
    const days = CHART_DAYS[period];
    if (days === Infinity || !series.length) return series;
    const end = new Date(series[series.length - 1][0]);
    const start = new Date(end.getTime() - days * 86400000);
    return series.filter(([d]) => new Date(d) >= start);
  }

  // A line over time with green/red areas around zero. opts.bench draws a
  // second series in long dashes; break-even is dotted, so the two differ by
  // pattern as well as colour. opts.desc is the text alternative.
  function chart(series, title, period, opts = {}) {
    const pts = windowed(series || [], period);
    if (pts.length < 2) {
      return `<div class="panel chart"><h3>${esc(title)}</h3><span class="muted">Not enough history yet.</span></div>`;
    }
    const first = pts[0][0];
    const bench = opts.bench ? windowed(opts.bench, period).filter(([d]) => d >= first) : [];
    const vals = pts.map(([, v]) => v).concat(bench.map(([, v]) => v));
    const lo = Math.min(0, ...vals);
    const hi = Math.max(0, ...vals);
    const span = hi - lo || 1;
    const W = 600;
    const H = 150;
    const t0 = new Date(first).getTime();
    const tN = new Date(pts[pts.length - 1][0]).getTime();
    const x = (d) => ((new Date(d).getTime() - t0) / ((tN - t0) || 1)) * W;
    const y = (v) => H - ((v - lo) / span) * H;
    const path = (s) => s.map(([d, v]) => `${x(d).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const line = path(pts);
    const zero = y(0);
    const area = `0,${zero.toFixed(1)} ${line} ${W},${zero.toFixed(1)}`;
    const own = pts.map(([, v]) => v);
    const iLo = own.indexOf(Math.min(...own));
    const iHi = own.indexOf(Math.max(...own));
    const show = opts.percent ? pct : signed;
    const desc = opts.desc || title;
    const benchLine = bench.length > 1
      ? `<polyline points="${path(bench)}" fill="none" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="8 4"/>` : "";
    const legend = bench.length > 1 ? ` · long dashes = ${esc(opts.benchLabel || "the market")}` : "";
    return `<div class="panel chart"><h3>${esc(title)}</h3>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(desc)}">
        <desc>${esc(desc)}</desc>
        <defs><clipPath id="above"><rect x="0" y="0" width="${W}" height="${zero.toFixed(1)}"/></clipPath>
              <clipPath id="below"><rect x="0" y="${zero.toFixed(1)}" width="${W}" height="${(H - zero).toFixed(1)}"/></clipPath></defs>
        <polygon points="${area}" fill="var(--up)" opacity=".18" clip-path="url(#above)"/>
        <polygon points="${area}" fill="var(--down)" opacity=".18" clip-path="url(#below)"/>
        <line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}" stroke="var(--muted)" stroke-dasharray="1 4" stroke-width="1"/>
        ${benchLine}
        <polyline points="${line}" fill="none" stroke="var(--text)" stroke-width="1.4"/>
      </svg>
      <div class="caption"><span>${esc(pts[0][0])}</span>
        <span>low ${show(own[iLo])} (${esc(pts[iLo][0])}) · high ${show(own[iHi])} (${esc(pts[iHi][0])}) · dotted = break-even${legend}</span>
        <span>${esc(pts[pts.length - 1][0])}</span></div></div>`;
  }

  function list(items) {
    const tags = {act: `<span class="tag act">■ Act now</span>`,
                  look: `<span class="tag look">▲ Look soon</span>`,
                  known: `<span class="tag muted">Known</span>`,
                  note: `<span class="tag muted">Note</span>`};
    return `<ul class="items">${items.map((i) => {
      const link = i.url ? ` <a href="${esc(i.url)}" target="_blank" rel="noopener">Open on GitHub ↗</a>` : "";
      return `<li>${tags[i.level] || ""}${i.book ? `<strong>${esc(i.book)}</strong> · ` : ""}${esc(i.text)}${link}</li>`;
    }).join("")}</ul>`;
  }

  const pill = (level, shape, word) =>
    `<span class="pill ${esc(level)}" role="status">${esc(shape)} ${esc(word)}</span>`;

  root.MB = {PERIODS, SINCE, CHART_DAYS, VERDICT, esc, fmt, dir, arrow, arrowFor, signed,
             pct, when, chips, chart, list, pill, screens: {}};
})(window);
```

- [ ] **Step 3: Point `home.js` at `MB`, with no behaviour change yet**

In `home.js`:
- Delete the top-level declarations of `PERIODS`, `SINCE`, `CHART_DAYS`, `VERDICT`, `esc`, `fmt`, `dir`, `arrow`, `signed`, `pct`, `when`, and the functions `chips`, `chart` and `list`.
- Put this line directly under `"use strict";`:

```javascript
const {SINCE, VERDICT, esc, fmt, dir, arrow, signed, pct, when} = window.MB;
```

Then replace the remaining uses:

| Old | New |
|---|---|
| `chips()` | `MB.chips(period)` |
| `chart(x, t)` | `MB.chart(x, t, period)` |
| `list(` | `MB.list(` |

Change both `href="/"` links to `href="index.html"`. In `render()`, change `main.querySelectorAll(".chip")` to `main.querySelectorAll("[data-period]")`.

Create empty `static/screens/book_overview.js` and `static/screens/book_positions.js`, each containing only:

```javascript
"use strict";
```

In `home.html`, replace `<script src="home.js"></script>` with the four tags in `SCRIPTS` order, and change `<main id="main" class="main" aria-live="polite">` to `<main id="main" class="main">`.

- [ ] **Step 4: Run the UI and home tests**

Run: `python3 -m pytest tests/test_home_ui.py tests/test_home_api.py tests/test_home_server.py`
Expected: PASS (all). The home screen looks and behaves as before.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/static tests/test_home_ui.py
git commit -m "refactor(home): shared building blocks on one namespace (MB)"
```

---

### Task 13: The book view: tabs, Overview, links, polling

**Files:**
- Modify: `trading_algo/dashboard/static/home.js` (book shell); `trading_algo/dashboard/static/home.css`
- Create (fill): `trading_algo/dashboard/static/screens/book_overview.js`
- Test: `tests/test_book_screens_browser.py` (new; Playwright)

**Interfaces:**
- Consumes: `/api/summary/<KEY>`, `/api/marks/<KEY>` (Task 11) and `MB` (Task 12).
- Produces:
  - `MB.screens.bookOverview.render(summary, {period, chartView}) -> html`;
  - in `home.js`: `tab`, `chartView`, `bookData`, `loadBook(key, force)`, `setTab(t)` and `watchMarks(key)`;
  - DOM hooks the tests use:
    - `.result`, `.main-problem`, `.story [data-item]`, `.tile`, `.vcol`;
    - `[role="tab"][data-tab]`, `a.ext[data-link]`;
    - every focusable element has a `data-focus`.

- [ ] **Step 1: Write the failing browser tests**

```python
"""The book screens in a real headless browser, against the frozen books.
Skipped where Playwright/Chromium are missing — unless REQUIRE_BROWSER=1, which
CI's `ui` job sets, so a skip can never hide a broken screen there."""
import os
import shutil
import threading

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("REQUIRE_BROWSER"):
        raise
    pytest.skip("Playwright not installed (REQUIRE_BROWSER=1 makes this fail)",
                allow_module_level=True)

from book_fixtures import FIXTURE

BOOKS = ["FULL", "MATT", "EXPERIMENTAL", "SMALL"]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binary missing
            if os.environ.get("REQUIRE_BROWSER"):
                raise
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from trading_algo.home import __main__ as launcher, jobs, sync
    from trading_algo.home.paths import AppPaths

    import book_fixtures
    state = book_fixtures.use_snapshot(tmp_path, monkeypatch)
    book_fixtures.no_network(monkeypatch)
    monkeypatch.setattr(jobs, "fetch_runs", lambda *a, **k: [])
    paths = AppPaths(tmp_path / "app")
    paths.snapshots.mkdir(parents=True)
    paths.current.symlink_to(state)
    first = sync.SyncResult("fixture", "2026-10-13T06:45:11Z", False, True, "test")
    httpd = launcher.make_app(paths, first, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def open_book(browser, base, key, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/home")
    page.wait_for_selector("#sidebar button.row")
    page.click(f'#sidebar button.row[data-key="{key}"]')
    page.wait_for_selector(".result")
    return page, errors


@pytest.mark.parametrize("key", BOOKS)
def test_overview_has_every_part(browser, app, key):
    page, errors = open_book(browser, app, key)
    assert page.locator(".pill").count() == 1
    assert page.locator(".main-problem").count() == 1
    assert page.locator(".story [data-item]").count() >= 2
    assert page.locator(".tile").count() == 6
    assert page.locator(".vcol").count() == 3
    assert errors == []


def test_links_open_the_old_terminal_on_this_book_and_tab(browser, app):
    page, _ = open_book(browser, app, "FULL")
    assert page.get_attribute('a.ext[data-link="classic"]', "href") == "index.html?account=FULL&tab=OVERVIEW"
    assert page.get_attribute('a.ext[data-link="backtest"]', "href") == "index.html?account=FULL&tab=BACKTEST"
    assert page.locator('a.ext[data-link="swarm"]').count() == 0          # equity book


def test_tabs_follow_the_keyboard_pattern(browser, app):
    page, _ = open_book(browser, app, "MATT")
    page.focus('[role="tab"][data-tab="overview"]')
    page.keyboard.press("ArrowRight")
    page.wait_for_selector('[role="tab"][data-tab="positions"][aria-selected="true"]')
    assert page.evaluate("document.activeElement.dataset.focus") == "tab:positions"
```

- [ ] **Step 2: Run to see them fail** (locally Playwright 1.62.0 is installed)

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py`
Expected: FAIL. It times out waiting for `.result`, because the book view is still B1's.

- [ ] **Step 3: Fill `screens/book_overview.js`**

```javascript
// One book's Overview: status is in the shell; this draws result → main problem
// → summary → tiles → "Is it working?" → chart → months → last closed. Every
// word and money figure is the server's (dashboard/book_summary.py).
"use strict";
(function (MB) {
  const {esc, arrowFor} = MB;

  function fix(i) {
    return i.backlog ? ` <span class="fix">Tracked as fix ${esc(i.backlog)}</span>` : "";
  }

  function item(i) {
    const tag = i.level === "act" ? `<span class="tag act">■</span>`
      : i.level === "look" ? `<span class="tag look">▲</span>` : "";
    const cav = i.caveat ? `<div class="caveat">${esc(i.caveat)}</div>` : "";
    return `<li data-item="${esc(i.uid)}">${tag}${esc(i.text)}.${fix(i)}${cav}<div class="src">Source: ${esc(i.source)}</div></li>`;
  }

  function col(title, cls, items, empty) {
    return `<div class="vcol"><h4 class="${cls}">${title}</h4>${items.length
      ? `<ul class="items">${items.map(item).join("")}</ul>` : `<p class="muted">${esc(empty)}</p>`}</div>`;
  }

  function tiles(ts) {
    return `<div class="tiles">${ts.map((t) => `<div class="tile ${esc(t.dir || "")}">
      <div class="tl">${esc(t.label)}</div><div class="tv">${esc(t.value)}</div>
      <div class="tc">${esc(t.caption)}</div></div>`).join("")}</div>`;
  }

  function months(ms) {
    if (!ms.length) return `<span class="muted">No full month yet.</span>`;
    return `<div class="months">${ms.map((m) => `<div class="month ${esc(m.dir)}">
      <span class="ml">${esc(m.label)}</span><span class="mv">${arrowFor(m.dir)} ${esc(m.text)}</span></div>`).join("")}</div>`;
  }

  function closed(rows) {
    if (!rows.length) return `<span class="muted">No closed positions yet.</span>`;
    return `<table class="tbl"><thead><tr><th scope="col">Closed</th><th scope="col">Holding</th>
      <th scope="col">Held</th><th scope="col">Result</th></tr></thead><tbody>${rows.map((r) =>
      `<tr><td>${esc(r.date)}</td><td>${esc(r.symbol)}</td><td>${esc(r.held)}</td>
       <td class="${esc(r.dir)}">${esc(r.net)}</td></tr>`).join("")}</tbody></table>`;
  }

  function chartBlock(s, ctx) {
    const views = [["value", "Value"], ["below", "Below its best"]];
    const toggle = `<div class="chips" role="group" aria-label="Chart">${views.map(([v, l]) =>
      `<button class="chip" data-view="${v}" data-focus="view:${v}" aria-pressed="${ctx.chartView === v}">${l}</button>`).join("")}</div>`;
    const c = s.chart;
    const body = ctx.chartView === "below"
      ? MB.chart(c.below_best, "Below its best (fall from the highest saved value)", ctx.period,
                 {percent: true, desc: c.below_desc})
      : MB.chart(c.value, "Profit and loss since the start (A$)", ctx.period,
                 {bench: c.benchmark, desc: c.value_desc, benchLabel: "the market (leaves out dividends)"});
    return MB.chips(ctx.period) + toggle + body;
  }

  function render(s, ctx) {
    let html = `<div class="result ${esc(s.result.dir || "")}">${arrowFor(s.result.dir)} ${esc(s.result.text)}</div>`;
    if (s.result.caveat) html += `<div class="caveat">${esc(s.result.caveat)}</div>`;
    const mp = s.main_problem;
    html += `<div class="main-problem"><span class="mp-label">Main problem</span> ${esc(mp.text)}${fix(mp)}</div>`;
    if (s.attention.length) html += `<ul class="items attn">${s.attention.map(item).join("")}</ul>`;
    html += `<div class="story">${s.summary.map((x) =>
      `<span data-item="${esc(x.item_id)}">${esc(x.text)}</span>`).join(" ")}</div>`;
    html += tiles(s.tiles);
    html += `<div class="panel"><h3>Is it working?</h3><div class="vgrid">
      ${col("✓ Going right", "right", s.panel.right, s.right_empty || "")}
      ${col("✗ Holding it back", "back", s.panel.wrong, "Nothing standing out.")}
      ${col("◆ Is the model any good?", "model", s.panel.model, "")}</div></div>`;
    html += chartBlock(s, ctx);
    html += `<div class="two"><div class="panel"><h3>Month by month</h3>${months(s.months)}</div>
      <div class="panel"><h3>Last closed positions</h3>${closed(s.closed)}</div></div>`;
    if (s.errors.length) html += `<p class="note">Couldn't measure: ${esc(s.errors.join(", "))}.</p>`;
    return html;
  }

  MB.screens.bookOverview = {render};
})(window.MB);
```

- [ ] **Step 4: Replace B1's single-book view in `home.js` with the book shell**

Add these top-level state lines under `let selected = null;`:

```javascript
let tab = "overview";                     // the selected book's view
let chartView = "value";                  // Overview chart: "value" | "below"
const bookData = {};                      // key -> {summary, positions, error}
let marksTimer = null;
```

Replace the whole `renderBook(b)` function with:

```javascript
function tabsBar(b) {
  const t = (id, label) => `<button role="tab" class="tab" data-tab="${id}" data-focus="tab:${id}"
    aria-selected="${tab === id}" tabindex="${tab === id ? 0 : -1}">${label}</button>`;
  const k = encodeURIComponent(b.key);
  const link = (id, label, t2) => `<a class="ext" data-link="${id}" data-focus="link:${id}"
    href="index.html?account=${k}&tab=${t2}" target="_blank" rel="noopener">${label} ↗</a>`;
  return `<div class="tabbar"><div role="tablist" aria-label="Book views">${t("overview", "Overview")}${t("positions", "Positions")}</div>
    <div class="links">${link("backtest", "Backtest", "BACKTEST")}${link("method", "Method", "METHOD")}${
      b.kind === "fx" ? link("swarm", "Swarm", "SWARM") : ""}${link("classic", "Classic view", "OVERVIEW")}</div></div>`;
}

function bookBody(b) {
  const c = bookData[b.key] || {};
  if (c.error) {
    return `<div class="banner" role="alert">Couldn't load this book (${esc(c.error)}). ⌘R to retry.</div>`;
  }
  if (tab === "positions") {
    return c.positions ? MB.screens.bookPositions.render(c.positions) : `<p class="muted">Loading positions…</p>`;
  }
  return c.summary ? MB.screens.bookOverview.render(c.summary, {period, chartView}) : `<p class="muted">Loading…</p>`;
}

function renderBook(b) {
  const st = (bookData[b.key] || {}).summary?.status;
  let html = banner();
  html += st ? MB.pill(st.level, st.shape, st.word) : "";
  html += `<h1 class="book-title">${esc(b.name)}</h1>`;
  html += tabsBar(b) + `<div class="book-body">${bookBody(b)}</div>`;
  return `${html}<p class="foot">Last update ${esc(when(b.as_of))}</p>`;
}

async function getJSON(url) {
  const res = await fetch(url, {cache: "no-store"});
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadBook(key, force = false) {
  const c = bookData[key] || (bookData[key] = {});
  try {
    if (force || !c.summary) c.summary = await getJSON(`/api/summary/${encodeURIComponent(key)}`);
    if (tab === "positions" && (force || !c.positions)) {
      c.positions = await getJSON(`/api/positions/${encodeURIComponent(key)}`);
    }
    c.error = null;
  } catch (err) {
    c.error = err.message;
  }
  if (selected === key) render();
  watchMarks(key);
}

// Equity books: today's prices arrive from a background fetch (~1 min). Ask
// once, poll every 5 s for up to ~3 min, then reload when they're ready.
function watchMarks(key) {
  clearTimeout(marksTimer);
  const c = bookData[key];
  if (!c || !c.summary || !["idle", "pending"].includes(c.summary.market)) return;
  let tries = 0;
  const tick = async () => {
    if (selected !== key || tries++ > 36) return;
    try {
      const m = await getJSON(`/api/marks/${encodeURIComponent(key)}`);
      if (["ready", "offline", "error"].includes(m.status)) { await loadBook(key, true); return; }
    } catch (err) {
      return;
    }
    marksTimer = setTimeout(tick, 5000);
  };
  tick();
}

function setTab(t) {
  tab = t;
  render();
  if (selected) loadBook(selected);
}
```

Replace `render()` with this version, which keeps focus across re-renders:

```javascript
function render() {
  if (!data) return;
  const focus = document.activeElement?.dataset?.focus;
  renderSidebar();
  const b = books().find((x) => x.key === selected);
  const main = document.getElementById("main");
  main.innerHTML = b ? renderBook(b) : renderOverview();
  main.querySelectorAll("[data-period]").forEach((el) =>
    el.addEventListener("click", () => { period = el.dataset.period; render(); }));
  main.querySelectorAll("[data-view]").forEach((el) =>
    el.addEventListener("click", () => { chartView = el.dataset.view; render(); }));
  main.querySelectorAll('[role="tab"]').forEach((el) =>
    el.addEventListener("click", () => setTab(el.dataset.tab)));
  if (focus) document.querySelector(`[data-focus="${CSS.escape(focus)}"]`)?.focus();
}
```

In `renderSidebar()`, add `data-focus="row:${esc(b.key)}"` to each book button and `data-focus="row:"` to the Overview button.

Replace `select(key)` and the body of `refresh(...)`:

```javascript
function select(key) {
  selected = key;
  render();
  if (key) loadBook(key);
}

async function refresh(syncNow = false) {
  try {
    const res = await fetch(`/api/home${syncNow ? "?sync=1" : ""}`, {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    render();
    if (selected) loadBook(selected, true);
  } catch (err) {
    document.getElementById("main").innerHTML =
      `<div class="banner" role="alert">The home screen couldn't load its data (${esc(err.message)}). ⌘R to retry.</div>`;
  }
}
```

In the `keydown` listener, insert this before the `Escape` line:

```javascript
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight")
      && document.activeElement?.getAttribute("role") === "tab") {
    e.preventDefault();
    setTab(tab === "overview" ? "positions" : "overview");
    document.querySelector(`[data-focus="tab:${tab}"]`)?.focus();
    return;
  }
```

- [ ] **Step 5: Add the styles** (append to `home.css`)

```css
.book-title { margin: 10px 0 0; font-size: 18px; }
.tabbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 12px 0 4px;
  border-bottom: 1px solid var(--line); }
[role="tablist"] { display: flex; gap: 4px; }
.tab { font: inherit; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent;
  padding: 6px 10px; cursor: pointer; }
.tab[aria-selected="true"] { color: var(--text); border-bottom-color: var(--select); }
.tabbar .links { margin-left: auto; display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; }
.result { font-size: 22px; font-weight: 300; margin: 14px 0 2px; }
.main-problem { margin: 8px 0; padding: 8px 12px; border: 1px solid var(--line); border-radius: 10px; }
.mp-label { font-weight: 600; margin-right: 6px; }
.story { background: rgba(122,162,255,.08); border-left: 3px solid var(--select); border-radius: 10px;
  padding: 10px 14px; margin: 12px 0; font-size: 14px; line-height: 1.55; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 12px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
.tile .tl, .tile .tc { color: var(--muted); font-size: 12px; }
.tile .tv { font-size: 18px; margin: 2px 0; }
.tile.up .tv { color: var(--up); } .tile.down .tv { color: var(--down); }
.vgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
.vcol h4 { margin: 0 0 6px; font-size: 13px; color: var(--text); }
.caveat, .src { color: var(--muted); font-size: 12px; }
.fix { font-size: 12px; color: var(--focus); white-space: nowrap; }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.months { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 6px; }
.month { border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; display: flex; flex-direction: column; }
.month .ml { color: var(--muted); font-size: 12px; }
.month.up .mv { color: var(--up); } .month.down .mv { color: var(--down); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: var(--muted); font-weight: 600; padding: 4px 6px; border-bottom: 1px solid var(--line); }
.tbl td { padding: 6px; border-top: 1px solid rgba(255,255,255,.06); vertical-align: top; }
.group.attn { border-color: var(--act); }
.why { margin: 4px 0 8px; } .note { color: var(--muted); font-size: 12px; margin: 4px 0; }
```

- [ ] **Step 6: Run the browser and UI tests**

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py tests/test_home_ui.py`
Expected: PASS (all; 6 browser tests)

- [ ] **Step 7: Commit**

```bash
git add trading_algo/dashboard/static tests/test_book_screens_browser.py
git commit -m "feat(home): a book's Overview screen with tabs, links and background prices"
```

---

### Task 14: The Positions screen and the Classic-view deep link

**Files:**
- Fill: `trading_algo/dashboard/static/screens/book_positions.js`
- Modify: `trading_algo/dashboard/static/app.js` (`boot()`)
- Test: `tests/test_book_screens_browser.py` (append)

**Interfaces:**
- Consumes: `/api/positions/<KEY>` (Task 11).
- Produces:
  - `MB.screens.bookPositions.render(p) -> html`, with DOM hooks `.group`, `.group .why` and `.group tbody tr`;
  - in `app.js`, `?account=<KEY>&tab=<TAB>` selects that book and tab at boot (not in exports).

- [ ] **Step 1: Write the failing tests** (append)

```python
def open_positions(browser, base, key):
    page, errors = open_book(browser, base, key)
    page.click('[role="tab"][data-tab="positions"]')
    page.wait_for_selector(".group")
    return page, errors


def test_full_positions_show_each_region_and_why_with_the_network_off(browser, app):
    page, errors = open_positions(browser, app, "FULL")
    assert page.locator(".group").count() == 3
    assert page.locator(".group .why").count() == 3
    assert "since the book began" in page.inner_text(".group:has-text('Australia')")
    assert page.locator(".group tbody tr").count() > 0
    assert "offline" in page.inner_text(".prices")
    assert errors == []


def test_fx_positions_group_by_asset_class(browser, app):
    page, errors = open_positions(browser, app, "MATT")
    names = page.locator(".group h3").all_inner_texts()
    assert any(n.startswith("Currencies") for n in names) and errors == []


@pytest.mark.parametrize("tab", ["OVERVIEW", "BACKTEST", "METHOD"])
def test_classic_view_opens_the_old_terminal_on_the_same_book_and_tab(browser, app, tab):
    page = browser.new_page()
    page.goto(f"{app}/index.html?account=SMALL&tab={tab}")
    page.wait_for_function("typeof S !== 'undefined' && S.account === 'SMALL'")
    assert page.evaluate("S.tab") == tab
```

- [ ] **Step 2: Run to see them fail**

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py -k "positions or classic"`
Expected:
- the Positions tests FAIL, timing out on `.group`, because `bookPositions` is undefined;
- the Classic view tests FAIL, because `S.account` is `FULL`.

- [ ] **Step 3: Fill `screens/book_positions.js`**

```javascript
// One book's Positions: per region (equity) or asset group (FX), whether it's
// investing or waiting and why, then each holding. All text is the server's
// (dashboard/book_positions.py); holdings show at once from the saved book.
"use strict";
(function (MB) {
  const {esc} = MB;

  function row(r) {
    const now = r.now ? `<br><span class="muted">${esc(r.now)}</span>` : "";
    const note = r.note ? `<div class="muted">${esc(r.note)}</div>` : "";
    const gain = r.gain ? `${esc(r.gain)}${r.gain_pct ? ` <small>${esc(r.gain_pct)}</small>` : ""}` : "—";
    return `<tr><td>${esc(r.symbol)}${r.side === "SHORT" ? ' <span class="muted">short</span>' : ""}</td>
      <td>${esc(r.qty)}</td><td>${esc(r.bought)}${now}</td><td>${esc(r.worth || "—")}</td>
      <td class="${esc(r.dir)}">${gain}${note}</td></tr>`;
  }

  function group(g) {
    const table = g.rows.length ? `<table class="tbl"><thead><tr><th scope="col">Holding</th>
      <th scope="col">Size</th><th scope="col">Bought → now</th><th scope="col">Worth (A$)</th>
      <th scope="col">Gain</th></tr></thead><tbody>${g.rows.map(row).join("")}</tbody></table>` : "";
    return `<section class="panel group ${g.level === "act" ? "attn" : ""}">
      <h3>${esc(g.name)}${g.cash ? ` <span class="muted">· ${esc(g.cash)}</span>` : ""}</h3>
      <p class="why">${g.level === "act" ? "■ " : ""}${esc(g.why)}</p>
      ${g.caveat ? `<p class="caveat">${esc(g.caveat)}</p>` : ""}${table}</section>`;
  }

  function render(p) {
    return `<p class="prices muted">${esc(p.count)} · ${esc(p.prices.text)}</p>
      ${p.notes.map((n) => `<p class="note">${esc(n)}</p>`).join("")}
      ${p.groups.map(group).join("")}`;
  }

  MB.screens.bookPositions = {render};
})(window.MB);
```

- [ ] **Step 4: Let `boot()` read the link** (in `app.js`)

Directly before `await setAccount(S.account);` inside `boot()`, insert:

```javascript
  // Classic view from the new screens: ?account=<KEY>&tab=<TAB> opens this
  // book on that tab. Never in an export, whose book is baked in.
  if (!S.isExport && !S.exportAll) {
    const q = new URLSearchParams(location.search);
    const want = (q.get('account') || '').toUpperCase();
    if (accs.some(a => a.key === want)) S.account = want;
    const t = (q.get('tab') || '').toUpperCase();
    if (['OVERVIEW', 'POSITIONS', 'BACKTEST', 'METHOD', 'SWARM'].includes(t)) S.tab = t;
  }
```

(`setAccount` already loads the backtest or swarm data when `S.tab` is `BACKTEST` or `SWARM`; see `app.js` `setAccount`.)

- [ ] **Step 5: Run the browser tests and the terminal tests**

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py tests/test_dashboard_terminal.py tests/test_dashboard.py`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add trading_algo/dashboard/static tests/test_book_screens_browser.py
git commit -m "feat(home): a book's Positions screen; Classic view opens the same book and tab"
```

---

### Task 15: Accessibility checks, the CI browser job, and the docs

**Files:**
- Modify: `tests/test_book_screens_browser.py` (append); `.github/workflows/ci.yml` (new `ui` job)
- Modify (docs):
  - `trading_algo/dashboard/CLAUDE.md`, `trading_algo/home/CLAUDE.md`, `docs/README.md`;
  - `docs/specs/book-screens.md` (AC statuses);
  - `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md` (Resume block).

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the accessibility tests** (append)

```python
def _rgb(css):
    import re
    r, g, b = (int(x) for x in re.findall(r"\d+", css)[:3])
    return r, g, b


def _contrast(a, b):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(x) for x in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PANEL_BG = (19, 22, 30)      # --bg #0a0d14 under the 3.5% white panel, rounded up


def test_text_contrast_in_tiles_and_month_grid(browser, app):
    page, _ = open_book(browser, app, "FULL")
    for sel in (".tile .tv", ".tile .tc", ".month .mv", ".month .ml"):
        for colour in page.eval_on_selector_all(sel, "els => els.map(e => getComputedStyle(e).color)"):
            assert _contrast(_rgb(colour), PANEL_BG) >= 4.5, (sel, colour)


def test_every_money_move_has_an_arrow(browser, app):
    page, _ = open_book(browser, app, "FULL")
    # Worth and Costs are amounts; Since start and each closed result are moves.
    moves = page.eval_on_selector_all(
        ".tile", "els => els.filter(e => e.querySelector('.tl').innerText === 'Since start')"
                 ".map(e => e.querySelector('.tv').innerText)")
    moves += page.locator(".tbl td:last-child").all_inner_texts()
    assert moves and all(t.strip()[0] in "▲▼▶" for t in moves), moves
    assert page.inner_text(".result")[0] in "▲▼▶"


def test_focus_survives_a_refresh_and_only_the_status_is_live(browser, app):
    page, _ = open_book(browser, app, "FULL")
    page.focus('[role="tab"][data-tab="overview"]')
    page.evaluate("refresh(false)")
    page.wait_for_timeout(500)
    assert page.evaluate("document.activeElement.dataset.focus") == "tab:overview"
    live = page.eval_on_selector_all("[aria-live], [role=status]", "els => els.map(e => e.className)")
    assert all("pill" in c for c in live), live


def test_the_chart_has_a_text_alternative_and_patterned_lines(browser, app):
    page, _ = open_book(browser, app, "FULL")
    assert len(page.inner_text(".chart svg desc")) > 20
    assert page.get_attribute(".chart svg line", "stroke-dasharray") == "1 4"
```

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py`
Expected: PASS. If a contrast check fails, fix the colour in `home.css`, not the threshold.

- [ ] **Step 2: Add the CI browser job** (append under `jobs:` in `.github/workflows/ci.yml`, after the `test` job)

```yaml
  # Book screens in a real browser. A separate job so the main matrix stays
  # quick; REQUIRE_BROWSER makes a missing browser a failure, never a skip.
  ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt "playwright==1.62.0"
      - run: python -m playwright install --with-deps chromium
      - run: pytest tests/test_book_screens_browser.py
        env:
          REQUIRE_BROWSER: "1"
```

Run: `python3 -m pytest tests/test_workflow_sanity.py`
Expected: PASS

- [ ] **Step 3: Update the docs**

- `trading_algo/dashboard/CLAUDE.md`: add a short "Book screens" section. Cover:
  - the new modules and their one-line jobs (copy the File structure table rows);
  - the three routes;
  - the saved-values-only rule;
  - "change a rule → its unit test, then `UPDATE_GOLDEN=1` and read the diff".
- `trading_algo/home/CLAUDE.md`: one line saying `make_app` wires a `BookService`, whose background prices run only when the last sync was online.
- `docs/README.md`: change the `specs/book-screens.md` row status to "in progress: 1a + 1b built".
- `docs/specs/book-screens.md`: fill the Status column of AC-1…AC-18 and AC-20 with `✅ pass — <test>` from the runs above, and leave AC-19 as `☐ Matt`. Keep `status: draft` (Matt moves it).
- Design doc "Resume here": "Phase 1a + 1b built (date); waiting on Matt's 5-second test (AC-19); 1c (phone) next, its own plan."

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -p no:cacheprovider > /tmp/claude-suite.txt 2>&1; tail -3 /tmp/claude-suite.txt`. In the session, use the scratchpad instead of `/tmp`.
Expected: all pass. The count is 1500 plus the new tests. Some are skipped (browser tests without `REQUIRE_BROWSER`), and the backlog test is 1 xfailed until PR #102 is in the base.

Run: `REQUIRE_BROWSER=1 python3 -m pytest tests/test_book_screens_browser.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_book_screens_browser.py .github/workflows/ci.yml trading_algo/dashboard/CLAUDE.md \
        trading_algo/home/CLAUDE.md docs/README.md docs/specs/book-screens.md \
        docs/superpowers/specs/2026-10-09-mac-dashboard-design.md
git commit -m "test(home): accessibility checks and a CI browser job; docs for the book screens"
```

---

## Spec coverage (self-check)

| Spec item | Task |
|---|---|
| AC-1 routes, < 2 s offline, 404/500 | 11 |
| AC-2 pure summarise | 9 (`summarise` only reads `inp`); 4 (all I/O in `load_inputs`) |
| AC-3 one valuation point | 9 `test_one_valuation_point_across_result_tiles_and_chart` |
| AC-4 each rule each side | 5, 7, 8 |
| AC-5 caveats travel | 5, 7, 8 (items); 9 (summary first sentence; tiles captions) |
| AC-6 summary shape, order, pinned 8 books | 9 |
| AC-7 every WARN/ERROR shown | 7 (coverage rule); 9 (`test_every_audit_warning_or_error_appears`) |
| AC-8 main-problem ranking | 9 |
| AC-9 backlog ids | 9 (xfail until PR #102) |
| AC-10 glossary | 2, 9 |
| AC-11 metrics single source | 1, 9 (spy) |
| AC-12 positions why-lines | 7 (`sleeve_reason`), 10 |
| AC-13 states | 5 (new book), 9 (throwing input; FX no closed), 8 (synthetic backtest) |
| AC-14 scripts parse, no collisions, no money maths | 12 |
| AC-15 browser on 4 books, network off, CI | 13, 14, 15 |
| AC-16 navigation and keyboard | 13, 14 |
| AC-17 accessibility and formats | 2 (formats), 15 |
| AC-18 nothing old breaks | 12, 14, 15 (full suite) |
| AC-19 5-second test | Matt, after Task 15 |
| AC-20 full suite | 15 |

**Rulings made while planning** (recorded so an executor doesn't re-decide them):
- **No synthetic marker on books.** The saved books carry none, so AC-13's synthetic case is tested on the backtest's `synthetic` flag. The spec was amended on 2026-10-14.
- **No Sharpe cash rate stated.** The saved backtests don't record which risk-free convention their Sharpe used, so the line doesn't state one. The spec was amended.
- **Short-form caveat** means the caveat's first sentence. The spec was amended.
- **`dead-price-turnover` gains `detail["recent"]`.** This is additive, the same pattern as B1's `subject`. Level, message and gating are unchanged, so NG-7 ("no change to verify behaviour") holds.
- **No summary sentence for good news when there are no problems.** "Never more right sentences than problems" means none when there are no problems. The good news stays on the panel.
