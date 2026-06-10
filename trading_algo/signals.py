"""Signal engine: 12-1 cross-sectional momentum with trend/crash filters.

Region-agnostic — every function takes a `StrategyParams` so the same code runs
for FTSE, US and ASX. All signals at date t use data up to and including t;
trades execute at t+1 (handled by the backtester) — no lookahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams


def momentum_score(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """12-1 momentum: total return over `lookback_days` excluding the most
    recent `skip_days` (short-term reversal avoidance)."""
    return prices.shift(p.skip_days) / prices.shift(p.lookback_days) - 1.0


def stock_trend_ok(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """True where price is above its trend moving average."""
    ma = prices.rolling(p.stock_trend_ma).mean()
    return prices > ma


def index_risk_on(index_prices: pd.Series, p: StrategyParams) -> pd.Series:
    """Regime filter: risk-on only when the index is above its trend MA."""
    ma = index_prices.rolling(p.index_trend_ma).mean()
    return index_prices > ma


def realised_vol(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Annualised trailing realised volatility per asset."""
    rets = prices.pct_change(fill_method=None)
    return rets.rolling(p.vol_lookback).std() * np.sqrt(252)


def select_portfolio(scores: pd.Series, trend_ok: pd.Series,
                     vols: pd.Series, risk_on: bool,
                     p: StrategyParams) -> pd.Series:
    """Build raw target weights for one rebalance date (before vol targeting).

    Selection: top N by momentum, subject to positive absolute momentum and the
    per-stock trend filter. Weighting: inverse-volatility, capped at max_weight,
    then de-levered if capping pushed the sum above 1. Gated by the regime
    filter. Returns a Series of weights that may sum to < 1 (remainder = cash).
    """
    eligible = scores.dropna()
    eligible = eligible[eligible > p.abs_momentum_floor]
    eligible = eligible[trend_ok.reindex(eligible.index).fillna(False)]

    if not risk_on or eligible.empty:
        return pd.Series(dtype=float)

    picks = eligible.nlargest(p.top_n).index
    inv_vol = 1.0 / vols.reindex(picks).replace(0, np.nan).dropna()
    if inv_vol.empty:
        return pd.Series(dtype=float)

    w = inv_vol / inv_vol.sum()
    w = w.clip(upper=p.max_weight)
    # If capping left the book summing above 1, de-lever back to 1 (never re-lever).
    total = w.sum()
    if total > 1.0:
        w = w / total
    return w
