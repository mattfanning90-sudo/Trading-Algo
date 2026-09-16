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
  rows only — and, when a validation block is carved out, on the rows the model
  actually fits on, so the block early stopping trusts is scored on statistics
  it did not help produce.
* **Per-fold row bookkeeping**: `index_factory(row_positions)` is called with
  each block's rows and returns a `panel_index.PanelIndex` addressed to THAT
  block's positions. A whole-panel index would satisfy every guard downstream
  and then group the wrong rows into a timestamp.
* **Validation block** (`val_frac`): the last slice of the fold's own TRAINING
  timestamps, contiguous and purged from the fit rows, so `fit` can early-stop.
  Never drawn from the test fold — that would be the lookahead this module
  exists to prevent.

`walk_forward_predict` returns out-of-sample predictions aligned to the input
rows (NaN where a row was never in a test fold with enough prior history). It
works on a *pooled* dataset (rows from many pairs sharing a timeline): splitting
is done on the unique sorted timestamps, so all pairs at a given time move
together between train and test.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

import numpy as np
import pandas as pd

from .nn import StandardScaler


class SupportsFitPredict(Protocol):
    """What `model_factory()` must return — the contract the docstrings below
    already state in prose, written so a type checker can hold us to it.

    Was `Callable[[], object]`, which said nothing: mypy then rejected every
    `.fit`/`.predict` call on the result.
    """

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> Any: ...

    def predict(self, X: np.ndarray) -> np.ndarray: ...


def row_positions(time_index) -> np.ndarray:
    """Each row's index into the sorted unique timestamps of `time_index`.

    Rows of a pooled panel share timestamps across pairs, and every split in this
    module is made on TIMESTAMPS, never on row positions — so the purge, the
    embargo and the validation block all measure distance in these units. Public
    because `train.py` splits the deployed fit the same way and must count in the
    same units to do it (one implementation, not two that can drift).
    """
    times = pd.Index(time_index)
    uniq = np.array(sorted(times.unique()))
    pos = {t: i for i, t in enumerate(uniq)}
    return np.array([pos[t] for t in times])


def validation_split(train_mask: np.ndarray, row_pos: np.ndarray,
                     val_frac: float, gap: int
                     ) -> tuple[np.ndarray, np.ndarray | None]:
    """Split a block of training rows into (fit, validation) blocks.

    Public, and used twice: once per walk-forward fold below, and once by
    `train.train_models` for the DEPLOYED bundle. The artefact that trades must
    be fit the way the grade gating it was earned, so the two cannot be allowed
    to hold separate copies of this geometry.

    The validation block is the LAST `val_frac` of the training window's
    timestamps: contiguous and later, never a random sample. The objective these
    blocks feed is a time-series Sharpe of a portfolio, so a shuffled slice would
    neither respect time nor form coherent portfolio timestamps.

    The fit block is purged from it by the same `gap` the fold itself uses —
    otherwise a fit row's forward-looking label would reach into the block used
    to decide when to stop, and early stopping would be judged on data it had
    already seen through the labels.

    Returns `(train_mask, None)` when the window is too short to give a block up;
    the caller decides what to do about it rather than quietly training blind.
    """
    if val_frac <= 0.0:
        return train_mask, None
    uniq_train = np.unique(row_pos[train_mask])
    if len(uniq_train) < 2:
        return train_mask, None
    n_val = min(max(1, int(round(val_frac * len(uniq_train)))), len(uniq_train) - 1)
    val_start = uniq_train[len(uniq_train) - n_val]
    val_mask = train_mask & (row_pos >= val_start)
    fit_mask = train_mask & (row_pos < val_start - gap)
    if not fit_mask.any() or not val_mask.any():
        return train_mask, None
    return fit_mask, val_mask


def _fold_bounds(n_times: int, n_folds: int) -> list[tuple[int, int]]:
    edges = np.linspace(0, n_times, n_folds + 1, dtype=int)
    return [(edges[i], edges[i + 1]) for i in range(n_folds) if edges[i + 1] > edges[i]]


def walk_forward_predict(X: np.ndarray, y: np.ndarray, time_index: np.ndarray,
                         model_factory: Callable[[], SupportsFitPredict], *,
                         n_folds: int = 6, label_horizon: int = 1, embargo: int = 5,
                         min_train: int = 250, rolling: int | None = None,
                         scale: bool = True, fit_kwargs: dict | None = None,
                         index_factory: Callable[[np.ndarray], Any] | None = None,
                         val_frac: float = 0.0
                         ) -> np.ndarray:
    """Out-of-sample predictions, aligned to the rows of X (NaN where untested).

    `time_index` is one timestamp per row (rows may share timestamps across
    pairs). `model_factory()` must return a fresh model exposing
    ``fit(X, y, **fit_kwargs)`` and ``predict(X) -> (m, 1)``.

    `index_factory(row_positions)` — optional — is called once per block with the
    positions of the rows in that block, and its result is handed to the model as
    `panel_index` (training) and `val_panel_index` (validation). It exists for
    objectives that are a property of the whole batch rather than of each row,
    and it MUST address the rows it is given: the model is fit on a subset, so a
    whole-panel index would mis-group every timestamp without raising.

    `val_frac` — optional — carves that fraction of each fold's TRAINING
    timestamps off as a contiguous later validation block, which is what makes
    `fit`'s early stopping live. `min_train` then counts the rows actually fit
    on, and a fold too short to yield both blocks is not scored at all rather
    than silently trained blind.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    uniq = np.array(sorted(pd.Index(time_index).unique()))
    row_pos = row_positions(time_index)                 # each row's unique-time index
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
        fit_mask, val_mask = validation_split(train_mask, row_pos, val_frac, gap)
        if fit_mask.sum() < min_train or test_mask.sum() == 0:
            continue
        if val_frac > 0.0 and val_mask is None:
            continue            # asked to early-stop and this fold cannot deliver

        Xtr, ytr, Xte = X[fit_mask], y[fit_mask], X[test_mask]
        Xva = X[val_mask] if val_mask is not None else None
        if scale:
            scaler = StandardScaler().fit(Xtr)          # the FIT rows, not fit+val
            Xtr, Xte = scaler.transform(Xtr), scaler.transform(Xte)
            if Xva is not None:
                Xva = scaler.transform(Xva)
        kwargs = dict(fit_kwargs)
        model = model_factory()
        if index_factory is not None:
            model.panel_index = index_factory(np.flatnonzero(fit_mask))
        if val_mask is not None:
            kwargs["X_val"], kwargs["y_val"] = Xva, y[val_mask]
            if index_factory is not None:
                kwargs["val_panel_index"] = index_factory(np.flatnonzero(val_mask))
        model.fit(Xtr, ytr, **kwargs)
        preds[test_mask] = model.predict(Xte).ravel()
    return preds


def fit_final_model(X: np.ndarray, y: np.ndarray, model_factory: Callable[[], SupportsFitPredict],
                    *, scale: bool = True, fit_kwargs: dict | None = None):
    """Fit one model on ALL available rows (for live deployment) and return
    (model, scaler). The scaler is fit on the same rows; persist both together."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    scaler = StandardScaler().fit(X) if scale else None
    Xs = scaler.transform(X) if scaler is not None else X
    model = model_factory()
    model.fit(Xs, y, **(fit_kwargs or {}))
    return model, scaler
