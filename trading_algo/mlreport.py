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

    w("The **market-neutral long/short** Sharpe is the honest skill metric (beta stripped); "
      "long-only is shown only as a beta/construction reference.\n")
    w("| ridge α | L/S Sharpe (SKILL) | L/S CAGR@10% | long-only Sharpe (beta) | hit rate |")
    w("|---|---|---|---|---|")
    for a in grid:
        m, ml = runs[a]["metrics"], runs[a]["ls_metrics"]
        w(f"| {a:g} | **{ml['Sharpe']:.2f}** | {ml['CAGR']:.1%} | {m['Sharpe']:.2f} | {ml['hit_rate']:.0%} |")

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
    s_ls = base["ls_metrics"]["Sharpe"]
    s_lo = base["metrics"]["Sharpe"]
    w(f"- **Market-neutral skill Sharpe {s_ls:.2f}** (OOS IC {base['ic']:.3f}) — the honest number. "
      f"Long-only was {s_lo:.2f}, but that is beta/construction, not skill.")
    if not clean:
        w("- ⚠️ Null IC non-zero → leakage. Fix the feature/label timing before trusting anything.")
    elif abs(s_ls) < 0.3:
        w("- On price+fundamentals the market-neutral edge is ≈ 0 — the model can't rank next "
          "month's winners (IC ~ noise). Consistent with everything: no cross-sectional alpha "
          "in this data. The flattering long-only Sharpe was pure beta on the small-cap/delisted "
          "universe with no vol-targeting — now correctly stripped out.")
    w("- The durable win is the *pipeline*: market-neutral, leakage-controlled (purged walk-"
      "forward + shuffle null), deflated, ready to ingest new data as columns "
      "(docs/research/PREDICTIVE_MODEL.md).")
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
