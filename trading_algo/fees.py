"""Per-region transaction costs.

Two pieces, both in the region's local currency:
- commission: max(floor, notional · commission_bps) — IBKR-style.
- stamp duty: a tax on PURCHASES only (UK Stamp Duty Reserve Tax, 0.5%).
  Sells and non-UK regions pay nothing. This asymmetry materially affects a
  high-turnover UK momentum book, so it is modelled explicitly.

Slippage is modelled separately (in the execution/backtest layer) as a price
adjustment per side; it is not a fee here.
"""
from __future__ import annotations

import math

from .regions import Region


def commission(region: Region, notional: float) -> float:
    """Broker commission on a trade of |notional| in local currency."""
    notional = abs(notional)
    if notional == 0:
        return 0.0
    return max(region.min_commission, notional * region.commission_bps / 1e4)


def stamp_duty(region: Region, buy_notional: float) -> float:
    """Tax charged on the BUY notional only (0 for sells / non-UK)."""
    buy_notional = max(buy_notional, 0.0)
    return buy_notional * region.stamp_duty_bps / 1e4


def round_trip_cost_rate(region: Region) -> float:
    """Commission + slippage as a fraction of notional, summed over both sides
    of a full turnover unit. Used by the backtester's turnover cost model.

    A turnover of `x` means |Δw| summed = x, i.e. x/2 bought and x/2 sold. Both
    sides pay commission_bps + slippage_bps. Stamp duty is added separately on
    the buy side by the caller (it is asymmetric)."""
    return (region.commission_bps + region.slippage_bps) / 1e4


def turnover_cost(region: Region, turnover: float, buy_turnover: float,
                  impact: float = 0.0) -> float:
    """The ONE backtest cost entrypoint (refactor R1): commission + slippage on
    turnover, asymmetric stamp duty on buys, plus an optional market-impact term
    (fraction of NAV) from F6. With impact=0 this is exactly the prior model, so
    the F16 regression baseline is unchanged."""
    return (turnover * round_trip_cost_rate(region)
            + buy_turnover * region.stamp_duty_bps / 1e4
            + impact)


def square_root_impact(order_notional: float, adv_dollar: float, vol: float,
                       coef: float) -> float:
    """Almgren-style market-impact RATE for one order (fraction of the order's
    value): coef · vol · sqrt(participation), participation = order / ADV$.

    A bigger order relative to a name's average dollar volume, or a more volatile
    name, costs more to trade — with square-root (concave) participation. Returns
    0 when ADV is unknown/zero (can't size the impact). Backlog F6."""
    if (adv_dollar is None or vol is None or coef is None
            or adv_dollar != adv_dollar or vol != vol        # NaN-safe
            or adv_dollar <= 0):
        return 0.0
    participation = max(float(order_notional) / float(adv_dollar), 0.0)
    return float(coef) * float(vol) * math.sqrt(participation)
