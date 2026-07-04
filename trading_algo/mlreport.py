"""Predictive-model report — run the baseline ML sleeve and validate it honestly.

Builds the feature/label dataset, runs the PURGED walk-forward ridge predictor over a
small regularisation grid, and reports the out-of-sample stats + a Deflated Sharpe that
accounts for the grid (so we don't fool ourselves), plus the model's feature loadings.

    python -m trading_algo.mlreport --synthetic
    python -m trading_algo.mlreport --point-in-time   # de-biased US (needs constituents + TIINGO)

Honest expectation (see docs/research/PREDICTIVE_MODEL.md): on price-only features the
OOS Sharpe lands near the existing ~0.28 book — the model just recombines the same weak
factors. The deliverable is a *validated, leakage-controlled pipeline* that's ready the
moment real data (fundamentals/options/sentiment) is added as extra feature columns.
"""
from __future__ import annotations

import argparse

import numpy as np

from . import config as cfg
from . import constituents, data, mlpipeline as mlp, robust
from .features import FEATURES
from .regions import get_region


def build_report(synthetic: bool, point_in_time: bool = False) -> str:
    us = get_region("US")
    note = ""
    if synthetic:
        prices, idx = data.synthetic_region(us)
    else:
        membership = constituents.get_membership(us) if point_in_time else None
        tickers = membership.all_tickers if membership is not None else None
        start = "2007-01-01" if point_in_time else cfg.START
        prices, idx = data.load_region(us, start, None, tickers=tickers)
        if membership is not None:
            prices = data.apply_delisting_returns(prices, set(us.universe))
            note = f"De-biased: {len(membership)} PIT snapshots, {len(membership.all_tickers)} names."
        elif point_in_time:
            note = "⚠️ --point-in-time but no constituents cache — survivorship-biased universe."

    grid = [0.1, 1.0, 10.0, 100.0]
    runs = {a: mlp.run_ml_backtest(prices, idx, alpha=a) for a in grid}
    base = runs[1.0]
    r = base["returns"]

    L, w = [], None
    w = L.append
    w("# Predictive model — baseline (purged walk-forward ridge)\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — pipeline check only, numbers meaningless.\n")
    if note:
        w(f"> {note}\n")
    w(f"OOS periods: {base['n_periods']} monthly. Features: {', '.join(FEATURES)}.\n")

    w("| ridge α | CAGR | Vol | Sharpe | hit rate |")
    w("|---|---|---|---|---|")
    for a in grid:
        m = runs[a]["metrics"]
        w(f"| {a:g} | {m['CAGR']:.1%} | {m['Vol']:.1%} | {m['Sharpe']:.2f} | {m['hit_rate']:.0%} |")

    sharpes = [runs[a]["metrics"]["Sharpe"] for a in grid]
    dsr = robust.deflated_sharpe_ratio(r, sharpes, periods_per_year=12)
    psr = robust.probabilistic_sharpe_ratio(r)
    w(f"\n**Deflated Sharpe {dsr['dsr']:.1%}** across the {len(grid)}-point α grid "
      f"{'✅ survives selection' if (dsr['dsr'] or 0) >= 0.95 else '⚠️ not robust to the grid'}; "
      f"Probabilistic Sharpe (P[SR>0]) {psr:.1%}.\n")

    # descriptive feature loadings (fit on the whole dataset — interpretation only)
    df = mlp.build_dataset(prices, idx)
    if not df.empty:
        wts = mlp.cross_sectional_ridge(df[FEATURES].to_numpy(), df["fwd_ret"].to_numpy(), 1.0)
        order = np.argsort(-np.abs(wts))
        loads = ", ".join(f"{FEATURES[i]} {wts[i]:+.3f}" for i in order)
        w(f"**Feature loadings** (whole-sample ridge, descriptive): {loads}\n")

    w("## Honest read\n")
    s = base["metrics"]["Sharpe"]
    w(f"- Baseline OOS Sharpe **{s:.2f}** vs the existing book's ~0.28.")
    w("- On price-only features this is expected to be a wash — the model recombines the "
      "same factors we already tested. The win here is the *pipeline*: leakage-controlled "
      "(purged/embargoed walk-forward), deflated, and ready to ingest real data as new "
      "feature columns with zero downstream change (docs/research/PREDICTIVE_MODEL.md).")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Predictive-model baseline report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    args = ap.parse_args(argv)
    print(build_report(args.synthetic, args.point_in_time))


if __name__ == "__main__":
    main()
