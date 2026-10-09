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


def test_format_bridge_prints_every_line_and_the_verdict():
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD", 100_000.0)
    for name in attribution.BRIDGE_LINES:
        assert name.replace("_", " ") in text, f"{name} missing from the table"
    assert "backtest return" in text and "paper return" in text
    assert "residual" in text
    assert "identity HOLDS" in text
    assert "AUD" in text
    # the commission floor cost A$4.92 on a 100,000 book -> the money column
    assert "5" in text


def test_format_bridge_says_so_when_the_identity_fails():
    rep = attribution.reconcile(_bridge_state(100_500.0), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD", 100_000.0)
    assert "identity DOES NOT HOLD" in text
    assert "note [dividends]" in text       # the unnamed lines explain themselves


def _row(text: str, name: str) -> list[str]:
    return next(ln for ln in text.splitlines() if ln.startswith(name)).split()


def test_format_bridge_annualises_in_calendar_time_through_metrics():
    """bps/yr is the window's return over metrics.years_between — calendar time,
    so a book that skipped a run or a backtest on a union-of-exchanges calendar
    reads the same length for the same window. 2026-01-05 -> 2026-06-30 is 176
    days: the 1% backtest return is 1e-2 / (176/365.25) = 207.5 bps/yr."""
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD", 100_000.0)
    assert _row(text, "backtest return")[-2:] == ["208", "1,000"]
    assert _row(text, "paper return")[-1] == "995"


def test_format_bridge_without_a_capital_prints_no_money_column():
    """initial=0.0 means 'no money column' (the docstring), not a column of
    zeros labelled with a currency — which would read as zero money."""
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD")
    assert "AUD" not in text.splitlines()[2]           # the header row
    assert _row(text, "backtest return")[-2:] == ["1.0000%", "208"]


def test_bridge_reports_the_equity_its_lines_are_fractions_of():
    """Every line is a fraction of the book's equity at the WINDOW start — after
    the build-out's costs, so not initial_capital_base. The money column needs
    that denominator to print the book's actual base-currency amounts."""
    state = _bridge_state(100_995.08)
    state["equity_history"][0][1] = 99_830.84
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["start_equity"] == 99_830.84


# --- the holdings lines: exposure_gap, cash_interest, rebalance_timing -------
# A two-sleeve AUD book over three consecutive sessions. ASX is identical in
# paper and backtest; every divergence is put in the US sleeve, which holds
# its cash in USD at a flat 1.5 AUD per USD, so base and local returns agree
# and a wrong conversion of the cash shows up as a wrong invested fraction.
_D = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04"])
_FX = 1.5
_ASX_RETS = (0.02, 0.01)


def _paper_book(us_invested, us_rets, *, buy_on_d1=0.0, positions=None):
    """Paper: each sleeve starts at 50,000 AUD. The US sleeve holds
    `us_invested` of it in stock earning `us_rets`, the rest as USD cash;
    `buy_on_d1` AUD of that cash goes into stock at the second session's
    close. The stored sleeve is the book AFTER every trade, as on disk, so its
    positions agree with the ledger: 10 AAA held from the start if invested,
    10 more if it bought."""
    asx = [50_000.0]
    for r in _ASX_RETS:
        asx.append(asx[-1] * (1 + r))
    cash, held = 50_000.0 * (1 - us_invested), 50_000.0 * us_invested
    us = [cash + held]
    for i, r in enumerate(us_rets):
        held *= 1 + r
        if i == 0 and buy_on_d1:
            held, cash = held + buy_on_d1, cash - buy_on_d1
        us.append(cash + held)
    trades = []
    if buy_on_d1:
        px = buy_on_d1 / _FX / 10
        trades.append({"date": "2026-03-03", "region": "US", "ticker": "AAA",
                       "side": "BUY", "shares": 10, "decision": px, "fill": px,
                       "commission": 0.0, "stamp_duty": 0.0, "currency": "USD"})
    days = [d.strftime("%Y-%m-%d") for d in _D]
    return {
        "initial_capital_base": 100_000.0, "base_currency": "AUD",
        "fx_snapshot": {"AUD": 1.0, "USD": _FX},
        "allocations": {"ASX": 0.5, "US": 0.5},
        "sleeves": {"ASX": {"currency": "AUD", "cash": 0.0,
                            "positions": {"BHP.AX": 100}},
                    "US": {"currency": "USD", "cash": cash / _FX,
                           "positions": positions or {
                               "AAA": 10 * bool(us_invested) + 10 * bool(buy_on_d1)}}},
        "equity_history": [[d, a + u] for d, a, u in zip(days, asx, us)],
        "sleeve_history": [{"date": d, "ASX": a, "US": u}
                           for d, a, u in zip(days, asx, us)],
        "trades": trades,
    }


def _backtest(us_rets, us_weight=1.0):
    """A run_portfolio_backtest-shaped result. Each sleeve's daily return is
    its weight times its holdings' return plus the backtest's idle-cash credit
    — the same composition backtest.py books. Two sessions cannot reach a
    month-end true-up, so the portfolio is the start-weighted hold."""
    from trading_algo import config as cfg
    from trading_algo import fees

    def sleeve(rets, weight, name):
        credit = (fees.idle_cash_credit(weight, 1, cfg.CASH_RATE_ANNUAL)
                  if cfg.CREDIT_IDLE_CASH else 0.0)
        r = pd.Series([0.0, *[weight * h + credit for h in rets]], index=_D)
        return {"returns": r, "base_returns": r.copy(),
                "costs": pd.Series(dtype=float),
                "weights": {d: pd.Series({name: weight}) for d in _D}}

    sleeves = {"ASX": sleeve(_ASX_RETS, 1.0, "BHP.AX"),
               "US": sleeve(us_rets, us_weight, "AAA")}
    equity = 50_000.0 * sum((1 + s["returns"]).cumprod() for s in sleeves.values())
    return {"equity": equity, "sleeves": sleeves}


def _aud(rep, line):
    """A line in the money column's units: AUD on the window-start equity."""
    return rep["lines"][line] * rep["start_equity"]


def test_exposure_gap_is_the_half_the_paper_sleeve_did_not_hold():
    """Backtest US fully invested: 50,000 x 1.10 x 0.95 = 52,250. Paper US half
    in cash: 25,000 + 25,000 x 1.10 x 0.95 = 51,125. The holdings earned the
    same return in both, so the whole 1,125.00 AUD is the half the paper did
    not hold — exposure_gap, to the cent, and nothing for rebalance_timing.

    (Summing (g_bt - g_p) * r day by day without linking gives 1,309.52 here
    and pushes -184.52 into rebalance_timing in a case with no timing at all.)"""
    rep = attribution.reconcile(_paper_book(0.5, (0.10, -0.05)),
                                _backtest((0.10, -0.05))["equity"],
                                backtest=_backtest((0.10, -0.05)))
    assert _aud(rep, "exposure_gap") == pytest.approx(1_125.00, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)
    assert _aud(rep, "cash_interest") == pytest.approx(0.0, abs=0.005)
    assert rep["identity_ok"] is True
    assert rep["residual"] == pytest.approx(0.0, abs=1e-12)


def test_matching_exposure_with_different_holdings_is_all_rebalance_timing():
    """Both fully invested; the paper's names earned +8% where the backtest's
    earned +10%. 50,000 x 1.10 x 0.95 = 52,250 against 50,000 x 1.08 x 0.95 =
    51,300: the 950.00 AUD gap is what was held, not how much, so it is all
    rebalance_timing and exposure_gap is zero."""
    bt = _backtest((0.10, -0.05))
    rep = attribution.reconcile(_paper_book(1.0, (0.08, -0.05)), bt["equity"],
                                backtest=bt)
    assert _aud(rep, "exposure_gap") == pytest.approx(0.0, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(950.00, abs=0.005)
    assert rep["identity_ok"] is True


def test_the_cash_path_is_rebuilt_from_the_ledger(monkeypatch):
    """The stored sleeve is the book AFTER every trade: fully invested. The
    paper was half in cash until it bought at the second session's close, so
    it missed half of the +10% day only: backtest 52,250 against paper
    (25,000 + 27,500) x 0.95 = 49,875. All 2,375.00 AUD is exposure_gap. Read
    off today's cash alone, the sleeve looks fully invested on both days and
    the gap would be mislabelled timing."""
    bt = _backtest((0.10, -0.05))
    rep = attribution.reconcile(
        _paper_book(0.5, (0.10, -0.05), buy_on_d1=25_000.0), bt["equity"],
        backtest=bt)
    assert _aud(rep, "exposure_gap") == pytest.approx(2_375.00, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)


def test_cash_interest_is_the_backtests_credit_on_its_idle_cash(monkeypatch):
    """Both engines hold the US sleeve half in cash and its stock earns
    nothing. The backtest credits fees.idle_cash_credit on the idle half —
    0.5 x 7.3% / 365 = 1bp a day — and the paper book credits nothing yet:
    25,000 x ((1.0001)^2 - 1) ... of the 50,000 sleeve = 10.0005 AUD, all of
    it cash_interest. Priced into the holdings return instead, it would read
    as exposure (or, stripped and dropped, as timing)."""
    from trading_algo import config as cfg
    monkeypatch.setattr(cfg, "CREDIT_IDLE_CASH", True)
    monkeypatch.setattr(cfg, "CASH_RATE_ANNUAL", 0.073)
    bt = _backtest((0.0, 0.0), us_weight=0.5)
    rep = attribution.reconcile(_paper_book(0.5, (0.0, 0.0)), bt["equity"],
                                backtest=bt)
    assert _aud(rep, "cash_interest") == pytest.approx(
        50_000.0 * (1.0001 ** 2 - 1), abs=0.005)
    assert _aud(rep, "exposure_gap") == pytest.approx(0.0, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)
    assert rep["identity_ok"] is True


def test_exposure_is_priced_without_the_backtests_cash_credit(monkeypatch):
    """The shape of `full`'s ASX sleeve: the backtest half invested, the paper
    all cash. The backtest's US sleeve makes 0.5 x +10% plus 1bp of credit,
    then 0.5 x -5% plus 1bp: 50,000 x 1.0501 x 0.9751 = 51,197.63 against the
    paper's 50,000. Of that 1,197.63 AUD, the credit is day 1's 5.00 carried
    through day 2's -2.49% (4.88) plus day 2's 5.00 = 9.88 of cash_interest;
    exposure_gap is the other 1,187.75. Dividing the backtest's WHOLE return
    by its invested half would double the credit into exposure instead."""
    from trading_algo import config as cfg
    monkeypatch.setattr(cfg, "CREDIT_IDLE_CASH", True)
    monkeypatch.setattr(cfg, "CASH_RATE_ANNUAL", 0.073)
    bt = _backtest((0.10, -0.05), us_weight=0.5)
    rep = attribution.reconcile(_paper_book(0.0, (0.0, 0.0)), bt["equity"],
                                backtest=bt)
    assert _aud(rep, "cash_interest") == pytest.approx(
        50_000.0 * 0.0001 * (0.9751 + 1), abs=0.005)
    assert _aud(rep, "exposure_gap") == pytest.approx(
        50_000.0 * (0.5 * 0.10 * 0.9751 - 0.5 * 0.05), abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)
    assert rep["identity_ok"] is True


def test_a_day_is_priced_at_the_book_held_through_it(monkeypatch):
    """backtest.py records weights[D] as the book at D's CLOSE, after any fill
    on D, so day t was earned by the book recorded at the PREVIOUS bar. Here
    the backtest tops its US sleeve up from half to fully invested at the
    first session's close and the paper stays half invested. Day 1 the two
    held the same half: 1bp of credit on the backtest's idle half (5.00,
    carried through -5%: 4.75) and no exposure. Day 2 the backtest was all in
    and the paper 27,500 / 52,500: (1 - 0.5238) x -5% on 52,500 = -1,250.00
    of exposure. Backtest 50,000 x 1.0501 x 0.95 = 49,879.75 against paper
    51,125 leaves nothing for timing. Read off the day's own close book, day
    1 would look fully invested: no credit, and +1,189.88 of exposure."""
    from trading_algo import config as cfg
    monkeypatch.setattr(cfg, "CREDIT_IDLE_CASH", True)
    monkeypatch.setattr(cfg, "CASH_RATE_ANNUAL", 0.073)
    bt = _backtest((0.10, -0.05), us_weight=0.5)
    us = bt["sleeves"]["US"]
    us["weights"] = {_D[0]: pd.Series({"AAA": 0.5}),
                     _D[1]: pd.Series({"AAA": 1.0}), _D[2]: pd.Series({"AAA": 1.0})}
    us["returns"] = pd.Series([0.0, 0.5 * 0.10 + 0.0001, -0.05], index=_D)
    us["base_returns"] = us["returns"].copy()
    bt["equity"] = 50_000.0 * sum((1 + s["returns"]).cumprod()
                                  for s in bt["sleeves"].values())
    rep = attribution.reconcile(_paper_book(0.5, (0.10, -0.05)), bt["equity"],
                                backtest=bt)
    assert _aud(rep, "cash_interest") == pytest.approx(4.75, abs=0.005)
    assert _aud(rep, "exposure_gap") == pytest.approx(-1_250.00, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)


def test_the_backtests_trading_cost_is_not_exposure():
    """The half-invested case again, but the backtest pays 10bps of NAV to
    trade on day 1. Its holdings still earned +10%, so exposure_gap is still
    1,125.00; the cost the paper did not pay (10bps carried through -5%:
    50,000 x 0.001 x 0.95 = 47.50) is a turnover difference, and lands in
    rebalance_timing as the paper's gain."""
    bt = _backtest((0.10, -0.05))
    us = bt["sleeves"]["US"]
    us["costs"] = pd.Series({_D[1]: 0.001})
    us["returns"].loc[_D[1]] -= 0.001
    us["base_returns"] = us["returns"].copy()
    bt["equity"] = 50_000.0 * sum((1 + s["returns"]).cumprod()
                                  for s in bt["sleeves"].values())
    rep = attribution.reconcile(_paper_book(0.5, (0.10, -0.05)), bt["equity"],
                                backtest=bt)
    assert _aud(rep, "exposure_gap") == pytest.approx(1_125.00, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(-47.50, abs=0.005)
    assert rep["identity_ok"] is True


def test_an_all_cash_close_gives_the_sleeve_its_own_exact_rate():
    """The paper keeps only its LATEST rate, which can be days past the window
    (on `full` it came from a run that stamped no equity). On a close where a
    sleeve holds nothing, its base value IS cash x rate, so value / cash is
    that day's exact rate — the sleeve's own record, preferred over the
    snapshot. Here the snapshot is wrong (2.0) and the US sleeve starts all
    cash at 1.5, buying half at the second close: backtest 52,250 against
    paper 25,000 + 25,000 x 0.95 = 48,750, all 3,500.00 AUD of it exposure."""
    bt = _backtest((0.10, -0.05))
    state = _paper_book(0.0, (0.10, -0.05), buy_on_d1=25_000.0)
    state["fx_snapshot"]["USD"] = 2.0
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert _aud(rep, "exposure_gap") == pytest.approx(3_500.00, abs=0.005)
    assert _aud(rep, "rebalance_timing") == pytest.approx(0.0, abs=0.005)


def test_a_flat_close_is_uninvested_whatever_the_rate():
    """The US sleeve holds nothing throughout, while its 33,333.33 USD moves
    from 1.5 to 1.6 AUD on the paper's own marks and the backtest's FX path
    stays flat — the two disagree, as they do on `full` by up to 1.6%. A close
    with no positions is g = 0 by definition, never a rate's echo: exposure is
    the backtest's holdings linked over the paper's growth, 50,000 x (0.10 x
    0.95 - 0.05 x 53,333.33 / 50,000) = 2,083.33 AUD. The FX gain the paper
    made is not exposure; it stays in rebalance_timing."""
    bt = _backtest((0.10, -0.05))
    state = _paper_book(0.0, (0.0, 0.0))
    us = 50_000.0 / _FX * 1.6
    for row, eq in zip(state["sleeve_history"][1:], state["equity_history"][1:]):
        row["US"] = us
        eq[1] = row["ASX"] + us
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert _aud(rep, "exposure_gap") == pytest.approx(
        50_000.0 * (0.10 * 0.95 - 0.05 * us / 50_000.0), abs=0.005)


def test_days_the_backtest_held_nothing_are_counted_not_priced(monkeypatch):
    """`ultra`'s backtest sits out most of its window behind the 25% breaker
    the paper book has switched off. On such a day the paper is invested and
    the backtest holds nothing, so there is no backtest holdings return to
    price the difference at: the day stays in rebalance_timing, and the
    exposure note says how many days that was."""
    from trading_algo import config as cfg
    monkeypatch.setattr(cfg, "CREDIT_IDLE_CASH", False)   # a flat day earns 0
    bt = _backtest((0.10, -0.05))
    us = bt["sleeves"]["US"]
    us["weights"][_D[1]] = pd.Series(dtype=float)   # flat from day 1's close
    us["returns"].loc[_D[2]] = 0.0
    us["base_returns"] = us["returns"].copy()
    bt["equity"] = 50_000.0 * sum((1 + s["returns"]).cumprod()
                                  for s in bt["sleeves"].values())
    rep = attribution.reconcile(_paper_book(0.5, (0.10, -0.05)), bt["equity"],
                                backtest=bt)
    assert "on 1 sleeve-day(s) the backtest held nothing" in rep["notes"]["exposure_gap"]
    assert rep["identity_ok"] is True


def test_a_sleeve_holding_shorts_gets_no_exposure_line():
    """Cash says only NET exposure: a short's proceeds sit in cash. A sleeve
    that held a short has an unknowable gross from its cash path, so the
    holdings lines are not computed — they read zero and say why, and the gap
    stays in the residual rather than being priced on the wrong exposure."""
    bt = _backtest((0.10, -0.05))
    state = _paper_book(0.5, (0.10, -0.05),
                        positions={"AAA": 10, "ZZZ": -5})
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert rep["lines"]["exposure_gap"] == 0.0
    assert rep["lines"]["rebalance_timing"] == 0.0
    assert "short" in rep["notes"]["exposure_gap"]
    assert "US" in rep["notes"]["exposure_gap"]
    assert rep["residual"] == pytest.approx(0.01125, abs=1e-12)


def test_a_sleeve_with_no_rate_gets_no_exposure_line():
    """Without a USD rate the paper's USD cash cannot be put in AUD, so its
    invested fraction is unknown. Do not guess parity: name the currency and
    leave the gap in the residual."""
    bt = _backtest((0.10, -0.05))
    state = _paper_book(0.5, (0.10, -0.05))
    state["fx_snapshot"] = {"AUD": 1.0}
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert rep["lines"]["exposure_gap"] == 0.0
    assert rep["lines"]["rebalance_timing"] == 0.0
    assert "USD" in rep["notes"]["exposure_gap"]


def test_holdings_lines_stay_zero_without_a_backtest():
    """Every existing caller passes no backtest; its bridge is unchanged."""
    state = _paper_book(0.5, (0.10, -0.05))
    rep = attribution.reconcile(state, _backtest((0.10, -0.05))["equity"])
    for name in ("exposure_gap", "rebalance_timing", "cash_interest"):
        assert rep["lines"][name] == 0.0
        assert rep["notes"][name]


# --- fills: a currency with no rate, and the window's last day -------------
def test_a_fill_in_a_currency_with_no_rate_is_not_converted_at_parity():
    """`paper_trade.fx_snapshot` OMITS a currency with no prior good rate
    (a new CAD sleeve on its first failed fetch). A US$1 floor on a US$100
    buy against 2bps modelled is a US$0.98 excess — but with no USD rate,
    converting it at 1.0 would book A$0.98 that never existed. Leave it out
    of the line, name the currency, and let it land in the residual."""
    state = _bridge_state(100_995.08)
    state["trades"][0].update({"region": "US", "ticker": "AAPL",
                               "commission": 1.0, "currency": "USD"})
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == 0.0
    assert "USD" in rep["notes"]["commission_floor"]
    assert "USD" in rep["notes"]["fill_convention"]
    # ...and the base currency itself still converts at exactly 1.0
    state["trades"][0]["currency"] = "AUD"
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == pytest.approx(0.98e-5, abs=1e-12)


def test_bridge_counts_a_fill_on_the_windows_last_day():
    """End equity is stamped AFTER that day's trades, so a fill dated on the
    window's last day is inside the end equity and its cost belongs in the
    bridge. Excluding it (`< end`) would leave the A$4.92 floor excess in the
    residual."""
    state = _bridge_state(100_995.08)
    state["trades"][0]["date"] = "2026-06-30"
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == pytest.approx(4.92e-5, abs=1e-12)


# --- a one-sleeve book: the identity cannot fail, so it must not certify ----
def _one_sleeve(state: dict, bt: dict) -> tuple[dict, dict]:
    """The same book and backtest with the ASX sleeve removed: US only."""
    state = {**state, "allocations": {"US": 1.0},
             "sleeves": {"US": state["sleeves"]["US"]},
             "sleeve_history": [{"date": r["date"], "US": r["US"]}
                                for r in state["sleeve_history"]],
             "equity_history": [[r["date"], r["US"]]
                                for r in state["sleeve_history"]]}
    us = bt["sleeves"]["US"]
    return state, {"equity": 50_000.0 * (1 + us["returns"]).cumprod(),
                   "sleeves": {"US": us}}


def test_a_one_sleeve_book_says_its_zero_residual_proves_nothing():
    """With one sleeve there is no portfolio layer, and rebalance_timing is
    that sleeve's whole remainder, so the residual is zero by construction —
    `ultra` printed 'identity HOLDS' while its backtest sat behind a breaker
    the book has switched off. The identity was not tested, so it must not
    read as a pass, and the table must say so in plain words."""
    state, bt = _one_sleeve(_paper_book(0.5, (0.10, -0.05)),
                            _backtest((0.08, -0.05)))
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert rep["residual"] == pytest.approx(0.0, abs=1e-12)
    assert rep["identity_ok"] is None
    assert "one sleeve" in rep["notes"]["residual"]
    text = attribution.format_bridge(rep, "AUD", rep["start_equity"])
    assert "identity HOLDS" not in text
    assert ("zero by construction for a one-sleeve book and is not evidence "
            "the comparison is like for like") in text


def test_a_multi_sleeve_or_unmeasured_book_keeps_its_verdict():
    """The caveat belongs only where the identity cannot fail: two sleeves
    leave a portfolio layer to test, and a one-sleeve book bridged without
    the holdings lines leaves its whole gap in the residual."""
    bt = _backtest((0.10, -0.05))
    rep = attribution.reconcile(_paper_book(0.5, (0.10, -0.05)), bt["equity"],
                                backtest=bt)
    assert rep["identity_ok"] is True
    assert "one-sleeve" not in attribution.format_bridge(rep, "AUD", 1.0)

    state, bt1 = _one_sleeve(_paper_book(0.5, (0.10, -0.05)),
                             _backtest((0.08, -0.05)))
    rep = attribution.reconcile(state, bt1["equity"])
    assert rep["identity_ok"] is False
    assert "one-sleeve" not in attribution.format_bridge(rep, "AUD", 1.0)


def test_an_unrated_fill_note_says_where_its_cost_went():
    """A fill left out of the cost lines is still inside its sleeve's return.
    With the holdings lines measured, rebalance_timing is each sleeve's gap
    less every other line, so the cost lands THERE; without them, in the
    residual. Here USD is missing from the snapshot but the US sleeve has an
    all-cash close, which gives it its own rate, so the lines are measured."""
    bt = _backtest((0.10, -0.05))
    state = _paper_book(0.0, (0.10, -0.05), buy_on_d1=25_000.0)
    del state["fx_snapshot"]["USD"]
    rep = attribution.reconcile(state, bt["equity"], backtest=bt)
    assert rep["notes"]["exposure_gap"].startswith("a different invested")
    assert "USD" in rep["notes"]["commission_floor"]
    assert "rebalance_timing" in rep["notes"]["commission_floor"]
    rep = attribution.reconcile(state, bt["equity"])
    assert "residual" in rep["notes"]["commission_floor"]
    assert "rebalance_timing" not in rep["notes"]["commission_floor"]


@pytest.mark.parametrize("bad", [float("nan"), 0.0, -1.5])
def test_a_nan_or_non_positive_rate_is_no_rate(bad):
    """`fx_snapshot` must never carry a NaN (paper_trade skips it), but the
    bridge must not trust that: a NaN, zero or negative rate is no rate, not
    parity — the US$0.98 floor excess stays out of the line."""
    state = _bridge_state(100_995.08)
    state["fx_snapshot"]["USD"] = bad
    state["trades"][0].update({"region": "US", "ticker": "AAPL",
                               "commission": 1.0, "currency": "USD"})
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == 0.0
    assert "USD" in rep["notes"]["commission_floor"]


# --- dividends: DIV ledger rows move cash and are not fills -----------------
def _div(date, region, ticker, shares, dps, ccy):
    return {"date": date, "region": region, "ticker": ticker, "side": "DIV",
            "shares": shares, "fill": dps, "commission": 0.0,
            "stamp_duty": 0.0, "currency": ccy}


def test_the_cash_path_undoes_a_dividend_without_moving_shares():
    """A DIV row is cash in (out, on a short) and no shares. Rebuilding
    backward, a dividend credited after the day comes out of that day's cash
    and leaves every position alone. Read as a fill, its share count goes
    back onto the position as if those shares had been sold — a long doubles,
    a short doubles — and the exposure line is computed off that."""
    state = {"sleeves": {"US": {"currency": "USD", "cash": 95.0,
                                "positions": {"AAA": 10, "BBB": -50}}},
             "trades": [_div("2026-03-04", "US", "AAA", 10, 0.5, "USD"),
                        _div("2026-03-04", "US", "BBB", -50, 0.2, "USD")]}
    cash, pos = attribution._sleeve_close(state, "US", pd.Timestamp("2026-03-03"))
    assert cash == pytest.approx(100.0)              # 95 - (+5) - (-10)
    assert pos == {"AAA": 10.0, "BBB": -50.0}


def test_a_dividend_does_not_dilute_the_cost_drag():
    """Cost drag is cost over TRADED notional. A DIV row's shares x fill is a
    dividend, not turnover; counted, it would shrink every region's drag."""
    buy = {"region": "US", "side": "BUY", "shares": 10, "decision": 100.0,
           "fill": 100.5, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"}
    with_div = [buy, _div("2026-03-04", "US", "AAA", 10, 0.5, "USD")]
    assert attribution.realized_cost_drag(with_div) == \
        attribution.realized_cost_drag([buy])


def test_the_bridge_names_the_dividends_the_paper_credited():
    """Both engines now include dividends — the backtest inside its
    total-return prices, the paper as DIV rows — so the line is 0.0 and its
    note carries the paper's amount, as stamp_duty's does: 10 x A$0.50 =
    A$5.00. A DIV row is not a fill, so one in a currency with no rate is
    named under dividends, not reported as a fill left out of the cost lines."""
    state = _bridge_state(100_995.08)
    state["trades"] += [_div("2026-03-02", "ASX", "BHP.AX", 10, 0.5, "AUD"),
                        _div("2026-03-02", "US", "AAPL", 10, 0.25, "USD")]
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["dividends"] == 0.0
    assert "5.00 AUD" in rep["notes"]["dividends"]
    assert "USD" in rep["notes"]["dividends"]
    assert "left out" not in rep["notes"].get("commission_floor", "")
    assert rep["lines"]["commission_floor"] == pytest.approx(4.92e-5, abs=1e-12)

def test_the_cash_path_undoes_any_cash_event_without_a_reader_edit(monkeypatch):
    """Cash events are defined once (`pnl.CASH_EVENTS`); Task 12's interest
    rows join it and the bridge's cash rebuild must undo them as cash with no
    edit here — not as a SELL that adds a phantom position."""
    from trading_algo import pnl

    monkeypatch.setattr(pnl, "CASH_EVENTS", pnl.CASH_EVENTS | {"INT"})
    state = {"sleeves": {"US": {"currency": "USD", "cash": 102.5,
                                "positions": {"AAA": 10}}},
             "trades": [{"date": "2026-03-04", "region": "US", "ticker": "CASH",
                         "side": "INT", "shares": 1, "fill": 2.5,
                         "commission": 0.0, "stamp_duty": 0.0, "currency": "USD"}]}
    cash, pos = attribution._sleeve_close(state, "US", pd.Timestamp("2026-03-03"))
    assert cash == pytest.approx(100.0)
    assert pos == {"AAA": 10.0}
