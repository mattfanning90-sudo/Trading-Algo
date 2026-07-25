"""CLI: run the multi-agent FX backtest and print a report.

    python -m trading_algo.forex.run_backtest --synthetic        # offline pipeline
    python -m trading_algo.forex.run_backtest                     # real Yahoo data
    python -m trading_algo.forex.run_backtest --profile aggressive
    python -m trading_algo.forex.run_backtest --compare           # all profiles
    python -m trading_algo.forex.run_backtest --source alpaca --bar 1h  # US equities
    python -m trading_algo.forex.run_backtest --source crypto --bar 1m  # crypto
    python -m trading_algo.forex.run_backtest --account matt      # + dashboard cache

Costs (spread + carry) are always on — there is no gross-only mode.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from ..metrics import metric as _metric   # fx_backtest builds these dicts with
                                          # metrics.compute_metrics — one reader
from . import feeds
from . import fx_book
from . import fx_config as cfg
from . import marks
from . import pairs
from .agents import AgentPool, default_agents
from .fx_backtest import run_backtest
from .fx_config import ACCOUNT_CURRENCY, profile, profile_names

MAX_POINTS = 500     # same curve budget as dashboard/backtest_store.py


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


def _curve(eq, bar: str) -> list[list]:
    """[[bar_key, equity], ...], downsampled to MAX_POINTS so the payload stays
    small. Bar keys use the book's own cadence (fx_book._bar_key), so an
    intraday book's curve doesn't collapse every bar of a day onto one label."""
    step = max(1, len(eq) // MAX_POINTS)
    idx = list(range(0, len(eq), step))
    if idx[-1] != len(eq) - 1:
        idx.append(len(eq) - 1)
    return [[fx_book._bar_key(eq.index[i], bar), round(float(eq.iloc[i]), 2)]
            for i in idx]


def _payload(res: dict, account: str, profile_name: str, bar: str, source: str,
             universe: list[str], capital: float, synthetic: bool) -> dict:
    """Reshape a finished backtest into the dashboard's FX BACKTEST cache
    (`kind: "fx"`, read by dashboard.backtest_store.load_backtest).

    Everything here already exists in `res` — only `payoff` and the annualised
    `turnover` are derived, and both come straight off the returned series. Two
    things the FX book genuinely does NOT have are omitted rather than faked:

      * `folds` — the ensemble fits no parameters, so there is no train/test
        split to report; a contiguous slicing of one run is not walk-forward.
      * `benchmark` — a long-only basket of FX pairs is not a market anyone
        holds, so there is no honest buy-and-hold line.

    The UI already degrades to "—" / "NO WALK-FORWARD FOLDS RECORDED" for both.
    """
    eq, rets, turn = res["equity"], res["returns"], res["turnover"]
    wins, losses = rets[rets > 0], rets[rets < 0]
    ppy = marks.periods_per_year(eq.index)
    m = res["metrics"]
    return {
        "kind": "fx",
        "account": account,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Invariant #5: a synthetic run is a pipeline test, never performance.
        # The flag travels with the numbers so the UI can say so.
        "synthetic": synthetic,
        "profile": profile_name,
        "bar": bar,
        "source": source,
        "universe": list(universe),
        "currency": ACCOUNT_CURRENCY,
        "initial_capital": float(capital),
        "start": fx_book._bar_key(eq.index[0], bar),
        "end": fx_book._bar_key(eq.index[-1], bar),
        "curve": _curve(eq, bar),
        "metrics": {
            "cagr": _metric(m, "CAGR"),
            "ann_vol": _metric(m, "AnnVol"),
            "sharpe": _metric(m, "Sharpe"),
            "sortino": _metric(m, "Sortino"),
            "max_drawdown": _metric(m, "MaxDrawdown"),
            "calmar": _metric(m, "Calmar"),
            "hit_rate": _metric(m, "WinRate"),          # fraction of profitable bars
            # Payoff = avg winning bar / avg losing bar. Null (not 1.0) when the
            # run had no losing bars — there is nothing to divide by.
            "payoff": (round(float(wins.mean() / -losses.mean()), 2)
                       if len(wins) and len(losses) else None),
            # One-way turnover (sum of |Δweight|) per bar, annualised at the
            # book's own cadence — "~9× / YR" of equity traded.
            "turnover": round(float(turn.mean() * ppy), 1) if len(turn) else None,
        },
        # The cost/risk reality the CLI prints, kept alongside the returns.
        "avg_gross_leverage": round(float(res["avg_gross_leverage"]), 3),
        "total_cost_fraction": round(float(res["total_cost_fraction"]), 6),
        "total_carry_fraction": round(float(res["total_carry_fraction"]), 6),
        "drawdown_halts": int(res["drawdown_halts"]),
        "drawdown_halt_days": int(res["drawdown_halt_days"]),
        "attribution": {str(k): round(float(v), 6)
                        for k, v in res["attribution"].items()},
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-agent FX backtest")
    ap.add_argument("--synthetic", action="store_true", help="offline synthetic data")
    ap.add_argument("--profile", default=None, choices=profile_names(),
                    help="risk profile (default: the --account book's own, else balanced)")
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--compare", action="store_true", help="run every risk profile")
    ap.add_argument("--account", default=None,
                    help="run this paper book's own configuration (profile, capital, "
                         "bar, universe, source) and write the dashboard's FX "
                         "BACKTEST cache to state/fx_backtest_{account}.json")
    ap.add_argument("--out", default=None, metavar="PATH",
                    help="write that cache here instead of the live path (use with "
                         "--synthetic so a pipeline test never lands where the "
                         "dashboard reads it — invariant #5)")
    ap.add_argument("--bar", default=None,
                    help="data bar interval, e.g. 60m for intraday, 1m for HF crypto "
                         "(default: the --account book's own, else 1d)")
    ap.add_argument("--source", default=None, choices=feeds.SOURCES,
                    help="market-data source: yahoo (default), crypto, oanda, "
                         "alpaca, openbb. See docs/DATA_FEEDS.md.")
    ap.add_argument("--exchange", default=None,
                    help="crypto exchange via ccxt (e.g. binance) for the crypto "
                         "source; default binance. See docs/CRYPTO_HF.md.")
    ap.add_argument("--universe", default=None,
                    help="override the source's natural universe: a named preset "
                         f"({', '.join(pairs.UNIVERSES)}) or a comma-separated "
                         "symbol list, e.g. majors+crosses.")
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, not performance.")
    # A book's BACKTEST tab must describe THAT book, so --account adopts its own
    # knobs (any explicit flag still wins). A book that hasn't been opened yet
    # falls back to its static ACCOUNTS spec, so --account works before --init.
    book: dict = {}
    if args.account:
        book = (fx_book.load_state(args.account)
                if fx_book.account_exists(args.account)
                else cfg.ACCOUNTS.get(args.account, {}))
    prof = args.profile or book.get("profile") or "balanced"
    bar = args.bar or book.get("bar") or "1d"
    capital = args.capital if args.capital is not None else float(
        book.get("initial_capital") or book.get("capital") or cfg.DEFAULT_CAPITAL)
    source = feeds.resolve_source(
        args.source if args.source is not None else book.get("source"), args.exchange)
    if prof == "hf_crypto" and source == "yahoo":
        source = "crypto"
    # Each source brings its natural universe (FX majors / crypto / US equities);
    # --universe overrides it so a candidate set (e.g. majors+crosses) can be tested.
    if args.universe:
        universe = pairs.resolve_universe(args.universe)
        print(f"Universe ({args.universe}): {', '.join(universe)}")
    elif book.get("symbols"):
        universe = list(book["symbols"])
    else:
        universe = feeds.default_universe(source, prof)
    panel = feeds.load(universe, synthetic=args.synthetic, interval=bar,
                       source=source, exchange=args.exchange, use_cache=True)
    if not panel:
        raise SystemExit("No market data (offline? try --synthetic).")
    pool = AgentPool(default_agents(), max_workers=args.workers)

    names = profile_names() if args.compare else [prof]
    for name in names:
        res = run_backtest(panel, profile(name), pool=pool, initial_capital=capital)
        _print_result(name, res)
        # --compare runs every profile, but only the book's own one is cached.
        if (args.account or args.out) and name == prof:
            path = args.out or os.path.join(
                fx_book.STATE_DIR, f"fx_backtest_{args.account}.json")
            with open(path, "w") as f:
                json.dump(_payload(res, args.account or "", name, bar, source,
                                   sorted(panel), capital, args.synthetic), f)
            print(f"  dashboard cache → {path}")


if __name__ == "__main__":
    main()
