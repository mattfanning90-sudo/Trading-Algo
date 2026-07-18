"""Backlog F3: live-vs-backtest tracking + attribution."""
import numpy as np
import pandas as pd

from trading_algo import attribution


def _equity_history(vals, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(vals))
    return [[d.strftime("%Y-%m-%d"), float(v)] for d, v in zip(idx, vals)]


def test_total_and_period_returns():
    eh = _equity_history([100.0, 101.0, 102.0])
    assert abs(attribution.total_return(eh) - 0.02) < 1e-9
    rets = attribution.equity_returns(eh)
    assert len(rets) == 2 and abs(rets.iloc[0] - 0.01) < 1e-9


def test_tracking_error_zero_when_identical():
    r = pd.Series(np.linspace(0.001, 0.003, 30),
                  index=pd.bdate_range("2026-01-05", periods=30))
    te = attribution.tracking_error(r, r.copy())
    assert te["tracking_error_bps"] == 0.0 and te["n_obs"] == 30


def test_tracking_error_positive_when_divergent():
    idx = pd.bdate_range("2026-01-05", periods=40)
    rng = np.random.default_rng(0)
    realized = pd.Series(rng.normal(0.0005, 0.01, 40), index=idx)
    predicted = pd.Series(rng.normal(0.0005, 0.01, 40), index=idx)
    te = attribution.tracking_error(realized, predicted)
    assert te["tracking_error_bps"] > 0


def test_realized_cost_drag_from_trades():
    trades = [
        # 10 shares, decision 100 -> fill 100.5 (slippage 5.0), commission 1, stamp 0
        {"region": "US", "side": "BUY", "shares": 10, "decision": 100.0,
         "fill": 100.5, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
    ]
    drag = attribution.realized_cost_drag(trades)
    # cost = 5.0 slippage + 1.0 commission = 6.0 on notional 1005 -> ~59.7 bps
    assert drag["US"]["cost"] == 6.0
    assert abs(drag["US"]["cost_drag_bps"] - 6.0 / 1005 * 1e4) < 0.5


def test_attribution_report_with_predicted_curve():
    eh = _equity_history([100_000, 100_500, 101_000, 100_800])
    state = {"equity_history": eh, "initial_capital_base": 100_000, "trades": [
        {"region": "US", "shares": 5, "decision": 50.0, "fill": 50.1,
         "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"}]}
    predicted = pd.Series([100_000.0, 100_400.0, 100_900.0, 101_100.0],
                          index=pd.to_datetime([d for d, _ in eh]))
    rep = attribution.attribution_report(state, predicted)
    assert "divergence" in rep and "tracking_error_bps" in rep
    assert rep["realized_total_return"] == round(100_800 / 100_000 - 1, 4)
    assert rep["predicted_total_return"] == round(101_100 / 100_000 - 1, 4)
    # realized (+0.8%) < predicted (+1.1%) -> negative divergence
    assert rep["divergence"] < 0
    assert "US" in rep["cost_drag_by_region"]


def test_tracking_alert_flag():
    idx = pd.bdate_range("2026-01-05", periods=60)
    eh = [[d.strftime("%Y-%m-%d"), 100_000 * (1.03 ** (i / 60))]
          for i, d in enumerate(idx)]
    state = {"equity_history": eh, "initial_capital_base": 100_000, "trades": []}
    # a wildly different predicted path -> large tracking error -> alert
    rng = np.random.default_rng(1)
    predicted = pd.Series(100_000 * np.cumprod(1 + rng.normal(0.0, 0.05, len(idx))),
                          index=idx)
    rep = attribution.attribution_report(state, predicted)
    assert rep["tracking_error_bps"] > attribution.TRACKING_ERROR_ALERT_BPS
    assert rep["tracking_alert"] is True


def test_report_without_predicted_curve_still_gives_cost():
    eh = _equity_history([1000, 990])
    state = {"equity_history": eh, "initial_capital_base": 1000, "trades": [
        {"region": "ASX", "shares": 3, "decision": 20.0, "fill": 20.02,
         "commission": 6.0, "currency": "AUD"}]}
    rep = attribution.attribution_report(state, None)
    assert "divergence" not in rep
    assert rep["cost_drag_by_region"]["ASX"]["cost"] > 0
