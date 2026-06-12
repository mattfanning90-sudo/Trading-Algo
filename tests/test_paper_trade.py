"""Multi-region paper-trading engine (offline / synthetic)."""
import json

import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade as pt


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


def test_micro_account_does_not_crash(account):
    """A tiny account can't afford the full book — must handle gracefully."""
    pt.init_account(account, capital=100, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] >= 0
