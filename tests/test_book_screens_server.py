import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import book_summary, server
from trading_algo.dashboard.book_service import BookService

KEYS = ["FULL", "ULTRA", "EXPERIMENTAL", "SMALL", "MATT", "PARTNER", "MULTI", "DAY"]


@pytest.fixture()
def base(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    books = BookService(now=lambda: NOW, online=lambda: False)
    httpd = server.create_server("full", False, "127.0.0.1", 0, books=books)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


@pytest.mark.parametrize("key", KEYS)
def test_every_book_answers_fast_with_no_network(base, key):
    for what in ("summary", "positions"):
        t0 = time.monotonic()
        code, body = get(f"{base}/api/{what}/{key}")
        assert code == 200 and body["key"] == key, (what, body)
        assert time.monotonic() - t0 < 2.0, (what, key)


def test_equity_marks_say_offline_and_fx_says_not_needed(base):
    assert get(f"{base}/api/marks/FULL") == (200, {"status": "offline"})
    assert get(f"{base}/api/marks/MATT") == (200, {"status": "n/a"})


@pytest.mark.parametrize("key", ["NOPE", "FX%C2%B7MATT", "..%2F..%2Fetc"])
def test_unknown_or_odd_keys_are_404(base, key):
    code, body = get(f"{base}/api/summary/{key}")
    assert code == 404 and "Traceback" not in json.dumps(body)


def test_a_builder_error_is_a_generic_500(base, monkeypatch):
    monkeypatch.setattr(book_summary, "summarise", lambda inp: 1 / 0)
    code, body = get(f"{base}/api/summary/FULL")
    assert code == 500 and body == {"error": "internal server error"}


def test_routes_are_404_when_book_screens_are_not_enabled(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    httpd = server.create_server("full", False, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert get(f"http://127.0.0.1:{httpd.server_address[1]}/api/summary/FULL")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
