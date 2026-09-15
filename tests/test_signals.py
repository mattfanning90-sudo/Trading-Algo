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


def test_index_risk_on_survives_a_missing_print():
    """One missing index print must not force the sleeve risk-off.

    `rolling(index_trend_ma).mean()` yields NaN for EVERY window containing a
    gap, and `price > NaN` is False — so a single absent close silently pins a
    whole sleeve in cash for the next `index_trend_ma` bars while reporting a
    legitimate-looking 'regime-off'. The regime must be read from the last
    KNOWN close, not blanked by a hole in the feed.
    """
    up = pd.Series(np.linspace(100, 200, 300))
    gapped = up.copy()
    gapped.iloc[250] = np.nan

    risk_on = sig.index_risk_on(gapped, P)

    assert bool(risk_on.iloc[-1]) is True, "still above the MA after the gap"
    # every bar from the gap onward, not just the last one
    assert bool(risk_on.iloc[251:].all()) is True


def test_stock_trend_survives_a_missing_print():
    """Same gap-poisoning applies per stock: one missing close must not exclude
    a name from the universe for the next `stock_trend_ma` bars."""
    up = pd.DataFrame({"A": np.linspace(100, 200, 300)})
    gapped = up.copy()
    gapped.iloc[250, 0] = np.nan

    trend_ok = sig.stock_trend_ok(gapped, P)

    assert bool(trend_ok["A"].iloc[-1]) is True
    assert bool(trend_ok["A"].iloc[251:].all()) is True


def _ten_names(calm_vol):
    """Ten equally-ranked names, nine ordinary and one unusually calm. Enough
    names that the `max_weight` cap does not bind and mask the vol floor."""
    names = ["CALM"] + [f"N{i}" for i in range(9)]
    scores = pd.Series({n: 1.0 - i * 0.01 for i, n in enumerate(names)})
    trend = pd.Series(True, index=scores.index)
    vols = pd.Series({n: 0.30 for n in names})
    vols["CALM"] = calm_vol
    return scores, trend, vols


def test_inverse_vol_floors_implausibly_calm_names():
    """A name measured calmer than `min_vol` is sized as if it were AT the floor.

    Inverse-vol weighting hands the biggest position to the lowest measured
    volatility, so a degraded price feed — one that barely ticks — attracts the
    most capital precisely because its data is broken. `data_quality` is the
    primary defence; this is the backstop for whatever slips through.
    """
    sub_floor = sig.select_portfolio(*_ten_names(0.005), risk_on=True, p=P)
    at_floor = sig.select_portfolio(*_ten_names(P.min_vol), risk_on=True, p=P)

    pd.testing.assert_series_equal(sub_floor, at_floor)


def test_vol_floor_does_not_flatten_real_differences():
    """Above the floor, genuine volatility differences must still drive size."""
    names = [f"N{i}" for i in range(10)]
    scores = pd.Series({n: 1.0 - i * 0.01 for i, n in enumerate(names)})
    trend = pd.Series(True, index=scores.index)
    vols = pd.Series({n: (0.20 if i < 5 else 0.30) for i, n in enumerate(names)})

    w = sig.select_portfolio(scores, trend, vols, risk_on=True, p=P)

    assert (w < P.max_weight).all(), "cap must not bind, or this proves nothing"
    assert w["N0"] > w["N9"]
