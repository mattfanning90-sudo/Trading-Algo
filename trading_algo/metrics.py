"""Performance statistics for a return / equity series.

THE measurement semantic layer (measurement-truth design §4): one module owns
every number a human reads. A measure is a labelled record — value, convention,
sample size — not a bare float whose annualisation the reader has to guess.

`periods_per_year` lives here (moved from `forex/marks.py`, which re-exports it)
because the annualisation convention and the measures that use it are one idea,
and having them in two places is how a 60-minute book came to be annualised at
252 trading days.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import RISK_FREE

# Daily bars: ~252 trading days a year. The DEFAULT annualisation, used when a
# series carries no usable DatetimeIndex to derive one from. Moved here from
# forex/fx_config.py, which now re-exports it.
ANNUALIZATION = 252


def periods_per_year(idx: pd.DatetimeIndex) -> float:
    """Periods-per-year implied by a DatetimeIndex's median bar spacing.

    Calendar-time convention (the project decision — see `forex/marks.py`'s
    module docstring): >= 12h spacing -> ``ANNUALIZATION`` (252); faster ->
    ``365.25*86400/secs`` capped at hourly (``24*365.25``), so a minute book does
    not pretend to 525,960 independent observations a year. Empty/degenerate
    indexes fall back to daily spacing (-> 252).
    """
    med = idx.to_series().diff().median() if len(idx) else pd.NaT
    secs = med.total_seconds() if pd.notna(med) and med.total_seconds() > 0 else 86400.0
    return min(ANNUALIZATION if secs >= 43200 else 365.25 * 86400.0 / secs, 24 * 365.25)


# Bound once so a `periods_per_year=` PARAMETER can shadow the public name
# inside a function without the function losing access to itself.
_ppy = periods_per_year


def _ppy_of(idx) -> float:
    """The annualisation a series' OWN index implies; 252 when it has no dates."""
    return _ppy(idx) if isinstance(idx, pd.DatetimeIndex) else float(ANNUALIZATION)


@dataclass(frozen=True)
class Measure:
    """One measured number plus the convention that produced it.

    `periods_per_year` is NaN for a measure that is not annualised (drawdown);
    `risk_free` is None when no risk-free rate was subtracted.
    """
    value: float
    name: str
    periods_per_year: float
    risk_free: float | None = None
    n: int = 0
    stderr: float | None = None

    def label(self) -> str:
        """e.g. "Sharpe (vs 3.5%, 252/yr)", "CAGR (252/yr)", "MaxDrawdown"."""
        parts: list[str] = []
        if self.risk_free is not None:
            parts.append(f"vs {self.risk_free:.1%}")
        if self.periods_per_year == self.periods_per_year:       # not NaN
            parts.append(f"{self.periods_per_year:g}/yr")
        return f"{self.name} ({', '.join(parts)})" if parts else self.name


def metric(metrics: dict, prefix: str):
    """Read one figure out of a `compute_metrics` dict by PREFIX.

    Two labels below carry their parameters — "Sharpe (vs 4.0%)" and
    "FinalEquity (AUD)" — so an exact-key lookup silently misses them. The
    reader lives next to the writer so callers don't each have to rediscover
    that; every consumer of these dicts routes through here."""
    return next((v for k, v in metrics.items() if k.startswith(prefix)), None)


# ---------------------------------------------------------------------------
# Primitives — every reported vol / Sharpe / CAGR / drawdown derives from these
# ---------------------------------------------------------------------------
def annualised_vol(rets: pd.Series, *, periods_per_year: float) -> float:
    """Annualised standard deviation of a return series (ddof=1).

    A single observation has no sample dispersion, so this is 0.0 rather than a
    silent ddof=1 nan."""
    r = rets.dropna()
    return float(r.std() * np.sqrt(periods_per_year)) if len(r) > 1 else 0.0


def sharpe(rets: pd.Series, *, periods_per_year: float,
           risk_free: float | None) -> Measure:
    """Annualised Sharpe. `risk_free=None` subtracts nothing and says so in the
    label. `stderr` is Lo's approximation, in the same annualised units."""
    r = rets.dropna()
    vol = annualised_vol(r, periods_per_year=periods_per_year)
    excess = float(r.mean() * periods_per_year)
    if risk_free is not None:
        excess -= risk_free
    value = float(excess / max(vol, 1e-9))
    se = None
    if len(r) > 2:
        sr_pp = value / np.sqrt(periods_per_year)
        se = float(np.sqrt((1.0 + 0.5 * sr_pp ** 2) / (len(r) - 1))
                   * np.sqrt(periods_per_year))
    return Measure(value=value, name="Sharpe",
                   periods_per_year=float(periods_per_year),
                   risk_free=risk_free, n=len(r), stderr=se)


def cagr(equity: pd.Series, *, periods_per_year: float) -> Measure:
    """Compound annual growth rate of an equity curve.

    `n` counts equity OBSERVATIONS, not intervals — the convention this repo has
    always used. A curve of N marks spans N-1 periods, so this runs one period
    fast (0.03% of the exponent on a 14-year daily backtest). Preserved
    deliberately so moving the formula here changes no published number; the
    off-by-one is recorded as a finding, not fixed in this task.
    """
    if len(equity) < 1 or float(equity.iloc[0]) == 0.0:
        return Measure(value=float("nan"), name="CAGR",
                       periods_per_year=float(periods_per_year), n=len(equity))
    n = len(equity)
    value = float((equity.iloc[-1] / equity.iloc[0]) ** (periods_per_year / n) - 1.0)
    return Measure(value=value, name="CAGR",
                   periods_per_year=float(periods_per_year), n=n)


def max_drawdown(equity: pd.Series) -> Measure:
    """Deepest peak-to-trough fall of an equity curve, as a negative fraction.

    Not annualised, so `periods_per_year` is NaN and `label()` omits it."""
    dd = equity / equity.cummax() - 1.0
    return Measure(value=float(dd.min()), name="MaxDrawdown",
                   periods_per_year=float("nan"), n=len(equity))


def compute_metrics(rets: pd.Series, equity: pd.Series,
                    risk_free: float = RISK_FREE,
                    currency: str = "AUD",
                    periods_per_year: float | None = None) -> dict:
    """Annualised performance summary assembled from the primitives above.

    `periods_per_year=None` derives the convention from `rets.index`, so a
    60-minute book annualises at 8766 and a daily one at 252 with NO call-site
    change — that single default is the fix for the hourly-annualised-at-252
    defect. A series with no DatetimeIndex falls back to 252.

    The returned KEYS are unchanged (pinned by
    `tests/test_metrics.py::test_compute_metrics_keys_are_unchanged`).
    """
    rets = rets.dropna()
    if len(rets) == 0 or equity.iloc[0] == 0:
        return {"error": "insufficient data"}

    ppy = _ppy_of(rets.index) if periods_per_year is None else float(periods_per_year)
    ann_ret = cagr(equity, periods_per_year=ppy).value
    ann_vol = annualised_vol(rets, periods_per_year=ppy)
    sr = sharpe(rets, periods_per_year=ppy, risk_free=risk_free)
    excess = float(rets.mean() * ppy - risk_free)
    downside = annualised_vol(rets[rets < 0], periods_per_year=ppy)
    # No (or too few) losing days -> downside deviation is 0/undefined. Fall
    # back to total volatility so Sortino stays finite instead of a silent nan
    # from the max(nan, 1e-9) idiom.
    if not (downside > 0):
        downside = ann_vol
    sortino = excess / downside if downside > 0 else float("nan")
    max_dd = max_drawdown(equity).value
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("nan")

    return {
        "CAGR": round(float(ann_ret), 4),
        "AnnVol": round(float(ann_vol), 4),
        f"Sharpe (vs {risk_free:.1%})": round(float(sr.value), 2),
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
    # Covariance and variance must share one ddof, else beta is biased by
    # (N-1)/N. np.cov(..., ddof=1) and Series.var() (pandas default ddof=1)
    # both normalise by N-1.
    var_b = float(b.var())
    beta = float(np.cov(s, b, ddof=1)[0, 1] / var_b) if var_b > 0 else float("nan")
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
