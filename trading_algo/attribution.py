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

import pandas as pd

from . import metrics, tca
from .regions import get_region

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0

# The reconciliation bridge (spec §5). 1 basis point on total return.
BRIDGE_TOLERANCE = 1e-4

BRIDGE_LINES = ("dividends", "cash_interest", "fill_convention", "commission_floor",
                "stamp_duty", "exposure_gap", "rebalance_timing", "fx_translation")


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
                   periods_per_year: int = metrics.ANNUALIZATION) -> dict:
    """Annualised std of (realized - predicted) per-period returns on common dates.

    The annualisation is the measurement layer's (`metrics.annualised_vol`), so
    a tracking error and a vol on this repo's pages can never mean two
    different things."""
    df = pd.concat([realized_ret.rename("r"), predicted_ret.rename("p")], axis=1).dropna()
    if len(df) < 2:
        return {"tracking_error_bps": None, "n_obs": int(len(df))}
    te = metrics.annualised_vol(df["r"] - df["p"],
                                periods_per_year=periods_per_year)
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


def _commission_bps(region_key: str | None) -> float | None:
    """The bps rate the BACKTEST charges for this region, or None if unknown."""
    try:
        return float(get_region(region_key).commission_bps)
    except Exception:
        return None


def _window_fills(paper_state: dict, start: pd.Timestamp,
                  end: pd.Timestamp) -> list[tuple[dict, float]]:
    """Each fill dated inside (start, end], with its base-per-local FX rate.

    A fill dated `start` is left out: equity is stamped AFTER a run's trades, so
    its cost is already inside the start equity (on a real book that day is the
    build-out). The rate is the book's CURRENT `fx_snapshot` (see `reconcile`).
    """
    snap = paper_state.get("fx_snapshot") or {}
    return [(t, float(snap.get(t.get("currency")) or 1.0))
            for t in paper_state.get("trades") or []
            if start < pd.Timestamp(t.get("date")) <= end
            and t.get("shares") and t.get("fill") is not None]


def _line_fill_convention(fills: list[tuple[dict, float]]) -> tuple[float, float]:
    """(shortfall BEYOND the modelled slippage, slippage paid), base currency.

    Both engines charge `region.slippage_bps`, so only the excess diverges.
    Floored at zero per fill, as `_line_commission_floor` is: a fill better than
    modelled is not credited and stays in the residual."""
    excess = paid_total = 0.0
    for t, rate in fills:
        bps = tca._modelled_bps(t.get("region"))
        if not t.get("decision") or bps is None:
            continue
        shares, decision = float(t["shares"]), float(t["decision"])
        paid = tca.implementation_shortfall(decision, t["fill"], shares,
                                            t.get("side", "BUY"))
        paid_total += paid * rate
        excess += max(0.0, paid - shares * decision * bps / 1e4) * rate
    return excess, paid_total


def _line_commission_floor(fills: list[tuple[dict, float]]) -> float:
    """Commission paid ABOVE the backtest's bps model, base currency."""
    excess = 0.0
    for t, rate in fills:
        bps = _commission_bps(t.get("region"))
        if bps is not None:
            modelled = float(t["shares"]) * float(t["fill"]) * bps / 1e4
            excess += max(0.0, float(t.get("commission") or 0.0) - modelled) * rate
    return excess


def reconcile(paper_state: dict, predicted_equity: pd.Series) -> dict:
    """Walk from the backtest's total return to the paper book's, line by line.

    Every line is a SIGNED return fraction of the book's equity at the window
    start, positive when the cause put the BACKTEST ahead:

        backtest_return - sum(lines) - paper_return = residual

    A line is non-zero only where the two engines treat that cause DIFFERENTLY.
    A cost both charge identically (stamp duty; the slippage inside
    `fees.round_trip_cost_rate`) nets to zero and its measured paper-side amount
    goes in `notes`, so the reader sees the money without the arithmetic
    double-counting it — which is why `fill_convention` carries only the
    shortfall beyond the modelled slippage. Anything not yet named lands in
    `residual` rather than being absorbed into a line — spec §5 sets NO target
    for the residual; the honest sequence is fix, measure, then judge.

    Local-currency amounts are converted with the book's CURRENT `fx_snapshot`,
    not the rate on the trade date; that approximation is part of what the
    `fx_translation` line will eventually carry, and until then it sits in the
    residual.
    """
    eh = paper_state.get("equity_history") or []
    paper = pd.Series({pd.Timestamp(d): float(v) for d, v in eh}).sort_index()
    pred = predicted_equity.dropna().sort_index()
    common = paper.index.intersection(pred.index)
    if len(common) < 2:
        raise ValueError("bridge needs >= 2 dates common to the book and the backtest")

    start, end = common[0], common[-1]
    paper_return = float(paper.loc[end] / paper.loc[start] - 1.0)
    backtest_return = float(pred.loc[end] / pred.loc[start] - 1.0)
    start_equity = float(paper.loc[start])

    fills = _window_fills(paper_state, start, end)
    slip_excess, slip_paid = _line_fill_convention(fills)
    duty = sum(float(t.get("stamp_duty") or 0.0) * rate for t, rate in fills)
    lines = {name: 0.0 for name in BRIDGE_LINES}
    lines["fill_convention"] = slip_excess / start_equity
    lines["commission_floor"] = _line_commission_floor(fills) / start_equity

    ccy = paper_state.get("base_currency") or "base"
    notes = {
        "dividends": "0.0 — the paper ledger credits no dividend yet; the "
                     "backtest's prices are already total-return",
        "cash_interest": "0.0 — the paper book accrues no interest on idle cash "
                         "yet; the backtest applies fees.idle_cash_credit",
        "fill_convention": f"only the shortfall beyond the modelled slippage, "
                           f"which both engines charge; the paper book paid "
                           f"{slip_paid:,.2f} {ccy} of slippage over this window "
                           f"(fills that record a decision price)",
        "stamp_duty": f"0.0 — both engines charge it; the paper book paid "
                      f"{duty:,.2f} {ccy} over this window",
        "exposure_gap": "0.0 — needs the gated target as of each rebalance, "
                        "which needs prices this signature does not carry",
        "rebalance_timing": "0.0 — needs the month-end counterfactual book",
        "fx_translation": "0.0 — both engines translate; the difference (and "
                          "this bridge's current-rate approximation) is not yet isolated",
    }

    residual = backtest_return - sum(lines.values()) - paper_return
    return {"start": str(start.date()), "end": str(end.date()),
            "backtest_return": backtest_return, "paper_return": paper_return,
            "lines": lines, "notes": notes, "residual": residual,
            "identity_ok": abs(residual) <= BRIDGE_TOLERANCE}
