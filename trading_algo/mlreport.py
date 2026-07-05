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
import pandas as pd

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


def _union_pbo(runs: list) -> dict:
    """PBO/CSCV over the union of price+alt L/S monthly returns (columns = model×alpha
    configs) — validates the SELECTION (which learner/alpha), the overfitting risk a
    nonlinear learner adds that an N+1 DSR bump cannot catch."""
    series = [r.get("ls_returns") for r in runs
              if r.get("ls_returns") is not None and len(r.get("ls_returns"))]
    if len(series) < 2:
        return {"pbo": float("nan"), "n_combinations": 0}
    mat = pd.concat(series, axis=1).dropna()
    if len(mat) < 8:
        return {"pbo": float("nan"), "n_combinations": 0}
    return robust.pbo_cscv(mat.to_numpy(), n_splits=8)


def _edge_pass(d1: dict, dsr: dict, pbo_v, clean_inc: bool) -> bool:
    """The pass gate for the pre-declared PRIMARY increment: CI lower bound > 0 AND DSR of the
    difference ≥ 95% AND PBO/CSCV ≤ 50% AND the shuffle-null collapsed AND Δ IC ≥ 0.005.
    It takes NO sentiment/covered argument by construction — a survivor-conditioned covered
    result can never contribute to a pass (the chief-engineer guard)."""
    ci_low = d1.get("ci_low", float("nan"))
    pbo_ok = pbo_v is not None and pbo_v == pbo_v and pbo_v <= 0.5
    return bool((ci_low == ci_low) and ci_low > 0
                and (dsr.get("dsr") or 0) >= 0.95 and pbo_ok
                and d1.get("delta_ic", 0.0) >= 0.005 and clean_inc)


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
      f"Probabilistic Sharpe (P[SR>0]) {psr:.1%}. "
      f"_(long-only reference book — the alt-data pass gate is the INCREMENT DSR + PBO below.)_\n")

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
        sub = mlp.covered_sub_universe(sent_df)          # survivor-conditioned; corroboration only
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

        # Nested price-only vs price+alt under BOTH the ridge (α grid) and the GBRT, DSR on
        # the DIFFERENCE (deflate the INCREMENT, not the book). The PRE-DECLARED PRIMARY cell
        # is: nonlinear GBRT + announcement-dated SUE (automatic in datasources) + the
        # full-universe 21d increment. Ridge is corroboration; sentiment sub-universe is
        # corroboration; only this one cell can constitute a pass.
        base_r = {a: mlp.run_ml_backtest(prices, idx, alpha=a, extra=None) for a in GRID}
        dlt_r = {a: mlp.incremental_delta(base_r[a], runs[a], n_paths=1000, seed=0) for a in GRID}
        d_rg = mlp.incremental_delta(base_r[1.0], runs[1.0], n_paths=2000, seed=0)
        gb_alt = mlp.run_ml_backtest(prices, idx, extra=extra, model="gbrt")
        gb_base = mlp.run_ml_backtest(prices, idx, extra=None, model="gbrt")
        d_gb = mlp.incremental_delta(gb_base, gb_alt, n_paths=2000, seed=0)
        d1 = d_gb                                            # the primary

        # UNION trial set (model × alpha) pays the multiplicity: every increment info-ratio
        # deflates the primary's DSR; every price+alt L/S book is a PBO/CSCV selection column.
        trial_irs = [dlt_r[a]["delta_ir"] for a in GRID] + [d_gb["delta_ir"]]
        trial_irs = [x for x in trial_irs if x == x]
        diff = d1.get("diff")
        dsr_d = (robust.deflated_sharpe_ratio(diff, trial_irs, periods_per_year=12)
                 if diff is not None and len(diff) > 2 and len(trial_irs) >= 2 else {"dsr": float("nan")})
        pbo = _union_pbo([runs[a] for a in GRID] + [gb_alt])
        pbo_v = pbo.get("pbo")

        w("**Nested increment — price+alt − price-only** (the INCREMENT, deflated — not the book):\n")
        w("| learner | Δ IC | increment info-ratio | 90% bootstrap CI | DSR of difference |")
        w("|---|---|---|---|---|")
        w(f"| ridge (α=1) | {d_rg['delta_ic']:+.3f} | {d_rg['delta_ir']:+.2f} | "
          f"[{d_rg['ci_low']:+.2f}, {d_rg['ci_high']:+.2f}] | — |")
        w(f"| **GBRT — PRIMARY** | {d_gb['delta_ic']:+.3f} | **{d_gb['delta_ir']:+.2f}** | "
          f"**[{d_gb['ci_low']:+.2f}, {d_gb['ci_high']:+.2f}]** | **{dsr_d['dsr']:.1%}** |")
        w(f"\n**PBO/CSCV** over the union {{ridge×{len(GRID)}, GBRT}} price+alt L/S books "
          f"**{(pbo_v if pbo_v is not None else float('nan')):.1%}** "
          f"{'✅ robust selection' if (pbo_v is not None and pbo_v == pbo_v and pbo_v <= 0.5) else '⚠️ selection overfits / n/a'}; "
          f"increment DSR deflated across N={len(trial_irs)} model×α trials.\n")

        # shuffle-null on the incremental measure — model-free, must collapse to ~0
        null_ic = mlp.partial_incremental_ic(_shuffle_labels(df, 0), price_cols,
                                             fundamentals + sentiment, oos_dates=oos)
        clean_inc = abs(null_ic["incremental_ic"]) < 0.02
        w(f"**Incremental shuffle-null:** block IC on label-shuffled data "
          f"**{null_ic['incremental_ic']:+.3f}** "
          f"{'✅ collapses (no residual timing leak)' if clean_inc else '⚠️ non-zero → leakage in the alt path'}\n")

        # PASS GATE — the pre-declared PRIMARY (GBRT increment) must clear ALL: CI lower bound
        # > 0, DSR of the difference >= 95% (union-deflated), PBO <= 0.5, shuffle-null collapses.
        straddle0 = (not np.isnan(d1["ci_low"])) and (d1["ci_low"] <= 0 <= d1["ci_high"])
        rg_straddle = np.isnan(d_rg["ci_low"]) or (d_rg["ci_low"] <= 0 <= d_rg["ci_high"])
        pbo_ok = pbo_v is not None and pbo_v == pbo_v and pbo_v <= 0.5
        edge = _edge_pass(d1, dsr_d, pbo_v, clean_inc)

        # forward-monitor record: one machine-readable row per run (does a source EARN weight
        # yet?). Primary = GBRT increment; ridge IR kept as corroboration. See altdata-monitor.yml.
        if metrics_sink is not None:
            metrics_sink.append({
                "universe": "synthetic" if synthetic else ("PIT-US" if point_in_time else "US"),
                "n_oos": int(base["n_periods"]), "primary_model": "gbrt",
                "fund_inc_ic": _num(fund_ic["incremental_ic"]),
                "sue_ic": _num(fund_ic["per_col"].get("sue")),
                "sent_inc_ic": _num(sent_ic["incremental_ic"]),
                "sent_dates": int(sent_ic["n_dates"]),
                "delta_ic": _num(d1["delta_ic"]), "delta_ir": _num(d1["delta_ir"]),
                "ci_low": _num(d1["ci_low"]), "ci_high": _num(d1["ci_high"]),
                "dsr_diff": _num(dsr_d.get("dsr")), "pbo": _num(pbo_v),
                "ridge_delta_ir": _num(d_rg["delta_ir"]),
                "passes": bool(edge),
            })
        if synthetic:
            control_ok = straddle0 and rg_straddle and abs(d1["delta_ic"]) < 0.02 and clean_inc
            if control_ok:
                w("**Negative control (synthetic): ✅ increment straddles 0 under BOTH learners** "
                  "— the full stack (announcement back-dating + coverability fix + GBRT) adds no "
                  "lookahead. Real numbers come only from the CI 'ml' run; nothing here is a claim.\n")
            else:
                w("**🛑 LEAKAGE BUG — synthetic increment is NON-ZERO.** Synthetic alt-data is "
                  "independent of synthetic prices, so any edge here is a lookahead artifact "
                  "(future-dated known_date, value back-dated past first-report, shift-0 baseline). "
                  "ALL real-data numbers from this path are VOID until fixed.\n")
        elif edge:
            w("**✅ Alt-data earns weight** on this run: the PRIMARY (GBRT, announcement-dated) "
              "increment has a bootstrap-CI lower bound > 0, clears Deflated Sharpe ≥ 95% "
              "(union-deflated), and survives PBO/CSCV. Size it into the book via the ERC "
              "combiner by its risk-adjusted marginal contribution.\n")
        else:
            reasons = []
            if not ((not np.isnan(d1["ci_low"])) and d1["ci_low"] > 0):
                reasons.append("CI lower bound ≤ 0")
            if (dsr_d.get("dsr") or 0) < 0.95:
                reasons.append(f"DSR {dsr_d.get('dsr') or float('nan'):.0%} < 95%")
            if not pbo_ok:
                reasons.append(f"PBO {(pbo_v if pbo_v is not None else float('nan')):.0%} > 50%")
            if not clean_inc:
                reasons.append("shuffle-null non-zero")
            w(f"**Not an edge (yet):** {', '.join(reasons) or 'increment not distinguishable from noise'}. "
              "No weight is assigned until the primary cell clears every gate — no sign-flip, no "
              "picking the better of ridge/GBRT or filed/announcement timing.\n")

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
      "after the PRE-DECLARED PRIMARY cell (nonlinear GBRT + announcement-dated SUE + full-"
      "universe 21d increment) clears every gate — CI lower bound > 0, DSR of the difference "
      "≥ 95% (union-deflated), PBO/CSCV ≤ 50%, shuffle-null collapse — on the CI real-data run.")
    if with_altdata:
        w("- **Corroboration ≠ pass.** The ridge increment and the covered-sub-universe "
          "sentiment IC are survivor-/model-variant-conditioned corroboration only; they can "
          "never themselves earn weight, and we never pick the better of filed/announcement "
          "timing or ridge/GBRT after seeing results (that is the banned variant-snooping).")
        w("- **The three levers move the CEILING, not the verdict.** Announcement-dated SUE "
          "fixes a real PEAD timing mis-specification; the GBRT tests a nonlinear effect the "
          "linear ridge cannot express; broader GDELT coverage makes sentiment measurable. But "
          "a GBRT null bounds only *this* pre-registered capacity (we refuse to sweep deeper to "
          "buy a pass), incomplete 8-K 2.02 coverage dilutes some quarters back to the filing "
          "date, and survivorship-clean sentiment still needs paid/GKG-bulk data.")
    w("- The durable win is the *pipeline*: market-neutral, leakage-controlled (purged walk-"
      "forward + shuffle null + synthetic negative control), increment-deflated + PBO-gated, "
      "ready to weight new data the day it earns it (docs/research/PREDICTIVE_MODEL.md).")
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
