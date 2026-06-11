"""Region registry integrity."""
from trading_algo.regions import REGIONS, get_region


def test_three_regions():
    assert set(REGIONS) == {"ASX", "US", "FTSE"}


def test_region_fields_populated():
    for key, r in REGIONS.items():
        assert r.key == key
        assert r.currency in {"AUD", "USD", "GBP"}
        assert r.universe, f"{key} has empty universe"
        assert r.index_ticker
        assert r.market_open < r.market_close
        assert r.commission_bps >= 0 and r.min_commission >= 0


def test_no_duplicate_tickers_within_region():
    for r in REGIONS.values():
        assert len(r.universe) == len(set(r.universe))


def test_universe_larger_than_top_n():
    for r in REGIONS.values():
        assert len(r.universe) > r.params.top_n


def test_all_tickers_includes_index():
    r = get_region("US")
    assert r.index_ticker in r.all_tickers
    assert len(r.all_tickers) == len(r.universe) + 1


def test_ftse_quirks():
    ftse = get_region("FTSE")
    assert ftse.price_scale == 0.01          # pence -> pounds
    assert ftse.stamp_duty_bps > 0           # UK SDRT modelled
    assert all(t.endswith(".L") for t in ftse.universe)


def test_us_has_etfs():
    us = get_region("US")
    assert "SPY" in us.universe and "QQQ" in us.universe
    assert us.stamp_duty_bps == 0
