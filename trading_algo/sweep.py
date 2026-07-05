"""Walk-forward parameter robustness sweep.

The point is NOT to find the single best (TOP_N, lookback) — that's how you
curve-fit. It's to check the edge is *stable*: a broad plateau of decent Sharpe
across neighbouring parameters means the strategy is robust; a lone sharp peak
surrounded by poor cells means you've fitted noise. This sweeps a grid and
reports a flatness verdict.

    python -m trading_algo.sweep --region US --synthetic
    python -m trading_algo.sweep                       # all sleeves, real data
    python -m trading_algo.sweep --region ASX --point-in-time
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from . import config as cfg
from . import constituents, data, walkforward
from .backtest import run_backtest
from .regions import Region, all_region_keys, get_region

DEFAULT_TOP_NS = [6, 8, 10, 12, 15]
DEFAULT_LOOKBACKS = [126, 189, 252, 315]   # ~6, 9, 12, 15 months


def _metric_value(metrics: dict, metric: str) -> float:
    if metric.lower() == "sharpe":
        key = next((k for k in metrics if k.startswith("Sharpe")), None)
        return float(metrics.get(key, np.nan)) if key else np.nan
    return float(metrics.get(metric, np.nan))


def sweep_region(region: Region, prices: pd.DataFrame, index_px: pd.Series,
                 top_ns=DEFAULT_TOP_NS, lookbacks=DEFAULT_LOOKBACKS,
                 metric: str = "sharpe", membership=None) -> pd.DataFrame:
    """Grid of `metric` over (lookback rows x top_n cols)."""
    grid = pd.DataFrame(index=[f"{lb}d" for lb in lookbacks], columns=top_ns, dtype=float)
    for lb in lookbacks:
        for tn in top_ns:
            variant = replace(region, params=region.params.with_overrides(
                top_n=tn, lookback_days=lb))
            try:
                res = run_backtest(prices, index_px, variant, membership=membership)
                grid.loc[f"{lb}d", tn] = _metric_value(res["metrics"], metric)
            except Exception:
                grid.loc[f"{lb}d", tn] = np.nan
    return grid


def robustness_report(grid: pd.DataFrame, higher_is_better: bool = True) -> dict:
    vals = grid.to_numpy(dtype=float)
    flat = vals[~np.isnan(vals)]
    if flat.size == 0:
        return {"verdict": "no result"}

    best = np.nanmax(vals) if higher_is_better else np.nanmin(vals)
    mean, std = float(np.nanmean(vals)), float(np.nanstd(vals))
    cv = std / (abs(mean) + 1e-9)
    pct_positive = float((flat > 0).mean())

    # peak isolation: best cell vs its orthogonal neighbours
    bi = np.unravel_index(np.nanargmax(vals) if higher_is_better
                          else np.nanargmin(vals), vals.shape)
    nb = []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r, c = bi[0] + dr, bi[1] + dc
        if 0 <= r < vals.shape[0] and 0 <= c < vals.shape[1] and not np.isnan(vals[r, c]):
            nb.append(vals[r, c])
    neighbour_mean = float(np.mean(nb)) if nb else float(best)
    peak_isolation = float(best - neighbour_mean)

    if cv < 0.5 and pct_positive > 0.6:
        verdict = "FLAT / ROBUST — edge persists broadly across the grid"
    elif peak_isolation > 1.5 * std and std > 0:
        verdict = "PEAKY — best params look isolated; risk of curve-fitting"
    else:
        verdict = "MODERATE — partial stability; widen the sweep before trusting"

    best_lb = grid.index[bi[0]]
    best_tn = grid.columns[bi[1]]
    return {
        "best": round(float(best), 3),
        "best_params": {"lookback": best_lb, "top_n": int(best_tn)},
        "mean": round(mean, 3), "std": round(std, 3), "cv": round(cv, 3),
        "pct_positive": round(pct_positive, 3),
        "peak_isolation": round(peak_isolation, 3),
        "verdict": verdict,
    }


def _print_grid(region: Region, grid: pd.DataFrame, metric: str) -> None:
    print(f"\n  {region.name} — {metric} by (lookback x TOP_N)")
    header = "  lookback " + "".join(f"{tn:>8}" for tn in grid.columns)
    print(header)
    for idx, row in grid.iterrows():
        cells = "".join(f"{v:>8.2f}" if pd.notna(v) else f"{'·':>8}" for v in row)
        print(f"  {idx:<8} {cells}")


def _print_purged_cv(region: Region, prices: pd.DataFrame, index_px: pd.Series,
                     membership=None) -> None:
    """F8 + F2: purged/embargoed walk-forward CV over the grid, then the
    Deflated-Sharpe + PBO overfitting gate (n_trials == grid size)."""
    rep = walkforward.purged_cv_report(
        prices, index_px, region, DEFAULT_TOP_NS, DEFAULT_LOOKBACKS,
        membership=membership)
    if rep.get("verdict") == "no result":
        print("\n  Purged CV: no result")
        return
    print(f"\n  Purged walk-forward CV ({rep['n_folds']} folds, embargo "
          f"{rep['embargo']}d, {rep['n_obs']} OOS obs over {rep['grid_size']} configs):")
    pbo_str = "n/a" if rep["pbo"] is None else f"{rep['pbo']:.2f}"
    print(f"    DSR {rep['dsr']:.2f}  |  PBO {pbo_str}  |  "
          f"n_trials {rep['n_trials']}  ->  {rep['verdict']}")


def run_sweep(region_key: str | None, synthetic: bool, point_in_time: bool,
              metric: str = "sharpe", purged_cv: bool = False) -> None:
    keys = [region_key] if region_key else list(cfg.ALLOCATIONS)
    higher_is_better = metric.lower() not in ("maxdrawdown", "annvol")

    for key in keys:
        region = get_region(key)
        membership = None
        if point_in_time:
            membership = (constituents.synthetic_membership(region)
                          if synthetic else constituents.get_membership(region))
        pit_tickers = membership.all_tickers if membership is not None else None

        if synthetic:
            prices, index_px = data.synthetic_region(region)
        else:
            prices, index_px = data.load_region(region, cfg.START, tickers=pit_tickers)

        grid = sweep_region(region, prices, index_px, metric=metric, membership=membership)
        _print_grid(region, grid, metric)
        rep = robustness_report(grid, higher_is_better)
        print(f"\n  Verdict: {rep['verdict']}")
        print(f"    best {rep['best']} @ {rep['best_params']}  |  mean {rep['mean']} "
              f"std {rep['std']} cv {rep['cv']}  |  %positive {rep['pct_positive']:.0%}")
        if purged_cv:
            _print_purged_cv(region, prices, index_px, membership)
        grid.to_csv(f"sweep_{key}_{metric}.csv")
        print(f"    grid -> sweep_{key}_{metric}.csv")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Parameter robustness sweep")
    # Explicit --region can target any registered sleeve (incl. unfunded TSX);
    # the no-region default still sweeps only the funded ALLOCATIONS sleeves.
    ap.add_argument("--region", choices=all_region_keys())
    ap.add_argument("--metric", default="sharpe",
                    choices=["sharpe", "CAGR", "MaxDrawdown", "AnnVol", "Calmar"])
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--purged-cv", action="store_true",
                    help="also run purged/embargoed walk-forward CV + the "
                         "Deflated-Sharpe/PBO overfitting gate (F8/F2)")
    args = ap.parse_args(argv)
    if args.synthetic:
        print("⚠ SYNTHETIC DATA — surface shape only, numbers are meaningless")
    run_sweep(args.region, args.synthetic, args.point_in_time, args.metric,
              purged_cv=args.purged_cv)


if __name__ == "__main__":
    main()
