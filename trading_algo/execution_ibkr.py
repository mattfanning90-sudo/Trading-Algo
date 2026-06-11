"""IBKR execution layer (ib_insync), generalised per region.

Each region routes to its own exchange/currency (ASX/AUD, SMART/USD, LSE/GBP).
Defaults to PAPER trading (port 7497). Workflow:
  1. Compute today's target weights from the strategy (per sleeve).
  2. Pull current positions + NAV from IBKR.
  3. Diff -> orders, skipping dust trades.
  4. Place as market orders (or preview with dry_run=True).

START WITH PAPER. Do not point this at a live account until you've watched it
behave for weeks. Run TWS / IB Gateway with the API enabled.
"""
from __future__ import annotations

import pandas as pd

from .regions import Region, get_region

PAPER_PORT = 7497
LIVE_PORT = 7496
MIN_TRADE_VALUE = 500  # in the region's local currency — skip rebalance dust


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

        positions = {p.contract.symbol: p.position * p.avgCost
                     for p in ib.positions()
                     if p.contract.currency == region.currency}

        symbols = {to_ib_symbol(t, region): t for t in target_weights.index}
        orders: list[dict] = []

        for sym in sorted(set(symbols) | set(positions)):
            contract = Stock(sym, region.ibkr_exchange, region.currency)
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(1.5)
            px = ticker.marketPrice()
            if not px or px != px:  # NaN guard
                continue

            target_val = nav * float(target_weights.get(symbols.get(sym, ""), 0.0))
            delta_val = target_val - positions.get(sym, 0.0)
            if abs(delta_val) < MIN_TRADE_VALUE:
                continue

            qty = int(abs(delta_val) / px)
            if qty == 0:
                continue
            action = "BUY" if delta_val > 0 else "SELL"
            orders.append({"region": region_key, "symbol": sym, "action": action,
                           "qty": qty, "approx_value": round(qty * px, 0),
                           "currency": region.currency})
            if not dry_run:
                ib.placeOrder(contract, MarketOrder(action, qty))

        return orders
    finally:
        ib.disconnect()
