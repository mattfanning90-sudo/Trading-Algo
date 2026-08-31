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


def value_score(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Price-based value proxy = long-term reversal. Cumulative return over the
    window ending `value_skip_days` ago and starting `value_lookback_days` ago,
    NEGATED — so long-term losers ('cheap') score high and long-term winners
    score low. Negatively correlated with 12-1 momentum by construction, which is
    what makes it a diversifying factor. (A true fundamental value factor needs
    historical fundamentals; this is the standard price-only proxy.)"""
    long_term_return = prices.shift(p.value_skip_days) / prices.shift(p.value_lookback_days) - 1.0
    return -long_term_return


def stock_trend_ok(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """True where price is above its trend moving average.

    Carries the last known close over gaps first — see `index_risk_on` for why.
    Forward-filling is causal (it only ever reuses a past print) and it is NOT
    the defence against a permanently dead feed: `data_quality` owns that, and
    removes such names from the candidate set before they reach here.
    """
    px = prices.ffill()
    ma = px.rolling(p.stock_trend_ma).mean()
    return px > ma


def index_risk_on(index_prices: pd.Series, p: StrategyParams) -> pd.Series:
    """Regime filter: risk-on only when the index is above its trend MA.

    The last known close is carried over gaps BEFORE the rolling mean. Without
    that, one missing print makes every window containing it NaN, and since
    `price > NaN` is False the sleeve reads risk-off — and stays there for the
    next `index_trend_ma` bars, reporting a legitimate-looking 'regime-off'
    while the index sits comfortably above its trend. A missing print is an
    absence of news, not a bearish signal.
    """
    px = index_prices.ffill()
    ma = px.rolling(p.index_trend_ma).mean()
    return px > ma


def realised_vol(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Annualised trailing realised volatility per asset."""
    rets = prices.pct_change(fill_method=None)
    return rets.rolling(p.vol_lookback).std() * np.sqrt(252)


def sizing_vol(vols: pd.Series, p: StrategyParams) -> pd.Series:
    """Volatilities as used for POSITION SIZING: floored at `p.min_vol`, with
    unusable (zero / missing) readings dropped.

    Kept separate from `realised_vol`, which stays an honest measurement — the
    floor is a sizing judgement ("we do not believe any equity is calmer than
    this"), not a claim about what the market did.
    """
    return vols.replace(0, np.nan).dropna().clip(lower=p.min_vol)


def select_portfolio(scores: pd.Series, trend_ok: pd.Series,
                     vols: pd.Series, risk_on: bool,
                     p: StrategyParams, rank_score: pd.Series | None = None) -> pd.Series:
    """Build raw target weights for one rebalance date (before vol targeting).

    Eligibility: positive absolute momentum AND above the per-stock trend MA AND
    regime risk-on. Ranking: top N by `rank_score` if given (e.g. a momentum+value
    composite), else by momentum. Weighting: inverse-volatility, capped at
    max_weight, then de-levered if capping pushed the sum above 1. Returns a
    Series that may sum to < 1 (remainder = cash).
    """
    eligible = scores.dropna()
    eligible = eligible[eligible > p.abs_momentum_floor]
    eligible = eligible[trend_ok.reindex(eligible.index).fillna(False)]

    if not risk_on or eligible.empty:
        return pd.Series(dtype=float)

    metric = eligible if rank_score is None else rank_score.reindex(eligible.index).dropna()
    if metric.empty:
        return pd.Series(dtype=float)
    picks = metric.nlargest(min(p.top_n, len(metric))).index
    inv_vol = 1.0 / sizing_vol(vols.reindex(picks), p)
    if inv_vol.empty:
        return pd.Series(dtype=float)

    w = inv_vol / inv_vol.sum()
    w = w.clip(upper=p.max_weight)
    # If capping left the book summing above 1, de-lever back to 1 (never re-lever).
    total = w.sum()
    if total > 1.0:
        w = w / total
    return w


def _leg_weights(names, vols: pd.Series, p: StrategyParams, sign: float) -> pd.Series:
    """Inverse-vol weights for one leg (long or short), normalised to sum to
    `sign` (±1) before the book-level vol targeting scales it. Returns an empty
    Series if no name has a usable vol."""
    inv_vol = 1.0 / sizing_vol(vols.reindex(names), p)
    if inv_vol.empty:
        return pd.Series(dtype=float)
    w = inv_vol / inv_vol.sum()
    w = w.clip(upper=p.max_weight)
    total = w.sum()
    if total > 1.0:
        w = w / total
    return w * sign


def select_long_short(scores: pd.Series, vols: pd.Series,
                      p: StrategyParams) -> pd.Series:
    """Raw dollar-neutral weights: long the strongest momentum, short the weakest.

    Purely cross-sectional — no absolute-momentum floor, trend filter or regime
    gate (those are directional/timing filters; a market-neutral book is designed
    to be agnostic to them and to hedge out market direction). Ranks every name
    with a finite score, longs the top `top_n` (positive, inverse-vol) and shorts
    the bottom `short_n` (negative, inverse-vol). Each leg is normalised to ±1 so
    the raw book is dollar-neutral; `compute_targets` then vol-targets it. Returns
    an empty Series if there aren't enough names to form both legs.
    """
    ranked = scores.dropna().sort_values(ascending=False)
    short_n = p.short_n or p.top_n
    if len(ranked) < p.top_n + short_n:
        return pd.Series(dtype=float)   # not enough names to hedge — stay flat

    longs = _leg_weights(ranked.index[:p.top_n], vols, p, +1.0)
    shorts = _leg_weights(ranked.index[-short_n:], vols, p, -1.0)
    if longs.empty or shorts.empty:
        return pd.Series(dtype=float)
    # A name can't be both long and short; disjoint by construction (top vs bottom).
    return pd.concat([longs, shorts])
