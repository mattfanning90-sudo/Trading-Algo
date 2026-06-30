"""Economic-calendar 'news' correlation — graceful, only-if-real behaviour."""
from trading_algo.forex import news


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
    import sys, types
    boom = types.ModuleType("requests")
    def _get(*a, **k):
        raise RuntimeError("network down")
    boom.get = _get
    monkeypatch.setitem(sys.modules, "requests", boom)
    assert news.economic_events(["USD"], "2026-06-27") == []
