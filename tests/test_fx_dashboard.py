"""Explainability layer + candlestick dashboard export."""
import pytest

from trading_algo.forex import dashboard, explain, fx_book
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.fx_strategy import compute_targets
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2018-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


# ---- explain -------------------------------------------------------------
def test_decide_and_explain_matches_compute_targets(panel, params):
    pool = AgentPool(max_workers=1)
    weights, rationale = explain.decide_and_explain(panel, params, pool=pool)
    ct = compute_targets(panel, params, pool=pool)
    # same canonical weight function -> identical latest weights
    for s in weights.index:
        assert abs(weights[s] - ct.get(s, 0.0)) < 1e-9


def test_rationale_has_learnable_fields(panel, params):
    _, rationale = explain.decide_and_explain(panel, params, pool=AgentPool(max_workers=1))
    r = rationale["EURUSD"]
    assert {"weight", "tilt", "regime", "agents", "indicators", "text"} <= set(r)
    assert r["regime"] in ("trending", "ranging")
    assert {"trend", "breakout", "meanrev", "momentum", "carry"} <= set(r["agents"])
    assert isinstance(r["text"], str) and len(r["text"]) > 20
    assert "EUR" in r["text"] or "FLAT" in r["text"]


def test_crypto_included_in_rationale(panel, params):
    _, rationale = explain.decide_and_explain(panel, params, pool=AgentPool(max_workers=1))
    assert "BTCUSD" in rationale and "ETHUSD" in rationale


# ---- book attaches the why to trades ------------------------------------
def test_trades_carry_rationale(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    trades = [t for t in fx_book.load_state("matt")["trades"] if t.get("why")]
    assert trades, "expected at least one trade with a rationale"
    t = trades[0]
    assert isinstance(t["why"], str) and t["pair"] in t["why"]
    assert t["regime"] in ("trending", "ranging")
    assert "decisions" in fx_book.load_state("matt")


# ---- dashboard export ----------------------------------------------------
def test_dashboard_export_offline(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    out = isolated / "fx_matt.html"
    html = dashboard.export_account("matt", synthetic=True, out_path=str(out))
    assert out.exists()
    for token in ("addCandlestickSeries", "setMarkers", "Trade journal",
                  "Today's read", "BTCUSD"):
        assert token in html
    # the embedded payload carries candles + at least one pair
    payload = dashboard.build_payload("matt", synthetic=True)
    assert payload["pairs"]
    first = payload["data"][payload["pairs"][0]]
    assert first["candles"] and "open" in first["candles"][0]


def test_dashboard_index(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.init_account("partner", 5_000, "conservative")
    dashboard.build_index(["matt", "partner"], str(isolated))
    idx = (isolated / "index.html").read_text()
    assert "matt" in idx and "partner" in idx
