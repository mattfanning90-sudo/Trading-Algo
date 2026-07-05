"""Time-series (trend) momentum sleeve — signal, sizing, no-lookahead, backtest."""
import numpy as np
import pandas as pd

from trading_algo import trend, universes
from trading_algo.config import DEFAULT_TREND_PARAMS as TP
from trading_algo.data import synthetic_prices


def _synth_trend():
    """Synthetic price frame for the trend ETF basket (offline)."""
    raw = synthetic_prices(universes.TREND, "DUMMYIDX", seed=7)
    return raw[universes.TREND]


def test_signal_in_range_and_signed():
    prices = _synth_trend()
    s = trend.trend_signal(prices, TP)
    last = s.iloc[-1].dropna()
    assert (last >= -1.0 - 1e-9).all() and (last <= 1.0 + 1e-9).all()
    # a steadily rising series should signal long (+); a falling one short (-)
    idx = prices.index
    up = pd.DataFrame({"X": np.linspace(10, 30, len(idx))}, index=idx)
    down = pd.DataFrame({"X": np.linspace(30, 10, len(idx))}, index=idx)
    assert trend.trend_signal(up, TP)["X"].iloc[-1] > 0
    assert trend.trend_signal(down, TP)["X"].iloc[-1] < 0


def test_long_only_floors_shorts():
    idx = pd.bdate_range("2015-01-01", periods=400)
    down = pd.DataFrame({"X": np.linspace(30, 10, len(idx))}, index=idx)
    p_ls = TP.with_overrides(long_only=False)
    p_lo = TP.with_overrides(long_only=True)
    assert trend.trend_signal(down, p_ls)["X"].iloc[-1] < 0
    assert trend.trend_signal(down, p_lo)["X"].iloc[-1] == 0.0


def test_size_positions_respects_gross_cap():
    sigvals = pd.Series({"A": 1.0, "B": -1.0, "C": 1.0, "D": -1.0})
    vols = pd.Series({"A": 0.10, "B": 0.10, "C": 0.10, "D": 0.10})
    p = TP.with_overrides(max_gross=1.0, target_vol=5.0)   # force the cap to bind
    w = trend.size_positions(sigvals, vols, p)
    assert w.abs().sum() <= 1.0 + 1e-9
    # long/short signs preserved
    assert w["A"] > 0 and w["B"] < 0


def test_compute_trend_targets_no_lookahead():
    prices = _synth_trend()
    asof = prices.index[-40]
    full = trend.compute_trend_targets(prices, TP, asof=asof)
    trunc = trend.compute_trend_targets(prices.loc[:asof], TP, asof=asof)
    pd.testing.assert_series_equal(full, trunc)


def test_trend_targets_ignore_corrupted_future():
    """Shift-test lock-down for the trend sleeve: garbage prices after asof must
    not change the weights decided at asof."""
    prices = _synth_trend()
    asof = prices.index[-40]
    base = trend.compute_trend_targets(prices, TP, asof=asof)
    p2 = prices.copy()
    p2.iloc[p2.index.get_loc(asof) + 1:] *= 1000.0
    pd.testing.assert_series_equal(base, trend.compute_trend_targets(p2, TP, asof=asof))


def test_cached_equals_uncached():
    prices = _synth_trend()
    cache = trend.precompute(prices, TP)
    for asof in (prices.index[-1], prices.index[-30]):
        a = trend.compute_trend_targets(prices, TP, asof=asof)
        b = trend.compute_trend_targets(prices, TP, asof=asof, signals_cache=cache)
        pd.testing.assert_series_equal(a, b)


def test_run_trend_backtest_outputs():
    prices = _synth_trend()
    res = trend.run_trend_backtest(prices)
    assert {"returns", "equity", "metrics", "avg_gross_exposure"} <= set(res)
    assert len(res["equity"]) > 0
    assert res["total_cost_fraction"] >= 0.0      # costs always on
    assert res["avg_gross_exposure"] <= TP.max_gross + 1e-9
