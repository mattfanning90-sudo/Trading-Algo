"""Backtest validation report — the anti-self-deception panel.

Runs one sleeve and reports, in one place, the things that separate a real edge
from a curve-fit:

  • Trade statistics (win rate DONE RIGHT): profit factor, payoff, expectancy,
    breakeven win rate, Wilson CI, max consecutive losses, % time in market.
  • Overfitting controls: Probabilistic & Deflated Sharpe (the latter deflated by
    the number of parameter trials in the sweep) + PBO via CSCV.
  • Regime-conditional performance (no-lookahead bull/bear & vol terciles).
  • Stress test: stationary-bootstrap Monte-Carlo distribution of CAGR/Sharpe/
    MaxDD, drawdown analytics, and a transaction-cost sensitivity sweep.

    python -m trading_algo.validate --region US          # real data (network)
    python -m trading_algo.validate --region US --synthetic
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from . import config as cfg
from . import constituents, data, robust, stress, tradestats
from .backtest import run_backtest
from .metrics import compute_metrics
from .regions import get_region
from .sweep import DEFAULT_LOOKBACKS, DEFAULT_TOP_NS

_PPY = 252


def _sharpe(metrics: dict) -> float:
    return next((v for k, v in metrics.items() if k.startswith("Sharpe")), float("nan"))


def _grid_trials(region, prices, index_px, membership=None):
    """Run the (lookback × top_n) grid once, collecting each config's annualised
    Sharpe (for DSR) and monthly return series (for PBO's T×N matrix)."""
    sharpes, monthly = [], {}
    for lb in DEFAULT_LOOKBACKS:
        for tn in DEFAULT_TOP_NS:
            variant = replace(region, params=region.params.with_overrides(
                lookback_days=lb, top_n=tn))
            try:
                res = run_backtest(prices, index_px, variant, membership=membership)
            except Exception:
                continue
            sharpes.append(_sharpe(res["metrics"]))
            m = (1 + res["returns"]).resample("ME").prod() - 1.0
            monthly[f"{lb}_{tn}"] = m
    mat = pd.DataFrame(monthly).dropna(how="any")
    return sharpes, mat


def build_report(region_key: str, synthetic: bool, point_in_time: bool = False) -> str:
    region = get_region(region_key)
    membership = None
    pit_note = None
    if point_in_time:
        membership = (constituents.synthetic_membership(region) if synthetic
                      else constituents.get_membership(region))
        if membership is None:
            pit_note = ("⚠️ --point-in-time requested but **no real constituents file** is "
                        "configured for this region, so the survivorship-biased CURRENT "
                        "universe was used. A genuine survivorship fix needs a PIT "
                        "membership file *with delisted names* (e.g. Norgate / a CSV).")
        elif synthetic:
            pit_note = ("⚠️ Point-in-time uses SYNTHETIC membership (rotates today's names) — "
                        "it exercises the PIT machinery but does **not** remove survivorship "
                        "bias (the delisted graveyard is still missing).")
        else:
            pit_note = (f"Point-in-time membership: {len(membership)} snapshots, "
                        f"{len(membership.all_tickers)} names ever in the index.")
    pit_tickers = membership.all_tickers if membership is not None else None

    if synthetic:
        prices, index_px = data.synthetic_region(region)
    else:
        prices, index_px = data.load_region(region, cfg.START, None, tickers=pit_tickers)
        if membership is not None:
            # book a terminal loss for delisted names so losers don't vanish at
            # their last quote (survivorship correction's second half)
            prices = data.apply_delisting_returns(prices, set(region.universe))

    bt = run_backtest(prices, index_px, region, membership=membership)
    rets = bt["returns"]
    m = compute_metrics(rets, bt["equity"], currency=region.currency)

    ts = tradestats.trade_stats(rets, period="ME")
    tim = tradestats.time_in_market(bt["weights"])

    trial_sharpes, perf_mat = _grid_trials(region, prices, index_px, membership)
    psr = robust.probabilistic_sharpe_ratio(rets)
    mintrl = robust.min_track_record_length(rets)
    dsr = robust.deflated_sharpe_ratio(rets, trial_sharpes)
    pbo = robust.pbo_cscv(perf_mat, n_splits=8) if perf_mat.shape[0] >= 8 else {"pbo": float("nan")}

    regimes = stress.regime_conditional(rets, index_px)
    mc = stress.mc_summary(rets, n_paths=2000)
    dd = stress.drawdown_analytics(rets)
    costs = stress.cost_stress(bt)

    L = []
    w = L.append
    w(f"# Backtest validation — {region.name} ({region.currency})\n")
    if synthetic:
        w("> ⚠️ SYNTHETIC DATA — pipeline check only, numbers are meaningless.\n")
    if pit_note:
        w(f"> {pit_note}\n")
    n_obs = len(rets)
    w(f"Sample: {n_obs} days ({n_obs/_PPY:.1f}y). Headline CAGR {m['CAGR']:.1%}, "
      f"Sharpe {_sharpe(m):.2f}, MaxDD {m['MaxDrawdown']:.1%}.\n")

    w("## 1. Trade statistics — win rate in context (monthly bets)\n")
    if ts:
        lo, hi = ts["win_rate_95ci"]
        w(f"- **Win rate {ts['win_rate']:.0%}** (95% CI {lo:.0%}–{hi:.0%}, "
          f"n={ts['n_active']} active months; {ts['pct_flat']:.0%} of months in cash) "
          f"vs **breakeven {ts['breakeven_win_rate']:.0%}** → edge {ts['edge_vs_breakeven']:+.0%}")
        w(f"- Payoff (avg win/avg loss) **{ts['payoff_ratio']}**, profit factor "
          f"**{ts['profit_factor']}**, expectancy/mo **{ts['expectancy']:+.2%}**")
        w(f"- Max consecutive losing months **{ts['max_consec_losses']}**; worst month "
          f"{ts['worst_period']:+.1%}; % time in market **{tim:.0%}**")
        w(f"- Fractional (½) Kelly sizing ≈ **{ts['half_kelly']}** of capital\n")

    w("## 2. Overfitting controls\n")
    w(f"- Probabilistic Sharpe (P[true SR>0]) **{psr:.1%}**; "
      f"min track record for 95% confidence **{mintrl/_PPY:.1f}y** "
      f"(have {n_obs/_PPY:.1f}y) {'✅' if mintrl <= n_obs else '⚠️ too short'}")
    w(f"- **Deflated Sharpe {dsr['dsr']:.1%}** across N={dsr['n_trials']} sweep trials "
      f"(deflated benchmark SR₀={dsr['sr0_annual']}); "
      f"{'✅ survives selection' if (dsr['dsr'] or 0) >= 0.95 else '⚠️ not robust to multiple-testing'}")
    w(f"- Probability of Backtest Overfitting (PBO) **{pbo['pbo']:.0%}** "
      f"{'✅' if (pbo['pbo'] or 1) < 0.5 else '⚠️ selection ≈ coin-flip'}\n")

    w("## 3. Regime-conditional performance (no-lookahead)\n")
    w("| regime | share | CAGR | Sharpe |")
    w("|---|---|---|---|")
    for name in ("bull", "bear", "low_vol", "high_vol"):
        r = regimes[name]
        cg = f"{r['CAGR']:.1%}" if r["CAGR"] == r["CAGR"] else "n/a"
        sh = f"{r['Sharpe']:.2f}" if r["Sharpe"] == r["Sharpe"] else "n/a"
        w(f"| {name} | {r['share']:.0%} | {cg} | {sh} |")
    w("")

    w("## 4. Stress test — Monte Carlo (stationary bootstrap, 2000 paths)\n")
    if mc:
        w("| metric | P5 | P50 | P95 |")
        w("|---|---|---|---|")
        w(f"| CAGR | {mc['CAGR']['p5']:.1%} | {mc['CAGR']['p50']:.1%} | {mc['CAGR']['p95']:.1%} |")
        w(f"| Sharpe | {mc['Sharpe']['p5']:.2f} | {mc['Sharpe']['p50']:.2f} | {mc['Sharpe']['p95']:.2f} |")
        w(f"| MaxDD | {mc['MaxDD']['p5']:.1%} | {mc['MaxDD']['p50']:.1%} | {mc['MaxDD']['p95']:.1%} |")
        w(f"\n- Worst simulated drawdown **{mc['worst_MaxDD']:.1%}**; "
          f"P(MaxDD worse than 30%) **{mc['P(MaxDD>30%)']:.0%}**; "
          f"P(Sharpe<0) **{mc['P(Sharpe<0)']:.0%}**")
    w(f"- Drawdown: Ulcer {dd['ulcer_index']:.3f}, time underwater {dd['time_underwater_pct']:.0%}, "
      f"longest underwater {dd['longest_underwater_days']} days, "
      f"daily CVaR95 {dd['daily_CVaR95%']:.2%}")
    if costs:
        c = ", ".join(f"{k}: CAGR {v['CAGR']:.1%}/Sharpe {v['Sharpe']:.2f}" for k, v in costs.items())
        w(f"- Transaction-cost sensitivity — {c}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Backtest validation report")
    ap.add_argument("--region", default="US", choices=["ASX", "US", "FTSE"])
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--point-in-time", action="store_true",
                    help="restrict to point-in-time index members (needs a constituents file)")
    args = ap.parse_args(argv)
    print(build_report(args.region, args.synthetic, args.point_in_time))


if __name__ == "__main__":
    main()
