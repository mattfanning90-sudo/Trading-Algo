"""Predictive-model data layer: features, labels, purged walk-forward, baseline model."""
import numpy as np
import pandas as pd

from trading_algo import data, datasources as ds, features as feat, labels as lab, mlpipeline as mlp
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


# --- honest marginal-edge harness: the mask stays out of the model; incremental IC is
#     a real negative control on synthetic; the nested delta returns a bootstrap CI ---

def test_coverage_mask_excluded_from_model():
    prices, idx = _synth()
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    df = mlp.build_dataset(prices, idx, extra=extra)
    assert "has_sentiment" in df.columns          # present for sub-universe selection
    assert "has_sentiment" not in mlp.feature_cols(df)   # but NEVER fed to the ridge


def test_partial_incremental_ic_synthetic_null_and_sensitivity():
    prices, idx = _synth()
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    df = mlp.build_dataset(prices, idx, extra=extra)
    alt = ["sue", "sentiment_shock", "buzz_shock"]
    res = mlp.partial_incremental_ic(df, feat.FEATURES, alt)
    assert res["n_dates"] > 0
    # NEGATIVE CONTROL: synthetic alt-data is independent of synthetic prices → ~0 edge.
    # A materially non-zero value here would be a leakage bug in the new feature path.
    assert abs(res["incremental_ic"]) < 0.05
    # SENSITIVITY: a planted label-correlated column must read a clearly positive edge,
    # proving the harness can SEE signal when it is genuinely there.
    planted = df.copy()
    planted["fake"] = df[mlp.LABEL]
    res2 = mlp.partial_incremental_ic(planted, feat.FEATURES, ["fake"])
    assert res2["incremental_ic"] > 0.3


_SMALL_GBRT = {"n_rounds": 12, "learning_rate": 0.1, "max_depth": 3, "min_leaf": 10}


def test_gbrt_learns_interaction_ridge_cannot():
    rng = np.random.default_rng(0)
    n = 4000
    a, b = rng.normal(size=n), rng.normal(size=n)
    y = np.sign(a) * np.sign(b) + rng.normal(0, 0.3, n)      # pure interaction, ~0 linear IC
    X = np.column_stack([a, b])
    tr, te = slice(0, 3000), slice(3000, n)
    ridge_ic = mlp._rank_ic(X[te] @ mlp.cross_sectional_ridge(X[tr], y[tr], 1.0), y[te])
    gbrt_ic = mlp._rank_ic(mlp.gbrt_predict(mlp.gradient_boost(X[tr], y[tr]), X[te]), y[te])
    assert abs(ridge_ic) < 0.1              # the linear model is blind to the interaction
    assert gbrt_ic > 0.3                    # the nonlinear learner extracts it


def test_gbrt_deterministic_and_pure_numpy():
    import sys
    rng = np.random.default_rng(1)
    X, y = rng.normal(size=(500, 4)), rng.normal(size=500)
    m1, m2 = mlp.gradient_boost(X, y, n_rounds=20), mlp.gradient_boost(X, y, n_rounds=20)
    assert np.array_equal(mlp.gbrt_predict(m1, X), mlp.gbrt_predict(m2, X))   # no RNG/seed
    for mod in ("scipy", "sklearn", "xgboost", "lightgbm"):
        assert mod not in sys.modules      # pure NumPy — no heavy learner imported


def test_gbrt_routes_through_shared_fold_loop():
    prices, idx = _synth()
    df = mlp.build_dataset(prices, idx)
    g = mlp.fit_predict_walk_forward(df, n_folds=3, model="gbrt", gbrt=_SMALL_GBRT)
    ridge = mlp.fit_predict_walk_forward(df, n_folds=3, model="ridge")
    assert list(g.index) == list(ridge.index)      # identical OOS (date,ticker) rows + splits


def test_gbrt_walkforward_uses_only_past_train_rows():
    prices, idx = _synth()
    df = mlp.build_dataset(prices, idx)
    cols = mlp.feature_cols(df)
    scores = mlp.fit_predict_walk_forward(df, n_folds=3, model="gbrt", gbrt=_SMALL_GBRT)
    dates = df.index.get_level_values("date")
    tr_d, te_d = mlp.purged_walk_forward(pd.unique(dates), n_folds=3, embargo=1)[0]
    tr, te = df[dates.isin(tr_d)], df[dates.isin(te_d)]
    manual = mlp._fit_predict(cols, tr, te, model="gbrt", gbrt=_SMALL_GBRT)
    # the fold's OOS scores == a fresh GBRT fit on ONLY that fold's (past) train rows
    assert np.allclose(scores.reindex(te.index).to_numpy(), manual)


def test_gbrt_shuffle_null_collapses():
    prices, idx = _synth()
    res = mlp.run_ml_backtest(prices, idx, model="gbrt", gbrt=_SMALL_GBRT,
                              n_folds=3, top_n=15, shuffle_seed=0)
    assert abs(res.get("ic", 0.0)) < 0.05          # label-shuffle leak detector still collapses


def test_gbrt_synthetic_negative_control():
    prices, idx = _synth()
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    base = mlp.run_ml_backtest(prices, idx, extra=None, n_folds=3, model="gbrt", gbrt=_SMALL_GBRT)
    alt = mlp.run_ml_backtest(prices, idx, extra=extra, n_folds=3, model="gbrt", gbrt=_SMALL_GBRT)
    d = mlp.incremental_delta(base, alt, n_paths=400)
    assert abs(d["delta_ic"]) < 0.05                                  # no spurious edge
    assert np.isnan(d["ci_low"]) or (d["ci_low"] <= 0 <= d["ci_high"])  # increment straddles 0


def test_incremental_delta_identical_books_is_zero_with_ci():
    prices, idx = _synth()
    base = mlp.run_ml_backtest(prices, idx, n_folds=4, top_n=15)
    d = mlp.incremental_delta(base, base, n_paths=500)
    assert abs(d["delta_ic"]) < 1e-12                # same book → zero IC difference
    assert np.isnan(d["delta_ir"])                   # zero difference series → undefined IR
    assert "diff" in d                               # exposes the paired difference to DSR-deflate
