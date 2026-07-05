"""Momentum-crash / crowding exposure monitor (backlog F9).

The strategy's only crash defence is the reactive dual 200-day trend/regime
filter. This monitor is an *early warning*: it flags when the book's tail risk is
elevated BEFORE the regime filter would trip, so a human can review. It is
observability-only — it never touches `compute_targets`, so invariant #3 is not
at risk (a test asserts sizing is unchanged).

Signals, all computed on data <= `asof` (no lookahead), returns-based only (no
volume/ADV dependency):
  * crowding    — average pairwise correlation of the current top-N momentum book
                  (a crowded book crashes together).
  * dispersion  — cross-sectional spread of trailing returns among those names.
  * vol_ratio   — recent index vol vs its ~3y average (a volatility spike).
  * crash_setup — the Daniel & Moskowitz configuration: index well below its
                  200-day MA AND rallied over the past month (bear-then-bounce),
                  when momentum crashes historically cluster.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals as sig

# Thresholds (documented here in one place; tune per book).
CORR_MAX = 0.70          # avg pairwise correlation above this = crowded
VOL_SPIKE = 2.0          # recent vol / 3y vol above this = spike
CRASH_BELOW_MA = -0.10   # index >=10% below its 200-day MA
CRASH_BOUNCE = 0.05      # ...and up >=5% over the past month


def crowding_report(prices: pd.DataFrame, index_px: pd.Series, region,
                    asof: pd.Timestamp | None = None, lookback: int = 63) -> dict:
    """Crowding / crash-risk metrics + an `elevated` flag as-of `asof`."""
    p = region.params
    if asof is None:
        asof = prices.index[-1]
    hist = prices.loc[:asof]
    idx = index_px.loc[:asof]

    # current top-N momentum book (candidates), correlation of their recent returns
    scores = sig.momentum_score(hist, p).loc[asof].dropna()
    top = list(scores.nlargest(min(p.top_n, len(scores))).index)
    avg_corr = float("nan")
    dispersion = float("nan")
    if len(top) >= 2:
        rets = hist[top].pct_change(fill_method=None).tail(lookback)
        corr = rets.corr().to_numpy()
        iu = np.triu_indices(len(top), k=1)
        vals = corr[iu]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            avg_corr = float(vals.mean())
        window = hist[top].tail(lookback)
        if len(window) >= 2:
            trailing = window.iloc[-1] / window.iloc[0] - 1.0
            dispersion = float(trailing.std())

    # index volatility spike (recent ~3 months vs ~3 years)
    idx_ret = idx.pct_change(fill_method=None)
    vol_recent = float(idx_ret.tail(60).std() * np.sqrt(252)) if len(idx_ret) > 5 else float("nan")
    vol_long = float(idx_ret.tail(756).std() * np.sqrt(252)) if len(idx_ret) > 60 else float("nan")
    vol_ratio = (vol_recent / vol_long) if (vol_long and vol_long > 0) else float("nan")

    # bear-then-bounce crash setup
    ma = idx.rolling(p.index_trend_ma).mean().iloc[-1] if len(idx) >= p.index_trend_ma else float("nan")
    below_ma = (float(idx.iloc[-1]) / ma - 1.0) if ma and ma == ma else float("nan")
    month_ret = (float(idx.iloc[-1]) / float(idx.iloc[-21]) - 1.0) if len(idx) > 21 else float("nan")
    crash_setup = bool(below_ma < CRASH_BELOW_MA and month_ret > CRASH_BOUNCE) \
        if (below_ma == below_ma and month_ret == month_ret) else False

    corr_flag = bool(avg_corr == avg_corr and avg_corr > CORR_MAX)
    vol_flag = bool(vol_ratio == vol_ratio and vol_ratio > VOL_SPIKE)
    elevated = bool(corr_flag or vol_flag or crash_setup)

    reasons = []
    if corr_flag:
        reasons.append(f"crowded (avg corr {avg_corr:.2f} > {CORR_MAX})")
    if vol_flag:
        reasons.append(f"vol spike ({vol_ratio:.1f}x 3y)")
    if crash_setup:
        reasons.append("bear-then-bounce crash setup")

    return {
        "asof": str(asof.date()) if hasattr(asof, "date") else str(asof),
        "n_names": len(top),
        "avg_correlation": round(avg_corr, 3) if avg_corr == avg_corr else None,
        "dispersion": round(dispersion, 4) if dispersion == dispersion else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio == vol_ratio else None,
        "below_200dma": round(below_ma, 3) if below_ma == below_ma else None,
        "crash_setup": crash_setup,
        "elevated": elevated,
        "reasons": reasons,
    }
