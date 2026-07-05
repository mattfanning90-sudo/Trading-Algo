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
import os
import sys

from .portfolio_backtest import run_portfolio_backtest

BASELINE = os.path.join(os.path.dirname(__file__), "_regression_baseline.json")

# Absolute tolerances per metric. Tight enough to catch a real behavioural
# regression (a lookahead leak moves CAGR/Sharpe well past these); loose enough
# to absorb cross-version floating-point noise.
TOL = {
    "CAGR": 0.02,
    "AnnVol": 0.02,
    "MaxDrawdown": 0.02,
    "Sharpe": 0.15,
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
            k: {"CAGR": s["metrics"]["CAGR"], "MaxDrawdown": s["metrics"]["MaxDrawdown"]}
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
            if abs(float(base) - float(cur)) > tol:
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
