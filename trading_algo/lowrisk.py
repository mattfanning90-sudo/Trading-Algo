"""Low-risk / betting-against-beta (BAB) sleeve — a risk-characteristic premium.

Unlike momentum/value/trend (all sorted on past *returns*), this sleeve sorts on a
*risk* characteristic: each stock's rolling **beta** to the regime index. Long the
low-beta names, short the high-beta names (Frazzini & Pedersen 2014: leverage-
constrained investors bid up high-beta assets, so low beta is underpriced). Because
the sort is on beta — not return — the sleeve is structurally orthogonal to the
return-based sleeves, which is exactly what the multi-strategy book needs.

Honest framing:
- BAB needs cross-sectional beta dispersion, so run it on the broad **point-in-time**
  S&P 500 set (incl. delisted) — the high-beta short leg is where delisted names
  cluster, so a survivorship-biased universe would flatter it.
- It is mildly defensive / short-vol: expect it to lag the sharpest V-recoveries.
  Its job is diversification, not standalone return.

No lookahead: beta at date t uses returns ≤ t; the shared L/S engine applies the
resulting weights at t+1 (same discipline as the other sleeves).
"""
from __future__ import annotations

import pandas as pd

from . import trend
from .config import DEFAULT_LOWRISK_PARAMS, LowRiskParams


def rolling_beta(prices: pd.DataFrame, index_prices: pd.Series, lookback: int) -> pd.DataFrame:
    """Per-asset rolling beta to the regime index: cov(rᵢ, r_mkt)/var(r_mkt)."""
    r = prices.pct_change(fill_method=None)
    rm = index_prices.reindex(prices.index).pct_change(fill_method=None)
    var_m = rm.rolling(lookback).var()
    cov = r.rolling(lookback).cov(rm)                 # each column's cov with rm
    return cov.div(var_m, axis=0)


def lowrisk_signal(beta_row: pd.Series, long_short: bool = True) -> pd.Series:
    """Per-asset signal in [-1, 1] for one date: long LOW beta, short HIGH beta.
    Cross-sectionally demean the (negated) beta and scale by the largest deviation."""
    b = beta_row.dropna()
    if b.empty:
        return pd.Series(dtype=float)
    s = (b.mean() - b) if long_short else (b.max() - b)   # low beta → positive
    m = float(s.abs().max())
    return s / m if m > 0 else s * 0.0


def precompute(prices: pd.DataFrame, index_prices: pd.Series, p: LowRiskParams) -> dict:
    """Low-risk signal + price-vol frames (causal: beta uses returns ≤ t)."""
    beta = rolling_beta(prices, index_prices, p.beta_lookback)
    # liquidity filter: only rank names trading >= min_price on the date — drops the
    # illiquid sub-$5 penny stocks whose 10x spikes wrecked the naive short leg.
    if p.min_price:
        beta = beta.where(prices >= p.min_price)
    sig = beta.apply(lambda row: lowrisk_signal(row, p.long_short), axis=1)
    # floor the per-name vol so inverse-vol sizing can't explode on a near-constant
    # or thinly-traded single name (the ETF sleeves never hit this).
    vol = trend._realised_vol(prices, p).clip(lower=p.vol_floor)
    return {"signal": sig, "vol": vol}


def compute_lowrisk_targets(prices: pd.DataFrame, index_prices: pd.Series,
                            p: LowRiskParams = DEFAULT_LOWRISK_PARAMS,
                            asof: pd.Timestamp | None = None,
                            signals_cache: dict | None = None) -> pd.Series:
    """Signed target weights for one rebalance date (single source of truth).
    Sizing/vol-targeting shared with trend via `trend.size_positions`."""
    if asof is None:
        asof = prices.index[-1]
    c = signals_cache if signals_cache is not None else precompute(prices, index_prices, p)
    w = trend.size_positions(c["signal"].loc[asof], c["vol"].loc[asof], p)
    # cap each name's weight so one short can't lose >100% in a day and blow up the
    # book (single-name L/S hazard; ETF sleeves don't need this).
    if p.max_weight_per_name:
        w = w.clip(lower=-p.max_weight_per_name, upper=p.max_weight_per_name)
    return w


def run_lowrisk_backtest(prices: pd.DataFrame, index_prices: pd.Series,
                         p: LowRiskParams = DEFAULT_LOWRISK_PARAMS,
                         initial_capital: float = 100_000.0,
                         currency: str = "USD") -> dict:
    """Walk-forward backtest of the low-risk sleeve (long/short, costs always on).
    Decide weights at month-end t from data ≤ t, apply at t+1 — shared L/S engine."""
    from .lsbacktest import run_ls_backtest

    prices = prices.dropna(how="all")
    if len(prices) <= p.min_history_days:
        raise ValueError(f"lowrisk: not enough history ({len(prices)} rows)")

    cache = precompute(prices, index_prices, p)
    rebal_marks = prices.resample(p.rebalance).last().index

    schedule: dict[pd.Timestamp, pd.Series] = {}
    for d in rebal_marks:
        loc = prices.index.searchsorted(d, side="right") - 1
        if loc < p.min_history_days:
            continue
        asof = prices.index[loc]
        schedule[asof] = compute_lowrisk_targets(prices, index_prices, p, asof=asof,
                                                 signals_cache=cache)

    return run_ls_backtest(prices, schedule, p.cost_bps, "LOWRISK",
                           currency=currency, initial_capital=initial_capital)
