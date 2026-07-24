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
from .validation import deflated_sharpe_ratio, sharpe_ratio, sr_variance_across

# Goal is met only if the best config beats the benchmark by this much AND its
# edge survives deflation for the size of the grid searched (selection bias).
_ACTIVE_MIN = 0.02
_DSR_MIN = 0.95

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
    # Excess (active) return series: the exact quantity we sort/select on, so it
    # is the series whose Sharpe must be deflated for the grid search.
    strat_ret = r["returns"]
    bench_ret = r["benchmark"].pct_change(fill_method=None).reindex(strat_ret.index).fillna(0.0)
    excess = (strat_ret - bench_ret).to_numpy(dtype=float)
    return {"CAGR": m["CAGR"], "MaxDD": m["MaxDrawdown"],
            "Bench": bm.get("CAGR"), "Active": bs.get("ActiveReturn"),
            "Alpha": bs.get("Alpha"), "Beta": bs.get("Beta"),
            "excess": excess}


def _goal_verdict(active, dsr, active_min: float = _ACTIVE_MIN,
                  dsr_min: float = _DSR_MIN) -> str:
    """The goal is MET only if the best config beats the benchmark by
    >= `active_min` AND its Deflated Sharpe clears `dsr_min` — i.e. the in-sample
    edge survives correction for how many configs we searched. Beating the
    benchmark on undeflated in-sample Active alone is NOT success (that is the
    selection bias this tuner would otherwise fall into)."""
    if active is None or dsr is None:
        return "❌ not yet (no result)"
    beats, survives = active >= active_min, dsr >= dsr_min
    if beats and survives:
        return (f"✅ MET (Active {active:+.1%} ≥ {active_min:+.0%} and "
                f"DSR {dsr:.2f} ≥ {dsr_min:.2f})")
    if beats and not survives:
        return (f"❌ not yet — in-sample Active {active:+.1%} clears, but DSR "
                f"{dsr:.2f} < {dsr_min:.2f}: the edge does not survive deflation "
                f"for the grid searched")
    return f"❌ not yet (Active {active:+.1%}, DSR {dsr:.2f})"


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
                       "Active": None, "Alpha": None, "Beta": None,
                       "excess": None, "err": repr(exc)}
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

        # Deflate the SELECTED best for the whole grid we searched: sorting a grid
        # by in-sample Active and reading row 1 is a max over `n_trials` draws, so
        # its Sharpe must be judged against the expected max under the null.
        n_trials = len(rows)
        dsr = None
        best_excess = best.get("excess")
        if best_excess is not None:
            sr_var = sr_variance_across(
                [sharpe_ratio(r["excess"]) for r in rows
                 if r.get("excess") is not None])
            dsr = deflated_sharpe_ratio(best_excess, n_trials,
                                        sr_var if sr_var > 0 else None)
        dsr_str = f"{dsr:.2f}" if dsr is not None else "n/a"
        print(f"\nDeflated Sharpe of the selected config (active return, "
              f"n_trials={n_trials}): {dsr_str}  "
              f"(vs raw in-sample Active {best['Active']:+.1%}).")
        print(f"\nGoal (beat by ≥ +{_ACTIVE_MIN:.0%} AND DSR ≥ {_DSR_MIN:.2f}): "
              f"{_goal_verdict(best['Active'], dsr)}")


if __name__ == "__main__":
    main()
