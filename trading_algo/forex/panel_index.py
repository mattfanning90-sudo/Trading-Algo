"""Row bookkeeping for the cost-aware portfolio objective.

`nn.MLP` sees a flat matrix of rows. The net-portfolio-Sharpe objective needs
two structures that matrix does not carry: which rows share a TIMESTAMP (they
are averaged into one portfolio return) and which row precedes each row within
its OWN pair's timeline (turnover is charged against that predecessor).

Pure bookkeeping — no model, no training, no pandas in the hot path — so it can
be tested exhaustively on hand-built arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PanelIndex:
    group: np.ndarray        # (n,) timestamp group id
    n_groups: int
    group_size: np.ndarray   # (T,) rows per timestamp
    prev: np.ndarray         # (n,) previous row for the same pair, -1 if none
    nxt: np.ndarray          # (n,) next row for the same pair, -1 if none
    cost: np.ndarray         # (n,) half-spread in vol-normalised units


def build_panel_index(times, pairs, vols, half_spreads) -> PanelIndex:
    """Index a flat (row -> pair, timestamp) panel for the portfolio objective.

    `half_spreads` maps a pair symbol to its half-spread as a fraction of price.
    The cost coefficient is divided by the same trailing vol the target was
    normalised by, so cost and return are in identical units — otherwise the
    objective compares a normalised return against a raw cost and mis-prices
    every trade.

    Rows need not arrive time-sorted (`pooled_dataset` concatenates per symbol):
    `prev`/`nxt` are the neighbours in each pair's own chronological timeline,
    never a neighbouring row position, and never a row belonging to another pair.
    """
    times = np.asarray(times)
    pairs = np.asarray(pairs)
    vols = np.asarray(vols, dtype=float)

    _, group = np.unique(times, return_inverse=True)
    group = group.astype(np.int64)
    n_groups = int(group.max()) + 1 if len(group) else 0
    group_size = np.bincount(group, minlength=n_groups).astype(float)

    n = len(pairs)
    prev = np.full(n, -1, dtype=np.int64)
    nxt = np.full(n, -1, dtype=np.int64)
    for sym in np.unique(pairs):
        rows = np.flatnonzero(pairs == sym)
        rows = rows[np.argsort(times[rows], kind="stable")]
        prev[rows[1:]] = rows[:-1]
        nxt[rows[:-1]] = rows[1:]

    safe_vol = np.where(vols > 0, vols, np.nan)
    spread = np.array([float(half_spreads.get(s, 0.0)) for s in pairs])
    cost = np.nan_to_num(spread / safe_vol, nan=0.0, posinf=0.0)

    return PanelIndex(group=group, n_groups=n_groups, group_size=group_size,
                      prev=prev, nxt=nxt, cost=cost)
