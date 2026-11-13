import pytest

from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.verify import WARN, Finding

EQ_COSTS = {"total": 449.17, "by_ccy": {"GBP": 396.16, "USD": 53.01},
            "stamp": {"GBP": 292.51}, "includes": "commission and stamp duty"}


def ids(items):
    return [i["id"] for i in items]


def test_costs_that_flip_a_gain_into_a_loss_name_the_uk():
    out = R.costs_flip(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    assert out[0]["text"].startswith("Costs (A$449.17) turned a A$401.55 gain into a A$47.62 loss")
    assert "mostly from UK trading: A$396.16, of which A$292.51 was stamp duty" in out[0]["text"]
    assert out[0]["backlog"] == "F25" and "slippage" in out[0]["caveat"]


def test_small_costs_on_a_loss_say_nothing():
    small = dict(EQ_COSTS, total=45.84, by_ccy={"USD": 45.84}, stamp={})
    assert R.costs_flip(make_inp(card={"equity": 9_496.51, "initial": 10_000.0}, costs=small)) == []


def test_fx_costs_near_the_whole_loss_and_the_residual_note():
    fxb = {"net_pnl": -902.15, "residual": 277.19, "costs": 897.25}
    inp = make_inp("fx", card={"equity": 9_097.85, "initial": 10_000.0},
                   costs={"total": 897.25, "includes": "the spread paid on trades"}, fx_book=fxb)
    out = R.costs_flip(inp)
    assert "are 99% of the A$902.15 loss" in out[0]["text"]
    assert "unexplained" in out[0]["caveat"] and out[0]["backlog"] is None


def test_holds_too_short_states_units_and_carries_the_counter_evidence():
    f = Finding(WARN, "t", "hold-shorter-than-signal", "…",
                {"median_bars": 5.0, "horizon_bars": 57, "cut_short": 59, "round_trips": 77})
    out = R.holds_too_short(make_inp("fx", findings=[f], state={"bar": "1d"}))
    assert "5 days" in out[0]["text"] and "57 trading days (about 11 weeks)" in out[0]["text"]
    assert "59 of 77" in out[0]["text"] and out[0]["backlog"] == "F20"
    assert "not been shown to earn more" in out[0]["caveat"]
    hourly = R.holds_too_short(make_inp("fx", findings=[f], state={"bar": "60m"}))
    assert "57 bars" in hourly[0]["text"]


def _positions(wins, n):
    return [{"symbol": "A", "opened": "2026-07-01", "closed": f"2026-08-{(i % 28) + 1:02d}",
             "side": "LONG", "net": 1.0 if i < wins else -1.0, "lots": 1} for i in range(n)]


def test_loses_more_often_only_when_the_whole_range_is_below_half():
    assert ids(R.loses_more_often(make_inp(positions_closed=_positions(29, 77)))) == ["loses-more-often"]
    assert R.loses_more_often(make_inp(positions_closed=_positions(36, 77))) == []


def _sleeve(cash, positions=None, **extra):
    return {"currency": "AUD", "cash": cash, "positions": positions or {}, **extra}


def test_mostly_idle_splits_cash_by_cause_and_tracks_only_the_sized_down_part():
    state = {"allocations": {"ASX": 0.34, "US": 0.33, "FTSE": 0.33},
             "fx_snapshot": {"AUD": 1.0},
             "sleeves": {"ASX": _sleeve(33_000.0, last_flat_reason="regime-off",
                                        last_rebalance_month="2026-10",
                                        last_rebalance_date="2026-10-06"),
                         "US": _sleeve(25_000.0, {"AAPL": 10}),
                         "FTSE": _sleeve(14_000.0, {"BP.L": 100})}}
    out = R.mostly_idle(make_inp(state=state))
    assert out[0]["text"].startswith("Mostly idle: 72% of the book is in cash")
    assert "below its long-term trend, by design" in out[0]["text"]
    assert "sized down" in out[0]["text"] and out[0]["backlog"] == "F23"


def test_mostly_idle_never_fires_on_a_long_short_book():
    state = {"param_overrides": {"long_short": True}, "fx_snapshot": {"AUD": 1.0},
             "sleeves": {"US": _sleeve(110_000.0)}}
    assert R.mostly_idle(make_inp(state=state)) == []


def test_missing_rate_never_crashes_costs_or_idle():
    state = {"fx_snapshot": {}, "sleeves": {"US": {"currency": "USD", "cash": 5_000.0, "positions": {}}}}
    inp = make_inp(state=state, costs={"total": 0.0, "by_ccy": {}, "stamp": {}, "includes": "x"})
    assert R.costs_flip(inp) == []
    assert R.invested_share(inp) == pytest.approx(1.0)       # unpriced cash counts as 0 A$


def test_frozen_prices_needs_ten_recent_and_otherwise_shows_as_an_audit_warning():
    f10 = Finding(WARN, "t", "dead-price-turnover", "100 position changes …", {"recent": 10})
    f9 = Finding(WARN, "t", "dead-price-turnover", "3 position changes …", {"recent": 9})
    assert ids(R.holding_back(make_inp("fx", findings=[f10]))) == ["frozen-prices"]
    assert ids(R.holding_back(make_inp("fx", findings=[f9]))) == ["audit-warn"]


def test_a_covered_warning_is_shown_once_by_its_rule():
    state = {"fx_snapshot": {"AUD": 1.0},
             "sleeves": {"ASX": _sleeve(90_000.0, last_flat_reason="regime-off")}}
    f = Finding(WARN, "t", "idle-sleeve", "sleeve ASX …", {"subject": "ASX"})
    got = ids(R.holding_back(make_inp(state=state, findings=[f])))
    assert got.count("mostly-idle") == 1 and "audit-warn" not in got


@pytest.mark.parametrize("sleeve,state,code", [
    (_sleeve(1.0), {"risk_halted": True, "halt_cooldown": 3}, "paused"),
    (_sleeve(1.0, last_flat_reason="data-quality"), {}, "data"),
    (_sleeve(1.0, last_status={"status": "cash:fx-unavailable"}), {}, "fx"),
    (_sleeve(1.0, last_status={"status": "cash:below-min"}), {}, "below-min"),
    (_sleeve(1.0, last_flat_reason="regime-off", last_rebalance_month="2026-10"), {}, "regime-off"),
    (_sleeve(1.0, last_flat_reason="no-eligible-names", last_rebalance_month="2026-10"), {}, "no-eligible"),
    (_sleeve(1.0, last_flat_reason="insufficient-names"), {}, "insufficient"),
    (_sleeve(1.0, last_rebalance_month="2026-10"), {}, "pinned"),
    (_sleeve(1.0), {}, "empty"),
    (_sleeve(1.0, {"AAPL": 3}), {}, "invested"),
])
def test_sleeve_reason_precedence(sleeve, state, code):
    assert R.sleeve_reason(make_inp(state=state), "US", sleeve)[0] == code


def test_regime_off_says_since_the_book_began_when_the_region_never_traded():
    text = R.sleeve_reason(make_inp(), "ASX",
                           _sleeve(1.0, last_flat_reason="regime-off",
                                   last_rebalance_date="2026-10-06",
                                   last_rebalance_month="2026-10"))[1]
    assert "since the book began" in text and "6 Oct 2026" in text and "next month" in text


def test_a_too_short_market_gives_no_behind_market_verdict():
    inp = make_inp(card={"since": "2026-10-01"})
    from unittest import mock
    with mock.patch.object(R, "market", return_value={"too_short": True, "days": 5}):
        assert R.behind_market(inp) == []


def _usd(x):
    return {"total": x, "by_ccy": {"USD": x}, "stamp": {}, "includes": "commission and stamp duty"}


def test_a_loss_where_costs_are_under_half_does_not_fire():
    assert R.costs_flip(make_inp(card={"equity": 99_840.0}, costs=_usd(60.0))) == []


def test_a_result_of_exactly_zero_says_costs_took_everything():
    out = R.costs_flip(make_inp(card={"equity": 100_000.0}, costs=_usd(50.0)))
    assert "took everything the book made before costs" in out[0]["text"]


def test_a_gain_where_costs_take_over_half_states_the_share():
    out = R.costs_flip(make_inp(card={"equity": 100_040.0}, costs=_usd(60.0)))
    assert "took 60% of the A$100.00 made before costs" in out[0]["text"]
