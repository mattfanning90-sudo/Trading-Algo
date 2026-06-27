"""Carry sleeve: cross-sectional yield signal, sizing, L/S backtest."""
import numpy as np
import pandas as pd

from trading_algo import carry, data, universes
from trading_algo.config import CarryParams

CP = CarryParams(min_history_days=60, vol_lookback=30, yield_lookback=60)


def _synth():
    prices = data.synthetic_prices(universes.CARRY, "IDX")[universes.CARRY]
    yields = data.synthetic_carry_yields(universes.CARRY)
    return prices, yields


def test_carry_signal_ranks_and_scales():
    row = pd.Series({"A": 0.01, "B": 0.03, "C": 0.05})
    s = carry.carry_signal(row, long_short=True)
    assert s["C"] > s["A"]                       # higher yield → higher signal
    assert s["A"] < 0 < s["C"]                   # demeaned: long high, short low
    assert abs(s.sum()) < 1e-9                    # dollar-neutral before vol sizing
    assert s.abs().max() <= 1.0 + 1e-9           # scaled into [-1, 1]


def test_carry_signal_long_only_nonnegative():
    row = pd.Series({"A": 0.01, "B": 0.03, "C": 0.05})
    s = carry.carry_signal(row, long_short=False)
    assert (s >= 0).all()


def test_precompute_cache_matches():
    prices, yields = _synth()
    cache = carry.precompute(prices, yields, CP)
    for asof in (prices.index[-1], prices.index[-40]):
        a = carry.compute_carry_targets(prices, yields, CP, asof=asof)
        b = carry.compute_carry_targets(prices, yields, CP, asof=asof, signals_cache=cache)
        pd.testing.assert_series_equal(a, b)


def test_carry_signal_no_lookahead():
    # the signal at asof must not depend on yields after asof
    prices, yields = _synth()
    cut = prices.index[-20]
    full = carry.precompute(prices, yields, CP)["signal"].loc[cut]
    truncated = carry.precompute(prices.loc[:cut], yields.loc[:cut], CP)["signal"].loc[cut]
    pd.testing.assert_series_equal(full, truncated)


def test_run_carry_backtest_outputs():
    prices, yields = _synth()
    res = carry.run_carry_backtest(prices, yields, CP)
    assert {"returns", "equity", "metrics", "avg_gross_exposure"} <= set(res)
    assert len(res["equity"]) > 0
    assert res["total_cost_fraction"] >= 0.0          # costs always on
    assert res["avg_gross_exposure"] <= CP.max_gross + 1e-9


def test_synthetic_yields_have_spread():
    y = data.synthetic_carry_yields(universes.CARRY)
    assert (y.mean().max() - y.mean().min()) > 0.005   # a real cross-sectional spread
