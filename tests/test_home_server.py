"""The server serves the home page and its API only when a home callable is wired."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from trading_algo.dashboard import server


@pytest.fixture()
def serve():
    started = []

    def _start(home=None):
        httpd = server.create_server("main", False, "127.0.0.1", 0, home=home)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"
    yield _start
    for h in started:
        h.shutdown()
        h.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get("Content-Type"), r.read().decode()


def test_home_page_is_served(serve):
    status, ctype, body = get(serve() + "/home")
    assert status == 200 and ctype.startswith("text/html")
    assert 'src="home.js"' in body and 'href="home.css"' in body


def test_api_home_calls_the_wired_builder(serve):
    calls = []

    def home(sync_now=False):
        calls.append(sync_now)
        return {"v": 1, "verdict": "clear"}
    base = serve(home)
    assert json.loads(get(base + "/api/home")[2]) == {"v": 1, "verdict": "clear"}
    get(base + "/api/home?sync=1")
    assert calls == [False, True]


def test_api_home_is_404_when_not_wired(serve):
    with pytest.raises(urllib.error.HTTPError) as err:
        get(serve() + "/api/home")
    assert err.value.code == 404


def test_terminal_still_served_at_root(serve):
    status, ctype, body = get(serve() + "/")
    assert status == 200 and "home.js" not in body
