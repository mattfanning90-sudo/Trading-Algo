"""Per-region fee model: commission floors and UK stamp duty."""
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
