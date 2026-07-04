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

from trading_algo.forex import dashboard, fx_book
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
    assert src.count("def _ppy") == 1
    # equity lookup extracted once too (issue 24)
    assert src.count("def equity_on") == 1


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


# ---- issues 1 + 7: client annualisation + daily-proxy signal note ----------
def test_book_ppy_and_signal_note_for_hourly_book(isolated):
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    st.update(_hourly_state())
    st["bar"] = "60m"
    fx_book.save_state("daytrader", st)
    p = dashboard.build_payload("daytrader", synthetic=True)
    assert p["book_ppy"] > 2000                        # hourly ppy ≈ 8766, not 252
    assert p["signal_note"] and "DAILY proxy panel" in p["signal_note"]
    html = dashboard.render(p)
    assert "book_ppy" in html
    assert "ANN=252" not in html                       # hardcoded annualisation gone
    assert "computed on a DAILY" in html               # note rendered on the page
    assert "compute(bh,252)" in html                   # bench stays daily-annualised


def test_daily_book_has_no_signal_note(isolated):
    fx_book.init_account("matt", 5_000, "balanced")
    p = dashboard.build_payload("matt", synthetic=True)
    assert p.get("signal_note") is None
    html = dashboard.render(p)
    assert "DAILY proxy panel" not in html


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
