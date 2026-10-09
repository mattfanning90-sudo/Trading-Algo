"""Per-region fee model: commission floors and UK stamp duty."""
import pytest

from trading_algo import fees
from trading_algo.regions import get_region


def test_commission_floor_applies():
    asx = get_region("ASX")
    # tiny trade -> floor (A$5)
    assert fees.commission(asx, 100.0) == asx.min_commission
    # large trade -> bps
    assert fees.commission(asx, 1_000_000.0) == 1_000_000.0 * asx.commission_bps / 1e4


def test_commission_zero_notional():
    assert fees.commission(get_region("US"), 0.0) == 0.0


def test_stamp_duty_uk_only():
    ftse = get_region("FTSE")
    us = get_region("US")
    asx = get_region("ASX")
    assert fees.stamp_duty(ftse, 10_000.0) == 10_000.0 * ftse.stamp_duty_bps / 1e4
    assert fees.stamp_duty(us, 10_000.0) == 0.0
    assert fees.stamp_duty(asx, 10_000.0) == 0.0


def test_stamp_duty_buys_only():
    ftse = get_region("FTSE")
    # negative (sell) notional pays nothing
    assert fees.stamp_duty(ftse, -10_000.0) == 0.0


def test_round_trip_rate():
    us = get_region("US")
    expected = (us.commission_bps + us.slippage_bps) / 1e4
    assert fees.round_trip_cost_rate(us) == expected


def test_turnover_cost_charges_the_per_order_commission_floor():
    """July finding H3: the backtest charged commission_bps only, so a book too
    small for bps to reach the floor was modelled as trading almost free, while
    the paper book paid A$5 an order through fees.commission."""
    asx = get_region("ASX")                      # 8bps, A$5 floor, no stamp duty
    nav, n_names, turnover, buys = 10_000.0, 5, 0.5, 0.25

    floored = fees.turnover_cost(asx, turnover, buys, nav=nav, n_names=n_names)
    bps_only = fees.turnover_cost(asx, turnover, buys)

    # each order is 0.5 * 10_000 / 5 = A$1,000 -> 8bps is 80c, so the floor binds
    assert floored > bps_only
    assert floored == pytest.approx(
        turnover * asx.slippage_bps / 1e4
        + n_names * asx.min_commission / nav
        + buys * asx.stamp_duty_bps / 1e4)


def test_large_orders_pay_bps_not_the_floor():
    """Above the floor the model is exactly what it always was."""
    us = get_region("US")
    nav, n_names, turnover = 10_000_000.0, 4, 0.8
    got = fees.turnover_cost(us, turnover, 0.0, nav=nav, n_names=n_names)
    assert got == pytest.approx(
        turnover * (us.commission_bps + us.slippage_bps) / 1e4)


def test_turnover_cost_without_nav_is_unchanged():
    """Every existing caller keeps the prior model, bit for bit."""
    us = get_region("US")
    assert fees.turnover_cost(us, 0.4, 0.2) == pytest.approx(
        0.4 * fees.round_trip_cost_rate(us) + 0.2 * us.stamp_duty_bps / 1e4)
