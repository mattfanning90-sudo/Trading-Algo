"""Overfitting / statistical-significance controls for backtest Sharpe ratios.

The Bailey & López de Prado toolkit — the antidote to "I tried 1000 configs and
kept the best" (which manufactures great-looking Sharpes from pure luck):

- Probabilistic Sharpe Ratio (PSR) + Minimum Track Record Length (MinTRL):
  is the Sharpe distinguishable from zero given sample length, skew and kurtosis?
- Deflated Sharpe Ratio (DSR): PSR but benchmarked against the *expected maximum*
  Sharpe achievable by luck across N trials, so a sweep-selected Sharpe is judged
  fairly. DSR ≳ 0.95 ⇒ unlikely to be a multiple-testing artifact.
- Probability of Backtest Overfitting (PBO) via CSCV: validates the selection
  *process* — the chance the in-sample-best config underperforms the OOS median.

Inputs are daily fractional returns unless noted. No scipy dependency: the normal
CDF uses math.erf; the inverse-CDF uses Acklam's rational approximation.
Refs: Bailey & López de Prado (2014) SSRN 2460551; Bailey, Borwein, LdP & Zhu
(2017) "The Probability of Backtest Overfitting".
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pandas as pd

EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


def _moments(rets) -> tuple[int, float, float, float]:
    """(n, per-period Sharpe, skewness, non-excess kurtosis) of a return series."""
    r = np.asarray(rets, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 2:
        return n, 0.0, 0.0, 3.0
    mu, sd = float(r.mean()), float(r.std(ddof=0))
    if sd == 0:
        return n, 0.0, 0.0, 3.0
    z = (r - mu) / sd
    return n, mu / sd, float((z**3).mean()), float((z**4).mean())


def _sr_var_factor(sr: float, skew: float, kurt: float) -> float:
    """Variance factor of the Sharpe estimator (Lo 2002 / Bailey-LdP), kurt
    non-excess (normal=3): 1 − skew·SR + ((kurt−1)/4)·SR²."""
    return max(1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr, 1e-12)


def probabilistic_sharpe_ratio(rets, sr_benchmark: float = 0.0) -> float:
    """P(true Sharpe > benchmark), adjusted for skew/kurtosis & sample length.
    `sr_benchmark` is a PER-PERIOD Sharpe (0 = 'has any skill')."""
    n, sr, skew, kurt = _moments(rets)
    if n < 3:
        return float("nan")
    se = math.sqrt(_sr_var_factor(sr, skew, kurt) / (n - 1))
    return _norm_cdf((sr - sr_benchmark) / se)


def min_track_record_length(rets, sr_benchmark: float = 0.0,
                            confidence: float = 0.95) -> float:
    """Observations needed for PSR to reach `confidence`. If it exceeds your
    actual sample, the track record is too short to trust the Sharpe."""
    n, sr, skew, kurt = _moments(rets)
    if sr <= sr_benchmark:
        return float("inf")
    z = _norm_ppf(confidence)
    return 1.0 + _sr_var_factor(sr, skew, kurt) * (z / (sr - sr_benchmark)) ** 2


def expected_max_sharpe(var_trials: float, n_trials: int) -> float:
    """Expected MAX per-period Sharpe achievable by luck across `n_trials`
    independent strategies whose Sharpes have variance `var_trials` (Gumbel /
    extreme-value approximation)."""
    if n_trials < 2 or var_trials <= 0:
        return 0.0
    g = EULER_MASCHERONI
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_trials) * ((1.0 - g) * z1 + g * z2)


def deflated_sharpe_ratio(rets, trial_sharpes, periods_per_year: int = 252) -> dict:
    """Deflated Sharpe Ratio. `trial_sharpes` = ANNUALISED Sharpes of every config
    tried (e.g. the parameter sweep). Returns dict with the DSR probability, the
    deflated benchmark, and N. DSR ≳ 0.95 ⇒ the Sharpe likely survives selection
    bias."""
    n, sr, skew, kurt = _moments(rets)                       # sr per-period
    ts = np.asarray([t for t in trial_sharpes if t == t], dtype=float)
    N = len(ts)
    if n < 3 or N < 2:
        return {"dsr": float("nan"), "n_trials": N, "sr0_annual": float("nan")}
    var_period = float(np.var(ts, ddof=1)) / periods_per_year   # de-annualise
    sr0 = expected_max_sharpe(var_period, N)
    se = math.sqrt(_sr_var_factor(sr, skew, kurt) / (n - 1))
    dsr = _norm_cdf((sr - sr0) / se)
    return {"dsr": round(float(dsr), 4), "n_trials": N,
            "sr0_annual": round(float(sr0 * math.sqrt(periods_per_year)), 3)}


def pbo_cscv(perf_matrix, n_splits: int = 8) -> dict:
    """Probability of Backtest Overfitting via Combinatorially Symmetric CV.

    `perf_matrix`: T×N (rows = time periods, cols = strategy configs), each cell a
    per-period performance (return). Splits the rows into `n_splits` contiguous
    chunks; over every way to pick half as in-sample, takes the IS-best config and
    records its out-of-sample rank. PBO = fraction of splits where the IS-best
    lands below the OOS median (logit ≤ 0). PBO near 0 = robust selection; ≳ 0.5 =
    selection is no better than chance (severe overfitting)."""
    M = np.asarray(perf_matrix, dtype=float)
    if M.ndim != 2:
        return {"pbo": float("nan"), "n_combinations": 0}
    T, N = M.shape
    S = n_splits - (n_splits % 2)
    if S < 2 or N < 2 or T < S:
        return {"pbo": float("nan"), "n_combinations": 0}
    chunks = np.array_split(np.arange(T), S)
    logits = []
    for is_sel in combinations(range(S), S // 2):
        is_rows = np.concatenate([chunks[i] for i in is_sel])
        oos_rows = np.concatenate([chunks[i] for i in range(S) if i not in is_sel])
        n_star = int(np.argmax(M[is_rows].mean(axis=0)))
        oos_rank = float(pd.Series(M[oos_rows].mean(axis=0)).rank().iloc[n_star])
        w = min(max(oos_rank / (N + 1.0), 1e-6), 1.0 - 1e-6)
        logits.append(math.log(w / (1.0 - w)))
    arr = np.asarray(logits)
    return {"pbo": round(float((arr <= 0).mean()), 4),
            "n_combinations": len(arr),
            "logit_median": round(float(np.median(arr)), 3)}
