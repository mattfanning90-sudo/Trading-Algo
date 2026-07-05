"""Residual (market-neutral) momentum: it strips market beta from the ranking."""
import numpy as np
import pandas as pd

from trading_algo import signals as sig
from trading_algo.config import StrategyParams

P = StrategyParams(lookback_days=252, skip_days=21, resmom_beta_lookback=252)


def test_residual_momentum_strips_market_beta():
    idx = pd.bdate_range("2013-01-01", periods=700)
    rng = np.random.default_rng(0)
    rm = pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx)   # market up-trend
    # A = pure market (beta 1, no idiosyncratic drift) → residual momentum ~ 0
    ra = 1.0 * rm + rng.normal(0.0, 1e-4, len(idx))
    # B = market + its OWN idiosyncratic up-drift → positive residual momentum
    rb = 1.0 * rm + 0.0006 + rng.normal(0.0, 1e-4, len(idx))
    index_px = 100 * (1 + rm).cumprod()
    prices = pd.DataFrame({"A": 100 * (1 + ra).cumprod(), "B": 100 * (1 + rb).cumprod()})

    resmom = sig.residual_momentum_score(prices, index_px, P).dropna()
    last = resmom.iloc[-1]
    assert last["B"] > last["A"]                    # idiosyncratic winner ranks above
    assert abs(last["A"]) < abs(last["B"])          # pure-market name has ~no residual edge

    # raw momentum, by contrast, can't tell them apart (both ride the same market)
    raw = sig.momentum_score(prices, P).dropna().iloc[-1]
    assert abs(raw["A"] - raw["B"]) < abs(last["A"] - last["B"])
