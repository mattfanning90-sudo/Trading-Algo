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


def residual_momentum_score(prices: pd.DataFrame, index_prices: pd.Series,
                            p: StrategyParams) -> pd.DataFrame:
    """Residual (market-neutral) momentum (Blitz-Huij-Martens 2011).

    Rank on the part of each stock's return NOT explained by the market, so the
    market beta that drives momentum *crashes* (prior losers rallying hard in a
    rebound) is stripped out. Steps, all causal (data ≤ t):
      1. rolling beta of each stock to the regime index (cov/var over beta_lookback);
      2. residual daily return  rᵢ − βᵢ·r_mkt;
      3. 12-1 momentum of residuals = Σ residual over [lookback, skip], standardised
         by trailing residual vol (Blitz's t-stat form).
    Higher Sharpe and lower crash beta than raw momentum in the literature."""
    r = prices.pct_change(fill_method=None)
    rm = index_prices.reindex(prices.index).pct_change(fill_method=None)
    lb = p.resmom_beta_lookback
    beta = r.rolling(lb).cov(rm).div(rm.rolling(lb).var(), axis=0)
    resid = r.sub(beta.mul(rm, axis=0))
    window = max(p.lookback_days - p.skip_days, 1)
    cum = resid.rolling(window).sum().shift(p.skip_days)          # 12-1 of residuals
    rvol = resid.rolling(p.lookback_days).std()
    return cum / rvol.replace(0, np.nan)


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
