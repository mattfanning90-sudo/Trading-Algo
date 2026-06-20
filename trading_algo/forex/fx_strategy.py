"""The single source of truth for FX target weights.

`target_weights_history()` runs the whole pipeline once, vectorized:

    panel ──▶ AgentPool (parallel agents) ──▶ ensemble tilts ──▶ risk sizing
          └────────────────────────────────────────────────▶ weights(time × pair)

`compute_targets()` is just the latest row of that history. **Both** the
backtester and the live paper book call these functions — there is no second
copy of the weight logic — so paper and backtest agree by construction (the FX
analog of the equity system's invariant #3, pinned by `tests/test_fx_*`).

No lookahead: weightₜ uses only data ≤ t; the backtest applies it to the return
realised over t→t+1.
"""
from __future__ import annotations

import pandas as pd

from . import ensemble, risk
from .agents import AgentPool, PairContext
from .fx_config import FXParams
from .fx_data import closes
from .pairs import get_pair


def _pool(pool: AgentPool | None) -> AgentPool:
    return pool if pool is not None else AgentPool()


def min_history(p: FXParams) -> int:
    """Bars of history that actually affect the latest weight.

    Older bars fall out of every rolling window, so the live path can trim the
    panel to this many recent bars and get an identical latest row in O(window)
    rather than O(history) time — this is what keeps per-cycle latency flat as
    years of data accumulate.
    """
    # EWM indicators (ema/atr/adx/rsi) are IIR filters with exponentially-
    # decaying infinite memory, so allow the slowest one to settle (~6 spans →
    # seed error e^(-6) ≈ 1e-8, well below any trading threshold). Hedge/adaptive
    # also chain a rolling loss over a rolling scale (~2·agent_lookback bars).
    ewm_settle = 6 * p.ema_slow
    chain = 2 * p.agent_lookback + 2 * p.vol_lookback + 20
    return ewm_settle + chain


def target_weights_history(panel: dict[str, pd.DataFrame], p: FXParams,
                           pool: AgentPool | None = None,
                           return_parts: bool = False):
    """Full signed-weight history (index=time, columns=pairs).

    With ``return_parts=True`` also returns the intermediate (signals, tilts) so
    the explainability layer can narrate a decision without recomputing — there
    is still exactly one weight formula (this function), preserving invariant #3.
    """
    if not panel:
        empty = pd.DataFrame()
        return (empty, {}, empty) if return_parts else empty
    pool = _pool(pool)
    contexts = {sym: PairContext(get_pair(sym)) for sym in panel}
    signals = pool.evaluate(panel, contexts, p)
    rets = closes(panel).pct_change(fill_method=None)
    tilts = ensemble.ensemble_tilts(signals, rets, p)
    vols = risk.pair_vols(panel, p)
    weights = risk.size_book(tilts, vols, p)
    return (weights, signals, tilts) if return_parts else weights


def compute_targets(panel: dict[str, pd.DataFrame], p: FXParams,
                    pool: AgentPool | None = None,
                    asof: pd.Timestamp | None = None,
                    fast: bool = True) -> pd.Series:
    """Target weights for one as-of bar (default: the latest available).

    Returns a Series (index=pair, signed weights). An all-zero / empty result
    means "hold flat". With ``fast=True`` (the live default) the panel is trimmed
    to `min_history(p)` recent bars first — the latest weight is identical but the
    cycle is bounded-time regardless of how much history exists.
    """
    if fast and panel:
        n = min_history(p)
        cut = None if asof is None else asof
        panel = {s: (df.loc[:cut] if cut is not None else df).tail(n)
                 for s, df in panel.items()}
        asof = None  # already trimmed to <= asof
    weights = target_weights_history(panel, p, pool=pool)
    if weights.empty:
        return pd.Series(dtype=float)
    if asof is not None:
        weights = weights.loc[:asof]
        if weights.empty:
            return pd.Series(dtype=float)
    return weights.iloc[-1]
