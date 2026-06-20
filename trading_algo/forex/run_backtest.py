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


def _load(symbols, synthetic, bar="1d", exchange=None):
    # Crypto exchange source (ccxt) — the high-frequency crypto path.
    if exchange:
        from . import crypto_data
        if synthetic:
            return crypto_data.synthetic_crypto_panel(symbols, timeframe=bar)
        return crypto_data.load_ohlcv(symbols, timeframe=bar, exchange=exchange)
    daily = bar in ("1d", "B")
    if synthetic:
        if daily:
            return fx_data.synthetic_panel(symbols)
        return fx_data.synthetic_panel(symbols, start="2025-01-01", end="2025-04-01", freq=bar)
    start = cfg.START if daily else "2024-06-01"
    return fx_data.load_panel(symbols, start, interval=bar, use_cache=True)


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
    ap.add_argument("--bar", default="1d",
                    help="data bar interval, e.g. 60m for intraday, 1m for HF crypto "
                         "(metrics assume daily)")
    ap.add_argument("--exchange", default=None,
                    help="crypto exchange via ccxt (e.g. binance) for HF crypto; "
                         "default uses Yahoo. See docs/CRYPTO_HF.md.")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, not performance.")
    # An hf_crypto profile (or any --exchange source) trades the crypto universe.
    if args.exchange or args.profile == "hf_crypto":
        from . import crypto_data
        universe = crypto_data.CRYPTO_UNIVERSE
    else:
        universe = DEFAULT_UNIVERSE
    panel = _load(universe, args.synthetic, bar=args.bar, exchange=args.exchange)
    if not panel:
        raise SystemExit("No FX data (offline? try --synthetic).")
    pool = AgentPool(default_agents(), max_workers=args.workers)

    names = profile_names() if args.compare else [args.profile]
    for name in names:
        res = run_backtest(panel, profile(name), pool=pool, initial_capital=args.capital)
        _print_result(name, res)


if __name__ == "__main__":
    main()
