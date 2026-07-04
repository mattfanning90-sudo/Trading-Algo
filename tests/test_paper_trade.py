"""Multi-region paper-trading engine (offline / synthetic)."""
import json

import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade as pt
from trading_algo.regions import get_region


def test_realized_pnl_on_sale():
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAPL": 10},
              "cost_basis": {"AAPL": 100.0}, "realized_pnl": 0.0,
              "last_rebalance_month": None}
    px = pd.Series({"AAPL": 120.0})
    pt.rebalance_sleeve(get_region("US"), sleeve, pd.Series(dtype=float),
                        px, "2026-01-02", [])
    assert sleeve["positions"] == {}            # fully sold
    assert sleeve["realized_pnl"] > 150          # ~ (120−100)·10, minus slippage


def test_legacy_position_without_basis_books_real_pnl():
    """A position opened before cost-basis tracking (no entry in cost_basis)
    must not book $0 on sale — the basis is backfilled from the fills log."""
    trade_log = [{"date": "2026-06-10", "region": "US", "ticker": "AAPL",
                  "side": "BUY", "shares": 10, "fill": 100.0,
                  "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"}]
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAPL": 10},
              "cost_basis": {}, "realized_pnl": 0.0}      # no basis recorded
    px = pd.Series({"AAPL": 120.0})
    pt.rebalance_sleeve(get_region("US"), sleeve, pd.Series(dtype=float),
                        px, "2026-07-01", trade_log)
    assert sleeve["realized_pnl"] > 150      # real gain, not the $0 the bug booked


def test_replay_fills_reconstructs_basis_and_realized():
    trades = [
        {"region": "US", "ticker": "AAA", "side": "BUY", "shares": 10, "fill": 100.0},
        {"region": "US", "ticker": "AAA", "side": "BUY", "shares": 10, "fill": 120.0},
        {"region": "US", "ticker": "AAA", "side": "SELL", "shares": 5, "fill": 150.0},
    ]
    basis, realized = pt.replay_fills(trades)
    assert basis[("US", "AAA")] == pytest.approx(110.0)      # avg cost unchanged by sell
    assert realized["US"] == pytest.approx((150.0 - 110.0) * 5)


def test_min_gap_guard_blocks_near_inception_churn():
    """A book rebalanced late in a month is not churned days later on the 1st,
    but does rebalance once the gap clears."""
    sleeve = {"last_rebalance_month": "2026-06", "last_rebalance_date": "2026-06-28"}
    assert pt._should_rebalance(sleeve, "2026-07-01", "2026-07") is False   # +3 days
    assert pt._should_rebalance(sleeve, "2026-07-20", "2026-07") is True    # +22 days
    # a fresh book (never rebalanced) always trades on its first run
    assert pt._should_rebalance({"last_rebalance_month": None}, "2026-07-01", "2026-07") is True


def test_repair_pnl_rebuilds_fields(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    # simulate legacy state: wipe the tracked P&L fields
    for s in state["sleeves"].values():
        s["cost_basis"] = {}
        s["realized_pnl"] = 0.0
    pt.save_state(account, state)
    pt.repair_pnl(account)
    state = pt.load_state(account)
    for s in state["sleeves"].values():
        for t in s["positions"]:
            assert s["cost_basis"].get(t, 0) > 0     # every holding gets a basis back


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    return "test"


def test_init_splits_into_sleeves(account):
    pt.init_account(account, capital=100_000, synthetic=True)
    state = pt.load_state(account)
    assert set(state["sleeves"]) == set(cfg.ALLOCATIONS)
    assert state["initial_capital_base"] == 100_000
    # each sleeve funded with positive local cash
    for k, s in state["sleeves"].items():
        assert s["cash"] > 0
        assert s["positions"] == {}


def test_daily_run_marks_and_persists(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)

    assert state["equity_history"], "equity history should be recorded"
    date, equity = state["equity_history"][-1]
    assert equity > 0

    # positions are whole-share integers
    for s in state["sleeves"].values():
        for shares in s["positions"].values():
            assert isinstance(shares, int)


def test_state_is_json_roundtrippable(account):
    pt.init_account(account, capital=100_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    raw = json.loads(open(pt._state_file(account)).read())
    assert raw["base_currency"] == cfg.BASE_CURRENCY


def test_force_rebalance_resets_months(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    for s in state["sleeves"].values():
        s["last_rebalance_month"] = None
    pt.save_state(account, state)
    # second run should not raise
    pt.run_daily(account, synthetic=True)


def test_cost_basis_tracked(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    for sleeve in state["sleeves"].values():
        assert "cost_basis" in sleeve
        for t in sleeve["positions"]:          # every holding has a positive avg cost
            assert sleeve["cost_basis"].get(t, 0) > 0


def test_single_region_account(account):
    """A US-only account holds just one sleeve and runs cleanly."""
    pt.init_account(account, capital=1_000, synthetic=True, allocations={"US": 1.0})
    state = pt.load_state(account)
    assert list(state["sleeves"]) == ["US"]
    assert list(state["allocations"]) == ["US"]
    assert abs(state["allocations"]["US"] - 1.0) < 1e-9
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] > 0
    # only USD trades, never ASX/FTSE
    assert all(t["region"] == "US" for t in state["trades"])


def test_init_rejects_unknown_region(account):
    with pytest.raises(SystemExit):
        pt.init_account(account, capital=1000, synthetic=True, allocations={"XYZ": 1.0})


def test_min_size_gate_holds_cash(account):
    # split three ways a tiny account sits below the viability floor → no trades
    pt.init_account(account, capital=300, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert len(state["trades"]) == 0
    assert all(not s["positions"] for s in state["sleeves"].values())


def test_drawdown_halt_liquidates(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)            # opens positions
    state = pt.load_state(account)
    state["risk_halted"] = True                      # force the breaker on
    state["halt_cooldown"] = 5
    pt.save_state(account, state)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert all(not s["positions"] for s in state["sleeves"].values())  # all cash
    assert state["risk_halted"] is True              # still halted (cooldown remains)


def test_micro_account_does_not_crash(account):
    """A tiny account can't afford the full book — must handle gracefully."""
    pt.init_account(account, capital=100, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] >= 0
