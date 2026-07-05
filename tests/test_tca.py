"""Backlog F11: execution-quality / transaction-cost analysis."""
from trading_algo import tca
from trading_algo.regions import get_region


def test_implementation_shortfall_signs():
    # BUY filled above the decision price is an adverse cost (positive)
    assert tca.implementation_shortfall(100.0, 101.0, 10, "BUY") == 10.0
    # SELL filled below the decision price is also an adverse cost (positive)
    assert tca.implementation_shortfall(100.0, 99.0, 10, "SELL") == 10.0
    # BUY at exactly the decision price = zero shortfall
    assert tca.implementation_shortfall(100.0, 100.0, 10, "BUY") == 0.0


def test_realized_slippage_bps():
    assert tca.realized_slippage_bps(100.0, 101.0, "BUY") == 100.0   # +1% = 100bps
    assert tca.realized_slippage_bps(100.0, 99.0, "SELL") == 100.0   # adverse for a sell
    assert tca.realized_slippage_bps(0.0, 99.0, "BUY") == 0.0        # guard on bad price


def test_report_rolls_up_per_region():
    trades = [
        {"region": "US", "side": "BUY", "shares": 10, "decision": 100.0, "fill": 100.5, "currency": "USD"},
        {"region": "US", "side": "SELL", "shares": 5, "decision": 200.0, "fill": 199.0, "currency": "USD"},
        {"region": "ASX", "side": "BUY", "shares": 2, "decision": 50.0, "fill": 50.25, "currency": "AUD"},
    ]
    rep = tca.tca_report(trades)
    assert rep["US"]["n_fills"] == 2 and rep["ASX"]["n_fills"] == 1
    assert rep["US"]["implementation_shortfall"] == 10.0   # 10*0.5 + 5*1.0
    assert rep["US"]["modelled_slippage_bps"] == get_region("US").slippage_bps
    assert rep["alerts"] == []


def test_trades_without_decision_are_skipped():
    trades = [{"region": "US", "side": "BUY", "shares": 10, "fill": 100.5}]   # no decision
    rep = tca.tca_report(trades)
    assert [k for k in rep if k != "alerts"] == []


def test_alert_fires_when_realized_exceeds_modelled():
    modelled = get_region("US").slippage_bps
    # every fill slips at 3x the modelled bps, across >= ALERT_MIN_FILLS trades
    bad_bps = modelled * 3 / 1e4
    trades = [{"region": "US", "side": "BUY", "shares": 1, "decision": 100.0,
               "fill": 100.0 * (1 + bad_bps), "currency": "USD"}
              for _ in range(tca.ALERT_MIN_FILLS)]
    rep = tca.tca_report(trades)
    assert rep["US"].get("alert") is True
    assert any("US" in a for a in rep["alerts"])


def test_no_alert_below_min_fills():
    modelled = get_region("US").slippage_bps
    bad_bps = modelled * 3 / 1e4
    trades = [{"region": "US", "side": "BUY", "shares": 1, "decision": 100.0,
               "fill": 100.0 * (1 + bad_bps), "currency": "USD"}
              for _ in range(3)]                       # too few to alert
    rep = tca.tca_report(trades)
    assert "alert" not in rep["US"] and rep["alerts"] == []
