"""Predictive-model report — run the baseline ML sleeve and validate it honestly.

Builds the feature/label dataset, runs the PURGED walk-forward ridge predictor over a
small regularisation grid, and reports the out-of-sample stats + a Deflated Sharpe that
accounts for the grid — AND, the part that actually answers "does alt-data add anything",
a MARGINAL-EDGE section that isolates alt-over-price:

  • per-source price-residualised partial IC (fundamentals vs sentiment, never lumped);
  • a nested price-only-vs-price+alt walk-forward DELTA with a stationary-block-bootstrap
    CI, DSR-deflating the DIFFERENCE series (the increment, not the alt book);
  • a shuffle-null on the incremental measure and a SYNTHETIC negative control (alt-data is
    independent of synthetic prices, so the increment MUST straddle 0 — a nonzero value is a
    leakage bug, caught before any real number is trusted).

    python -m trading_algo.mlreport --synthetic                 # plumbing + negative control
    python -m trading_algo.mlreport --point-in-time --with-altdata  # de-biased US, real EDGAR+GDELT

PROVENANCE: a real pass is read ONLY from the CI real-data 'ml' run (EDGAR + GDELT).
Synthetic and local runs are plumbing / negative-control only and are never a performance
claim. Options-IV features are DEFERRED (no real feed) and excluded from every claim.
See docs/research/PREDICTIVE_MODEL.md.
"""
from __future__ import annotations

import argparse

import numpy as np

from . import config as cfg
from . import constituents, data, datasources, mlpipeline as mlp, robust
from .regions import get_region

# PRE-REGISTERED evaluation config — fixed from the literature, NOT swept on this sample
# (the only tuned hyperparameter is the ridge α grid; everything below is pinned so it
# adds no unpaid multiplicity to the Deflated Sharpe).
GRID = [0.1, 1.0, 10.0, 100.0]              # ridge α — the one swept knob
FUND_COLS = ("roe", "net_margin", "asset_growth", "sue")   # SUE is the horizon-matched new signal
SENT_COLS = ("sentiment_shock", "buzz_shock")              # tone/attention changes


def _num(x):
    """nan/inf → None for clean JSON; else a plain float (for the forward monitor log)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _shuffle_labels(df, seed: int = 0):
    """Permute the label within each date — the leakage null for the incremental measure."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    out[mlp.LABEL] = out.groupby(level="date")[mlp.LABEL].transform(
        lambda s: rng.permutation(s.to_numpy()))
    return out


def _oos_dates(df, n_folds: int = 5, embargo: int = 1):
    dates = df.index.get_level_values("date").unique()
    splits = mlp.purged_walk_forward(dates, n_folds=n_folds, embargo=embargo)
    if not splits:
        return dates
    seen = []
    for _, te in splits:
        seen.extend(list(te))
    return sorted(set(seen))


def build_report(synthetic: bool, point_in_time: bool = False,
                 with_altdata: bool = False, sent_horizon: int = 10,
                 metrics_sink: list | None = None) -> str:
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
    # Real EDGAR fundamentals + GDELT sentiment on real runs; IV is a synthetic adapter and
    # DEFERRED. --synthetic uses synthetic for all three (a negative control, not a result).
    extra = None
    alt_note = ""
    if with_altdata:
        start_alt = "2007-01-01" if point_in_time else cfg.START
        extra = datasources.build_extra_panel(datasources.ALL_SOURCES, prices, start_alt,
                                              synthetic=synthetic)
        got = list(extra.columns) if extra is not None and not extra.empty else []
        alt_note = (f"Alt-data columns merged: {', '.join(got)}." if got
                    else "⚠️ alt-data requested but no source returned data (feeds not wired).")

    runs = {a: mlp.run_ml_backtest(prices, idx, alpha=a, extra=extra) for a in GRID}
    base = runs[1.0]
    r = base["returns"]

    L = []
    w = L.append
    w("# Predictive model — baseline (purged walk-forward ridge)\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — pipeline check + negative control only, numbers meaningless.\n")
    if note:
        w(f"> {note}\n")
    if alt_note:
        w(f"> {alt_note}\n")
    df = mlp.build_dataset(prices, idx, extra=extra)
    feats = mlp.feature_cols(df)
    w(f"OOS periods: {base['n_periods']} monthly. {len(feats)} model features: {', '.join(feats)}.\n")

    w("The **market-neutral long/short** Sharpe is the honest skill metric (beta stripped); "
      "long-only is shown only as a beta/construction reference.\n")
    w("| ridge α | L/S Sharpe (SKILL) | L/S CAGR@10% | long-only Sharpe (beta) | hit rate |")
    w("|---|---|---|---|---|")
    for a in GRID:
        m, ml = runs[a]["metrics"], runs[a]["ls_metrics"]
        w(f"| {a:g} | **{ml['Sharpe']:.2f}** | {ml['CAGR']:.1%} | {m['Sharpe']:.2f} | {ml['hit_rate']:.0%} |")

    sharpes = [runs[a]["metrics"]["Sharpe"] for a in GRID]
    dsr = robust.deflated_sharpe_ratio(r, sharpes, periods_per_year=12)
    psr = robust.probabilistic_sharpe_ratio(r)
    w(f"\n**Deflated Sharpe {dsr['dsr']:.1%}** across the {len(GRID)}-point α grid "
      f"{'✅ survives selection' if (dsr['dsr'] or 0) >= 0.95 else '⚠️ not robust to the grid'}; "
      f"Probabilistic Sharpe (P[SR>0]) {psr:.1%}.\n")

    # ---------------------------------------------------------------------
    # MARGINAL EDGE — does alt-data add anything BEYOND the price features?
    # ---------------------------------------------------------------------
    if with_altdata and extra is not None and not extra.empty:
        w("## Marginal edge — alt-data over price (the honest test)\n")
        alt_all = list(extra.columns)
        scored = [c for c in alt_all if c not in datasources.MASK_COLS
                  and c not in datasources.DEFERRED_COLS]
        fundamentals = [c for c in FUND_COLS if c in scored]
        sentiment = [c for c in SENT_COLS if c in scored]
        deferred = [c for c in alt_all if c in datasources.DEFERRED_COLS]
        price_cols = [c for c in mlp.feat.FEATURES if c in df.columns]
        oos = _oos_dates(df)

        # per-source price-residualised partial IC (fundamentals full-universe @21d)
        fund_ic = mlp.partial_incremental_ic(df, price_cols, fundamentals, oos_dates=oos)
        # sentiment: short horizon + covered sub-universe (survivor-conditioned → corroboration)
        sent_df = mlp.build_dataset(prices, idx, horizon=sent_horizon, extra=extra)
        sub = sent_df["has_sentiment"] == 1 if "has_sentiment" in sent_df else None
        sent_ic = mlp.partial_incremental_ic(sent_df, price_cols, sentiment,
                                             oos_dates=_oos_dates(sent_df), sub_universe=sub)

        def _fmt(d):
            return ", ".join(f"{k} {v:+.3f}" for k, v in d["per_col"].items()) or "—"
        w("| source | universe / horizon | block incremental IC | per-column |")
        w("|---|---|---|---|")
        w(f"| fundamentals (incl. SUE) | full / 21d | **{fund_ic['incremental_ic']:+.3f}** "
          f"({fund_ic['n_dates']} dates) | {_fmt(fund_ic)} |")
        w(f"| sentiment (shocks) | covered / {sent_horizon}d | **{sent_ic['incremental_ic']:+.3f}** "
          f"({sent_ic['n_dates']} dates) | {_fmt(sent_ic)} |")
        if deferred:
            w(f"| options-IV | — | _deferred_ | excluded (no real feed): {', '.join(deferred)} |")
        w("")

        # nested delta: price-only vs price+alt, DSR on the DIFFERENCE (deflate the increment)
        base_only = {a: mlp.run_ml_backtest(prices, idx, alpha=a, extra=None) for a in GRID}
        deltas = {a: mlp.incremental_delta(base_only[a], runs[a], n_paths=1000, seed=0) for a in GRID}
        d1 = mlp.incremental_delta(base_only[1.0], runs[1.0], n_paths=2000, seed=0)
        trial_irs = [deltas[a]["delta_ir"] for a in GRID if not np.isnan(deltas[a]["delta_ir"])]
        diff = d1.get("diff")
        dsr_d = (robust.deflated_sharpe_ratio(diff, trial_irs, periods_per_year=12)
                 if diff is not None and len(diff) > 2 and trial_irs else {"dsr": float("nan")})
        straddle0 = (not np.isnan(d1["ci_low"])) and (d1["ci_low"] <= 0 <= d1["ci_high"])
        w(f"**Nested delta (price+alt − price-only, α=1):** Δ IC **{d1['delta_ic']:+.3f}**, "
          f"increment info-ratio **{d1['delta_ir']:+.2f}** "
          f"(90% bootstrap CI [{d1['ci_low']:+.2f}, {d1['ci_high']:+.2f}], n={d1['n']}). "
          f"Deflated Sharpe of the DIFFERENCE {dsr_d['dsr']:.1%} across the α grid.\n")

        # shuffle-null on the incremental measure — must collapse to ~0
        null_ic = mlp.partial_incremental_ic(_shuffle_labels(df, 0), price_cols,
                                             fundamentals + sentiment, oos_dates=oos)
        clean_inc = abs(null_ic["incremental_ic"]) < 0.02
        w(f"**Incremental shuffle-null:** block IC on label-shuffled data "
          f"**{null_ic['incremental_ic']:+.3f}** "
          f"{'✅ collapses (no residual timing leak)' if clean_inc else '⚠️ non-zero → leakage in the alt path'}\n")

        # honest pass / fail read on the increment
        edge = ((not np.isnan(d1["ci_low"])) and d1["ci_low"] > 0
                and (dsr_d.get("dsr") or 0) >= 0.95 and d1["delta_ic"] >= 0.005 and clean_inc)

        # forward-monitor record: one machine-readable row per run, so the honest test can
        # be logged over time (does any source EARN weight yet?). See altdata-monitor.yml.
        if metrics_sink is not None:
            metrics_sink.append({
                "universe": "synthetic" if synthetic else ("PIT-US" if point_in_time else "US"),
                "n_oos": int(base["n_periods"]),
                "fund_inc_ic": _num(fund_ic["incremental_ic"]),
                "sue_ic": _num(fund_ic["per_col"].get("sue")),
                "sent_inc_ic": _num(sent_ic["incremental_ic"]),
                "sent_dates": int(sent_ic["n_dates"]),
                "delta_ic": _num(d1["delta_ic"]),
                "delta_ir": _num(d1["delta_ir"]),
                "ci_low": _num(d1["ci_low"]),
                "ci_high": _num(d1["ci_high"]),
                "dsr_diff": _num(dsr_d.get("dsr")),
                "passes": bool(edge),
            })
        if synthetic:
            control_ok = straddle0 and abs(d1["delta_ic"]) < 0.01 and clean_inc
            if control_ok:
                w("**Negative control (synthetic): ✅ increment straddles 0** — the new "
                  "feature path (SUE/shock/decay/mask) introduces no lookahead. Real numbers "
                  "must come from the CI 'ml' run; nothing here is a performance claim.\n")
            else:
                w("**🛑 LEAKAGE BUG — synthetic increment is NON-ZERO.** Synthetic alt-data is "
                  "independent of synthetic prices, so any edge here is a lookahead artifact "
                  "(future-dated known_date, shift-0 shock baseline, decay reading a future "
                  "date). ALL real-data numbers from this path are VOID until fixed.\n")
        elif edge:
            w("**✅ Alt-data adds a real increment** on this run: the price-residualised edge "
              "is positive with a bootstrap-CI lower bound > 0 AND the difference survives "
              "Deflated-Sharpe deflation. Weight it into the book via the ERC combiner, sized "
              "by its risk-adjusted marginal contribution.\n")
        else:
            w("**Not an edge (yet):** the increment's CI lower bound is not > 0, or the "
              "difference does not clear DSR ≥ 95%. A positive point estimate without both is "
              "reported as noise — no weight is assigned until a source EARNS it.\n")

    # Leakage probe (combined model): retrain on SHUFFLED labels. Leak-free → null IC ≈ 0.
    null = mlp.run_ml_backtest(prices, idx, alpha=1.0, extra=extra, shuffle_seed=0)
    clean = abs(null.get("ic", 0.0)) < 0.02
    w(f"**Leakage probe (combined)** — OOS IC real **{base['ic']:.3f}** vs label-shuffled "
      f"null **{null.get('ic', float('nan')):.3f}**: "
      f"{'✅ clean (null ≈ 0)' if clean else '⚠️ NULL IC NON-ZERO → the pipeline is peeking'}\n")

    # descriptive feature loadings (fit on the whole dataset — interpretation only)
    if not df.empty:
        wts = mlp.cross_sectional_ridge(df[feats].to_numpy(), df[mlp.LABEL].to_numpy(), 1.0)
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
    w("- **Source weighting is EARNED, not assumed.** A source is weighted into the book only "
      "after its price-residualised increment clears the CI-lower-bound-> 0 and DSR >= 95% bar "
      "on the CI real-data run; until then it carries zero weight (the ridge already shrinks "
      "sparse alt columns toward zero).")
    w("- The durable win is the *pipeline*: market-neutral, leakage-controlled (purged walk-"
      "forward + shuffle null + synthetic negative control), deflated on the INCREMENT, ready "
      "to weight new data the day it earns it (docs/research/PREDICTIVE_MODEL.md).")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Predictive-model baseline report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--with-altdata", action="store_true",
                    help="merge fundamentals (EDGAR) + options-IV + sentiment feature panels")
    ap.add_argument("--sent-horizon", type=int, default=10,
                    help="forward-return horizon (days) for the short-horizon sentiment eval")
    ap.add_argument("--emit-metrics", metavar="PATH",
                    help="append the marginal-edge metrics as one JSON line to PATH "
                         "(the forward-monitor log — see .github/workflows/altdata-monitor.yml)")
    args = ap.parse_args(argv)
    sink = [] if args.emit_metrics else None
    print(build_report(args.synthetic, args.point_in_time, args.with_altdata,
                       args.sent_horizon, metrics_sink=sink))
    if args.emit_metrics and sink:
        import datetime
        import json as _json
        rec = dict(sink[-1])
        rec["run_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(args.emit_metrics, "a") as f:
            f.write(_json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
