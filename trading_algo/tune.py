"""Search principled strategy configs for the best edge over the index blend.

Runs the portfolio backtest across a small grid of cost- and exposure-driven
knobs (rebalance cadence, regime gate on/off, vol target, concentration,
allocation tilt) and prints the **active return** (strategy CAGR − benchmark
CAGR) for each, sorted best-first. Market data is cached after the first config,
so the whole grid runs on a single download.

The aim is a *robust* config that beats the equal-weight index blend — not a lone
overfit peak. Read the whole table, not just row 1.

    python -m trading_algo.tune              # real data (needs network)
    python -m trading_algo.tune --synthetic  # offline harness check
"""
from __future__ import annotations

import argparse
import itertools

from .config import DEFAULT_PARAMS
from .portfolio_backtest import run_portfolio_backtest

# Allocation presets (benchmark is always the equal-weight index blend).
# US-tilt didn't help in earlier runs; keep it equal-weight.
_ALLOCS = {"equal": None}

# Grid of strategy knobs. Focused: best exposure settings (ME, vol 20%) x regime
# on/off x momentum-only vs momentum+value blend, so we can see if adding the
# value factor lifts the active return toward the goal.
_GRID = {
    "rebalance": ["ME"],
    "regime_filter": [True, False],
    "target_vol": [0.20],
    "top_n": [10],
    "use_value": [False, True],       # pure momentum vs 50/50 momentum+value
}


def _evaluate(synthetic: bool, params, allocations) -> dict:
    r = run_portfolio_backtest(synthetic=synthetic, params=params,
                               allocations=allocations)
    m, bm, bs = r["metrics"], r.get("benchmark_metrics", {}), r.get("benchmark_stats", {})
    return {"CAGR": m["CAGR"], "MaxDD": m["MaxDrawdown"],
            "Bench": bm.get("CAGR"), "Active": bs.get("ActiveReturn"),
            "Alpha": bs.get("Alpha"), "Beta": bs.get("Beta")}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Strategy config tuner")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    keys = list(_GRID)
    rows = []
    for combo in itertools.product(*_GRID.values()):
        kw = dict(zip(keys, combo))
        params = DEFAULT_PARAMS.with_overrides(**kw)
        for alloc_name, alloc in _ALLOCS.items():
            try:
                res = _evaluate(args.synthetic, params, alloc)
            except Exception as exc:
                res = {"CAGR": None, "MaxDD": None, "Bench": None,
                       "Active": None, "Alpha": None, "Beta": None, "err": repr(exc)}
            rows.append({**kw, "alloc": alloc_name, **res})

    rows.sort(key=lambda r: (r["Active"] if r["Active"] is not None else -9), reverse=True)

    print("# Strategy tuning — active return vs the equal-weight index blend\n")
    if args.synthetic:
        print("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    print("| rebal | regime | vol | top_n | value | alloc | CAGR | Bench | **Active** | Alpha | Beta | MaxDD |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["Active"] is None:
            print(f"| {r['rebalance']} | {r['regime_filter']} | {r['target_vol']:.0%} | "
                  f"{r['top_n']} | {r.get('use_value')} | {r['alloc']} | — | — | ERR | — | — | — |")
            continue
        print(f"| {r['rebalance']} | {r['regime_filter']} | {r['target_vol']:.0%} | "
              f"{r['top_n']} | {r.get('use_value')} | {r['alloc']} | {r['CAGR']:.1%} | {r['Bench']:.1%} | "
              f"**{r['Active']:+.1%}** | {r['Alpha']:+.1%} | {r['Beta']} | {r['MaxDD']:.1%} |")

    best = rows[0]
    if best["Active"] is not None:
        print(f"\n**Best active return: {best['Active']:+.1%}** — rebalance="
              f"{best['rebalance']}, regime_filter={best['regime_filter']}, "
              f"target_vol={best['target_vol']:.0%}, top_n={best['top_n']}, "
              f"use_value={best.get('use_value')}, alloc={best['alloc']} "
              f"(benchmark CAGR {best['Bench']:.1%}).")
        print(f"\nGoal (beat by ≥ +2.0%): "
              f"{'✅ MET' if best['Active'] >= 0.02 else '❌ not yet'}")


if __name__ == "__main__":
    main()
