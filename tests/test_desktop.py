"""Native desktop launcher + non-blocking server helper."""
import importlib.util
import json
import threading
import urllib.request

import pytest

from trading_algo import paper_trade as pt
from trading_algo.dashboard import desktop, server


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("deskacct", capital=300_000, synthetic=True)
    pt.run_daily("deskacct", synthetic=True)
    return "deskacct"


def test_create_server_serves_on_free_port(account):
    httpd = server.create_server(account, synthetic=True, port=0)
    port = httpd.server_address[1]
    assert port > 0
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10) as r:
            assert r.status == 200
            assert json.loads(r.read())["account"] == account
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_launch_requires_webview():
    """Without pywebview installed, launch() must fail loudly and early."""
    if importlib.util.find_spec("webview") is not None:
        pytest.skip("pywebview is installed in this environment")
    with pytest.raises(SystemExit):
        desktop.launch(account="full", synthetic=True)
