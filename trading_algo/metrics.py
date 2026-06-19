"""Performance statistics for a return / equity series."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import RISK_FREE


def compute_metrics(rets: pd.Series, equity: pd.Series,
                    risk_free: float = RISK_FREE,
                    currency: str = "AUD") -> dict:
    """Annualised performance summary. `rets` are daily fractional returns;
    `equity` is the matching equity curve."""
    rets = rets.dropna()
    if len(rets) == 0 or equity.iloc[0] == 0:
        return {"error": "insufficient data"}

    n = len(rets)
    ann_ret = (equity.iloc[-1] / equity.iloc[0]) ** (252 / n) - 1
    ann_vol = rets.std() * np.sqrt(252)
    excess = rets.mean() * 252 - risk_free
    sharpe = excess / max(ann_vol, 1e-9)
    downside = rets[rets < 0].std() * np.sqrt(252)
    sortino = excess / max(downside, 1e-9)
    dd = equity / equity.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("nan")

    return {
        "CAGR": round(float(ann_ret), 4),
        "AnnVol": round(float(ann_vol), 4),
        f"Sharpe (vs {risk_free:.1%})": round(float(sharpe), 2),
        "Sortino": round(float(sortino), 2),
        "MaxDrawdown": round(max_dd, 4),
        "Calmar": round(float(calmar), 2),
        "WinRate(days)": round(float((rets > 0).mean()), 3),
        f"FinalEquity ({currency})": round(float(equity.iloc[-1]), 0),
    }


def benchmark_stats(strat_rets: pd.Series, bench_rets: pd.Series,
                    risk_free: float = RISK_FREE) -> dict:
    """Strategy-vs-benchmark stats: benchmark CAGR, active return, beta, Jensen's
    alpha, tracking error and information ratio. Both inputs are daily returns."""
    df = pd.concat([strat_rets.rename("s"), bench_rets.rename("b")], axis=1).dropna()
    if len(df) < 2:
        return {}
    s, b = df["s"], df["b"]

    bench_cagr = (1 + b).prod() ** (252 / len(b)) - 1
    strat_cagr = (1 + s).prod() ** (252 / len(s)) - 1
    var_b = float(b.var())
    beta = float(((s - s.mean()) * (b - b.mean())).mean() / var_b) if var_b > 0 else float("nan")
    alpha = (s.mean() * 252 - risk_free) - beta * (b.mean() * 252 - risk_free)
    active = s - b
    te = float(active.std() * np.sqrt(252))
    info = float(active.mean() * 252 / te) if te > 0 else float("nan")

    return {
        "BenchmarkCAGR": round(float(bench_cagr), 4),
        "ActiveReturn": round(float(strat_cagr - bench_cagr), 4),
        "Beta": round(beta, 2),
        "Alpha": round(float(alpha), 4),
        "TrackingError": round(te, 4),
        "InfoRatio": round(info, 2),
    }
