"""Prediction labels (targets) for the ML layer.

The label at date t describes what happens AFTER t — it deliberately uses future
data (that is what we are trying to predict). Keeping labels here, separate from
`features.py`, makes the one rule that matters impossible to break by accident:
**a label is never a feature**, and the training split must embargo the horizon so
a label's look-ahead window can't overlap the test period (see `mlpipeline`).

Two targets:
- `forward_return` — regression target, the next-`horizon` total return.
- `triple_barrier` — López-de-Prado {+1, 0, −1}: which barrier (take-profit / stop /
  time) the path hits first over the horizon. A cleaner classification target.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def forward_return(prices: pd.DataFrame, horizon: int = 21) -> pd.Series:
    """Next-`horizon`-day total return per (date, ticker). Uses data > t (label)."""
    fwd = prices.shift(-horizon) / prices - 1.0
    return fwd.stack().rename_axis(["date", "ticker"]).rename("fwd_ret")


def triple_barrier(prices: pd.DataFrame, horizon: int = 21,
                   up: float = 0.05, down: float = 0.05) -> pd.Series:
    """Triple-barrier label in {+1, 0, −1}: +1 if the +`up` barrier is touched before
    the −`down` barrier within `horizon` days, −1 if the down barrier hits first, else
    0 (neither hit → time barrier). Path-dependent but strictly forward-looking."""
    rets = prices.pct_change(fill_method=None)
    out = {}
    arr = rets.to_numpy()
    idx = prices.index
    for j, col in enumerate(prices.columns):
        col_lab = np.full(len(idx), np.nan)
        r = arr[:, j]
        for i in range(len(idx) - 1):
            cum = 0.0
            label = 0.0                       # time barrier default
            end = min(i + horizon, len(idx) - 1)
            for k in range(i + 1, end + 1):
                if np.isnan(r[k]):
                    continue
                cum = (1.0 + cum) * (1.0 + r[k]) - 1.0
                if cum >= up:
                    label = 1.0
                    break
                if cum <= -down:
                    label = -1.0
                    break
            col_lab[i] = label
        out[col] = pd.Series(col_lab, index=idx)
    return pd.DataFrame(out).stack().rename_axis(["date", "ticker"]).rename("tb_label")
