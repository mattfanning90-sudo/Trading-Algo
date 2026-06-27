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
                  "Today's read", "BTCUSD", "Equity vs buy-and-hold",
                  "Agent scorecard", "data-tip", "tip:hover::after", "how.html",
                  "Transactions — full blotter", 'id="txntable"', "Spread bps"):
        assert token in html


def test_beginner_explanation_plain_english():
    # long position with trend + momentum agreeing, in a trending regime
    txt = dashboard._beginner_explanation(
        "BUY", 0.18, {"trend": 0.8, "momentum": 0.7, "meanrev": -0.1},
        {"ema_fast": 1.1, "ema_slow": 1.0, "adx": 30, "rsi": 62, "roc": 0.08, "ann_vol": 0.12},
        "EURUSD")
    assert "bet the price will" in txt and "EURUSD" in txt
    assert "ADX" in txt and "RSI" in txt          # terms used…
    assert "trend strength" in txt.lower() or "trend <i>strength</i>" in txt  # …and explained
    assert "volatility targeting" in txt
    # flat case explains why we DON'T trade
    flat = dashboard._beginner_explanation("LONG", 0.0, {}, {}, "EURUSD")
    assert "No position" in flat


def test_how_page_built(isolated):
    dashboard.build_how_page(str(isolated))
    h = (isolated / "how.html").read_text()
    assert "mermaid" in h and "flowchart TD" in h
    assert "Validation" in h and "Deflated Sharpe" in h
    assert "no statistically significant" in h    # honest caveat present


def test_dashboard_payload_analytics(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    p = dashboard.build_payload("matt", synthetic=True)
    # Tier-1 analytics + attribution + glossary all present.
    for key in ("book_curve", "bench_curve", "bench_metrics", "attribution",
                "glossary", "gross"):
        assert key in p
    assert len(p["bench_curve"]) > 50                  # benchmark over the window
    attr = p["attribution"]
    assert {"ensemble", "buy&hold"} <= set(attr)       # references included
    assert {"trend", "breakout", "carry"} <= set(attr) # per-agent contributions
    # benchmark metrics are real numbers
    assert "sharpe" in p["bench_metrics"]
    # every trade carries an outcome field (open/win/loss)
    for pair in p["data"].values():
        for t in pair["trades"]:
            assert t["outcome"] in ("open", "win", "loss")


def test_transactions_blotter(isolated):
    """Detailed transaction blotter: price economics + honest P&L-since."""
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    p = dashboard.build_payload("matt", synthetic=True)
    txn = p["transactions"]
    assert {"rows", "totals", "count", "shown"} <= set(txn)
    assert txn["count"] >= 1
    r = txn["rows"][0]
    assert {"time", "pair", "side", "dweight", "target", "price", "bid", "ask",
            "spread_bps", "notional", "cost", "last", "move", "pnl"} <= set(r)
    # bid ≤ mid ≤ ask, spread positive, cost + notional non-negative
    assert r["bid"] <= r["price"] <= r["ask"]
    assert r["spread_bps"] > 0
    assert r["cost"] >= 0 and r["notional"] >= 0
    # cost equals half the spread crossed on the notional traded (matches the book)
    from trading_algo.forex.pairs import get_pair
    pr = get_pair(r["pair"])
    assert r["cost"] == pytest.approx(0.5 * pr.spread_fraction(r["price"]) * r["notional"],
                                      rel=1e-2)        # values are rounded for display
    assert {"cost", "notional", "pnl"} <= set(txn["totals"])


def test_benchmark_aligned_to_book_inception(isolated):
    """The buy-and-hold benchmark is clipped to the book's live window and
    re-based to 100 on day one, so book vs benchmark is an honest comparison."""
    fx_book.init_account("matt", 5_000, "balanced")
    # Craft a 30-bar history whose dates fall inside the synthetic price panel.
    dates = [d.strftime("%Y-%m-%d")
             for d in synthetic_panel(["EURUSD"])["EURUSD"].index][-30:]
    state = fx_book.load_state("matt")
    state["equity_history"] = [[d, 5_000.0 + i] for i, d in enumerate(dates)]
    state["positions"] = {"EURUSD": 0.2, "BTCUSD": -0.1}
    fx_book.save_state("matt", state)

    p = dashboard.build_payload("matt", synthetic=True)
    bc, kc = p["book_curve"], p["bench_curve"]
    assert bc and kc
    # both curves start at 100 on the SAME day
    assert bc[0]["time"] == dates[0] and abs(bc[0]["value"] - 100.0) < 1e-6
    assert kc[0]["time"] == dates[0] and abs(kc[0]["value"] - 100.0) < 1e-6
    # benchmark is clipped to the book window (~30 bars), not the full 180
    assert len(kc) <= len(dates) + 2
    # open positions are surfaced, sorted by absolute size
    syms = [x["sym"] for x in p["positions"]]
    assert syms[0] == "EURUSD" and "BTCUSD" in syms


def test_dashboard_index(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.init_account("partner", 5_000, "conservative")
    dashboard.build_index(["matt", "partner"], str(isolated))
    idx = (isolated / "index.html").read_text()
    assert "matt" in idx and "partner" in idx
