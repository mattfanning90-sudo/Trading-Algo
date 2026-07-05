"""Stress testing — does the edge survive paths we didn't happen to observe?

A single historical backtest is one draw from a distribution. This module asks
how lucky that draw was:

- `stationary_bootstrap` (Politis-Romano 1994): resample the return series in
  random-length blocks, preserving volatility clustering. IID resampling destroys
  that clustering and so *understates* drawdown risk — block/stationary methods are
  the honest choice for a serially-dependent series.
- `mc_summary`: P5/P50/P95 distribution of CAGR / Sharpe / MaxDD across thousands
  of bootstrap paths, plus CVaR and P(MaxDD worse than a threshold).
- `regime_conditional`: performance split by bull/bear (index vs 200d MA) and by
  realised-vol terciles — NO-lookahead labels — to expose a strategy whose entire
  edge lives in one regime.
- `cost_stress`: CAGR/Sharpe as transaction costs are multiplied 1×/2×/3× (exact,
  using the backtest's per-day cost series). An edge that dies at 2× is fragile.
- `drawdown_analytics`: max DD, Ulcer index, time-underwater, daily CVaR.

Magdon-Ismail benchmark for a zero-drift series: E[MaxDD] ≈ √(π/2)·σ·√T.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .metrics import compute_metrics

_PPY = 252


def stationary_bootstrap(returns: pd.Series, mean_block: int = 21,
                         n_paths: int = 2000, length: int | None = None,
                         seed: int = 0) -> np.ndarray:
    """Return an (n_paths × length) array of resampled returns. Block lengths are
    geometric with mean `mean_block` (restart prob p = 1/mean_block), so vol
    clustering survives. Wraps at the series end to stay well-defined."""
    r = np.asarray(returns.dropna(), dtype=float)
    T = len(r)
    if T == 0:
        return np.empty((0, 0))
    length = length or T
    rng = np.random.default_rng(seed)
    p = 1.0 / max(mean_block, 1)

    restart = rng.random((n_paths, length)) < p
    restart[:, 0] = True
    starts = rng.integers(0, T, size=(n_paths, length))
    pos = np.arange(length)
    last = np.where(restart, pos[None, :], 0)
    last = np.maximum.accumulate(last, axis=1)              # col of last restart
    start_at = np.take_along_axis(starts, last, axis=1)     # start chosen there
    idx = (start_at + (pos[None, :] - last)) % T
    return r[idx]


def _path_metrics(paths: np.ndarray, ppy: int = _PPY):
    eq = np.cumprod(1.0 + paths, axis=1)
    T = paths.shape[1]
    cagr = eq[:, -1] ** (ppy / T) - 1.0
    mu, sd = paths.mean(axis=1), paths.std(axis=1)
    sharpe = np.where(sd > 0, mu / sd * math.sqrt(ppy), 0.0)
    dd = (eq / np.maximum.accumulate(eq, axis=1) - 1.0).min(axis=1)
    return cagr, sharpe, dd


def mc_summary(returns: pd.Series, mean_block: int = 21, n_paths: int = 2000,
               dd_threshold: float = 0.30, seed: int = 0) -> dict:
    """Monte-Carlo (stationary-bootstrap) distribution of CAGR/Sharpe/MaxDD."""
    paths = stationary_bootstrap(returns, mean_block, n_paths, seed=seed)
    if paths.size == 0:
        return {}
    cagr, sharpe, dd = _path_metrics(paths)

    def pct(a):
        return {"p5": round(float(np.percentile(a, 5)), 4),
                "p50": round(float(np.percentile(a, 50)), 4),
                "p95": round(float(np.percentile(a, 95)), 4)}

    return {
        "n_paths": n_paths, "mean_block": mean_block,
        "CAGR": pct(cagr), "Sharpe": pct(sharpe), "MaxDD": pct(dd),
        "worst_MaxDD": round(float(dd.min()), 4),
        f"P(MaxDD>{dd_threshold:.0%})": round(float((dd <= -dd_threshold).mean()), 3),
        "P(Sharpe<0)": round(float((sharpe < 0).mean()), 3),
        "P(CAGR<0)": round(float((cagr < 0).mean()), 3),
    }


def drawdown_analytics(returns: pd.Series, cvar_alpha: float = 0.95) -> dict:
    """Depth AND duration of pain, plus daily tail risk."""
    r = returns.dropna()
    if len(r) == 0:
        return {}
    eq = (1 + r).cumprod()
    peak = eq.cummax()
    dd = eq / peak - 1.0
    underwater = dd < -1e-9
    ulcer = float(math.sqrt((dd[dd < 0] ** 2).mean())) if (dd < 0).any() else 0.0
    # longest underwater stretch (days)
    longest = cur = 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    var = float(np.percentile(r, (1 - cvar_alpha) * 100))
    cvar = float(r[r <= var].mean()) if (r <= var).any() else var
    # Magdon-Ismail zero-drift expected max drawdown benchmark
    sigma_T = float(r.std() * math.sqrt(len(r)))
    e_maxdd_zero_drift = math.sqrt(math.pi / 2.0) * sigma_T
    return {
        "max_drawdown": round(float(dd.min()), 4),
        "ulcer_index": round(ulcer, 4),
        "time_underwater_pct": round(float(underwater.mean()), 3),
        "longest_underwater_days": int(longest),
        f"daily_CVaR{cvar_alpha:.0%}": round(cvar, 4),
        "E[MaxDD]_zero_drift": round(-e_maxdd_zero_drift, 4),
    }


def regime_conditional(returns: pd.Series, index_prices: pd.Series,
                       ma: int = 200, vol_lookback: int = 63) -> dict:
    """Performance split by NO-lookahead regimes: bull/bear (index above/below its
    `ma`-day MA, both known at t) and realised-vol terciles. A strategy whose edge
    lives in only one regime is fragile."""
    r = returns.dropna()
    idx = index_prices.reindex(r.index).ffill()
    bull = (idx > idx.rolling(ma).mean())                    # causal
    rv = r.rolling(vol_lookback).std()
    lo, hi = rv.quantile(1 / 3), rv.quantile(2 / 3)

    def stats(sub: pd.Series) -> dict:
        sub = sub.dropna()
        if len(sub) < 2:
            return {"share": 0.0, "CAGR": float("nan"), "Sharpe": float("nan")}
        m = compute_metrics(sub, (1 + sub).cumprod())
        return {"share": round(len(sub) / len(r), 3),
                "CAGR": m["CAGR"],
                "Sharpe": next(v for k, v in m.items() if k.startswith("Sharpe"))}

    return {
        "bull": stats(r[bull.reindex(r.index).fillna(False)]),
        "bear": stats(r[~bull.reindex(r.index).fillna(True)]),
        "low_vol": stats(r[rv <= lo]),
        "high_vol": stats(r[rv >= hi]),
    }


def cost_stress(bt_result: dict, multipliers=(1.0, 2.0, 3.0)) -> dict:
    """Re-derive CAGR/Sharpe at multiplied transaction costs (exact: the backtest
    already subtracts 1× cost per day, so stressed = returns − (m−1)·cost_day)."""
    rets = bt_result["returns"]
    costs = bt_result.get("costs")
    if costs is None or len(costs) == 0:
        return {}
    costs = costs.reindex(rets.index).fillna(0.0)
    out = {}
    for m in multipliers:
        stressed = rets - (m - 1.0) * costs
        met = compute_metrics(stressed, (1 + stressed).cumprod())
        out[f"{m:.0f}x"] = {"CAGR": met["CAGR"],
                            "Sharpe": next(v for k, v in met.items() if k.startswith("Sharpe"))}
    return out
