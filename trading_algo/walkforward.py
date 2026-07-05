"""Purged & embargoed walk-forward CV for the equity sleeves (backlog F8).

The equity signal is a fixed formula, not a trained model, so we reuse the
*discipline* from `forex.walkforward` (purge + embargo) rather than
`walk_forward_predict` itself. The timeline is cut into contiguous folds and the
first `embargo` rows of each fold are dropped, so the returns counted in a fold
come from positions decided WITHIN that fold — this kills the fold-boundary
leakage that the 12-month momentum and 200-day trend/regime windows would
otherwise carry across a naive split.

The output is a leakage-reduced (T × N) matrix of each configuration's
out-of-sample daily returns, which is exactly what `validation.pbo` and
`validation.deflated_sharpe_ratio` consume. This feeds the F2 overfitting gate.

No lookahead (invariant #1): each configuration is a single continuous backtest
(already causal); the CV only *selects which realised days to count*, using the
fold structure — it never reveals future data to the signal.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from . import validation
from .backtest import run_backtest
from .regions import Region

# Defaults: one rebalance period of embargo, >= 6 folds (backlog F8 acceptance).
DEFAULT_EMBARGO = 21     # ~1 month of trading days
DEFAULT_N_FOLDS = 6


def fold_edges(n: int, n_folds: int) -> list[tuple[int, int]]:
    """Contiguous [start, end) row blocks covering 0..n."""
    edges = np.linspace(0, n, n_folds + 1, dtype=int)
    return [(edges[i], edges[i + 1]) for i in range(n_folds) if edges[i + 1] > edges[i]]


def embargoed_oos_mask(n: int, n_folds: int, embargo: int) -> np.ndarray:
    """Boolean row mask keeping only rows at least `embargo` into their fold.

    Dropping the first `embargo` rows of each fold removes the returns most likely
    driven by a position decided just before the fold boundary.
    """
    mask = np.zeros(n, dtype=bool)
    for a, b in fold_edges(n, n_folds):
        start = min(a + embargo, b)
        mask[start:b] = True
    return mask


def cv_returns_matrix(prices: pd.DataFrame, index_px: pd.Series, region: Region,
                      top_ns, lookbacks, *, n_folds: int = DEFAULT_N_FOLDS,
                      embargo: int = DEFAULT_EMBARGO, membership=None) -> dict | None:
    """Build the (T × N) embargoed OOS return matrix over a parameter grid.

    Each column is one (top_n, lookback) configuration's daily returns, restricted
    to the embargoed out-of-sample rows. Returns None if no configuration ran.
    """
    cols: list[pd.Series] = []
    configs: list[dict] = []
    for lb in lookbacks:
        for tn in top_ns:
            variant = replace(region, params=region.params.with_overrides(
                top_n=tn, lookback_days=lb))
            try:
                res = run_backtest(prices, index_px, variant, membership=membership,
                                   max_drawdown_stop=None)
            except Exception:
                continue
            cols.append(res["returns"].rename(len(cols)))
            configs.append({"top_n": tn, "lookback": lb})
    if not cols:
        return None

    R = pd.concat(cols, axis=1).fillna(0.0)
    mask = embargoed_oos_mask(len(R), n_folds, embargo)
    return {
        "matrix": R.to_numpy()[mask],
        "configs": configs,
        "n_obs": int(mask.sum()),
        "n_configs": len(configs),
        "n_folds": n_folds,
        "embargo": embargo,
        "index": R.index[mask],
    }


def purged_cv_report(prices: pd.DataFrame, index_px: pd.Series, region: Region,
                     top_ns, lookbacks, *, n_folds: int = DEFAULT_N_FOLDS,
                     embargo: int = DEFAULT_EMBARGO, membership=None,
                     n_trials: int | None = None,
                     dsr_min: float = 0.95, pbo_max: float = 0.5) -> dict:
    """Run the purged/embargoed CV over the grid and apply the F2 overfitting gate.

    `n_trials` defaults to the grid size (the honest count of configurations
    searched, per the F2 acceptance criterion).
    """
    cv = cv_returns_matrix(prices, index_px, region, top_ns, lookbacks,
                           n_folds=n_folds, embargo=embargo, membership=membership)
    if cv is None:
        return {"verdict": "no result", "n_configs": 0}
    n_trials = n_trials if n_trials is not None else cv["n_configs"]
    gate = validation.overfitting_gate(cv["matrix"], n_trials=n_trials,
                                       dsr_min=dsr_min, pbo_max=pbo_max)
    gate.update({"n_obs": cv["n_obs"], "n_folds": cv["n_folds"],
                 "embargo": cv["embargo"], "grid_size": cv["n_configs"]})
    return gate
