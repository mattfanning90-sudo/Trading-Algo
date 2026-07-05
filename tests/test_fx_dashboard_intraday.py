"""True-bar (60m) analytics panel for intraday books — round-2 item 4.

The daytrader book trades hourly bars; its dashboard page must be built AT
that bar: genuinely intraday candle timestamps, trade markers on their own
hourly candle, outcomes judged over ``_OUTCOME_BARS`` of the BOOK'S bars, and
the buy-and-hold bench built from the same 60m panel (``bench_ppy`` ==
``book_ppy``). The neutral 'intraday' outcome and the DAILY-proxy honesty
note survive ONLY on the degraded fallback path (an empty or shorter-than-
warm-up intraday fetch — Yahoo caps 60m history at ~730 days and offline/CI
builds have no feed at all).
"""
import pandas as pd
import pytest

from trading_algo.forex import dashboard, fx_book, fx_data
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_strategy import min_history


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


def _daytrader_with_hourly_trades():
    """A daytrader-style book whose equity keys + trades sit exactly on the
    synthetic 60m panel grid — the very panel feeds.load(interval='60m',
    synthetic=True) hands build_payload."""
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    panel = fx_data.synthetic_panel(st["symbols"], start="2025-01-01",
                                    end="2025-04-01", freq="60m")
    df = panel["EURUSD"]
    idx = df.index
    keys = idx[-120:]                                  # ~a week of hourly equity
    st["equity_history"] = [[t.strftime("%Y-%m-%d %H:%M"),
                             round(10_000.0 * (1 + 0.0005 * i), 2)]
                            for i, t in enumerate(keys)]
    st["equity"] = st["equity_history"][-1][1]
    ts_judged, ts_open = idx[-60], idx[-4]             # judged / still-open trades
    st["trades"] = [
        {"date": ts_judged.strftime("%Y-%m-%d %H:%M"), "pair": "EURUSD",
         "side": "BUY", "delta_weight": 0.2, "target_weight": 0.2,
         "price": float(df["close"].loc[ts_judged]), "regime": "trending"},
        {"date": ts_open.strftime("%Y-%m-%d %H:%M"), "pair": "EURUSD",
         "side": "SELL", "delta_weight": -0.2, "target_weight": 0.0,
         "price": float(df["close"].loc[ts_open]), "regime": "ranging"},
    ]
    fx_book.save_state("daytrader", st)
    return st, df, ts_judged, ts_open


def test_intraday_book_builds_true_60m_panel(isolated):
    st, df, ts_judged, ts_open = _daytrader_with_hourly_trades()
    p = dashboard.build_payload("daytrader", synthetic=True)

    # candles are genuinely intraday: hour-carrying keys, several per calendar day
    candles = p["data"]["EURUSD"]["candles"]
    times = [c["time"] for c in candles]
    assert times and all(" " in t for t in times)
    assert len({t[:10] for t in times}) < len(times)   # candles share a date
    assert len(candles) <= 180                          # bounded display window

    # trade markers land on their OWN hourly candle
    trades = p["data"]["EURUSD"]["trades"]
    tj = next(t for t in trades if t["time"] == ts_judged.strftime("%Y-%m-%d %H:%M"))
    to = next(t for t in trades if t["time"] == ts_open.strftime("%Y-%m-%d %H:%M"))
    assert tj["time"] in times and to["time"] in times

    # outcomes are judged on _OUTCOME_BARS of the BOOK'S bars — the neutral
    # 'intraday' outcome does not exist on the true-bar panel
    assert all(t["outcome"] in ("win", "loss", "open") for t in trades)
    assert tj["outcome"] in ("win", "loss") and tj["fwd_return"] is not None
    closes = df["close"]
    i = closes.index.get_loc(ts_judged)
    expected = float(closes.iloc[i + dashboard._OUTCOME_BARS] / tj["price"] - 1.0)
    assert tj["fwd_return"] == pytest.approx(expected, abs=1e-4)
    assert to["outcome"] == "open" and to["fwd_return"] is None

    # bench: built from the SAME 60m panel, annualised at the book's own ppy
    assert p["book_ppy"] == pytest.approx(24 * 365.25, abs=1)
    assert p["bench_ppy"] == p["book_ppy"]
    assert p["bench_curve"] and " " in p["bench_curve"][0]["time"]
    # no daily-proxy honesty note on the true-bar path
    assert p["signal_note"] is None
    # and the page renders with the bench_ppy-driven annualisation token
    html = dashboard.render(p)
    assert "compute(bh,BENCH_ANN)" in html and "compute(bh,252)" not in html


def test_empty_intraday_fetch_degrades_to_daily_proxy(isolated, monkeypatch):
    """A dead intraday feed (offline CI, Yahoo outage) falls back to the daily
    panel — WITH the reworded proxy note and neutral 'intraday' outcomes: the
    honesty note survives exactly where it still applies."""
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    ddf = fx_data.synthetic_panel(["EURUSD"])["EURUSD"]
    day = ddf.index[-60]
    st["equity_history"] = [
        [(day + pd.Timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), 10_000.0 + h]
        for h in range(30)]
    st["equity"] = 10_029.0
    st["trades"] = [{"date": day.strftime("%Y-%m-%d") + " 10:00",
                     "pair": "EURUSD", "side": "BUY", "delta_weight": 0.2,
                     "target_weight": 0.2, "price": float(ddf["close"].iloc[-60])}]
    fx_book.save_state("daytrader", st)

    monkeypatch.setattr(dashboard.feeds, "load", lambda *a, **k: {})
    p = dashboard.build_payload("daytrader", synthetic=True)

    # daily proxy: daily candles + the reworded honesty note
    candles = p["data"]["EURUSD"]["candles"]
    assert candles and all(" " not in c["time"] for c in candles)
    assert p["signal_note"] and "DAILY proxy panel" in p["signal_note"]
    assert "could not be fetched" in p["signal_note"]  # reworded: says WHY
    assert p["book_ppy"] > 2000 and p["bench_ppy"] == 252
    # intraday-keyed trades are NOT judged on the 10-DAILY-bar window
    tr = p["data"]["EURUSD"]["trades"]
    assert tr and tr[-1]["outcome"] == "intraday" and tr[-1]["fwd_return"] is None
    # ...but the marker still lands on the daily candle for that date
    assert tr[-1]["time"] == day.strftime("%Y-%m-%d")
    html = dashboard.render(p)
    assert "computed on a DAILY" in html


def test_short_intraday_fetch_also_degrades(isolated, monkeypatch):
    """An intraday fetch shorter than the strategy warm-up (min_history) is as
    useless as an empty one — same honest degradation."""
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    fx_book.save_state("daytrader", st)
    short = fx_data.synthetic_panel(["EURUSD", "AUDUSD"], start="2025-03-25",
                                    end="2025-04-01", freq="60m")
    assert len(fx_data.closes(short)) < min_history(profile("intraday"))
    monkeypatch.setattr(dashboard.feeds, "load", lambda *a, **k: short)
    p = dashboard.build_payload("daytrader", synthetic=True)
    assert p["signal_note"] and "DAILY proxy panel" in p["signal_note"]
    assert p["bench_ppy"] == 252
