"""Indicator-frame caching must not change results — only their cost.

`strategy.precompute` builds the heavy signal frames once so a walk-forward
backtest reuses them instead of recomputing per rebalance. Because every
indicator is causal, the cached path must produce *identical* weights to the
cold path on every as-of date. These tests guard that equivalence (the speedup
is only legitimate if the numbers are byte-for-byte the same).
"""
import pandas as pd

from trading_algo import strategy


def test_cached_equals_cold_momentum(synth_asx, asx_region):
    prices, index_px = synth_asx
    p = asx_region.params
    cache = strategy.precompute(prices, index_px, p)
    for asof in (prices.index[-1], prices.index[-30], prices.index[len(prices) // 2]):
        cold = strategy.compute_targets(prices, index_px, p, asof=asof)
        cached = strategy.compute_targets(prices, index_px, p, asof=asof,
                                          signals_cache=cache)
        pd.testing.assert_series_equal(cold, cached)


def test_cached_equals_cold_value_blend(synth_asx, asx_region):
    prices, index_px = synth_asx
    p = asx_region.params.with_overrides(use_value=True)
    cache = strategy.precompute(prices, index_px, p)
    asof = prices.index[-1]
    cold = strategy.compute_targets(prices, index_px, p, asof=asof)
    cached = strategy.compute_targets(prices, index_px, p, asof=asof,
                                      signals_cache=cache)
    pd.testing.assert_series_equal(cold, cached)


def test_value_cache_includes_value_frame(synth_asx, asx_region):
    prices, index_px = synth_asx
    p_mom = asx_region.params
    p_val = asx_region.params.with_overrides(use_value=True)
    assert "value" not in strategy.precompute(prices, index_px, p_mom)
    assert "value" in strategy.precompute(prices, index_px, p_val)


def test_cached_equals_cold_regime_off(synth_asx, asx_region):
    prices, index_px = synth_asx
    p = asx_region.params.with_overrides(regime_filter=False)
    cache = strategy.precompute(prices, index_px, p)
    asof = prices.index[-1]
    cold = strategy.compute_targets(prices, index_px, p, asof=asof)
    cached = strategy.compute_targets(prices, index_px, p, asof=asof,
                                      signals_cache=cache)
    pd.testing.assert_series_equal(cold, cached)
