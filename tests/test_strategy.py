"""compute_targets (single source of truth) + vol targeting."""
import pandas as pd

from trading_algo import strategy
from trading_algo.config import DEFAULT_PARAMS as P


def test_compute_targets_gross_within_limit(synth_asx, asx_region):
    prices, index_px = synth_asx
    w = strategy.compute_targets(prices, index_px, asx_region.params)
    assert w.sum() <= P.max_gross + 1e-9
    assert (w >= 0).all()


def test_compute_targets_is_deterministic(synth_asx, asx_region):
    prices, index_px = synth_asx
    a = strategy.compute_targets(prices, index_px, asx_region.params)
    b = strategy.compute_targets(prices, index_px, asx_region.params)
    pd.testing.assert_series_equal(a, b)


def test_compute_targets_no_lookahead(synth_asx, asx_region):
    """Targets at an as-of date must not change if future data is removed."""
    prices, index_px = synth_asx
    asof = prices.index[-30]
    full = strategy.compute_targets(prices, index_px, asx_region.params, asof=asof)
    truncated = strategy.compute_targets(
        prices.loc[:asof], index_px.loc[:asof], asx_region.params, asof=asof)
    pd.testing.assert_series_equal(full, truncated)


def test_vol_target_scales_down_high_vol():
    w = pd.Series({"A": 0.5, "B": 0.5})
    high = strategy.vol_target(w, pd.Series({"A": 0.8, "B": 0.8}), P)
    assert high.sum() < w.sum()           # very volatile book gets cut
    assert high.sum() <= P.max_gross + 1e-9


def test_vol_target_caps_leverage():
    w = pd.Series({"A": 0.5, "B": 0.5})
    low = strategy.vol_target(w, pd.Series({"A": 0.01, "B": 0.01}), P)
    # tiny vol would imply huge leverage, but capped by max_vol_scale and max_gross
    assert low.sum() <= P.max_gross + 1e-9


def test_vol_target_empty_is_empty():
    assert strategy.vol_target(pd.Series(dtype=float), pd.Series(dtype=float), P).empty
