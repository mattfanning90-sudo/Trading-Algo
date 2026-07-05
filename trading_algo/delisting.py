"""Delisting-return correction (backlog F13, complements F1).

Point-in-time membership (constituents.py) lets a since-delisted name appear in
the backtest — but only while the data layer has its prices. When a name delists,
its price series simply *ends*, so in a naive backtest a held position contributes
a 0% return on its way out instead of the sharply negative delisting return that
actually occurred. That residual upward bias is what this module removes.

On the day after a held name's price series terminates before the end of the
sample, we inject a single replacement return (Shumway 1997 / Shumway & Warther
1999): roughly -30% for NYSE/AMEX and -55% for Nasdaq. The exact rate is a config
knob (`config.DELISTING_REPLACEMENT_RETURN`, default None = off) with an optional
per-region override; None leaves prices untouched, so this is a perfect no-op
unless explicitly enabled in the point-in-time path.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as cfg
from .regions import Region

# Optional per-region override of the replacement return. Empty by default, so
# the single config knob applies everywhere; set e.g. {"US": -0.55} for a
# Nasdaq-heavy sleeve.
REGION_REPLACEMENT: dict[str, float] = {}


def replacement_return(region: Region) -> float | None:
    """The delisting replacement return for a region, or None if the correction
    is disabled. Region override wins over the global config default."""
    if region.key in REGION_REPLACEMENT:
        return REGION_REPLACEMENT[region.key]
    return getattr(cfg, "DELISTING_REPLACEMENT_RETURN", None)


def apply_delisting_returns(prices: pd.DataFrame, region: Region,
                            replacement: float | None = None) -> pd.DataFrame:
    """Return a copy of `prices` with a delisting return injected for every name
    whose series terminates before the end of the sample.

    A name is treated as delisted if its last valid price is strictly before the
    frame's last date. On the next trading day after that last price we write one
    synthetic close of `last_price * (1 + replacement)`; subsequent days stay NaN.
    No lookahead: the injected point sits at the delisting boundary, using only
    the last observed price.
    """
    rep = replacement if replacement is not None else replacement_return(region)
    if rep is None:
        return prices

    out = prices.copy()
    index = out.index
    last_pos = len(index) - 1
    for col in out.columns:
        s = out[col]
        valid = s.to_numpy()
        finite = np.isfinite(valid)
        if not finite.any():
            continue
        last_valid = int(np.max(np.nonzero(finite)[0]))
        if last_valid >= last_pos:
            continue                     # still trading at sample end — not delisted
        last_price = float(s.iloc[last_valid])
        if last_price <= 0:
            continue
        out.iloc[last_valid + 1, out.columns.get_loc(col)] = last_price * (1.0 + rep)
    return out
