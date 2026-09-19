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
