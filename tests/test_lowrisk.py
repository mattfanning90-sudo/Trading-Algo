"""Low-risk / BAB sleeve: rolling beta, signal, L/S backtest."""
import numpy as np
import pandas as pd

from trading_algo import data, lowrisk
from trading_algo.config import LowRiskParams
from trading_algo.regions import get_region

LP = LowRiskParams(min_history_days=60, vol_lookback=30, beta_lookback=60)


def test_rolling_beta_recovers_known_betas():
    idx = pd.bdate_range("2015-01-01", periods=400)
    rng = np.random.default_rng(0)
    rm = pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)
    # A has beta 0.5, B has beta 1.5 (plus tiny idiosyncratic noise)
    ra = 0.5 * rm + rng.normal(0, 1e-4, len(idx))
    rb = 1.5 * rm + rng.normal(0, 1e-4, len(idx))
    index_px = 100 * (1 + rm).cumprod()
    prices = pd.DataFrame({"A": 100 * (1 + ra).cumprod(), "B": 100 * (1 + rb).cumprod()})
    beta = lowrisk.rolling_beta(prices, index_px, 120).dropna()
    assert abs(beta["A"].iloc[-1] - 0.5) < 0.1
    assert abs(beta["B"].iloc[-1] - 1.5) < 0.1


def test_lowrisk_signal_favours_low_beta():
    row = pd.Series({"A": 0.5, "B": 1.0, "C": 1.6})
    s = lowrisk.lowrisk_signal(row, long_short=True)
    assert s["A"] > 0 > s["C"]              # long low beta, short high beta
    assert abs(s.sum()) < 1e-9             # dollar-neutral before vol sizing
    assert s.abs().max() <= 1.0 + 1e-9


def test_precompute_cache_matches():
    reg = get_region("US")
    prices, idx = data.synthetic_region(reg)
    cache = lowrisk.precompute(prices, idx, LP)
    for asof in (prices.index[-1], prices.index[-40]):
        a = lowrisk.compute_lowrisk_targets(prices, idx, LP, asof=asof)
        b = lowrisk.compute_lowrisk_targets(prices, idx, LP, asof=asof, signals_cache=cache)
        pd.testing.assert_series_equal(a, b)


def test_signal_no_lookahead():
    reg = get_region("US")
    prices, idx = data.synthetic_region(reg)
    cut = prices.index[-20]
    full = lowrisk.precompute(prices, idx, LP)["signal"].loc[cut]
    trunc = lowrisk.precompute(prices.loc[:cut], idx.loc[:cut], LP)["signal"].loc[cut]
    pd.testing.assert_series_equal(full, trunc)


def test_run_lowrisk_backtest_outputs():
    reg = get_region("US")
    prices, idx = data.synthetic_region(reg)
    res = lowrisk.run_lowrisk_backtest(prices, idx, LP)
    assert {"returns", "equity", "metrics", "avg_gross_exposure"} <= set(res)
    assert len(res["equity"]) > 0
    assert res["total_cost_fraction"] >= 0.0
    assert res["avg_gross_exposure"] <= LP.max_gross + 1e-9
