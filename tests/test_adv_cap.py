"""Backlog F15 / P0-I / R3: pre-trade ADV cap + capacity hook + volume ingestion."""
import numpy as np
import pandas as pd

from trading_algo import config as cfg
from trading_algo import data, strategy
from trading_algo.backtest import run_backtest
from trading_algo.regions import get_region


def _invested_frame(n=450, cols=8, seed=0):
    """Rising prices + a rising index so the regime is risk-on and compute_targets
    actually holds a book (not cash)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    market = np.cumsum(rng.normal(0.0005, 0.006, n))
    data_ = {f"S{i}": 100 * np.exp(np.cumsum(rng.normal(0.0007, 0.011, n)))
             for i in range(cols)}
    prices = pd.DataFrame(data_, index=idx)
    index_px = pd.Series(5000 * np.exp(market), index=idx)
    return prices, index_px


# --- R3: volume ingestion ---------------------------------------------------
def test_synthetic_volume_deterministic_and_shaped():
    idx = pd.bdate_range("2020-01-01", periods=50)
    a = data.synthetic_volume(["X", "Y"], idx, seed=7)
    b = data.synthetic_volume(["X", "Y"], idx, seed=7)
    assert a.shape == (50, 2) and a.equals(b) and (a > 0).all().all()


def test_adv_dollar_is_causal():
    idx = pd.bdate_range("2020-01-01", periods=40)
    prices = pd.DataFrame({"X": np.linspace(10, 20, 40)}, index=idx)
    vol = pd.DataFrame({"X": np.full(40, 1000.0)}, index=idx)
    advd = data.adv_dollar(prices, vol, window=5)
    p2 = prices.copy(); p2.iloc[-1] *= 10                  # spike AFTER the point
    assert advd["X"].iloc[20] == data.adv_dollar(p2, vol, window=5)["X"].iloc[20]


# --- P0-I: capacity hook in compute_targets ---------------------------------
def test_capacity_is_noop_when_none():
    prices, index_px = _invested_frame()
    p = get_region("US").params
    base = strategy.compute_targets(prices, index_px, p)
    pd.testing.assert_series_equal(base, strategy.compute_targets(prices, index_px, p, capacity=None))


def test_capacity_caps_weights():
    prices, index_px = _invested_frame()
    p = get_region("US").params
    base = strategy.compute_targets(prices, index_px, p)
    assert not base.empty
    cap = base.abs() * 0.5                                  # halve every name's cap
    capped = strategy.compute_targets(prices, index_px, p, capacity=cap)
    assert (capped.abs() <= base.abs() + 1e-12).all()      # never exceeds prior magnitude
    assert (capped.abs() < base.abs() - 1e-12).any()       # at least one actually shrank


# --- F15: backtest ADV cap --------------------------------------------------
def test_backtest_adv_cap_is_noop_when_disabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    vol = data.synthetic_volume(list(prices.columns), prices.index)
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)          # off
    a = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    b = run_backtest(prices, index_px, region, max_drawdown_stop=None)
    assert a["metrics"]["CAGR"] == b["metrics"]["CAGR"]    # identical -> baseline safe


def test_backtest_adv_cap_binds_when_enabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    vol = pd.DataFrame(1000.0, index=prices.index, columns=prices.columns)
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 1e-6)          # tiny cap that binds
    capped = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    uncapped = run_backtest(prices, index_px, region, max_drawdown_stop=None)
    assert capped["metrics"] != uncapped["metrics"]
