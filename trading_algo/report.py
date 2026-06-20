"""Generate a Markdown backtest report (real or synthetic data).

Run anywhere with market-data access (your machine, or the cloud Backtest
workflow). It runs the full AUD portfolio backtest and prints a Markdown report:
portfolio metrics, the benchmark comparison (alpha / beta / active return),
per-sleeve breakdown, allocations and costs.

    python -m trading_algo.report                    # real data
    python -m trading_algo.report --point-in-time    # survivorship-corrected
    python -m trading_algo.report --synthetic        # offline pipeline test
    python -m trading_algo.report --out report.md    # also write to a file
"""
from __future__ import annotations

import argparse

from . import config as cfg
from .portfolio_backtest import run_portfolio_backtest


def portfolio_markdown(result: dict, synthetic: bool, point_in_time: bool) -> str:
    m = result["metrics"]
    bm = result.get("benchmark_metrics", {})
    bs = result.get("benchmark_stats", {})
    out: list[str] = [f"# Multi-Region Momentum — Backtest ({cfg.BASE_CURRENCY})", ""]

    if synthetic:
        out.append("> ⚠️ **SYNTHETIC DATA** — pipeline test only, not performance.\n")
    universe = ("point-in-time constituents (survivorship-corrected)"
                if point_in_time else
                "current universe (survivorship-biased — treat as an upper bound)")
    out += [f"- **Universe:** {universe}",
            f"- **Period start:** {cfg.START}", ""]

    out += ["## Portfolio", "", "| Metric | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in m.items()]
    out.append("")

    if bs:
        out += ["## vs Benchmark (equal-weight indices, AUD buy & hold)", "",
                "| Metric | Value |", "|---|---|",
                f"| Benchmark CAGR | {bm.get('CAGR')} |",
                f"| Benchmark MaxDrawdown | {bm.get('MaxDrawdown')} |"]
        out += [f"| {k} | {v} |" for k, v in bs.items()]
        out.append("")

    out += ["## Per-sleeve (standalone, local currency)", "",
            "| Sleeve | CAGR | Vol | Sharpe | MaxDD | Turnover | DD halts |",
            "|---|---|---|---|---|---|---|"]
    for k, s in result["sleeves"].items():
        sm = s["metrics"]
        sk = next((kk for kk in sm if kk.startswith("Sharpe")), "")
        turn = s["turnover"].mean() if len(s["turnover"]) else 0.0
        out.append(f"| {k} | {sm['CAGR']:.1%} | {sm['AnnVol']:.1%} | "
                   f"{sm.get(sk)} | {sm['MaxDrawdown']:.1%} | {turn:.1%} | "
                   f"{s.get('drawdown_halts', 0)} |")
    out.append("")

    out.append("- Allocations: " + ", ".join(f"{k} {v:.0%}"
                                              for k, v in result["allocations"].items()))
    out.append(f"- FX rebalance cost (cum.): {cfg.BASE_CURRENCY} "
               f"{result['fx_rebalance_cost']:,.0f}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Markdown backtest report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true")
    ap.add_argument("--out", default=None, help="also write the report to this file")
    args = ap.parse_args(argv)

    result = run_portfolio_backtest(synthetic=args.synthetic,
                                    point_in_time=args.point_in_time)
    md = portfolio_markdown(result, args.synthetic, args.point_in_time)
    print(md)
    try:                                   # equity curves for the run artifact
        result["equity"].to_csv("equity_curve_portfolio.csv")
        result["benchmark"].to_csv("benchmark_curve.csv")
    except Exception:
        pass
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
