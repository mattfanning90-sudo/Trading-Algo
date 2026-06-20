"""Data layer: OHLCV download/caching via yfinance + a synthetic generator.

`load_region()` returns prices already in the region's *local trading currency*
(applying price_scale, e.g. LSE pence -> pounds). The synthetic generators let
the whole pipeline be smoke-tested with no network access — their numbers are
meaningless for performance, only for plumbing.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
import pandas as pd

from .regions import Region

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")


def _cache_path(cache_key: str) -> str:
    safe = hashlib.sha1(cache_key.encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"prices_{safe}.parquet")


def load_prices(tickers: list[str], start: str, end: str | None = None,
                cache_key: str | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Adjusted-close prices (index=date, cols=tickers). Raw Yahoo units."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = _cache_path(cache_key or ",".join(sorted(tickers)))

    if use_cache and os.path.exists(cache_file):
        # Reuse the cache for this key even if a few tickers persistently fail to
        # download (else every call re-fetches the whole universe). Return the
        # requested tickers that are present.
        df = pd.read_parquet(cache_file)
        have = [t for t in tickers if t in df.columns]
        if have:
            return df.loc[start:end, have]

    import time

    import yfinance as yf  # imported lazily so the package works offline

    raw = None
    for attempt in range(3):                       # Yahoo can be flaky — retry
        try:
            raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                              progress=False)["Close"]
            if raw is not None and len(raw):
                break
        except Exception:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(tickers[0])
    raw = raw.reindex(columns=tickers)
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    try:
        raw.to_parquet(cache_file)
    except Exception:
        pass  # parquet engine optional; caching is a nicety, not a requirement
    return raw


def load_region(region: Region, start: str, end: str | None = None,
                use_cache: bool = True,
                tickers: list[str] | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """Return (prices, index_prices) for a region in its local currency.

    Universe prices are scaled by `region.price_scale` (pence -> pounds for the
    LSE). The regime index is left in native points (the regime filter is
    scale-invariant, so it doesn't need converting).

    `tickers` overrides the universe to download — used for point-in-time
    backtests, where the download set is the union of all names ever in the
    index (including since-delisted ones), not just today's members."""
    universe = list(tickers) if tickers is not None else list(region.universe)
    download = [*dict.fromkeys([*universe, region.index_ticker])]  # dedupe, keep order
    df = load_prices(download, start, end,
                     cache_key=f"{region.key}:{start}:{end}:{len(download)}",
                     use_cache=use_cache)
    index_px = df[region.index_ticker]
    prices = df[[c for c in df.columns if c != region.index_ticker]]
    prices = prices * region.price_scale
    return prices, index_px


# ---------------------------------------------------------------------------
# Synthetic data (offline pipeline testing only)
# ---------------------------------------------------------------------------

def synthetic_prices(tickers: list[str], index_ticker: str,
                     start: str = "2012-01-01", end: str = "2026-01-01",
                     seed: int = 42, base_price: float = 20.0,
                     index_base: float = 5000.0) -> pd.DataFrame:
    """GBM with a common market factor, cross-sectional drift dispersion and
    two injected crash regimes (so the index/trend filters get exercised).
    Returns universe columns plus the index column. Pipeline testing only."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    n_days, n_assets = len(dates), len(tickers)

    market = rng.normal(0.0003, 0.009, n_days)
    for crash_start in (int(n_days * 0.3), int(n_days * 0.7)):
        market[crash_start:crash_start + 90] -= 0.004

    alphas = rng.normal(0.0001, 0.0004, n_assets)
    betas = rng.uniform(0.6, 1.4, n_assets)
    idio = rng.normal(0, 0.013, (n_days, n_assets))

    rets = alphas + betas * market[:, None] + idio
    prices = base_price * np.exp(np.cumsum(rets, axis=0))
    df = pd.DataFrame(prices, index=dates, columns=tickers)
    df[index_ticker] = index_base * np.exp(np.cumsum(market))
    return df


def synthetic_region(region: Region, start: str = "2012-01-01",
                     end: str = "2026-01-01", seed: int | None = None
                     ) -> tuple[pd.DataFrame, pd.Series]:
    """Synthetic (prices, index) for one region. Seed derived from the region
    key so each sleeve gets an independent but reproducible path."""
    if seed is None:
        seed = 1000 + sum(ord(c) for c in region.key)
    df = synthetic_prices(region.universe, region.index_ticker,
                          start=start, end=end, seed=seed)
    index_px = df[region.index_ticker]
    prices = df[[c for c in df.columns if c != region.index_ticker]]
    return prices, index_px
