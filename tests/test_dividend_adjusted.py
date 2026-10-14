"""The dividend-adjusted (total-return) close is built HERE, for every ticker.

Yahoo's own adjustment (`auto_adjust=True`) applies London (.L) dividends at
1/100 — measured 2023-01 -> 2025-01: HSBA.L's adjusted close gains 0.16% on its
raw close against 16.5% of dividends paid. Its dividend VALUES are right (pence,
like the prices), so `data.dividend_adjusted` builds the adjusted close from
Yahoo's raw (split-adjusted) Close and Dividends instead, one definition for
every market. Where Yahoo is right (US, .AX, .TO) the two agree.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from trading_algo import data

IDX = pd.bdate_range("2026-03-02", periods=5)


def _frame(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols, index=IDX[:len(next(iter(cols.values())))])


def test_one_dividend_scales_every_earlier_close_by_one_minus_div_over_prior_close():
    close = _frame(AAA=[100.0, 102.0, 100.0, 101.0])
    div = _frame(AAA=[0.0, 0.0, 2.04, 0.0])         # ex-date = third session
    out = data.dividend_adjusted(close, div)
    # 1 - 2.04/102 = 0.98 on every close strictly before the ex-date; the
    # ex-date close and everything after it are the raw close.
    assert out["AAA"].tolist() == pytest.approx([98.0, 99.96, 100.0, 101.0], abs=1e-12)


def test_dividends_compound_and_use_the_tickers_own_last_close():
    """A missing print before the ex-date (a holiday, a gap) is not a close:
    the factor is read off the ticker's last REAL close."""
    close = _frame(AAA=[100.0, np.nan, 98.0, 99.0, 50.0])
    div = _frame(AAA=[0.0, 0.0, 2.0, 0.0, 1.0])
    out = data.dividend_adjusted(close, div)["AAA"]
    f1, f2 = 1 - 2.0 / 100.0, 1 - 1.0 / 99.0
    assert out.iloc[0] == pytest.approx(100.0 * f1 * f2, rel=1e-12)
    assert np.isnan(out.iloc[1]), "a missing print stays missing"
    assert out.iloc[2] == pytest.approx(98.0 * f2, rel=1e-12)
    assert out.iloc[3] == pytest.approx(99.0 * f2, rel=1e-12)
    assert out.iloc[4] == 50.0


def test_a_split_adjusted_close_is_not_adjusted_for_the_split_again():
    """Yahoo's raw Close is already split-adjusted (NVDA reads 120.888 on
    2024-06-07, the session before its 10-for-1) and so are its dividends
    (NVDA's pre-split $0.04 reads 0.004). A 2-for-1 here: the closes before it
    are already halved, so the only adjustment left is the dividend's."""
    close = _frame(AAA=[50.0, 51.0, 52.0, 53.0])     # traded 100, 102, then 52, 53
    div = _frame(AAA=[0.0, 0.0, 0.0, 0.53])          # split-adjusted, like the close
    out = data.dividend_adjusted(close, div)["AAA"]
    f = 1 - 0.53 / 52.0
    assert out.tolist() == pytest.approx([50.0 * f, 51.0 * f, 52.0 * f, 53.0], rel=1e-12)


def test_a_ticker_with_no_dividends_is_unchanged():
    close = _frame(AAA=[10.0, 11.0, 12.0], BBB=[20.0, np.nan, 21.0],
                   CCC=[5.0, 5.5, 6.0])
    # BBB paid nothing; CCC has no Dividends column at all (yfinance gives a
    # failed name no column) — neither may move.
    div = _frame(AAA=[0.0, 0.5, 0.0], BBB=[0.0, 0.0, 0.0])
    out = data.dividend_adjusted(close, div)
    pd.testing.assert_series_equal(out["BBB"], close["BBB"])
    pd.testing.assert_series_equal(out["CCC"], close["CCC"])


def test_a_dividend_after_the_newest_close_leaves_the_newest_close_raw():
    """yfinance appends an event it has no bar for yet as a row of NaN prices
    ("Adding out-of-range Dividends ... in new prices row of NaNs"). Nothing
    in the series has gone ex, so nothing is adjusted — the paper book marks
    at the newest close, which must stay the raw close."""
    close = _frame(AAA=[10.0, 11.0, 12.0, np.nan])
    div = _frame(AAA=[0.0, 0.0, 0.0, 0.6])
    out = data.dividend_adjusted(close, div)["AAA"]
    assert out.iloc[:3].tolist() == [10.0, 11.0, 12.0]


# --- the loader: _download_primary is the one price source ------------------
def _fake_yfinance(rows: dict, yahoo_adj: dict):
    """`yf.download` as yfinance does it: a (field, ticker) frame; with
    `auto_adjust=True` its Close IS Yahoo's adjusted close and Dividends are
    dropped unless `actions=True`."""
    def download(tickers, start=None, end=None, auto_adjust=False,
                 actions=False, progress=False, **kw):
        cols = {}
        for t in tickers:
            c, d, s = rows[t]
            cols[("Close", t)] = yahoo_adj[t] if auto_adjust else c
            if not auto_adjust:
                cols[("Adj Close", t)] = yahoo_adj[t]
            if actions:
                cols[("Dividends", t)] = d
                cols[("Stock Splits", t)] = s
        df = pd.DataFrame(cols, index=IDX[:4])
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        return df
    return download


def test_the_loader_returns_the_own_adjusted_close_for_every_ticker(monkeypatch):
    """HSBA.L-shaped: Yahoo's adjusted close carries 1/100 of the dividend.
    JPM-shaped: Yahoo's is right. The loader must return ours for both — the
    corrected one for London, the same one Yahoo had for New York."""
    import yfinance

    c = [100.0, 102.0, 100.0, 101.0]
    d = [0.0, 0.0, 2.04, 0.0]
    nosplit = [0.0, 0.0, 0.0, 0.0]
    rows = {"AAA.L": (c, d, nosplit), "BBB": (c, d, [0.0, 2.0, 0.0, 0.0])}
    right = [98.0, 99.96, 100.0, 101.0]
    wrong = [x * (1 - 0.0204 / 100) for x in c[:2]] + c[2:]     # 1/100 of it
    monkeypatch.setattr(yfinance, "download",
                        _fake_yfinance(rows, {"AAA.L": wrong, "BBB": right}))
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    out = data.load_prices(["AAA.L", "BBB"], "2026-03-02", "2026-03-07",
                           use_cache=False)
    assert out["AAA.L"].tolist() == pytest.approx(right, abs=1e-12)
    assert out["BBB"].tolist() == pytest.approx(right, abs=1e-12)


def test_a_price_cache_written_before_own_adjustment_is_never_served(
        tmp_path, monkeypatch):
    """A closed window's cache never expires, so a Yahoo-adjusted file written
    before this change would be served forever under the old key: the price
    cache key carries a version, and the old file is ignored."""
    pytest.importorskip("pyarrow")
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    stale = pd.DataFrame({"AAA": [1.0] * 4}, index=IDX[:4])
    stale.to_parquet(data._cache_path("k"))           # how HEAD keyed it
    fresh = pd.DataFrame({"AAA": [2.0] * 4}, index=IDX[:4])
    calls = []
    monkeypatch.setattr(data, "_download_primary",
                        lambda t, s, e: calls.append(t) or fresh)
    out = data.load_prices(["AAA"], "2026-03-02", "2026-03-07", cache_key="k")
    assert calls, "the pre-change cache file must not be served"
    assert out["AAA"].tolist() == [2.0] * 4
    assert stale["AAA"].tolist() == pd.read_parquet(data._cache_path("k"))["AAA"].tolist()
    assert os.path.exists(data._cache_path(f"{data.PRICE_CACHE_VERSION}:k"))
