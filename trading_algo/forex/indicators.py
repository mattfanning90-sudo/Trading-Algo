"""Technical indicator library — vectorized, with streaming variants.

Two flavours of every hot indicator:

* **Vectorized** functions (``ema``, ``rsi``, ``atr``, ``adx`` …) operate on
  whole pandas Series/DataFrames. They are what the backtest and the
  agent layer use — one pass, no Python loops, no lookahead (every value at
  index t uses only data ≤ t).
* **Streaming** classes (``StreamingEMA``, ``StreamingATR``) update in O(1) per
  new bar with a fixed memory footprint. They back the low-latency live path,
  where recomputing a 100k-bar rolling window on every tick would be wasteful.
  ``tests/`` pins the streaming output to the vectorized output so the two can
  never silently diverge.

Wilder's smoothing (RSI/ATR/ADX) is the EMA with α = 1/n (``adjust=False``),
the standard fast approximation used across charting platforms.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_EPS = 1e-12


# ---------------------------------------------------------------------------
# Moving averages / momentum
# ---------------------------------------------------------------------------
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window).mean()


def roc(s: pd.Series, window: int) -> pd.Series:
    """Rate of change over `window` bars (fractional)."""
    return s.pct_change(window, fill_method=None)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series]:
    """(macd line, signal line)."""
    line = ema(s, fast) - ema(s, slow)
    return line, ema(line, signal)


def realized_vol(close: pd.Series, window: int, ann: float = 252) -> pd.Series:
    """Annualised trailing realised volatility from close-to-close returns."""
    rets = close.pct_change(fill_method=None)
    return rets.rolling(window).std() * np.sqrt(ann)


# ---------------------------------------------------------------------------
# Oscillators / bands
# ---------------------------------------------------------------------------
def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0)  # zero average loss => maximally overbought


def bollinger_z(close: pd.Series, window: int = 20) -> pd.Series:
    """Z-score of price vs its rolling mean (how many std devs from the middle band)."""
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    return (close - mid) / sd.replace(0.0, np.nan)


def donchian(high: pd.Series, low: pd.Series, window: int
             ) -> tuple[pd.Series, pd.Series]:
    """Donchian channel (upper, lower) computed on bars *strictly before* the
    current one — shifted by 1 so a breakout test never peeks at its own bar."""
    upper = high.rolling(window).max().shift(1)
    lower = low.rolling(window).min().shift(1)
    return upper, lower


# ---------------------------------------------------------------------------
# True-range family
# ---------------------------------------------------------------------------
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
        ) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
        ) -> pd.Series:
    """Average Directional Index in [0, 100] — trend *strength* (not direction)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    tr = true_range(high, low, close)
    atr_w = tr.ewm(alpha=1.0 / window, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / window, adjust=False).mean() / atr_w.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / window, adjust=False).mean() / atr_w.replace(0.0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / window, adjust=False).mean().fillna(0.0)


# ---------------------------------------------------------------------------
# Streaming (O(1) per bar) — the low-latency live path
# ---------------------------------------------------------------------------
@dataclass
class StreamingEMA:
    """Incremental EMA matching `ema(span)` exactly after the first sample."""
    span: int
    value: float | None = None

    @property
    def alpha(self) -> float:
        return 2.0 / (self.span + 1.0)

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value


@dataclass
class StreamingATR:
    """Incremental Wilder ATR matching `atr(window)` exactly (seeded like ewm)."""
    window: int
    _atr: float | None = None
    _prev_close: float | None = None

    @property
    def alpha(self) -> float:
        return 1.0 / self.window

    def update(self, high: float, low: float, close: float) -> float:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._atr = tr if self._atr is None else self.alpha * tr + (1 - self.alpha) * self._atr
        self._prev_close = close
        return self._atr
