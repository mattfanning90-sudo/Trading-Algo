"""Build the dashboard's JSON state snapshot from the persisted paper account.

Reads paper_state_{account}.json, marks every position to the latest available
price, converts each sleeve to the base currency, computes the regime per sleeve,
and assembles the contract the frontend consumes. Pure read — never mutates state.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .. import config as cfg
from .. import paper_trade, signals
from ..regions import get_region


def _safe_price(px, ticker: str) -> float:
    v = px.get(ticker)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0  # NaN -> 0


def build_snapshot(account: str, synthetic: bool = False) -> dict:
    """Assemble the full dashboard state for one account."""
    if not os.path.exists(paper_trade._state_file(account)):
        raise FileNotFoundError(f"no account '{account}'")

    state = paper_trade.load_state(account)
    snap_fx = paper_trade.fx_snapshot(synthetic)
    regions = list(cfg.ALLOCATIONS)

    sleeves_out, as_of = [], ""
    total_base = total_cash_base = 0.0
    n_positions = 0

    for k in regions:
        region = get_region(k)
        prices, index_px = paper_trade.latest_region_data(region, synthetic)
        px = prices.iloc[-1]
        as_of = max(as_of, prices.index[-1].strftime("%Y-%m-%d"))
        sleeve = state["sleeves"][k]
        m = snap_fx[region.currency]
        regime = ("RISK_ON"
                  if bool(signals.index_risk_on(index_px, region.params).iloc[-1])
                  else "RISK_OFF")

        invested_local = 0.0
        positions = []
        for t, sh in sleeve["positions"].items():
            price = _safe_price(px, t)
            val_local = sh * price
            invested_local += val_local
            positions.append({"ticker": t, "shares": int(sh),
                              "price": round(price, 4),
                              "value_local": round(val_local, 2)})
        cash_local = float(sleeve["cash"])
        eq_local = cash_local + invested_local
        eq_base = eq_local * m
        for p in positions:
            p["value_base"] = round(p["value_local"] * m, 2)

        total_base += eq_base
        total_cash_base += cash_local * m
        n_positions += len(positions)
        sleeves_out.append({
            "key": k, "name": region.name, "currency": region.currency,
            "regime": regime,
            "cash_local": round(cash_local, 2),
            "invested_local": round(invested_local, 2),
            "equity_local": round(eq_local, 2),
            "equity_base": round(eq_base, 2),
            "cash_pct": round(cash_local / eq_local, 4) if eq_local else 1.0,
            "last_rebalance_month": sleeve.get("last_rebalance_month"),
            "_positions": positions,
        })

    # weights of the total book (fill now that total_base is known)
    denom = total_base or 1.0
    for s in sleeves_out:
        s["weight"] = round(s["equity_base"] / denom, 4)
        for p in s["_positions"]:
            p["weight"] = round(p["value_base"] / denom, 4)
        s["positions"] = s.pop("_positions")

    # fees grouped by currency
    fees: dict[str, float] = {}
    for t in state["trades"]:
        fees[t["currency"]] = fees.get(t["currency"], 0.0) \
            + t.get("commission", 0.0) + t.get("stamp_duty", 0.0)

    eq_hist = state.get("equity_history", [])
    prev_equity = eq_hist[-1][1] if eq_hist else state["initial_capital_base"]
    initial = state["initial_capital_base"]

    recent = []
    for t in reversed(state["trades"][-40:]):
        recent.append({**t, "value": round(t["shares"] * t["fill"], 2)})

    return {
        "account": account,
        "base_currency": state.get("base_currency", cfg.BASE_CURRENCY),
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": synthetic,
        "kpis": {
            "total_equity": round(total_base, 2),
            "initial_capital": initial,
            "total_return": round(total_base / initial - 1, 4) if initial else 0.0,
            "day_change": round(total_base / prev_equity - 1, 4) if prev_equity else 0.0,
            "n_trades": len(state["trades"]),
            "n_positions": n_positions,
            "cash_pct": round(total_cash_base / denom, 4),
            "fees": [{"currency": c, "amount": round(v, 2)} for c, v in fees.items()],
        },
        "allocations": state.get("allocations", cfg.ALLOCATIONS),
        "fx": snap_fx,
        "equity_curve": [{"date": d, "equity": e} for d, e in eq_hist],
        "sleeve_curves": state.get("sleeve_history", []),
        "sleeves": sleeves_out,
        "recent_trades": recent,
    }
