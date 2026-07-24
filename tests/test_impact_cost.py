"""Backlog R1 + F6: unified cost entrypoint + market-impact model."""
import math

import numpy as np
import pandas as pd

from trading_algo import config as cfg
from trading_algo import data, fees
from trading_algo.backtest import run_backtest
from trading_algo.regions import get_region


def _invested_frame(n=450, cols=8, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    market = np.cumsum(rng.normal(0.0005, 0.006, n))
    df = {f"S{i}": 100 * np.exp(np.cumsum(rng.normal(0.0007, 0.011, n))) for i in range(cols)}
    return pd.DataFrame(df, index=idx), pd.Series(5000 * np.exp(market), index=idx)


# --- R1: one cost entrypoint, identical to the prior flat model -------------
def test_turnover_cost_matches_prior_formula():
    r = get_region("ASX")
    turnover, buy = 0.4, 0.25
    expected = turnover * fees.round_trip_cost_rate(r) + buy * r.stamp_duty_bps / 1e4
    assert fees.turnover_cost(r, turnover, buy) == expected          # impact=0 -> unchanged


def test_turnover_cost_adds_impact():
    r = get_region("US")
    base = fees.turnover_cost(r, 0.4, 0.2, impact=0.0)
    assert fees.turnover_cost(r, 0.4, 0.2, impact=0.001) == base + 0.001


# --- F6: square-root impact formula -----------------------------------------
def test_square_root_impact_shape():
    # coef * vol * sqrt(order/ADV)
    assert fees.square_root_impact(1000, 1_000_000, 0.2, 0.1) == \
        0.1 * 0.2 * math.sqrt(1000 / 1_000_000)
    # concave in participation: 4x the order is only 2x the impact
    small = fees.square_root_impact(1000, 1e6, 0.2, 0.1)
    big = fees.square_root_impact(4000, 1e6, 0.2, 0.1)
    assert abs(big - 2 * small) < 1e-12


def test_impact_zero_when_adv_unknown():
    assert fees.square_root_impact(1000, 0, 0.2, 0.1) == 0.0
    assert fees.square_root_impact(1000, float("nan"), 0.2, 0.1) == 0.0
    assert fees.square_root_impact(1000, 1e6, float("nan"), 0.1) == 0.0


# --- F6 backtest wiring -----------------------------------------------------
def test_backtest_impact_noop_when_disabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    vol = data.synthetic_volume(list(prices.columns), prices.index)
    monkeypatch.setattr(cfg, "IMPACT_COEF", None)
    a = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    b = run_backtest(prices, index_px, region, max_drawdown_stop=None)
    assert a["total_cost_fraction"] == b["total_cost_fraction"]      # identical


def test_backtest_impact_raises_cost_when_enabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    # thin volume so participation (and impact) is large
    vol = pd.DataFrame(500.0, index=prices.index, columns=prices.columns)
    off = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    monkeypatch.setattr(cfg, "IMPACT_COEF", 0.5)
    on = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    assert on["total_cost_fraction"] > off["total_cost_fraction"]    # impact adds cost
