"""Bar-unit correctness across the dashboard: the hourly (daytrader) book must
never have its bars narrated, annualised or cached as if they were days.

Pins the intraday-audit findings: _risk_costs annualisation + unit fields (0),
the verdict/significance narration formulas (6), the client metrics table
annualisation (1), the daily-proxy signal note (7), intraday trade outcomes on
the daily candle grid (39), the trade-stats unit label (25), the benchmark clip
start (42) and the shared panel cache start (23).
"""
import inspect
import math

import numpy as np
import pandas as pd
import pytest

from trading_algo import metrics
from trading_algo.forex import dashboard, fx_book, fx_pnl, marks
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


def _hourly_state(n=120, sigma=0.001, bar="60m", start="2026-06-01"):
    keys = pd.date_range(start, periods=n, freq="h")
    vals = [10_000.0]
    for i in range(n - 1):
        vals.append(vals[-1] * (1 + (sigma if i % 2 else -sigma)))
    eqh = [[d.strftime("%Y-%m-%d %H:%M"), v] for d, v in zip(keys, vals)]
    st = {"equity_history": eqh, "equity": vals[-1], "initial_capital": 10_000.0,
          "trades": [], "positions": {}}
    if bar is not None:
        st["bar"] = bar
    return st


# ---- issue 0: _risk_costs is unit-aware end-to-end -------------------------
def test_risk_costs_hourly_annualisation():
    st = _hourly_state()
    rk = dashboard._risk_costs(st, profile("intraday"), {"rows": []})
    idx = pd.to_datetime([d for d, _ in st["equity_history"]], format="mixed")
    ppy = dashboard._ppy(idx)
    assert ppy == pytest.approx(24 * 365.25)          # calendar-hourly, capped
    r = pd.Series([v for _, v in st["equity_history"]]).pct_change().dropna()
    expected = float(r.std() * np.sqrt(ppy))
    assert rk["realized_vol"] == pytest.approx(expected, rel=0.01)
    # sanity vs the sigma we injected
    assert rk["realized_vol"] == pytest.approx(0.001 * math.sqrt(ppy), rel=0.05)
    # >3x what the old hardcoded sqrt(252) formula produced (actual ratio ~5.9x)
    old = float(r.std() * np.sqrt(252))
    assert rk["realized_vol"] > 3 * old
    # authoritative mapping lookups are EXACT — no tolerance
    assert rk["unit"] == "hour" and rk["bars_per_day"] == 24
    # n_obs stays an OBSERVATION count (Bailey/LdP math unrescaled)
    assert rk["n_obs"] == len(st["equity_history"]) - 1


def test_risk_costs_curve_and_vol_are_the_layers():
    """The page's drawdown CURVE and realized-vol figure equal the metrics
    primitives at the curve's own annualisation, exactly (to the 4 dp shipped)."""
    st = _hourly_state()
    # a random walk that sets new highs, so the running peak actually moves
    rets = np.random.default_rng(3).normal(0.0005, 0.002, len(st["equity_history"]))
    st["equity_history"] = [[d, round(10_000.0 * float(g), 2)] for (d, _), g in
                            zip(st["equity_history"], np.cumprod(1 + rets))]
    rk = dashboard._risk_costs(st, profile("intraday"), {"rows": []})
    keys = [d for d, _ in st["equity_history"]]
    s = pd.Series([v for _, v in st["equity_history"]], index=keys, dtype=float)
    r = s.pct_change().dropna()
    ppy = metrics.periods_per_year(pd.to_datetime(keys, format="mixed"))
    vol = metrics.annualised_vol(r, periods_per_year=ppy)
    assert round(vol, 4) != round(float(r.std(ddof=0) * np.sqrt(ppy)), 4), \
        "the pin must tell ddof apart"
    assert rk["realized_vol"] == round(vol, 4)
    assert rk["drawdown"] == [{"time": d, "value": round(float(v), 4)}
                              for d, v in metrics.drawdown(s).items()]
    s0 = s / s.iloc[0] - 1.0                                 # no running peak
    assert [round(float(v), 4) for v in s0] != [p["value"] for p in rk["drawdown"]], \
        "the pin must tell a running peak from the first mark"


def test_risk_costs_routes_through_the_metrics_layer(monkeypatch):
    """A local formula with the same values passes the pin above; this passes
    only if _risk_costs CALLS the layer for both the curve and the vol."""
    st = _hourly_state()
    monkeypatch.setattr(metrics, "annualised_vol",
                        lambda r, *, periods_per_year: 0.4321)
    monkeypatch.setattr(metrics, "drawdown", lambda e: e * 0.0 - 0.1234)
    rk = dashboard._risk_costs(st, profile("intraday"), {"rows": []})
    assert rk["realized_vol"] == 0.4321
    assert {p["value"] for p in rk["drawdown"]} == {-0.1234}


def test_bar_unit_mapping_exact():
    st = _hourly_state(n=10, bar="1m")
    rk = dashboard._risk_costs(st, profile("intraday"), {"rows": []})
    assert rk["unit"] == "minute" and rk["bars_per_day"] == 1440

    daily = {"bar": "1d", "equity_history": [["2026-07-01", 100.0], ["2026-07-02", 101.0],
                                             ["2026-07-03", 102.0]],
             "equity": 102.0, "initial_capital": 100.0, "trades": [], "positions": {}}
    rk2 = dashboard._risk_costs(daily, profile("balanced"), {"rows": []})
    assert rk2["unit"] == "day" and rk2["bars_per_day"] == 1


def test_bar_unit_legacy_state_spacing_fallback():
    st = _hourly_state(n=30, bar=None)                 # no 'bar' key: legacy state
    rk = dashboard._risk_costs(st, profile("balanced"), {"rows": []})
    assert rk["unit"] == "hour" and rk["bars_per_day"] == 24


def test_bar_unit_single_shared_implementation():
    src = inspect.getsource(dashboard)
    assert src.count("def _bar_unit") == 1             # ONE mapping for the whole page
    # ...and both the risk card and the trade-stats card route through it
    assert "_bar_unit(" in inspect.getsource(dashboard._risk_costs)
    assert "_bar_unit(" in inspect.getsource(dashboard._trade_stats)
    # round-2 item 5: _ppy IS marks.periods_per_year (a delegation, not a copy) —
    # the ONE calendar-time annualisation convention shared with fx_book.status
    assert dashboard._ppy is marks.periods_per_year
    assert "def _ppy" not in src                       # no local implementation left
    # equity lookup extracted once too (issue 24) — THE implementation now lives
    # in fx_pnl, next to the money ledger that also prices trades by date; the
    # dashboard delegates to it rather than keeping a second copy.
    assert dashboard._equity_lookup is fx_pnl.equity_lookup
    assert "def equity_on" not in src


# ---- issue 6: verdict + significance narration in bar units ----------------
def test_template_narration_is_bar_aware():
    page = dashboard._PAGE
    assert "bars_per_day" in page
    assert "21*bpd" in page                            # months = mtd / (21*bpd)
    assert "/21)" not in page                          # old raw month division gone
    assert "days of returns" not in page               # bars narrated as bars
    assert "Math.ceil(rk.min_track_days/bpd)" in page  # bars -> trading days
    # server-side re-implementation of the two template formulas
    bpd, mtd = 24, 2552
    assert math.ceil(mtd / bpd) == 107                 # trading days
    assert round(mtd / (21 * bpd)) == 5                # months


# ---- issues 1 + 7 (round-2 item 4): client annualisation + honesty note ----
# The hourly book now gets a TRUE 60m analytics panel; the DAILY-proxy note
# survives ONLY on the degraded fallback path (empty/short intraday fetch).
def test_book_ppy_true_panel_no_note_for_hourly_book(isolated):
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    # equity keys inside the synthetic 60m panel window (2025-01..2025-04)
    st.update(_hourly_state(start="2025-02-03"))
    st["bar"] = "60m"
    fx_book.save_state("daytrader", st)
    p = dashboard.build_payload("daytrader", synthetic=True)
    assert p["book_ppy"] > 2000                        # hourly ppy ≈ 8766, not 252
    assert p["bench_ppy"] == p["book_ppy"]             # bench built at the book's bar
    assert p["signal_note"] is None                    # note only where it applies
    # the period table is annualised at each curve's OWN ppy: its ALL row is
    # the header tile, which derives ppy from the curve (a 252 would not match)
    assert "sharpe" in p["book_metrics"] and "sharpe" in p["bench_metrics"]
    assert p["book_period_metrics"]["0"] == p["book_metrics"]
    assert p["bench_period_metrics"]["0"] == p["bench_metrics"]
    html = dashboard.render(p)
    assert "book_ppy" in html and "bench_ppy" in html
    assert "ANN=252" not in html                       # hardcoded annualisation gone
    assert "Math.sqrt" not in html                     # no JS Sharpe at all
    assert "book_period_metrics" in html               # Python's numbers instead
    assert "compute(bh,252)" not in html               # hardcoded bench ann gone
    assert "DAILY proxy panel" not in html             # true-bar page: no proxy note
    # hourly-vs-daily legend is CONDITIONAL on the two ppys differing
    assert "BOOK_ANN!==BENCH_ANN" in html
    assert "annualised for its own bar spacing" in html


def test_signal_note_survives_on_daily_fallback(isolated, monkeypatch):
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    st.update(_hourly_state())
    st["bar"] = "60m"
    fx_book.save_state("daytrader", st)
    # dead intraday feed (offline CI / Yahoo outage) -> degrade to daily proxy
    monkeypatch.setattr(dashboard.feeds, "load", lambda *a, **k: {})
    p = dashboard.build_payload("daytrader", synthetic=True)
    assert p["book_ppy"] > 2000                        # the BOOK is still hourly
    assert p["bench_ppy"] == 252                       # bench fell back to daily
    assert p["signal_note"] and "DAILY proxy panel" in p["signal_note"]
    html = dashboard.render(p)
    assert "book_ppy" in html
    assert "computed on a DAILY" in html               # note rendered on the page


def test_daily_book_has_no_signal_note(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    p = dashboard.build_payload("matt", synthetic=True)
    assert p.get("signal_note") is None
    # daily books are UNCHANGED by the true-bar panel work: daily candles,
    # daily annualisation on both curves
    assert p["book_ppy"] == 252 and p["bench_ppy"] == 252
    for d in p["data"].values():
        assert all(" " not in c["time"] for c in d["candles"])
    assert p["bench_curve"] and " " not in p["bench_curve"][0]["time"]
    html = dashboard.render(p)
    assert "DAILY proxy panel" not in html


# ---- round-2 item 5: books and dashboard share ONE annualisation convention -
def test_books_and_dashboard_share_one_annualisation(isolated, capsys):
    """On one identical hourly equity history, the book-side annualised vol
    (fx_book.status) equals the dashboard's realized_vol to rounding — both
    route through marks.periods_per_year (calendar-time, THE convention)."""
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    hs = _hourly_state(n=120, sigma=0.002)
    st.update(hs)
    fx_book.save_state("daytrader", st)

    idx = pd.to_datetime([d for d, _ in hs["equity_history"]], format="mixed")
    r = pd.Series([v for _, v in hs["equity_history"]]).pct_change().dropna()
    expected = float(r.std() * np.sqrt(marks.periods_per_year(idx)))

    # dashboard side (_risk_costs routes through _ppy = marks.periods_per_year)
    rk = dashboard._risk_costs(st, profile("intraday"), {"rows": []})
    assert rk["realized_vol"] == pytest.approx(expected, rel=1e-3)

    # book side: status prints the SAME annualised vol
    capsys.readouterr()
    fx_book.status("daytrader")
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "Ann. vol" in l)
    assert f"{expected:.1%}" in line
    wrong = float(r.std() * np.sqrt(252))
    assert f"{expected:.1%}" != f"{wrong:.1%}"         # the test distinguishes
    assert f"{wrong:.1%}" not in line


# ---- issue 39: intraday trades are never judged on the 10-daily-bar grid ---
def test_intraday_trades_get_neutral_outcome():
    df = synthetic_panel(["EURUSD"])["EURUSD"].tail(60)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    day = dates[10]
    px = float(df["close"].iloc[10])
    trades = [
        {"pair": "EURUSD", "date": f"{day} 09:00", "side": "BUY",
         "price": px, "target_weight": 0.2, "delta_weight": 0.2},
        {"pair": "EURUSD", "date": f"{day} 14:00", "side": "SELL",
         "price": px * 1.004, "target_weight": 0.0, "delta_weight": -0.2},
        {"pair": "EURUSD", "date": day, "side": "BUY",
         "price": px, "target_weight": 0.2, "delta_weight": 0.2},
    ]
    pp = dashboard._pair_payload("EURUSD", df, trades, None, profile("balanced"), 60)
    out = pp["trades"]
    assert out[0]["outcome"] == "intraday" and out[0]["fwd_return"] is None
    assert out[1]["outcome"] == "intraday" and out[1]["fwd_return"] is None
    # a daily-keyed trade still gets the honest 10-bar forward window
    assert out[2]["outcome"] in ("win", "loss") and out[2]["fwd_return"] is not None
    # markers still land on the daily candle
    assert out[0]["time"] == day


# ---- issue 25: trade-stats unit from the authoritative 'bar' field ---------
def test_trade_stats_unit_from_bar_field():
    st = _hourly_state(n=6, bar="1m")   # keys hourly but the BOOK says 1m bars
    st["equity_history"] = [["2026-07-01 09:0%d" % i, 100.0 + i] for i in range(6)]
    assert dashboard._trade_stats(st)["unit"] == "minute"

    # one stray hourly key (a single --bar 60m override run) must NOT flip a
    # daily book's whole card to per-hour labels
    daily = {"bar": "1d", "trades": [],
             "equity_history": [["2026-07-01", 100.0], ["2026-07-02", 101.0],
                                ["2026-07-03 14:00", 102.0], ["2026-07-04", 103.0]]}
    assert dashboard._trade_stats(daily)["unit"] == "day"

    # legacy state without 'bar': spacing sniff still says hour
    legacy = {"trades": [],
              "equity_history": [["2026-07-01 09:00", 100.0], ["2026-07-01 10:00", 101.0],
                                 ["2026-07-01 11:00", 102.0], ["2026-07-01 12:00", 103.0]]}
    assert dashboard._trade_stats(legacy)["unit"] == "hour"


# ---- issue 42: benchmark clip includes the book's own first day ------------
def test_bench_clip_includes_intraday_first_day(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    dates = [d.strftime("%Y-%m-%d")
             for d in synthetic_panel(["EURUSD"])["EURUSD"].index][-10:]
    st = fx_book.load_state("matt")
    st["equity_history"] = [[dates[0] + " 09:00", 5_000.0],
                            [dates[1], 5_010.0], [dates[2], 5_020.0]]
    fx_book.save_state("matt", st)
    p = dashboard.build_payload("matt", synthetic=True)   # also: the mixed daily/
    # hourly key parse (issues 10/12) must not raise anywhere in the build
    assert p["bench_curve"][0]["time"] == dates[0]        # previously dates[1]


# ---- issue 23: one shared fetch start regardless of profile need -----------
def test_panel_start_shared_across_profiles():
    # intraday need (594) and balanced need (1110) must produce ONE start
    # string, so every account hits the same "{sym}:{start}:{end}:1d" cache key
    assert dashboard._panel_start(594) == dashboard._panel_start(1110)
    # ...while a hypothetical LARGER future need still widens the fetch (floor)
    assert dashboard._panel_start(5000) < dashboard._panel_start(1110)


def test_period_metrics_come_from_python(isolated):
    """The 1W/1M/3M/ALL table is computed server-side, per period, at the
    curve's own annualisation — the page only renders it."""
    fx_book.init_account("matt", 5_000, "balanced")
    # init_account seeds an EMPTY equity_history (no curve, nothing to slice),
    # so give the book one long enough for every period to carry a Sharpe.
    dates = synthetic_panel(["EURUSD"])["EURUSD"].index[-130:]
    vals = 5_000 * np.cumprod(1 + np.random.default_rng(7).normal(0.0002, 0.006, 130))
    st = fx_book.load_state("matt")
    st["equity_history"] = [[d.strftime("%Y-%m-%d"), round(float(v), 2)]
                            for d, v in zip(dates, vals)]
    fx_book.save_state("matt", st)
    p = dashboard.build_payload("matt", synthetic=True)
    bm = p["book_period_metrics"]
    assert set(bm) == {"0", "7", "30", "90"}
    assert all("sharpe" in m for m in bm.values())   # not a vacuous {} == {}
    assert bm["0"] == p["book_metrics"]          # ALL == the header tile
    assert p["bench_period_metrics"]["0"] == p["bench_metrics"]


def test_curve_metrics_route_through_the_metrics_layer():
    """No local Sharpe: the page's numbers ARE trading_algo.metrics'."""
    from trading_algo import metrics
    from trading_algo.forex.fx_config import FX_RISK_FREE
    idx = pd.date_range("2026-06-01", periods=40, freq="B")
    rng = np.random.default_rng(11)
    vals = list(10_000 * np.cumprod(1 + rng.normal(0.0003, 0.008, 40)))
    m = dashboard._curve_metrics([d.strftime("%Y-%m-%d") for d in idx], vals)
    s = pd.Series(vals, index=idx, dtype=float)
    r = s.pct_change().dropna()
    assert m["sharpe"] == round(metrics.sharpe(r, periods_per_year=252,
                                               risk_free=FX_RISK_FREE).value, 2)
    assert m["vol"] == round(metrics.annualised_vol(r, periods_per_year=252), 4)
    assert m["max_dd"] == round(metrics.max_drawdown(s).value, 4)


def test_period_metrics_slice_each_window():
    """Each period button's row is measured over ITS window only — the last
    `days` calendar days, boundary inclusive (the chart's own `cut()` rule) —
    not over the whole curve."""
    idx = pd.date_range("2026-01-01", periods=120, freq="D")
    vals = 100 * np.cumprod(1 + np.random.default_rng(3).normal(0.0, 0.01, 120))
    curve = [{"time": d.strftime("%Y-%m-%d"), "value": float(v)}
             for d, v in zip(idx, vals)]
    pm = dashboard._period_metrics(curve, 252)
    # a `days` window on a daily curve holds days+1 marks: return is from the
    # mark `days` back to the last one
    want = {"0": vals[-1] / vals[0] - 1.0,
            **{str(d): vals[-1] / vals[-(d + 1)] - 1.0 for d in (7, 30, 90)}}
    assert len({round(v, 4) for v in want.values()}) == 4    # windows distinguishable
    for key, ret in want.items():
        assert pm[key]["total_return"] == round(float(ret), 4), key


# ---- Task 4d: ALL row and header tile are one computation on one series ----
# Regression for the bug at 4337c96: `_curve_metrics` (the header tile) ran on
# the FULL-PRECISION equity/bench series, but `_period_metrics` (the period
# table) ran on the `*_curve` list already rounded to 4dp for the chart. A bar
# whose TRUE return is tiny but positive (here +0.0000003, same order as the
# brief's 100.00000 -> 100.00003) rounds to a flat 0.0 bar at 4dp and silently
# drops out of the win-rate there, while the tile — reading the unrounded
# series — counts it as a win. Engineered so the two sides can only agree if
# they measure the SAME (full-precision) series.
_TASK4D_RETS = (0.02, -0.015, 0.01, -0.02, 0.0, 0.0000003, 0.0, 0.015)


def _task4d_curve(base=100.0):
    vals = [base]
    for r in _TASK4D_RETS:
        vals.append(vals[-1] * (1 + r))
    return vals


def test_book_all_row_equals_header_tile_despite_rounding(isolated):
    """The real code path: inject an equity history with the engineered tiny
    bar and check the dashboard's own payload, not a hand-rolled copy of it."""
    fx_book.init_account("matt", 5_000, "balanced")
    vals = _task4d_curve(base=100.0)   # base is eqh[0][1] -> curve rebases to itself
    dates = pd.date_range("2026-01-01", periods=len(vals), freq="D")
    st = fx_book.load_state("matt")
    st["equity_history"] = [[d.strftime("%Y-%m-%d"), v] for d, v in zip(dates, vals)]
    st["equity"] = vals[-1]
    fx_book.save_state("matt", st)
    p = dashboard.build_payload("matt", synthetic=True)
    assert p["book_metrics"]["win_rate"] == 0.5     # full precision: 4 of 8 bars win
    assert p["book_period_metrics"]["0"] == p["book_metrics"]      # ALL == the tile


def test_bench_win_rate_matches_header_tile_through_build_payload(isolated, monkeypatch):
    """The BENCH side, through the REAL `build_payload` path — this is where
    the original bug was actually found (Task 23's 10-symbol universe: bench
    win rate 0.481 in the ALL row vs 0.488 in the tile).

    `synthetic=True` reaches the panel through `_panel()` ->
    `fx_data.synthetic_panel(symbols)` — ONE call site. Patching that instead
    of `fx_data.closes` matters: `_signal_parts` makes its own independent
    `fx_data.closes(panel)` call inside `build_payload`, so patching `closes`
    would silently redirect that unrelated computation too.

    Every symbol's tail is forced to the SAME engineered values so the
    cross-sectional mean in `bh_ret.mean(axis=1)` is exactly that sequence,
    not an average across different per-symbol paths. The book's own equity
    history is given the SAME tail dates so the bench clip
    (`w = closes_df[closes_df.index >= start]`, the book's own start) lands
    on exactly that window.
    """
    real_synthetic_panel = dashboard.fx_data.synthetic_panel
    vals = _task4d_curve(base=100.0)

    def rigged_synthetic_panel(symbols, *a, **k):
        panel = real_synthetic_panel(symbols, *a, **k)
        for df in panel.values():
            tail_idx = df.index[-len(vals):]
            for col in ("open", "high", "low", "close"):
                df.loc[tail_idx, col] = vals
        return panel

    monkeypatch.setattr(dashboard.fx_data, "synthetic_panel", rigged_synthetic_panel)

    fx_book.init_account("matt", 5_000, "balanced")
    tail_dates = real_synthetic_panel(["EURUSD"])["EURUSD"].index[-len(vals):]
    st = fx_book.load_state("matt")
    st["equity_history"] = [[d.strftime("%Y-%m-%d"), 5_000.0] for d in tail_dates]
    st["equity"] = 5_000.0
    fx_book.save_state("matt", st)

    p = dashboard.build_payload("matt", synthetic=True)
    assert p["bench_metrics"]["win_rate"] == 0.5     # full precision: 4 of 8 bars win
    assert p["bench_period_metrics"]["0"] == p["bench_metrics"]      # ALL == the tile
