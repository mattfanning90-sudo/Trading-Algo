"""Walk-forward prediction: no lookahead, leakage-safe scaling, learns an edge."""
import numpy as np
import pandas as pd

from trading_algo.forex.nn import MLP
from trading_algo.forex.walkforward import fit_final_model, walk_forward_predict


def _factory(n_feat):
    return lambda: MLP([n_feat, 8, 1], hidden_act="tanh", task="regression",
                       l2=1e-3, seed=0)


def _data(n=800, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    y = 0.7 * X[:, 0] + 0.2 * rng.normal(size=n)        # learnable edge in feature 0
    t = np.repeat(pd.bdate_range("2015-01-01", periods=n // 2), 2)[:n]  # 2 "pairs"
    return X, y, np.array(t)


def test_walk_forward_no_lookahead():
    X, y, t = _data()
    fit_kwargs = {"epochs": 25, "batch_size": 64, "lr": 1e-2}
    preds1 = walk_forward_predict(X, y, t, _factory(4), n_folds=4, min_train=100,
                                  embargo=3, fit_kwargs=fit_kwargs)
    # Perturb the final 20 rows (they are test-only, never train any fold).
    X2 = X.copy()
    X2[-20:] += 5.0
    preds2 = walk_forward_predict(X2, y, t, _factory(4), n_folds=4, min_train=100,
                                  embargo=3, fit_kwargs=fit_kwargs)
    mask = np.ones(len(X), bool)
    mask[-20:] = False
    np.testing.assert_allclose(preds1[mask], preds2[mask], rtol=1e-9, equal_nan=True)


def test_walk_forward_first_fold_is_untested():
    X, y, t = _data()
    preds = walk_forward_predict(X, y, t, _factory(4), n_folds=4, min_train=100,
                                 embargo=3, fit_kwargs={"epochs": 5})
    assert np.isnan(preds[:50]).all()        # earliest rows never predicted
    assert np.isfinite(preds).any()          # later rows are


def test_walk_forward_learns_edge():
    X, y, t = _data(n=1200)
    preds = walk_forward_predict(X, y, t, _factory(4), n_folds=5, min_train=150,
                                 embargo=3, fit_kwargs={"epochs": 60, "batch_size": 64,
                                                        "lr": 1e-2})
    ok = np.isfinite(preds)
    corr = np.corrcoef(preds[ok], y[ok])[0, 1]
    assert corr > 0.4                        # OOS predictions track the truth


def test_fit_final_model_roundtrip():
    X, y, _ = _data(n=200)
    model, scaler = fit_final_model(X, y, _factory(4), fit_kwargs={"epochs": 10})
    assert scaler is not None
    pred = model.predict(scaler.transform(X))
    assert pred.shape == (200, 1)
