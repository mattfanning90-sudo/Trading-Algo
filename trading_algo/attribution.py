"""Live-vs-backtest tracking and P&L attribution (backlog F3).

The paper (and, later, live) book has no reference point today: you stare at raw
P&L with no idea whether it is tracking the strategy the backtest promised. This
module closes that loop. Given a book's realized equity curve and a backtest's
predicted curve over the SAME window, it reports:

  * divergence    — realized total return minus backtest-predicted total return.
  * tracking_error — annualised std of the per-period return differences, with an
    alert when it exceeds a budget (AC4: 200bps).
  * cost drag     — realized transaction cost per region, measured from the trade
    log (commission + stamp + slippage-from-decision-vs-fill, reusing F11's
    decision price), the one attribution bucket we can measure exactly.

No lookahead (invariant #1): the predicted curve must come from a backtest over
the identical price window the book actually saw — the caller passes it in; this
module never refetches with hindsight. Cost is reported per region in the sleeve's
LOCAL currency (invariant #6) — sleeves are not blended across currencies here.
"""
from __future__ import annotations

import math

import pandas as pd

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0
TRADING_DAYS = 252


def equity_returns(equity_history: list) -> pd.Series:
    """[[date, value], ...] -> period-return Series indexed by date."""
    if not equity_history:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([d for d, _ in equity_history])
    vals = pd.Series([float(v) for _, v in equity_history], index=idx).sort_index()
    return vals.pct_change().dropna()


def total_return(equity_history: list) -> float:
    if not equity_history or len(equity_history) < 2:
        return 0.0
    first, last = float(equity_history[0][1]), float(equity_history[-1][1])
    return last / first - 1.0 if first else 0.0


def tracking_error(realized_ret: pd.Series, predicted_ret: pd.Series,
                   periods_per_year: int = TRADING_DAYS) -> dict:
    """Annualised std of (realized - predicted) per-period returns on common dates."""
    df = pd.concat([realized_ret.rename("r"), predicted_ret.rename("p")], axis=1).dropna()
    if len(df) < 2:
        return {"tracking_error_bps": None, "n_obs": int(len(df))}
    diff = df["r"] - df["p"]
    te = float(diff.std(ddof=1) * math.sqrt(periods_per_year))
    return {"tracking_error_bps": round(te * 1e4, 1), "n_obs": int(len(df))}


def realized_cost_drag(trades: list) -> dict:
    """Per-region realized transaction cost (commission + stamp + slippage) as a
    fraction of traded notional, in the sleeve's local currency."""
    by_region: dict[str, dict] = {}
    for t in trades:
        fill = t.get("fill")
        shares = t.get("shares", 0)
        if not fill or not shares:
            continue
        notional = float(shares) * float(fill)
        decision = t.get("decision", fill)
        slippage = abs(float(fill) - float(decision)) * float(shares)
        cost = float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0)) + slippage
        agg = by_region.setdefault(t.get("region"), {
            "cost": 0.0, "notional": 0.0, "currency": t.get("currency")})
        agg["cost"] += cost
        agg["notional"] += notional

    out: dict = {}
    for rk, agg in by_region.items():
        drag_bps = (agg["cost"] / agg["notional"] * 1e4) if agg["notional"] else 0.0
        out[rk] = {
            "cost": round(agg["cost"], 2),
            "notional": round(agg["notional"], 2),
            "cost_drag_bps": round(drag_bps, 1),
            "currency": agg["currency"],
        }
    return out


def attribution_report(paper_state: dict, predicted_equity: pd.Series | None = None,
                       predicted_cost_fraction: float | None = None) -> dict:
    """Tracking + attribution for one book.

    `predicted_equity` is a backtest equity curve over the SAME window (from the
    caller — no hindsight refetch here). `predicted_cost_fraction` is the
    backtest's modelled round-trip cost, if available, to attribute the cost
    bucket of the divergence.
    """
    eh = paper_state.get("equity_history", [])
    realized_total = total_return(eh)
    realized_ret = equity_returns(eh)
    cost = realized_cost_drag(paper_state.get("trades", []))

    report: dict = {
        "realized_total_return": round(realized_total, 4),
        "cost_drag_by_region": cost,
        "n_equity_points": len(eh),
    }

    if predicted_equity is not None and len(predicted_equity) >= 2:
        pred_total = float(predicted_equity.iloc[-1] / predicted_equity.iloc[0] - 1.0)
        pred_ret = predicted_equity.pct_change().dropna()
        te = tracking_error(realized_ret, pred_ret)
        divergence = realized_total - pred_total
        report.update({
            "predicted_total_return": round(pred_total, 4),
            "divergence": round(divergence, 4),
            "tracking_error_bps": te["tracking_error_bps"],
            "tracking_obs": te["n_obs"],
        })
        # Cost bucket of the divergence: paper paying MORE than the modelled cost
        # drags realized below predicted (a negative contribution).
        if predicted_cost_fraction is not None:
            paid = _blended_cost_fraction(cost)
            report["cost_bucket"] = round(predicted_cost_fraction - paid, 4)
            report["residual"] = round(divergence - report["cost_bucket"], 4)
        te_bps = te["tracking_error_bps"]
        report["tracking_alert"] = bool(te_bps is not None and te_bps > TRACKING_ERROR_ALERT_BPS)

    return report


def _blended_cost_fraction(cost_by_region: dict) -> float:
    """Rough cross-region cost as a fraction of total traded notional (currency
    labels dropped — a coarse blend used only for the cost-bucket estimate)."""
    tot_cost = sum(v["cost"] for v in cost_by_region.values())
    tot_notional = sum(v["notional"] for v in cost_by_region.values())
    return (tot_cost / tot_notional) if tot_notional else 0.0
