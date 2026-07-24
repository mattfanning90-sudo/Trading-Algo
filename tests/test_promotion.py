"""Backlog F10: paper->live promotion gate."""
import pytest

from trading_algo import config as cfg
from trading_algo import execution_ibkr, promotion


def _ready_state(months=8):
    trades = [{"date": f"2026-{m:02d}-15", "region": "US"} for m in range(1, months + 1)]
    return {
        "account": "t", "schema_version": 2, "base_currency": "AUD",
        "initial_capital_base": 100_000, "allocations": {"US": 1.0},
        "sleeves": {"US": {"currency": "USD", "cash": 100_000.0, "positions": {},
                           "cost_basis": {}, "realized_pnl": 0.0,
                           "last_rebalance_month": "2026-08"}},
        "trades": trades,
        "equity_history": [["2026-08-15", 105_000.0]],
        "risk_halted": False,
    }


def _evidence():
    return {"dsr": 0.97, "pbo": 0.2, "tracking_error_bps": 120.0}


def test_fully_qualified_book_is_ready():
    v = promotion.promotion_check(_ready_state(), **_evidence())
    assert v["ready"] is True
    assert all(v["checks"].values())


def test_short_track_record_blocks():
    v = promotion.promotion_check(_ready_state(months=3), **_evidence())
    assert v["ready"] is False and v["checks"]["track_record"] is False
    assert any("rebalance month" in r for r in v["reasons"])


def test_missing_overfitting_evidence_blocks():
    v = promotion.promotion_check(_ready_state())          # no dsr/pbo/tracking
    assert v["ready"] is False
    assert v["checks"]["overfitting"] is False and v["checks"]["tracking"] is False


def test_halted_book_blocks():
    s = _ready_state()
    s["risk_halted"] = True
    v = promotion.promotion_check(s, **_evidence())
    assert v["ready"] is False and v["checks"]["not_halted"] is False


def test_bad_dsr_blocks():
    ev = _evidence()
    ev["dsr"] = 0.80                                        # below the floor
    assert promotion.promotion_check(_ready_state(), **ev)["checks"]["overfitting"] is False


def test_require_live_ok_raises_when_not_ready():
    with pytest.raises(promotion.PromotionError):
        promotion.require_live_ok(_ready_state(months=1))


def test_require_live_ok_passes_when_ready():
    v = promotion.require_live_ok(_ready_state(), **_evidence())
    assert v["ready"] is True and v["override"] is False


def test_override_allows_unready_book_but_is_recorded():
    v = promotion.require_live_ok(_ready_state(months=1), override=True)
    assert v["override"] is True and v["ready"] is False   # audited override


def test_execution_blocks_live_order_on_unpromoted_book(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(cfg, "PROMOTION_GATE", True)
    # LIVE port + real order + an unqualified book must raise BEFORE connecting
    with pytest.raises(promotion.PromotionError):
        execution_ibkr.rebalance("US", pd.Series({"AAPL": 0.5}), dry_run=False,
                                 port=execution_ibkr.LIVE_PORT,
                                 promotion_state=_ready_state(months=1))


def test_execution_dry_run_never_gated(monkeypatch):
    """A dry-run preview must not trigger the gate (it places no real order)."""
    import pandas as pd
    monkeypatch.setattr(cfg, "PROMOTION_GATE", True)
    # dry_run=True on the LIVE port would try to connect; we only assert the gate
    # itself doesn't raise. Connection will fail, but not with PromotionError.
    try:
        execution_ibkr.rebalance("US", pd.Series({"AAPL": 0.5}), dry_run=True,
                                 port=execution_ibkr.LIVE_PORT,
                                 promotion_state=_ready_state(months=1))
    except promotion.PromotionError:
        pytest.fail("dry-run must not be gated")
    except Exception:
        pass   # ib_insync connection failure is expected/acceptable here
