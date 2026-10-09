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
def account(tmp_path, monkeypatch, paper_cycle):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    name = "dash"
    pt.init_account(name, capital=300_000, synthetic=True)
    paper_cycle(name)
    return name


def test_snapshot_contract(account):
    snap = api.build_snapshot(account, synthetic=True)
    for key in ("account", "base_currency", "kpis", "allocations", "fx",
                "equity_curve", "sleeve_curves", "sleeves", "blotter"):
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


# --- cash events: a dividend is income, never a fill on any screen ----------
def _with_a_dividend(account):
    """The cycled book plus one 0.50 dividend on a held name, credited to cash
    exactly as paper_trade books it (a DIV row; shares unchanged)."""
    state = pt.load_state(account)
    key, sleeve = next((k, s) for k, s in state["sleeves"].items() if s["positions"])
    ticker, shares = next(iter(sleeve["positions"].items()))
    sleeve["cash"] += shares * 0.5
    state["trades"].append({"date": state["equity_history"][-1][0], "region": key,
                            "ticker": ticker, "side": "DIV", "shares": shares,
                            "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0,
                            "currency": sleeve["currency"]})
    pt.save_state(account, state)
    fills = [t for t in state["trades"] if t["side"] != "DIV"]
    return state, fills, shares * 0.5, sleeve["currency"]


def test_a_dividend_is_income_not_a_fill_on_the_dashboard(account):
    """The SPA builds its blotter, trade feed, LAST FILL and fill counts from
    `blotter`, so the payload carries fills only; the dividend reaches the
    screen as INCOME, a part of NET P&L. Task 12's interest rows (~250 a year
    per sleeve) take the same path and so can reach no count or list."""
    state, fills, amount, ccy = _with_a_dividend(account)
    snap = api.build_snapshot(account, synthetic=True)
    k = snap["kpis"]
    assert all(t["side"] in ("BUY", "SELL") for t in snap["blotter"])
    assert len(snap["blotter"]) == len(fills)
    assert k["n_trades"] == len(fills)
    assert k["income_base"] == pytest.approx(amount * snap["fx"].get(ccy, 1.0), abs=0.01)


def test_net_pnl_parts_sum_to_the_whole(account):
    """REAL + OPEN never summed to NET (measured on the live `full` book:
    +75.93 + 27.86 against -169.62): open lots' entry costs and the FX
    translation of the funded capital sit in neither. INCOME takes the cash
    events, and OTHER names what is left, so the parts add up."""
    _with_a_dividend(account)
    k = api.build_snapshot(account, synthetic=True)["kpis"]
    parts = k["realized_base"] + k["unrealized_base"] + k["income_base"] + k["other_base"]
    assert round(parts, 2) == k["net_pnl_base"]


def test_net_pnl_parts_are_exact_to_the_cent():
    """Rounding each part on its own and OTHER from the unrounded values is
    off by a cent: 0.004 three times gives parts 0.00 + 0.00 + 0.00 + 0.00
    against a NET of 0.01. Round the parts first and take OTHER from them."""
    parts = api._net_parts(0.004, 0.004, 0.004, 0.012)
    assert parts["net_pnl_base"] == 0.01
    assert [parts[k] for k in ("realized_base", "unrealized_base", "income_base")] \
        == [0.0, 0.0, 0.0]
    assert parts["other_base"] == 0.01
    total = sum(parts[k] for k in ("realized_base", "unrealized_base",
                                   "income_base", "other_base"))
    assert round(total, 2) == parts["net_pnl_base"]


def test_the_net_pnl_tile_shows_every_part():
    import pathlib

    from trading_algo import dashboard
    js = (pathlib.Path(dashboard.__file__).parent / "static" / "app.js").read_text()
    tile = next(line for line in js.splitlines() if "NET P&amp;L" in line)
    assert "sgnNum(k.net_pnl_base, 2)" in tile
    for part in ("k.realized_base", "k.unrealized_base", "k.income_base", "k.other_base"):
        assert f"sgnNum({part}, 2)" in tile, f"{part} at NET's precision"
