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
                  "Full blotter", 'id="txntable"', "Spread bps",
                  'id="riskstats"', "Drawdown (underwater)", "is it luck?",
                  'id="ddchart"', 'id="costchart"',
                  'class="subnav"', 'id="overview"', 'class="cards"',
                  'id="conviction"', 'id="pnlpair"', 'id="tradestats"',
                  "Conviction heatmap", 'id="attrib"',
                  'class="pop"', "In plain English", 'id="cmdk"', 'id="cmdin"',
                  "<kbd>1</kbd>", "IntersectionObserver",
                  'id="verdict"', 'id="eqperiod"', 'id="eqread"',
                  "function sparkline", "subscribeCrosshairMove", 'class="pbtn',
                  'id="today"', 'id="dailycard"', "Daily summary", "Market backdrop",
                  'id="books"', 'id="ago"', 'id="cbtoggle"', 'id="csvbtn"',
                  "body.cb", "data-pair", "th.sortable", "function goPair"):
        assert token in html
    # every section's plain-English explainer now lives in a hover popover on the
    # band header (Bloomberg-dense body), not inline in the flow
    assert html.count('class="pop"') >= 6
    assert '<p class="plain">' not in html
    assert '.plain{' not in html                      # dead ruleset deleted too
    # popover hover bridge: invisible ::after extends the icon's hit area and the
    # gap shrank, so the cursor can reach the popover without it closing
    assert '.band .info::after' in html
    assert 'top:150%' not in html
    # pair-nav arrow keys ignore browser shortcuts + all editable targets
    assert 'e.altKey||e.metaKey||e.ctrlKey' in html
    assert "tg.tagName==='TEXTAREA'" in html
    # each pair switch disposes the previous LWC chart (ResizeObserver leak)
    assert 'chart.remove()' in html
    # ONE timestamp parser: the freshness pill reuses toT, and toT normalises
    # plain daily dates to UNIX timestamps too (no mixed time types per series)
    assert html.count("replace(' UTC'") == 1
    assert "toT((DASH.updated||'').replace(' UTC',''))" in html
    assert "T00:00:00Z" in html
    # scorecard/attribution honestly labelled gross-of-costs
    assert 'gross of spread costs' in html.lower() or 'Gross of spread costs' in html


def test_build_payload_single_weight_engine_pass(isolated, monkeypatch):
    """Perf invariant: the whole page build runs the (expensive) weight engine
    exactly ONCE — attribution, scorecard and PBO all share that pass."""
    from trading_algo.forex import dashboard as dash, fx_strategy
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    calls = {"n": 0}
    real = fx_strategy.target_weights_history
    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(dash, "target_weights_history", counted)
    p = dash.build_payload("matt", synthetic=True)
    assert calls["n"] == 1
    # ...and the shared pass still feeds every consumer
    assert p["attribution"] and "ensemble" in p["attribution"]
    assert "dsr" in p["risk"] and "pbo" in p["risk"]
    assert p["books"] == ["matt"]


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
    assert "From AUD to a trade" in h and "flowchart LR" in h   # AUD currency flow
    # detailed beginner guide to every dashboard panel
    assert "What every panel on your dashboard means" in h
    assert "Conviction heatmap" in h and "cost wedge" in h and "PBO" in h


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


def test_risk_costs_significance(isolated):
    """Risk/cost/significance analytics: drawdown, cost wedge, per-currency
    exposure, realized vol, PSR and minimum-track-record honesty."""
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    # Craft a multi-day rising history + a couple of trades on those dates so the
    # drawdown curve, cost wedge and PSR all populate deterministically.
    st = fx_book.load_state("matt")
    st["equity_history"] = [[f"2025-01-{i:02d}", 5_000.0 * (1 + 0.001 * i)]
                            for i in range(1, 8)]
    st["trades"] = st["trades"] + [
        {"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
         "delta_weight": 0.2, "target_weight": 0.2, "price": 1.08, "regime": "trending"},
        {"date": "2025-01-04", "pair": "BTCUSD", "side": "SELL",
         "delta_weight": -0.1, "target_weight": -0.1, "price": 60000.0, "regime": "ranging"}]
    fx_book.save_state("matt", st)

    rk = dashboard.build_payload("matt", synthetic=True)["risk"]
    for key in ("drawdown", "cost_curve", "exposure", "total_cost", "target_vol",
                "psr", "realized_vol", "min_track_days", "n_obs"):
        assert key in rk
    assert len(rk["drawdown"]) == 7 and len(rk["cost_curve"]) == 7
    assert rk["n_obs"] == 6
    # cost wedge: both start at 100, gross never below net, and ends above (costs paid)
    assert abs(rk["cost_curve"][0]["net"] - 100.0) < 1e-6
    assert all(r["gross"] >= r["net"] - 1e-9 for r in rk["cost_curve"])
    assert rk["cost_curve"][-1]["gross"] > rk["cost_curve"][-1]["net"]
    assert rk["total_cost"] > 0
    # drawdown is never positive; rising history => flat at 0
    assert all(d["value"] <= 1e-9 for d in rk["drawdown"])
    # PSR is a probability; exposure decomposes pairs into currency legs
    assert rk["psr"] is not None and 0.0 <= rk["psr"] <= 1.0
    assert rk["realized_vol"] is not None
    assert "USD" in rk["exposure"]


def test_daily_summary(isolated):
    """The daily P&L summary: drivers ranked by contribution + derived currency
    strength, from the book's stored daily snapshot."""
    from trading_algo.forex import dashboard as dash
    fx_book.init_account("matt", 5_000, "balanced")
    state = fx_book.load_state("matt")
    state["currency"] = "AUD"
    state["daily"] = {
        "date": "2026-06-27", "start_equity": 5_010.0, "end_equity": 5_023.4,
        "pnl_pct": 0.0031, "carry_pct": 0.0002, "cost_pct": -0.0006,
        "net_pct": 0.00267, "net_aud": 13.4, "halted": False,
        "by_pair": [
            {"pair": "USDJPY", "weight": 0.12, "move": 0.004, "fx": 0.0, "contrib": 0.00044},
            {"pair": "SOLUSD", "weight": -0.17, "move": 0.02, "fx": 0.0, "contrib": -0.0034},
            {"pair": "EURUSD", "weight": -0.03, "move": -0.002, "fx": 0.0, "contrib": 0.00006}]}
    state["decisions"] = {"USDJPY": {"regime": "trending", "agents": {"trend": 0.8}},
                          "SOLUSD": {"regime": "ranging", "agents": {"meanrev": -0.6}}}
    fx_book.save_state("matt", state)

    d = dash.build_payload("matt", synthetic=True)["daily"]
    assert d is not None
    # drivers ranked by absolute contribution (SOLUSD biggest)
    assert d["drivers"][0]["pair"] == "SOLUSD"
    assert d["drivers"][0]["regime"] == "ranging"
    assert d["net_aud"] == 13.4
    # currency strength derived from the moves: SOL strongest, USD weakest
    s = d["strength"]
    assert "USD" in s and "SOL" in s
    assert list(s.keys())[0] == "SOL"            # sorted strongest-first
    assert s["USD"] < 0                           # USD broadly soft that day


def test_daily_catalysts_only_when_real(isolated, monkeypatch):
    """News catalysts appear ONLY when a real high-impact release hit a traded
    currency — silent otherwise (correlation, never fabricated)."""
    from trading_algo.forex import dashboard as dash, news
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    fx_book.init_account("matt", 5_000, "balanced")
    st = fx_book.load_state("matt")
    st["daily"] = {"date": "2026-06-27", "start_equity": 5_000.0, "end_equity": 5_010.0,
                   "pnl_pct": 0.002, "carry_pct": 0.0, "cost_pct": 0.0, "net_pct": 0.002,
                   "net_aud": 10.0, "halted": False,
                   "by_pair": [{"pair": "USDJPY", "weight": 0.1, "move": 0.003, "fx": 0.0, "contrib": 0.0003}]}
    fx_book.save_state("matt", st)

    # no key / no event -> silent
    assert dash.build_payload("matt", synthetic=True)["daily"]["catalysts"] == []

    # a real high-impact USD release on a traded currency -> surfaced
    monkeypatch.setattr(news, "economic_events",
                        lambda curs, date, **k: [{"currency": "USD", "event": "CPI YoY",
                                                  "impact": "High", "actual": "3.1%"}]
                        if "USD" in curs else [])
    cats = dash.build_payload("matt", synthetic=True)["daily"]["catalysts"]
    assert cats and cats[0]["currency"] == "USD" and cats[0]["event"] == "CPI YoY"


def test_attribution_conviction_and_advanced_significance(isolated):
    """P&L attribution, trade-quality stats, conviction heatmap, and DSR/PBO."""
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    # multi-day history so trade stats + DSR populate
    st = fx_book.load_state("matt")
    st["equity_history"] = [[f"2025-01-{i:02d}", 5_000.0 * (1 + 0.002 * (i % 3 - 1))]
                            for i in range(1, 9)]
    fx_book.save_state("matt", st)
    p = dashboard.build_payload("matt", synthetic=True)

    # 1. P&L attribution
    attr = p["pnl_attribution"]
    assert {"by_pair", "by_side", "by_regime"} <= set(attr)
    assert set(attr["by_side"]) == {"long", "short"}
    # 2. trade-quality stats
    ts = p["trade_stats"]
    assert ts["trades"] >= 1 and ts["turnover"] >= 0
    assert {"profit_factor", "expectancy", "win_streak", "avg_win", "avg_loss"} <= set(ts)
    # 3. conviction heatmap: tilt in [-1,1] per pair
    conv = p["conviction"]
    assert conv and all(-1.0001 <= c["tilt"] <= 1.0001 for c in conv)
    assert "pair" in conv[0] and "regime" in conv[0]
    # 4. DSR + PBO surfaced alongside PSR
    assert "dsr" in p["risk"] and "pbo" in p["risk"]
    assert p["risk"]["dsr"] is None or 0.0 <= p["risk"]["dsr"] <= 1.0
    assert p["risk"]["pbo"] is None or 0.0 <= p["risk"]["pbo"] <= 1.0


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


# ---- audit round 2: units, resilience, escaping, dedup --------------------
def test_glossary_labels_scorecard_gross():
    assert "gross of spread costs" in dashboard.GLOSSARY["Agent scorecard"]
    assert "gross of spread costs" in dashboard.GLOSSARY["Attribution"]


def test_curve_metrics_mixed_date_formats():
    """One --bar 60m override run on a daily book mixes 'YYYY-MM-DD' and
    'YYYY-MM-DD HH:MM' equity keys — must not raise on pandas 2/3."""
    m = dashboard._curve_metrics(
        ["2026-07-01", "2026-07-02", "2026-07-03 09:00", "2026-07-04",
         "2026-07-05", "2026-07-06", "2026-07-07"],
        [100.0, 101.0, 100.5, 101.5, 102.0, 101.0, 103.0])
    assert "total_return" in m and "sharpe" in m


def test_main_all_survives_corrupt_state(isolated, capsys):
    """--all must not silently drop the remaining books when one state file is
    corrupt (the CI workflows run the exporter under '|| true')."""
    fx_book.init_account("matt", 5_000, "balanced")
    (isolated / "fx_state_bad.json").write_text("{this is not json")
    dashboard.main(["--all", "--synthetic", "--out-dir", str(isolated / "public")])
    assert (isolated / "public" / "fx_matt.html").exists()
    assert (isolated / "public" / "index.html").exists()
    assert "[skip bad" in capsys.readouterr().out


def test_third_party_news_strings_escaped(isolated):
    """FMP calendar strings cross a trust boundary: they must not be able to
    terminate the inline __DATA__ script or inject markup via innerHTML."""
    fx_book.init_account("matt", 5_000, "balanced")
    p = dashboard.build_payload("matt", synthetic=True)
    evil = '</script><script>alert(1)</script><img src=x onerror=1>'
    p["news_feed"] = [{"time": "09:00", "currency": "USD", "event": evil,
                       "impact": "high", "actual": evil, "estimate": None,
                       "previous": None}]
    html = dashboard.render(p)
    assert '<\\/script' in html                        # JSON-embedded '</' escaped
    assert '</script><script>alert' not in html        # can't break out of __DATA__
    # client-side: every third-party field goes through esc() in BOTH sinks
    for token in ("esc(e.event)", "esc(e.actual)", "esc(e.estimate)",
                  "esc(e.previous)", "esc(e.currency)", "esc(e.time)"):
        assert token in html
    assert "const esc" in html


def test_blotter_full_rows_and_csv_quoting(isolated):
    """The CSV export covers ALL rows (reconciles with the footer totals) and
    is RFC-4180 quoted; only the table DISPLAY is capped at 400."""
    import pandas as pd
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    mk = lambda a, b: pd.DataFrame({"open": [a, b], "high": [a, b],
                                    "low": [a, b], "close": [a, b]}, index=idx)
    panel = {"EURUSD": mk(1.08, 1.08), "AUDUSD": mk(0.66, 0.66)}
    st = {"equity_history": [["2025-01-02", 5_000.0]], "equity": 5_000.0,
          "initial_capital": 5_000.0,
          "trades": [{"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
                      "delta_weight": 0.01, "target_weight": 0.01, "price": 1.08}
                     for _ in range(450)]}
    txn = dashboard._transactions(st, panel)
    assert len(txn["rows"]) == 450                    # full blotter, no server slice
    assert txn["count"] == 450 and txn["shown"] == 400
    # footer totals reconcile exactly with summing the exported rows
    assert txn["totals"]["cost"] == round(sum(r["cost"] for r in txn["rows"]), 2)
    # template: CSV maps over the FULL T.rows with doubled-quote escaping,
    # and the table renderer only slices for display
    page = dashboard._PAGE
    assert "T.rows.map(r=>kk.map(k=>q(r[k]))" in page
    assert 'replace(/"/g' in page
    assert "rows=rows.slice(0,400)" in page


def test_risk_costs_reuse_blotter_costs(isolated):
    """Tripwire: the risk card's total_cost is DERIVED from the blotter rows
    (one cost-formula site), so the two can never disagree."""
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    p = dashboard.build_payload("matt", synthetic=True)
    txn, rk = p["transactions"], p["risk"]
    assert rk["total_cost"] == round(sum(r["cost"] for r in txn["rows"]), 2)
    assert rk["total_cost"] == txn["totals"]["cost"]


def test_blotter_prefers_stored_aud_per_quote():
    """Round-2 item 1 (consumption): a trade's stored execution-time
    aud_per_quote drives its blotter P&L — factor = aud_per_quote_now / stored
    — while legacy trades without the stamp (or with a null / non-positive
    stamp) fall back to today's-frame reconstruction. The hand-built panel is
    rigged so the two paths give DIFFERENT numbers, proving the preference."""
    import pandas as pd
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    mk = lambda a, b: pd.DataFrame({"open": [a, b], "high": [a, b],
                                    "low": [a, b], "close": [a, b]}, index=idx)
    # AUDUSD 0.50 -> 0.80: aud_per_USD was 2.0 at entry (reconstruction would
    # use THIS), and is 1.25 now. The stored stamp says 4.0 — deliberately
    # different from the panel's own entry-day 2.0.
    panel = {"EURUSD": mk(1.00, 1.10), "AUDUSD": mk(0.50, 0.80)}
    base = {"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
            "delta_weight": 0.2, "target_weight": 0.2, "price": 1.00}
    st = {"equity_history": [["2025-01-02", 5_000.0]], "equity": 5_000.0,
          "initial_capital": 5_000.0,
          "trades": [dict(base, aud_per_quote=4.0),     # stored ≠ panel's 2.0
                     dict(base),                         # legacy: no key
                     dict(base, aud_per_quote=None),     # null stamp
                     dict(base, aud_per_quote=-1.0)]}    # corrupt stamp
    txn = dashboard._transactions(st, panel)
    rows = list(reversed(txn["rows"]))                  # back to write order
    # stored path: 0.2 * ((1.10/1.00) * (1.25/4.0) - 1) * 5000 = -656.25
    assert rows[0]["pnl"] == pytest.approx(-656.25, abs=0.01)
    # reconstruction path: factor = 1.25/2.0 = 0.625 -> -312.50 for all three
    recon = 0.2 * ((1.10 / 1.00) * (1.25 / 2.0) - 1.0) * 5_000.0
    for r in rows[1:]:
        assert r["pnl"] == pytest.approx(recon, abs=0.01)
    # ...and the two genuinely differ: the stored value took precedence
    assert abs(rows[0]["pnl"] - recon) > 100


def test_blotter_stored_factor_guarded_when_now_side_underivable():
    """Architect guard (round-2 item 1): a VALID stored aud_per_quote must NOT
    be used when the *now-side* aud_per_quote can't be derived from today's
    frame — a crypto-only / hub-less book with no AUDUSD to translate through.
    Dividing by a phantom now-rate would mismark the trade; the now-side guard
    (``if now and now > 0``) instead falls back to reconstruction, which with no
    hub yields factor 1.0 — the honest pure pair move."""
    import pandas as pd
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    mk = lambda a, b: pd.DataFrame({"open": [a, b], "high": [a, b],
                                    "low": [a, b], "close": [a, b]}, index=idx)
    panel = {"EURUSD": mk(1.00, 1.10)}          # NO AUDUSD hub -> now-side underivable
    st = {"equity_history": [["2025-01-02", 5_000.0]], "equity": 5_000.0,
          "initial_capital": 5_000.0,
          "trades": [{"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
                      "delta_weight": 0.2, "target_weight": 0.2, "price": 1.00,
                      "aud_per_quote": 4.0}]}   # valid stamp, but unusable now
    pnl = dashboard._transactions(st, panel)["rows"][0]["pnl"]
    # guard -> reconstruction -> factor 1.0 -> 0.2 * (1.10/1.00 - 1) * 5000 = +100.
    # Without the guard the phantom now=0 would collapse the mark toward -1000.
    assert pnl == pytest.approx(100.0, abs=0.01)


def test_dashboard_routes_formulas_through_marks():
    """Round-2 item 2 (dashboard half): the blotter's cost/mark/annualisation
    formulas are IMPORTS of trading_algo.forex.marks — no local formula bodies.
    Together with test_fx_marks.py::test_fx_book_routes_through_marks_only
    (the book half) this is the 'can never diverge' consistency pair."""
    import inspect
    from trading_algo.forex import marks
    assert dashboard._trade_cost is marks.trade_cost
    assert dashboard._trade_mark is marks.trade_mark
    assert dashboard._ppy is marks.periods_per_year
    src = inspect.getsource(dashboard)
    assert "def _trade_cost" not in src and "def _trade_mark" not in src
    assert "0.5 * pair.spread_fraction" not in src      # no local cost body
    assert "0.5 * spread_fraction" not in src
    assert "* fxf - 1.0" not in src                     # no local mark body


def test_blotter_matches_book_charge(isolated):
    """Cross-tying regression (CLAUDE.md 'costs always on' / one-formula): the
    dashboard's blotter cost must reconcile with what the BOOK actually charged
    (state['daily'].cost_pct × that day's equity), so a future change to
    fx_book's charging breaks THIS test instead of silently diverging."""
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    st = fx_book.load_state("matt")
    assert st["trades"], "expected the synthetic run to trade"
    p = dashboard.build_payload("matt", synthetic=True)
    equity_on, _ = dashboard._equity_lookup(st)
    daily = st["daily"]
    expected = -daily["cost_pct"] * equity_on(daily["date"])
    # tolerance = stored rounding only (cost_pct 6dp, row costs 4dp)
    assert p["transactions"]["totals"]["cost"] == pytest.approx(expected, abs=0.05)
    assert expected > 0
