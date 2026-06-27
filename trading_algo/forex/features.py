"""Leakage-safe feature engineering and labeling for the FX ML layer.

Every feature value at bar t is computed from data ≤ t (it is information you
truly have at t's close). Labels look *forward* (next-period return / barrier
outcome) and are therefore only ever used for *training*, never as inputs — and
the walk-forward harness (`walkforward.py`) guarantees a model predicting bar t
was trained only on bars whose labels were already known before t.

Two labeling schemes:
* `direction_labels` — sign of the forward return (with an optional deadband).
* `triple_barrier_labels` — López de Prado's method: from each bar, which comes
  first within `max_h` bars — a +ATR profit barrier, a −ATR stop, or the time
  limit. Used for meta-labeling (was the primary signal's side profitable?).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .pairs import Pair

# Feature set chosen to be ECONOMICALLY GROUNDED and lean — the research is
# blunt that daily FX is near-random-walk and that piling on collinear technical
# oscillators just feeds overfitting. So: multi-horizon returns (time-series
# momentum, incl. the well-evidenced 12-month signal), a slow value/PPP proxy, a
# per-pair carry proxy (informative once pairs are pooled into one global model),
# realised vol as a risk feature, and only a handful of trend/regime indicators.
RETURN_HORIZONS = (1, 5, 21, 63, 126, 252)   # 1d … 12m time-series momentum
MA_WINDOWS = (50, 200)                        # trend proxies (kept few; collinear)
VALUE_WINDOW = 504                            # ~2y mean-reversion (value/PPP proxy)


def build_features(bars: pd.DataFrame,
                   agent_signals: pd.DataFrame | None = None,
                   pair: Pair | None = None) -> pd.DataFrame:
    """Causal feature matrix for one pair's OHLC frame.

    `agent_signals` (optional, time x agent) is concatenated so a meta-model can
    learn to *stack* the base agents alongside raw technicals. `pair` adds a
    static carry proxy that lets one pooled model distinguish high- vs low-carry
    pairs cross-sectionally.
    """
    close, high, low = bars["close"], bars["high"], bars["low"]
    f: dict[str, pd.Series] = {}

    for h in RETURN_HORIZONS:                       # time-series momentum
        f[f"ret_{h}"] = close.pct_change(h, fill_method=None)
    for n in MA_WINDOWS:                            # trend
        sma = close.rolling(n).mean()
        f[f"ma_dist_{n}"] = (close - sma) / sma

    # Value / PPP proxy: long-window z-score (slow mean reversion).
    vmean = close.rolling(VALUE_WINDOW).mean()
    vstd = close.rolling(VALUE_WINDOW).std().replace(0.0, np.nan)
    f["value_z"] = ((close - vmean) / vstd).clip(-4, 4)

    f["rsi"] = (ind.rsi(close, 14) - 50.0) / 50.0
    f["adx"] = (ind.adx(high, low, close, 14) / 50.0).clip(0, 2)   # trend-strength regime
    f["atr_rel"] = ind.atr(high, low, close, 14) / close
    f["vol_20"] = ind.realized_vol(close, 20)                      # risk feature
    f["vol_60"] = ind.realized_vol(close, 60)
    f["bb_z"] = ind.bollinger_z(close, 20).clip(-4, 4)

    upper, lower = ind.donchian(high, low, 55)
    width = (upper - lower).replace(0.0, np.nan)
    f["donch_pos"] = ((close - lower) / width).clip(0, 1)

    # Carry proxy (static per pair): tilt toward the positive-financing side.
    if pair is not None:
        f["carry"] = pd.Series((pair.swap_long_pips - pair.swap_short_pips) / 1.0,
                               index=bars.index).clip(-2, 2)

    # Calendar (FX has weak, decaying day-of-week effects — minor feature only).
    dow = bars.index.dayofweek.to_numpy()
    f["dow_sin"] = pd.Series(np.sin(2 * np.pi * dow / 5.0), index=bars.index)
    f["dow_cos"] = pd.Series(np.cos(2 * np.pi * dow / 5.0), index=bars.index)

    out = pd.DataFrame(f, index=bars.index)
    if agent_signals is not None:
        out = pd.concat([out, agent_signals.reindex(out.index).add_prefix("ag_")], axis=1)
    return out


# ---------------------------------------------------------------------------
# Labels (forward-looking — training only)
# ---------------------------------------------------------------------------
def direction_labels(close: pd.Series, horizon: int = 1,
                     deadband: float = 0.0) -> pd.Series:
    """1 if the forward `horizon`-bar return exceeds `deadband`, else 0.

    Tail rows whose forward return is unknown are NaN (excluded from training).
    """
    fwd = close.shift(-horizon) / close - 1.0
    y = (fwd > deadband).astype(float)
    y[fwd.isna()] = np.nan
    return y


def triple_barrier_labels(close: pd.Series, atr: pd.Series, side: pd.Series,
                          pt_mult: float = 1.5, sl_mult: float = 1.0,
                          max_h: int = 10) -> pd.Series:
    """Meta-label: did taking `side` (±1) at t reach the profit barrier before
    the stop within `max_h` bars? 1 = profitable trade, 0 = not.

    Barriers are ATR-scaled: +pt_mult·ATR (in the side's favour) and −sl_mult·ATR.
    A vectorised-enough loop over the bounded horizon (cheap for daily bars).
    """
    c = close.to_numpy()
    a = atr.to_numpy()
    s = side.to_numpy()
    n = len(c)
    y = np.full(n, np.nan)
    for i in range(n):
        if i + 1 >= n or s[i] == 0 or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        entry = c[i]
        up = entry + pt_mult * a[i] * np.sign(s[i])
        dn = entry - sl_mult * a[i] * np.sign(s[i])
        hi = up if s[i] > 0 else dn          # profit side for the trade direction
        lo = dn if s[i] > 0 else up
        label = 0.0
        end = min(i + max_h, n - 1)
        for j in range(i + 1, end + 1):
            move = (c[j] - entry) * np.sign(s[i])
            if move >= pt_mult * a[i]:
                label = 1.0
                break
            if move <= -sl_mult * a[i]:
                label = 0.0
                break
        else:
            # time barrier: profitable if we ended on the right side
            label = 1.0 if (c[end] - entry) * np.sign(s[i]) > 0 else 0.0
        y[i] = label
    return pd.Series(y, index=close.index)


def align_xy(features: pd.DataFrame, labels: pd.Series
             ) -> tuple[pd.DataFrame, pd.Series]:
    """Drop rows with any NaN feature or a missing label; keep them aligned."""
    df = features.copy()
    df["__y__"] = labels
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    return df.drop(columns="__y__"), df["__y__"]
