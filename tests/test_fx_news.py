"""Economic-calendar 'news' correlation — graceful, only-if-real behaviour."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from trading_algo.forex import news


@pytest.fixture(autouse=True)
def _clear_news_cache():
    """The shared fetch is memoised per (date, key) — isolate every test."""
    news._fetch_rows_cached.cache_clear()
    yield
    news._fetch_rows_cached.cache_clear()


def _stub_requests(monkeypatch, rows, calls):
    class _Resp:
        ok = True
        def json(self):
            return rows
    mod = types.ModuleType("requests")
    def _get(url, params=None, timeout=None):
        calls["n"] += 1
        calls["params"] = params
        return _Resp()
    mod.get = _get
    monkeypatch.setitem(sys.modules, "requests", mod)


def test_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    # No key => no network, no events (the daily summary simply omits the section).
    assert news.economic_events(["USD", "EUR"], "2026-06-27") == []


def test_non_fiat_or_empty_currencies_skipped(monkeypatch):
    monkeypatch.setenv("NEWS_API_KEY", "dummy")
    # Crypto has no economic calendar -> filtered out -> no query, returns [].
    assert news.economic_events(["BTC", "ETH"], "2026-06-27") == []
    assert news.economic_events([], "2026-06-27") == []
    assert news.economic_events(["USD"], "") == []


def test_high_impact_classifier():
    assert news._is_high("High") and news._is_high("3") and news._is_high("HIGH")
    assert not news._is_high("Low") and not news._is_high("Medium") and not news._is_high(None)


def test_never_raises_on_bad_provider(monkeypatch):
    monkeypatch.setenv("NEWS_API_KEY", "dummy")
    # Force the lazy requests import / call to blow up -> must return [], not raise.
    boom = types.ModuleType("requests")
    def _get(*a, **k):
        raise RuntimeError("network down")
    boom.get = _get
    monkeypatch.setitem(sys.modules, "requests", boom)
    assert news.economic_events(["USD"], "2026-06-27") == []


# ---- one shared provider fetch (calendar_feed + economic_events) -----------
_FIXTURE = [
    {"currency": "USD", "impact": "High", "event": "CPI YoY",
     "date": "2026-06-27 13:30:00", "actual": 3.1, "estimate": 3.0, "previous": 3.2},
    {"currency": "EUR", "impact": "Medium", "event": "PMI",
     "date": "2026-06-27 08:00:00", "actual": None, "estimate": None, "previous": None},
    {"currency": "USD", "impact": "Low", "event": "irrelevant",
     "date": "2026-06-27 01:00:00"},
    {"country": "GBP", "impact": "3", "event": "BoE decision", "date": ""},
    {"currency": "MXN", "impact": "High", "event": "not ours", "date": ""},
]


def test_shared_fetch_feeds_both_functions(monkeypatch):
    """One _fetch_rows patch point feeds BOTH consumers; each keeps its own
    filtering/shape/sort exactly as before the extraction."""
    monkeypatch.setenv("NEWS_API_KEY", "k")
    calls = {"n": 0}
    _stub_requests(monkeypatch, _FIXTURE, calls)

    feed = news.calendar_feed(["USD", "EUR", "GBP"], "2026-06-27")
    # medium+high only, time-sorted (missing time sorts last), impact normalised
    assert [e["event"] for e in feed] == ["PMI", "CPI YoY", "BoE decision"]
    assert [e["impact"] for e in feed] == ["medium", "high", "high"]
    assert feed[1]["time"] == "13:30" and feed[1]["actual"] == 3.1

    ev = news.economic_events(["USD", "EUR", "GBP"], "2026-06-27")
    # high-impact only, provider order, original shape (no 'time' field)
    assert [e["event"] for e in ev] == ["CPI YoY", "BoE decision"]
    assert ev[0]["currency"] == "USD" and "time" not in ev[0]
    # both routed through the single memoised fetch
    assert calls["n"] == 1
    assert calls["params"]["from"] == "2026-06-27" and calls["params"]["apikey"] == "k"


def test_fetch_memoised_per_date_and_key(monkeypatch):
    """8 identical GETs per --all export -> 1 (FMP free tier headroom)."""
    monkeypatch.setenv("NEWS_API_KEY", "k")
    calls = {"n": 0}
    _stub_requests(monkeypatch, _FIXTURE, calls)
    news.calendar_feed(["USD"], "2026-06-27")
    news.calendar_feed(["USD"], "2026-06-27")
    news.economic_events(["USD"], "2026-06-27")
    assert calls["n"] == 1
    news.economic_events(["USD"], "2026-06-28")        # different date -> new call
    assert calls["n"] == 2


# ---- dashboard call sites: dates + currency collection ----------------------
def test_with_catalysts_truncates_intraday_date(monkeypatch):
    """The daytrader book keys daily['date'] as 'YYYY-MM-DD HH:MM'; the provider
    contract is a plain YYYY-MM-DD."""
    from trading_algo.forex import dashboard as dash
    seen = {}
    monkeypatch.setattr(news, "economic_events",
                        lambda curs, date, **k: (seen.__setitem__("date", date), [])[1])
    d = dash._with_catalysts({"date": "2026-07-03 14:00",
                              "drivers": [{"pair": "EURUSD"}]})
    assert seen["date"] == "2026-07-03"
    assert d["catalysts"] == []


def test_news_feed_dated_today_not_last_bar(monkeypatch):
    """'Today's scheduled releases' must be dated from TODAY, not the book's
    last completed bar (yesterday/Friday between nightly runs)."""
    from trading_algo.forex import dashboard as dash
    seen = {}
    monkeypatch.setattr(news, "calendar_feed",
                        lambda curs, date, **k: (seen.__setitem__("date", date), [])[1])
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    dash._news_feed({"symbols": ["EURUSD"], "daily": {"date": yday}})
    assert seen["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_news_feed_currencies_via_canonical_helper(monkeypatch):
    """Currency collection goes through pairs.currencies_in — unknown symbols
    tolerated, crypto/non-fiat filtered."""
    from trading_algo.forex import dashboard as dash
    seen = {}
    monkeypatch.setattr(news, "calendar_feed",
                        lambda curs, date, **k: (seen.__setitem__("curs", list(curs)), [])[1])
    dash._news_feed({"symbols": ["EURUSD", "AUDJPY", "BOGUS", "GBPUSD"]})
    assert seen["curs"] == ["AUD", "EUR", "GBP", "JPY", "USD"]
