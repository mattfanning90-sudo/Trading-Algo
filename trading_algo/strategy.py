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


def precompute(prices: pd.DataFrame, index_prices: pd.Series,
               p: StrategyParams) -> dict:
    """Precompute the full-history indicator frames once, for reuse across many
    `compute_targets` calls (a walk-forward backtest evaluates one as-of date per
    rebalance — dozens to hundreds of them).

    Every indicator here is *causal* (rolling / shift only), so reading
    ``frame.loc[asof]`` later returns exactly what recomputing from
    ``prices.loc[:asof]`` would — see `test_strategy_cache`. Caching therefore
    changes nothing about the result, only the cost: the heavy frame math runs
    a single time instead of once per rebalance (~100× fewer passes over the
    price history). The single-source-of-truth contract (invariant #3) is
    preserved — `compute_targets` is still the only place weights are built.
    """
    cache = {
        "momentum": sig.momentum_score(prices, p),
        "trend": sig.stock_trend_ok(prices, p),
        "vol": sig.realised_vol(prices, p),
        # store the index regime pre-aligned to the price calendar (forward-filled)
        "risk_on": sig.index_risk_on(index_prices, p).reindex(prices.index).ffill(),
    }
    if p.use_residual_momentum:
        cache["resmom"] = sig.residual_momentum_score(prices, index_prices, p)
    if p.use_value:
        cache["value"] = sig.value_score(prices, p)
    return cache


def compute_targets(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams, asof: pd.Timestamp | None = None,
                    eligible: set[str] | None = None,
                    signals_cache: dict | None = None) -> pd.Series:
    """Target weights for one rebalance date (default: the latest available).

    Uses only data up to and including `asof` — no lookahead. Returns a Series
    of weights summing to ≤ max_gross; an empty Series means "go to cash"
    (regime risk-off or nothing eligible).

    `eligible`, if given, restricts the candidate set to those tickers — used
    for point-in-time backtests so a name can't be picked before it was actually
    an index member.

    `signals_cache`, if given (from `precompute`), supplies the full indicator
    frames so they aren't rebuilt on this call. When omitted they are computed
    on the fly — identical result, just slower. The backtester precomputes once
    and passes the cache to every rebalance.
    """
    if asof is None:
        asof = prices.index[-1]

    c = signals_cache if signals_cache is not None else precompute(prices, index_prices, p)

    # residual (market-neutral) momentum swaps in as the ranking score when enabled
    scores = c["resmom" if p.use_residual_momentum else "momentum"].loc[asof]
    if eligible is not None:
        scores = scores[scores.index.isin(eligible)]
    trend = c["trend"].loc[asof]
    vols = c["vol"].loc[asof]
    risk_on = True if not p.regime_filter else bool(c["risk_on"].loc[asof])

    rank_score = None
    if p.use_value:
        val = c["value"].loc[asof]
        if eligible is not None:
            val = val[val.index.isin(eligible)]
        # cross-sectional percentile blend: high = strong momentum AND cheap
        rank_score = (p.momentum_weight * scores.rank(pct=True)
                      + p.value_weight * val.rank(pct=True))

    raw = sig.select_portfolio(scores, trend, vols, risk_on, p, rank_score=rank_score)
    return vol_target(raw, vols, p)
