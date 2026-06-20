"""Signal engine: correctness + the no-lookahead invariant."""
import numpy as np
import pandas as pd

from trading_algo import signals as sig
from trading_algo.config import DEFAULT_PARAMS as P


def test_momentum_is_12_minus_1(small_frame):
    score = sig.momentum_score(small_frame, P)
    t = small_frame.index[-1]
    expected = (small_frame.shift(P.skip_days).loc[t]
                / small_frame.shift(P.lookback_days).loc[t] - 1.0)
    pd.testing.assert_series_equal(score.loc[t], expected, check_names=False)


def test_momentum_no_lookahead(small_frame):
    """Score at row i must be identical whether computed on the full frame or
    only on data up to i — i.e. it uses no future information."""
    full = sig.momentum_score(small_frame, P)
    i = 350
    truncated = sig.momentum_score(small_frame.iloc[: i + 1], P).iloc[-1]
    pd.testing.assert_series_equal(full.iloc[i], truncated, check_names=False)


def test_trend_no_lookahead(small_frame):
    full = sig.stock_trend_ok(small_frame, P)
    i = 350
    truncated = sig.stock_trend_ok(small_frame.iloc[: i + 1], P).iloc[-1]
    pd.testing.assert_series_equal(full.iloc[i], truncated, check_names=False)


def test_select_respects_max_weight():
    scores = pd.Series({f"S{i}": 1.0 - i * 0.01 for i in range(20)})
    trend = pd.Series(True, index=scores.index)
    vols = pd.Series(0.2, index=scores.index)
    w = sig.select_portfolio(scores, trend, vols, risk_on=True, p=P)
    assert (w <= P.max_weight + 1e-9).all()
    assert len(w) == P.top_n
    assert w.sum() <= 1.0 + 1e-9


def test_select_risk_off_is_cash():
    scores = pd.Series({f"S{i}": 1.0 for i in range(20)})
    trend = pd.Series(True, index=scores.index)
    vols = pd.Series(0.2, index=scores.index)
    assert sig.select_portfolio(scores, trend, vols, risk_on=False, p=P).empty


def test_select_requires_positive_momentum_and_trend():
    scores = pd.Series({"A": -0.1, "B": 0.5, "C": 0.3})
    trend = pd.Series({"A": True, "B": False, "C": True})
    vols = pd.Series(0.2, index=scores.index)
    w = sig.select_portfolio(scores, trend, vols, risk_on=True, p=P)
    assert "A" not in w   # negative momentum excluded
    assert "B" not in w   # failed trend filter
    assert "C" in w


def test_value_score_no_lookahead(synth_asx):
    prices, _ = synth_asx
    full = sig.value_score(prices, P)
    i = 1500
    truncated = sig.value_score(prices.iloc[: i + 1], P).iloc[-1]
    pd.testing.assert_series_equal(full.iloc[i], truncated, check_names=False)


def test_value_score_is_negated_long_term_return(synth_asx):
    prices, _ = synth_asx
    t = prices.index[-1]
    v = sig.value_score(prices, P).loc[t]
    expected = -(prices.shift(P.value_skip_days).loc[t]
                 / prices.shift(P.value_lookback_days).loc[t] - 1.0)
    pd.testing.assert_series_equal(v, expected, check_names=False)


def test_index_risk_on_tracks_ma():
    up = pd.Series(np.linspace(100, 200, 300))
    assert bool(sig.index_risk_on(up, P).iloc[-1]) is True
    down = pd.Series(np.linspace(200, 100, 300))
    assert bool(sig.index_risk_on(down, P).iloc[-1]) is False
