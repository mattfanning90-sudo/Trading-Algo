"""Build the dashboard's JSON state snapshot from the persisted paper account.

Reads paper_state_{account}.json, marks every position to the latest available
price, converts each sleeve to the base currency, computes the regime per sleeve,
and assembles the contract the frontend consumes. Pure read — never mutates state.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .. import config as cfg
from .. import fx, paper_trade, signals
from ..regions import get_region

HISTORY_BARS = 66          # ~90 calendar days of closes for the hover popovers


def _benchmark_curve(index_by_region: dict, eq_hist: list, initial: float,
                     synthetic: bool) -> list[dict]:
    """Equal-weight buy-and-hold of the regional indices (in AUD), normalised to
    `initial` at the account's inception, sampled at the equity-history dates.
    Returns [] on any failure (frontend treats it as optional)."""
    if not eq_hist or not index_by_region:
        return []
    try:
        dates = pd.to_datetime([d for d, _ in eq_hist])
        currencies = sorted({ccy for _, ccy in index_by_region.values()})
        fx_tbl = (fx.synthetic_fx(currencies, base=cfg.BASE_CURRENCY) if synthetic
                  else fx.load_fx(currencies, cfg.START, base=cfg.BASE_CURRENCY, use_cache=False))
        parts = []
        for idx, ccy in index_by_region.values():
            mult = fx.align_fx(fx_tbl, idx.index, ccy)
            idx_aud = (idx * mult).reindex(dates, method="ffill").bfill()
            parts.append(idx_aud / idx_aud.iloc[0])
        norm = sum(parts) / len(parts)
        return [{"date": d.strftime("%Y-%m-%d"), "value": round(float(initial * v), 2)}
                for d, v in zip(dates, norm)]
    except Exception:
        return []


def closed_trades(trades: list[dict], snap_fx: dict) -> dict:
    """FIFO round-trips reconstructed from the fills list (which paper_trade
    appends chronologically). Entry costs are allocated per share; the closing
    fill carries its own commission. Fills already include modelled slippage,
    so `net` here is commission + stamp on top of price P&L."""
    lots: dict[tuple, list] = {}          # (region, ticker) -> [qty, fill, cost/share, date]
    rows: list[dict] = []
    for t in trades:
        key = (t["region"], t["ticker"])
        qty = int(t["shares"])
        cost = float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0))
        if t["side"] == "BUY":
            lots.setdefault(key, []).append([qty, float(t["fill"]), cost / qty if qty else 0.0, t["date"]])
            continue
        # SELL: consume lots head-first
        queue = lots.get(key, [])
        remaining, matched, entry_cost = qty, [], 0.0
        while remaining > 0 and queue:
            lot = queue[0]
            take = min(remaining, lot[0])
            matched.append((take, lot[1], lot[3]))
            entry_cost += take * lot[2]
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                queue.pop(0)
        filled = qty - remaining
        if filled <= 0:
            continue
        exit_px = float(t["fill"])
        entry_notional = sum(m * px for m, px, _ in matched)
        entry_avg = entry_notional / filled
        gross = filled * exit_px - entry_notional
        costs = entry_cost + cost
        net = gross - costs
        mult = snap_fx.get(t["currency"], 1.0)
        try:
            held = (date.fromisoformat(t["date"]) - date.fromisoformat(matched[0][2])).days
        except ValueError:
            held = 0
        left_over = sum(lot[0] for lot in queue)
        rows.append({
            "date": t["date"], "ticker": t["ticker"], "region": t["region"],
            "currency": t["currency"], "qty": filled,
            "entry": round(entry_avg, 4), "exit": round(exit_px, 4),
            "held_days": held,
            "gross": round(gross, 2), "costs": round(costs, 2),
            "net": round(net, 2), "net_base": round(net * mult, 2),
            "return_pct": round(net / entry_notional, 4) if entry_notional else 0.0,
            "note": f"PARTIAL {filled}/{filled + left_over}" if left_over else "",
        })
    rows.sort(key=lambda r: (r["date"], -abs(r["net_base"])))
    by_ccy: dict[str, float] = {}
    for r in rows:
        by_ccy[r["currency"]] = by_ccy.get(r["currency"], 0.0) + r["net"]
    return {
        "rows": rows,
        "net_base": round(sum(r["net_base"] for r in rows), 2),
        "wins": sum(1 for r in rows if r["net"] > 0),
        "count": len(rows),
        "by_currency": [{"currency": c, "net": round(v, 2)} for c, v in by_ccy.items()],
    }


def _next_rebalance(as_of: str, last_rebalance_months: list[str | None]) -> str:
    """Next execution date. Normally the first weekday of the month after
    `as_of` (signals decided at month-end, trades execute T+1) — but if any
    sleeve hasn't rebalanced in the as_of month yet, the very next engine run
    will trade, so report the next weekday instead."""
    try:
        d = date.fromisoformat(as_of)
    except ValueError:
        d = date.today()
    if any(m is not None and m < as_of[:7] for m in last_rebalance_months):
        nxt = d + timedelta(days=1)
    else:
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def _month_return(sleeve_hist: list[dict], key: str) -> float | None:
    """This-month % move of one sleeve, from the persisted sleeve history.
    Baseline is the last mark BEFORE the month (so rebalance-day moves count);
    falls back to the first in-month mark for a book born this month."""
    vals = [(h["date"], h.get(key)) for h in sleeve_hist if h.get(key)]
    if not vals:
        return None
    month = vals[-1][0][:7]
    prior = [v for d, v in vals if d[:7] < month]
    in_month = [v for d, v in vals if d[:7] == month]
    base = prior[-1] if prior else (in_month[0] if len(in_month) > 1 else None)
    if not base or not in_month:
        return 0.0
    return round(in_month[-1] / base - 1.0, 4)


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
    # Iterate the account's OWN regions (a small account may trade only one).
    regions = list(state.get("allocations") or cfg.ALLOCATIONS)

    # Realised P&L and open-position cost basis both come from the fills log so
    # the OVERVIEW tiles and the closed-trades ledger can't disagree, and so
    # positions opened before cost-basis tracking still show a real unrealised
    # P&L (their stored basis may be missing).
    closed = closed_trades(state["trades"], snap_fx)
    basis_fallback = paper_trade.reconstruct_basis(state["trades"])

    sleeves_out, as_of = [], ""
    total_base = total_cash_base = total_unrealized_base = 0.0
    total_invested_base = 0.0
    total_realized_base = closed["net_base"]   # FIFO round-trips, net of costs
    index_by_region: dict[str, tuple] = {}
    n_positions = 0
    history: dict[str, dict] = {}
    index_state: list[dict] = []

    for k in regions:
        region = get_region(k)
        prices, index_px = paper_trade.latest_region_data(region, synthetic)
        px = prices.iloc[-1]
        px_prev = prices.iloc[-2] if len(prices) > 1 else px  # for day-change
        as_of = max(as_of, prices.index[-1].strftime("%Y-%m-%d"))
        sleeve = state["sleeves"][k]
        cost_basis = sleeve.get("cost_basis", {})
        m = snap_fx[region.currency]
        regime = ("RISK_ON"
                  if bool(signals.index_risk_on(index_px, region.params).iloc[-1])
                  else "RISK_OFF")
        index_state.append({"region": k, "symbol": region.index_ticker,
                            "risk_on": regime == "RISK_ON"})

        invested_local = 0.0
        positions = []
        for t, sh in sleeve["positions"].items():
            if t in prices.columns:
                tail = prices[t].dropna().tail(HISTORY_BARS)
                history[t] = {
                    "dates": [d.strftime("%Y-%m-%d") for d in tail.index],
                    "closes": [round(float(v), 4) for v in tail],
                }
            price = _safe_price(px, t)
            prev_price = _safe_price(px_prev, t)
            val_local = sh * price
            invested_local += val_local
            avg = float(cost_basis.get(t) or basis_fallback.get((k, t)) or price)
            day_change = (price / prev_price - 1.0) if prev_price else 0.0
            unrl_pct = (price / avg - 1.0) if avg else 0.0
            unrl_base = sh * (price - avg) * m
            total_unrealized_base += unrl_base
            positions.append({
                "ticker": t, "shares": int(sh),
                "price": round(price, 4),
                "value_local": round(val_local, 2),
                "value_base": round(val_local * m, 2),
                "day_change": round(day_change, 4),
                "change_local": round(price - prev_price, 4),
                "avg_cost": round(avg, 4),
                "unrealized_pct": round(unrl_pct, 4),
                "unrealized_base": round(unrl_base, 2),
            })
        cash_local = float(sleeve["cash"])
        eq_local = cash_local + invested_local
        eq_base = eq_local * m

        total_base += eq_base
        total_cash_base += cash_local * m
        total_invested_base += invested_local * m
        index_by_region[k] = (index_px, region.currency)
        n_positions += len(positions)
        sleeves_out.append({
            "key": k, "name": region.name, "currency": region.currency,
            "regime": regime,
            "index_ticker": region.index_ticker,
            "fx_rate": round(float(m), 4),
            "month_return": _month_return(state.get("sleeve_history", []), k),
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

    # fees grouped by currency, and totalled into the base currency
    fees: dict[str, float] = {}
    stamp: dict[str, float] = {}
    for t in state["trades"]:
        fees[t["currency"]] = fees.get(t["currency"], 0.0) \
            + t.get("commission", 0.0) + t.get("stamp_duty", 0.0)
        if t.get("stamp_duty"):
            stamp[t["currency"]] = stamp.get(t["currency"], 0.0) + t["stamp_duty"]
    fees_base = sum(v * snap_fx.get(c, 1.0) for c, v in fees.items())

    eq_hist = state.get("equity_history", [])
    prev_equity = eq_hist[-1][1] if eq_hist else state["initial_capital_base"]
    initial = state["initial_capital_base"]
    benchmark_curve = _benchmark_curve(index_by_region, eq_hist, initial, synthetic)

    recent = []
    for t in reversed(state["trades"][-40:]):
        recent.append({**t, "value": round(t["shares"] * t["fill"], 2)})

    blotter = [{**t, "value": round(t["shares"] * t["fill"], 2)}
               for t in state["trades"]]

    peak = float(state.get("peak_equity_base") or total_base or 1.0)

    return {
        "kind": "equity",
        # single-sleeve SMALL screens; multi-region books keep the full UI
        "micro": bool(initial) and initial < paper_trade.MICRO_THRESHOLD
                 and len(regions) == 1,
        "peak_equity": round(peak, 2),
        "off_peak": round(total_base / peak - 1.0, 6) if peak else 0.0,
        "risk_halted": bool(state.get("risk_halted", False)),
        "breaker": cfg.MAX_DRAWDOWN_STOP,
        "min_viable": cfg.MIN_VIABLE_EQUITY_BASE,
        "next_rebalance": _next_rebalance(
            as_of, [s.get("last_rebalance_month") for s in sleeves_out]),
        "index_state": index_state,
        "history": history,
        "blotter": blotter,
        "closed": closed,
        "stamp_duty": [{"currency": c, "amount": round(v, 2)} for c, v in stamp.items()],
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
            "day_change_base": round(total_base - prev_equity, 2),
            "target_vol": cfg.DEFAULT_PARAMS.target_vol,
            "n_trades": len(state["trades"]),
            "n_positions": n_positions,
            "cash_pct": round(total_cash_base / denom, 4),
            # --- total financial position (all in base currency) ---
            "invested_base": round(total_invested_base, 2),
            "cash_base": round(total_cash_base, 2),
            "fees_base": round(fees_base, 2),
            "realized_base": round(total_realized_base, 2),
            "unrealized_base": round(total_unrealized_base, 2),
            "net_pnl_base": round(total_base - initial, 2),
            "gross_exposure": round(total_invested_base / denom, 4),
            "fees": [{"currency": c, "amount": round(v, 2)} for c, v in fees.items()],
        },
        "allocations": state.get("allocations", cfg.ALLOCATIONS),
        "benchmark_curve": benchmark_curve,
        "fx": snap_fx,
        "equity_curve": [{"date": d, "equity": e} for d, e in eq_hist],
        "sleeve_curves": state.get("sleeve_history", []),
        "sleeves": sleeves_out,
        "recent_trades": recent,
    }
