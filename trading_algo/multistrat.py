"""Multi-strategy combiner — read uncorrelated strategy return streams and build
ONE book that is a strong **upside taker** and **downside mitigator**.

The whole research arc landed here: no single signal is the edge (published signals
lose ~58% post-publication; our own momentum book collapsed to ~0 Sharpe once
de-biased). The edge is *combining a few genuinely uncorrelated, evidence-backed
premia and letting risk management do the work*. This module does exactly that:

  - reads N strategy return streams (e.g. equity momentum, trend, carry),
  - sizes them by RISK — inverse-vol or equal-risk-contribution (ERC) — not dollars,
  - vol-targets the combined book (Harvey et al.: higher Sharpe, shallower tails),
  - rebalances on a cadence with NO lookahead (weights at t use data ≤ t, applied t+1).

Intended division of labour: equities / carry are the *upside takers* (drive bull
markets); trend is the *downside mitigator* (convex crisis alpha — pays in crashes).
Combining a convex stream with concave ones, risk-sized, gives better upside/downside
*capture asymmetry* than any single sleeve. `capture_ratios()` measures it.

No scipy: ERC uses the cyclical-coordinate-descent algorithm (Griveau-Billion 2013).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_PPY = 252


def inverse_vol_weights(vols: pd.Series) -> pd.Series:
    """Weights ∝ 1/vol (risk-parity-lite; exact ERC when correlations are equal)."""
    iv = (1.0 / vols.replace(0, np.nan)).dropna()
    return iv / iv.sum() if iv.sum() > 0 else iv


def risk_parity_weights(cov: pd.DataFrame, iters: int = 250) -> pd.Series:
    """Equal-risk-contribution weights via cyclical coordinate descent.

    Solves, per coordinate, a·wᵢ² + bᵢ·wᵢ − cᵢ = 0 where a=Σᵢᵢ,
    bᵢ=Σ_{j≠i} wⱼΣᵢⱼ, cᵢ = risk budget (1/N), then normalises. Converges for a
    positive-definite covariance; falls back to inverse-vol on degenerate input."""
    S = cov.to_numpy(dtype=float)
    n = S.shape[0]
    if n == 0:
        return pd.Series(dtype=float)
    vols = np.sqrt(np.clip(np.diag(S), 1e-18, None))
    w = (1.0 / vols)
    w = w / w.sum()
    budget = 1.0 / n
    for _ in range(iters):
        for i in range(n):
            a = S[i, i]
            b = float(S[i, :] @ w) - a * w[i]      # Σ_{j≠i} wⱼ Σ_ij
            if a <= 0:
                continue
            w[i] = (-b + np.sqrt(b * b + 4.0 * a * budget)) / (2.0 * a)
        s = w.sum()
        if s > 0:
            w = w / s
    if not np.all(np.isfinite(w)) or w.sum() <= 0:
        return inverse_vol_weights(pd.Series(vols, index=cov.index))
    return pd.Series(w / w.sum(), index=cov.index)


def _target_weights(window: pd.DataFrame, method: str) -> pd.Series:
    """Risk-based weights from a trailing return window (one rebalance)."""
    vols = window.std() * np.sqrt(_PPY)
    if method == "equal":
        cols = vols.index
        return pd.Series(1.0 / len(cols), index=cols)
    if method == "erc":
        cov = window.cov() * _PPY
        return risk_parity_weights(cov)
    return inverse_vol_weights(vols)        # default: inverse-vol


def combine(streams: dict[str, pd.Series], target_vol: float = 0.10,
            method: str = "invvol", lookback: int = 126,
            rebalance: str = "ME", max_leverage: float = 1.5,
            avg_correlation: float | None = None) -> dict:
    """Combine strategy return streams into one risk-managed book.

    `streams`: name -> daily fractional returns. `method`: 'invvol' | 'erc' |
    'equal'. The combined book is scaled toward `target_vol` (capped at
    `max_leverage` gross) using the trailing covariance. No lookahead: weights are
    decided from data ≤ each rebalance date and applied from the next day.
    """
    R = pd.DataFrame(streams).dropna(how="all").fillna(0.0)
    if R.shape[1] == 0 or len(R) <= lookback:
        return {"returns": pd.Series(dtype=float), "weights": pd.DataFrame()}

    rebal_dates = R.resample(rebalance).last().index
    wrows: dict[pd.Timestamp, pd.Series] = {}
    for d in rebal_dates:
        loc = R.index.searchsorted(d, side="right") - 1
        if loc < lookback:
            continue
        asof = R.index[loc]
        window = R.iloc[loc - lookback + 1: loc + 1]
        w = _target_weights(window, method).reindex(R.columns).fillna(0.0)
        # scale to target vol via the trailing covariance of the weighted book
        cov = window.cov() * _PPY
        port_var = float(w.values @ cov.to_numpy() @ w.values)
        port_vol = np.sqrt(max(port_var, 1e-12))
        scale = min(target_vol / port_vol, max_leverage / max(w.abs().sum(), 1e-9))
        wrows[asof] = w * scale

    if not wrows:
        return {"returns": pd.Series(dtype=float), "weights": pd.DataFrame()}

    # daily weights = step-function of the rebalance weights, applied from t+1
    weights = pd.DataFrame(wrows).T.reindex(R.index).ffill().shift(1).fillna(0.0)
    combined = (weights * R).sum(axis=1)
    combined = combined.loc[weights.dropna(how="all").index[0]:]
    return {"returns": combined,
            "equity": (1 + combined).cumprod(),
            "weights": weights,
            "gross": weights.abs().sum(axis=1)}


def validate_combo(streams: dict[str, pd.Series], target_vol: float = 0.12,
                   base_method: str = "erc", max_leverage: float = 1.5) -> dict:
    """Run the combiner across its own hyperparameter grid (method × lookback ×
    vol target) to feed the overfitting tests: Deflated Sharpe needs the trial
    Sharpes, PBO needs the monthly return matrix. This asks the honest question —
    does the multi-strat book's edge survive selection over ITS OWN knobs, or did
    we just pick the lucky combiner config? Returns the base combined returns plus
    the trial Sharpes and the T×N monthly matrix."""
    base = combine(streams, target_vol=target_vol, method=base_method,
                   max_leverage=max_leverage)["returns"]
    sharpes: list[float] = []
    monthly: dict[str, pd.Series] = {}
    for m in ("invvol", "erc", "equal"):
        for lb in (63, 126, 252):
            for tv in (0.08, 0.10, 0.12):
                r = combine(streams, target_vol=tv, method=m, lookback=lb,
                            max_leverage=max_leverage)["returns"]
                r = r.dropna()
                if len(r) < 60:
                    continue
                sharpes.append(float(r.mean() / r.std() * np.sqrt(_PPY)) if r.std() else float("nan"))
                monthly[f"{m}_{lb}_{int(tv*100)}"] = (1 + r).resample("ME").prod() - 1.0
    mat = pd.DataFrame(monthly).dropna(how="any")
    return {"base": base, "trial_sharpes": sharpes, "perf_matrix": mat}


def capture_ratios(returns: pd.Series, benchmark: pd.Series,
                   period: str = "ME") -> dict:
    """Upside/downside capture vs a benchmark (the upside-taker / downside-mitigator
    scorecard). Up-capture = avg strategy return in benchmark-up periods ÷ avg
    benchmark return there; down-capture likewise for down periods. Want UP high,
    DOWN low (a ratio > 1 means asymmetric — taking upside, mitigating downside)."""
    s = ((1 + returns).resample(period).prod() - 1).dropna()
    b = ((1 + benchmark).resample(period).prod() - 1).dropna()
    df = pd.concat([s.rename("s"), b.rename("b")], axis=1).dropna()
    if df.empty:
        return {}
    up, dn = df[df.b > 0], df[df.b < 0]
    up_cap = float(up.s.mean() / up.b.mean()) if len(up) and up.b.mean() else float("nan")
    dn_cap = float(dn.s.mean() / dn.b.mean()) if len(dn) and dn.b.mean() else float("nan")
    return {
        "up_capture": round(up_cap, 3),
        "down_capture": round(dn_cap, 3),
        "capture_ratio": round(up_cap / dn_cap, 2) if dn_cap not in (0, float("nan")) and dn_cap == dn_cap and dn_cap != 0 else float("nan"),
    }
