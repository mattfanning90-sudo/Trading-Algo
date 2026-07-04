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
from . import constituents, data, datasources, mlpipeline as mlp, robust
from .regions import get_region


def build_report(synthetic: bool, point_in_time: bool = False,
                 with_altdata: bool = False) -> str:
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

    # alt-data feature panel (fundamentals + IV + sentiment), as-of merged (no lookahead).
    # Real EDGAR fundamentals on real runs; IV/sentiment are synthetic adapters until a
    # paid feed is wired (see datasources). --synthetic uses synthetic for all three.
    extra = None
    alt_note = ""
    if with_altdata:
        start_alt = "2007-01-01" if point_in_time else cfg.START
        extra = datasources.build_extra_panel(datasources.ALL_SOURCES, prices, start_alt,
                                              synthetic=synthetic)
        got = list(extra.columns) if extra is not None and not extra.empty else []
        alt_note = (f"Alt-data columns merged: {', '.join(got)}." if got
                    else "⚠️ alt-data requested but no source returned data (feeds not wired).")

    grid = [0.1, 1.0, 10.0, 100.0]
    runs = {a: mlp.run_ml_backtest(prices, idx, alpha=a, extra=extra) for a in grid}
    base = runs[1.0]
    r = base["returns"]

    L, w = [], None
    w = L.append
    w("# Predictive model — baseline (purged walk-forward ridge)\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — pipeline check only, numbers meaningless.\n")
    if note:
        w(f"> {note}\n")
    if alt_note:
        w(f"> {alt_note}\n")
    ds = mlp.build_dataset(prices, idx, extra=extra)
    feats = mlp.feature_cols(ds)
    w(f"OOS periods: {base['n_periods']} monthly. {len(feats)} features: {', '.join(feats)}.\n")

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

    # Leakage probe: retrain on SHUFFLED labels. A leak-free pipeline → null IC ≈ 0.
    null = mlp.run_ml_backtest(prices, idx, alpha=1.0, extra=extra, shuffle_seed=0)
    clean = abs(null.get("ic", 0.0)) < 0.02
    w(f"**Leakage probe** — out-of-sample IC real **{base['ic']:.3f}** vs label-shuffled "
      f"null **{null.get('ic', float('nan')):.3f}**: "
      f"{'✅ clean (null ≈ 0)' if clean else '⚠️ NULL IC NON-ZERO → the pipeline is peeking; the headline Sharpe is not real'}\n")

    # descriptive feature loadings (fit on the whole dataset — interpretation only)
    if not ds.empty:
        wts = mlp.cross_sectional_ridge(ds[feats].to_numpy(), ds[mlp.LABEL].to_numpy(), 1.0)
        order = np.argsort(-np.abs(wts))
        loads = ", ".join(f"{feats[i]} {wts[i]:+.3f}" for i in order)
        w(f"**Feature loadings** (whole-sample ridge, descriptive): {loads}\n")

    w("## Honest read\n")
    s = base["metrics"]["Sharpe"]
    w(f"- Baseline OOS Sharpe **{s:.2f}** (OOS IC {base['ic']:.3f}) vs the existing book's ~0.28.")
    if s > 0.5 and clean:
        w("- The Sharpe is high but the label-shuffle null is clean, so it isn't a hard "
          "leak. Treat with suspicion anyway: a long-only, always-invested top-N by a "
          "flexible model on the delisted-inclusive universe can be *flattered* by the "
          "deterministic −30% delisting mark (avoiding identifiable losers) and by having "
          "no regime/vol de-risking — a construction difference from the book, not proven "
          "alpha. Reconcile costs + construction before trusting it.")
    elif not clean:
        w("- ⚠️ The null IC is non-zero → there is leakage in the pipeline. The headline "
          "number is an artefact; fix the leak (feature/label timing, embargo) before any "
          "other conclusion.")
    w("- The durable win here is the *pipeline*: leakage-controlled (purged/embargoed "
      "walk-forward + shuffle null), deflated, and ready to ingest real data as new feature "
      "columns with zero downstream change (docs/research/PREDICTIVE_MODEL.md).")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Predictive-model baseline report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--with-altdata", action="store_true",
                    help="merge fundamentals (EDGAR) + options-IV + sentiment feature panels")
    args = ap.parse_args(argv)
    print(build_report(args.synthetic, args.point_in_time, args.with_altdata))


if __name__ == "__main__":
    main()
