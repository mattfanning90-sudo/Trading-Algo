"""Backlog F7 / foundation P0-D: the pre-signal data-quality gate.

Covers the acceptance criteria: region-aware impossible-move detection, staleness
and gap exclusion, no-lookahead, composition with point-in-time membership, and
the perfect no-op behaviour on clean data / when the gate is off.
"""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import data_quality
from trading_algo.regions import get_region


def _clean_frame(n=60, cols=("A", "B", "C")):
    idx = pd.bdate_range("2023-01-02", periods=n)
    data = {c: 100 * (1 + 0.001 * (i + 1)) ** np.arange(n) for i, c in enumerate(cols)}
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def us():
    return get_region("US")


@pytest.fixture
def ftse():
    return get_region("FTSE")


# --- currency mismatch (quote_overrides) -----------------------------------
def test_currency_mismatch_is_reported_not_silently_rescaled(ftse):
    """A London name quoted in USD is REJECTED, never divided by 100.

    The name still needs its own price scale so any existing holding marks
    correctly, but it must not be selected into a GBP sleeve while its quote
    currency disagrees with the sleeve's (invariant #6).
    """
    df = _clean_frame(cols=("CPG.L", "IHG.L", "HSBA.L"))
    report = data_quality.assess(df, ftse, df.index[-1])
    assert report.excluded == {"CPG.L", "IHG.L"}
    assert "currency" in report.reasons["CPG.L"]
    assert "HSBA.L" not in report.excluded


def test_load_region_does_not_divide_a_usd_quoted_name_by_100(monkeypatch, ftse):
    """The price path applies the per-TICKER scale, not one blanket region scale."""
    import pandas as pd

    from trading_algo import data

    idx = pd.bdate_range("2026-06-01", periods=5)
    frame = pd.DataFrame({"CPG.L": 2400.0, "HSBA.L": 900.0, "^FTSE": 8000.0},
                         index=idx)
    monkeypatch.setattr(data, "load_prices",
                        lambda *a, **k: frame.copy())
    prices, index_px = data.load_region(ftse, "2026-06-01",
                                        tickers=["CPG.L", "HSBA.L"])
    assert prices["HSBA.L"].iloc[-1] == 9.0          # 900 GBp -> £9
    assert prices["CPG.L"].iloc[-1] == 2400.0        # USD, untouched by /100


# --- clean data is never flagged -------------------------------------------
def test_clean_frame_flags_nothing(us):
    df = _clean_frame()
    report = data_quality.assess(df, us, df.index[-1])
    assert report.excluded == set()


def test_eligible_is_noop_on_clean_data(us):
    df = _clean_frame()
    elig, report = data_quality.eligible(df, us, df.index[-1])
    assert elig is None          # base (None) passes through unchanged
    assert report.excluded == set()


# --- individual checks ------------------------------------------------------
def test_staleness_flagged(us):
    df = _clean_frame()
    df.iloc[-8:, df.columns.get_loc("B")] = 123.0   # frozen feed
    report = data_quality.assess(df, us, df.index[-1])
    assert "B" in report.excluded and "stale" in report.reasons["B"]


def test_gap_flagged(us):
    df = _clean_frame()
    df.iloc[-6:-1, df.columns.get_loc("C")] = np.nan  # 5 missing in trailing window
    report = data_quality.assess(df, us, df.index[-1])
    assert "C" in report.excluded and "gappy" in report.reasons["C"]


def test_dead_price_flagged(us):
    df = _clean_frame()
    df.iloc[-1, df.columns.get_loc("A")] = 0.0
    report = data_quality.assess(df, us, df.index[-1])
    assert "A" in report.excluded


def test_impossible_move_is_region_aware(us, ftse):
    df = _clean_frame()
    # a +40% one-day jump: within US's 50% threshold, beyond FTSE's 30%
    df.iloc[-1, df.columns.get_loc("A")] = df.iloc[-2, df.columns.get_loc("A")] * 1.40
    assert "A" not in data_quality.assess(df, us, df.index[-1]).excluded
    assert "A" in data_quality.assess(df, ftse, df.index[-1]).excluded


def test_huge_jump_flagged_everywhere(us):
    df = _clean_frame()
    df.iloc[-1, df.columns.get_loc("A")] = df.iloc[-2, df.columns.get_loc("A")] * 3.0
    report = data_quality.assess(df, us, df.index[-1])
    assert "A" in report.excluded and "impossible move" in report.reasons["A"]


# --- no lookahead -----------------------------------------------------------
def test_future_bad_print_does_not_flag_at_asof(us):
    df = _clean_frame()
    asof = df.index[-5]
    df.iloc[-1, df.columns.get_loc("A")] = df.iloc[-2, df.columns.get_loc("A")] * 5.0
    # the spike is AFTER asof, so it must not be visible at asof
    assert "A" not in data_quality.assess(df, us, asof).excluded


# --- composition + gate switch ---------------------------------------------
def test_eligible_intersects_with_base_membership(us):
    df = _clean_frame()
    df.iloc[-8:, df.columns.get_loc("B")] = 123.0   # flag B
    elig, _ = data_quality.eligible(df, us, df.index[-1], base={"A", "B"})
    assert elig == {"A"}                              # B removed from the base set


def test_gate_off_is_a_noop(us, monkeypatch):
    df = _clean_frame()
    df.iloc[-8:, df.columns.get_loc("B")] = 123.0   # would flag B if gate were on
    monkeypatch.setattr(cfg, "DATA_QUALITY_GATE", False)
    elig, report = data_quality.eligible(df, us, df.index[-1], base={"A", "B"})
    assert elig == {"A", "B"} and report.excluded == set()


# --- integration: both engines drop the bad name ---------------------------
def test_backtest_excludes_flagged_name(us):
    from trading_algo import data
    from trading_algo.backtest import run_backtest
    prices, index_px = data.synthetic_region(us)
    # freeze one name for the whole history so it is always stale
    victim = prices.columns[0]
    prices[victim] = float(prices[victim].iloc[0])
    result = run_backtest(prices, index_px, us, max_drawdown_stop=None)
    assert victim in result["data_quality_excluded"]


def test_paper_freezes_held_flagged_name(us):
    """AC4: a held name that is flagged holds its prior weight (no trade)."""
    from trading_algo import paper_trade
    sleeve = {"currency": "USD", "cash": 100_000.0, "positions": {"AAA": 10},
              "cost_basis": {"AAA": 100.0}, "realized_pnl": 0.0}
    px = pd.Series({"AAA": 100.0})
    trades: list = []
    # empty targets would normally sell AAA to cash; frozen must hold it.
    paper_trade.rebalance_sleeve(us, sleeve, pd.Series(dtype=float), px,
                                 "2026-06-01", trades, frozen={"AAA"})
    assert sleeve["positions"]["AAA"] == 10
    assert trades == []
    # sanity: without the freeze the same setup DOES exit the position
    sleeve2 = {"currency": "USD", "cash": 100_000.0, "positions": {"AAA": 10},
               "cost_basis": {"AAA": 100.0}, "realized_pnl": 0.0}
    paper_trade.rebalance_sleeve(us, sleeve2, pd.Series(dtype=float), px,
                                 "2026-06-01", [])
    assert "AAA" not in sleeve2["positions"]


# --- near-frozen feeds ------------------------------------------------------
def _frozen_series(n=80, levels=(5.835, 5.840), run=3):
    """A feed that technically ticks but carries almost no information: it
    oscillates between a couple of levels every `run` bars. Never trips the
    staleness check (no STALE_DAYS+1 identical closes in a row)."""
    vals = [levels[(i // run) % len(levels)] for i in range(n)]
    return np.array(vals, dtype=float)


def test_near_frozen_feed_is_flagged(ftse):
    """A price that barely moves must not reach the weighter.

    Inverse-vol weighting rewards low measured volatility, so a degraded feed
    earns the LARGEST position and, because it drags the sleeve's vol estimate
    down, the maximum vol-target leverage on top. The staleness check only sees
    exactly-identical runs, so an oscillating dead feed walks straight through.
    """
    idx = pd.bdate_range("2023-01-02", periods=80)
    df = pd.DataFrame({
        "GOOD": 100 * (1 + 0.02 * np.sin(np.arange(80) / 3.0)),
        "FROZEN": _frozen_series(80),
    }, index=idx)

    report = data_quality.assess(df, ftse, df.index[-1])

    assert "FROZEN" in report.excluded
    assert "near-frozen" in report.reasons["FROZEN"]
    assert "GOOD" not in report.excluded


def test_low_volatility_but_moving_feed_is_not_flagged(us):
    """A genuinely calm instrument (a short-duration bond ETF) is not a broken
    feed: its closes still change every day. Only the conjunction of 'barely any
    distinct closes' AND 'implausibly low vol' means the data is dead."""
    idx = pd.bdate_range("2023-01-02", periods=80)
    rng = np.random.default_rng(7)
    # ~3% annualised: every close distinct, just a very calm instrument
    steps = rng.normal(0, 0.03 / np.sqrt(252), 80)
    df = pd.DataFrame({"BOND": 100 * np.exp(np.cumsum(steps))}, index=idx)

    report = data_quality.assess(df, us, df.index[-1])

    assert report.excluded == set()


def test_coarse_tick_but_volatile_feed_is_not_flagged(us):
    """A low-priced stock on a coarse tick grid repeats closes often, but it is
    genuinely moving. Volatility is what separates it from a dead feed."""
    idx = pd.bdate_range("2023-01-02", periods=80)
    rng = np.random.default_rng(3)
    walk = 3.0 + np.cumsum(rng.choice([-0.02, 0.02], size=80))
    df = pd.DataFrame({"PENNY": np.round(walk, 2)}, index=idx)

    report = data_quality.assess(df, us, df.index[-1])

    assert report.excluded == set()


def test_near_frozen_feed_is_flagged_despite_a_gap(ftse):
    """The frozen check must survive a hole in the feed.

    The trailing scan window is read as a fixed block, so if it is sized exactly
    to the frozen window a single missing print leaves one valid close too few
    and the check silently never runs — for every name. It needs headroom.
    """
    n = 200
    idx = pd.bdate_range("2023-01-02", periods=n)
    frozen = _frozen_series(n)
    df = pd.DataFrame({
        "GOOD": 100 * (1 + 0.02 * np.sin(np.arange(n) / 3.0)),
        "FROZEN": frozen,
    }, index=idx)
    # one missing print, outside the gap window so only the frozen check applies
    df.iloc[-30, df.columns.get_loc("FROZEN")] = np.nan

    report = data_quality.assess(df, ftse, df.index[-1])

    assert "FROZEN" in report.excluded
    assert "near-frozen" in report.reasons["FROZEN"]


# ---------------------------------------------------------------------------
# WHOLE-PANEL staleness — the failure the per-name gate cannot see
# ---------------------------------------------------------------------------
# data_quality judges names against each other, so if EVERY name in a region
# stops printing on the same day nothing looks anomalous: the panel is
# internally consistent, just frozen. The sleeve then de-risks to cash on a
# price nobody is quoting, forever, in silence. The live ASX sleeve spent 57
# days in exactly that state before anyone looked.
def _panel_for(region, last_day, periods=300):
    idx = pd.bdate_range(end=last_day, periods=periods)
    cols = [*region.universe, region.index_ticker]
    return pd.DataFrame({c: 100.0 for c in cols}, index=idx)


def _capture_alerts(monkeypatch):
    from trading_algo import notifications
    got = []
    notifications.register_channel("_cap", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "_cap")
    return got


def test_a_stale_region_panel_raises_an_alert(monkeypatch):
    from trading_algo import data
    region = get_region("ASX")
    got = _capture_alerts(monkeypatch)
    stale = pd.Timestamp.now().normalize() - pd.Timedelta(days=60)
    monkeypatch.setattr(data, "load_prices",
                        lambda *a, **k: _panel_for(region, stale))
    data.load_region(region, "2024-01-01")
    events = [p["event"] for p in got]
    assert "stale_panel" in events
    payload = next(p for p in got if p["event"] == "stale_panel")
    assert payload["region"] == "ASX"
    assert payload["age_days"] >= 60
    assert payload["level"] == "alert"


def test_a_current_region_panel_is_silent(monkeypatch):
    from trading_algo import data
    region = get_region("ASX")
    got = _capture_alerts(monkeypatch)
    fresh = pd.Timestamp.now().normalize()
    monkeypatch.setattr(data, "load_prices",
                        lambda *a, **k: _panel_for(region, fresh))
    data.load_region(region, "2024-01-01")
    assert [p for p in got if p["event"] == "stale_panel"] == []


def test_a_closed_backtest_window_is_not_judged_stale(monkeypatch):
    """`end` given = a deliberate historical window. Of course it is old; that
    is the request, not a fault."""
    from trading_algo import data
    region = get_region("ASX")
    got = _capture_alerts(monkeypatch)
    old = pd.Timestamp("2020-06-30")
    monkeypatch.setattr(data, "load_prices",
                        lambda *a, **k: _panel_for(region, old))
    data.load_region(region, "2019-01-01", "2020-06-30")
    assert [p for p in got if p["event"] == "stale_panel"] == []


# ---------------------------------------------------------------------------
# missing_names — a ticker that never loaded at all (no column, not a bad one)
# ---------------------------------------------------------------------------
# `assess` judges COLUMNS, so it is blind to a name that has none: data.py's
# load_prices ends with dropna(axis=1, how="all"), so a delisted, renamed or
# mistyped ticker in universes.py loses its column and simply ceases to exist.
# A broken universe then reads as a smaller universe, not a broken one.
@pytest.fixture
def captured_alerts(monkeypatch):
    from trading_algo import notifications
    got = []
    notifications.register_channel("cap", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "cap")
    return got


def test_missing_names_reports_a_ticker_that_never_loaded(us, captured_alerts):
    """`assess` judges COLUMNS, so a ticker whose download failed is invisible
    to it — data.load_prices drops the all-NaN column and the name ceases to
    exist. A delisted or mistyped symbol in universes.py then looks like a
    smaller universe rather than a broken one."""
    df = _clean_frame(cols=("A", "B"))
    report = data_quality.missing_names(us, df, ["A", "B", "GONE", "ALSOGONE"])
    assert report.excluded == {"GONE", "ALSOGONE"}
    assert "no data" in report.reasons["GONE"]
    alerts = [p for p in captured_alerts if p["event"] == "dead_tickers"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "alert"
    assert sorted(alerts[0]["tickers"]) == ["ALSOGONE", "GONE"]


def test_missing_names_is_silent_when_everything_loaded(us, captured_alerts):
    df = _clean_frame(cols=("A", "B"))
    report = data_quality.missing_names(us, df, ["A", "B"])
    assert report.excluded == set()
    assert not [p for p in captured_alerts if p["event"] == "dead_tickers"]


def test_load_region_reports_the_names_it_dropped(monkeypatch, us, captured_alerts):
    """The drop happens inside load_prices; load_region is where the region is
    known, so that is where it is reported."""
    import pandas as pd

    from trading_algo import data

    idx = pd.bdate_range("2026-06-01", periods=5)
    # "DEAD" was requested but never came back.
    frame = pd.DataFrame({"AAPL": 200.0, "^GSPC": 5000.0}, index=idx)
    monkeypatch.setattr(data, "load_prices", lambda *a, **k: frame.copy())
    data.load_region(us, "2026-06-01", tickers=["AAPL", "DEAD"])
    alerts = [p for p in captured_alerts if p["event"] == "dead_tickers"]
    assert len(alerts) == 1 and alerts[0]["tickers"] == ["DEAD"]
    assert alerts[0]["region"] == "US"


# ---------------------------------------------------------------------------
# A suspected split on a held name. Yahoo back-adjusts the WHOLE history for a
# split on every download (`auto_adjust=True`), so a split never shows as a jump
# inside the series — NVDA's 10-for-1 on 2024-06-10 reads 120.54 -> 121.44
# across it. What changes is the past: the close on the day the book traded no
# longer matches the price the book recorded. So these tests rewrite history
# the way Yahoo does and compare it with the book's own fills.
def _bought(ticker, prices, i=-10, shares=100):
    """A ledger BUY of `ticker` at the close the book saw on bar `i`."""
    close = float(prices[ticker].iloc[i])
    return {"date": str(prices.index[i].date()), "region": "US", "ticker": ticker,
            "side": "BUY", "shares": shares, "decision": round(close, 4),
            "fill": round(close * 1.0005, 4), "commission": 1.0,
            "stamp_duty": 0.0, "currency": "USD"}


def test_suspected_split_matches_a_known_factor_on_a_held_name():
    """A 2-for-1 split halves every close in the history, including the one we
    bought at. The feed is healthy — only the share count is now wrong."""
    seen = _clean_frame(cols=("A", "B"))             # what the book traded on
    fills = [_bought("A", seen), _bought("B", seen)]
    now = seen.copy()
    now["A"] = seen["A"] / 2                         # history rewritten under us
    found = data_quality.suspected_split(now, {"A": 100, "B": 100}, fills)
    assert set(found) == {"A"}
    ratio, factor = found["A"]
    assert ratio == pytest.approx(0.5, abs=0.01)
    assert factor == pytest.approx(0.5)


def test_suspected_split_ignores_a_name_we_do_not_hold():
    """Detection protects a SHARE COUNT. A name we hold none of has none."""
    seen = _clean_frame(cols=("A", "B"))
    fills = [_bought("A", seen), _bought("B", seen)]
    now = seen.copy()
    now["A"] = seen["A"] / 2
    assert data_quality.suspected_split(now, {"B": 100}, fills) == {}
    assert data_quality.suspected_split(now, {"A": 0, "B": 100}, fills) == {}


def test_suspected_split_ignores_an_ordinary_crash():
    """A held name that halves AFTER we bought it is a market, not a corporate
    action: the close we traded at is still in the series, untouched. This is
    the exact shape a close-to-close check cannot tell from a 2-for-1."""
    seen = _clean_frame(cols=("A",))
    fills = [_bought("A", seen)]
    now = seen.copy()
    now.iloc[-1, 0] = now.iloc[-2, 0] / 2            # -50% today, history intact
    assert data_quality.suspected_split(now, {"A": 100}, fills) == {}


def test_reverse_split_is_detected_too():
    seen = _clean_frame(cols=("A",))
    fills = [_bought("A", seen)]
    now = seen.copy()
    now["A"] = seen["A"] * 3                         # 1-for-3 consolidation
    found = data_quality.suspected_split(now, {"A": 100}, fills)
    assert found and found["A"][1] == pytest.approx(3.0)


def test_the_large_splits_in_the_live_universes_are_known_factors():
    """The splits these universes actually had since 2024: NVDA, AVGO and NFLX
    10-for-1, NOW 5-for-1, BKNG 25-for-1, AMC.AX 1-for-5."""
    seen = _clean_frame(cols=("A",))
    fills = [_bought("A", seen)]
    for rewrite in (1 / 10, 1 / 5, 1 / 25, 5.0):
        now = seen.copy()
        now["A"] = seen["A"] * rewrite
        found = data_quality.suspected_split(now, {"A": 100}, fills)
        assert found and found["A"][1] == pytest.approx(rewrite), rewrite


def test_dividend_drift_is_not_a_split_but_does_not_hide_one():
    """`auto_adjust` rewrites history for dividends too, pulling the ratio a
    little below 1 — measured on the live books, 0.980 to 1.005 over 116
    fills. That is not a split; but a split on a name that has also paid out
    since we bought it must still match."""
    seen = _clean_frame(cols=("A",))
    fills = [_bought("A", seen)]
    now = seen.copy()
    now["A"] = seen["A"] * 0.97
    assert data_quality.suspected_split(now, {"A": 100}, fills) == {}
    now["A"] = seen["A"] * 0.97 / 2
    found = data_quality.suspected_split(now, {"A": 100}, fills)
    assert found and found["A"][1] == pytest.approx(0.5)


def test_suspected_split_reads_the_latest_fill_and_never_a_cash_event():
    """The anchor is the name's most recent EXECUTION. A dividend row records
    cash per share in `fill`, not a price: read as one, a 1% dividend would
    look like a x100 rewrite."""
    seen = _clean_frame(cols=("A",))
    div = {"date": str(seen.index[-3].date()), "region": "US", "ticker": "A",
           "side": "DIV", "shares": 200,
           "fill": round(float(seen["A"].iloc[-3]) / 100, 4), "commission": 0.0,
           "stamp_duty": 0.0, "currency": "USD"}
    fills = [_bought("A", seen, i=-20), _bought("A", seen, i=-10), div]
    assert data_quality.suspected_split(seen, {"A": 200}, fills) == {}



def test_suspected_split_anchors_on_the_latest_fill_not_the_first():
    """A round trip closed BEFORE a split, then re-entered after it: the old
    fills are in pre-split units, but the shares held now were bought at the
    post-split price, so the latest fill agrees with the series. Anchoring on
    the first fill would halt a correct book."""
    seen = _clean_frame(cols=("A",))                 # pre-split, as traded
    sell = {**_bought("A", seen, i=-20), "side": "SELL"}
    now = seen / 2                                   # 2-for-1 at bar -15
    reentry = _bought("A", now, i=-10, shares=200)   # bought post-split
    fills = [_bought("A", seen, i=-30), sell, reentry]
    assert data_quality.suspected_split(now, {"A": 200}, fills) == {}

def test_suspected_split_falls_back_to_the_fill_on_older_ledger_rows():
    """Rows written before the `decision` field (49 of the 120 live fills)
    carry only the slipped fill. A few bp of slippage is far inside tolerance."""
    seen = _clean_frame(cols=("A",))
    old = _bought("A", seen)
    del old["decision"]
    now = seen.copy()
    now["A"] = seen["A"] / 2
    found = data_quality.suspected_split(now, {"A": 100}, [old])
    assert found and found["A"][1] == pytest.approx(0.5)


def test_a_unit_error_the_book_traded_at_is_not_caught():
    """The IHG.L /100 defect (Task 15) is NOT caught, and cannot be by this
    check: the book bought at the same wrong price the series still shows, so
    record and series agree. Live `full` numbers: BUY 593 IHG.L on 2026-06-11
    at 1.6328 (no `decision`); today's series reads 1.599281 on that date —
    a ratio of 0.98, a dividend, not a factor."""
    now = pd.DataFrame({"IHG.L": 1.599281},
                       index=pd.bdate_range("2026-06-01", "2026-06-30"))
    fills = [{"date": "2026-06-11", "region": "FTSE", "ticker": "IHG.L",
              "side": "BUY", "shares": 593, "fill": 1.6328, "commission": 3.0,
              "stamp_duty": 4.84, "currency": "GBP"}]
    assert data_quality.suspected_split(now, {"IHG.L": 593}, fills) == {}


def test_a_quote_unit_change_under_a_held_name_is_caught():
    """What it DOES catch of that class: the scale changing under a position
    still held. Task 15's quote_overrides moves IHG.L from the region's 0.01 to
    1.0, which rewrites its whole history x100 under any book holding it."""
    now = pd.DataFrame({"IHG.L": 159.9281},
                       index=pd.bdate_range("2026-06-01", "2026-06-30"))
    fills = [{"date": "2026-06-11", "region": "FTSE", "ticker": "IHG.L",
              "side": "BUY", "shares": 593, "fill": 1.6328, "commission": 3.0,
              "stamp_duty": 4.84, "currency": "GBP"}]
    found = data_quality.suspected_split(now, {"IHG.L": 593}, fills)
    assert found and found["IHG.L"][1] == pytest.approx(100.0)
