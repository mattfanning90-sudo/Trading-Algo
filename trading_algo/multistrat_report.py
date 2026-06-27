"""Multi-strategy model report — the upside-taker / downside-mitigator book.

Reads the available strategy return streams (equity cross-sectional momentum +
multi-asset trend + cross-asset carry), combines them by equal-risk-contribution
and vol-targets the whole book (multistrat.combine), and scores the result vs SPY
on the thing that matters for "upside taker + downside mitigator": upside/downside
CAPTURE and crisis-year behaviour.

    python -m trading_algo.multistrat_report                 # real data (network)
    python -m trading_algo.multistrat_report --synthetic     # offline harness
    python -m trading_algo.multistrat_report --validate      # + overfitting gauntlet
    python -m trading_algo.multistrat_report --point-in-time # de-bias the equity sleeve
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import config as cfg
from . import carry as carry_mod
from . import constituents, data, multistrat, robust, stress, tradestats, universes
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


def _build_streams(synthetic: bool, start: str, point_in_time: bool) -> tuple[dict, pd.Series, str]:
    """Return (streams, spy_price_series, pit_note)."""
    us = get_region("US")
    pit_note = ""

    if synthetic:
        eq_p, eq_i = data.synthetic_region(us)
        tr_p = data.synthetic_prices(universes.TREND, "DUMMY")[universes.TREND]
        ca_p = data.synthetic_prices(universes.CARRY, "DUMMY")[universes.CARRY]
        ca_y = data.synthetic_carry_yields(universes.CARRY)
        membership = None
    else:
        membership = constituents.get_membership(us) if point_in_time else None
        pit_tickers = membership.all_tickers if membership is not None else None
        eq_p, eq_i = data.load_region(us, start, None, tickers=pit_tickers)
        if membership is not None:
            eq_p = data.apply_delisting_returns(eq_p, set(us.universe))
            pit_note = (f"Equity sleeve de-biased: {len(membership)} PIT snapshots, "
                        f"{len(membership.all_tickers)} names ever in the index.")
        elif point_in_time:
            pit_note = ("⚠️ --point-in-time requested but NO constituents file is cached — "
                        "run `constituents.download_constituents('US')` and set TIINGO_API_KEY "
                        "for delisted prices. Falling back to the survivorship-biased universe.")
        tr_p = data.load_prices(universes.TREND, start, None)
        tr_p = tr_p[[t for t in universes.TREND if t in tr_p.columns]]
        ca_p = data.load_prices(universes.CARRY, start, None)
        ca_p = ca_p[[t for t in universes.CARRY if t in ca_p.columns]]
        ca_y = data.load_carry_yields(universes.CARRY, start, None)

    equity = run_backtest(eq_p, eq_i, us, membership=membership)["returns"]
    trend = run_trend_backtest(tr_p)["returns"]
    streams = {"equity_momentum": equity, "trend": trend}

    if not ca_y.empty and ca_p.shape[1] >= 3:
        try:
            streams["carry"] = carry_mod.run_carry_backtest(ca_p, ca_y)["returns"]
        except Exception:
            pit_note += "  (carry sleeve skipped: insufficient history)"
    else:
        pit_note += "  (carry sleeve skipped: yields unavailable)"

    spy = tr_p["SPY"] if "SPY" in tr_p else None
    return streams, spy, pit_note


def build_report(synthetic: bool, start: str = "2007-01-01", method: str = "erc",
                 do_validate: bool = False, point_in_time: bool = False) -> str:
    streams, spy, pit_note = _build_streams(synthetic, start, point_in_time)
    combo = multistrat.combine(streams, target_vol=0.12, method=method)
    cr = combo["returns"]
    common = cr.index
    bench = (spy.pct_change(fill_method=None).reindex(common).fillna(0.0)
             if spy is not None else pd.Series(0.0, index=common))

    L = []
    w = L.append
    w("# Multi-strategy model — upside taker + downside mitigator\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    if pit_note:
        w(f"> {pit_note}\n")
    span = f"{common[0].date()} → {common[-1].date()}" if len(common) else "n/a"
    names = " + ".join(streams.keys())
    w(f"Streams: **{names}**, combined by **{method.upper()}** at 12% vol target. "
      f"History {span} (USD).\n")

    rows = {k: v.reindex(common).fillna(0.0) for k, v in streams.items()}
    rows["MULTI-STRAT (combined)"] = cr
    rows["SPY (buy & hold)"] = bench
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
    cols = list(rows.keys())
    hdr = [c.replace(" (buy & hold)", "").replace(" (combined)", "") for c in cols]
    w("| year | " + " | ".join(hdr) + " |")
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
    if not point_in_time:
        w("- NB: equity sleeve is survivorship-biased (current US names) so its return "
          "is overstated; the drawdown/capture *shape* comes from trend + carry + vol-"
          "targeting and survives de-biasing. Re-run with `--point-in-time` (needs "
          "constituents cache + TIINGO_API_KEY) for the honest return level.")

    if do_validate:
        w("\n" + _validation_section(streams, bench, spy))
    return "\n".join(L)


def _validation_section(streams: dict, bench: pd.Series, spy: pd.Series) -> str:
    """The overfitting/robustness gauntlet, run on the COMBINED book — the same
    panel `validate.py` runs on a single sleeve, adapted to the multi-strat."""
    v = multistrat.validate_combo(streams, target_vol=0.12, base_method="erc")
    rets, sharpes, mat = v["base"], v["trial_sharpes"], v["perf_matrix"]
    n = len(rets)

    ts = tradestats.trade_stats(rets, period="ME")
    psr = robust.probabilistic_sharpe_ratio(rets)
    mintrl = robust.min_track_record_length(rets)
    dsr = robust.deflated_sharpe_ratio(rets, sharpes)
    pbo = robust.pbo_cscv(mat, n_splits=8) if mat.shape[0] >= 8 else {"pbo": float("nan")}
    mc = stress.mc_summary(rets, n_paths=2000)
    dd = stress.drawdown_analytics(rets)
    regimes = stress.regime_conditional(rets, spy) if spy is not None else None

    L = ["## Validation gauntlet — combined book\n",
         f"Run on the ERC multi-strat returns ({n/_PPY:.1f}y), with the overfitting "
         f"tests deflated across the combiner's own {len(sharpes)} hyperparameter "
         f"trials (method × lookback × vol target).\n"]
    w = L.append
    if ts:
        lo, hi = ts["win_rate_95ci"]
        w(f"- **Win rate {ts['win_rate']:.0%}** (95% CI {lo:.0%}–{hi:.0%}) vs breakeven "
          f"{ts['breakeven_win_rate']:.0%} → edge {ts['edge_vs_breakeven']:+.0%}; "
          f"profit factor {ts['profit_factor']}, expectancy/mo {ts['expectancy']:+.2%}")
    w(f"- Probabilistic Sharpe (P[SR>0]) **{psr:.1%}**; min track record "
      f"**{mintrl/_PPY:.1f}y** (have {n/_PPY:.1f}y) "
      f"{'✅' if mintrl <= n else '⚠️ too short'}")
    w(f"- **Deflated Sharpe {dsr['dsr']:.1%}** across N={dsr['n_trials']} combiner trials "
      f"{'✅ survives selection' if (dsr['dsr'] or 0) >= 0.95 else '⚠️ not robust to multiple-testing'}")
    w(f"- PBO **{pbo['pbo']:.0%}** {'✅' if (pbo['pbo'] or 1) < 0.5 else '⚠️ selection ≈ coin-flip'}")
    if mc:
        w(f"- Monte-Carlo (2000 paths): CAGR P5 {mc['CAGR']['p5']:.1%} / P50 "
          f"{mc['CAGR']['p50']:.1%} / P95 {mc['CAGR']['p95']:.1%}; "
          f"P(Sharpe<0) {mc['P(Sharpe<0)']:.0%}; worst MaxDD {mc['worst_MaxDD']:.1%}")
    w(f"- Drawdown: Ulcer {dd['ulcer_index']:.3f}, time underwater "
      f"{dd['time_underwater_pct']:.0%}, daily CVaR95 {dd['daily_CVaR95%']:.2%}")
    if regimes:
        bull, bear = regimes["bull"], regimes["bear"]
        bu = f"{bull['Sharpe']:.2f}" if bull['Sharpe'] == bull['Sharpe'] else "n/a"
        be = f"{bear['Sharpe']:.2f}" if bear['Sharpe'] == bear['Sharpe'] else "n/a"
        w(f"- Regime Sharpe — bull **{bu}**, bear **{be}** (no-lookahead)")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-strategy model report")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--start", default="2007-01-01")
    ap.add_argument("--method", default="erc", choices=["erc", "invvol", "equal"])
    ap.add_argument("--validate", action="store_true",
                    help="append the overfitting/robustness gauntlet on the combined book")
    ap.add_argument("--point-in-time", action="store_true",
                    help="de-bias the equity sleeve (needs constituents cache + TIINGO_API_KEY)")
    args = ap.parse_args(argv)
    print(build_report(args.synthetic, args.start, args.method,
                       do_validate=args.validate, point_in_time=args.point_in_time))


if __name__ == "__main__":
    main()
