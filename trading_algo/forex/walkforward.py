"""Walk-forward prediction with purging + embargo (no lookahead for ML).

The cardinal sin of ML backtests is training on data that overlaps — in time or
via forward-looking labels — with what you then predict. This module enforces the
López de Prado discipline:

* **Expanding (anchored) walk-forward**: fold k is predicted by a model trained
  only on data strictly before fold k.
* **Purge**: because a label at time s uses returns out to s+`label_horizon`, any
  training row whose label window reaches into the test fold is dropped.
* **Embargo**: an extra gap of rows after the purge boundary, to kill leakage
  from serial correlation.
* **Leakage-safe scaling**: the `StandardScaler` is fit on each fold's training
  rows only.

`walk_forward_predict` returns out-of-sample predictions aligned to the input
rows (NaN where a row was never in a test fold with enough prior history). It
works on a *pooled* dataset (rows from many pairs sharing a timeline): splitting
is done on the unique sorted timestamps, so all pairs at a given time move
together between train and test.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .nn import StandardScaler


def _fold_bounds(n_times: int, n_folds: int) -> list[tuple[int, int]]:
    edges = np.linspace(0, n_times, n_folds + 1, dtype=int)
    return [(edges[i], edges[i + 1]) for i in range(n_folds) if edges[i + 1] > edges[i]]


def walk_forward_predict(X: np.ndarray, y: np.ndarray, time_index: np.ndarray,
                         model_factory: Callable[[], object], *,
                         n_folds: int = 6, label_horizon: int = 1, embargo: int = 5,
                         min_train: int = 250, rolling: int | None = None,
                         scale: bool = True, fit_kwargs: dict | None = None
                         ) -> np.ndarray:
    """Out-of-sample predictions, aligned to the rows of X (NaN where untested).

    `time_index` is one timestamp per row (rows may share timestamps across
    pairs). `model_factory()` must return a fresh model exposing
    ``fit(X, y, **fit_kwargs)`` and ``predict(X) -> (m, 1)``.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    times = pd.Index(time_index)
    uniq = np.array(sorted(times.unique()))
    pos = {t: i for i, t in enumerate(uniq)}
    row_pos = np.array([pos[t] for t in times])         # each row's unique-time index
    preds = np.full(len(X), np.nan)
    fit_kwargs = fit_kwargs or {}
    gap = label_horizon + embargo

    for a, b in _fold_bounds(len(uniq), n_folds):
        cutoff = a - gap                                 # purge + embargo boundary
        if cutoff < 1:
            continue
        train_mask = row_pos < cutoff
        if rolling is not None:
            train_mask &= row_pos >= (cutoff - rolling)
        test_mask = (row_pos >= a) & (row_pos < b)
        if train_mask.sum() < min_train or test_mask.sum() == 0:
            continue

        Xtr, ytr, Xte = X[train_mask], y[train_mask], X[test_mask]
        if scale:
            scaler = StandardScaler().fit(Xtr)
            Xtr, Xte = scaler.transform(Xtr), scaler.transform(Xte)
        model = model_factory()
        model.fit(Xtr, ytr, **fit_kwargs)
        preds[test_mask] = model.predict(Xte).ravel()
    return preds


def fit_final_model(X: np.ndarray, y: np.ndarray, model_factory: Callable[[], object],
                    *, scale: bool = True, fit_kwargs: dict | None = None):
    """Fit one model on ALL available rows (for live deployment) and return
    (model, scaler). The scaler is fit on the same rows; persist both together."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    scaler = StandardScaler().fit(X) if scale else None
    Xs = scaler.transform(X) if scale else X
    model = model_factory()
    model.fit(Xs, y, **(fit_kwargs or {}))
    return model, scaler
