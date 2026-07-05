"""Tests for the terminal dashboard's multi-account backend:
registry discovery, FX snapshots, the all-accounts overview, meta, the FIFO
closed-trade ledger, the new server routes and the export payload map."""
import io
import json
import os

import pytest

import trading_algo.paper_trade as pt
from trading_algo.dashboard import api, export, fx_api, meta, overview, registry, server
from trading_algo.forex import fx_book


# ---------------------------------------------------------------------------
# fixtures: one synthetic equity account + one hand-built FX book on disk
# ---------------------------------------------------------------------------
FX_STATE = {
    "account": "matt", "currency": "AUD", "profile": "balanced", "source": "yahoo",
    "bar": "1d", "symbols": ["EURUSD", "BTCUSD"], "initial_capital": 5000.0,
    "equity": 4900.0, "positions": {"EURUSD": -0.10, "BTCUSD": 0.05},
    "last_close": {"EURUSD": 1.1440, "BTCUSD": 62609.0},
    "last_bar_date": "2026-07-04", "peak_equity": 5070.0,
    "risk_halted": False, "halt_cooldown": 0,
    "trades": [{"date": "2026-06-20", "pair": "EURUSD", "side": "SELL",
                "delta_weight": -0.10, "target_weight": -0.10, "price": 1.15,
                "why": "SHORT EURUSD", "regime": "trending", "agents": {}}],
    "equity_history": [["2026-06-20", 4997.58], ["2026-07-04", 4900.0]],
    "decisions": {
        "EURUSD": {"weight": -0.10, "tilt": -0.5, "regime": "trending",
                   "agents": {"trend": -0.6, "breakout": -1.0, "meanrev": 0.0,
                              "momentum": -0.9, "carry": 0.05, "neural": 0.7},
                   "indicators": {"price": 1.1440, "ann_vol": 0.05},
                   "text": "SHORT EURUSD at 1.144."},
        "BTCUSD": {"weight": 0.05, "tilt": 0.2, "regime": "ranging",
                   "agents": {"trend": 0.1, "breakout": 1.0, "meanrev": -0.1,
                              "momentum": 0.4, "carry": 0.0, "neural": 0.2},
                   "indicators": {"price": 62609.0, "ann_vol": 0.31},
                   "text": "LONG BTCUSD at 62609."},
    },
    "daily": {"date": "2026-07-04", "start_equity": 4926.0, "end_equity": 4900.0,
              "pnl_pct": -0.005, "carry_pct": 0.0, "cost_pct": -0.0002,
              "net_pct": -0.0053, "net_aud": -26.0,
              "by_pair": [{"pair": "EURUSD", "weight": -0.10, "move": 0.002,
                           "fx": -0.003, "contrib": -0.0034}]},
}


@pytest.fixture()
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=300_000, synthetic=True)
    pt.run_daily("full", synthetic=True)
    with open(os.path.join(str(tmp_path), "fx_state_matt.json"), "w") as f:
        json.dump(FX_STATE, f)
    return tmp_path


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry_discovers_both_kinds(books):
    accs = registry.discover_accounts()
    kinds = {a["key"]: a["kind"] for a in accs}
    assert kinds == {"FULL": "equity", "MATT": "fx"}
    full = registry.resolve("full")
    assert full and full["kind"] == "equity"
    assert registry.resolve("MATT")["account"] == "matt"
    assert registry.resolve("nope") is None


def test_registry_micro_label(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    # micro = tiny AND single-sleeve (multi-region books keep the full UI)
    pt.init_account("small", capital=1_000, synthetic=True,
                    allocations={"US": 1.0})
    entry = registry.discover_accounts()[0]
    assert entry["micro"] is True
    assert "A$1K" in entry["label"]
    pt.init_account("tiny3", capital=1_000, synthetic=True)
    tiny3 = registry.resolve("TINY3")
    assert tiny3["micro"] is False


# ---------------------------------------------------------------------------
# fx snapshot
# ---------------------------------------------------------------------------
def test_fx_snapshot_contract(books):
    snap = fx_api.build_fx_snapshot("MATT")
    assert snap["kind"] == "fx"
    assert snap["equity"] == 4900.0
    assert snap["gross"] == pytest.approx(0.15)
    assert snap["net"] == pytest.approx(-0.05)
    assert (snap["n_long"], snap["n_short"]) == (1, 1)
    assert snap["off_peak"] == pytest.approx(4900 / 5070 - 1, abs=1e-6)
    assert snap["breaker"] == pytest.approx(0.20)   # balanced profile
    rows = snap["rows"]
    assert [r["pair"] for r in rows] == ["EURUSD", "BTCUSD"]  # sorted by |weight|
    assert len(rows[0]["agents"]) == 6                        # T·B·M·R·C·N order
    assert rows[0]["agents"][2] == pytest.approx(-0.9)        # momentum slot
    assert snap["day_pct"] == pytest.approx(-0.0053)
    assert snap["attribution"][0]["pair"] == "EURUSD"
    assert snap["regime_counts"] == {"trending": 1, "ranging": 1}


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
def test_overview_totals(books):
    ov = overview.build_overview()
    assert ov["totals"]["books"] == 2
    aum = sum(c["equity"] for c in ov["accounts"])
    assert ov["totals"]["aum"] == pytest.approx(aum)
    assert sum(c["share"] for c in ov["accounts"]) == pytest.approx(1.0, abs=0.01)
    keys = {c["key"] for c in ov["accounts"]}
    assert keys == {"FULL", "MATT"}


def test_overview_regime_hint(books):
    ov = overview.build_overview({"full": "ASX RISK-OFF"})
    card = next(c for c in ov["accounts"] if c["key"] == "FULL")
    assert card["status"] == "ASX RISK-OFF"


# ---------------------------------------------------------------------------
# FIFO closed trades
# ---------------------------------------------------------------------------
def test_closed_trades_fifo_partial():
    trades = [
        {"date": "2026-06-01", "region": "US", "ticker": "AAA", "side": "BUY",
         "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
        {"date": "2026-06-05", "region": "US", "ticker": "AAA", "side": "BUY",
         "shares": 10, "fill": 110.0, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
        {"date": "2026-07-01", "region": "US", "ticker": "AAA", "side": "SELL",
         "shares": 15, "fill": 120.0, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
    ]
    out = api.closed_trades(trades, {"USD": 1.5})
    assert out["count"] == 1 and out["wins"] == 1
    row = out["rows"][0]
    # FIFO: 10 @ 100 + 5 @ 110 -> entry avg 103.33
    assert row["qty"] == 15
    assert row["entry"] == pytest.approx((10 * 100 + 5 * 110) / 15, abs=1e-3)
    gross = 15 * 120 - (10 * 100 + 5 * 110)
    # entry costs: full first lot (1.0) + half second lot (0.5) + sell comm 1.0
    assert row["gross"] == pytest.approx(gross)
    assert row["costs"] == pytest.approx(1.0 + 0.5 + 1.0)
    assert row["net"] == pytest.approx(gross - 2.5)
    assert row["net_base"] == pytest.approx((gross - 2.5) * 1.5)
    assert row["note"] == "PARTIAL 15/20"
    assert row["held_days"] == 30


def test_realized_matches_closed_ledger(books):
    """The OVERVIEW realised-P&L tile and the closed-trades ledger are sourced
    from the same FIFO reconstruction, so they can never disagree."""
    snap = api.build_snapshot("full", synthetic=True)
    assert snap["kpis"]["realized_base"] == snap["closed"]["net_base"]


def test_closed_trades_in_snapshot(books):
    snap = api.build_snapshot("full", synthetic=True)
    assert "closed" in snap and "rows" in snap["closed"]
    assert snap["peak_equity"] > 0
    assert snap["breaker"] == pytest.approx(0.25)
    assert isinstance(snap["history"], dict)
    assert isinstance(snap["blotter"], list)
    assert snap["kind"] == "equity"
    for s in snap["sleeves"]:
        assert "fx_rate" in s and "index_ticker" in s


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
def test_meta_contract(books):
    m = meta.build_meta()
    assert m["params"]["top_n"] >= 1
    assert m["risk"]["max_drawdown_stop"] == pytest.approx(0.25)
    assert len(m["regions"]) == 4        # ASX/US/FTSE funded + TSX scaffolded
    funded = {r["key"]: r["funded"] for r in m["regions"]}
    assert funded == {"ASX": True, "US": True, "FTSE": True, "TSX": False}
    assert "matt" in m["fx_profiles"]
    assert {a["key"] for a in m["accounts"]} == {"FULL", "MATT"}


# ---------------------------------------------------------------------------
# server routes
# ---------------------------------------------------------------------------
def _get(handler_cls, path):
    class FakeRequest:
        def makefile(self, mode, *a, **k):
            if "r" in mode:
                return io.BytesIO(f"GET {path} HTTP/1.1\r\nHost: t\r\n\r\n".encode())
            return io.BytesIO()
        def sendall(self, *a):  # pragma: no cover
            pass

    responses = {}

    class Capture(handler_cls):
        def _send(self, code, body, ctype):
            responses["code"] = code
            responses["body"] = body

    Capture(FakeRequest(), ("127.0.0.1", 0), None)
    return responses


def test_server_routes(books):
    handler = server.make_handler("full", synthetic=True)
    for path in ("/api/meta", "/api/overview", "/api/account/MATT",
                 "/api/account/FULL", "/api/backtest/FULL", "/api/state"):
        resp = _get(handler, path)
        assert resp["code"] == 200, path
        json.loads(resp["body"])
    assert _get(handler, "/api/account/NOPE")["code"] == 404
    assert _get(handler, "/api/nothing")["code"] == 404


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def test_export_fx_account(books, tmp_path):
    out = tmp_path / "fxdash.html"
    export.export("matt", synthetic=True, out_path=str(out))
    html = out.read_text()
    assert "__SNAPSHOT__" in html and "__EXPORT_ACCOUNT__" in html
    assert "/api/account/MATT" in html
    assert 'src="app.js"' not in html and 'href="styles.css"' not in html


def test_export_payload_map(books):
    payloads, key = export.build_payloads("full", synthetic=True)
    assert key == "FULL"
    assert "/api/meta" in payloads and "/api/state" in payloads
    assert payloads["/api/account/FULL"]["account"] == "full"
    # exported meta is locked to the single baked account
    assert [a["key"] for a in payloads["/api/meta"]["accounts"]] == ["FULL"]


def test_export_site_bakes_every_book(books, tmp_path):
    payloads = export.build_payloads_site(synthetic=True)
    assert "/api/overview" in payloads
    keys = {a["key"] for a in payloads["/api/meta"]["accounts"]}
    assert keys == {"FULL", "MATT"}
    for k in keys:
        assert payloads[f"/api/account/{k}"]
        assert f"/api/backtest/{k}" in payloads

    out = tmp_path / "site.html"
    export.export_site(synthetic=True, out_path=str(out))
    html = out.read_text()
    assert "__EXPORT_ALL__ = true" in html
    assert "/api/overview" in html
    # self-contained: inlined assets, no external resources
    assert 'src="app.js"' not in html and 'href="styles.css"' not in html
    assert 'src="http' not in html and 'href="http' not in html
