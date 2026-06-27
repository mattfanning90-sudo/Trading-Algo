"""Overfitting-aware performance statistics (López de Prado family).

A backtest Sharpe is almost meaningless without correcting for (a) track-record
length and non-normal returns, and (b) how many strategy variants you tried to
find it. This module implements the standard corrections, in pure NumPy:

* `probabilistic_sharpe_ratio` (PSR) — P(true SR > benchmark) given length, skew,
  kurtosis.
* `deflated_sharpe_ratio` (DSR) — PSR against the *expected maximum* Sharpe under
  the null of zero skill across N trials (corrects selection bias).
* `pbo` — Probability of Backtest Overfitting via Combinatorially Symmetric
  Cross-Validation (CSCV): the chance the in-sample-best config is below-median
  out-of-sample.
* `bet_size_from_prob` — López de Prado's meta-label → position-size map.

These are what make any "deep learning found an edge" claim credible — or honestly
debunk it. References (full citations in docs/FX_DEEP_RESEARCH.md): Bailey & López
de Prado, *The Deflated Sharpe Ratio* (2014); *The Probability of Backtest
Overfitting* (2015); *Advances in Financial Machine Learning* (2018).
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np

_GAMMA = 0.5772156649015329   # Euler–Mascheroni
_E = math.e


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


def bet_size_from_prob(p: np.ndarray) -> np.ndarray:
    """Map a meta-model probability of a correct call to a size in [0, 1].

    López de Prado bet sizing: z = (p − 0.5)/√(p(1−p)); size = 2·Φ(z) − 1.
    Multiply by the primary signal's sign for a signed position.
    """
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    z = (p - 0.5) / np.sqrt(p * (1 - p))
    vec = np.vectorize(_norm_cdf)
    return 2.0 * vec(z) - 1.0
