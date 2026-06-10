"""The single source of truth for target portfolio weights.

`compute_targets()` turns a price history into the desired weight vector for one
as-of date. It is the ONLY place selection + vol targeting live, and it is
called by BOTH the backtester and the paper-trading engine. That is what keeps
invariant #4 (backtest and paper must agree) true by construction — there is no
second copy of the weight logic to drift out of sync.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals as sig
from .config import StrategyParams


def vol_target(weights: pd.Series, vols: pd.Series, p: StrategyParams) -> pd.Series:
    """Scale a raw weight book toward the portfolio volatility target.

    Portfolio vol is estimated with the constant-average-correlation
    approximation:

        var ≈ (1-ρ)·Σ(wᵢσᵢ)²  +  ρ·(Σ wᵢσᵢ)²

    where ρ = `avg_correlation`. The book is then scaled by
    target_vol / est_vol, capped at `max_vol_scale`, and finally de-levered so
    gross exposure never exceeds `max_gross`.
    """
    if weights.empty:
        return weights
    wv = (weights * vols.reindex(weights.index)).dropna()
    if wv.empty:
        return pd.Series(dtype=float)

    rho = p.avg_correlation
    port_var = (1.0 - rho) * (wv**2).sum() + rho * (wv.sum() ** 2)
    port_vol = float(np.sqrt(max(port_var, 0.0)))

    scale = min(p.target_vol / max(port_vol, 1e-9), p.max_vol_scale)
    w = weights * scale

    gross = w.sum()
    if gross > p.max_gross:
        w = w * (p.max_gross / gross)
    return w


def compute_targets(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams, asof: pd.Timestamp | None = None) -> pd.Series:
    """Target weights for one rebalance date (default: the latest available).

    Uses only data up to and including `asof` — no lookahead. Returns a Series
    of weights summing to ≤ max_gross; an empty Series means "go to cash"
    (regime risk-off or nothing eligible).
    """
    if asof is None:
        asof = prices.index[-1]

    scores = sig.momentum_score(prices, p).loc[asof]
    trend = sig.stock_trend_ok(prices, p).loc[asof]
    vols = sig.realised_vol(prices, p).loc[asof]
    risk_on = bool(
        sig.index_risk_on(index_prices, p)
        .reindex(prices.index)
        .ffill()
        .loc[asof]
    )

    raw = sig.select_portfolio(scores, trend, vols, risk_on, p)
    return vol_target(raw, vols, p)
