"""CI backtest regression gate (backlog F16 / foundation for safe refactors).

CI runs `pytest` but nothing checks that a refactor silently changed strategy
*behaviour* — an accidental lookahead, a dropped cost, a sizing drift. This gate
runs the deterministic synthetic portfolio backtest and compares a handful of
headline metrics to a committed baseline; drift beyond tolerance fails the build.

Synthetic data is seeded (invariant #5: pipeline test only, never a performance
claim) so the numbers are reproducible across machines. The baseline lives next
to this module and is updated deliberately with `--update` (a reviewable diff).

    python -m trading_algo.ci_regression --check     # compare, exit 1 on drift
    python -m trading_algo.ci_regression --update     # rewrite the baseline
    python -m trading_algo.ci_regression --show       # print current metrics
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

from .portfolio_backtest import run_portfolio_backtest

BASELINE = os.path.join(os.path.dirname(__file__), "_regression_baseline.json")

# Absolute tolerances per metric: just above the measured noise. Task 20 ran
# this backtest on arm64 and x86_64, Python 3.11-3.14, numpy 2.3.5-2.5.4 and
# pandas 3.0.5-3.0.6: the unrounded metrics were bit-identical on arm64 and
# within 2.7e-15 on x86_64, and every rounded value was identical. The gate
# reads them rounded (4 dp, Sharpe 2 dp), so the most that noise can do is
# flip the last digit at a rounding boundary: 1.5 steps allows one step and
# refuses two. The old 0.02 / 0.15 could not see a convention change: Task 9's
# next-close fills moved synthetic portfolio CAGR 0.0011 and Sharpe 0.02, and
# the gate went red only on a cost line.
TOL = {
    "CAGR": 1.5e-4,
    "AnnVol": 1.5e-4,
    "MaxDrawdown": 1.5e-4,
    "Sharpe": 0.015,
    # Cumulative cost drag, as a fraction of NAV summed over the run. TIGHT on
    # purpose: deleting every transaction cost moved the synthetic sleeves' CAGR
    # by only 0.0022-0.0114, inside the 0.02 CAGR tolerance before Task 20, so
    # CAGR alone could not see a dropped cost. This line names the cost
    # directly, and enforces invariant #2 in CI whatever the return lines do.
    "total_cost_fraction": 1e-4,
}


def synthetic_metrics() -> dict:
    """Deterministic headline metrics from the synthetic portfolio backtest."""
    res = run_portfolio_backtest(synthetic=True)
    pm = res["metrics"]
    sharpe_key = next(k for k in pm if k.startswith("Sharpe"))
    out = {
        "portfolio": {
            "CAGR": pm["CAGR"],
            "AnnVol": pm["AnnVol"],
            "Sharpe": pm[sharpe_key],
            "MaxDrawdown": pm["MaxDrawdown"],
        },
        "sleeves": {
            k: {"CAGR": s["metrics"]["CAGR"],
                "MaxDrawdown": s["metrics"]["MaxDrawdown"],
                # run_backtest already returns this; rounded to keep the
                # committed baseline readable and diffable.
                "total_cost_fraction": round(float(s["total_cost_fraction"]), 6)}
            for k, s in res["sleeves"].items()
        },
    }
    return out


def _tol_for(metric: str) -> float:
    return TOL.get(metric, 0.02)


def compare(baseline: dict, current: dict) -> list[str]:
    """Return per-metric drift messages; empty means within tolerance."""
    drift: list[str] = []

    def walk(path, base, cur):
        if isinstance(base, dict):
            for k in base:
                if k not in cur:
                    drift.append(f"{path}{k}: missing in current run")
                else:
                    walk(f"{path}{k}.", base[k], cur[k])
            for k in cur:
                if k not in base:
                    drift.append(f"{path}{k}: new metric not in baseline")
        else:
            metric = path.rstrip(".").split(".")[-1]
            tol = _tol_for(metric)
            # `abs(b - c) > tol` is False when either side is NaN, so a metric
            # that turns into NaN would pass silently. No metric here is
            # legitimately NaN or infinite, so either side non-finite is drift.
            if not (math.isfinite(float(base)) and math.isfinite(float(cur))):
                drift.append(f"{path.rstrip('.')}: {base} -> {cur} (non-finite)")
            elif abs(float(base) - float(cur)) > tol:
                drift.append(f"{path.rstrip('.')}: {base} -> {cur} "
                             f"(|Δ| > tol {tol})")

    walk("", baseline, current)
    return drift


def load_baseline() -> dict | None:
    if not os.path.exists(BASELINE):
        return None
    with open(BASELINE) as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CI backtest regression gate")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="compare to baseline (default)")
    g.add_argument("--update", action="store_true", help="rewrite the baseline")
    g.add_argument("--show", action="store_true", help="print current metrics")
    args = ap.parse_args(argv)

    current = synthetic_metrics()

    if args.show:
        print(json.dumps(current, indent=2))
        return 0

    if args.update:
        with open(BASELINE, "w") as f:
            json.dump(current, f, indent=2)
        print(f"Baseline updated -> {BASELINE}")
        return 0

    # default: --check
    baseline = load_baseline()
    if baseline is None:
        print("FAIL: no committed baseline. Run --update first.", file=sys.stderr)
        return 1
    drift = compare(baseline, current)
    if drift:
        print("FAIL: synthetic backtest drifted from baseline "
              "(a behavioural regression, or update the baseline deliberately):\n",
              file=sys.stderr)
        for d in drift:
            print(f"  - {d}", file=sys.stderr)
        return 1
    print("OK: synthetic backtest matches baseline within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
