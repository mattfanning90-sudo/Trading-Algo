"""Time-series (trend) momentum sleeve — the diversifier.

Unlike the equity sleeves (cross-sectional momentum: rank stocks, hold the top N
*long*), this is **time-series** momentum: each asset is traded on its OWN trend
— long when it's trending up, short (or flat) when trending down — and sized by
inverse volatility toward a portfolio vol target. Run on a diversified basket of
liquid ETFs spanning equities, bonds, commodities and FX, trend has ~a century
of out-of-sample evidence, near-zero correlation to equities, and a tendency to
make money in *sustained* equity selloffs ("crisis alpha", e.g. 2008 and 2022).

Honest framing (from the research):
- It is a **diversifier, not a return engine** — expect a modest standalone
  Sharpe (~0.4 net of costs) and long, painful flat stretches (2009-2019 was
  basically flat). Its value is the low/negative correlation it adds to a
  long-equity book, which lifts the *combined* Sharpe and cuts drawdown.
- The short leg and any gross exposure > 1.0 require **futures or margin**;
  set `long_only=True` (and `max_gross<=1.0`) for an unlevered ETF-only version,
  which captures the "get out of the way" benefit but little short-side crisis
  alpha.

No lookahead: every signal at date t uses data <= t; the backtester executes the
resulting weights at t+1 — same discipline as the equity sleeves.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DEFAULT_TREND_PARAMS, TrendParams


def trend_signal(prices: pd.DataFrame, p: TrendParams) -> pd.DataFrame:
    """Multi-horizon time-series-momentum signal in [-1, 1] per asset.

    The average of the *sign* of the trailing total return over each lookback
    (AQR-style 1/3/12-month blend): +1 = every horizon up, -1 = every horizon
    down. Robust to outliers (sign, not magnitude). With `long_only`, negative
    signals are floored at 0 (long-or-flat)."""
    parts = [np.sign(prices / prices.shift(lb) - 1.0) for lb in p.lookbacks]
    s = sum(parts) / len(parts)
    if p.long_only:
        s = s.clip(lower=0.0)
    return s


def _realised_vol(prices: pd.DataFrame, p: TrendParams) -> pd.DataFrame:
    """Annualised trailing volatility per asset (for inverse-vol sizing)."""
    rets = prices.pct_change(fill_method=None)
    return rets.rolling(p.vol_lookback).std() * np.sqrt(252)


def precompute(prices: pd.DataFrame, p: TrendParams) -> dict:
    """Build the trend signal + vol frames once for reuse across rebalances
    (causal, so per-date `.loc[asof]` reads match recomputing — see tests)."""
    return {"signal": trend_signal(prices, p), "vol": _realised_vol(prices, p)}


def size_positions(signal: pd.Series, vols: pd.Series, p: TrendParams) -> pd.Series:
    """Turn a per-asset signal into signed target weights.

    Inverse-vol sizing (each asset's risk ∝ |signal|), then scale the whole book
    to `target_vol` using the constant-average-correlation approximation, capped
    at `max_vol_scale` leverage of the raw book and `max_gross` gross exposure.
    Gross is the sum of ABSOLUTE weights (long + short)."""
    raw = (signal / vols.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    raw = raw[raw != 0.0]
    if raw.empty:
        return pd.Series(dtype=float)

    wv = (raw * vols.reindex(raw.index)).dropna()        # per-asset vol contribution
    rho = p.avg_correlation
    port_var = (1.0 - rho) * (wv**2).sum() + rho * (wv.sum() ** 2)
    port_vol = float(np.sqrt(max(port_var, 0.0)))

    scale = min(p.target_vol / max(port_vol, 1e-9), p.max_vol_scale)
    w = raw * scale

    gross = float(w.abs().sum())
    if gross > p.max_gross:
        w = w * (p.max_gross / gross)
    return w


def compute_trend_targets(prices: pd.DataFrame, p: TrendParams = DEFAULT_TREND_PARAMS,
                          asof: pd.Timestamp | None = None,
                          signals_cache: dict | None = None) -> pd.Series:
    """Signed target weights for one rebalance date (default: latest available).
    The single source of truth for trend weights — backtest and any future paper
    trading both route through here (mirrors the equity sleeves' invariant)."""
    if asof is None:
        asof = prices.index[-1]
    c = signals_cache if signals_cache is not None else precompute(prices, p)
    return size_positions(c["signal"].loc[asof], c["vol"].loc[asof], p)


def run_trend_backtest(prices: pd.DataFrame, p: TrendParams = DEFAULT_TREND_PARAMS,
                       initial_capital: float = 100_000.0,
                       currency: str = "USD") -> dict:
    """Walk-forward backtest of the trend sleeve (long/short, costs always on).

    Same no-lookahead discipline as the equity backtester: decide weights at
    month-end t from data <= t, apply them from t+1, charge commission+slippage
    on turnover (gross of long+short). Returns a result dict shaped like
    `backtest.run_backtest` (returns / equity / metrics / weights)."""
    from .lsbacktest import run_ls_backtest

    prices = prices.dropna(how="all")
    if len(prices) <= p.min_history_days:
        raise ValueError(f"trend: not enough history ({len(prices)} rows)")

    cache = precompute(prices, p)
    rebal_marks = prices.resample(p.rebalance).last().index

    schedule: dict[pd.Timestamp, pd.Series] = {}
    for d in rebal_marks:
        loc = prices.index.searchsorted(d, side="right") - 1
        if loc < p.min_history_days:
            continue
        asof = prices.index[loc]
        schedule[asof] = compute_trend_targets(prices, p, asof=asof, signals_cache=cache)

    return run_ls_backtest(prices, schedule, p.cost_bps, "TREND",
                           currency=currency, initial_capital=initial_capital)
