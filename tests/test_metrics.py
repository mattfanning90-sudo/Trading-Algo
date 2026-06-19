"""Benchmark statistics."""
import numpy as np
import pandas as pd

from trading_algo.metrics import benchmark_stats


def test_benchmark_stats_against_self():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 400))
    s = benchmark_stats(r, r)
    assert abs(s["Beta"] - 1.0) < 0.01          # a series has beta 1 to itself
    assert abs(s["ActiveReturn"]) < 1e-6        # …and no active return
    for k in ("BenchmarkCAGR", "Alpha", "TrackingError", "InfoRatio"):
        assert k in s


def test_benchmark_stats_empty():
    assert benchmark_stats(pd.Series(dtype=float), pd.Series(dtype=float)) == {}
