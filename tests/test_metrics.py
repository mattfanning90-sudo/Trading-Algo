"""Benchmark statistics."""
import math

import numpy as np
import pandas as pd

from trading_algo.metrics import benchmark_stats, compute_metrics


def test_benchmark_stats_against_self():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 400))
    s = benchmark_stats(r, r)
    assert abs(s["Beta"] - 1.0) < 0.01          # a series has beta 1 to itself
    assert abs(s["ActiveReturn"]) < 1e-6        # …and no active return
    for k in ("BenchmarkCAGR", "Alpha", "TrackingError", "InfoRatio"):
        assert k in s


def test_beta_against_self_is_exactly_one():
    # Beta of a series against itself is 1.0 exactly, for ANY sample size.
    # A ddof mismatch between covariance (population, /N) and variance
    # (sample, /N-1) biases it by (N-1)/N, which at small N shows up even
    # after 2dp rounding (e.g. N=50 -> 0.98).
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.0005, 0.01, 50))
    s = benchmark_stats(r, r)
    assert s["Beta"] == 1.0


def test_sortino_finite_when_no_losing_days():
    # All-positive returns -> no downside deviation. Sortino must stay a
    # finite, non-nan number rather than being silently poisoned to nan by
    # the max(nan, 1e-9) idiom.
    rets = pd.Series([0.01, 0.02, 0.005, 0.03, 0.015])
    equity = (1 + rets).cumprod() * 100.0
    m = compute_metrics(rets, equity)
    assert math.isfinite(m["Sortino"])
    assert not math.isnan(m["Sortino"])


def test_single_observation_defined_values_not_nan():
    # A single observation cannot yield a ddof=1 volatility, but the values
    # that ARE well-defined must not come back nan.
    rets = pd.Series([0.02])
    equity = pd.Series([102.0])
    m = compute_metrics(rets, equity)
    assert not math.isnan(m["CAGR"])
    assert not math.isnan(m["MaxDrawdown"])
    assert not math.isnan(m["WinRate(days)"])
    assert not math.isnan(m["FinalEquity (AUD)"])
    # AnnVol is deliberately defined (no dispersion in one point) rather than
    # a silent ddof=1 nan.
    assert not math.isnan(m["AnnVol"])


def test_benchmark_stats_empty():
    assert benchmark_stats(pd.Series(dtype=float), pd.Series(dtype=float)) == {}
