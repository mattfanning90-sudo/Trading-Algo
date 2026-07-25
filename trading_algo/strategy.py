"""The single source of truth for target portfolio weights.

`compute_targets()` turns a price history into the desired weight vector for one
as-of date. It is the ONLY place selection + vol targeting live, and it is
called by BOTH the backtester and the paper-trading engine. That is what keeps
invariant #4 (backtest and paper must agree) true by construction — there is no
second copy of the weight logic to drift out of sync.

Two entry points, ONE formula
-----------------------------
The signal frames (momentum, vol, trend, regime, value) are causal rolling
transforms of the whole price history, but a rebalance only ever reads ONE row
out of each. Recomputing them per date made a backtest O(rebalances × history);
they are now built once by `precompute()` into a `SignalPanel`, and
`targets_at()` reads the as-of row.

    precompute(prices, index, p) -> SignalPanel      # the expensive part, once
    targets_at(panel, p, asof, …) -> weights         # the ONE weight formula
    compute_targets(prices, index, p, asof, …)       # = precompute + targets_at

`compute_targets` is unchanged for every existing caller (paper trading, the
live planner, the tests) — it is now a thin composition of the two. The
backtester builds the panel once and calls `targets_at` per date. Both roads run
the *same* `targets_at` body, so invariant #3 is preserved by construction: there
is still exactly one place selection + vol targeting happen.

No lookahead (invariant #1) is preserved too: every frame in a `SignalPanel` is
built from causal ops (`shift`, `rolling`, `ffill`), and `targets_at` reads a
single row with `.loc[asof]`, so a decision can never see past `asof`. This is
the same structure the FX subsystem already uses
(`forex.fx_strategy.target_weights_history`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import signals as sig
from .config import StrategyParams


def vol_target(weights: pd.Series, vols: pd.Series, p: StrategyParams) -> pd.Series:
    """Scale a raw weight book toward the portfolio volatility target.

    Portfolio vol is estimated with the constant-average-correlation
    approximation:

        var ≈ (1-ρ)·Σ(wᵢσᵢ)²  +  ρ·(Σ wᵢσᵢ)²

    where ρ = `avg_correlation`. The book is then scaled by
    target_vol / est_vol, capped at `max_vol_scale`, and finally de-levered so
    gross exposure never exceeds `max_gross`.
    """
    if weights.empty:
        return weights
    wv = (weights * vols.reindex(weights.index)).dropna()
    if wv.empty:
        return pd.Series(dtype=float)

    rho = p.avg_correlation
    port_var = (1.0 - rho) * (wv**2).sum() + rho * (wv.sum() ** 2)
    port_vol = float(np.sqrt(max(port_var, 0.0)))

    scale = min(p.target_vol / max(port_vol, 1e-9), p.max_vol_scale)
    w = weights * scale

    # Gross exposure is Σ|w| — for a long-only book this equals w.sum(); for a
    # long/short book the legs net out, so the abs form is what must respect the
    # leverage cap.
    gross = w.abs().sum()
    if gross > p.max_gross:
        w = w * (p.max_gross / gross)
    return w


def _apply_capacity(w: pd.Series, capacity: pd.Series | None) -> pd.Series:
    """Cap each name's weight magnitude at `capacity` (a per-name max weight),
    never re-levering (backlog F15 / foundation P0-I). This is the ONE place a
    liquidity/capacity constraint enters the weight vector, so backtest and paper
    stay identical (invariant #3). No-op when `capacity` is None."""
    if capacity is None or w.empty:
        return w
    cap = capacity.reindex(w.index).fillna(np.inf).clip(lower=0.0)
    return np.sign(w) * np.minimum(w.abs(), cap)


@dataclass(frozen=True)
class SignalPanel:
    """Every causal signal frame a rebalance can read, computed once.

    Built by `precompute()` for one (prices, index_prices, params) triple and
    consumed one row at a time by `targets_at()`. Frames a given book's params
    can never reach are left as None rather than computed and discarded:

      * `trend` / `risk_on` — not built for a `long_short` book, which is purely
        cross-sectional and by design has no directional or timing filter.
      * `risk_on` — also None when `regime_filter` is off (the `ultra` book).
      * `value` — only built when `use_value` is on.

    That mirrors exactly which branches the weight formula takes, so the panel
    never computes something the formula would not have.
    """

    scores: pd.DataFrame
    vols: pd.DataFrame
    trend: pd.DataFrame | None = None
    risk_on: pd.Series | None = None
    value: pd.DataFrame | None = None


def precompute(prices: pd.DataFrame, index_prices: pd.Series,
               p: StrategyParams) -> SignalPanel:
    """Build every signal frame `p` can require, over the whole history, once.

    This is the expensive half of a rebalance (rolling windows over the full
    panel) and it is identical for every as-of date, so a backtest builds it once
    and reuses it. All ops are causal — `shift`, `rolling`, `ffill` — so no row
    of the result can depend on a later row.
    """
    scores = sig.momentum_score(prices, p)
    vols = sig.realised_vol(prices, p)

    # A market-neutral book takes neither the trend nor the regime branch, so
    # building them would be pure waste (and would misrepresent the formula).
    if p.long_short:
        return SignalPanel(scores=scores, vols=vols)

    risk_on = None
    if p.regime_filter:
        risk_on = sig.index_risk_on(index_prices, p).reindex(prices.index).ffill()
    value = sig.value_score(prices, p) if p.use_value else None
    return SignalPanel(scores=scores, vols=vols,
                       trend=sig.stock_trend_ok(prices, p),
                       risk_on=risk_on, value=value)


def targets_at(panel: SignalPanel, p: StrategyParams, asof: pd.Timestamp,
               eligible: set[str] | None = None,
               capacity: pd.Series | None = None) -> pd.Series:
    """**The one weight formula** — selection + vol targeting for one date.

    Reads the `asof` row out of a prebuilt `SignalPanel`. Both the backtester
    (via a shared panel) and paper/live trading (via `compute_targets`) end up
    here, so there is exactly one place weights are made (invariant #3).

    Returns a Series of weights whose gross is ≤ `max_gross`; an empty Series
    means "go to cash" (regime risk-off, or nothing eligible).

    `eligible`, if given, restricts the candidate set to those tickers — used for
    point-in-time backtests so a name can't be picked before it was actually an
    index member. `capacity`, if given, is a per-name maximum weight (the F15
    pre-trade ADV cap) applied after vol targeting. None = no cap.
    """
    scores = panel.scores.loc[asof]
    if eligible is not None:
        scores = scores[scores.index.isin(eligible)]
    vols = panel.vols.loc[asof]

    # Market-neutral book: dollar-neutral long/short, no directional filters.
    # Still routed through the single vol_target so backtest and paper agree.
    if p.long_short:
        raw = sig.select_long_short(scores, vols, p)
        return _apply_capacity(vol_target(raw, vols, p), capacity)

    # A panel is built FOR a particular params object. Using one built for a
    # different book (e.g. a long/short panel, which carries no trend frame, with
    # long-only params) would silently drop a filter and produce a book nobody
    # asked for — so refuse loudly instead. This is the one new failure mode the
    # precompute/targets_at split introduces, and it must never be silent.
    if panel.trend is None:
        raise ValueError(
            "SignalPanel carries no trend frame but these params need one — it "
            "was built for a long/short book. Rebuild it with precompute(prices, "
            "index_prices, p) for the params you are pricing.")
    trend = panel.trend.loc[asof]

    # The PARAMS decide whether the regime gate applies — never the panel's
    # shape. Reading it off the panel would mean a regime-on panel silently
    # gating a book that turned the filter off (`ultra`), and vice versa.
    risk_on = True
    if p.regime_filter:
        if panel.risk_on is None:
            raise ValueError(
                "SignalPanel carries no regime series but regime_filter is on — "
                "it was built for a book with the gate off. Rebuild it with "
                "precompute() for the params you are pricing.")
        risk_on = bool(panel.risk_on.loc[asof])

    rank_score = None
    if p.use_value:
        if panel.value is None:
            raise ValueError(
                "SignalPanel carries no value frame but use_value is on — it was "
                "built for a momentum-only book. Rebuild it with precompute() "
                "for the params you are pricing.")
        val = panel.value.loc[asof]
        if eligible is not None:
            val = val[val.index.isin(eligible)]
        # cross-sectional percentile blend: high = strong momentum AND cheap
        rank_score = (p.momentum_weight * scores.rank(pct=True)
                      + p.value_weight * val.rank(pct=True))

    raw = sig.select_portfolio(scores, trend, vols, risk_on, p, rank_score=rank_score)
    return _apply_capacity(vol_target(raw, vols, p), capacity)


def compute_targets(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams, asof: pd.Timestamp | None = None,
                    eligible: set[str] | None = None,
                    capacity: pd.Series | None = None) -> pd.Series:
    """Target weights for one rebalance date (default: the latest available).

    The single-date entry point used by paper trading and the live planner:
    `precompute()` the panel, then read `asof` out of it. Identical in every
    respect to calling `targets_at` on a shared panel — same formula, same
    numbers — it just doesn't get to amortise the panel across dates.
    """
    if asof is None:
        asof = prices.index[-1]
    return targets_at(precompute(prices, index_prices, p), p, asof,
                      eligible=eligible, capacity=capacity)
