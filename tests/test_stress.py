"""Stress testing: bootstrap MC, drawdown analytics, regime split, cost stress."""
import numpy as np
import pandas as pd

from trading_algo import stress
from trading_algo.regions import get_region
from trading_algo import data
from trading_algo.backtest import run_backtest


def _rets(n=1500, mu=0.0004, sd=0.01, seed=0):
    idx = pd.bdate_range("2010-01-01", periods=n, freq="B")
    return pd.Series(np.random.default_rng(seed).normal(mu, sd, n), index=idx)


def test_stationary_bootstrap_shape_and_values():
    r = _rets(500)
    paths = stress.stationary_bootstrap(r, mean_block=20, n_paths=50, seed=1)
    assert paths.shape == (50, 500)
    # every resampled value must come from the original series (exact copies)
    assert set(np.unique(paths)).issubset(set(r.values.tolist()))


def test_mc_summary_percentiles_ordered():
    r = _rets(1000)
    s = stress.mc_summary(r, n_paths=300, seed=2)
    for key in ("CAGR", "Sharpe", "MaxDD"):
        assert s[key]["p5"] <= s[key]["p50"] <= s[key]["p95"]
    assert 0.0 <= s["P(MaxDD>30%)"] <= 1.0


def test_drawdown_analytics_on_decline():
    idx = pd.bdate_range("2020-01-01", periods=300, freq="B")
    r = pd.Series(-0.001, index=idx)          # steady bleed → always underwater
    d = stress.drawdown_analytics(r)
    assert d["max_drawdown"] < 0
    assert d["time_underwater_pct"] > 0.9
    assert d["ulcer_index"] > 0
    assert d["E[MaxDD]_zero_drift"] <= 0


def test_cost_stress_monotonic():
    region = get_region("US")
    prices, index_px = data.synthetic_region(region)
    bt = run_backtest(prices, index_px, region)
    cs = stress.cost_stress(bt, multipliers=(1.0, 2.0, 3.0))
    assert cs["1x"]["CAGR"] >= cs["2x"]["CAGR"] >= cs["3x"]["CAGR"]   # more cost, less return


def test_regime_conditional_keys():
    region = get_region("US")
    prices, index_px = data.synthetic_region(region)
    bt = run_backtest(prices, index_px, region)
    rc = stress.regime_conditional(bt["returns"], index_px)
    assert set(rc) == {"bull", "bear", "low_vol", "high_vol"}
    assert abs(rc["bull"]["share"] + rc["bear"]["share"] - 1.0) < 0.05
