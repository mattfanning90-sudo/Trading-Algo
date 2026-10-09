"""Dashboard state API + the stdlib web server."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import re

from trading_algo import config as cfg
from trading_algo import paper_trade as pt
from trading_algo import pnl
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
    fills = [t for t in state["trades"] if pnl.is_fill(t)]
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
    interest = sum(pnl.cash_amount(t) * snap["fx"][t["currency"]]
                   for t in state["trades"] if t["side"] == "INT")
    assert k["income_base"] == pytest.approx(
        amount * snap["fx"].get(ccy, 1.0) + interest, abs=0.01)


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


# --- one FX-rate rule for every NET part ------------------------------------
def test_the_dashboard_and_the_bridge_convert_by_the_one_rule(monkeypatch):
    """Both readers route through `fx.base_per_local` (the rule itself is
    pinned in test_fx): swap it and the dashboard's round trip and the
    bridge's rate both move with it. A USD round trip that netted 18.00 USD
    converts at the swapped rule's 2.0 to 36.00 AUD."""
    from trading_algo import attribution, fx

    asked = []

    def rule(snapshot, ccy, base):
        asked.append(ccy)
        return 2.0

    monkeypatch.setattr(fx, "base_per_local", rule)
    trades = [{"date": "2026-06-01", "region": "US", "ticker": "AAA", "side": "BUY",
               "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0,
               "currency": "USD"},
              {"date": "2026-07-01", "region": "US", "ticker": "AAA", "side": "SELL",
               "shares": 10, "fill": 102.0, "commission": 1.0, "stamp_duty": 0.0,
               "currency": "USD"}]
    assert api.closed_trades(trades, {"AUD": 1.0})["net_base"] == pytest.approx(36.0)
    assert attribution._base_per_local({"base_currency": "AUD"}, "GBP") == 2.0
    assert asked == ["USD", "GBP"]


def test_interest_is_income_on_the_net_tile_and_never_a_trade(account):
    """The fixture's book has run two sessions, so every sleeve's second run
    paid interest on the cash it held since its first, as INT rows. They are
    INCOME, converted by the one rate rule; they are in neither the trade
    count nor the blotter; and NET's parts still sum to NET to the cent."""
    from trading_algo import fx, pnl

    state = pt.load_state(account)
    paid = [t for t in state["trades"] if t["side"] == "INT"]
    assert {t["region"] for t in paid} == set(state["sleeves"])
    snap = api.build_snapshot(account, synthetic=True)
    k = snap["kpis"]
    want = sum(pnl.cash_amount(t) * fx.base_per_local(snap["fx"], t["currency"],
                                                      snap["base_currency"])
               for t in paid)
    assert want > 0 and k["income_base"] == round(want, 2)
    assert k["n_trades"] == sum(1 for t in state["trades"] if pnl.is_fill(t))
    assert all(row["side"] != "INT" for row in snap["blotter"])
    parts = k["realized_base"] + k["unrealized_base"] + k["income_base"] + k["other_base"]
    assert round(parts, 2) == k["net_pnl_base"]


def test_a_round_trip_with_no_rate_is_not_converted_at_parity():
    trades = [{"date": "2026-06-01", "region": "US", "ticker": "AAA", "side": "BUY",
               "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0,
               "currency": "USD"},
              {"date": "2026-07-01", "region": "US", "ticker": "AAA", "side": "SELL",
               "shares": 10, "fill": 110.0, "commission": 1.0, "stamp_duty": 0.0,
               "currency": "USD"}]
    closed = api.closed_trades(trades, {"AUD": 1.0})
    assert closed["rows"][0]["net_base"] is None
    assert closed["net_base"] == 0.0


def test_a_sleeve_with_no_rate_is_out_of_every_net_part_and_said_so(
        account, monkeypatch):
    """The valuation priced a sleeve with no rate at 0 while REAL and INCOME
    converted it at 1.0 — a USD amount booked as AUD. Every NET part now uses
    the valuation's rule: the sleeve is out of REAL, OPEN, INCOME and NET
    (its share of the starting capital comes off with its equity), and the
    payload names the currency so the tile can say so."""
    state = pt.load_state(account)
    us = state["sleeves"]["US"]
    ticker, shares = next(iter(us["positions"].items()))
    us["cash"] += shares * 0.5
    state["trades"].append({"date": state["equity_history"][-1][0], "region": "US",
                            "ticker": ticker, "side": "DIV", "shares": shares,
                            "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0,
                            "currency": "USD"})
    pt.save_state(account, state)
    real = pt.fx_snapshot
    monkeypatch.setattr(pt, "fx_snapshot", lambda *a, **kw: {
        c: r for c, r in real(*a, **kw).items() if c != "USD"})

    snap = api.build_snapshot(account, synthetic=True)
    k = snap["kpis"]
    assert k["unvalued"] == ["USD"]
    # the USD dividend and the USD sleeve's interest are out; the other
    # sleeves' interest is income at their own rates
    valued = sum(pnl.cash_amount(t) * snap["fx"][t["currency"]]
                 for t in state["trades"]
                 if pnl.is_cash_event(t) and t["currency"] != "USD")
    assert k["income_base"] == round(valued, 2)
    funded = state["initial_capital_base"] * (1 - state["allocations"]["US"])
    assert k["net_pnl_base"] == pytest.approx(k["total_equity"] - funded, abs=0.01)
    parts = k["realized_base"] + k["unrealized_base"] + k["income_base"] + k["other_base"]
    assert round(parts, 2) == k["net_pnl_base"]


def test_other_names_the_allocation_true_up_spread_when_it_is_on(account, monkeypatch):
    """With PAPER_ALLOCATION_REBALANCE on, the FX spread paid moving cash
    between sleeves is in NET and in no named part, so OTHER carries it."""
    monkeypatch.setattr(cfg, "PAPER_ALLOCATION_REBALANCE", False)
    assert "true-up" not in api.build_snapshot(account, synthetic=True)["kpis"]["other_note"]
    monkeypatch.setattr(cfg, "PAPER_ALLOCATION_REBALANCE", True)
    assert "true-up" in api.build_snapshot(account, synthetic=True)["kpis"]["other_note"]


def test_the_tile_says_what_it_left_out_and_what_other_holds():
    import pathlib

    from trading_algo import dashboard
    js = (pathlib.Path(dashboard.__file__).parent / "static" / "app.js").read_text()
    tile = next(line for line in js.splitlines() if "NET P&amp;L" in line)
    assert "k.other_note" in tile and "k.unvalued" in tile
    row = next(line for line in js.splitlines() if "c.net_base" in line)
    assert "c.net_base == null" in row, "a round trip with no rate shows a dash, not A$0.00"
