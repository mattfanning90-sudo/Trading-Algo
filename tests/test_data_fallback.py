"""Backlog F14: market-data provider fallback."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import data
from trading_algo.regions import get_region


def _fake_loader(tickers, start, end):
    idx = pd.bdate_range(start, periods=5)
    return pd.DataFrame({t: 10.0 for t in tickers}, index=idx)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    # isolate the registry + config for each test
    monkeypatch.setattr(data, "_FALLBACK_LOADERS", {}, raising=False)
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", None)
    yield


def test_try_fallback_off_by_default():
    data.register_fallback("fake", _fake_loader)
    assert data._try_fallback(["X"], "2020-01-01", None) is None   # source not selected


def test_try_fallback_unknown_source(monkeypatch):
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", "missing")
    assert data._try_fallback(["X"], "2020-01-01", None) is None


def test_try_fallback_returns_frame(monkeypatch):
    data.register_fallback("fake", _fake_loader)
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", "fake")
    df = data._try_fallback(["X", "Y"], "2020-01-01", None)
    assert df is not None and list(df.columns) == ["X", "Y"] and len(df) == 5


def test_load_prices_uses_fallback_when_primary_fails(monkeypatch):
    monkeypatch.setattr(data, "_download_primary",
                        lambda t, s, e: (_ for _ in ()).throw(RuntimeError("403")))
    data.register_fallback("fake", _fake_loader)
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", "fake")
    df = data.load_prices(["AAA", "BBB"], "2020-01-01", use_cache=False)
    assert list(df.columns) == ["AAA", "BBB"] and len(df) == 5


def test_load_prices_raises_when_no_fallback(monkeypatch):
    monkeypatch.setattr(data, "_download_primary",
                        lambda t, s, e: (_ for _ in ()).throw(RuntimeError("403")))
    with pytest.raises(RuntimeError):
        data.load_prices(["ZZZ"], "2020-01-01", use_cache=False)


def test_primary_success_skips_fallback(monkeypatch):
    called = {"fb": False}

    def _fb(tickers, start, end):
        called["fb"] = True
        return _fake_loader(tickers, start, end)

    idx = pd.bdate_range("2020-01-01", periods=8)
    monkeypatch.setattr(data, "_download_primary",
                        lambda t, s, e: pd.DataFrame({t[0]: 5.0}, index=idx))
    data.register_fallback("fake", _fb)
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", "fake")
    data.load_prices(["ONLY"], "2020-01-01", use_cache=False)
    assert called["fb"] is False, "fallback must not run when the primary succeeds"


def test_load_region_drops_rows_where_only_the_index_printed(monkeypatch):
    """A day the index printed but no stock did is not a tradeable session.

    `load_prices` drops all-NaN rows across the COMBINED frame, so a row
    carrying only the regime index survives into `prices` as an all-NaN row.
    Every trailing signal read off that row is then NaN — realised vol most of
    all — so the sleeve produces no targets and silently liquidates to cash on
    a day nothing was actually wrong with it.
    """
    region = get_region("FTSE")
    idx = pd.bdate_range("2024-01-01", periods=4)
    frame = pd.DataFrame(
        {region.universe[0]: [100.0, 101.0, 102.0, np.nan],
         region.universe[1]: [50.0, 51.0, 52.0, np.nan],
         region.index_ticker: [7000.0, 7010.0, 7020.0, 7030.0]},
        index=idx)
    monkeypatch.setattr(data, "load_prices", lambda *a, **k: frame)

    prices, index_px = data.load_region(region, "2024-01-01")

    assert prices.index[-1] == idx[2], "the index-only row must not be the as-of date"
    assert prices.notna().any(axis=1).all(), "no all-NaN rows survive"
    assert len(index_px) == 4, "the index series itself keeps its own calendar"


# ---------------------------------------------------------------------------
# F14: the built-in Tiingo secondary source
# ---------------------------------------------------------------------------
# A TIINGO_API_SECRET repo secret has existed since 2026-06-27 with ZERO code
# references — a credential that bought nothing. The fallback registry has
# existed just as long with nothing registered in it, so `_try_fallback` always
# returned None. These two gaps fit each other exactly.
import json as _json


class _Resp:
    def __init__(self, payload):
        self._p = _json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _tiingo_payload():
    return [{"date": "2026-01-02T00:00:00.000Z", "adjClose": 10.0},
            {"date": "2026-01-03T00:00:00.000Z", "adjClose": 11.0}]


def test_tiingo_loader_returns_a_close_frame(monkeypatch):
    from trading_algo import tiingo_data
    monkeypatch.setenv("TIINGO_API_SECRET", "tok")
    monkeypatch.setattr(tiingo_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_tiingo_payload()))
    df = tiingo_data.load(["AAPL"], "2026-01-01")
    assert list(df.columns) == ["AAPL"] and len(df) == 2
    assert df["AAPL"].iloc[-1] == 11.0


def test_tiingo_loader_is_a_noop_without_a_token(monkeypatch):
    from trading_algo import tiingo_data
    monkeypatch.delenv("TIINGO_API_SECRET", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert tiingo_data.load(["AAPL"], "2026-01-01") is None


def test_named_builtin_fallback_self_registers(monkeypatch):
    """Setting DATA_FALLBACK_SOURCE='tiingo' must be enough — no caller should
    have to remember to import the adapter to register it."""
    from trading_algo import data, tiingo_data
    monkeypatch.setattr(cfg, "DATA_FALLBACK_SOURCE", "tiingo")
    monkeypatch.setenv("TIINGO_API_SECRET", "tok")
    monkeypatch.setattr(tiingo_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_tiingo_payload()))
    monkeypatch.delitem(data._FALLBACK_LOADERS, "tiingo", raising=False)
    out = data._try_fallback(["AAPL"], "2026-01-01", None)
    assert out is not None and "AAPL" in out.columns
