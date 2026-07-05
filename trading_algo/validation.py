"""Overfitting-aware performance statistics — the ONE shared implementation.

A backtest Sharpe is almost meaningless without correcting for (a) track-record
length and non-normal returns, and (b) how many strategy variants you tried to
find it. This module implements the standard López de Prado corrections in pure
NumPy, and is the single home for that math (foundation P0-E): the equity sleeves
import it here, and the FX subsystem re-exports it from
`trading_algo.forex.validation` for backward compatibility — so there is no second
copy to drift.

Core stats:
  * `probabilistic_sharpe_ratio` (PSR) — P(true SR > benchmark) given length, skew,
    kurtosis.
  * `deflated_sharpe_ratio` (DSR) — PSR against the *expected maximum* Sharpe under
    the null of zero skill across N trials (corrects selection bias).
  * `pbo` — Probability of Backtest Overfitting via Combinatorially Symmetric
    Cross-Validation (CSCV).

Equity-facing helpers (backlog F2 / F19):
  * `overfitting_gate` — DSR + PBO pass/fail over a config return matrix.
  * `sharpe_haircut` — expected live Sharpe after deducting the selection-luck
    benchmark.

References (full citations in docs/FX_DEEP_RESEARCH.md): Bailey & López de Prado,
*The Deflated Sharpe Ratio* (2014); *The Probability of Backtest Overfitting*
(2015); *Advances in Financial Machine Learning* (2018).
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np

_GAMMA = 0.5772156649015329   # Euler–Mascheroni
_E = math.e
TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Normal CDF / inverse CDF (no scipy dependency)
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if p <= 0.0:
        return -np.inf
    if p >= 1.0:
        return np.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---------------------------------------------------------------------------
# Sharpe statistics
# ---------------------------------------------------------------------------
def sharpe_ratio(returns: np.ndarray) -> float:
    """Per-period (NOT annualised) Sharpe of a return series."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std())


def probabilistic_sharpe_ratio(returns: np.ndarray, benchmark_sr: float = 0.0) -> float:
    """PSR: probability the true (per-period) Sharpe exceeds `benchmark_sr`.

    Accounts for sample length, skewness and kurtosis (Bailey & López de Prado).
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 3 or r.std() == 0:
        return 0.0
    sr = r.mean() / r.std()
    g3 = float(((r - r.mean()) ** 3).mean() / r.std() ** 3)          # skew
    g4 = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)          # kurtosis (normal=3)
    denom = math.sqrt(max(1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2, 1e-12))
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / denom
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum (per-period) Sharpe under the null of zero skill across
    `n_trials` independent strategy configurations (false-strategy theorem)."""
    if n_trials < 1:
        return 0.0
    if n_trials == 1 or sr_variance <= 0:
        return 0.0
    v = math.sqrt(sr_variance)
    return v * ((1 - _GAMMA) * _norm_ppf(1 - 1.0 / n_trials)
                + _GAMMA * _norm_ppf(1 - 1.0 / (n_trials * _E)))


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int,
                          sr_variance: float | None = None) -> float:
    """DSR: PSR evaluated against the deflated benchmark (expected max SR under
    the null). `sr_variance` is the variance of the (per-period) Sharpes across
    the `n_trials` configurations you tried — pass it when you have it. If omitted
    with n_trials>1, a conservative estimate from the strategy's own SR
    uncertainty is used (better to supply the real cross-trial dispersion)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return 0.0
    if sr_variance is None:
        sr = sharpe_ratio(r)
        sr_variance = (1.0 + 0.5 * sr ** 2) / (len(r) - 1)   # SE² of a single SR
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(r, benchmark_sr=sr0)


def pbo(returns_matrix: np.ndarray, n_splits: int = 10) -> float:
    """Probability of Backtest Overfitting via CSCV.

    `returns_matrix` is (T observations × N configurations). Returns the fraction
    of symmetric IS/OOS combinations in which the in-sample-best configuration
    lands below the OOS median — the probability your selection is overfit.
    """
    M = np.asarray(returns_matrix, dtype=float)
    T, N = M.shape
    if N < 2:
        return 0.0
    S = n_splits - (n_splits % 2)
    S = max(2, min(S, T))
    rows = np.array_split(np.arange(T), S)
    groups = [r for r in rows if len(r)]
    S = len(groups)
    logits = []
    for combo in combinations(range(S), S // 2):
        is_idx = np.concatenate([groups[i] for i in combo])
        oos_idx = np.concatenate([groups[i] for i in range(S) if i not in combo])
        is_sr = np.array([sharpe_ratio(M[is_idx, j]) for j in range(N)])
        oos_sr = np.array([sharpe_ratio(M[oos_idx, j]) for j in range(N)])
        best = int(np.argmax(is_sr))
        # OOS rank (percentile) of the IS-best config, then logit.
        rank = (oos_sr < oos_sr[best]).mean()
        rank = min(max(rank, 1.0 / (N + 1)), 1.0 - 1.0 / (N + 1))
        logits.append(math.log(rank / (1.0 - rank)))
    if not logits:
        return 0.0
    return float(np.mean(np.asarray(logits) <= 0.0))


# ---------------------------------------------------------------------------
# Equity-facing helpers (backlog F2 gate + F19 Sharpe haircut)
# ---------------------------------------------------------------------------
def annualise_sharpe(per_period_sr: float, periods_per_year: int = TRADING_DAYS) -> float:
    return float(per_period_sr) * math.sqrt(periods_per_year)


def sr_variance_across(sharpes) -> float:
    """Variance of per-period Sharpes across configurations — the correct DSR
    input when you actually ran a grid (better than the single-SR estimate)."""
    a = np.asarray([s for s in sharpes if np.isfinite(s)], dtype=float)
    return float(a.var(ddof=1)) if len(a) > 1 else 0.0


def deflation_summary(returns: np.ndarray, n_trials: int,
                      sr_variance: float | None = None) -> dict:
    """PSR + DSR + annualised Sharpe for one return series deflated for n_trials."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    per = sharpe_ratio(r)
    return {
        "n_obs": int(len(r)),
        "n_trials": int(n_trials),
        "sharpe_ann": round(annualise_sharpe(per), 3),
        "psr": round(probabilistic_sharpe_ratio(r, 0.0), 4),
        "dsr": round(deflated_sharpe_ratio(r, n_trials, sr_variance), 4),
    }


def sharpe_haircut(returns: np.ndarray, n_trials: int,
                   sr_variance: float | None = None) -> dict:
    """F19: expected live Sharpe after deducting the selection-luck benchmark.

    haircut = raw − E[max Sharpe under the null across n_trials]  (per-period,
    then annualised, floored at 0). With n_trials == 1 the deflation is 0, so the
    haircut equals the raw Sharpe.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    per = sharpe_ratio(r)
    if sr_variance is None:
        sr_variance = (1.0 + 0.5 * per ** 2) / max(len(r) - 1, 1)
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    hc = max(per - sr0, 0.0)
    return {
        "raw_sharpe_ann": round(annualise_sharpe(per), 3),
        "deflation_ann": round(annualise_sharpe(sr0), 3),
        "haircut_sharpe_ann": round(annualise_sharpe(hc), 3),
        "dsr": round(deflated_sharpe_ratio(r, n_trials, sr_variance), 4),
    }


def _gate_verdict(dsr: float, p, dsr_min: float, pbo_max: float) -> str:
    if p is None:
        return f"{'PASS' if dsr >= dsr_min else 'FAIL'} — DSR {dsr:.2f} (need >= {dsr_min})"
    ok = dsr >= dsr_min and p <= pbo_max
    return (f"{'PASS' if ok else 'FAIL'} — DSR {dsr:.2f} (>= {dsr_min}?), "
            f"PBO {p:.2f} (<= {pbo_max}?)")


def overfitting_gate(returns_matrix: np.ndarray, n_trials: int,
                     dsr_min: float = 0.95, pbo_max: float = 0.5,
                     sr_variance: float | None = None) -> dict:
    """F2 gate. `returns_matrix` is (T × N) per-period OOS returns across the N
    configurations tried. Picks the in-sample-best column, deflates its Sharpe
    for `n_trials`, and computes PBO across the matrix. A single column skips PBO
    (needs >= 2 configs) and gates on DSR alone."""
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim == 1:
        M = M.reshape(-1, 1)
    if M.shape[1] < 2:
        col = M[:, 0]
        dsr = deflated_sharpe_ratio(col, n_trials, sr_variance)
        return {"dsr": round(dsr, 4), "pbo": None, "n_trials": int(n_trials),
                "n_configs": 1, "best_config": 0,
                "passed": bool(dsr >= dsr_min),
                "verdict": _gate_verdict(dsr, None, dsr_min, pbo_max)}
    sharpes = [sharpe_ratio(M[:, j]) for j in range(M.shape[1])]
    best = int(np.argmax(sharpes))
    if sr_variance is None:
        sr_variance = sr_variance_across(sharpes)
    dsr = deflated_sharpe_ratio(M[:, best], n_trials, sr_variance)
    p = pbo(M)
    return {"dsr": round(dsr, 4), "pbo": round(p, 4), "n_trials": int(n_trials),
            "n_configs": int(M.shape[1]), "best_config": best,
            "passed": bool(dsr >= dsr_min and p <= pbo_max),
            "verdict": _gate_verdict(dsr, p, dsr_min, pbo_max)}
