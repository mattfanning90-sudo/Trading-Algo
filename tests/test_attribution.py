"""Backlog F3: live-vs-backtest tracking + attribution."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import attribution, metrics


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


def test_tracking_error_is_the_metrics_annualised_vol():
    """The annualised std of the difference IS annualised_vol — not a second
    copy of sqrt(252) living in attribution.py."""
    idx = pd.bdate_range("2026-01-05", periods=40)
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0004, 0.009, 40), index=idx)
    p = pd.Series(rng.normal(0.0004, 0.009, 40), index=idx)
    te = attribution.tracking_error(r, p)
    expected = metrics.annualised_vol(r - p,
                                      periods_per_year=metrics.ANNUALIZATION)
    assert te["tracking_error_bps"] == round(expected * 1e4, 1)
    assert te["n_obs"] == 40


def test_tracking_error_honours_an_explicit_convention():
    """The public signature is unchanged: callers can still pass their own."""
    idx = pd.date_range("2026-01-05", periods=40, freq="h")
    rng = np.random.default_rng(4)
    r = pd.Series(rng.normal(0.0, 0.002, 40), index=idx)
    p = pd.Series(rng.normal(0.0, 0.002, 40), index=idx)
    daily = attribution.tracking_error(r, p)["tracking_error_bps"]
    hourly = attribution.tracking_error(r, p, periods_per_year=8766)["tracking_error_bps"]
    assert hourly > daily * 5          # sqrt(8766/252) ~ 5.9


def test_no_second_annualisation_constant_in_attribution():
    """One definition of 252 in the repo, and it is metrics.ANNUALIZATION."""
    assert not hasattr(attribution, "TRADING_DAYS")


def _bridge_state(end_equity: float) -> dict:
    """A book whose ONLY divergence from the backtest is the commission floor.

    One ASX buy: 10 shares at 10.00 = 100 notional. The backtest charges
    commission_bps (8bps) = 0.08; the paper book paid the A$5 min_commission
    floor. The excess is 4.92 AUD, i.e. 4.92e-5 of a 100,000 AUD book.
    decision == fill, so there is no implementation shortfall.
    """
    return {
        "initial_capital_base": 100_000.0,
        "base_currency": "AUD",
        "fx_snapshot": {"AUD": 1.0},
        "equity_history": [["2026-01-05", 100_000.0], ["2026-06-30", end_equity]],
        "trades": [{"date": "2026-02-02", "region": "ASX", "ticker": "BHP.AX",
                    "side": "BUY", "shares": 10, "decision": 10.0, "fill": 10.0,
                    "commission": 5.0, "stamp_duty": 0.0, "currency": "AUD"}],
    }


def _bridge_predicted() -> pd.Series:
    return pd.Series([100_000.0, 101_000.0],
                     index=pd.to_datetime(["2026-01-05", "2026-06-30"]))


def test_bridge_reports_exactly_the_named_lines():
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    assert tuple(rep["lines"]) == attribution.BRIDGE_LINES
    assert rep["start"] == "2026-01-05" and rep["end"] == "2026-06-30"
    # dividends and cash interest are not credited to the paper book yet; they
    # must read zero AND say why, rather than silently absorbing the divergence.
    assert rep["lines"]["dividends"] == 0.0
    assert rep["lines"]["cash_interest"] == 0.0
    assert rep["notes"]["dividends"] and rep["notes"]["cash_interest"]


def test_bridge_identity_holds_when_every_cause_is_named():
    """backtest_return - sum(lines) - paper_return == residual, and the residual
    is inside tolerance when the only divergence is an explained one."""
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    assert rep["lines"]["commission_floor"] == pytest.approx(4.92e-5, abs=1e-12)
    identity = (rep["backtest_return"] - sum(rep["lines"].values())
                - rep["paper_return"])
    assert rep["residual"] == pytest.approx(identity, abs=1e-15)
    assert abs(rep["residual"]) <= attribution.BRIDGE_TOLERANCE
    assert rep["identity_ok"] is True


def test_bridge_puts_an_unexplained_gap_in_the_residual():
    """A divergence no line accounts for must land in residual and fail the
    identity — never be absorbed into a line to make the table balance."""
    rep = attribution.reconcile(_bridge_state(100_500.0), _bridge_predicted())
    assert rep["identity_ok"] is False
    assert rep["residual"] == pytest.approx(0.01 - 4.92e-5 - 0.005, abs=1e-12)


def test_bridge_counts_the_floor_excess_only_above_the_modelled_bps():
    """A trade large enough that commission_bps exceeds the floor contributes
    nothing: there is no divergence from the backtest's model to explain."""
    state = _bridge_state(101_000.0)
    state["trades"][0].update({"shares": 100_000, "fill": 10.0,
                               "commission": 800.0})   # 1,000,000 * 8bps
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == 0.0
    # ...and the zero is the comparison, not a skipped trade: A$5 over the
    # modelled 800 on the same fill is counted.
    state["trades"][0]["commission"] = 805.0
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == pytest.approx(5e-5, abs=1e-12)


def test_bridge_refuses_a_window_it_cannot_walk():
    state = _bridge_state(100_995.08)
    apart = pd.Series([100_000.0, 101_000.0],
                      index=pd.to_datetime(["2027-01-05", "2027-06-30"]))
    with pytest.raises(ValueError, match="common"):
        attribution.reconcile(state, apart)
    # A freshly initialised book has an EMPTY equity_history: refuse it too,
    # rather than report a bridge over nothing.
    with pytest.raises(ValueError, match="common"):
        attribution.reconcile({**state, "equity_history": []}, _bridge_predicted())


def test_bridge_nets_the_slippage_both_engines_charge():
    """The paper sim fills at decision * (1 + slippage_bps), exactly the slippage
    the backtest's round_trip_cost_rate charges. Both engines pay it, so
    fill_convention reads zero and the paid amount goes in its note; only a
    fill away from the modelled slippage puts one engine ahead of the other."""
    state = _bridge_state(100_995.08)
    state["trades"][0]["fill"] = 10.01          # ASX models 10bps a side
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["fill_convention"] == pytest.approx(0.0, abs=1e-12)
    assert "0.10 AUD" in rep["notes"]["fill_convention"]
    # A live fill 5bps worse than modelled: 10 shares * 10.00 * 5bps = A$0.05.
    state["trades"][0]["fill"] = 10.015
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["fill_convention"] == pytest.approx(0.05 / 100_000, abs=1e-12)


def test_bridge_leaves_out_trades_already_inside_the_start_equity():
    """equity_history is stamped AFTER a run's trades, so a trade dated on the
    window's first day is already inside the start equity. Counting its cost
    again would double it — and on a real book that day is the build-out."""
    state = _bridge_state(100_995.08)
    state["trades"][0]["date"] = "2026-01-05"
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == 0.0


def test_bridge_converts_local_costs_at_the_books_fx_snapshot():
    """A FTSE buy pays its floor and stamp duty in GBP; the bridge reports both
    in the base currency via the snapshot's base-per-local multiplier. Stamp
    duty is charged by both engines, so its line is zero and its note says
    what was paid."""
    state = _bridge_state(100_995.08)
    state["fx_snapshot"] = {"AUD": 1.0, "GBP": 2.0}
    state["trades"][0].update({"region": "FTSE", "ticker": "SHEL.L",
                               "commission": 1.0, "stamp_duty": 0.5,
                               "currency": "GBP"})
    rep = attribution.reconcile(state, _bridge_predicted())
    # FTSE: £1 floor vs 5bps of £100 = £0.05 modelled -> £0.95 = A$1.90.
    assert rep["lines"]["commission_floor"] == pytest.approx(1.9e-5, abs=1e-12)
    assert rep["lines"]["stamp_duty"] == 0.0
    assert "1.00 AUD" in rep["notes"]["stamp_duty"]
