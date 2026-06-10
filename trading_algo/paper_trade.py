"""Multi-region paper-trading engine — no broker connection required.

Models three self-contained regional sub-books (FTSE / US / ASX). At init the
base-currency capital is split by ALLOCATIONS and converted to each sleeve's
local currency; thereafter each sleeve compounds in its own currency with
whole-share lots, the per-region fee schedule (commission floor + UK stamp duty)
and slippage. Combined equity is reported in the base currency via current FX.

State persists per account in paper_state_{account}.json. Each sleeve trades
only on the first run of a new month (mirroring the backtest's month-end signal
-> next-day execution).

Note vs the portfolio backtest: this sim funds each sleeve ONCE and lets
allocations drift (the realistic "fund each sub-account, run it" model). The
backtest trues allocations back to target each period — see CLAUDE.md.

Usage
-----
    python -m trading_algo.paper_trade --init --capital 100000   # open account
    python -m trading_algo.paper_trade                           # daily run
    python -m trading_algo.paper_trade --status                  # report
    python -m trading_algo.paper_trade --force-rebalance         # rebalance now
    python -m trading_algo.paper_trade --compare micro full
    (append --synthetic to run fully offline)
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from . import config as cfg
from . import data, fees, fx, strategy
from .regions import get_region

STATE_DIR = os.path.join(os.path.dirname(__file__), "..")
MICRO_THRESHOLD = 5_000.0     # below this (local ccy) a sleeve concentrates


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _state_file(account: str) -> str:
    return os.path.join(STATE_DIR, f"paper_state_{account}.json")


def load_state(account: str) -> dict:
    path = _state_file(account)
    if not os.path.exists(path):
        raise SystemExit(f"No account '{account}'. Run --init --capital <amt> first.")
    with open(path) as f:
        return json.load(f)


def save_state(account: str, state: dict) -> None:
    with open(_state_file(account), "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Data / FX helpers
# ---------------------------------------------------------------------------
def _regions() -> list[str]:
    return list(cfg.ALLOCATIONS)


def fx_snapshot(synthetic: bool) -> dict[str, float]:
    currencies = [get_region(k).currency for k in _regions()]
    if synthetic:
        tbl = fx.synthetic_fx(currencies, base=cfg.BASE_CURRENCY)
    else:
        tbl = fx.load_fx(currencies, cfg.START, base=cfg.BASE_CURRENCY, use_cache=False)
    return {c: float(tbl[c].iloc[-1]) for c in currencies}


def latest_region_data(region, synthetic: bool):
    if synthetic:
        return data.synthetic_region(region)
    return data.load_region(region, cfg.START, use_cache=False)


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------
def sleeve_equity_local(sleeve: dict, px: pd.Series) -> float:
    holdings = 0.0
    for t, sh in sleeve["positions"].items():
        price = px.get(t)
        if price is not None and price == price:  # not NaN
            holdings += sh * float(price)
    return sleeve["cash"] + holdings


def init_account(account: str, capital: float, synthetic: bool) -> None:
    snap = fx_snapshot(synthetic)
    total = sum(cfg.ALLOCATIONS[k] for k in _regions())
    sleeves = {}
    for k in _regions():
        region = get_region(k)
        alloc = cfg.ALLOCATIONS[k] / total
        base_amount = capital * alloc
        local_cash = base_amount / snap[region.currency]
        sleeves[k] = {
            "currency": region.currency,
            "cash": local_cash,
            "positions": {},
            "last_rebalance_month": None,
        }
    state = {
        "account": account,
        "base_currency": cfg.BASE_CURRENCY,
        "initial_capital_base": capital,
        "allocations": {k: cfg.ALLOCATIONS[k] / total for k in _regions()},
        "sleeves": sleeves,
        "trades": [],
        "equity_history": [],
        "sleeve_history": [],
        "fx_snapshot": snap,
    }
    save_state(account, state)
    print(f"Paper account '{account}' opened with {capital:,.0f} {cfg.BASE_CURRENCY}")
    for k in _regions():
        s = sleeves[k]
        print(f"  {k:<5} funded {s['cash']:>12,.2f} {s['currency']}")


# ---------------------------------------------------------------------------
# Rebalancing one sleeve
# ---------------------------------------------------------------------------
def rebalance_sleeve(region, sleeve: dict, targets: pd.Series, px: pd.Series,
                     today: str, trade_log: list) -> None:
    equity = sleeve_equity_local(sleeve, px)
    print(f"\n  [{region.key}] rebalancing — equity {equity:,.0f} {region.currency}")

    # Micro-account mode: too small to hold the full book in whole shares.
    if equity < MICRO_THRESHOLD and not targets.empty:
        affordable = [t for t in targets.index
                      if px.get(t) and px[t] <= equity / 1.05]
        picks = affordable[:max(1, min(3, int(equity // 40)))] if affordable else []
        if picks:
            targets = pd.Series(0.97 / len(picks), index=picks)
            print(f"    ⚠ micro mode: concentrating into {picks}")
        else:
            targets = pd.Series(dtype=float)
            print("    ⚠ no affordable names — staying in cash")

    dust = min(200.0, equity * 0.05)
    desired = {}
    for t, w in targets.items():
        price = px.get(t)
        if price and price == price and price > 0:
            desired[t] = int((equity * w) / price)

    for t in sorted(set(sleeve["positions"]) | set(desired)):
        cur = sleeve["positions"].get(t, 0)
        tgt = desired.get(t, 0)
        delta = tgt - cur
        if delta == 0:
            continue
        price = px.get(t)
        if not price or price != price:
            continue
        fill = price * (1 + np.sign(delta) * region.slippage_bps / 1e4)
        notional = abs(delta) * fill
        if notional < dust and tgt != 0:   # skip dust adjustments, allow full exits
            continue
        fee = fees.commission(region, notional)
        duty = fees.stamp_duty(region, notional) if delta > 0 else 0.0
        sleeve["cash"] -= delta * fill + fee + duty
        if tgt == 0:
            sleeve["positions"].pop(t, None)
        else:
            sleeve["positions"][t] = tgt
        side = "BUY" if delta > 0 else "SELL"
        trade_log.append({"date": today, "region": region.key, "ticker": t,
                          "side": side, "shares": abs(delta),
                          "fill": round(fill, 4), "commission": round(fee, 2),
                          "stamp_duty": round(duty, 2), "currency": region.currency})
        extra = f" duty {duty:.2f}" if duty else ""
        print(f"    {side:<4} {abs(delta):>7} {t:<10} @ {fill:>10.3f}  "
              f"(fee {fee:.2f}{extra} {region.currency})")


# ---------------------------------------------------------------------------
# Daily run
# ---------------------------------------------------------------------------
def run_daily(account: str, synthetic: bool) -> None:
    state = load_state(account)
    snap = fx_snapshot(synthetic)
    state["fx_snapshot"] = snap

    report_date = ""
    combined = 0.0
    breakdown = {}
    for k in _regions():
        region = get_region(k)
        prices, index_px = latest_region_data(region, synthetic)
        px_today = prices.iloc[-1]
        today = prices.index[-1].strftime("%Y-%m-%d")
        report_date = max(report_date, today)
        sleeve = state["sleeves"][k]
        this_month = today[:7]

        if sleeve["last_rebalance_month"] != this_month:
            targets = strategy.compute_targets(prices, index_px, region.params)
            if targets.empty:
                print(f"  [{k}] regime RISK-OFF — moving/holding cash.")
            rebalance_sleeve(region, sleeve, targets, px_today, today, state["trades"])
            sleeve["last_rebalance_month"] = this_month

        eq_local = sleeve_equity_local(sleeve, px_today)
        eq_base = eq_local * snap[region.currency]
        breakdown[k] = (eq_local, region.currency, eq_base)
        combined += eq_base

    if not state["equity_history"] or state["equity_history"][-1][0] != report_date:
        state["equity_history"].append([report_date, round(combined, 2)])
        sleeve_row = {"date": report_date}
        sleeve_row.update({k: round(v[2], 2) for k, v in breakdown.items()})
        state.setdefault("sleeve_history", []).append(sleeve_row)
    save_state(account, state)

    pnl = combined / state["initial_capital_base"] - 1
    print(f"\n{report_date}  combined {combined:,.0f} {cfg.BASE_CURRENCY} "
          f"({pnl:+.2%} since inception)")
    for k, (loc, ccy, base) in breakdown.items():
        print(f"    {k:<5} {loc:>12,.0f} {ccy}  ->  {base:>12,.0f} {cfg.BASE_CURRENCY}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def status(account: str) -> None:
    state = load_state(account)
    eq = pd.DataFrame(state["equity_history"], columns=["date", "equity"])
    print("=" * 52)
    print(f"  Paper Account '{account}'  (base {state['base_currency']})")
    print("=" * 52)
    if eq.empty:
        print("  No history yet — run a daily update first.")
    else:
        eq["date"] = pd.to_datetime(eq["date"])
        s = eq.set_index("date")["equity"]
        rets = s.pct_change(fill_method=None).dropna()
        print(f"  Inception        {s.index[0].date()}  "
              f"({state['initial_capital_base']:,.0f} {state['base_currency']})")
        print(f"  Current equity   {s.iloc[-1]:,.2f} {state['base_currency']}")
        print(f"  Total return     {s.iloc[-1] / state['initial_capital_base'] - 1:+.2%}")
        if len(rets) > 20:
            print(f"  Ann. vol         {rets.std() * np.sqrt(252):.1%}")
            print(f"  Max drawdown     {(s / s.cummax() - 1).min():.2%}")
        print(f"  Trades to date   {len(state['trades'])}")

    fees_by_ccy: dict[str, float] = {}
    for t in state["trades"]:
        fees_by_ccy[t["currency"]] = fees_by_ccy.get(t["currency"], 0.0) \
            + t["commission"] + t.get("stamp_duty", 0.0)
    if fees_by_ccy:
        print("  Fees paid        " + ", ".join(f"{v:,.2f} {c}"
                                                 for c, v in fees_by_ccy.items()))
    print("\n  Holdings by sleeve:")
    for k in _regions():
        sleeve = state["sleeves"][k]
        print(f"    [{k}] cash {sleeve['cash']:,.2f} {sleeve['currency']}")
        for t, sh in sorted(sleeve["positions"].items()):
            print(f"        {t:<10} {sh:>8} shares")
        if not sleeve["positions"]:
            print("        (all cash)")


def compare(accounts: list[str]) -> None:
    print(f"{'Account':<10} {'Capital':>14} {'Equity':>14} {'Return':>9} {'Trades':>7}")
    for name in accounts:
        if not os.path.exists(_state_file(name)):
            continue
        s = load_state(name)
        eq = s["equity_history"][-1][1] if s["equity_history"] else s["initial_capital_base"]
        print(f"{name:<10} {s['initial_capital_base']:>14,.0f} {eq:>14,.2f} "
              f"{eq / s['initial_capital_base'] - 1:>+8.2%} {len(s['trades']):>7}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-region momentum paper trader")
    ap.add_argument("--account", default="main", help="account name (separate state per name)")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--capital", type=float, default=cfg.INITIAL_CAPITAL)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force-rebalance", action="store_true")
    ap.add_argument("--compare", nargs="+", metavar="ACCT")
    ap.add_argument("--synthetic", action="store_true", help="run offline on synthetic data")
    args = ap.parse_args(argv)

    if args.compare:
        compare(args.compare)
    elif args.init:
        init_account(args.account, args.capital, args.synthetic)
    elif args.status:
        status(args.account)
    elif args.force_rebalance:
        state = load_state(args.account)
        for s in state["sleeves"].values():
            s["last_rebalance_month"] = None
        save_state(args.account, state)
        run_daily(args.account, args.synthetic)
    else:
        run_daily(args.account, args.synthetic)


if __name__ == "__main__":
    main()
