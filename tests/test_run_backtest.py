"""The survivorship-bias measurement.

Spec section 7: the bias is the PIT-versus-non-PIT difference, and it is
reported PER REGION because the off-ramp is per region and the measured bound
ranges from about zero (FTSE) to +9.4pp/yr (ASX).
"""
import pytest

from trading_algo import config as cfg
from trading_algo import run_backtest as rb


@pytest.fixture(scope="module")
def impact():
    """Synthetic membership exercises the PIT machinery offline. Invariant 5:
    the NUMBERS here are meaningless; only the shape is under test."""
    return rb.pit_impact(synthetic=True)


def test_pit_impact_reports_a_delta_for_every_funded_region(impact):
    assert set(impact["per_region"]) == set(cfg.ALLOCATIONS)


def test_each_regions_delta_is_its_own_two_cagrs(impact):
    """A delta that is not the difference of the two numbers printed beside it
    is a number nobody can check."""
    for key, row in impact["per_region"].items():
        assert set(row) == {"static_cagr", "pit_cagr", "delta"}
        assert row["static_cagr"] - row["pit_cagr"] == pytest.approx(
            row["delta"], abs=1e-12), key


def test_the_portfolio_delta_is_still_reported(impact):
    for k in ("static_cagr", "pit_cagr", "delta"):
        assert isinstance(impact[k], float)
