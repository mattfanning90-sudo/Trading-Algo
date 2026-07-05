"""Run the backtest.

    python -m trading_algo.run_backtest                 # full AUD portfolio (all sleeves)
    python -m trading_algo.run_backtest --region US     # single sleeve (local currency)
    python -m trading_algo.run_backtest --synthetic     # offline pipeline smoke test
"""
from __future__ import annotations

import argparse
import os

from . import config as cfg
from . import constituents, data, manifest, validation
from .backtest import run_backtest
from .portfolio_backtest import run_portfolio_backtest
from .regions import all_region_keys, get_region
from .strategy import compute_targets

# Where run manifests + the experiment ledger live (env override for CI).
_STATE_DIR = os.environ.get("MOMENTUM_STATE_DIR") or os.path.join(os.path.dirname(__file__), "..")
_LEDGER = os.path.join(_STATE_DIR, "experiment_ledger.jsonl")


def _emit_manifest(kind, params, regions, metrics, synthetic, point_in_time,
                   data_range) -> None:
    """Record a reproducible manifest for this run and append it to the ledger
    (backlog F17). Best-effort: a manifest failure must never fail a backtest."""
    try:
        m = manifest.build_manifest(
            kind, params=params, regions=list(regions), metrics=metrics,
            data_range=data_range, synthetic=synthetic, point_in_time=point_in_time)
        fp = m["params_fingerprint"]
        manifest.write_manifest(
            m, os.path.join(_STATE_DIR, "manifests", f"{kind}_{fp}.json"))
        manifest.append_run(_LEDGER, m)
        print(f"  Manifest logged ({kind}, params {fp}) -> ledger "
              f"[{manifest.trial_count(_LEDGER)} runs]")
    except Exception as exc:   # pragma: no cover - never break a run over logging
        print(f"  ⚠ manifest not written: {exc}")


def _print_deflation(returns) -> None:
    """F2 / F19: surface PSR, a Deflated Sharpe and a haircut Sharpe next to the
    raw one. The trial count is the honest cumulative number of runs recorded in
    the experiment ledger (F17) — so a Sharpe found after many attempts is
    deflated accordingly. A single fresh ledger deflates by 1 (no penalty)."""
    n_trials = max(1, manifest.trial_count(_LEDGER))
    r = returns.to_numpy()
    summ = validation.deflation_summary(r, n_trials)
    hc = validation.sharpe_haircut(r, n_trials)
    print(f"  PSR(>0)                    {summ['psr']:.2f}")
    print(f"  Deflated Sharpe (N={n_trials:<3} trials) {summ['dsr']:.2f}")
    print(f"  Haircut Sharpe             {hc['haircut_sharpe_ann']} "
          f"(raw {hc['raw_sharpe_ann']}, less selection luck {hc['deflation_ann']})")


def _universe_label(point_in_time: bool) -> str:
    return ("point-in-time constituents (survivorship-bias corrected)"
            if point_in_time else
            "CURRENT universe — survivorship-biased, treat numbers as an upper bound")


def _print_metrics(title: str, metrics: dict) -> None:
    print("=" * 52)
    print(f"  {title}")
    print("=" * 52)
    for k, v in metrics.items():
        print(f"  {k:<26} {v}")
    print("=" * 52)


def _latest_picks(prices, index_px, region) -> None:
    w = compute_targets(prices, index_px, region.params)
    if w.empty:
        print(f"  [{region.key}] regime RISK-OFF — would hold cash.")
        return
    print(f"  [{region.key}] latest target book:")
    for t, wt in w.sort_values(ascending=False).items():
        print(f"      {t:<10} {wt:6.1%}")


def run_single(region_key: str, synthetic: bool, point_in_time: bool) -> None:
    region = get_region(region_key)
    membership = None
    if point_in_time:
        membership = (constituents.synthetic_membership(region)
                      if synthetic else constituents.get_membership(region))
        if membership is None:
            print(f"  ⚠ no constituents file for {region.key}; "
                  f"falling back to current universe.")
    pit_tickers = membership.all_tickers if membership is not None else None

    if synthetic:
        prices, index_px = data.synthetic_region(region)
    else:
        prices, index_px = data.load_region(region, cfg.START, tickers=pit_tickers)
    result = run_backtest(prices, index_px, region, membership=membership)
    _print_metrics(f"{region.name} sleeve — Backtest ({region.currency})", result["metrics"])
    print(f"  Universe: {_universe_label(result['point_in_time'])}")
    if len(result["turnover"]):
        print(f"  Avg monthly turnover       {result['turnover'].mean():.1%}")
    print(f"  Cumulative cost drag       {result['total_cost_fraction']:.1%}")
    if result.get("drawdown_halts"):
        print(f"  Drawdown halts             {result['drawdown_halts']} "
              f"({result['drawdown_halt_days']} days in cash)")
    _print_deflation(result["returns"])
    _latest_picks(prices, index_px, region)
    _emit_manifest("backtest", region.params, [region.key], result["metrics"],
                   synthetic, result["point_in_time"],
                   (prices.index[0], prices.index[-1]))
    result["equity"].to_csv(f"equity_curve_{region.key}.csv")
    print(f"\n  Equity curve -> equity_curve_{region.key}.csv")


def run_portfolio(synthetic: bool, point_in_time: bool) -> None:
    result = run_portfolio_backtest(synthetic=synthetic, point_in_time=point_in_time)
    _print_metrics(f"Multi-Region Portfolio — Backtest ({cfg.BASE_CURRENCY})",
                   result["metrics"])
    print(f"  Universe: {_universe_label(result['point_in_time'])}")
    print("  Per-sleeve (standalone, local currency):")
    for k, s in result["sleeves"].items():
        m = s["metrics"]
        sharpe_key = next((kk for kk in m if kk.startswith("Sharpe")), None)
        print(f"    {k:<5} CAGR {m['CAGR']:>7.1%}  Vol {m['AnnVol']:>6.1%}  "
              f"Sharpe {m.get(sharpe_key, float('nan')):>5}  MaxDD {m['MaxDrawdown']:>7.1%}")
    bs = result.get("benchmark_stats") or {}
    if bs:
        bm = result["benchmark_metrics"]
        print("\n  vs Benchmark (equal-weight indices, AUD buy & hold):")
        print(f"    Benchmark CAGR {bm['CAGR']:>7.1%}  Vol {bm['AnnVol']:>6.1%}  "
              f"MaxDD {bm['MaxDrawdown']:>7.1%}")
        print(f"    Active {bs['ActiveReturn']:>+7.1%}  Alpha {bs['Alpha']:>+7.1%}  "
              f"Beta {bs['Beta']}  InfoRatio {bs['InfoRatio']}")
    print("\n  Allocations: " + ", ".join(f"{k} {v:.0%}"
                                            for k, v in result["allocations"].items()))
    print(f"  FX rebalance cost (cum.):  {cfg.BASE_CURRENCY} "
          f"{result['fx_rebalance_cost']:,.0f}")
    _print_deflation(result["returns"])
    _emit_manifest("portfolio", cfg.DEFAULT_PARAMS, list(result["allocations"]),
                   result["metrics"], synthetic, result["point_in_time"],
                   (result["equity"].index[0], result["equity"].index[-1]))
    result["equity"].to_csv("equity_curve_portfolio.csv")
    result["sleeve_equity"].to_csv("equity_curve_sleeves.csv")
    print("\n  Equity curves -> equity_curve_portfolio.csv, equity_curve_sleeves.csv")


def pit_impact(synthetic: bool) -> dict:
    """F1: quantify the survivorship bias — CAGR of the static (current-universe)
    backtest minus the point-in-time backtest. A positive delta is the inflation
    the current universe carries. Returns both CAGRs and their difference."""
    static = run_portfolio_backtest(synthetic=synthetic, point_in_time=False)
    pit = run_portfolio_backtest(synthetic=synthetic, point_in_time=True)
    s_cagr = float(static["metrics"]["CAGR"])
    p_cagr = float(pit["metrics"]["CAGR"])
    return {"static_cagr": s_cagr, "pit_cagr": p_cagr, "delta": s_cagr - p_cagr}


def run_compare_pit(synthetic: bool) -> None:
    imp = pit_impact(synthetic)
    print("=" * 52)
    print("  Survivorship bias — static universe vs point-in-time")
    print("=" * 52)
    print(f"  Static (current universe) CAGR   {imp['static_cagr']:+.2%}")
    print(f"  Point-in-time CAGR               {imp['pit_cagr']:+.2%}")
    print(f"  Survivorship inflation (delta)   {imp['delta']:+.2%}")
    print("  (positive delta = the current universe flatters returns)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    # Single-sleeve choices come from the region REGISTRY, not ALLOCATIONS, so a
    # scaffolded-but-unfunded sleeve (e.g. TSX) can still be backtested on its own.
    ap.add_argument("--region", choices=all_region_keys(), help="single sleeve")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true",
                    help="use point-in-time constituents (survivorship-bias corrected)")
    ap.add_argument("--compare-pit", action="store_true",
                    help="report the static-vs-point-in-time CAGR delta (F1)")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, numbers are meaningless\n")
    if args.compare_pit:
        run_compare_pit(args.synthetic)
    elif args.region:
        run_single(args.region, args.synthetic, args.point_in_time)
    else:
        run_portfolio(args.synthetic, args.point_in_time)


if __name__ == "__main__":
    main()
