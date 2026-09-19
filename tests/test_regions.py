"""Region registry integrity."""
from trading_algo import config
from trading_algo.regions import REGIONS, all_region_keys, get_region


def test_registered_regions():
    assert set(REGIONS) == {"ASX", "US", "FTSE", "TSX"}


def test_region_fields_populated():
    for key, r in REGIONS.items():
        assert r.key == key
        assert r.currency in {"AUD", "USD", "GBP", "CAD"}
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


def test_tsx_scaffold():
    tsx = get_region("TSX")
    assert tsx.currency == "CAD"
    assert tsx.index_ticker == "^GSPTSE"
    assert tsx.price_scale == 1.0 and tsx.stamp_duty_bps == 0
    assert all(t.endswith(".TO") for t in tsx.universe)


def test_every_funded_region_is_registered():
    """The funding gate's DURABLE half.

    TSX used to be the worked example of "registered but unfunded". It cleared
    the register -> backtest -> fund gate on 2026-09-19 (raw Sharpe 0.948,
    haircut 0.608, maxDD -15.6%; survivorship-biased, so an upper bound) and is
    now funded, so pinning "TSX not in ALLOCATIONS" would pin a moment rather
    than a rule.

    The rule that must always hold: capital can only be allocated to a region
    that actually exists, and the split must be a complete one. The registry may
    legitimately hold regions with no allocation — that is the gate working.
    """
    assert "TSX" in all_region_keys()
    assert "TSX" in config.ALLOCATIONS
    for key in config.ALLOCATIONS:
        assert key in all_region_keys(), f"{key} is funded but not registered"
    assert abs(sum(config.ALLOCATIONS.values()) - 1.0) < 1e-9
