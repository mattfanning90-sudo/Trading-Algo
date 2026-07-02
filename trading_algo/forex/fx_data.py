"""FX market-data layer: OHLC bar panels + a synthetic generator.

A *panel* is ``dict[symbol -> DataFrame]`` where each frame has columns
``[open, high, low, close]`` indexed by timestamp, all aligned to one common
calendar (outer-join + forward-fill) so the agent/backtest layers can work
column-wise without re-aligning.

`load_panel()` pulls bars from Yahoo (daily by default; pass ``interval`` for
intraday, e.g. ``"60m"``/``"15m"``). `synthetic_panel()` fabricates trending and
ranging regimes with no network, so the whole pipeline is testable offline — its
numbers are plumbing only, never performance.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
import pandas as pd

from .pairs import Pair, get_pair

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
_FIELDS = ["open", "high", "low", "close"]


def _cache_path(key: str) -> str:
    safe = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"fx_{safe}.parquet")


def _download_one(pair: Pair, start: str, end: str | None, interval: str
                  ) -> pd.DataFrame:
    import time

    import yfinance as yf  # lazy import so the package works offline

    raw = None
    for attempt in range(3):
        try:
            raw = yf.download(pair.yahoo_ticker, start=start, end=end,
                              interval=interval, auto_adjust=True, progress=False)
            if raw is not None and len(raw):
                break
        except Exception:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))
    if raw is None or not len(raw):
        return pd.DataFrame(columns=_FIELDS)
    if isinstance(raw.columns, pd.MultiIndex):       # (field, ticker) -> field
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)
    return raw[[c for c in _FIELDS if c in raw.columns]].dropna(how="all")


def load_panel(symbols: list[str], start: str, end: str | None = None,
               interval: str = "1d", use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """OHLC panel for `symbols`, aligned to a common forward-filled calendar."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        pair = get_pair(sym)
        cache_file = _cache_path(f"{sym}:{start}:{end}:{interval}")
        df = None
        if use_cache and os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
            except Exception:
                df = None
        if df is None:
            df = _download_one(pair, start, end, interval)
            try:
                df.to_parquet(cache_file)
            except Exception:
                pass
        if len(df):
            frames[sym] = df.loc[start:end]
    return _align(frames)


def _align(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Reindex every frame onto the union calendar and forward-fill gaps."""
    if not frames:
        return frames
    idx = None
    for df in frames.values():
        idx = df.index if idx is None else idx.union(df.index)
    out = {}
    for sym, df in frames.items():
        out[sym] = df.reindex(idx).ffill().dropna(how="all")
    return out


def closes(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Close-price matrix (index=time, columns=symbols) from a panel."""
    return pd.DataFrame({sym: df["close"] for sym, df in panel.items()})


# ---------------------------------------------------------------------------
# Synthetic data (offline pipeline testing only)
# ---------------------------------------------------------------------------
_SYNTH_LEVEL = {
    "EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 150.0, "AUDUSD": 0.66,
    "USDCAD": 1.36, "USDCHF": 0.90, "NZDUSD": 0.61, "EURGBP": 0.85,
    "EURJPY": 162.0, "GBPJPY": 190.0, "AUDJPY": 99.0, "AUDNZD": 1.08,
    "EURAUD": 1.63,
    "BTCUSD": 60000.0, "ETHUSD": 3000.0, "SOLUSD": 150.0,
    "AAPL": 195.0, "MSFT": 420.0, "NVDA": 120.0, "SPY": 540.0, "QQQ": 470.0,
    "TLT": 95.0, "IEF": 94.0, "AGG": 98.0, "SHY": 82.0,
}

# Pairs that need a higher synthetic volatility path (crypto ≫ equities ≫ FX ≫ bonds).
_SYNTH_VOL = {"BTCUSD": 4.0, "ETHUSD": 4.5, "SOLUSD": 6.0,
              "AAPL": 1.8, "MSFT": 1.6, "NVDA": 2.6, "SPY": 1.2, "QQQ": 1.4,
              "TLT": 0.9, "IEF": 0.5, "AGG": 0.35, "SHY": 0.15}

# Yahoo interval -> pandas frequency (for the synthetic generator).
_PD_FREQ = {"1d": "B", "1h": "1h", "60m": "1h", "30m": "30min", "15m": "15min",
            "5m": "5min", "1m": "1min"}


def _date_index(start, end, freq):
    if freq in ("B", "1d"):
        return pd.bdate_range(start, end)
    idx = pd.date_range(start, end, freq=_PD_FREQ.get(freq, freq))
    return idx[idx.weekday < 5]          # FX trades on weekdays


def synthetic_pair(pair: Pair, start: str = "2015-01-01", end: str = "2026-01-01",
                   seed: int = 0, freq: str = "B") -> pd.DataFrame:
    """One pair's OHLC with persistent trend regimes (AR(1) drift) plus ranging
    stretches, so trend/breakout *and* mean-reversion agents all get exercised.
    `freq` accepts a Yahoo interval (e.g. "60m") for intraday synthetic bars."""
    rng = np.random.default_rng(seed)
    dates = _date_index(start, end, freq)
    n = len(dates)
    vmult = _SYNTH_VOL.get(pair.symbol, 1.0)        # crypto runs hotter than FX

    # AR(1) drift => trends that build and decay (gives breakouts & reversals).
    drift = np.zeros(n)
    phi, sigma_d = 0.985, 0.00035 * vmult
    for t in range(1, n):
        drift[t] = phi * drift[t - 1] + rng.normal(0.0, sigma_d)
    idio = rng.normal(0.0, 0.0045 * vmult, n)
    lvl = _SYNTH_LEVEL.get(pair.symbol, 1.0)
    # Mean-reverting (OU) log-price: keeps the path within a realistic band of the
    # reference (so spreads quoted in pips stay sane, esp. for crypto) while
    # ALWAYS moving — a hard clip would stick the price dead-flat for long
    # stretches, which is both unrealistic and breaks the windowed fast path.
    kappa = 0.004
    dev = np.empty(n)
    acc = 0.0
    for t in range(n):
        acc = acc * (1.0 - kappa) + drift[t] + idio[t]
        dev[t] = acc
    close = lvl * np.exp(dev)

    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] * np.exp(rng.normal(0.0, 0.0008, n - 1))   # small gaps
    rng_bar = np.abs(rng.normal(0.0, 0.004, n)) * close                # intrabar range
    hi = np.maximum(open_, close) + rng_bar * rng.uniform(0.2, 0.8, n)
    lo = np.minimum(open_, close) - rng_bar * rng.uniform(0.2, 0.8, n)
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close}, index=dates)


def synthetic_panel(symbols: list[str], start: str = "2015-01-01",
                    end: str = "2026-01-01", seed: int | None = None,
                    freq: str = "B") -> dict[str, pd.DataFrame]:
    """Synthetic OHLC panel — one independent reproducible path per pair.
    Pass a Yahoo interval as `freq` (e.g. "60m") for intraday synthetic bars."""
    frames = {}
    for sym in symbols:
        pair = get_pair(sym)
        s = seed if seed is not None else 1000 + sum(ord(c) for c in sym)
        frames[sym] = synthetic_pair(pair, start=start, end=end, seed=s, freq=freq)
    return _align(frames)


def synthetic_recent(symbols: list[str], timeframe: str = "1m", days: int = 3,
                     seed: int | None = None) -> dict[str, pd.DataFrame]:
    """A short recent window of synthetic intraday bars (offline pipeline tests).

    Shared by every intraday data source (crypto / OANDA / Alpaca / OpenBB) so
    they all fabricate the same panel shape with no network. Pipeline only —
    never performance.
    """
    end = pd.Timestamp("2025-01-04")
    start = (end - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return synthetic_panel(symbols, start=start, end=end.strftime("%Y-%m-%d"),
                           seed=seed, freq=timeframe)
