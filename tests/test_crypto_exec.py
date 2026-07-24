"""Crypto execution planner — the pure, testable core + an offline dry-run CLI.

No ccxt, no network, no keys: plan_orders is pure, and the CLI runs fully offline
with --synthetic (which forces dry-run).
"""
import pandas as pd
import pytest

from trading_algo.forex import crypto_exec, fx_book


PRICES = {"BTCUSD": 60_000.0, "ETHUSD": 3_000.0, "SOLUSD": 150.0}


def test_buy_from_flat_sizes_by_weight():
    tw = pd.Series({"BTCUSD": 0.5, "ETHUSD": 0.25})
    orders = crypto_exec.plan_orders(tw, PRICES, equity=10_000.0, current_notional={})
    by = {o["symbol"]: o for o in orders}
    assert by["BTCUSD"]["side"] == "buy"
    assert by["BTCUSD"]["notional"] == pytest.approx(5_000.0, rel=1e-6)
    assert by["BTCUSD"]["amount"] == pytest.approx(5_000.0 / 60_000.0, rel=1e-6)
    assert by["ETHUSD"]["notional"] == pytest.approx(2_500.0, rel=1e-6)


def test_spot_is_long_only_shorts_clamped():
    tw = pd.Series({"BTCUSD": -0.5})           # can't short spot
    assert crypto_exec.plan_orders(tw, PRICES, 10_000.0, {}, spot=True) == []
    # with shorting allowed (margin/perp) it would place a sell
    allowed = crypto_exec.plan_orders(tw, PRICES, 10_000.0, {}, spot=False)
    assert allowed and allowed[0]["side"] == "sell"


def test_dust_below_min_notional_skipped():
    tw = pd.Series({"SOLUSD": 0.0005})         # 0.0005 * 10k = $5 < $10 min
    assert crypto_exec.plan_orders(tw, PRICES, 10_000.0, {}) == []


def test_sell_capped_at_holdings():
    # hold $9k of BTC, target 0 -> sell, but never more than held
    tw = pd.Series({"BTCUSD": 0.0})
    orders = crypto_exec.plan_orders(tw, PRICES, 10_000.0, {"BTCUSD": 9_000.0})
    assert len(orders) == 1
    o = orders[0]
    assert o["side"] == "sell"
    assert o["amount"] == pytest.approx(9_000.0 / 60_000.0, rel=1e-6)


def test_rebalance_only_trades_the_delta():
    # hold $6k BTC, target 50% of $10k = $5k -> sell ~$1k
    tw = pd.Series({"BTCUSD": 0.5})
    orders = crypto_exec.plan_orders(tw, PRICES, 10_000.0, {"BTCUSD": 6_000.0})
    assert orders[0]["side"] == "sell"
    assert orders[0]["notional"] == pytest.approx(1_000.0, rel=1e-6)


def test_max_order_notional_caps_each_order():
    tw = pd.Series({"BTCUSD": 1.0})
    orders = crypto_exec.plan_orders(tw, PRICES, 100_000.0, {},
                                     max_order_notional=2_500.0)
    assert orders[0]["notional"] == pytest.approx(2_500.0, rel=1e-6)


def test_bad_price_skipped():
    tw = pd.Series({"BTCUSD": 0.5})
    assert crypto_exec.plan_orders(tw, {"BTCUSD": float("nan")}, 10_000.0, {}) == []


class _FakeEx:
    """A minimal fake ccxt client for exercising rebalance() offline.

    Serves a USDT quote balance + optional base holdings and last prices; records
    any create_order calls. No network, no keys, never a real order.
    """

    def __init__(self, quote_equity, held_base, prices):
        self._quote_equity = quote_equity
        self._held_base = held_base            # {"BTC": qty, ...}
        self._prices = prices                  # {"BTCUSD": px, ...}
        self.orders: list[tuple] = []
        self.precision_mult = 1.0              # >1 to simulate rounding UP

    def load_markets(self):
        pass

    def fetch_balance(self):
        bal = {"USDT": {"total": self._quote_equity}}
        for base, qty in self._held_base.items():
            bal[base] = {"total": qty}
        return bal

    def fetch_tickers(self, markets):
        out = {}
        for sym, px in self._prices.items():
            out[crypto_exec.crypto_data.SPOT.get(sym, sym)] = {"last": px}
        return out

    def amount_to_precision(self, market, amount):
        return amount * self.precision_mult

    def create_order(self, market, otype, side, amount):
        self.orders.append((market, otype, side, amount))


def test_rebalance_halted_book_places_no_opening_orders(monkeypatch):
    # A flat but drawdown-halted book must open ZERO positions, even live.
    fake = _FakeEx(quote_equity=100_000.0, held_base={}, prices=PRICES)
    monkeypatch.setattr(crypto_exec, "private_exchange", lambda name: fake)
    tw = pd.Series({"BTCUSD": 1.0})
    orders = crypto_exec.rebalance(tw, dry_run=False, max_order_notional=1_000.0,
                                   risk_halted=True)
    assert orders == []
    assert fake.orders == []                   # nothing sent to the exchange


def test_rebalance_live_without_cap_clamps_to_default(monkeypatch, capsys):
    # dry_run=False with no explicit cap must NOT send an uncapped order: a sane
    # default fraction-of-equity cap is applied and each order is clamped to it.
    fake = _FakeEx(quote_equity=100_000.0, held_base={}, prices=PRICES)
    monkeypatch.setattr(crypto_exec, "private_exchange", lambda name: fake)
    tw = pd.Series({"BTCUSD": 1.0})            # wants full 100k of BTC
    orders = crypto_exec.rebalance(tw, dry_run=False, max_order_notional=None)
    cap = crypto_exec.DEFAULT_MAX_NOTIONAL_FRACTION * 100_000.0
    assert len(orders) == 1
    assert orders[0]["notional"] <= cap + 1e-6
    assert "cap" in capsys.readouterr().out.lower()


def test_rebalance_recaps_after_precision_rounding(monkeypatch):
    # amount_to_precision that rounds UP must not smuggle an order back over the
    # cap: the notional is re-validated AFTER rounding.
    fake = _FakeEx(quote_equity=100_000.0, held_base={}, prices=PRICES)
    fake.precision_mult = 1.5                   # rounding inflates the amount
    monkeypatch.setattr(crypto_exec, "private_exchange", lambda name: fake)
    tw = pd.Series({"BTCUSD": 1.0})
    orders = crypto_exec.rebalance(tw, dry_run=False, max_order_notional=2_500.0)
    assert len(orders) == 1
    # real exposure = final (rounded) amount * price must respect the cap, and the
    # reported notional must match that real exposure (not a stale pre-round value)
    assert orders[0]["amount"] * orders[0]["price"] <= 2_500.0 + 1e-6
    assert orders[0]["notional"] <= 2_500.0 + 1e-6


def test_cli_halted_book_no_opening_orders(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.main(["--init", "--account", "chf", "--profile", "hf_crypto"])
    st = fx_book.load_state("chf")
    st["risk_halted"] = True
    fx_book.save_state("chf", st)
    crypto_exec.main(["--account", "chf", "--synthetic", "--equity", "10000", "--bar", "1m"])
    out = capsys.readouterr().out
    assert "RISK-HALTED" in out
    assert "no orders" in out.lower()          # flatten-only on a flat book


def test_cli_synthetic_dry_run(tmp_path, monkeypatch, capsys):
    """End-to-end offline: synthetic prices, flat book, forced dry-run."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.main(["--init", "--account", "chf", "--profile", "hf_crypto"])
    crypto_exec.main(["--account", "chf", "--synthetic", "--equity", "10000", "--bar", "1m"])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "LIVE" not in out.replace("nothing", "")
    assert "Crypto execution" in out
