"""The three measurements the book screens add — kept in metrics.py, the one
source (CLAUDE.md; tests/test_metrics_single_source.py)."""
import pandas as pd
import pytest

from trading_algo import metrics


def test_monthly_returns_month_end_against_previous_month_end():
    idx = pd.to_datetime(["2026-06-11", "2026-06-30", "2026-07-15", "2026-07-31", "2026-08-03"])
    eq = pd.Series([100.0, 110.0, 99.0, 104.5, 104.5], index=idx)
    out = metrics.monthly_returns(eq)
    assert list(out.index) == ["2026-06", "2026-07", "2026-08"]
    assert out["2026-06"] == pytest.approx(0.10)              # vs the first mark
    assert out["2026-07"] == pytest.approx(104.5 / 110 - 1)
    assert out["2026-08"] == pytest.approx(0.0)


def test_monthly_returns_first_month_against_the_starting_capital():
    """The book's first day (capital put in -> first mark) belongs to the first
    month, or the months don't compound to the since-start return."""
    idx = pd.to_datetime(["2026-06-11", "2026-06-30", "2026-07-31"])
    eq = pd.Series([99.0, 101.0, 98.0], index=idx)
    out = metrics.monthly_returns(eq, start=100.0)
    assert out["2026-06"] == pytest.approx(0.01)
    assert (1 + out).prod() - 1 == pytest.approx(98.0 / 100.0 - 1)


def test_monthly_returns_skips_unvaluable_marks():
    idx = pd.to_datetime(["2026-06-11", "2026-06-20", "2026-06-30"])
    eq = pd.Series([100.0, float("nan"), 105.0], index=idx)
    assert metrics.monthly_returns(eq)["2026-06"] == pytest.approx(0.05)


def test_monthly_returns_empty():
    assert metrics.monthly_returns(pd.Series(dtype=float)).empty


@pytest.mark.parametrize("wins,n,lo,hi", [(29, 77, 0.277, 0.488), (87, 197, 0.374, 0.511)])
def test_wilson_interval(wins, n, lo, hi):
    a, b = metrics.wilson_interval(wins, n)
    assert a == pytest.approx(lo, abs=0.001)
    assert b == pytest.approx(hi, abs=0.001)


def test_wilson_interval_empty_sample_says_nothing():
    assert metrics.wilson_interval(0, 0) == (0.0, 1.0)


def test_expected_gap_scales_with_root_years():
    assert metrics.expected_gap(0.10, 4.0) == pytest.approx(0.20)
    assert metrics.expected_gap(0.0, 4.0) == 0.0
    assert metrics.expected_gap(0.1, 0.0) == 0.0
