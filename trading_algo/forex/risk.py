"""Risk & position sizing: turn directional tilts into actual portfolio weights.

Three sequential controls, all vectorized across the time index so the backtest
and the live path share one implementation:

1. **Volatility targeting** — scale the whole book toward `target_vol` using the
   constant-average-correlation variance approximation
   ``var ≈ (1-ρ)·Σ(wᵢσᵢ)² + ρ·(Σ wᵢσᵢ)²`` (the same estimator the equity sleeve
   uses, but with *signed* weights, so offsetting longs/shorts correctly reduce
   estimated risk). The scale is capped at `max_vol_scale`.
2. **Per-pair cap** — no single pair may exceed `per_pair_cap` of equity.
3. **Per-asset-class gross caps** — legs within one class (crypto, equity
   cluster, bond duration) are really ONE bet; each capped class's gross
   (Σ|wᵢ| over that class) is scaled down proportionally to its cap
   (`class_gross_caps`, plus the dedicated `crypto_gross_cap` knob for crypto).
4. **Gross-leverage cap** — Σ|wᵢ| is held at or below `max_gross`.

A weight of +0.25 means "long 25% of equity of notional in this pair"; negative
is short. The sum of |weights| is gross leverage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import marks
from .fx_config import FXParams
from .pairs import ALL_PAIRS


def pair_vols(panel: dict[str, pd.DataFrame], p: FXParams) -> pd.DataFrame:
    """Annualised realised vol per pair (time x symbol).

    Annualises at the *bar frequency* (``marks.periods_per_year`` — ~8766 for
    hourly bars, 252 for daily), the one convention the rest of the book uses.
    A hardcoded 252 would understate sub-daily vol ~6x, saturating the
    vol-target scale and effectively disabling vol targeting on the intraday /
    hf books that route real orders.
    """
    return pd.DataFrame({
        sym: ind.realized_vol(df["close"], p.vol_lookback,
                              ann=marks.periods_per_year(df.index))
        for sym, df in panel.items()
    })


def size_book(tilts: pd.DataFrame, vols: pd.DataFrame, p: FXParams) -> pd.DataFrame:
    """Vectorized sizing: tilts (time x pair, [-1,1]) -> weights (time x pair).

    Applies vol targeting, the per-pair cap, the per-asset-class gross caps and
    the gross-leverage cap, in that order. Empty/unknown vols are treated as a
    neutral median so a single missing estimate can't blow up the scale.
    """
    if tilts.empty:
        return tilts

    vols = vols.reindex_like(tilts)
    # Fill missing vol with the row median (then a global fallback) so the
    # variance estimate is finite even before every pair has enough history.
    row_med = vols.median(axis=1)
    vols = vols.apply(lambda col: col.fillna(row_med)).fillna(0.10)

    wv = tilts * vols                                  # signed risk contributions
    rho = p.avg_correlation
    port_var = (1.0 - rho) * (wv ** 2).sum(axis=1) + rho * (wv.sum(axis=1) ** 2)
    port_vol = np.sqrt(port_var.clip(lower=0.0))
    scale = (p.target_vol / port_vol.replace(0.0, np.nan)).clip(upper=p.max_vol_scale)
    scale = scale.fillna(0.0)

    w = tilts.mul(scale, axis=0)
    w = w.clip(-p.per_pair_cap, p.per_pair_cap)        # hard per-pair cap

    # Asset-class gross caps: legs within one class are highly correlated with
    # each other, so three "diversified" crypto coins (or five US mega-caps, or
    # four treasury ETFs — duration is one bet) are really ONE position. Cap
    # each class's gross (Σ|w| over the class) at its cap by scaling that
    # class's legs down proportionally. Risk policy, not a market view; a None
    # cap disables that class (e.g. the crypto-only hf_crypto profile must not
    # be strangled). Crypto stays driven by the dedicated `crypto_gross_cap`
    # knob (back-compat); FX has no entry, i.e. it is uncapped by class.
    caps: dict[str, float | None] = dict(p.class_gross_caps)
    caps["crypto"] = p.crypto_gross_cap
    for klass, cap in caps.items():
        if cap is None:
            continue
        cols = [c for c in w.columns
                if c in ALL_PAIRS and ALL_PAIRS[c].asset_class == klass]
        if not cols:
            continue
        cgross = w[cols].abs().sum(axis=1)
        cscale = (cap / cgross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
        w[cols] = w[cols].mul(cscale, axis=0)

    gross = w.abs().sum(axis=1)
    delever = (p.max_gross / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    return w.mul(delever, axis=0)
