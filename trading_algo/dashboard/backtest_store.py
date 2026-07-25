"""Cached backtest results for the dashboard's BACKTEST tab.

The dashboard itself never runs a backtest (it can take minutes and needs
market data); instead it reads a JSON cache written by:

    python -m trading_algo.dashboard.backtest_store                 # real data
    python -m trading_algo.dashboard.backtest_store --synthetic --out /tmp/x.json
    python -m trading_algo.dashboard.backtest_store --point-in-time # PIT universe
    python -m trading_algo.dashboard.backtest_store --sweep         # + robustness grid

When no cache exists the frontend renders the layout with clearly-labelled
illustrative curves and points at this command.

FX agent books read state/fx_backtest_{account}.json (kind "fx") — authored by
the forex tooling (run_backtest / walkforward) rather than this exporter.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from .. import config as cfg
from .. import paper_trade

EQUITY_CACHE = "backtest_equity.json"
MAX_POINTS = 500        # downsample curves so payloads stay small


def _cache_path() -> str:
    return os.path.join(paper_trade.STATE_DIR, EQUITY_CACHE)


def load_backtest(kind: str = "equity", account: str = "") -> dict:
    """The cached result for the BACKTEST tab, or {'available': False}."""
    if kind == "fx":
        from ..forex import fx_book
        path = os.path.join(fx_book.STATE_DIR, f"fx_backtest_{account}.json")
    else:
        path = _cache_path()
    try:
        with open(path) as f:
            data = json.load(f)
        data["available"] = True
        return data
    except (OSError, ValueError):
        return {"available": False, "kind": kind}


def _metric(metrics: dict, prefix: str):
    return next((v for k, v in metrics.items() if k.startswith(prefix)), None)


def _downsample(series) -> list[list]:
    n = len(series)
    step = max(1, n // MAX_POINTS)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [[series.index[i].strftime("%Y-%m-%d"), round(float(series.iloc[i]), 2)]
            for i in idx]


def _metrics_out(m: dict) -> dict:
    """Everything `metrics.compute_metrics` produces except FinalEquity — that
    one is money and each sleeve reports in its own local currency (invariant
    #6), so it cannot go in a shared, unlabelled metric row. The rest are
    unitless and comparable across sleeves. `win_rate` counts DAYS, not trades —
    a day the sleeve sits in cash scores as a non-win, so a low number is often
    the regime filter working, not a bad hit rate."""
    return {
        "cagr": _metric(m, "CAGR"),
        "ann_vol": _metric(m, "AnnVol"),
        "sharpe": _metric(m, "Sharpe"),
        "sortino": _metric(m, "Sortino"),
        "max_drawdown": _metric(m, "MaxDrawdown"),
        "calmar": _metric(m, "Calmar"),
        "win_rate": _metric(m, "WinRate"),
    }


def export_equity(synthetic: bool = False, point_in_time: bool = False,
                  sweep: bool = False, report_out: str | None = None,
                  out_path: str | None = None) -> str:
    """Run the portfolio backtest and write the dashboard cache. Returns path.
    `report_out` additionally writes the Markdown report from the SAME run
    (so CI gets both without paying for two backtests). `out_path` writes the
    cache somewhere other than the live one — use it with `synthetic=True` so a
    pipeline test never lands where the dashboard reads it (invariant #5)."""
    from ..portfolio_backtest import run_portfolio_backtest

    result = run_portfolio_backtest(synthetic=synthetic, point_in_time=point_in_time)

    if report_out:
        from ..report import portfolio_markdown
        with open(report_out, "w", encoding="utf-8") as f:
            f.write(portfolio_markdown(result, synthetic, point_in_time) + "\n")
        try:                               # equity curves for the CI artifact
            result["equity"].to_csv("equity_curve_portfolio.csv")
            result["benchmark"].to_csv("benchmark_curve.csv")
        except Exception:
            pass
    # Per-sleeve cost/turnover reality, alongside the return metrics. Turnover
    # and costs are fractions of NAV; `avg_turnover` is the same figure the
    # Markdown report's per-sleeve table labels "Turnover" (mean per rebalance),
    # and total_cost_fraction is the un-compounded sum of every rebalance's cost.
    sleeves = []
    for k, s in result["sleeves"].items():
        turn = s["turnover"]
        sleeves.append({
            "key": k, **_metrics_out(s["metrics"]),
            "avg_turnover": round(float(turn.mean()), 4) if len(turn) else None,
            "rebalances": int(len(turn)),
            "total_cost_fraction": round(float(s["total_cost_fraction"]), 6),
            "drawdown_halts": int(s["drawdown_halts"]),
            "drawdown_halt_days": int(s["drawdown_halt_days"]),
            "data_quality_excluded": list(s["data_quality_excluded"]),
        })

    bs = result.get("benchmark_stats") or {}
    out = {
        "kind": "equity",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": synthetic,
        "point_in_time": point_in_time,
        "start": cfg.START,
        "end": result["equity"].index[-1].strftime("%Y-%m-%d"),
        "initial_capital": cfg.INITIAL_CAPITAL,
        "curve": _downsample(result["equity"]),
        "benchmark": _downsample(result["benchmark"]),
        "metrics": _metrics_out(result["metrics"]),
        "benchmark_metrics": _metrics_out(result["benchmark_metrics"]),
        # "is the strategy adding anything over owning the index?" — portfolio
        # level only; run_backtest pairs no sleeve with its own index. Missing
        # (benchmark_stats returns {} on <2 overlapping days) stays null, never 0.
        "benchmark_stats": {
            "benchmark_cagr": bs.get("BenchmarkCAGR"),
            "active_return": bs.get("ActiveReturn"),
            "beta": bs.get("Beta"),
            "alpha": bs.get("Alpha"),
            "tracking_error": bs.get("TrackingError"),
            "info_ratio": bs.get("InfoRatio"),
        },
        "allocations": {k: round(float(v), 4)
                        for k, v in result["allocations"].items()},
        # Base currency, cumulative: the FX spread paid truing sleeves back to
        # their target allocation. The only portfolio-level cost line.
        "fx_rebalance_cost": round(float(result["fx_rebalance_cost"]), 2),
        "sleeves": sleeves,
    }

    if sweep:
        out["sweep"] = _run_sweep(synthetic)
        out["overfitting"] = _overfitting_gates(synthetic, point_in_time)

    path = out_path or _cache_path()
    with open(path, "w") as f:
        json.dump(out, f)
    return path


def sweep_verdict(values: list[list[float]], top_ns: list, lookbacks: list) -> dict:
    """Judge the grid with the project's own robustness rule (sweep.py) —
    one definition of 'robust', not two."""
    import pandas as pd

    from ..sweep import robustness_report
    # robustness_report expects sweep.py's orientation: rows=lookbacks, cols=top_n
    grid = pd.DataFrame(values, index=list(top_ns), columns=list(lookbacks)).T
    return robustness_report(grid)


def _run_sweep(synthetic: bool,
               top_ns: tuple = (8, 10, 12, 15),
               lookbacks: tuple = (126, 189, 252)) -> dict:
    """Portfolio-level Sharpe over the TOP_N × lookback grid (slow: one full
    backtest per cell)."""
    from ..portfolio_backtest import run_portfolio_backtest

    values = []
    for top_n in top_ns:
        row = []
        for lb in lookbacks:
            params = cfg.DEFAULT_PARAMS.with_overrides(top_n=top_n, lookback_days=lb)
            r = run_portfolio_backtest(synthetic=synthetic, params=params)
            row.append(round(float(_metric(r["metrics"], "Sharpe") or 0.0), 2))
        values.append(row)
    report = sweep_verdict(values, list(top_ns), [f"{lb}d" for lb in lookbacks])
    return {
        "top_ns": list(top_ns),
        "lookbacks": [f"{lb}d" for lb in lookbacks],
        "values": values,
        "verdict": report.get("verdict", ""),
        "report": report,
    }


def _overfitting_gates(synthetic: bool, point_in_time: bool,
                       top_ns: tuple = (8, 10, 12, 15),
                       lookbacks: tuple = (126, 189, 252)) -> dict:
    """Per-sleeve F2 overfitting gate: purged/embargoed walk-forward CV over the
    TOP_N × lookback grid, then Deflated Sharpe + PBO (n_trials = grid size). This
    is the honest test of whether a sleeve's edge survives once you correct for
    the number of configurations searched."""
    from .. import config as _cfg
    from .. import constituents, data, walkforward
    from ..regions import get_region

    gates: dict = {}
    for key in _cfg.ALLOCATIONS:
        region = get_region(key)
        membership = None
        if point_in_time:
            membership = (constituents.synthetic_membership(region) if synthetic
                          else constituents.get_membership(region))
        try:
            if synthetic:
                prices, index_px = data.synthetic_region(region)
            else:
                pit = membership.all_tickers if membership is not None else None
                prices, index_px = data.load_region(region, _cfg.START, tickers=pit)
            gate = walkforward.purged_cv_report(
                prices, index_px, region, list(top_ns), list(lookbacks),
                membership=membership)
        except Exception as exc:                     # never fail the cache write
            gate = {"verdict": f"error: {exc}", "n_configs": 0}
        # The failure paths (error here, "no result" from purged_cv_report) carry
        # only a verdict. Give the UI one shape to key off: absent == null, so it
        # can say "unavailable" instead of rendering a missing gate as a pass.
        gates[key] = {"dsr": None, "pbo": None, "passed": None, **gate}
    return gates


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write the dashboard backtest cache")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="also run the TOP_N × lookback robustness grid (slow)")
    ap.add_argument("--report", default=None, metavar="PATH",
                    help="also write the Markdown report from the same run")
    ap.add_argument("--out", default=None, metavar="PATH",
                    help="write the cache here instead of the live one "
                         "(use with --synthetic so a pipeline test never lands "
                         "where the dashboard reads it)")
    args = ap.parse_args(argv)
    path = export_equity(args.synthetic, args.point_in_time, args.sweep,
                         report_out=args.report, out_path=args.out)
    print(f"backtest cache → {path}")


if __name__ == "__main__":
    main()
