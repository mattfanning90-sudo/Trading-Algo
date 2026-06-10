"""Run the backtest.

    python -m trading_algo.run_backtest                 # full AUD portfolio (all sleeves)
    python -m trading_algo.run_backtest --region US     # single sleeve (local currency)
    python -m trading_algo.run_backtest --synthetic     # offline pipeline smoke test
"""
from __future__ import annotations

import argparse

from . import config as cfg
from . import data
from .backtest import run_backtest
from .portfolio_backtest import run_portfolio_backtest
from .regions import get_region
from .strategy import compute_targets


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


def run_single(region_key: str, synthetic: bool) -> None:
    region = get_region(region_key)
    if synthetic:
        prices, index_px = data.synthetic_region(region)
    else:
        prices, index_px = data.load_region(region, cfg.START)
    result = run_backtest(prices, index_px, region)
    _print_metrics(f"{region.name} sleeve — Backtest ({region.currency})", result["metrics"])
    if len(result["turnover"]):
        print(f"  Avg monthly turnover       {result['turnover'].mean():.1%}")
    print(f"  Cumulative cost drag       {result['total_cost_fraction']:.1%}")
    _latest_picks(prices, index_px, region)
    result["equity"].to_csv(f"equity_curve_{region.key}.csv")
    print(f"\n  Equity curve -> equity_curve_{region.key}.csv")


def run_portfolio(synthetic: bool) -> None:
    result = run_portfolio_backtest(synthetic=synthetic)
    _print_metrics(f"Multi-Region Portfolio — Backtest ({cfg.BASE_CURRENCY})",
                   result["metrics"])
    print("  Per-sleeve (standalone, local currency):")
    for k, s in result["sleeves"].items():
        m = s["metrics"]
        sharpe_key = next((kk for kk in m if kk.startswith("Sharpe")), None)
        print(f"    {k:<5} CAGR {m['CAGR']:>7.1%}  Vol {m['AnnVol']:>6.1%}  "
              f"Sharpe {m.get(sharpe_key, float('nan')):>5}  MaxDD {m['MaxDrawdown']:>7.1%}")
    print(f"\n  Allocations: " + ", ".join(f"{k} {v:.0%}"
                                            for k, v in result["allocations"].items()))
    print(f"  FX rebalance cost (cum.):  {cfg.BASE_CURRENCY} "
          f"{result['fx_rebalance_cost']:,.0f}")
    result["equity"].to_csv("equity_curve_portfolio.csv")
    result["sleeve_equity"].to_csv("equity_curve_sleeves.csv")
    print("\n  Equity curves -> equity_curve_portfolio.csv, equity_curve_sleeves.csv")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", choices=list(cfg.ALLOCATIONS), help="single sleeve")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, numbers are meaningless\n")
    if args.region:
        run_single(args.region, args.synthetic)
    else:
        run_portfolio(args.synthetic)


if __name__ == "__main__":
    main()
