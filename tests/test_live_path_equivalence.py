"""Live-path equivalence gate — the pin the code review flagged as missing.

Invariant #3 (ONE weight function shared by backtest and paper) was only ever
enforced across the machine-bound backtest<->paper boundary (``test_consistency``,
``test_fx_consistency``). The *live* execution paths — ``execution_ibkr`` for the
equity sleeves and ``forex.crypto_exec`` for spot crypto — were never gated, so a
second sizing copy, a dropped halt, an uncapped order or a NaN weight could reach
a broker without any test noticing.

This file closes that boundary. Everything here runs in **dry-run / synthetic** —
fake ib_insync, fake ccxt, no keys, no network — so no order can ever leave the
machine. It pins four things on BOTH live paths:

  1. EQUIVALENCE — on identical inputs (same synthetic prices, same book state)
     the live path expresses exactly the target weights the gated paper engine
     computes via the one weight function (``strategy.compute_targets`` /
     ``fx_strategy.compute_targets``). No second sizing path.
  2. HALT SAFETY — a persisted ``risk_halted`` book plans ZERO opening/rebalancing
     orders (flatten-only or no-op). Highest-severity swarm fix; pinned so it can
     never silently regress.
  3. NOTIONAL CAP — an order whose notional would exceed the configured per-order
     cap is clamped/refused before placement.
  4. FINITE-WEIGHT GUARD — a NaN / degenerate target weight is rejected cleanly:
     no partial order run, no NaN order, no ValueError mid-loop.

Where the code does NOT yet enforce (4) — the crypto path — the test is an honest
``xfail`` encoding the *intended* contract, not a vacuous pass, so the gap stays
machine-visible until it is fixed.
"""
from __future__ import annotations

import dataclasses
import math
import sys
import types

import numpy as np
import pandas as pd
import pytest

from trading_algo import execution_ibkr as ex
from trading_algo import strategy
from trading_algo.config import DEFAULT_PARAMS
from trading_algo.regions import get_region

from trading_algo.forex import crypto_exec, explain, fx_strategy
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_config import profile as fx_profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE

CRYPTO_PX = {"BTCUSD": 60_000.0, "ETHUSD": 3_000.0, "SOLUSD": 150.0}


# ===========================================================================
#  Fake brokers — record orders, serve NAV/positions/prices. Never a real order.
# ===========================================================================
class _Val:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


class _Contract:
    def __init__(self, symbol, currency):
        self.symbol, self.currency = symbol, currency


class _Pos:
    def __init__(self, symbol, currency, position, avg_cost=100.0):
        self.contract = _Contract(symbol, currency)
        self.position, self.avgCost = position, avg_cost


class _Ticker:
    def __init__(self, px):
        self._px = px

    def marketPrice(self):
        return self._px


class _OrderStatus:
    status = "Submitted"
    avgFillPrice = 0.0


class _Trade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = _OrderStatus()


class _FakeIB:
    def __init__(self, nav, positions, prices):
        self._nav, self._positions, self._prices = nav, positions, prices
        self.placed: list[tuple[str, str, int]] = []

    def connect(self, *a, **k):
        pass

    def disconnect(self):
        pass

    def accountSummary(self):
        return [_Val("NetLiquidation", str(self._nav))]

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
        return _Trade(order)


class _MarketOrder:
    def __init__(self, action, qty):
        self.action, self.totalQuantity = action, qty


class _Stock:
    def __init__(self, symbol, exchange, currency):
        self.symbol, self.exchange, self.currency = symbol, exchange, currency


def _install_fake_ib(monkeypatch, ib):
    mod = types.ModuleType("ib_insync")
    mod.IB = lambda: ib
    mod.MarketOrder = _MarketOrder
    mod.Stock = _Stock
    monkeypatch.setitem(sys.modules, "ib_insync", mod)


class _FakeEx:
    """Minimal fake ccxt client: USDT quote balance + base holdings + last prices."""

    def __init__(self, quote_equity, held_base, prices):
        self._quote_equity = quote_equity
        self._held_base = held_base
        self._prices = prices
        self.orders: list[tuple] = []

    def load_markets(self):
        pass

    def fetch_balance(self):
        bal = {"USDT": {"total": self._quote_equity}}
        for base, qty in self._held_base.items():
            bal[base] = {"total": qty}
        return bal

    def fetch_tickers(self, markets):
        return {crypto_exec.crypto_data.SPOT.get(s, s): {"last": px}
                for s, px in self._prices.items()}

    def amount_to_precision(self, market, amount):
        return amount

    def create_order(self, market, otype, side, amount):
        self.orders.append((market, otype, side, amount))


# ===========================================================================
#  Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def fx_panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2018-01-01", end="2023-01-01")


@pytest.fixture(scope="module")
def fx_params():
    return fx_profile("balanced")


@pytest.fixture
def asx_region():
    return get_region("ASX")


@pytest.fixture
def asx_targets(asx_region):
    """A non-empty ASX target book from the shared equity weight function.

    ``regime_filter=False`` only guarantees a non-cash book to plan against — the
    equivalence claim (the live path expresses exactly this vector) is independent
    of which knobs produced it.
    """
    from trading_algo import data
    prices, index_px = data.synthetic_region(
        asx_region, start="2014-01-01", end="2024-01-01")
    p = dataclasses.replace(DEFAULT_PARAMS, regime_filter=False)
    W = strategy.compute_targets(prices, index_px, p)
    assert not W.empty                     # guard against a vacuous test
    return W


# ===========================================================================
#  1. EQUIVALENCE — live path routes through the ONE weight function
# ===========================================================================
def test_fx_live_and_paper_share_one_weight_function(fx_panel, fx_params):
    """The FX live crypto path sources its weights from ``fx_strategy.compute_targets``
    (crypto_exec.main); the gated paper engine sources them from
    ``explain.decide_and_explain`` (fx_book.run_once). On identical inputs the two
    must be the SAME vector — one weight function, no live/paper drift."""
    pool = AgentPool(max_workers=1)
    live_w = fx_strategy.compute_targets(fx_panel, fx_params, pool=pool)     # live source
    paper_w, _ = explain.decide_and_explain(fx_panel, fx_params, pool=pool)  # paper source
    cols = sorted(set(live_w.index) | set(paper_w.index))
    np.testing.assert_allclose(
        live_w.reindex(cols).fillna(0.0).values,
        paper_w.reindex(cols).fillna(0.0).values,
        rtol=1e-9, atol=1e-12)
    assert (live_w.abs() > 0).any()        # not a trivially-flat book


def test_fx_live_planner_expresses_the_shared_weights(fx_panel, fx_params):
    """Feeding the shared weights through the live planner produces a book whose
    per-name target notional == weight × equity — the planner routes the weights,
    it does not re-size them (no second sizing copy on the live path)."""
    pool = AgentPool(max_workers=1)
    w = fx_strategy.compute_targets(fx_panel, fx_params, pool=pool)
    # Use fixed prices so target notional is unambiguous; allow shorts so every
    # signed weight is expressed (spot long-only would clamp the shorts).
    px = {s: 100.0 for s in w.index}
    equity = 1_000_000.0
    orders = crypto_exec.plan_orders(w, px, equity, current_notional={}, spot=False)
    by = {o["symbol"]: o for o in orders}
    for s, wt in w.items():
        target_notional = abs(wt) * equity
        if target_notional < crypto_exec.DEFAULT_MIN_NOTIONAL:
            continue
        # notional is stored rounded to 2dp; compare within that rounding.
        assert by[s]["notional"] == pytest.approx(target_notional, abs=0.01)
        assert by[s]["side"] == ("buy" if wt > 0 else "sell")


def test_equity_live_expresses_shared_compute_targets(monkeypatch, asx_region,
                                                      asx_targets):
    """execution_ibkr does not own a weight formula — it consumes
    strategy.compute_targets. Given that vector against a flat book it must plan a
    target value of nav×w per name (within one share / the per-order cap), i.e. it
    reproduces the paper engine's book rather than re-deriving it."""
    W = asx_targets
    nav, px = 1_000_000.0, 100.0
    ib = _FakeIB(nav=nav, positions=[],
                 prices={ex.to_ib_symbol(t, asx_region): px for t in W.index})
    _install_fake_ib(monkeypatch, ib)
    orders = ex.rebalance("ASX", W, dry_run=True)
    by = {o["symbol"]: o for o in orders}
    for t, w in W.items():
        want = min(w * nav, ex.MAX_ORDER_NAV_FRACTION * nav)
        if want < asx_region.min_trade_value:
            continue
        sym = ex.to_ib_symbol(t, asx_region)
        assert by[sym]["action"] == "BUY"
        # int(want/px)*px == want within one share; the live path expresses exactly
        # the shared weight, no re-sizing.
        assert by[sym]["approx_value"] == pytest.approx(want, abs=px)


# ===========================================================================
#  2. HALT SAFETY — a persisted risk_halted book opens NOTHING
# ===========================================================================
def test_crypto_halted_book_opens_nothing_even_live(monkeypatch):
    """risk_halted -> flatten-only: a flat but halted book placing a risk-on target
    LIVE (dry_run=False) must send ZERO orders. The single highest-severity fix."""
    fake = _FakeEx(quote_equity=100_000.0, held_base={}, prices=CRYPTO_PX)
    monkeypatch.setattr(crypto_exec, "private_exchange", lambda name: fake)
    tw = pd.Series({"BTCUSD": 1.0, "ETHUSD": 0.5})   # wants risk on
    orders = crypto_exec.rebalance(tw, dry_run=False, max_order_notional=1_000.0,
                                   risk_halted=True)
    assert orders == []
    assert fake.orders == []                          # nothing hit the exchange


def test_crypto_live_guards_zero_the_weights_when_halted():
    """The pure guard the live path calls: a halted book's weights are all forced to
    zero regardless of dry_run, so no downstream planner can open risk."""
    tw = pd.Series({"BTCUSD": 0.8, "ETHUSD": -0.4})
    guarded, _ = crypto_exec._live_guards(
        tw, equity=50_000.0, dry_run=True, max_order_notional=None,
        risk_halted=True, quote="USDT")
    assert (guarded == 0.0).all()
    # And a flat book planning those zeroed weights emits no orders.
    assert crypto_exec.plan_orders(guarded, CRYPTO_PX, 50_000.0, {}) == []


def test_equity_halted_book_flattens_and_opens_nothing(monkeypatch, asx_region,
                                                       asx_targets):
    """A persisted drawdown halt forces flatten-only on the equity live path: an
    existing holding is SOLD, but no name in the risk-on target book is bought."""
    W = asx_targets
    nav, px = 1_000_000.0, 100.0
    held_sym = ex.to_ib_symbol(W.index[0], asx_region)   # a name we currently hold
    ib = _FakeIB(
        nav=nav,
        positions=[_Pos(held_sym, asx_region.currency, 1_000)],
        prices={ex.to_ib_symbol(t, asx_region): px for t in W.index})
    _install_fake_ib(monkeypatch, ib)
    orders = ex.rebalance("ASX", W, dry_run=False, risk_halted=True)
    assert orders, "halted book with a holding should still flatten it"
    assert all(o["action"] == "SELL" for o in orders)    # only ever reduce risk
    assert not any(action == "BUY" for _, action, _ in ib.placed)


# ===========================================================================
#  3. NOTIONAL CAP — oversized order clamped/refused before placement
# ===========================================================================
def test_crypto_order_clamped_to_notional_cap():
    """A weight that would deploy the whole book into one name is clamped to the
    per-order notional cap before it can be placed."""
    tw = pd.Series({"BTCUSD": 1.0})                  # wants 100% of equity
    orders = crypto_exec.plan_orders(tw, CRYPTO_PX, equity=100_000.0,
                                     current_notional={}, max_order_notional=2_500.0)
    assert len(orders) == 1
    assert orders[0]["notional"] == pytest.approx(2_500.0, rel=1e-9)


def test_crypto_live_run_never_uncapped(monkeypatch, capsys):
    """A live run with no explicit cap must NOT send an uncapped order: a default
    fraction-of-equity fat-finger cap is applied and every order respects it."""
    fake = _FakeEx(quote_equity=100_000.0, held_base={}, prices=CRYPTO_PX)
    monkeypatch.setattr(crypto_exec, "private_exchange", lambda name: fake)
    orders = crypto_exec.rebalance(pd.Series({"BTCUSD": 1.0}), dry_run=False,
                                   max_order_notional=None)
    cap = crypto_exec.DEFAULT_MAX_NOTIONAL_FRACTION * 100_000.0
    assert len(orders) == 1 and orders[0]["notional"] <= cap + 1e-6
    assert "cap" in capsys.readouterr().out.lower()


def test_equity_oversized_order_clamped_to_nav_cap(monkeypatch, asx_region):
    """A single name asking for ~90% of NAV is clamped to the per-order NAV cap
    before the order is placed."""
    nav, px = 1_000_000.0, 100.0
    ticker = asx_region.universe[0]
    sym = ex.to_ib_symbol(ticker, asx_region)
    ib = _FakeIB(nav=nav, positions=[], prices={sym: px})
    _install_fake_ib(monkeypatch, ib)
    orders = ex.rebalance("ASX", pd.Series({ticker: 0.9}), dry_run=False,
                          max_order_nav_frac=0.20)
    assert len(orders) == 1
    cap = 0.20 * nav
    assert orders[0]["approx_value"] <= cap + px             # within one share
    assert ib.placed[0][2] * px <= cap + px                  # what actually placed


# ===========================================================================
#  4. FINITE-WEIGHT GUARD — a NaN / degenerate weight is rejected cleanly
# ===========================================================================
def test_equity_nonfinite_weight_rejected_before_any_order(monkeypatch, asx_region):
    """A NaN target weight is rejected UP FRONT — before connecting or placing —
    never mid-loop after earlier orders already went to the broker."""
    a, b = asx_region.universe[0], asx_region.universe[1]
    ib = _FakeIB(nav=1_000_000.0, positions=[],
                 prices={ex.to_ib_symbol(a, asx_region): 100.0,
                         ex.to_ib_symbol(b, asx_region): 100.0})
    _install_fake_ib(monkeypatch, ib)
    tw = pd.Series({a: 0.2, b: float("nan")})
    with pytest.raises(ValueError):
        ex.rebalance("ASX", tw, dry_run=False)
    assert ib.placed == []                                    # no partial run


def test_equity_infinite_gross_leverage_rejected(monkeypatch, asx_region):
    """A degenerate over-levered book (Σ|w| >> cap) is refused before placement."""
    a, b = asx_region.universe[0], asx_region.universe[1]
    ib = _FakeIB(nav=1_000_000.0, positions=[],
                 prices={ex.to_ib_symbol(a, asx_region): 100.0,
                         ex.to_ib_symbol(b, asx_region): 100.0})
    _install_fake_ib(monkeypatch, ib)
    with pytest.raises(ValueError):
        ex.rebalance("ASX", pd.Series({a: 1.2, b: 1.2}), dry_run=False)
    assert ib.placed == []


def test_crypto_nonfinite_weight_rejected_cleanly():
    """INTENDED contract (not yet met): a NaN weight must never yield a live order.
    Either the whole book is refused, or the NaN leg is dropped while finite legs
    trade — but no order may carry a non-finite amount/notional."""
    tw = pd.Series({"BTCUSD": float("nan"), "ETHUSD": 0.3})
    orders = crypto_exec.plan_orders(tw, CRYPTO_PX, equity=10_000.0,
                                     current_notional={}, spot=False)
    for o in orders:
        assert math.isfinite(o["amount"]), f"non-finite amount in {o}"
        assert math.isfinite(o["notional"]), f"non-finite notional in {o}"
    # And the NaN leg must not masquerade as a real position.
    assert "BTCUSD" not in {o["symbol"] for o in orders}
