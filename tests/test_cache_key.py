"""Cache keys must encode the ticker/currency SET, not just its cardinality.

Regression guard: editing a universe (or currency set) while keeping the count
constant previously collided on the same cache file and silently served stale
prices in a backtest.
"""
from __future__ import annotations

from trading_algo import data, fx


def _key_for(tickers, start="2012-01-01", end="2026-01-01"):
    captured = {}

    def fake_load_prices(tks, s, e=None, cache_key=None, use_cache=True):
        captured["key"] = cache_key
        import pandas as pd
        return pd.DataFrame(columns=tks)

    return captured, fake_load_prices


def test_region_cache_key_depends_on_ticker_set(monkeypatch):
    import pandas as pd
    from trading_algo.regions import get_region

    seen = []

    def fake_load_prices(tickers, start, end=None, cache_key=None, use_cache=True):
        seen.append(cache_key)
        return pd.DataFrame(columns=list(tickers))

    monkeypatch.setattr(data, "load_prices", fake_load_prices)
    region = get_region("US")
    data.load_region(region, "2012-01-01", "2026-01-01", tickers=["AAA", "BBB"])
    data.load_region(region, "2012-01-01", "2026-01-01", tickers=["AAA", "CCC"])
    # Same count, different names -> keys must differ.
    assert seen[0] != seen[1]


def test_fx_cache_key_depends_on_currency_set(monkeypatch):
    import pandas as pd

    seen = []

    def fake_load_prices(tickers, start, end=None, cache_key=None, use_cache=True):
        seen.append(cache_key)
        idx = pd.to_datetime(["2012-01-02", "2012-01-03"])
        return pd.DataFrame({t: [1.0, 1.0] for t in tickers}, index=idx)

    monkeypatch.setattr(data, "load_prices", fake_load_prices)
    fx.load_fx(["AUD", "USD"], "2012-01-01", "2026-01-01")
    fx.load_fx(["AUD", "GBP"], "2012-01-01", "2026-01-01")
    assert seen[0] != seen[1]
