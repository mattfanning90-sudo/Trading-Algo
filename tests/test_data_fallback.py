"""Backlog F14: market-data provider fallback."""
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import data


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


def test_volume_cache_is_scoped_to_the_requested_date_range(tmp_path, monkeypatch):
    """A short cached download must not truncate a later, longer ADV history."""
    calls = []

    def download(tickers, start, end, **_kwargs):
        calls.append((tuple(tickers), start, end))
        idx = pd.bdate_range(start, end)
        return pd.DataFrame({("Volume", "AAA"): range(1, len(idx) + 1)}, index=idx)

    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    data.load_volume(["AAA"], "2020-01-02", "2020-01-03")
    longer = data.load_volume(["AAA"], "2020-01-01", "2020-01-06")

    assert len(calls) == 2
    assert longer.index.equals(pd.bdate_range("2020-01-01", "2020-01-06"))
