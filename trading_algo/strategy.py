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

    # Gross exposure is Σ|w| — for a long-only book this equals w.sum(); for a
    # long/short book the legs net out, so the abs form is what must respect the
    # leverage cap.
    gross = w.abs().sum()
    if gross > p.max_gross:
        w = w * (p.max_gross / gross)
    return w


def _apply_capacity(w: pd.Series, capacity: pd.Series | None) -> pd.Series:
    """Cap each name's weight magnitude at `capacity` (a per-name max weight),
    never re-levering (backlog F15 / foundation P0-I). This is the ONE place a
    liquidity/capacity constraint enters the weight vector, so backtest and paper
    stay identical (invariant #3). No-op when `capacity` is None."""
    if capacity is None or w.empty:
        return w
    cap = capacity.reindex(w.index).fillna(np.inf).clip(lower=0.0)
    return np.sign(w) * np.minimum(w.abs(), cap)


def compute_targets(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams, asof: pd.Timestamp | None = None,
                    eligible: set[str] | None = None,
                    capacity: pd.Series | None = None) -> pd.Series:
    """Target weights for one rebalance date (default: the latest available).

    Uses only data up to and including `asof` — no lookahead. Returns a Series
    of weights summing to ≤ max_gross; an empty Series means "go to cash"
    (regime risk-off or nothing eligible).

    `eligible`, if given, restricts the candidate set to those tickers — used
    for point-in-time backtests so a name can't be picked before it was actually
    an index member.

    `capacity`, if given, is a per-name maximum weight (from a pre-trade ADV cap,
    F15); each name is trimmed to it after vol targeting. None = no cap.
    """
    if asof is None:
        asof = prices.index[-1]

    scores = sig.momentum_score(prices, p).loc[asof]
    if eligible is not None:
        scores = scores[scores.index.isin(eligible)]
    vols = sig.realised_vol(prices, p).loc[asof]

    # Market-neutral book: dollar-neutral long/short, no directional filters.
    # Still routed through the single vol_target so backtest and paper agree.
    if p.long_short:
        raw = sig.select_long_short(scores, vols, p)
        return _apply_capacity(vol_target(raw, vols, p), capacity)

    trend = sig.stock_trend_ok(prices, p).loc[asof]
    risk_on = (True if not p.regime_filter else bool(
        sig.index_risk_on(index_prices, p)
        .reindex(prices.index)
        .ffill()
        .loc[asof]
    ))

    rank_score = None
    if p.use_value:
        val = sig.value_score(prices, p).loc[asof]
        if eligible is not None:
            val = val[val.index.isin(eligible)]
        # cross-sectional percentile blend: high = strong momentum AND cheap
        rank_score = (p.momentum_weight * scores.rank(pct=True)
                      + p.value_weight * val.rank(pct=True))

    raw = sig.select_portfolio(scores, trend, vols, risk_on, p, rank_score=rank_score)
    return _apply_capacity(vol_target(raw, vols, p), capacity)
