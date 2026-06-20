"""Does adding a trend sleeve to the equity-momentum book actually help?

Runs three return streams over their common history, all in USD (no FX, so it's
apples-to-apples):

  1. US equity cross-sectional momentum sleeve  (the existing strategy)
  2. Trend / multi-asset time-series momentum    (the new diversifier)
  3. Blends of the two                            (e.g. 70/30, 50/50)

…and reports CAGR / vol / Sharpe / max-drawdown for each, the correlation between
the two sleeves (the whole point), and their returns in the crisis years
(2008 / 2020 / 2022) where trend is supposed to earn its keep. Benchmark = SPY
buy-and-hold. The honest test: does the blend beat SPY *risk-adjusted* and cut
the drawdown, even though trend standalone is modest?

    python -m trading_algo.trend_report            # real data (needs network)
    python -m trading_algo.trend_report --synthetic # offline harness check
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import config as cfg
from . import data
from . import universes
from .backtest import run_backtest
from .regions import get_region
from .trend import run_trend_backtest

CRISIS_YEARS = [2008, 2020, 2022]


def _stats(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) < 2:
        return {"CAGR": float("nan"), "Vol": float("nan"),
                "Sharpe": float("nan"), "MaxDD": float("nan")}
    eq = (1 + ret).cumprod()
    cagr = eq.iloc[-1] ** (252 / len(ret)) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252 - cfg.RISK_FREE) / max(vol, 1e-9)
    maxdd = float((eq / eq.cummax() - 1).min())
    return {"CAGR": float(cagr), "Vol": float(vol), "Sharpe": float(sharpe), "MaxDD": maxdd}


def _annual(ret: pd.Series) -> pd.Series:
    return (1 + ret.dropna()).resample("YE").prod() - 1


def _load(synthetic: bool, start: str):
    us = get_region("US")
    if synthetic:
        eq_prices, eq_index = data.synthetic_region(us)
        raw = data.synthetic_prices(universes.TREND, "DUMMYIDX")
        tr_prices = raw[universes.TREND]
    else:
        eq_prices, eq_index = data.load_region(us, start, None)
        tr_prices = data.load_prices(universes.TREND, start, None)
        tr_prices = tr_prices[[t for t in universes.TREND if t in tr_prices.columns]]
    return eq_prices, eq_index, tr_prices


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Trend-diversifier report")
    ap.add_argument("--synthetic", action="store_true")
    # Default to 2007 so the GFC (trend-following's best-ever year) is INCLUDED —
    # starting at the usual 2012 would exclude it and sit on trend's lost decade,
    # an unfairly hostile window for evaluating the diversifier.
    ap.add_argument("--start", default="2007-01-01", help="history start (real mode)")
    args = ap.parse_args(argv)

    eq_prices, eq_index, tr_prices = _load(args.synthetic, args.start)

    eq_bt = run_backtest(eq_prices, eq_index, get_region("US"))
    tr_bt = run_trend_backtest(tr_prices)

    eq_ret = eq_bt["returns"]
    tr_ret = tr_bt["returns"]
    spy_ret = tr_prices["SPY"].pct_change(fill_method=None) if "SPY" in tr_prices else None

    # Common dates across both sleeves (and SPY).
    common = eq_ret.index.intersection(tr_ret.index)
    eq_ret, tr_ret = eq_ret.reindex(common).fillna(0.0), tr_ret.reindex(common).fillna(0.0)
    bench = (spy_ret.reindex(common).fillna(0.0) if spy_ret is not None
             else pd.Series(0.0, index=common))

    blends = {"100% equity": (1.0, 0.0), "70/30": (0.7, 0.3),
              "50/50": (0.5, 0.5), "100% trend": (0.0, 1.0)}
    streams = {name: we * eq_ret + wt * tr_ret for name, (we, wt) in blends.items()}
    streams["SPY (buy & hold)"] = bench

    corr = float(eq_ret.corr(tr_ret))

    print("# Trend diversifier — does adding it to the equity book help?\n")
    if args.synthetic:
        print("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    span = f"{common[0].date()} → {common[-1].date()}" if len(common) else "n/a"
    print(f"Common history: **{span}**, all in USD. "
          f"Trend avg gross exposure: {tr_bt['avg_gross_exposure']:.2f}× "
          f"(long+short; >1 needs futures/margin).\n")
    print(f"**Correlation(equity momentum, trend) = {corr:+.2f}** "
          f"— low/negative is exactly what makes trend a diversifier.\n")

    print("| stream | CAGR | Vol | Sharpe | MaxDD |")
    print("|---|---|---|---|---|")
    for name, r in streams.items():
        s = _stats(r)
        print(f"| {name} | {s['CAGR']:.1%} | {s['Vol']:.1%} | "
              f"{s['Sharpe']:.2f} | {s['MaxDD']:.1%} |")

    # Crisis-year returns — where trend is supposed to earn its keep.
    print("\n## Crisis-year returns (where trend should help)\n")
    cols = ["100% equity", "100% trend", "70/30", "SPY (buy & hold)"]
    print("| year | " + " | ".join(cols) + " |")
    print("|---" * (len(cols) + 1) + "|")
    annuals = {c: _annual(streams[c]) for c in cols}
    for yr in CRISIS_YEARS:
        cells = []
        for c in cols:
            a = annuals[c]
            match = a[a.index.year == yr]
            cells.append(f"{match.iloc[0]:+.1%}" if len(match) else "n/a")
        print(f"| {yr} | " + " | ".join(cells) + " |")

    # Honest verdict
    base, blend = _stats(streams["100% equity"]), _stats(streams["70/30"])
    spy = _stats(streams["SPY (buy & hold)"])
    print("\n## Honest read\n")
    print(f"- Equity-only Sharpe {base['Sharpe']:.2f} (MaxDD {base['MaxDD']:.1%}) → "
          f"70/30 blend Sharpe {blend['Sharpe']:.2f} (MaxDD {blend['MaxDD']:.1%}).")
    print(f"- SPY buy & hold: CAGR {spy['CAGR']:.1%}, Sharpe {spy['Sharpe']:.2f}, "
          f"MaxDD {spy['MaxDD']:.1%}.")
    better_sharpe = blend["Sharpe"] > base["Sharpe"]
    better_dd = blend["MaxDD"] > base["MaxDD"]   # less negative = better
    print(f"- Adding trend {'improved' if better_sharpe else 'did NOT improve'} "
          f"risk-adjusted return and {'reduced' if better_dd else 'did NOT reduce'} "
          f"the worst drawdown.")


if __name__ == "__main__":
    main()
