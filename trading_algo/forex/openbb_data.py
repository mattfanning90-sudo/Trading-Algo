"""OpenBB market data — the open-source "Bloomberg Terminal" for research.

OpenBB (https://openbb.co) is the closest thing to a **free, open-source terminal**:
it aggregates many data providers behind one Python SDK. It is best understood as
a **research / backtest data source, not a live execution feed** — most of its
free providers are end-of-day or delayed, and the rich real-time providers behind
it are paid. We wire it in for richer historical/intraday research data; for live
intraday use the crypto (ccxt), OANDA or Alpaca sources instead.

By default this uses OpenBB's free `yfinance` provider, so no key is needed; pass
`provider=` (and configure its key in OpenBB) for others. `openbb` is imported
lazily (optional dependency: `pip install openbb`), so the package still works
offline; tests use the synthetic generator. See docs/DATA_FEEDS.md.
"""
from __future__ import annotations

import pandas as pd

from . import fx_data
from .pairs import EQUITY_UNIVERSE

# Research default: a liquid US-equity basket (OpenBB also covers FX/crypto).
OPENBB_UNIVERSE = list(EQUITY_UNIVERSE)

# Yahoo-style interval -> OpenBB interval string.
_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
             "1h": "1h", "60m": "1h", "1d": "1d"}

_FIELDS = ["open", "high", "low", "close"]


def _obb():
    try:
        from openbb import obb
    except ImportError as e:
        raise SystemExit("openbb not installed — run `pip install openbb` for the "
                         "OpenBB research source, or use --synthetic offline.") from e
    return obb


def load_ohlcv(symbols: list[str], timeframe: str = "1d", limit: int = 1000,
               provider: str = "yfinance") -> dict[str, pd.DataFrame]:
    """OHLC panel from OpenBB's equity price history, aligned like every source."""
    obb = _obb()
    interval = _INTERVAL.get(timeframe, "1d")
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            res = obb.equity.price.historical(symbol=sym, interval=interval,
                                              provider=provider)
            df = res.to_dataframe()
        except Exception as exc:                       # one bad symbol shouldn't kill the run
            print(f"  [openbb] {sym}: {exc!r}")
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        cols = [c for c in _FIELDS if c in df.columns]
        if cols:
            frames[sym] = df[cols].tail(int(limit))
    return fx_data._align(frames)


def synthetic_panel(symbols: list[str], timeframe: str = "1d", days: int = 5,
                    seed: int | None = None) -> dict[str, pd.DataFrame]:
    """Offline synthetic panel (pipeline testing only)."""
    if timeframe in ("1d", "B"):
        return fx_data.synthetic_panel(symbols)
    return fx_data.synthetic_recent(symbols, timeframe=timeframe, days=days, seed=seed)
