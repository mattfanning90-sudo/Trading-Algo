"""Cached backtest results for the dashboard's BACKTEST tab.

The dashboard itself never runs a backtest (it can take minutes and needs
market data); instead it reads a JSON cache written by:

    python -m trading_algo.dashboard.backtest_store                 # real data
    python -m trading_algo.dashboard.backtest_store --synthetic     # pipeline test
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
    return {
        "cagr": _metric(m, "CAGR"),
        "ann_vol": _metric(m, "AnnVol"),
        "sharpe": _metric(m, "Sharpe"),
        "sortino": _metric(m, "Sortino"),
        "max_drawdown": _metric(m, "MaxDrawdown"),
        "calmar": _metric(m, "Calmar"),
    }


def export_equity(synthetic: bool = False, point_in_time: bool = False,
                  sweep: bool = False) -> str:
    """Run the portfolio backtest and write the dashboard cache. Returns path."""
    from ..portfolio_backtest import run_portfolio_backtest

    result = run_portfolio_backtest(synthetic=synthetic, point_in_time=point_in_time)
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
        "sleeves": [
            {"key": k, **_metrics_out(s["metrics"])}
            for k, s in result["sleeves"].items()
        ],
    }

    if sweep:
        out["sweep"] = _run_sweep(synthetic)

    path = _cache_path()
    with open(path, "w") as f:
        json.dump(out, f)
    return path


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
    flat = [v for row in values for v in row]
    mean = sum(flat) / len(flat)
    spread = (max(flat) - min(flat)) / abs(mean) if mean else 0.0
    return {
        "top_ns": list(top_ns),
        "lookbacks": [f"{lb}d" for lb in lookbacks],
        "values": values,
        "verdict": "PLATEAU — ROBUST" if spread < 0.35 else "PEAKED — FRAGILE",
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write the dashboard backtest cache")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="also run the TOP_N × lookback robustness grid (slow)")
    args = ap.parse_args(argv)
    path = export_equity(args.synthetic, args.point_in_time, args.sweep)
    print(f"backtest cache → {path}")


if __name__ == "__main__":
    main()
