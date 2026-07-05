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
