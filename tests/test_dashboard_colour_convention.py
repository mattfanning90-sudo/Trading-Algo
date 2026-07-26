"""The dashboard's colour convention, pinned.

On this dashboard a colour is a CLAIM: green says "this went up", red says "this
went down", amber says "watch this". The reported bug was the TOTAL EQUITY tile
drawing a green sparkline under `TOTAL RETURN −59.32%` in red — the page
asserting a gain on a book that had lost 59% of its capital. That is a
correctness bug, not a cosmetic one, and it had fourteen siblings: hardcoded
greens on the backtest CAGR, both growth curves, the walk-forward fold Sharpe
and the BEST/WORST BOOK tiles, none of which any data could turn off.

The convention, in four rules:
  1. A NUMBER is coloured by its own sign.
  2. A CHART is coloured by the direction of the series it actually draws, over
     the window it actually draws.
  3. ADJACENCY must not contradict: the TOTAL EQUITY tile's sparkline sits
     beside TOTAL RETURN (since inception), so it draws the FULL history and
     takes that number's sign. The big range-explorer curve keeps rule 2 — it
     carries visible 1M/3M/ALL chips saying which window it shows.
  4. NEUTRAL when undefined: a flat, empty, single-point or unmeasurable series
     is neither up nor down.

There is no JS test runner in this repo and must not be one, so part 1 pins the
convention the way `test_fx_pnl.py` and `test_fx_dashboard_units.py` pin theirs
— by source inspection, asserting the offending expressions are gone and that
the rule has exactly ONE decision site. Part 2 pins the payload-level sign bugs
behaviourally, because a colour rule is only as honest as the number under it: a
raw NaN made the whole page unparseable, and an unflipped `day_change` made a
short's winning day render red.
"""
import inspect
import json
import math
import pathlib
import re

import pytest

import trading_algo.paper_trade as pt
from trading_algo import dashboard
from trading_algo.dashboard import api, fx_api, overview
from trading_algo.forex import fx_book

APP_JS = pathlib.Path(dashboard.__file__).parent / "static" / "app.js"
SRC = APP_JS.read_text(encoding="utf-8")

GREEN, RED = "#7ee787", "#ff7b72"
GREEN_RGBA = "rgba(126,231,135"


def _fn(name: str) -> str:
    """One top-level render function's source. All of them are declared at
    column zero, so the next `\\nfunction ` is the end."""
    start = SRC.index(f"function {name}(")
    end = SRC.find("\nfunction ", start + 1)
    return SRC[start:end if end != -1 else len(SRC)]


# ===========================================================================
# part 1 — the convention, pinned over static/app.js
# ===========================================================================
CHART_TAG = re.compile(r"<(?:polyline|polygon|circle|rect|path|line)\b[^>]*>")
STROKE_OR_FILL = re.compile(r'\b(?:stroke|fill)="([^"]*)"')


def test_no_chart_stroke_or_fill_is_a_hardcoded_green():
    """THE reported bug, generalised. Every stroke/fill on a chart primitive
    must be an expression, because a literal green cannot be turned off by a
    losing book — that is what put a rising green line under −59.32%."""
    offenders = []
    for tag in CHART_TAG.finditer(SRC):
        t = tag.group(0)
        # The candlestick renderer partitions its geometry into an up-path and a
        # down-path BEFORE it colours anything (candlePaths -> wickUp/bodyUp), so
        # green there is not a claim about data the colour cannot see.
        if "Up}" in t:
            continue
        for m in STROKE_OR_FILL.finditer(t):
            v = m.group(1)
            if "${" in v:                      # an expression — rules 2/3 below
                continue
            if GREEN in v or GREEN_RGBA in v:
                offenders.append(t[:110])
    assert not offenders, (
        "a chart is claiming 'up' in a colour no data can switch off:\n  "
        + "\n  ".join(offenders))


def test_the_direction_rule_has_exactly_one_decision_site():
    """The rule used to be written three different ways and not at all in four
    more places. One helper decides direction; everything else asks it."""
    assert SRC.count("const dirOf =") == 1
    assert SRC.count("const dirColor =") == 1
    assert SRC.count("const curveDir =") == 1
    assert SRC.count("const areaFill =") == 1
    assert SRC.count("const sideOf =") == 1
    # cSign is dirOf's colour, NOT a second copy of the rule
    assert "const cSign = v => dirColor(dirOf(v));" in SRC
    assert "v < 0 ? R : G" not in SRC, "the old two-branch rule is back"


def test_a_flat_or_unmeasurable_series_is_neutral():
    """Rule 4. `>= 0` sends a dead-flat book down the GREEN branch, and Python's
    round() emits −0.0, which `v < 0` cannot see — so a losing row printed
    '+0.00%' in green. Both need an explicit zero band."""
    assert "Math.abs(+v) <= EPS" in SRC
    assert "if (f.length < 2 || !f[0]) return 0;" in SRC   # one point is not a trend
    assert "dirOf(v) === 0 ? ''" in SRC, "sgn() still prints '+' on an exact zero"
    # a flat curve is drawn down the MIDDLE of its box; `|| 1` pinned it to the
    # BOTTOM edge — a shape claiming 'at its low' for a book that had not moved
    assert "(rng ? 1 - (v - min) / rng : 0.5)" in SRC
    assert "rng = (max - min) || 1" not in SRC
    # the equity screen's own scale (shared with the benchmark line) had the
    # same fallback, so a flat book drew along the bottom there while its tile
    # sparkline — the same series — drew down the middle, one panel apart
    assert "(hi > lo ? (v - lo) / (hi - lo) : 0.5)" in SRC
    assert "(v - lo) / ((hi - lo) || 1)" not in SRC


def test_total_equity_tile_sparkline_cannot_contradict_total_return():
    """Rule 3, the exact reported contradiction. This sparkline has no range
    chips and sits beside a since-inception number, so it draws the whole
    history and borrows that number's sign.

    Rules 2 and 3 only agree if the DRAWN series is the one that number
    measures: the history alone runs first mark → last SCHEDULED mark, while
    TOTAL RETURN runs initial capital → the live mark printed above it. FULL
    drew a falling green line under +92.03% on exactly that gap, so the series
    is anchored at both ends."""
    fn = _fn("equityOverviewHTML")
    assert "M.curve.map(p => p.v)" in fn                    # FULL history...
    assert "anchor(k.initial_capital)" in fn                # ...from capital...
    assert "anchor(k.total_equity)" in fn                   # ...to the live mark
    assert "toPts(allVals" in fn                            # ...is what it draws
    assert "dirOf(k.total_return)" in fn                    # ...and what it claims
    assert "rangeFilter" not in fn.split("const eqSpark")[1].split("\n")[0]


def test_drawdown_from_peak_means_the_books_own_peak_on_both_subsystems():
    """The panel is labelled DRAWDOWN FROM PEAK with no qualifier. Building it
    from the RANGE-FILTERED curve made 'PEAK' the visible window's local high:
    a book 17.5% underwater printed 'NOW −5.89% · WORST −5.89%' under a tape
    reading OFF-PEAK −17.50%, on one screen. The FX twin always read against
    the real high-water mark; the equity screen now does too.

    And red is the loss colour: it must not claim 'underwater' on a book that
    has never traded below a prior peak — the panel prints WORST 0.00% and the
    episodes table below it says NO DRAWDOWN YET."""
    fn = _fn("equityOverviewHTML")
    assert "ddSeries(M.curve.map(p => p.v)).slice(" in fn
    assert "ddSeries(vals)" not in fn
    for f in ("equityOverviewHTML", "agentCurveAttrHTML"):
        src = _fn(f)
        assert "const ddDeep = ddMin < -EPS;" in src
        assert 'stroke="#ff7b72" stroke-width="1.2"' not in src
        assert 'points="${ddArea}" fill="rgba(255,123,114,0.18)"' not in src


def test_the_accounts_card_spark_is_coloured_by_the_return_printed_above_it():
    """DAYTRADER drew a green line under '−2.57%' in red, 30px apart. The card
    has no range chips, so rule 3 applies: the line answers to the number. And
    because `spark` was the last 40 marks measured from the first one while
    `ret` runs capital → today, obeying rule 3 alone still left a book up 51.4%
    drawing a FALLING green spark — so the payload now draws what ret measures."""
    fn = _fn("allAccountsHTML")
    assert "dirOf(c.ret)" in fn
    src = inspect.getsource(overview._card)
    assert '[v for _, v in hist[-40:]]' not in src   # not just the last 40 marks
    assert "[initial] + pts" in src                  # anchored where ret is


def test_the_accounts_card_spark_draws_the_return_it_is_coloured_by():
    """The sampled spark must keep both ends and start at capital, or the drawn
    direction can still contradict `ret`."""
    hist = [(f"2026-01-{i + 1:02d}", 100.0 + i) for i in range(120)]
    state = {"initial_capital_base": 90.0, "equity_history": hist, "sleeves": {}}
    entry = {"account": "x", "key": "X", "kind": "equity",
             "label": "X · EQUITIES", "sub": "", "micro": False}
    card = overview._card(entry, state)
    spark = card["spark"]
    assert len(spark) == 41                   # capital + 40 sampled marks
    assert spark[0] == 90.0                   # ...anchored at initial capital
    assert spark[-1] == hist[-1][1] == card["equity"]   # ...ending on the mark
    # direction drawn == sign of the number it is coloured by
    assert (spark[-1] > spark[0]) == (card["ret"] > 0)


DEAD_CLAIMS = {
    'points="${eqPts140}" fill="none" stroke="#7ee787"':
        "the equity curve was green whether the book made or lost money",
    '<circle cx="600" cy="${eqLastY}" r="3" fill="#7ee787">':
        "the 'you are here' endpoint dot was green on a book down 59%",
    'points="${eqSpark}" fill="none" stroke="#7ee787"':
        "the TOTAL EQUITY tile sparkline — the reported bug",
    'fill="rgba(126,231,135,0.08)"':
        "equity curve area fill hardcoded as gain",
    'fill="rgba(126,231,135,0.06)"':
        "GROWTH OF A$100,000 drew a green line over a losing run",
    "vals.length > 1 ? vals[vals.length - 1] >= vals[0] : true":
        "the FX curve returned literal true for a one-point book",
    "s[s.length - 1] >= s[0]":
        "the 90-day popover called a perfectly flat series up",
    "const down = k.total_return < 0":
        "SMALL's drawn curve took a since-inception sign",
    "c.spark[c.spark.length - 1] >= c.spark[0]":
        "the accounts card coloured a 40-mark spark against an inception return",
    "t === 'bad' ? R : t === 'warn' ? AMB : G":
        "an absent status_tone fell through to green and asserted health",
    "ALLOC_COLORS":
        "the capital-share ribbon spent the two P&L colours as identity colours",
    "ALLOC_FALLBACK":
        "...and so did its fallback",
    "k.gross_exposure > 1.001 ? R : '#e3b341'":
        "the GROSS EXPOSURE tile had an amber FLOOR, so a 7% cash book was flagged",
    "actual) > parseFloat(e.estimate) ? G":
        "a hot CPI print was green because 'actual > estimate'",
    "wSrc[i] / wMax":
        "agent weight bars were normalised to the largest weight, so 21% filled 84%",
    "(fp.max_drawdown_stop || page.breaker || 0.2)":
        "invented a −20% drawdown stop for a profile that has none",
}


@pytest.mark.parametrize("dead,why", sorted(DEAD_CLAIMS.items()))
def test_a_false_claim_stays_dead(dead, why):
    assert dead not in SRC, f"regressed: {why}"


def test_one_threshold_and_one_colour_for_each_shared_claim():
    """The same quantity must not be amber on one screen and neutral on the
    next. Off-peak had four thresholds (−2%, −2%, −1%, 0) for one claim, and a
    cost rendered in four colours — ULTRA printed the same A$5 white in a tile
    and red in the block below it."""
    assert SRC.count("const OFF_PEAK_WARN =") == 1
    assert SRC.count("const COST =") == 1
    for stale in ("off_peak < -0.02", "off_peak < -0.01", "ddNow < -0.02",
                  "page.off_peak < 0 ?", "k.fees_base ? R :"):
        assert stale not in SRC, f"a private threshold/colour came back: {stale}"


def test_a_zero_weight_leg_is_not_long():
    """'▲ LONG 0.0%' in green is a claim about a leg that is not on. The glyph
    pair lives in sideOf and nowhere else, so no site can re-derive it."""
    assert "'— FLAT'" in SRC
    assert SRC.count("'▲ LONG'") == 1 and SRC.count("'▼ SHORT'") == 1
    assert "weight >= 0 ? '▲ LONG'" not in SRC


def test_the_allocation_ramp_is_categorical():
    """A share-of-capital segment makes no direction claim, so it must not wear
    a direction colour: SMALL's 0.8% sliver read as an alert next to a green
    block that read as profit."""
    ramp = re.search(r"const ALLOC_RAMP = \[([^\]]*)\]", SRC).group(1)
    assert GREEN not in ramp and RED not in ramp


def test_an_invented_number_does_not_wear_a_measured_colour():
    """Invariant #5: synthetic results are pipeline tests, never performance.
    The FX tab already ambered its illustrative CAGR; the equity tab printed a
    --synthetic CAGR in full green under an amber warning banner."""
    fn = _fn("backtestHTML")
    assert "bt.synthetic ? AMB : cSign(m.cagr)" in fn
    assert "cagrTone" in fn                       # the per-sleeve + COMBINED rows share it


def test_a_switched_off_control_is_not_drawn_as_a_live_one():
    """The METHOD pipeline printed 'DISABLED — NO REGIME GATE' in the same green
    box as the five working steps, while the RISK CONTROLS panel on the SAME
    screen ambers its disabled breaker with a ⚠."""
    fn = _fn("methodHTML")
    assert "off: true" in fn and "st.off ? AMB : G" in fn


# ===========================================================================
# part 2 — the payload under the colour
# ===========================================================================
@pytest.fixture()
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


# ---- a mark the book could not be valued on is None, never NaN -------------
def test_num_keeps_only_finite_numbers():
    assert api._num(float("nan")) is None
    assert api._num(float("inf")) is None
    assert api._num(None) is None
    assert api._num("not a number") is None
    assert api._num(1.23456, 2) == 1.23
    assert overview._num(float("nan")) is None
    assert overview._num(3.0) == 3.0


def test_a_nan_mark_cannot_make_the_payload_invalid_json(books):
    """`state/paper_state_full.json` carries four unvaluable marks today.
    json.dumps emits a BARE `NaN` token, which is not JSON: the browser's
    JSON.parse throws, loadJSON swallows it and returns null, and the account
    page renders empty with no error shown. app.js already guards with
    Number.isFinite on the assumption that a NaN 'reaches the browser as null' —
    this is what makes that assumption true."""
    pt.init_account("full", capital=300_000, synthetic=True)
    pt.run_daily("full", synthetic=True)
    state = pt.load_state("full")
    state["equity_history"].append(["2026-07-21", float("nan")])
    state["sleeve_history"].append({"date": "2026-07-21", "US": float("nan")})
    pt.save_state("full", state)

    snap = api.build_snapshot("full", synthetic=True)
    json.dumps(snap, allow_nan=False)          # raises if any NaN survived
    assert snap["equity_curve"][-1] == {"date": "2026-07-21", "equity": None}
    assert snap["sleeve_curves"][-1]["US"] is None
    # ...and the analytics blocks that read the same history
    assert (snap["promotion"] or {}).get("equity") is None
    # the DAY tile is measured against the last mark the book could be VALUED on,
    # not against a NaN (which poisoned it) nor against initial capital (which
    # would report the whole book's return as "today")
    assert api._num(snap["kpis"]["day_change"]) is not None


def test_an_unmeasurable_sleeve_month_says_so_instead_of_reporting_flat():
    """0.0 reads as 'flat' — a measurement nobody made — and app.js coloured
    that green. `h.get(key)` also admitted NaN (truthy) while DROPPING a
    legitimately-zero mark (falsy)."""
    assert api._month_return([{"date": "2026-06-30", "US": 100.0},
                              {"date": "2026-07-02", "US": float("nan")}], "US") is None
    assert api._month_return([{"date": "2026-07-02", "US": 100.0}], "US") is None
    assert api._month_return([], "US") is None
    # a real zero mark is a measurement and must survive the filter
    assert api._month_return([{"date": "2026-06-30", "US": 100.0},
                              {"date": "2026-07-02", "US": 0.0}], "US") == -1.0


def test_an_unmeasurable_book_is_excluded_from_the_day_total_and_counted(books):
    """`nan > -1` is False, so FULL — 77% of core AUM — contributed EXACTLY ZERO
    to a headline that still read as a complete day P&L, and `nan < 0` is False
    so it counted as not-red."""
    good = {"account": "aa", "currency": "AUD", "profile": "balanced", "bar": "1d",
            "initial_capital": 5000.0, "equity": 4950.0, "positions": {},
            "last_close": {}, "peak_equity": 5000.0, "trades": [], "decisions": {},
            "equity_history": [["2026-07-01", 5000.0], ["2026-07-04", 4950.0]]}
    blind = dict(good, account="bb", equity=4900.0,
                 equity_history=[["2026-07-01", 5000.0],
                                 ["2026-07-04", float("nan")]])
    for st in (good, blind):
        with open(fx_book._state_file(st["account"]), "w") as f:
            json.dump(st, f)

    ov = overview.build_overview()
    json.dumps(ov, allow_nan=False)            # one NaN killed the WHOLE screen
    cards = {c["key"]: c for c in ov["accounts"]}
    assert cards["AA"]["day"] == pytest.approx(-0.01)
    assert cards["BB"]["day"] is None, "unmeasurable must not be reported as flat"
    assert all(math.isfinite(v) for v in cards["BB"]["spark"])

    t = ov["totals"]
    assert (t["books"], t["day_books"], t["books_red"]) == (2, 1, 1)
    assert t["day_aud"] == pytest.approx(4950.0 * -0.01 / 0.99, abs=0.01)


def test_off_peak_can_never_be_positive(books):
    """A running peak includes the current mark. paper_trade only persists it on
    a scheduled run while the dashboard re-marks live, so the equity page
    rendered 'OFF-PEAK +89.96%' in green — impossible for a real book."""
    st = {"account": "cc", "currency": "AUD", "profile": "balanced", "bar": "1d",
          "initial_capital": 5000.0, "equity": 4900.0, "positions": {},
          "last_close": {}, "peak_equity": 100.0,      # stale, far below the mark
          "trades": [], "decisions": {},
          "equity_history": [["2026-07-04", 4900.0]]}
    with open(fx_book._state_file("cc"), "w") as f:
        json.dump(st, f)
    assert fx_api.build_fx_snapshot("cc")["off_peak"] <= 0.0

    pt.init_account("full", capital=300_000, synthetic=True)
    pt.run_daily("full", synthetic=True)
    state = pt.load_state("full")
    state["peak_equity_base"] = 1.0
    pt.save_state("full", state)
    assert api.build_snapshot("full", synthetic=True)["off_peak"] <= 0.0


# ---- the DAY column describes the POSITION, not the instrument -------------
def test_day_change_is_the_positions_move_not_the_instruments(books):
    """The bug already fixed for `unrealized_pct` three lines below, whose own
    comment describes it ('a short marked +44% green next to −A$202 red'). The
    fix never reached the column to its left, so EXPERIMENTAL rendered 6 of 7
    rows with DAY the opposite colour to the money in the same row."""
    pt.init_account("experimental", capital=10_000, synthetic=True,
                    profile="experimental")
    pt.run_daily("experimental", synthetic=True)
    snap = api.build_snapshot("experimental", synthetic=True)
    rows = [p for s in snap["sleeves"] for p in s["positions"]]
    shorts = [p for p in rows if p["shares"] < 0]
    assert shorts, "expected a hedged book to hold shorts"
    for p in rows:
        prev = p["price"] - p["change_local"]
        instrument = p["price"] / prev - 1.0 if prev else 0.0
        expect = -instrument if p["shares"] < 0 else instrument
        assert p["day_change"] == pytest.approx(expect, abs=5e-5), p["ticker"]


def test_fx_day_move_is_the_positions_move_not_the_pairs(books):
    """fx_book stores it as `"move": ... # the pair's own move`, so a SHORT on a
    pair that ROSE showed DAY green beside OPEN P&L red in the same row."""
    st = {"account": "dd", "currency": "AUD", "profile": "balanced", "bar": "1d",
          "symbols": ["EURUSD", "GBPUSD"], "initial_capital": 5000.0,
          "equity": 4900.0, "positions": {"EURUSD": -0.10, "GBPUSD": 0.10},
          "last_close": {"EURUSD": 1.144, "GBPUSD": 1.27},
          "peak_equity": 5000.0, "decisions": {},
          "trades": [{"date": "2026-06-20", "pair": "EURUSD", "side": "SELL",
                      "delta_weight": -0.10, "target_weight": -0.10, "price": 1.15,
                      "why": "", "regime": "trending", "agents": {}},
                     {"date": "2026-06-20", "pair": "GBPUSD", "side": "BUY",
                      "delta_weight": 0.10, "target_weight": 0.10, "price": 1.26,
                      "why": "", "regime": "trending", "agents": {}}],
          "equity_history": [["2026-07-01", 5000.0], ["2026-07-04", 4900.0]],
          "daily": {"date": "2026-07-04", "net_pct": -0.02, "net_aud": -100.0,
                    "by_pair": [{"pair": "EURUSD", "weight": -0.10, "move": 0.002},
                                {"pair": "GBPUSD", "weight": 0.10, "move": 0.002}]}}
    with open(fx_book._state_file("dd"), "w") as f:
        json.dump(st, f)
    moves = {r["pair"]: r["day_move"]
             for r in fx_api.build_fx_snapshot("dd")["positions_money"]}
    # the pair rose 0.2% either way; only the LONG made money on it
    assert moves["EURUSD"] == pytest.approx(-0.002)
    assert moves["GBPUSD"] == pytest.approx(0.002)


# ---- a closed round-trip says which way round it was ----------------------
SHORT_TRIP = [
    {"date": "2026-01-05", "region": "US", "ticker": "ZZZ", "currency": "USD",
     "side": "SELL", "shares": 10, "fill": 120.0, "commission": 1.0},
    {"date": "2026-02-05", "region": "US", "ticker": "ZZZ", "currency": "USD",
     "side": "BUY", "shares": 10, "fill": 100.0, "commission": 1.0},
]
LONG_TRIP = [
    {"date": "2026-01-05", "region": "US", "ticker": "AAA", "currency": "USD",
     "side": "BUY", "shares": 5, "fill": 50.0, "commission": 1.0},
    {"date": "2026-03-05", "region": "US", "ticker": "AAA", "currency": "USD",
     "side": "SELL", "shares": 5, "fill": 60.0, "commission": 1.0},
]


def test_closed_trades_name_the_side_they_were():
    """'120.00 → 100.00 · +$198 · +16.5%' all in green is a gain on a price that
    FELL, and read as a data error until the row said SHORT. pnl._close knows
    this exactly as `lot_dir` and then discards it."""
    rows = api.closed_trades(SHORT_TRIP + LONG_TRIP, {"USD": 1.0})["rows"]
    by_ticker = {r["ticker"]: r for r in rows}
    short = by_ticker["ZZZ"]
    assert short["side"] == "SHORT"
    assert short["exit"] < short["entry"] and short["net"] > 0   # fell AND gained
    assert by_ticker["AAA"]["side"] == "LONG"


def test_a_round_trip_with_no_move_claims_no_side():
    """Nothing to infer from, so the column em-dashes rather than guessing."""
    flat = [dict(SHORT_TRIP[0], commission=0.0),
            dict(SHORT_TRIP[1], fill=120.0, commission=0.0)]
    assert api.closed_trades(flat, {})["rows"][0]["side"] is None


# ---- an unpriced holding is unpriced, not worthless -----------------------
def test_an_unpriced_holding_is_held_at_cost_not_marked_to_zero(books):
    """data.load_region does not forward-fill and delisting.py deliberately
    terminates a dead series, so a NaN tail on a held name is a DESIGNED state.
    _safe_price's 0.0 then printed '−100.00%' on the row and quietly subtracted
    the whole cost basis from TOTAL EQUITY."""
    src = inspect.getsource(api.build_snapshot)
    assert "priced = mark > 0.0" in src
    assert "price = mark if priced else avg" in src
    pt.init_account("full", capital=300_000, synthetic=True)
    pt.run_daily("full", synthetic=True)
    snap = api.build_snapshot("full", synthetic=True)
    rows = [p for s in snap["sleeves"] for p in s["positions"]]
    assert rows and all("priced" in p for p in rows), "the screen cannot em-dash what it is not told"
