"""Economic-calendar 'news' correlation — graceful, only-if-real behaviour."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from trading_algo.forex import news


@pytest.fixture(autouse=True)
def _clear_news_cache():
    """The shared fetches are memoised per (date/range, key) — isolate every test."""
    news._fetch_rows_cached.cache_clear()
    news._fetch_range_cached.cache_clear()
    yield
    news._fetch_rows_cached.cache_clear()
    news._fetch_range_cached.cache_clear()


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


# ---- calendar_range + dashboard.fx_api news wiring --------------------------
def test_calendar_range_one_call_dated_high_impact(monkeypatch):
    """One provider call for the whole window; dated, high-impact only."""
    monkeypatch.setenv("NEWS_API_KEY", "k")
    rows = [
        {"date": "2026-07-01 12:30", "currency": "USD", "impact": "High",
         "event": "CPI", "actual": 3.1, "estimate": 3.0, "previous": 3.2},
        {"date": "2026-07-02 08:00", "currency": "EUR", "impact": "Medium",
         "event": "PMI", "actual": 51, "estimate": 50},
        {"date": "2026-07-03 18:00", "currency": "JPY", "impact": "Low",
         "event": "minor", "actual": 1},
    ]
    calls = {"n": 0}
    _stub_requests(monkeypatch, rows, calls)
    out = news.calendar_range(["USD", "EUR", "JPY"], "2026-06-25", "2026-07-04")
    assert calls["n"] == 1                       # single ranged GET
    assert calls["params"]["from"] == "2026-06-25" and calls["params"]["to"] == "2026-07-04"
    assert [e["event"] for e in out] == ["CPI"]  # high-impact only, dated
    assert out[0]["date"] == "2026-07-01" and out[0]["time"] == "12:30"
    assert out[0]["currency"] == "USD" and out[0]["impact"] == "high"
    # CPI beat (3.1 vs 3.0) → currency-positive predicted impact
    assert out[0]["bias"] == "positive" and out[0]["bias_text"] == "USD POSITIVE"
    # medium included when high_only=False, still sorted by date/time
    out2 = news.calendar_range(["USD", "EUR"], "2026-06-25", "2026-07-04", high_only=False)
    assert [e["event"] for e in out2] == ["CPI", "PMI"]


def test_calendar_range_graceful_without_key(monkeypatch):
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    assert news.calendar_range(["USD"], "2026-06-01", "2026-07-04") == []


def test_fx_snapshot_carries_news(tmp_path, monkeypatch):
    """build_fx_snapshot surfaces dated news for the book's currencies, and
    stays [] (never raises) with no key."""
    import json
    import trading_algo.paper_trade as pt
    from trading_algo.dashboard import fx_api
    from trading_algo.forex import fx_book

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    state = {
        "account": "matt", "currency": "AUD", "profile": "balanced", "bar": "1d",
        "symbols": ["EURUSD", "BTCUSD"], "initial_capital": 5000.0, "equity": 4900.0,
        "positions": {"EURUSD": -0.1}, "last_close": {"EURUSD": 1.14},
        "last_bar_date": "2026-07-04", "peak_equity": 5000.0, "risk_halted": False,
        "halt_cooldown": 0, "trades": [], "equity_history": [["2026-07-04", 4900.0]],
        "decisions": {"EURUSD": {"weight": -0.1, "tilt": -0.5, "regime": "trending",
                                 "agents": {}, "indicators": {"price": 1.14, "ann_vol": 0.05},
                                 "text": "x"}},
        "daily": {},
    }
    (tmp_path / "fx_state_matt.json").write_text(json.dumps(state))

    # currencies derived from the pairs (EUR, USD — BTC dropped as non-fiat)
    assert fx_api._pair_currencies(["EURUSD", "BTCUSD"]) == ["EUR", "USD"]

    captured = {}
    monkeypatch.setenv("NEWS_API_KEY", "k")
    def _fake_range(curs, start, end, **k):
        captured["curs"] = list(curs); captured["start"] = start; captured["end"] = end
        return [{"date": "2026-07-01", "time": "12:30", "currency": "USD",
                 "event": "CPI", "impact": "high", "actual": 3.1, "estimate": 3.0}]
    monkeypatch.setattr(fx_api.fxnews, "calendar_range", _fake_range)

    snap = fx_api.build_fx_snapshot("matt")
    assert snap["news_available"] is True
    assert [e["event"] for e in snap["news"]] == ["CPI"]
    assert captured["curs"] == ["EUR", "USD"]
    assert captured["end"] == "2026-07-06"       # anchor + 2 days
    assert captured["start"] == "2026-05-20"     # anchor − 45 days

    # no key → silent, never raises
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.setattr(fx_api.fxnews, "calendar_range", lambda *a, **k: [])
    snap2 = fx_api.build_fx_snapshot("matt")
    assert snap2["news"] == [] and snap2["news_available"] is False


# ---- predicted currency impact ---------------------------------------------
def test_predicted_impact_reads():
    pi = news.predicted_impact
    # hawkish beat vs miss
    assert pi("CPI YoY", "USD", 3.1, 3.0)["bias"] == "positive"
    assert pi("CPI YoY", "USD", 2.8, 3.0)["bias"] == "negative"
    # labour slack is inverted: higher unemployment = currency-negative
    assert pi("Unemployment Rate", "USD", 4.3, 4.1)["bias"] == "negative"
    assert pi("Unemployment Rate", "USD", 3.9, 4.1)["bias"] == "positive"
    # suffix parsing (K) on a miss
    assert pi("Nonfarm Payrolls", "USD", "180K", "200K")["bias"] == "negative"
    # rate hike above expectation
    assert pi("BoE Interest Rate Decision", "GBP", 4.50, 4.25)["bias"] == "positive"
    # inline
    assert pi("US GDP", "USD", 2.0, 2.0)["bias"] == "neutral"
    # a speech is "watch tone", not a direction
    assert pi("ECB President Speech", "EUR")["bias"] == "watch"
    assert pi("ECB President Speech", "EUR")["text"] == "WATCH TONE"
    # upcoming (forecast only) → convention arrow, no realised direction
    up = pi("German GDP", "EUR", None, "0.3%", "0.2%")
    assert up["bias"] == "watch" and up["text"] == "HIGHER → EUR+"
    # unclassified indicator → unknown, empty text
    assert pi("Obscure Diffusion Index", "JPY", 5, 4) == {"bias": "unknown", "text": ""}
