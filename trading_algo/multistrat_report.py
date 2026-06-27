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
from dataclasses import replace

import numpy as np
import pandas as pd

from . import config as cfg
from . import carry as carry_mod
from . import lowrisk as lowrisk_mod
from . import fx as fx_mod
from . import constituents, data, multistrat, robust, stress, tradestats, universes
from .backtest import run_backtest
from .regions import get_region
from .trend import run_trend_backtest

GLOBAL_EQUITY_REGIONS = ("US", "ASX", "FTSE")

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


def _to_base_returns(returns: pd.Series, from_ccy: str, base: str,
                     synthetic: bool, start: str) -> pd.Series:
    """Convert a local-currency return stream into the base currency, incl. FX P&L.

    base_return = (1+local_return)·(1+fx_return) − 1, where fx_return is the daily
    move of the (base-per-local) multiplier. For an AUD-based investor this is what
    actually lands in the account: unhedged foreign assets carry their FX move."""
    if from_ccy == base:
        return returns
    fx = (fx_mod.synthetic_fx([from_ccy], base=base) if synthetic
          else fx_mod.load_fx([from_ccy], start, None, base=base))
    m = fx_mod.align_fx(fx, returns.index, from_ccy)
    rm = m.pct_change(fill_method=None).fillna(0.0)
    return (1.0 + returns) * (1.0 + rm) - 1.0


def _region_returns_base(region_key: str, base: str, synthetic: bool, start: str) -> pd.Series:
    """One regional momentum sleeve's daily returns in the BASE currency (incl. FX).

    Each sleeve trades in its LOCAL currency (invariant #6); we convert the local
    equity curve via the FX multiplier and take returns — currency-consistent."""
    reg = get_region(region_key)
    if synthetic:
        p, i = data.synthetic_region(reg)
    else:
        p, i = data.load_region(reg, start, None)
    eq = run_backtest(p, i, reg)["equity"]            # local-currency equity curve
    if reg.currency == base:
        return eq.pct_change(fill_method=None)
    fx = (fx_mod.synthetic_fx([reg.currency], base=base) if synthetic
          else fx_mod.load_fx([reg.currency], start, None, base=base))
    m = fx_mod.align_fx(fx, eq.index, reg.currency)
    return (eq * m).pct_change(fill_method=None)       # base return incl. FX move


def _build_streams(synthetic: bool, start: str, point_in_time: bool,
                   include_carry: bool = False, global_equity: bool = False,
                   base: str = cfg.BASE_CURRENCY,
                   include_value: bool = False,
                   include_lowrisk: bool = False) -> tuple[dict, pd.Series, str]:
    """Return (streams, spy_price_series, pit_note).

    Carry is OFF by default: on real data the price-only income-yield carry proxy
    behaved as a long-credit-risk bet (negative in 2008/2020/2022) and *dragged
    the combined book down* — Sharpe 0.48→0.16, capture 3.68→1.33. Until it's
    rebuilt with proper term-structure/roll data (or dollar-neutralised within
    asset class), the headline book is equity + trend. Pass include_carry=True to
    add it back for research."""
    us = get_region("US")
    pit_note = ""

    if synthetic:
        eq_p, eq_i = data.synthetic_region(us)
        tr_p = data.synthetic_prices(universes.TREND, "DUMMY")[universes.TREND]
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

    if global_equity:
        # globally-diversified equity TAKER: equal-third US/ASX/FTSE (matching
        # config.ALLOCATIONS), each converted to the BASE currency incl. FX P&L.
        regional = [_region_returns_base(k, base, synthetic, start) for k in GLOBAL_EQUITY_REGIONS]
        equity = pd.concat(regional, axis=1).mean(axis=1).dropna()
        pit_note += f"  (equity = equal-third {'+'.join(GLOBAL_EQUITY_REGIONS)}, →{base})"
    else:
        # US sleeve in USD → base currency (unhedged FX, as an investor actually holds)
        equity = run_backtest(eq_p, eq_i, us, membership=membership)["returns"]
        equity = _to_base_returns(equity, us.currency, base, synthetic, start)
    trend = _to_base_returns(run_trend_backtest(tr_p)["returns"], "USD", base, synthetic, start)
    streams = {"equity_momentum": equity, "trend": trend}

    if include_value:
        # VALUE sleeve: pure long-term reversal (use_value, momentum_weight=0) on the
        # SAME US universe + de-biasing path. Value and momentum are the canonical
        # negatively-correlated pair, so this is a genuinely new premium — not more
        # momentum. Same currency conversion as the momentum sleeve.
        val_region = replace(us, params=us.params.with_overrides(
            use_value=True, momentum_weight=0.0, value_weight=1.0))
        val = run_backtest(eq_p, eq_i, val_region, membership=membership)["returns"]
        streams["equity_value"] = _to_base_returns(val, us.currency, base, synthetic, start)

    if include_lowrisk:
        # BAB / low-risk sleeve: sorts on beta (a risk characteristic), so it's
        # orthogonal to the return-based sleeves. Same US universe + de-biasing path.
        try:
            lr = lowrisk_mod.run_lowrisk_backtest(eq_p, eq_i)["returns"]
            streams["lowrisk_bab"] = _to_base_returns(lr, us.currency, base, synthetic, start)
        except Exception:
            pit_note += "  (low-risk sleeve skipped: insufficient history)"

    if include_carry:
        if synthetic:
            ca_p = data.synthetic_prices(universes.CARRY, "DUMMY")[universes.CARRY]
            ca_y = data.synthetic_carry_yields(universes.CARRY)
        else:
            ca_p = data.load_prices(universes.CARRY, start, None)
            ca_p = ca_p[[t for t in universes.CARRY if t in ca_p.columns]]
            ca_y = data.load_carry_yields(universes.CARRY, start, None)
        if not ca_y.empty and ca_p.shape[1] >= 3:
            try:
                ca_r = carry_mod.run_carry_backtest(ca_p, ca_y)["returns"]
                streams["carry"] = _to_base_returns(ca_r, "USD", base, synthetic, start)
            except Exception:
                pit_note += "  (carry sleeve skipped: insufficient history)"
        else:
            pit_note += "  (carry sleeve skipped: yields unavailable)"

    spy = tr_p["SPY"] if "SPY" in tr_p else None
    return streams, spy, pit_note


def build_report(synthetic: bool, start: str = "2007-01-01", method: str = "erc",
                 do_validate: bool = False, point_in_time: bool = False,
                 include_carry: bool = False, target_vol: float = 0.12,
                 max_leverage: float = 1.5, drawdown_stop: float | None = None,
                 global_equity: bool = False, base: str = cfg.BASE_CURRENCY,
                 include_value: bool = False, include_lowrisk: bool = False) -> str:
    # Financing on the leveraged portion is ALWAYS charged (leverage isn't free).
    # The reactive drawdown stop is OFF by default: real-data evidence showed it
    # WHIPSAWS a levered book (cuts at the 2020/2022 bottoms, misses the V-recovery
    # → deeper drawdown AND lower return). Risk is controlled EX-ANTE here (vol
    # target + diversification + not over-levering), per the investment-council.
    # Pass --drawdown-stop to study it as an optional backstop.
    streams, spy, pit_note = _build_streams(synthetic, start, point_in_time,
                                            include_carry, global_equity, base,
                                            include_value=include_value,
                                            include_lowrisk=include_lowrisk)
    combo = multistrat.combine(streams, target_vol=target_vol, method=method,
                               max_leverage=max_leverage,
                               financing_spread=cfg.LEVERAGE_FINANCING_SPREAD,
                               drawdown_stop=drawdown_stop,
                               cooldown_days=cfg.DRAWDOWN_COOLDOWN_DAYS)
    cr = combo["returns"]
    common = cr.index
    # benchmark = SPY held UNHEDGED by a base-currency investor (USD return + FX)
    if spy is not None:
        bench = _to_base_returns(spy.pct_change(fill_method=None).fillna(0.0),
                                 "USD", base, synthetic, start).reindex(common).fillna(0.0)
    else:
        bench = pd.Series(0.0, index=common)

    L = []
    w = L.append
    w("# Multi-strategy model — upside taker + downside mitigator\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — harness check only, numbers are meaningless.\n")
    if pit_note:
        w(f"> {pit_note}\n")
    span = f"{common[0].date()} → {common[-1].date()}" if len(common) else "n/a"
    names = " + ".join(streams.keys())
    w(f"Streams: **{names}**, combined by **{method.upper()}** at {target_vol:.0%} vol "
      f"target. History {span} (base **{base}**, foreign sleeves unhedged).\n")

    rows = {k: v.reindex(common).fillna(0.0) for k, v in streams.items()}
    rows["MULTI-STRAT (combined)"] = cr
    rows[f"SPY in {base} (buy & hold)"] = bench
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

    eq_s, combo_s, spy_s = _stats(rows["equity_momentum"]), _stats(cr), _stats(bench)
    w("\n## Honest read\n")
    w(f"- Equity-only Sharpe {eq_s['Sharpe']:.2f} (MaxDD {eq_s['MaxDD']:.1%}) → "
      f"multi-strat Sharpe {combo_s['Sharpe']:.2f} (MaxDD {combo_s['MaxDD']:.1%}).")
    w(f"- SPY (in {base}): CAGR {spy_s['CAGR']:.1%}, Sharpe {spy_s['Sharpe']:.2f}, "
      f"MaxDD {spy_s['MaxDD']:.1%}.")
    diversifiers = " + ".join(k for k in streams if k != "equity_momentum")
    if not point_in_time:
        w(f"- NB: equity sleeve is survivorship-biased (current US names) so its return "
          f"is overstated; the drawdown/capture *shape* comes from {diversifiers} + vol-"
          f"targeting and survives de-biasing. Re-run with `--point-in-time` (needs "
          f"constituents cache + TIINGO_API_KEY) for the honest return level.")

    if max_leverage > 1.6:
        w("- ⚠️ Leverage risk: gross >1.6 means borrowing (financing charged here at "
          f"{cfg.LEVERAGE_FINANCING_SPREAD:.0%} over rf) and drawdowns scale up. A "
          "reactive drawdown stop WHIPSAWS a levered book (cuts at crash bottoms, "
          "misses the recovery) — control risk by NOT over-levering, not by a stop.")
    if do_validate:
        w("\n" + _validation_section(streams, bench, spy, target_vol, max_leverage,
                                     drawdown_stop))
    return "\n".join(L)


def _validation_section(streams: dict, bench: pd.Series, spy: pd.Series,
                        target_vol: float = 0.12, max_leverage: float = 1.5,
                        drawdown_stop: float | None = None) -> str:
    """The overfitting/robustness gauntlet, run on the COMBINED book — the same
    panel `validate.py` runs on a single sleeve, adapted to the multi-strat."""
    v = multistrat.validate_combo(streams, target_vol=target_vol, base_method="erc",
                                  max_leverage=max_leverage,
                                  financing_spread=cfg.LEVERAGE_FINANCING_SPREAD,
                                  drawdown_stop=drawdown_stop,
                                  cooldown_days=cfg.DRAWDOWN_COOLDOWN_DAYS)
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
    ap.add_argument("--with-carry", action="store_true",
                    help="add the carry sleeve (off by default — it dragged the book down on real data)")
    ap.add_argument("--target-vol", type=float, default=0.12,
                    help="annualised vol target for the combined book (the risk/return dial)")
    ap.add_argument("--max-leverage", type=float, default=1.5,
                    help="gross-exposure cap on the combined book (leverage needs margin + financing cost)")
    ap.add_argument("--drawdown-stop", type=float, default=0.0,
                    help="optional reactive drawdown circuit breaker (0=off; it whipsaws a levered book)")
    ap.add_argument("--global-equity", action="store_true",
                    help="diversify the equity taker across US+ASX+FTSE (equal-third, →base ccy)")
    ap.add_argument("--base-currency", default=cfg.BASE_CURRENCY,
                    help="reporting/base currency (default AUD); foreign sleeves are unhedged")
    ap.add_argument("--with-value", action="store_true",
                    help="add a value (long-term reversal) sleeve — uncorrelated to momentum")
    ap.add_argument("--with-lowrisk", action="store_true",
                    help="add a low-risk / betting-against-beta sleeve (risk-sorted, orthogonal)")
    args = ap.parse_args(argv)
    print(build_report(args.synthetic, args.start, args.method,
                       do_validate=args.validate, point_in_time=args.point_in_time,
                       include_carry=args.with_carry, target_vol=args.target_vol,
                       max_leverage=args.max_leverage,
                       drawdown_stop=(args.drawdown_stop or None),
                       global_equity=args.global_equity, base=args.base_currency,
                       include_value=args.with_value, include_lowrisk=args.with_lowrisk))


if __name__ == "__main__":
    main()
