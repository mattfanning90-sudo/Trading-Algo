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
