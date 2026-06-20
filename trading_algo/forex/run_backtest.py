"""CLI: run the multi-agent FX backtest and print a report.

    python -m trading_algo.forex.run_backtest --synthetic        # offline pipeline
    python -m trading_algo.forex.run_backtest                     # real Yahoo data
    python -m trading_algo.forex.run_backtest --profile aggressive
    python -m trading_algo.forex.run_backtest --compare           # all profiles

Costs (spread + carry) are always on — there is no gross-only mode.
"""
from __future__ import annotations

import argparse

from . import fx_config as cfg
from . import fx_data
from .agents import AgentPool, default_agents
from .fx_backtest import run_backtest
from .fx_config import profile, profile_names
from .pairs import DEFAULT_UNIVERSE


def _load(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    return fx_data.load_panel(symbols, cfg.START, use_cache=True)


def _print_result(name: str, res: dict) -> None:
    print(f"\n=== FX backtest [{name}] ===")
    for k, v in res["metrics"].items():
        print(f"  {k:<22} {v}")
    print(f"  Avg gross leverage     {res['avg_gross_leverage']:.2f}x")
    print(f"  Total spread cost      {res['total_cost_fraction']:.2%} of equity")
    print(f"  Total carry            {res['total_carry_fraction']:+.2%} of equity")
    print(f"  Drawdown halts         {res['drawdown_halts']} "
          f"({res['drawdown_halt_days']} days flat)")
    print("  P&L attribution by pair:")
    for pair, pnl in res["attribution"].items():
        print(f"    {pair:<8} {pnl:+.2%}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-agent FX backtest")
    ap.add_argument("--synthetic", action="store_true", help="offline synthetic data")
    ap.add_argument("--profile", default="balanced", choices=profile_names())
    ap.add_argument("--capital", type=float, default=cfg.DEFAULT_CAPITAL)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--compare", action="store_true", help="run every risk profile")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, not performance.")
    panel = _load(DEFAULT_UNIVERSE, args.synthetic)
    if not panel:
        raise SystemExit("No FX data (offline? try --synthetic).")
    pool = AgentPool(default_agents(), max_workers=args.workers)

    names = profile_names() if args.compare else [args.profile]
    for name in names:
        res = run_backtest(panel, profile(name), pool=pool, initial_capital=args.capital)
        _print_result(name, res)


if __name__ == "__main__":
    main()
