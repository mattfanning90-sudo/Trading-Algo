"""Technical indicator correctness, causality and streaming equivalence."""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import indicators as ind


@pytest.fixture
def ohlc():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-01", periods=300)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx))))
    high = close * (1 + np.abs(rng.normal(0, 0.003, len(idx))))
    low = close * (1 - np.abs(rng.normal(0, 0.003, len(idx))))
    return pd.DataFrame({"high": high, "low": low, "close": close}, index=idx)


def test_rsi_in_range(ohlc):
    r = ind.rsi(ohlc["close"], 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_adx_non_negative(ohlc):
    a = ind.adx(ohlc["high"], ohlc["low"], ohlc["close"], 14).dropna()
    assert (a >= 0).all() and (a <= 100).all()


def test_atr_positive(ohlc):
    a = ind.atr(ohlc["high"], ohlc["low"], ohlc["close"], 14).dropna()
    assert (a > 0).all()


def test_donchian_is_shifted_no_lookahead(ohlc):
    """The channel at t must not depend on bar t's own high/low."""
    upper, lower = ind.donchian(ohlc["high"], ohlc["low"], 20)
    # spike the last bar's high; the channel value at that bar is from t-1 data
    spiked = ohlc.copy()
    spiked.iloc[-1, spiked.columns.get_loc("high")] *= 2
    u2, _ = ind.donchian(spiked["high"], spiked["low"], 20)
    assert u2.iloc[-1] == upper.iloc[-1]   # unchanged: uses only prior bars


def test_indicator_causality(ohlc):
    """Perturbing a middle bar must not change earlier indicator values."""
    k = 150
    ema1 = ind.ema(ohlc["close"], 20)
    spiked = ohlc.copy()
    spiked.iloc[k, spiked.columns.get_loc("close")] *= 1.1
    ema2 = ind.ema(spiked["close"], 20)
    pd.testing.assert_series_equal(ema1.iloc[:k], ema2.iloc[:k])


def test_streaming_ema_matches_vectorized(ohlc):
    span = 20
    vec = ind.ema(ohlc["close"], span)
    s = ind.StreamingEMA(span)
    stream = [s.update(x) for x in ohlc["close"]]
    np.testing.assert_allclose(stream, vec.values, rtol=1e-9)


def test_streaming_atr_matches_vectorized(ohlc):
    win = 14
    vec = ind.atr(ohlc["high"], ohlc["low"], ohlc["close"], win)
    s = ind.StreamingATR(win)
    stream = [s.update(h, l, c) for h, l, c in
              zip(ohlc["high"], ohlc["low"], ohlc["close"])]
    np.testing.assert_allclose(stream, vec.values, rtol=1e-9)
