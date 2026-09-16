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


# --- per-fold panel index + the validation split that makes early stopping real
class _Recorder:
    """A model that records exactly what the walk-forward handed it, per fold."""

    def __init__(self, log):
        self.log = log
        self.panel_index = None

    def fit(self, X, y, **kw):
        self.log.append({"X": X, "y": y, "panel_index": self.panel_index, **kw})
        return self

    def predict(self, X):
        self.log[-1]["X_test"] = X
        return np.zeros((len(X), 1))


def _panel_rows(n_times=300, n_feat=3, seed=0):
    """A two-pair panel whose FIRST feature is the global row position, so a test
    can recover which rows the walk-forward put in which block (with scale=False)."""
    rng = np.random.default_rng(seed)
    times = np.repeat(np.arange(n_times), 2)
    pairs = np.array(["A", "B"] * n_times)
    X = rng.normal(size=(len(times), n_feat))
    X[:, 0] = np.arange(len(times))
    return X, rng.normal(size=len(times)), times, pairs


def _real_index_factory(times, pairs, calls):
    from trading_algo.forex.panel_index import build_panel_index
    vols = np.full(len(times), 0.2)
    spreads = {"A": 0.001, "B": 0.002}

    def index_factory(rows):
        rows = np.asarray(rows)
        calls.append(rows)
        return build_panel_index(times[rows], pairs[rows], vols[rows], spreads)
    return index_factory


def test_each_fold_gets_its_own_panel_index():
    """A fold trains on a SUBSET of rows. Handing it the whole-panel index would
    mis-group timestamps and index past the end of the fold's arrays."""
    seen = []

    def index_factory(rows):
        seen.append(len(rows))

        class _Stub:
            n_groups = 1
            group = np.zeros(len(rows), dtype=np.int64)
            group_size = np.array([float(len(rows))])
            prev = np.full(len(rows), -1, dtype=np.int64)
            nxt = np.full(len(rows), -1, dtype=np.int64)
            cost = np.zeros(len(rows))
        return _Stub()

    n = 600
    X = np.random.default_rng(0).normal(0, 1, (n, 3))
    y = np.random.default_rng(1).normal(0, 1, n)
    t = np.arange(n)
    log = []
    walk_forward_predict(X, y, t, lambda: _Recorder(log), n_folds=3,
                         min_train=100, index_factory=index_factory)

    assert len(seen) >= 2, "index_factory must be called once per scored fold"
    assert all(s > 0 for s in seen)
    assert len(set(seen)) > 1, "an expanding walk-forward grows the train set"
    # the index must reach the model BEFORE fit, sized to that fold's own rows
    assert log and all(r["panel_index"] is not None for r in log)
    assert [len(r["panel_index"].group) for r in log] == [len(r["X"]) for r in log]


def test_panel_index_is_addressed_to_the_folds_own_rows():
    """THE failure this task exists to prevent: handing a fold the WHOLE-panel
    index. It satisfies every `is None` check and then mis-addresses every row —
    `group` names timestamps the fold does not hold and `prev` points past the
    end of the fold's arrays. Nothing raises; the loss is simply meaningless.
    Only the row COUNT and the addressing range can catch it."""
    X, y, times, pairs = _panel_rows()
    calls, log = [], []
    walk_forward_predict(X, y, times, lambda: _Recorder(log), n_folds=4,
                         min_train=100, scale=False,
                         index_factory=_real_index_factory(times, pairs, calls))

    assert log and len(calls) == len(log)
    for rows, rec in zip(calls, log):
        idx = rec["panel_index"]
        n = len(rec["X"])
        assert len(rows) < len(X), "a fold never trains on the whole panel"
        assert len(idx.group) == n, "index must describe THESE rows, not the panel"
        assert idx.n_groups == len(np.unique(times[rows]))
        assert idx.group.max() < idx.n_groups
        assert idx.prev.max() < n and idx.nxt.max() < n
        assert (idx.cost > 0).all()                     # costs always on
        np.testing.assert_array_equal(rec["X"][:, 0].astype(int), rows)


def test_validation_slice_is_a_contiguous_later_block_of_training():
    """The objective is a time-series Sharpe, so the validation block must be a
    contiguous LATER block of the training window — a random sample would neither
    respect time nor form coherent portfolio timestamps. It comes from TRAINING
    rows only: taking it from the test fold is the lookahead this module exists
    to prevent."""
    X, y, times, _ = _panel_rows()
    log = []
    walk_forward_predict(X, y, times, lambda: _Recorder(log), n_folds=4,
                         min_train=100, embargo=5, label_horizon=1,
                         val_frac=0.2, scale=False)
    assert log
    for rec in log:
        assert len(rec["X_val"]) == len(rec["y_val"]) > 0
        fit_rows = rec["X"][:, 0].astype(int)
        val_rows = rec["X_val"][:, 0].astype(int)
        test_rows = rec["X_test"][:, 0].astype(int)
        assert set(fit_rows).isdisjoint(val_rows)
        assert set(val_rows).isdisjoint(test_rows)          # invariant #1
        ft, vt, tt = times[fit_rows], times[val_rows], times[test_rows]
        assert ft.max() < vt.min(), "validation is later than what is fit on"
        assert vt.max() < tt.min(), "validation is training data, not test data"
        assert vt.max() + 1 + 5 <= tt.min(), "purge + embargo still hold"
        assert ft.max() + 1 + 5 <= vt.min(), "fit block is purged from validation"
        assert len(np.unique(vt)) == vt.max() - vt.min() + 1, "contiguous block"
        assert len(np.unique(vt)) >= 2


def test_validation_split_gets_its_own_panel_index():
    """The validation rows are a different panel: their own timestamps, their own
    predecessors. Scoring them against the training index would charge turnover
    against rows that are not there."""
    X, y, times, pairs = _panel_rows()
    calls, log = [], []
    walk_forward_predict(X, y, times, lambda: _Recorder(log), n_folds=4,
                         min_train=100, val_frac=0.2, scale=False,
                         index_factory=_real_index_factory(times, pairs, calls))
    assert log
    for rec in log:
        tr, va = rec["panel_index"], rec["val_panel_index"]
        assert va is not tr
        assert len(tr.group) == len(rec["X"])
        assert len(va.group) == len(rec["X_val"])
        assert va.prev.max() < len(rec["X_val"])
        assert va.n_groups == len(np.unique(times[rec["X_val"][:, 0].astype(int)]))


def test_validation_is_opt_in_so_existing_callers_train_on_every_row():
    """`meta_oos_signal` and every other caller must keep fitting on ALL their
    training rows; carving a block off by default would silently shrink them."""
    X, y, times, _ = _panel_rows()
    log = []
    walk_forward_predict(X, y, times, lambda: _Recorder(log), n_folds=4,
                         min_train=100, scale=False)
    assert log
    assert all({"X_val", "y_val", "val_panel_index"}.isdisjoint(rec) for rec in log)


def test_scaler_is_fit_on_the_fit_rows_only():
    """Early stopping trusts the validation block, so folding its own mean and
    scale into the standardiser makes that judgement optimistic. With a drifting
    feature, a scaler fit on fit+val leaves the fit block visibly off-centre."""
    X, y, times, _ = _panel_rows()
    X[:, 1] = np.arange(len(X)) / len(X) * 10.0          # strong upward drift
    log = []
    walk_forward_predict(X, y, times, lambda: _Recorder(log), n_folds=4,
                         min_train=100, val_frac=0.25)
    assert log
    for rec in log:
        assert abs(rec["X"][:, 1].mean()) < 1e-9         # centred on ITS OWN rows
        assert rec["X_val"][:, 1].mean() > 0.5           # the later block is not
