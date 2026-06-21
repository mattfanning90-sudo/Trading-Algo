"""Alpaca market data — free real-time US equities + free paper trading.

Alpaca gives retail **free real-time US-equity bars** (the IEX feed) plus a free
paper-trading account and an open-source SDK (`alpaca-py`). The catch to be
honest about: the free real-time feed is **IEX only** — a single venue that is a
few percent of consolidated US volume, so quotes/prints are thinner than the full
SIP tape (which is a paid subscription). It's genuinely usable for intraday
research and paper trading; size and microstructure assumptions should stay
conservative. See docs/DATA_FEEDS.md.

Credentials come from the environment (never the repo):
    APCA_API_KEY_ID, APCA_API_SECRET_KEY   — free from the Alpaca dashboard

`alpaca-py` is imported lazily (optional dependency: `pip install alpaca-py`), so
the package still works offline; tests use the synthetic generator.
"""
from __future__ import annotations

import os

import pandas as pd

from . import fx_data
from .pairs import EQUITY_UNIVERSE

# A liquid US-equity default universe (tickers double as the canonical id).
ALPACA_UNIVERSE = list(EQUITY_UNIVERSE)

_FIELDS = ["open", "high", "low", "close"]


def _timeframe(interval: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    return {
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "60m": TimeFrame(1, TimeFrameUnit.Hour),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }.get(interval, TimeFrame(1, TimeFrameUnit.Hour))


def _client():
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError as e:
        raise SystemExit("alpaca-py not installed — run `pip install alpaca-py` for "
                         "live Alpaca equities, or use --synthetic offline.") from e
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not (key and secret):
        raise SystemExit("set APCA_API_KEY_ID and APCA_API_SECRET_KEY (free Alpaca "
                         "keys) to use the alpaca source; see docs/DATA_FEEDS.md.")
    return StockHistoricalDataClient(key, secret)


def load_ohlcv(symbols: list[str], timeframe: str = "1h", limit: int = 1000,
               feed: str = "iex") -> dict[str, pd.DataFrame]:
    """OHLC panel from Alpaca (free IEX feed by default), aligned like every source."""
    from alpaca.data.requests import StockBarsRequest
    client = _client()
    req = StockBarsRequest(symbol_or_symbols=list(symbols),
                           timeframe=_timeframe(timeframe), limit=int(limit), feed=feed)
    try:
        bars = client.get_stock_bars(req).df
    except Exception as exc:
        print(f"  [alpaca] {exc!r}")
        return {}
    frames: dict[str, pd.DataFrame] = {}
    if bars is None or bars.empty:
        return {}
    for sym in symbols:
        if sym not in bars.index.get_level_values(0):
            continue
        df = bars.xs(sym, level=0)
        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        frames[sym] = df[[c for c in _FIELDS if c in df.columns]]
    return fx_data._align(frames)


def synthetic_panel(symbols: list[str], timeframe: str = "1d", days: int = 5,
                    seed: int | None = None) -> dict[str, pd.DataFrame]:
    """Offline synthetic equity panel (pipeline testing only)."""
    if timeframe in ("1d", "B"):
        return fx_data.synthetic_panel(symbols)
    return fx_data.synthetic_recent(symbols, timeframe=timeframe, days=days, seed=seed)
