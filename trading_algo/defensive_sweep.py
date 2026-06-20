"""Defensive-sleeve sweep — what should idle / risk-off capital earn?

The momentum book is only ~half invested on average (the trend, regime and
vol-target filters park the rest). Today that idle fraction sits in 0% cash,
which is the single biggest drag versus a fully-invested index. This sweep
re-runs ONE regional sleeve with the idle fraction rotated into each defensive
option and reports the active return vs the index, so we can see whether parking
the cash productively closes the gap *without* giving up the crash protection:

    cash   — 0% (today's behaviour, the baseline)
    tbill  — short T-bills (pure carry, no duration/equity risk)
    bonds  — 7-10y Treasuries (carry + flight-to-quality rally in crashes)
    gold   — gold (crisis hedge, no yield, volatile)

Only the uninvested fraction earns the defensive return; equity holdings are
untouched, so none of these add equity-crash risk. The benchmark is the regional
index buy-and-hold (the thing the goal says to beat).

    python -m trading_algo.defensive_sweep --region US     # real data (network)
    python -m trading_algo.defensive_sweep --synthetic     # offline harness check
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from . import config as cfg
from . import data
from .backtest import run_backtest
from .metrics import benchmark_stats, compute_metrics
from .regions import get_region

# Annual carry assumed for the constant "tbill" model when no real series exists
# (synthetic mode only). Real runs use the actual BIL ETF return series.
_SYNTH_TBILL_YIELD = 0.04


def _synth_defensive(index: pd.Index, seed: int, drift: float, vol: float) -> pd.Series:
    """A reproducible synthetic daily-return path for a defensive asset (offline
    harness only — meaningless numbers, just exercises the plumbing)."""
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(drift, vol, len(index)), index=index)


def _options(region, prices, index_px, synthetic: bool):
    """Yield (label, cash_yield, defensive_returns) for each defensive choice."""
    idx = prices.index
    yield ("cash (0%)", 0.0, None)
    if synthetic:
        yield (f"tbill (~{_SYNTH_TBILL_YIELD:.0%})", _SYNTH_TBILL_YIELD, None)
        yield ("bonds (synthetic)", 0.0, _synth_defensive(idx, 1, 0.00015, 0.003))
        yield ("gold (synthetic)", 0.0, _synth_defensive(idx, 2, 0.00020, 0.009))
        return
    da = region.defensive_assets
    start = str(idx[0].date())
    for label, key in (("tbill (BIL)", "tbill"), ("bonds (IEF)", "bonds"),
                       ("gold (GLD)", "gold")):
        ticker = da.get(key)
        if not ticker:
            continue
        try:
            yield (label, 0.0, data.load_defensive_returns(ticker, start))
        except Exception as exc:                          # provider miss — skip, note it
            print(f"<!-- {label}: {exc!r} -->")


def _evaluate(region, prices, index_px, cash_yield, defensive_returns) -> dict:
    reg = replace(region, params=region.params.with_overrides(cash_yield=cash_yield))
    bt = run_backtest(prices, index_px, reg, defensive_returns=defensive_returns)
    m = bt["metrics"]
    bench_ret = index_px.pct_change(fill_method=None).reindex(bt["returns"].index)
    bs = benchmark_stats(bt["returns"], bench_ret)
    bench_m = compute_metrics(bench_ret.fillna(0.0),
                              (1 + bench_ret.fillna(0.0)).cumprod(),
                              currency=region.currency)
    sharpe = next((v for k, v in m.items() if k.startswith("Sharpe")), None)
    return {"CAGR": m["CAGR"], "MaxDD": m["MaxDrawdown"], "Sharpe": sharpe,
            "Bench": bench_m["CAGR"], "Active": bs.get("ActiveReturn"),
            "Alpha": bs.get("Alpha"), "Beta": bs.get("Beta")}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Defensive-sleeve sweep")
    ap.add_argument("--region", default="US", choices=["ASX", "US", "FTSE"])
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    region = get_region(args.region)
    if args.synthetic:
        prices, index_px = data.synthetic_region(region)
    else:
        prices, index_px = data.load_region(region, cfg.START, None)

    rows = []
    for label, cy, defr in _options(region, prices, index_px, args.synthetic):
        try:
            res = _evaluate(region, prices, index_px, cy, defr)
        except Exception as exc:
            res = {"CAGR": None, "Active": None, "err": repr(exc)}
        rows.append({"defensive": label, **res})

    rows.sort(key=lambda r: (r["Active"] if r.get("Active") is not None else -9),
              reverse=True)

    print(f"# Defensive-sleeve sweep — {region.name} ({region.currency}) "
          f"vs the index\n")
    if args.synthetic:
        print("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    else:
        print("Idle/risk-off capital rotated into each option; benchmark = index "
              "buy-and-hold. Equities untouched, so no extra equity-crash risk.\n")
    print("| defensive | CAGR | Bench | **Active** | Alpha | Beta | Sharpe | MaxDD |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("Active") is None:
            print(f"| {r['defensive']} | — | — | ERR | — | — | — | — |")
            continue
        print(f"| {r['defensive']} | {r['CAGR']:.1%} | {r['Bench']:.1%} | "
              f"**{r['Active']:+.1%}** | {r['Alpha']:+.1%} | {r['Beta']} | "
              f"{r['Sharpe']} | {r['MaxDD']:.1%} |")

    best = rows[0]
    if best.get("Active") is not None:
        print(f"\n**Best: {best['defensive']} — active {best['Active']:+.1%}** "
              f"(index CAGR {best['Bench']:.1%}, MaxDD {best['MaxDD']:.1%}).")
        print(f"\nGoal (beat index by ≥ +2.0%): "
              f"{'✅ MET' if best['Active'] >= 0.02 else '❌ not yet'}")


if __name__ == "__main__":
    main()
