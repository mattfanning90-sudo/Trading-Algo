"""Provider symbol translation + chain routing (offline, no network)."""
import pandas as pd

from trading_algo import providers as pv


def test_polygon_symbol_translation():
    s = pv.PolygonProvider._symbol
    assert s("AAPL") == "AAPL"
    assert s("BRK-B") == "BRK.B"
    assert s("AUDUSD=X") == "C:AUDUSD"
    assert s("^GSPC") == "I:SPX"
    # Polygon has no LSE/ASX listings and no ^AXJO/^FTSE
    assert s("BHP.AX") is None
    assert s("AZN.L") is None
    assert s("^AXJO") is None


def test_polygon_supports_requires_key():
    assert pv.PolygonProvider(api_key=None).supports("AAPL") is False
    p = pv.PolygonProvider(api_key="dummy")
    assert p.supports("AAPL") and p.supports("AUDUSD=X")
    assert not p.supports("BHP.AX") and not p.supports("AZN.L")


def test_stooq_symbol_translation():
    s = pv.StooqProvider._symbol
    assert s("AAPL") == "aapl.us"
    assert s("BHP.AX") == "bhp.au"
    assert s("AZN.L") == "azn.uk"
    assert s("AUDUSD=X") is None and s("^GSPC") is None


def test_chain_defaults_to_yfinance(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    chain = pv.get_chain()
    assert chain[0].name == "yfinance"
    assert {p.name for p in chain} == {"yfinance", "stooq"}


def test_chain_primary_polygon(monkeypatch):
    monkeypatch.setenv("MOMENTUM_DATA_PROVIDER", "polygon")
    names = [p.name for p in pv.get_chain()]
    assert names[0] == "polygon" and "yfinance" in names and "stooq" in names


class _Fake(pv.PriceProvider):
    def __init__(self, name, supported, frame):
        self.name = name
        self._supported = set(supported)
        self._frame = frame

    def supports(self, ticker):
        return ticker in self._supported

    def fetch(self, tickers, start, end):
        cols = [t for t in tickers if t in self._frame.columns]
        return self._frame[cols]


def test_fetch_prices_routes_and_combines():
    idx = pd.bdate_range("2020-01-01", periods=5)
    a = _Fake("a", ["AAPL"], pd.DataFrame({"AAPL": range(5)}, index=idx))
    b = _Fake("b", ["MSFT", "BHP.AX"],
              pd.DataFrame({"MSFT": range(5), "BHP.AX": range(5)}, index=idx))
    out = pv.fetch_prices(["AAPL", "MSFT", "BHP.AX"], "2020-01-01", None, chain=[a, b])
    assert list(out.columns) == ["AAPL", "MSFT", "BHP.AX"]


def test_fetch_prices_falls_back_when_primary_empty():
    idx = pd.bdate_range("2020-01-01", periods=5)
    empty = _Fake("primary", ["AAPL"], pd.DataFrame(index=idx))   # supports but returns nothing
    catchall = _Fake("fb", ["AAPL"], pd.DataFrame({"AAPL": range(5)}, index=idx))
    out = pv.fetch_prices(["AAPL"], "2020-01-01", None, chain=[empty, catchall])
    assert "AAPL" in out.columns and len(out) == 5
