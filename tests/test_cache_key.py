"""Cache keys must encode the ticker/currency SET, not just its cardinality.

Regression guard: editing a universe (or currency set) while keeping the count
constant previously collided on the same cache file and silently served stale
prices in a backtest.
"""
from __future__ import annotations

import os
import time

import pandas as pd

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


# ---------------------------------------------------------------------------
# Cache FRESHNESS — a key that never collides is still stale forever
# ---------------------------------------------------------------------------
# `load_prices` returned a cached parquet for the life of the file with no
# freshness check at all. Local caches written 2026-07-24 were still being
# served on 2026-09-19, so every local backtest, sweep and research run silently
# used 8-week-old prices. CI never saw it (no equity cache there).
#
# Only an OPEN-ENDED request (end=None, "prices up to now") can go stale. A
# request with an explicit `end` is a closed historical window and its cache is
# valid forever — expiring that would re-download the universe on every backtest.



def _fake_downloader(calls):
    def _download(tickers, start, end):
        calls.append((tuple(tickers), start, end))
        idx = pd.bdate_range("2026-01-01", periods=5)
        return pd.DataFrame({t: 1.0 for t in tickers}, index=idx)
    return _download


def _age_file(path, hours):
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_open_ended_cache_is_reused_while_fresh(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_download_primary", _fake_downloader(calls))
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    assert len(calls) == 1


def test_open_ended_cache_expires_and_refetches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_download_primary", _fake_downloader(calls))
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    _age_file(data._cache_path("k"), data.CACHE_TTL_HOURS + 1)
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    assert len(calls) == 2


def test_closed_window_cache_never_expires(tmp_path, monkeypatch):
    """A fixed start/end window is immutable history — re-downloading it every
    run would cost a full universe fetch per backtest for nothing."""
    calls = []
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_download_primary", _fake_downloader(calls))
    data.load_prices(["AAA"], "2026-01-01", "2026-01-08", cache_key="k2")
    _age_file(data._cache_path("k2"), 10_000)
    data.load_prices(["AAA"], "2026-01-01", "2026-01-08", cache_key="k2")
    assert len(calls) == 1


def test_ttl_is_under_one_day_so_a_daily_run_always_refetches():
    """A scheduled run happens once every 24h; a TTL at or above that would let
    a book trade on yesterday's prices forever."""
    assert 0 < data.CACHE_TTL_HOURS < 24
