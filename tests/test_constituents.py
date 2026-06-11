"""Point-in-time constituents (survivorship-bias fix)."""
import pandas as pd

from trading_algo import constituents as c
from trading_algo.backtest import run_backtest
from trading_algo.regions import get_region


def test_members_asof_lookup():
    df = pd.DataFrame({
        "date": ["2020-01-31", "2020-01-31", "2020-06-30", "2020-06-30"],
        "ticker": ["AAA", "BBB", "BBB", "CCC"],
    })
    table = c.MembershipTable.from_frame(df)
    assert table.members_asof("2019-12-31") == set()          # before first snapshot
    assert table.members_asof("2020-03-01") == {"AAA", "BBB"}  # uses Jan snapshot
    assert table.members_asof("2020-07-01") == {"BBB", "CCC"}  # uses Jun snapshot
    assert set(table.all_tickers) == {"AAA", "BBB", "CCC"}
    assert len(table) == 2


def test_from_frame_requires_columns():
    import pytest
    with pytest.raises(ValueError):
        c.MembershipTable.from_frame(pd.DataFrame({"x": [1]}))


def test_synthetic_membership_is_subset_of_universe():
    region = get_region("ASX")
    table = c.synthetic_membership(region, "2015-01-01", "2020-01-01")
    assert len(table) > 0
    universe = set(region.universe)
    for d in ("2015-06-30", "2018-01-31", "2019-12-31"):
        members = table.members_asof(d)
        assert members
        assert members.issubset(universe)


def test_backtest_uses_point_in_time(synth_asx, asx_region):
    prices, index_px = synth_asx
    membership = c.synthetic_membership(asx_region, "2014-01-01", "2024-01-01")
    result = run_backtest(prices, index_px, asx_region, membership=membership)
    assert result["point_in_time"] is True
    assert (result["equity"] > 0).all()


def test_get_membership_none_when_unset():
    assert c.get_membership(get_region("US")) is None
