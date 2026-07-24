"""Execution-layer correctness, driven by a fake ib_insync (no broker needed).

Pins the rebalancing fixes: positions are valued at MARKET (not cost basis),
sell quantities come from HELD SHARES (never oversell into a short), the dust
floor is per-region, and placeOrder's Trade result is captured, not discarded.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from trading_algo import execution_ibkr as ex


# --- a minimal fake ib_insync -------------------------------------------------
class _Val:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


class _Contract:
    def __init__(self, symbol, currency):
        self.symbol, self.currency = symbol, currency


class _Pos:
    def __init__(self, symbol, currency, position, avg_cost):
        self.contract = _Contract(symbol, currency)
        self.position, self.avgCost = position, avg_cost


class _Ticker:
    def __init__(self, px):
        self._px = px

    def marketPrice(self):
        return self._px


class _Order:
    def __init__(self, order_id):
        self.orderId = order_id


class _OrderStatus:
    status = "Submitted"


class _Trade:
    _next = 100

    def __init__(self):
        _Trade._next += 1
        self.order = _Order(_Trade._next)
        self.orderStatus = _OrderStatus()


class _FakeIB:
    """Records placed orders; serves NAV, positions and per-symbol prices."""

    def __init__(self, nav, positions, prices):
        self._nav = nav
        self._positions = positions
        self._prices = prices
        self.placed: list[tuple[str, str, int]] = []

    def connect(self, *a, **k):
        pass

    def disconnect(self):
        pass

    def accountSummary(self):
        return [_Val("NetLiquidation", str(self._nav)), _Val("BuyingPower", "1")]

    def positions(self):
        return self._positions

    def qualifyContracts(self, contract):
        return [contract]

    def reqMktData(self, contract, *a, **k):
        return _Ticker(self._prices.get(contract.symbol, float("nan")))

    def sleep(self, *_):
        pass

    def placeOrder(self, contract, order):
        self.placed.append((contract.symbol, order.action, order.totalQuantity))
        return _Trade()


class _MarketOrder:
    def __init__(self, action, qty):
        self.action, self.totalQuantity = action, qty


class _Stock:
    def __init__(self, symbol, exchange, currency):
        self.symbol, self.exchange, self.currency = symbol, exchange, currency


def _install_fake(monkeypatch, ib):
    mod = types.ModuleType("ib_insync")
    mod.IB = lambda: ib
    mod.MarketOrder = _MarketOrder
    mod.Stock = _Stock
    monkeypatch.setitem(sys.modules, "ib_insync", mod)


def test_positions_valued_at_market_not_cost_basis(monkeypatch):
    # Held 100 AAPL bought at 50 (cost basis 5,000) now worth 200 (mkt 20,000).
    # NAV 100k, target 10% -> target_val 10,000. Correct delta = 10k-20k = -10k
    # (SELL). A cost-basis bug would see 10k-5k = +5k and wrongly BUY.
    ib = _FakeIB(nav=100_000,
                 positions=[_Pos("AAPL", "USD", 100, 50.0)],
                 prices={"AAPL": 200.0})
    _install_fake(monkeypatch, ib)
    orders = ex.rebalance("US", pd.Series({"AAPL": 0.10}), dry_run=True)
    assert len(orders) == 1
    assert orders[0]["action"] == "SELL"


def test_full_exit_never_oversells(monkeypatch):
    # Target weight 0 for a held name -> sell exactly the held shares, no more.
    ib = _FakeIB(nav=100_000,
                 positions=[_Pos("AAPL", "USD", 100, 150.0)],
                 prices={"AAPL": 200.0})
    _install_fake(monkeypatch, ib)
    orders = ex.rebalance("US", pd.Series(dtype=float), dry_run=False)
    assert len(orders) == 1
    o = orders[0]
    assert o["action"] == "SELL"
    assert o["qty"] <= 100                      # never more than held
    assert ib.placed == [("AAPL", "SELL", o["qty"])]
    assert o["order_id"] is not None            # Trade captured, not discarded
    assert o["status"] == "Submitted"


def test_per_region_dust_floor(monkeypatch):
    # A ~£200 delta is below FTSE's 260 floor -> skipped; the same £ delta would
    # clear ASX's 500 floor only if larger. Here we assert the FTSE floor bites.
    ib = _FakeIB(nav=100_000,
                 positions=[],
                 prices={"BP": 5.0})
    _install_fake(monkeypatch, ib)
    # target 0.2% of 100k = £200 < 260 floor -> no order
    orders = ex.rebalance("FTSE", pd.Series({"BP.L": 0.002}), dry_run=True)
    assert orders == []


def test_nav_floor_holds_cash(monkeypatch):
    ib = _FakeIB(nav=10.0, positions=[], prices={"AAPL": 200.0})
    _install_fake(monkeypatch, ib)
    orders = ex.rebalance("US", pd.Series({"AAPL": 0.5}), dry_run=True)
    assert orders == []


def test_nan_weight_rejected_before_any_order(monkeypatch):
    # A NaN target weight must be rejected UP FRONT, before connecting/placing —
    # not raise mid-loop after earlier orders have already gone to the broker.
    ib = _FakeIB(nav=100_000, positions=[], prices={"AAPL": 200.0, "MSFT": 300.0})
    _install_fake(monkeypatch, ib)
    tw = pd.Series({"AAPL": 0.2, "MSFT": float("nan")})
    with pytest.raises(ValueError):
        ex.rebalance("US", tw, dry_run=False)
    assert ib.placed == []                      # no partial order run


def test_excess_gross_leverage_rejected(monkeypatch):
    ib = _FakeIB(nav=100_000, positions=[], prices={"AAPL": 200.0, "MSFT": 300.0})
    _install_fake(monkeypatch, ib)
    tw = pd.Series({"AAPL": 1.2, "MSFT": 1.2})  # Σ|w| = 2.4 >> cap
    with pytest.raises(ValueError):
        ex.rebalance("US", tw, dry_run=False)
    assert ib.placed == []


def test_oversized_single_order_clamped_to_nav_cap(monkeypatch):
    # A single name asking for ~90% of NAV is clamped to the per-order NAV cap.
    ib = _FakeIB(nav=100_000, positions=[], prices={"AAPL": 200.0})
    _install_fake(monkeypatch, ib)
    orders = ex.rebalance("US", pd.Series({"AAPL": 0.9}), dry_run=False,
                          max_order_nav_frac=0.20)
    assert len(orders) == 1
    cap = 0.20 * 100_000
    assert orders[0]["approx_value"] <= cap + 200      # within one share of cap
    assert ib.placed[0][0] == "AAPL"
    assert ib.placed[0][2] * 200.0 <= cap + 200


def test_halted_book_flattens_and_opens_nothing(monkeypatch):
    # A persisted drawdown halt => flatten-only: sell the held name, open no new
    # risk, even though the target book is risk-on.
    ib = _FakeIB(nav=100_000,
                 positions=[_Pos("AAPL", "USD", 100, 150.0)],
                 prices={"AAPL": 200.0, "MSFT": 300.0})
    _install_fake(monkeypatch, ib)
    tw = pd.Series({"AAPL": 0.3, "MSFT": 0.3})  # risk-on target
    orders = ex.rebalance("US", tw, dry_run=False, risk_halted=True)
    actions = {o["symbol"]: o["action"] for o in orders}
    assert actions == {"AAPL": "SELL"}          # flatten AAPL, no MSFT buy
    assert all(o["action"] == "SELL" for o in orders)
    assert not any(a == "BUY" for _, a, _ in ib.placed)
