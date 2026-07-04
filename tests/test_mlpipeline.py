"""Predictive-model data layer: features, labels, purged walk-forward, baseline model."""
import numpy as np
import pandas as pd

from trading_algo import data, features as feat, labels as lab, mlpipeline as mlp
from trading_algo.regions import get_region


def _synth():
    reg = get_region("US")
    return data.synthetic_region(reg)   # (prices, index)


def test_feature_panel_causal_and_standardised():
    prices, idx = _synth()
    panel = feat.build_feature_panel(prices, idx)
    assert list(panel.columns) == feat.FEATURES
    # no lookahead: features on the truncated history match the full history at the cut
    cut = prices.index[-40]
    full = feat.build_feature_panel(prices, idx).xs(cut, level="date")
    trunc = feat.build_feature_panel(prices.loc[:cut], idx.loc[:cut]).xs(cut, level="date")
    pd.testing.assert_frame_equal(full.sort_index(), trunc.sort_index())
    # cross-sectionally standardised: per-date mean ≈ 0
    per_date_mean = panel.groupby(level="date").mean().abs().mean().max()
    assert per_date_mean < 0.5


def test_forward_return_is_future():
    prices, _ = _synth()
    h = 21
    y = lab.forward_return(prices, h)
    t = prices.index[100]
    tk = prices.columns[0]
    expected = prices[tk].iloc[100 + h] / prices[tk].iloc[100] - 1.0
    assert abs(y.loc[(t, tk)] - expected) < 1e-9


def test_purged_walkforward_respects_embargo():
    dates = pd.date_range("2010-01-31", periods=120, freq="ME")
    splits = mlp.purged_walk_forward(dates, n_folds=5, embargo=1)
    assert splits
    order = list(dates)
    for tr, te in splits:
        assert tr.max() < te.min()                       # train strictly before test
        assert len(set(tr) & set(te)) == 0               # disjoint
        gap = order.index(te.min()) - order.index(tr.max())
        assert gap >= 2                                   # embargo(1) purged the tail


def test_ridge_recovers_linear_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 3))
    w_true = np.array([1.5, -0.5, 0.0])
    y = X @ w_true + rng.normal(0, 0.1, 2000)
    w = mlp.cross_sectional_ridge(X, y, alpha=1.0)
    assert np.allclose(w, w_true, atol=0.1)


def test_run_ml_backtest_produces_oos_returns():
    prices, idx = _synth()
    res = mlp.run_ml_backtest(prices, idx, top_n=15, n_folds=4)
    assert res["n_periods"] > 0
    assert set(("CAGR", "Vol", "Sharpe", "hit_rate")) <= set(res["metrics"])
    # OOS scores exist and are indexed by (date, ticker)
    assert res["scores"].index.names == ["date", "ticker"]
