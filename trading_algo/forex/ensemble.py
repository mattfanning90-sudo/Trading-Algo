"""Combine the parallel agents into one directional *tilt* per pair.

Each agent emits a [-1, 1] signal; the ensemble blends them into a single
[-1, 1] conviction per pair (positive = net long). Two modes:

* ``equal``    — straight average of the agents.
* ``adaptive`` — weight each agent by its own recent risk-adjusted performance
                 on that pair (rolling information ratio of signalₜ₋₁·returnₜ),
                 with a floor so no agent is ever fully switched off. Weights at
                 bar t depend only on performance through t, so there is no
                 lookahead. This lets the system lean on whichever agents are
                 currently "right" per pair, and back off the ones that aren't.

Output is a DataFrame (index=time, columns=pairs) of tilts in [-1, 1]; turning
tilts into actual position sizes (vol targeting, leverage caps) is the risk
layer's job.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .fx_config import FXParams


def _hedge_pair_tilt(sig: pd.DataFrame, ret: pd.Series, p: FXParams) -> pd.Series:
    """Hedge / multiplicative-weights blend of one pair's agents.

    Each agent accrues a bounded loss ℓ = ½(1 − tanh(pnl/scale)) ∈ (0,1) from its
    realised pnl (signalₜ₋₁·returnₜ). Weights are wᵢ ∝ exp(−η·Σ_window ℓᵢ), i.e.
    multiplicative weights over a trailing window (the window gives the
    fixed-share recovery property so a temporarily-bad agent isn't killed off).
    Weights are shifted one bar so a tilt at t never uses t's own outcome.
    """
    pnl = sig.shift(1).mul(ret, axis=0)
    # Causal scale (rolling, NOT full-sample) so losses use only data ≤ t.
    scale = pnl.rolling(p.vol_lookback, min_periods=p.vol_lookback // 2).std().replace(0.0, np.nan)
    loss = 0.5 * (1.0 - np.tanh(pnl / scale))               # in (0, 1)
    roll = loss.rolling(p.agent_lookback, min_periods=p.agent_lookback // 2).sum()
    w = np.exp(-p.hedge_eta * roll).shift(1)                 # use info ≤ t-1
    wsum = w.sum(axis=1).replace(0.0, np.nan)
    wn = w.div(wsum, axis=0)
    n = sig.shape[1]
    wn = (1.0 - p.agent_floor_weight) * wn + p.agent_floor_weight / n   # fixed share
    tilt = (wn * sig).sum(axis=1)
    return tilt.fillna(sig.mean(axis=1))   # fall back to 1/N before warmup


def _adaptive_pair_tilt(sig: pd.DataFrame, ret: pd.Series, p: FXParams) -> pd.Series:
    """Performance-weighted blend of one pair's agent signals."""
    L = p.agent_lookback
    # Each agent's realised pnl proxy: yesterday's signal times today's return.
    pnl = sig.shift(1).mul(ret, axis=0)
    mean = pnl.rolling(L, min_periods=L // 2).mean()
    std = pnl.rolling(L, min_periods=L // 2).std().replace(0.0, np.nan)
    score = (mean / std).clip(lower=0.0)                  # info ratio, long-only weight
    weight = score.fillna(0.0) + p.agent_floor_weight      # keep everyone in the game
    wsum = weight.sum(axis=1).replace(0.0, np.nan)
    tilt = (weight * sig).sum(axis=1) / wsum
    return tilt.fillna(0.0)


def ensemble_tilts(signals: dict[str, pd.DataFrame], returns: pd.DataFrame,
                   p: FXParams) -> pd.DataFrame:
    """Blend agent signals into one tilt series per pair.

    `signals`  : {symbol -> DataFrame(time x agent)} from the AgentPool.
    `returns`  : close-to-close returns (time x symbol), used for adaptive scoring.
    """
    out: dict[str, pd.Series] = {}
    for sym, sig in signals.items():
        has_ret = sym in returns.columns
        if p.agent_weighting == "hedge" and has_ret:
            tilt = _hedge_pair_tilt(sig, returns[sym], p)
        elif p.agent_weighting == "adaptive" and has_ret:
            tilt = _adaptive_pair_tilt(sig, returns[sym], p)
        else:
            tilt = sig.mean(axis=1)
        out[sym] = tilt.clip(-1.0, 1.0).fillna(0.0)
    return pd.DataFrame(out)
