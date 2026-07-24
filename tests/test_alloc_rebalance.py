"""Backlog F4: cross-border allocation rebalancing in the paper sim."""
import pandas as pd

from trading_algo import config as cfg
from trading_algo import paper_trade


def _two_sleeve_book():
    # US over target (all cash), ASX under target — a true-up should move cash US->ASX
    sleeves = {
        "US": {"currency": "USD", "cash": 80_000.0, "positions": {}, "cost_basis": {},
               "realized_pnl": 0.0, "last_rebalance_month": "2026-07"},
        "ASX": {"currency": "AUD", "cash": 20_000.0, "positions": {}, "cost_basis": {},
                "realized_pnl": 0.0, "last_rebalance_month": "2026-07"},
    }
    snap = {"USD": 1.5, "AUD": 1.0}          # base per local (AUD base)
    allocations = {"US": 0.5, "ASX": 0.5}
    px = {"US": pd.Series(dtype=float), "ASX": pd.Series(dtype=float)}
    return sleeves, snap, allocations, px


def _base_total(sleeves, snap, px):
    return sum(paper_trade.sleeve_equity_local(sl, px[k]) * snap[sl["currency"]]
               for k, sl in sleeves.items())


def test_true_up_moves_toward_target_and_conserves_minus_spread():
    sleeves, snap, alloc, px = _two_sleeve_book()
    before = _base_total(sleeves, snap, px)          # 80k*1.5 + 20k = 140k base
    cost = paper_trade.rebalance_allocations(sleeves, snap, alloc, px, spread_bps=5.0)
    after = _base_total(sleeves, snap, px)
    # value conserved except the FX spread charged on the crossing amount
    assert cost > 0
    assert abs((before - after) - cost) < 1e-6
    # allocations moved toward 50/50 (US base share was 120/140; now closer to half)
    us_base = paper_trade.sleeve_equity_local(sleeves["US"], px["US"]) * snap["USD"]
    assert us_base < before * 0.6                    # was ~0.857 of total, now nearer 0.5


def test_no_move_when_already_on_target():
    sleeves = {
        "US": {"currency": "USD", "cash": 50_000.0, "positions": {}, "cost_basis": {},
               "realized_pnl": 0.0},
        "ASX": {"currency": "AUD", "cash": 75_000.0, "positions": {}, "cost_basis": {},
                "realized_pnl": 0.0},
    }
    snap = {"USD": 1.5, "AUD": 1.0}   # US=75k base, ASX=75k base -> already 50/50
    px = {"US": pd.Series(dtype=float), "ASX": pd.Series(dtype=float)}
    cost = paper_trade.rebalance_allocations(sleeves, snap, {"US": 0.5, "ASX": 0.5}, px)
    assert cost == 0.0


def test_bounded_by_donor_cash():
    # US is over target but fully invested (no cash) -> cannot donate
    sleeves = {
        "US": {"currency": "USD", "cash": 0.0, "positions": {"AAPL": 100},
               "cost_basis": {"AAPL": 800.0}, "realized_pnl": 0.0},
        "ASX": {"currency": "AUD", "cash": 20_000.0, "positions": {}, "cost_basis": {},
                "realized_pnl": 0.0},
    }
    snap = {"USD": 1.5, "AUD": 1.0}
    px = {"US": pd.Series({"AAPL": 900.0}), "ASX": pd.Series(dtype=float)}
    cost = paper_trade.rebalance_allocations(sleeves, snap, {"US": 0.5, "ASX": 0.5}, px)
    assert cost == 0.0                                # nothing could move (no donor cash)


def test_default_off_in_run_daily(monkeypatch, tmp_path):
    """With the flag off (default), a paper run performs no allocation true-up."""
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    assert cfg.PAPER_ALLOCATION_REBALANCE is False
    paper_trade.init_account("t", 100_000, synthetic=True)   # all 3 sleeves
    paper_trade.run_daily("t", synthetic=True)
    state = paper_trade.load_state("t")
    assert state.get("fx_rebalance_cost", 0.0) == 0.0        # no true-up happened
