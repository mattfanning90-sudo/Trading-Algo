"""Multi-strategy model report — the upside-taker / downside-mitigator book.

Reads the available strategy return streams (equity cross-sectional momentum +
multi-asset trend; carry slots in here later), combines them by equal-risk-
contribution and vol-targets the whole book (multistrat.combine), and scores the
result vs SPY on the thing that matters for "upside taker + downside mitigator":
upside/downside CAPTURE and crisis-year behaviour.

    python -m trading_algo.multistrat_report                 # real data (network)
    python -m trading_algo.multistrat_report --synthetic     # offline harness
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import config as cfg
from . import data, multistrat, universes
from .backtest import run_backtest
from .regions import get_region
from .trend import run_trend_backtest

CRISIS_YEARS = [2008, 2020, 2022]
_PPY = 252


def _stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 2:
        return {k: float("nan") for k in ("CAGR", "Vol", "Sharpe", "MaxDD")}
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (_PPY / len(r)) - 1
    vol = r.std() * np.sqrt(_PPY)
    dd = float((eq / eq.cummax() - 1).min())
    return {"CAGR": float(cagr), "Vol": float(vol),
            "Sharpe": float((r.mean() * _PPY - cfg.RISK_FREE) / max(vol, 1e-9)),
            "MaxDD": dd}


def _annual(r: pd.Series) -> pd.Series:
    return (1 + r.dropna()).resample("YE").prod() - 1


def build_report(synthetic: bool, start: str = "2007-01-01", method: str = "erc") -> str:
    us = get_region("US")
    if synthetic:
        eq_p, eq_i = data.synthetic_region(us)
        tr_p = data.synthetic_prices(universes.TREND, "DUMMY")[universes.TREND]
    else:
        eq_p, eq_i = data.load_region(us, start, None)
        tr_p = data.load_prices(universes.TREND, start, None)
        tr_p = tr_p[[t for t in universes.TREND if t in tr_p.columns]]

    equity = run_backtest(eq_p, eq_i, us)["returns"]            # upside taker
    trend = run_trend_backtest(tr_p)["returns"]                 # downside mitigator
    spy = tr_p["SPY"].pct_change(fill_method=None) if "SPY" in tr_p else None

    streams = {"equity_momentum": equity, "trend": trend}
    combo = multistrat.combine(streams, target_vol=0.12, method=method)
    cr = combo["returns"]

    common = cr.index
    bench = (spy.reindex(common).fillna(0.0) if spy is not None
             else pd.Series(0.0, index=common))

    L = []
    w = L.append
    w("# Multi-strategy model — upside taker + downside mitigator\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    span = f"{common[0].date()} → {common[-1].date()}" if len(common) else "n/a"
    w(f"Streams: equity momentum (upside) + trend (downside hedge), combined by "
      f"**{method.upper()}** at 12% vol target. History {span} (USD).\n")

    rows = {"equity_momentum": equity.reindex(common).fillna(0.0),
            "trend": trend.reindex(common).fillna(0.0),
            "MULTI-STRAT (combined)": cr,
            "SPY (buy & hold)": bench}
    w("| stream | CAGR | Vol | Sharpe | MaxDD |")
    w("|---|---|---|---|---|")
    for name, r in rows.items():
        s = _stats(r)
        w(f"| {name} | {s['CAGR']:.1%} | {s['Vol']:.1%} | {s['Sharpe']:.2f} | {s['MaxDD']:.1%} |")

    cap = multistrat.capture_ratios(cr, bench)
    if cap:
        w(f"\n**Upside/downside capture vs SPY** — up-capture **{cap['up_capture']}**, "
          f"down-capture **{cap['down_capture']}**, ratio **{cap['capture_ratio']}** "
          f"(>1 = takes upside while mitigating downside).\n")

    w("## Crisis-year returns\n")
    cols = ["equity_momentum", "trend", "MULTI-STRAT (combined)", "SPY (buy & hold)"]
    w("| year | " + " | ".join(c.replace(" (buy & hold)", "").replace(" (combined)", "") for c in cols) + " |")
    w("|---" * (len(cols) + 1) + "|")
    ann = {c: _annual(rows[c]) for c in cols}
    for yr in CRISIS_YEARS:
        cells = []
        for c in cols:
            m = ann[c][ann[c].index.year == yr]
            cells.append(f"{m.iloc[0]:+.1%}" if len(m) else "n/a")
        w(f"| {yr} | " + " | ".join(cells) + " |")

    base, combo_s, spy_s = _stats(rows["equity_momentum"]), _stats(cr), _stats(bench)
    w("\n## Honest read\n")
    w(f"- Equity-only Sharpe {base['Sharpe']:.2f} (MaxDD {base['MaxDD']:.1%}) → "
      f"multi-strat Sharpe {combo_s['Sharpe']:.2f} (MaxDD {combo_s['MaxDD']:.1%}).")
    w(f"- SPY: CAGR {spy_s['CAGR']:.1%}, Sharpe {spy_s['Sharpe']:.2f}, MaxDD {spy_s['MaxDD']:.1%}.")
    better = combo_s["Sharpe"] > base["Sharpe"] and combo_s["MaxDD"] > base["MaxDD"]
    w(f"- Combining {'improved BOTH risk-adjusted return and drawdown' if better else 'shifted the risk/return tradeoff'} "
      f"vs the equity sleeve alone. NB: equity sleeve here is survivorship-biased "
      f"(current US names); trend is not. Validate the combined book with "
      f"`validate` before trusting it.")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-strategy model report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--start", default="2007-01-01")
    ap.add_argument("--method", default="erc", choices=["erc", "invvol", "equal"])
    args = ap.parse_args(argv)
    print(build_report(args.synthetic, args.start, args.method))


if __name__ == "__main__":
    main()
