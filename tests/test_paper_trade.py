"""Multi-region paper-trading engine (offline / synthetic)."""
import json

import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade as pt
from trading_algo import pnl
from trading_algo.regions import get_region


def test_sell_stamps_actual_realized_pnl():
    """Each sell is stamped with its real realised P&L, computed from the actual
    lots it consumes in the fills ledger — not from a stored tally."""
    trade_log = [{"date": "2026-06-10", "region": "US", "ticker": "AAPL",
                  "side": "BUY", "shares": 10, "fill": 100.0,
                  "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"}]
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAPL": 10},
              "cost_basis": {}, "realized_pnl": 0.0, "last_rebalance_month": None}
    px = pd.Series({"AAPL": 120.0})
    pt.rebalance_sleeve(get_region("US"), sleeve, pd.Series(dtype=float),
                        px, "2026-07-01", trade_log)
    assert sleeve["positions"] == {}                 # fully sold
    sell = trade_log[-1]
    assert sell["side"] == "SELL"
    assert sell["entry"] == pytest.approx(100.0)     # actual FIFO cost basis
    assert sell["realized"] > 150                    # ~ (120−100)·10, minus costs/slippage
    # the vestigial stored fields are NOT used as the source of truth
    assert sleeve["realized_pnl"] == 0.0 and sleeve["cost_basis"] == {}


def test_min_gap_guard_blocks_near_inception_churn():
    """A book rebalanced late in a month is not churned days later on the 1st,
    but does rebalance once the gap clears."""
    sleeve = {"last_rebalance_month": "2026-06", "last_rebalance_date": "2026-06-28"}
    assert pt._should_rebalance(sleeve, "2026-07-01", "2026-07") is False   # +3 days
    assert pt._should_rebalance(sleeve, "2026-07-20", "2026-07") is True    # +22 days
    # a fresh book (never rebalanced) always trades on its first run
    assert pt._should_rebalance({"last_rebalance_month": None}, "2026-07-01", "2026-07") is True


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


def test_cost_basis_derived_from_fills(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    # The stored fields stay at their empty defaults — they are not the source of
    # truth. Basis and realised P&L are derived from the fills ledger.
    for sleeve in state["sleeves"].values():
        assert sleeve.get("cost_basis") == {} and sleeve.get("realized_pnl") == 0.0
    basis = pnl.open_basis(pnl.build_lots(state["trades"])[0])
    for k, sleeve in state["sleeves"].items():
        for t in sleeve["positions"]:          # every holding traces to a real buy
            assert basis.get((k, t), 0) > 0


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


def test_cooldown_counts_market_days_not_runs(account):
    """The engine fires several times a day; the drawdown cooldown must decrement
    per distinct report date, not per run."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    state["risk_halted"] = True
    state["halt_cooldown"] = 3
    state.pop("halt_last_day", None)
    pt.save_state(account, state)
    # Two runs land on the SAME synthetic report date -> one day of cooldown.
    pt.run_daily(account, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["halt_cooldown"] == 2               # dropped by ONE, not two
    assert state["risk_halted"] is True


def test_micro_account_does_not_crash(account):
    """A tiny account can't afford the full book — must handle gracefully."""
    pt.init_account(account, capital=100, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] >= 0


_KNOWN_STATUSES = {
    "rebalanced", "held", "cash:idle", "cash:halted", "cash:below-min",
    "cash:regime-off", "cash:no-eligible-names", "cash:data-quality",
    "cash:insufficient-names",
}


def test_every_sleeve_records_a_status(account):
    """Each run stamps every sleeve with a machine-readable reason for its state,
    so an idle sleeve (e.g. ASX sitting in cash) is diagnosable, not silent."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    for k, s in state["sleeves"].items():
        st = s.get("last_status")
        assert st is not None, f"{k} has no last_status"
        assert st["status"] in _KNOWN_STATUSES, f"{k}: unknown status {st['status']}"
        assert st["date"] == state["equity_history"][-1][0]
        # a sleeve in cash must record since-when; a working sleeve must not
        if st["status"].startswith("cash"):
            assert st["flat_since"], f"{k} flat but no flat_since"
        else:
            assert st["flat_since"] is None


def test_empty_target_reason_distinguishes_regime_off():
    """A risk-off index yields 'regime-off'; a risk-on index with no qualifying
    name yields 'no-eligible-names' — the distinction the old blanket
    'regime RISK-OFF' print could not make."""
    from trading_algo.regions import get_region
    p = get_region("ASX").params
    idx = pd.Series(range(200, 100, -1),
                    index=pd.bdate_range("2025-01-01", periods=100))  # falling → risk-off
    prices = pd.DataFrame({"AAA": range(100)}, index=idx.index)
    assert pt._empty_target_reason(prices, idx, p, eligible=None) == "regime-off"
    # everything frozen by data quality → 'data-quality', regardless of regime
    assert pt._empty_target_reason(prices, idx, p, eligible=set()) == "data-quality"


def _break_one_pair(monkeypatch, currency="USD"):
    """Simulate a transient single-pair FX download failure: the given
    currency's column comes back all-NaN (as when its Yahoo pair 403s while the
    others succeed), which is exactly what poisoned the US sleeve's equity."""
    real = pt.fx.synthetic_fx

    def broken(currencies, *a, **k):
        tbl = real(currencies, *a, **k)
        tbl[currency] = float("nan")
        return tbl

    monkeypatch.setattr(pt.fx, "synthetic_fx", broken)


def test_fx_snapshot_carries_forward_a_failed_rate(monkeypatch):
    """A NaN FX rate (failed pair fetch) must not surface as NaN — the last
    known-good rate is carried forward instead."""
    _break_one_pair(monkeypatch, "USD")
    prev = {"AUD": 1.0, "USD": 1.50, "GBP": 1.90}
    snap = pt.fx_snapshot(synthetic=True, prev=prev)
    assert snap["USD"] == pytest.approx(1.50)     # carried forward, not NaN
    assert snap["AUD"] == 1.0
    assert snap["GBP"] == snap["GBP"]             # the healthy pair is unaffected


def test_failed_fx_rate_never_poisons_equity(account, monkeypatch):
    """End-to-end: a transient USD-pair failure must never write a NaN FX rate
    or NaN equity into state — the regression behind the `full` book's NaN AUM."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)                 # establishes a good snapshot
    good_usd = pt.load_state(account)["fx_snapshot"]["USD"]
    assert good_usd == good_usd                            # sanity: not NaN

    _break_one_pair(monkeypatch, "USD")
    pt.run_daily(account, synthetic=True)                  # USD pair now fails
    state = pt.load_state(account)

    # The snapshot (overwritten every run) must never contain a NaN rate.
    for ccy, rate in state["fx_snapshot"].items():
        assert rate == rate, f"NaN FX rate persisted for {ccy}"
    assert state["fx_snapshot"]["USD"] == pytest.approx(good_usd)
    # No NaN ever enters the persisted equity/sleeve history.
    for date, eq in state["equity_history"]:
        assert eq == eq, f"NaN equity persisted on {date}"
