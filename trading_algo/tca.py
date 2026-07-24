"""Execution-quality / transaction-cost analysis (backlog F11).

Measures the gap between the price a trade was *decided* at and the price it
actually *filled* at — implementation shortfall — and rolls it up per region so
realized slippage can be compared to the modelled `region.slippage_bps`. This is
where the backtest-vs-live cost gap gets diagnosed and, over time, where the
modelled slippage is recalibrated.

Two sources feed the same report:
  * paper trades carry a `decision` price (the pre-slippage close) and a `fill`;
    in the paper sim realized == modelled by construction (there is no real market
    impact), so paper TCA is a proxy / plumbing check.
  * live fills (execution_ibkr) carry the arrival price and the broker's actual
    average fill — that is the real, non-circular measurement.

A per-region alert fires when realized slippage materially exceeds the modelled
assumption over a minimum number of fills.
"""
from __future__ import annotations

from .regions import get_region

# Alert when realized slippage exceeds the modelled bps by this factor...
ALERT_FACTOR = 1.5
# ...but only once there are at least this many fills (avoid noise on 1-2 trades).
ALERT_MIN_FILLS = 20


def implementation_shortfall(decision: float, fill: float, shares: float,
                             side: str) -> float:
    """Adverse execution cost of one fill, in the trade's currency (positive =
    worse than the decision price). BUY fills above / SELL fills below the
    decision price both cost the book."""
    sgn = 1.0 if side.upper() == "BUY" else -1.0
    return float(shares) * sgn * (float(fill) - float(decision))


def realized_slippage_bps(decision: float, fill: float, side: str) -> float:
    """Signed realized slippage of one fill in basis points (positive = adverse)."""
    decision = float(decision)
    if decision <= 0:
        return 0.0
    sgn = 1.0 if side.upper() == "BUY" else -1.0
    return sgn * (float(fill) - decision) / decision * 1e4


def _modelled_bps(region_key: str) -> float | None:
    try:
        return float(get_region(region_key).slippage_bps)
    except Exception:
        return None


def tca_report(trades: list[dict]) -> dict:
    """Per-region implementation-shortfall summary over `trades`.

    Each trade needs `region`, `side`, `shares`, `decision`, `fill` (and
    optionally `currency`). Trades without a decision price are skipped (they
    predate F11). Returns a dict keyed by region plus a top-level `alerts` list.
    """
    by_region: dict[str, dict] = {}
    for tr in trades:
        if tr.get("decision") in (None, 0) or tr.get("fill") is None:
            continue
        rk = tr.get("region")
        if rk is None:                 # a trade with no region can't be attributed
            continue
        side = tr.get("side", "BUY")
        shares = tr.get("shares", 0)
        dec, fill = tr["decision"], tr["fill"]
        agg = by_region.setdefault(rk, {
            "n_fills": 0, "shortfall": 0.0, "bps_sum": 0.0,
            "currency": tr.get("currency"), "modelled_bps": _modelled_bps(rk)})
        agg["n_fills"] += 1
        agg["shortfall"] += implementation_shortfall(dec, fill, shares, side)
        agg["bps_sum"] += realized_slippage_bps(dec, fill, side)

    report: dict = {}
    alerts: list[str] = []
    for rk, agg in by_region.items():
        n = agg["n_fills"]
        realized = agg["bps_sum"] / n if n else 0.0
        modelled = agg["modelled_bps"]
        entry = {
            "n_fills": n,
            "implementation_shortfall": round(agg["shortfall"], 2),
            "currency": agg["currency"],
            "realized_slippage_bps": round(realized, 2),
            "modelled_slippage_bps": modelled,
        }
        if (modelled is not None and modelled > 0 and n >= ALERT_MIN_FILLS
                and realized > modelled * ALERT_FACTOR):
            entry["alert"] = True
            alerts.append(f"{rk}: realized {realized:.1f}bps > "
                          f"{ALERT_FACTOR:g}x modelled {modelled:.1f}bps over {n} fills")
        report[rk] = entry

    report["alerts"] = alerts
    return report
