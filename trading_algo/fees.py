"""Per-region transaction costs, plus interest on uninvested cash.

Two pieces, both in the region's local currency:
- commission: max(floor, notional · commission_bps) — IBKR-style.
- stamp duty: a tax on PURCHASES only (UK Stamp Duty Reserve Tax, 0.5%).
  Sells and non-UK regions pay nothing. This asymmetry materially affects a
  high-turnover UK momentum book, so it is modelled explicitly.

Slippage is modelled separately (in the execution/backtest layer) as a price
adjustment per side; it is not a fee here.

`idle_cash_credit` is the odd one out: a CREDIT rather than a cost. It lives here
because this module is the one entrypoint for money adjustments the simulator
applies per bar (refactor R1), and the alternative was a second place that knows
the cash rate.
"""
from __future__ import annotations

import math

import pandas as pd

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
                  impact: float = 0.0, *,
                  nav: float | None = None,
                  orders: pd.Series | None = None) -> float:
    """The ONE backtest cost entrypoint (refactor R1): commission + slippage on
    turnover, asymmetric stamp duty on buys, plus an optional market-impact term
    (fraction of NAV) from F6.

    Given `nav` (the book's equity) and `orders` (the per-name |Δw| of every
    name whose weight actually changed this rebalance, nonzero only), commission
    is the EXACT per-order charge — Σ_i commission(region, |Δw_i| · nav) / nav,
    one call to `commission()` (which applies `region.min_commission`) per order
    — not an average. An uneven rebalance (one order too small for bps to clear
    the floor, one large enough it doesn't) is charged floor + bps·large, never
    2·max(bps·avg, floor): averaging a tiny order's floor into a big order's bps
    would under-charge it (M-1). Without `nav`/`orders` (every pre-existing
    caller) the bps rate is used on its own, which is the prior model exactly,
    so any caller that cannot size the individual orders keeps the number it
    always got.

    Charging bps alone (July finding H3) modelled a small book as trading almost
    free. The paper engine ALSO skips "dust" trims below a notional floor
    (`paper_trade.py`) — that is a HOLDINGS difference between the two engines
    (the paper book is left still holding a sliver the backtest would have
    sold), already accounted for by the reconciliation bridge's holdings lines.
    It is not a cost to imitate here: every entry in `orders` is a trade the
    backtest itself actually placed.
    """
    duty = buy_turnover * region.stamp_duty_bps / 1e4
    if nav is not None and nav > 0 and orders is not None and len(orders) > 0:
        commission_frac = sum(
            commission(region, abs(float(dw)) * float(nav)) for dw in orders
        ) / float(nav)
        return (turnover * region.slippage_bps / 1e4
                + commission_frac + duty + impact)
    return turnover * round_trip_cost_rate(region) + duty + impact


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


def idle_cash_credit(net_exposure: float, days: float, annual_rate: float) -> float:
    """Interest earned on the UNINVESTED fraction of NAV, as a fraction of NAV.

    `net_exposure` is Σw — NET and signed. Cash held is 1 − Σw, because longs
    consume cash and shorts generate it: a flat book (Σw = 0) has its whole
    equity on deposit, a fully-invested long-only book (Σw = 1) has none, and a
    dollar-neutral long/short book (Σw = 0, gross 2.0) also has its equity on
    deposit — the longs are funded by the short proceeds.

    That signing is deliberate and is the same trap the FX financing model hit
    twice: the exposure that matters is NET, never `gross − 1`. Charging (or
    crediting) on gross double-counts the short leg, which generates cash rather
    than consuming it.

    ACT/365 on calendar `days`, so a weekend accrues three days and a full year
    of daily bars sums to `annual_rate`.

    CREDIT SIDE ONLY. A net exposure above 1 is a margin debit and returns 0.0
    here — the borrow charge is a separate cost that the equity stack does not yet
    model (docs/SHARPE_RESEARCH.md §7). This function must never return a negative
    number, or that unmodelled debit would appear by accident and only for books
    that happen to be levered.
    """
    idle = 1.0 - float(net_exposure)
    if idle <= 0.0 or annual_rate <= 0.0 or days <= 0:
        return 0.0
    return idle * float(annual_rate) * float(days) / 365.0
