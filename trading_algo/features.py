"""Feature panel for the predictive (ML) layer.

Turns a price history into a tidy, causal, cross-sectionally-standardised feature
panel `X[(date, ticker), feature]` — the input a predictive model learns from.
Every feature at date t uses data ≤ t (no lookahead), and each is z-scored ACROSS
names per date so the model sees relative, scale-free signals.

Price-only for now (reusing the signal math we already trust). New data sources —
fundamentals, analyst revisions, short interest, options-implied, sentiment — become
extra columns here and nothing downstream changes. That is the whole point of the
panel: the day we add data, the only new work is one more feature function.
See `docs/research/PREDICTIVE_MODEL.md`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import lowrisk, signals as sig
from .config import DEFAULT_PARAMS, StrategyParams

# Ordered list of the columns `build_feature_panel` produces.
FEATURES = ["mom", "rev1m", "resmom", "lowvol", "lowbeta", "value", "trend_gap", "high52"]


def _cross_section_z(wide: pd.DataFrame) -> pd.DataFrame:
    """Z-score each row (date) across tickers, clipped to ±3 to tame outliers."""
    mu = wide.mean(axis=1)
    sd = wide.std(axis=1).replace(0.0, np.nan)
    return wide.sub(mu, axis=0).div(sd, axis=0).clip(-3.0, 3.0)


def build_feature_panel(prices: pd.DataFrame, index_prices: pd.Series,
                        p: StrategyParams = DEFAULT_PARAMS,
                        extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """Causal, cross-sectionally z-scored feature panel.

    Returns a long DataFrame indexed by (date, ticker) with one column per price feature
    in `FEATURES`. All price inputs are strictly causal. `extra`, if given, is an
    already-as-of-merged alt-data panel (fundamentals / IV / sentiment from
    `datasources.build_extra_panel`) — its columns are z-scored per date and appended,
    so new data sources add columns without touching anything downstream."""
    wide = {
        "mom":       sig.momentum_score(prices, p),                       # 12-1 momentum
        "rev1m":     -(prices / prices.shift(21) - 1.0),                  # short-term reversal
        "resmom":    sig.residual_momentum_score(prices, index_prices, p),  # market-neutral mom
        "lowvol":    -sig.realised_vol(prices, p),                        # low-vol premium (neg vol)
        "lowbeta":   -lowrisk.rolling_beta(prices, index_prices, 252),    # BAB (neg beta)
        "value":     sig.value_score(prices, p),                          # long-term reversal
        "trend_gap": prices / prices.rolling(p.stock_trend_ma).mean() - 1.0,  # distance above MA
        "high52":    prices / prices.rolling(252).max(),                  # 52-week-high proximity
    }
    cols = {k: _cross_section_z(v).stack() for k, v in wide.items()}
    panel = pd.concat(cols, axis=1)[FEATURES]
    panel.index.names = ["date", "ticker"]
    panel = panel.dropna()          # rows must have ALL core price features

    if extra is not None and not extra.empty:
        # z-score each alt-data column cross-sectionally per date, then append. Alt-data
        # is often SPARSE (fundamentals filed quarterly; sentiment only where covered and
        # only ~2017+ for GDELT), so fill missing with 0 = neutral-after-z-score rather
        # than dropping the row — a sparse feed must not shrink the whole dataset.
        ez = {}
        for c in extra.columns:
            ez[c] = _cross_section_z(extra[c].unstack("ticker")).stack()
        extra_z = pd.concat(ez, axis=1)
        extra_z.index.names = ["date", "ticker"]
        panel = panel.join(extra_z, how="left")
        panel[list(extra.columns)] = panel[list(extra.columns)].fillna(0.0)

    return panel


def feature_names(extra: pd.DataFrame | None = None) -> list[str]:
    """The full column list a panel will have, incl. any alt-data columns."""
    return FEATURES + (list(extra.columns) if extra is not None and not extra.empty else [])
