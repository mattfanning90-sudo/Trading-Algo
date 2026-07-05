"""IBKR execution layer (ib_insync), generalised per region.

Each region routes to its own exchange/currency (ASX/AUD, SMART/USD, LSE/GBP).
Defaults to PAPER trading (port 7497). Workflow:
  1. Compute today's target weights from the strategy (per sleeve).
  2. Pull current positions + NAV from IBKR.
  3. Diff -> orders, skipping dust trades.
  4. Place as market orders (or preview with dry_run=True).

START WITH PAPER. Do not point this at a live account until you've watched it
behave for weeks. Run TWS / IB Gateway with the API enabled.

Risk-gate boundary: this layer only translates an already-computed target book
into orders. Strategy-level protections — the drawdown circuit breaker and the
min-viable-size gate — live in the caller (the paper engine / a decision agent),
which owns the cross-run equity peak this layer does not see. The one guard here
is a NAV floor so a de-funded account cannot fire dust orders.
"""
from __future__ import annotations

import pandas as pd

from . import config as cfg
from .regions import Region, get_region

PAPER_PORT = 7497
LIVE_PORT = 7496


def to_ib_symbol(yahoo_ticker: str, region: Region) -> str:
    """Yahoo ticker -> IBKR symbol (strip the region suffix, dash -> space)."""
    sym = yahoo_ticker
    if region.yahoo_suffix and sym.endswith(region.yahoo_suffix):
        sym = sym[: -len(region.yahoo_suffix)]
    return sym.replace("-", " ")  # e.g. BRK-B -> "BRK B"


def rebalance(region_key: str, target_weights: pd.Series, dry_run: bool = True,
              port: int = PAPER_PORT, client_id: int = 17) -> list[dict]:
    """Diff target weights vs live IBKR positions for one region and
    (optionally) place orders. Returns the order list (also as a preview)."""
    from ib_insync import IB, MarketOrder, Stock

    region = get_region(region_key)
    ib = IB()
    ib.connect("127.0.0.1", port, clientId=client_id)
    try:
        nav = float([v for v in ib.accountSummary()
                     if v.tag == "NetLiquidation"][0].value)

        # NAV floor: a de-funded account only bleeds commission floors — hold cash.
        if nav < cfg.MIN_VIABLE_EQUITY_BASE:
            return []

        # Current holdings as SHARE COUNTS (not cost basis) — valued at live
        # market price below, so the diff is target market value vs held market
        # value. Costs-basis valuation would systematically under-sell winners
        # and over-sell losers on every rebalance.
        held_shares = {p.contract.symbol: float(p.position)
                       for p in ib.positions()
                       if p.contract.currency == region.currency}

        symbols = {to_ib_symbol(t, region): t for t in target_weights.index}
        orders: list[dict] = []

        for sym in sorted(set(symbols) | set(held_shares)):
            contract = Stock(sym, region.ibkr_exchange, region.currency)
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(1.5)
            px = ticker.marketPrice()
            if not px or px != px:  # NaN guard
                continue

            shares = held_shares.get(sym, 0.0)
            held_val = shares * px
            target_val = nav * float(target_weights.get(symbols.get(sym, ""), 0.0))
            delta_val = target_val - held_val
            if abs(delta_val) < region.min_trade_value:
                continue

            if delta_val > 0:
                action, qty = "BUY", int(delta_val / px)
            else:
                # Sell from held shares; never oversell into a short.
                action = "SELL"
                qty = min(int(abs(delta_val) / px), int(shares))
            if qty == 0:
                continue

            order = {"region": region_key, "symbol": sym, "action": action,
                     "qty": qty, "approx_value": round(qty * px, 0),
                     "currency": region.currency}
            if not dry_run:
                trade = ib.placeOrder(contract, MarketOrder(action, qty))
                # Capture the returned Trade rather than discarding it, so the
                # caller has an order id / status to reconcile fills against.
                order["order_id"] = getattr(getattr(trade, "order", None), "orderId", None)
                order["status"] = getattr(getattr(trade, "orderStatus", None), "status", None)
            orders.append(order)

        return orders
    finally:
        ib.disconnect()
