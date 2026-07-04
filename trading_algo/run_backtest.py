"""Run the backtest.

    python -m trading_algo.run_backtest                 # full AUD portfolio (all sleeves)
    python -m trading_algo.run_backtest --region US     # single sleeve (local currency)
    python -m trading_algo.run_backtest --synthetic     # offline pipeline smoke test
"""
from __future__ import annotations

import argparse

from . import config as cfg
from . import constituents, data
from .backtest import run_backtest
from .portfolio_backtest import run_portfolio_backtest
from .regions import all_region_keys, get_region
from .strategy import compute_targets


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
    _latest_picks(prices, index_px, region)
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
    print(f"\n  Allocations: " + ", ".join(f"{k} {v:.0%}"
                                            for k, v in result["allocations"].items()))
    print(f"  FX rebalance cost (cum.):  {cfg.BASE_CURRENCY} "
          f"{result['fx_rebalance_cost']:,.0f}")
    result["equity"].to_csv("equity_curve_portfolio.csv")
    result["sleeve_equity"].to_csv("equity_curve_sleeves.csv")
    print("\n  Equity curves -> equity_curve_portfolio.csv, equity_curve_sleeves.csv")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    # Single-sleeve choices come from the region REGISTRY, not ALLOCATIONS, so a
    # scaffolded-but-unfunded sleeve (e.g. TSX) can still be backtested on its own.
    ap.add_argument("--region", choices=all_region_keys(), help="single sleeve")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true",
                    help="use point-in-time constituents (survivorship-bias corrected)")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, numbers are meaningless\n")
    if args.region:
        run_single(args.region, args.synthetic, args.point_in_time)
    else:
        run_portfolio(args.synthetic, args.point_in_time)


if __name__ == "__main__":
    main()
