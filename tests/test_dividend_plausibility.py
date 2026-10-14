"""A dividend value the price contradicts is rejected, the same way in both engines.

Building the dividend-adjusted close ourselves (Task 21b) removed Yahoo's 1/100
London bug and exposed dividend VALUES its own prices contradict: AV.L
2022-05-16 claims 25.5% of the price on a day the close fell 2.2%, MRO.L's
2012-15 values are ~6.9x too large, DHR's Fortive spin-off is booked as both a
split and a dividend. A real dividend that large takes about itself off the
price on its ex-date; a typo does not. `data_quality.implausible_dividends` is
the one rule, and both the backtest's prices (`data._download_primary`) and the
paper book's cash credit (`data.dividends`) go through it.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from trading_algo import data, data_quality, notifications
from trading_algo import paper_trade as pt
from trading_algo.regions import get_region

IDX = pd.bdate_range("2026-03-02", periods=5)          # ex-date = IDX[2], 03-04
START, END = "2026-03-02", "2026-03-07"


def _raw(close: list, div: list, ticker: str = "AAA") -> pd.DataFrame:
    """A yfinance `auto_adjust=False, actions=True` frame for one name."""
    df = pd.DataFrame({("Close", ticker): close, ("Adj Close", ticker): close,
                       ("Dividends", ticker): div,
                       ("Stock Splits", ticker): [0.0] * len(close)},
                      index=IDX[:len(close)])
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def _engines(monkeypatch, tmp_path, raw: pd.DataFrame):
    """What each engine makes of `raw`: (backtest close, paper dividend frame,
    paper cash credited on 100 shares)."""
    import yfinance

    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: raw)
    monkeypatch.setattr(data, "_download_dividends", lambda t, s, e: raw)
    prices = data.load_prices(["AAA"], START, END, use_cache=False)["AAA"]
    divs = data.dividends(["AAA"], START, END, use_cache=False)
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 100},
              "last_dividend_date": START}
    credited = pt._credit_dividends(get_region("US"), sleeve, str(IDX[-1].date()),
                                    [], synthetic=False)
    return prices.tolist(), divs, credited


def test_a_typo_dividend_is_rejected_by_the_backtest_and_the_paper_credit(
        monkeypatch, tmp_path):
    """20% of the price, and the price did not move: a typo. Neither the
    backtest's adjusted close nor the paper book's cash may take it."""
    raw = _raw([100.0] * 5, [0.0, 0.0, 20.0, 0.0, 0.0])
    prices, divs, credited = _engines(monkeypatch, tmp_path, raw)
    assert prices == [100.0] * 5, "the adjustment applied a dividend the price contradicts"
    assert divs.empty or not (divs.fillna(0.0) != 0).any().any()
    assert credited == 0.0


def test_a_genuine_large_dividend_is_kept_by_both(monkeypatch, tmp_path):
    """20% of the price, and the close fell by all of it: a real special
    dividend (TSCO.L 2021 is 21% and fell 19.8%)."""
    close, div = [100.0, 100.0, 80.0, 80.0, 80.0], [0.0, 0.0, 20.0, 0.0, 0.0]
    kept, report = data_quality.implausible_dividends(
        pd.DataFrame({"AAA": close}, index=IDX), pd.DataFrame({"AAA": div}, index=IDX))
    assert kept["AAA"].tolist() == div and not report.excluded
    prices, divs, credited = _engines(monkeypatch, tmp_path, _raw(close, div))
    assert prices == pytest.approx([80.0] * 5, abs=1e-12)
    assert float(divs.loc[IDX[2], "AAA"]) == 20.0
    assert credited == pytest.approx(2_000.0)


def test_a_normal_dividend_is_untouched(monkeypatch, tmp_path):
    """2% is inside one ordinary day's move, so it is not judged at all —
    even on a day the price rose."""
    close, div = [100.0, 100.0, 101.0, 101.0, 101.0], [0.0, 0.0, 2.0, 0.0, 0.0]
    kept, report = data_quality.implausible_dividends(
        pd.DataFrame({"AAA": close}, index=IDX), pd.DataFrame({"AAA": div}, index=IDX))
    assert kept["AAA"].tolist() == div and not report.excluded
    prices, divs, credited = _engines(monkeypatch, tmp_path, _raw(close, div))
    assert prices == pytest.approx([98.0, 98.0, 101.0, 101.0, 101.0], abs=1e-12)
    assert credited == pytest.approx(200.0)


@pytest.mark.parametrize("dividend, ex_close, rejected", [
    (7.99, 100.0, False),       # under 8% of the prior close: never judged
    (8.00, 100.0, True),        # 8% exactly is judged, and nothing fell
    (20.0, 90.0, False),        # fell by exactly half of it: kept
    (20.0, 90.01, True),        # fell by a hair less than half: rejected
])
def test_the_rule_is_size_at_least_8pct_and_a_fall_under_half(dividend, ex_close,
                                                               rejected):
    close = pd.DataFrame({"AAA": [100.0, 100.0, ex_close]}, index=IDX[:3])
    div = pd.DataFrame({"AAA": [0.0, 0.0, dividend]}, index=IDX[:3])
    kept, report = data_quality.implausible_dividends(close, div)
    assert bool(kept.at[IDX[2], "AAA"] == 0.0) is rejected
    assert ("AAA" in report.excluded) is rejected


def test_a_dividend_that_cannot_be_judged_is_kept():
    """No close before it, or none on or after it yet: nothing contradicts it."""
    close = pd.DataFrame({"AAA": [100.0, 100.0, float("nan")]}, index=IDX[:3])
    div = pd.DataFrame({"AAA": [30.0, 0.0, 30.0]}, index=IDX[:3])
    kept, report = data_quality.implausible_dividends(close, div)
    assert kept["AAA"].tolist() == [30.0, 0.0, 30.0] and not report.excluded


def test_a_rejection_raises_a_data_quality_report_and_a_notification(monkeypatch):
    """Never dropped silently: the report names the event, and the alert
    channel hears about a recent one. An old one is history — the full panel
    is re-read on every run, so it is printed at info and never posted
    (verify.py ages its findings the same way)."""
    sent = []
    monkeypatch.setattr(notifications, "notify",
                        lambda event, message, **kw: sent.append((event, message, kw)))
    recent = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=3)
    for idx, level, channel in ((recent, "alert", None),
                                (pd.bdate_range("2012-04-02", periods=3), "info", "log")):
        sent.clear()
        close = pd.DataFrame({"MRO.L": [137.0, 137.0, 130.0]}, index=idx)
        div = pd.DataFrame({"MRO.L": [0.0, 0.0, 26.2]}, index=idx)
        kept, report = data_quality.implausible_dividends(close, div, source="prices")
        assert report.excluded == {"MRO.L"}
        assert str(idx[2].date()) in report.reasons["MRO.L"]
        assert [s[0] for s in sent] == ["implausible_dividend"]
        assert sent[0][2]["level"] == level and sent[0][2].get("channel") == channel
        assert "MRO.L" in sent[0][1]


def test_caches_written_before_the_rule_are_never_served(tmp_path, monkeypatch):
    """A closed window's cache never expires, so a file holding values the
    rule now rejects would be served forever without a key version bump."""
    pytest.importorskip("pyarrow")
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    stale = pd.DataFrame({"AAA": [1.0] * 5}, index=IDX)
    stale.to_parquet(data._cache_path("adj:v2:k"))
    stale.to_parquet(data._cache_path(f"div:v1:{START}:{END}:AAA"))
    raw = _raw([100.0] * 5, [0.0] * 5)
    monkeypatch.setattr(data, "_download_primary", lambda t, s, e: raw["Close"])
    monkeypatch.setattr(data, "_download_dividends", lambda t, s, e: raw)
    assert data.load_prices(["AAA"], START, END, cache_key="k")["AAA"].tolist() == [100.0] * 5
    assert data.dividends(["AAA"], START, END).empty
    assert os.path.exists(data._cache_path(f"{data.PRICE_CACHE_VERSION}:k"))
