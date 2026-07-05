"""Backlog F9: momentum-crash / crowding monitor (observability-only)."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import crowding, strategy
from trading_algo.regions import get_region


@pytest.fixture
def us():
    return get_region("US")


def _trending_frame(n=400, cols=8, corr=False, seed=0):
    """Price frame with enough history for the 12-1 momentum window."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n)
    market = rng.normal(0.0005, 0.01, n)
    data = {}
    for i in range(cols):
        idio = np.zeros(n) if corr else rng.normal(0, 0.012, n)
        # a rising drift so momentum scores are positive and names get picked
        rets = 0.0006 + (market if corr else 0.6 * market) + idio
        data[f"S{i}"] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=idx)


def test_benign_book_not_elevated(us):
    prices = _trending_frame(corr=False)
    index_px = prices.mean(axis=1)
    rep = crowding.crowding_report(prices, index_px, us)
    assert rep["elevated"] is False


def test_perfectly_crowded_book_flags(us):
    prices = _trending_frame(corr=True)          # all names driven by the market
    index_px = prices.mean(axis=1)
    rep = crowding.crowding_report(prices, index_px, us)
    assert rep["avg_correlation"] is not None and rep["avg_correlation"] > crowding.CORR_MAX
    assert rep["elevated"] is True
    assert any("crowded" in r for r in rep["reasons"])


def test_crash_setup_detected(us):
    prices = _trending_frame(corr=False)
    n = len(prices)
    # index: long plateau (keeps the 200-day MA high) then a late drop and a
    # sharp monthly bounce — the classic bear-then-bounce crash setup.
    vals = np.concatenate([
        np.full(n - 40, 100.0),
        np.linspace(100, 68, 19),
        np.linspace(68, 73, 21),
    ])
    index_px = pd.Series(vals, index=prices.index)
    rep = crowding.crowding_report(prices, index_px, us)
    assert rep["below_200dma"] < crowding.CRASH_BELOW_MA
    assert rep["crash_setup"] is True and rep["elevated"] is True


def test_no_lookahead(us):
    prices = _trending_frame(corr=False)
    index_px = prices.mean(axis=1)
    asof = prices.index[-100]
    base = crowding.crowding_report(prices, index_px, us, asof=asof)
    tampered = prices.copy()
    tampered.iloc[-1] *= 5.0                       # a spike strictly after asof
    after = crowding.crowding_report(tampered, index_px, us, asof=asof)
    assert base["avg_correlation"] == after["avg_correlation"]


def test_monitor_does_not_touch_sizing(us):
    """Invariant #3: the monitor is read-only — compute_targets is unchanged."""
    prices = _trending_frame(corr=False)
    index_px = prices.mean(axis=1)
    before = strategy.compute_targets(prices, index_px, us.params)
    crowding.crowding_report(prices, index_px, us)
    after = strategy.compute_targets(prices, index_px, us.params)
    pd.testing.assert_series_equal(before, after)
