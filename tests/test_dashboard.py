"""Dashboard state API + the stdlib web server."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import re

from trading_algo import config as cfg
from trading_algo import paper_trade as pt
from trading_algo.dashboard import api, export, server


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    name = "dash"
    pt.init_account(name, capital=300_000, synthetic=True)
    pt.run_daily(name, synthetic=True)
    return name


def test_snapshot_contract(account):
    snap = api.build_snapshot(account, synthetic=True)
    for key in ("account", "base_currency", "kpis", "allocations", "fx",
                "equity_curve", "sleeve_curves", "sleeves", "recent_trades"):
        assert key in snap
    assert len(snap["sleeves"]) == len(cfg.ALLOCATIONS)
    for k in ("total_equity", "total_return", "n_positions", "cash_pct", "fees"):
        assert k in snap["kpis"]
    assert snap["kpis"]["total_equity"] > 0


def test_snapshot_positions_have_weights(account):
    snap = api.build_snapshot(account, synthetic=True)
    for sleeve in snap["sleeves"]:
        assert sleeve["regime"] in ("RISK_ON", "RISK_OFF")
        for pos in sleeve["positions"]:
            assert {"ticker", "shares", "price", "value_base", "weight"} <= set(pos)
            assert isinstance(pos["shares"], int)


def test_positions_have_change_and_pnl(account):
    snap = api.build_snapshot(account, synthetic=True)
    assert "unrealized_base" in snap["kpis"]
    for sleeve in snap["sleeves"]:
        for pos in sleeve["positions"]:
            for key in ("day_change", "change_local", "avg_cost",
                        "unrealized_pct", "unrealized_base"):
                assert key in pos


def test_financial_position_kpis(account):
    k = api.build_snapshot(account, synthetic=True)["kpis"]
    for key in ("invested_base", "cash_base", "fees_base", "realized_base",
                "unrealized_base", "net_pnl_base", "gross_exposure"):
        assert key in k
    # the position decomposes: invested + cash == total equity
    assert abs(k["invested_base"] + k["cash_base"] - k["total_equity"]) < 1.0


def test_benchmark_curve_present(account):
    snap = api.build_snapshot(account, synthetic=True)
    assert isinstance(snap.get("benchmark_curve"), list)


def test_missing_account_raises():
    with pytest.raises(FileNotFoundError):
        api.build_snapshot("does_not_exist_xyz", synthetic=True)


def test_export_is_self_contained(account, tmp_path):
    out = tmp_path / "dash.html"
    export.export(account, synthetic=True, out_path=str(out))
    html = out.read_text(encoding="utf-8")
    # inlined, not linked
    assert "<style>" in html and "__SNAPSHOT__" in html
    assert 'src="app.js"' not in html and 'href="styles.css"' not in html
    # the baked snapshot is the real account's
    assert account in html
    # no external resources anywhere
    assert not re.findall(r'(?:src|href)\s*=\s*"https?://', html)


def test_server_serves_state_and_index(account):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                server.make_handler(account, synthetic=True))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10) as r:
            assert r.status == 200
            body = json.loads(r.read())
            assert body["account"] == account
            assert "sleeves" in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            assert r.status == 200
            assert b"<" in r.read()  # some HTML came back
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_404_for_missing_account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                server.make_handler("nope", synthetic=True))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10)
            assert False, "expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
