"""Latency benchmark — times the strategy's hot paths and reports **microseconds**.

Uses ``time.perf_counter_ns()`` (nanosecond clock) and reports min / median / mean
/ p95 in µs. Runs fully offline on synthetic data, so it measures *compute*
latency (signal + weight construction), not network/broker I/O.

    python -m trading_algo.bench
    python -m trading_algo.bench --region US --iters 2000
"""
from __future__ import annotations

import argparse
import statistics
import time

from . import data
from . import signals as sig
from . import strategy
from .config import DEFAULT_PARAMS
from .regions import get_region


def time_us(fn, iters: int) -> dict:
    """Return latency stats (microseconds) for `fn` over `iters` runs."""
    fn()  # warm up (imports, caches, JIT-y bits)
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)  # ns -> µs
    samples.sort()
    return {
        "min": samples[0],
        "median": statistics.median(samples),
        "mean": statistics.fmean(samples),
        "p95": samples[min(len(samples) - 1, int(0.95 * len(samples)))],
        "n": iters,
    }


def benchmarks(region_key: str):
    """(label, callable, iters) for each hot path."""
    region = get_region(region_key)
    prices, index_px = data.synthetic_region(region)      # offline, no network
    p = DEFAULT_PARAMS
    pv = p.with_overrides(use_value=True)
    asof = prices.index[-1]

    # Pre-extract single-row inputs for the truly µs-scale selection/sizing ops.
    scores = sig.momentum_score(prices, p).loc[asof]
    trend = sig.stock_trend_ok(prices, p).loc[asof]
    vols = sig.realised_vol(prices, p).loc[asof]
    raw_w = sig.select_portfolio(scores, trend, vols, True, p)

    return [
        # micro-ops: operate on one as-of cross-section → genuine microseconds
        ("select_portfolio (1 rebalance)",
         lambda: sig.select_portfolio(scores, trend, vols, True, p), 2000),
        ("vol_target (1 rebalance)",
         lambda: strategy.vol_target(raw_w, vols, p), 5000),
        # full-frame signal recompute (what compute_targets does each call)
        ("momentum_score (full history)", lambda: sig.momentum_score(prices, p), 500),
        ("value_score (full history)", lambda: sig.value_score(prices, pv), 500),
        ("realised_vol (full history)", lambda: sig.realised_vol(prices, p), 300),
        # the end-to-end per-rebalance decision
        ("compute_targets — momentum",
         lambda: strategy.compute_targets(prices, index_px, p, asof=asof), 200),
        ("compute_targets — momentum+value",
         lambda: strategy.compute_targets(prices, index_px, pv, asof=asof), 200),
    ]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Strategy latency benchmark (µs)")
    ap.add_argument("--region", default="US", choices=["ASX", "US", "FTSE"])
    ap.add_argument("--iters", type=int, default=None, help="override iteration count")
    args = ap.parse_args(argv)

    print(f"# Latency benchmark — {args.region} sleeve (synthetic data)\n")
    print("All times in **microseconds (µs)**. Compute only (no network/broker I/O).\n")
    print("| Operation | median µs | mean µs | p95 µs | min µs | runs |")
    print("|---|---:|---:|---:|---:|---:|")
    for label, fn, iters in benchmarks(args.region):
        s = time_us(fn, args.iters or iters)
        print(f"| {label} | {s['median']:,.1f} | {s['mean']:,.1f} | "
              f"{s['p95']:,.1f} | {s['min']:,.1f} | {s['n']} |")


if __name__ == "__main__":
    main()
