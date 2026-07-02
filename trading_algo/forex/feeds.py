"""Pluggable market-data sources behind one resolver.

Every source returns the *same* panel shape (``dict[symbol -> OHLC DataFrame]``,
aligned + forward-filled) so the agents / ensemble / risk / book / backtest stay
completely source-agnostic — exactly like the equity sleeve's `Region` record.

| source   | what                                   | cost / access            |
|----------|----------------------------------------|--------------------------|
| `yahoo`  | FX majors + crypto (delayed intraday)  | free, no key (default)   |
| `crypto` | real-time crypto OHLCV via ccxt        | free, no key             |
| `oanda`  | real-time FX via a practice account    | free account + token     |
| `alpaca` | real-time US equities (free IEX feed)  | free account + keys      |
| `openbb` | open-source research aggregator        | research, not a live feed|

Live sources need credentials (env vars) and that source's optional dependency;
every source also ships a synthetic generator so the whole pipeline is testable
offline. Full setup + honesty on each: docs/DATA_FEEDS.md.
"""
from __future__ import annotations

import pandas as pd

from . import fx_config as cfg
from . import fx_data

SOURCES = ["yahoo", "crypto", "oanda", "alpaca", "openbb"]


def _intraday_start(interval: str) -> str:
    """How far back to fetch Yahoo intraday (its history limits)."""
    days = {"60m": 700, "1h": 700, "30m": 55, "15m": 55, "5m": 55, "1m": 7}.get(interval, 700)
    return (pd.Timestamp.utcnow() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def resolve_source(source: str | None, exchange: str | None) -> str:
    """Normalise the source name. ``--exchange`` implies crypto (back-compat)."""
    source = (source or "yahoo").lower()
    if exchange and source == "yahoo":
        return "crypto"
    if source not in SOURCES:
        raise SystemExit(f"unknown --source {source!r}; known: {SOURCES}")
    return source


def default_universe(source: str, profile_name: str | None = None) -> list[str]:
    """The natural instrument universe for a source (FX majors / crypto / equities)."""
    from .pairs import DEFAULT_UNIVERSE
    if source == "crypto" or profile_name == "hf_crypto":
        from . import crypto_data
        return list(crypto_data.CRYPTO_UNIVERSE)
    if source == "oanda":
        from . import oanda_data
        return list(oanda_data.OANDA_UNIVERSE)
    if source in ("alpaca", "openbb"):
        from .pairs import EQUITY_UNIVERSE
        return list(EQUITY_UNIVERSE)
    return list(DEFAULT_UNIVERSE)


def load(symbols: list[str], synthetic: bool = False, interval: str = "1d",
         source: str = "yahoo", exchange: str | None = None,
         use_cache: bool = False, min_bars: int | None = None) -> dict[str, pd.DataFrame]:
    """Load an aligned OHLC panel for `symbols` from the chosen `source`.

    `min_bars` bounds how much daily history is *fetched* (yahoo source): callers
    that only need the strategy's warm-up window (the live book, the dashboard)
    pass their `min_history + display` need instead of downloading the full
    archive since START — a pure latency/bandwidth win; decisions are unchanged
    because the strategy trims to `min_history` anyway. Backtests omit it and
    keep the full history.
    """
    source = resolve_source(source, exchange)

    if source == "crypto":
        from . import crypto_data
        if synthetic:
            return crypto_data.synthetic_crypto_panel(symbols, timeframe=interval)
        return crypto_data.load_ohlcv(symbols, timeframe=interval,
                                      exchange=exchange or "binance")
    if source == "oanda":
        from . import oanda_data
        if synthetic:
            return oanda_data.synthetic_panel(symbols, timeframe=interval)
        return oanda_data.load_ohlcv(symbols, timeframe=interval)
    if source == "alpaca":
        from . import alpaca_data
        if synthetic:
            return alpaca_data.synthetic_panel(symbols, timeframe=interval)
        return alpaca_data.load_ohlcv(symbols, timeframe=interval)
    if source == "openbb":
        from . import openbb_data
        if synthetic:
            return openbb_data.synthetic_panel(symbols, timeframe=interval)
        return openbb_data.load_ohlcv(symbols, timeframe=interval)

    # default: yahoo (fx_data)
    daily = interval in ("1d", "B")
    if synthetic:
        if daily:
            return fx_data.synthetic_panel(symbols)
        return fx_data.synthetic_panel(symbols, start="2025-01-01",
                                       end="2025-04-01", freq=interval)
    start = cfg.START if daily else _intraday_start(interval)
    if min_bars and daily:
        # ~1.55 calendar days per business day, plus margin for holidays.
        bounded = (pd.Timestamp.utcnow().tz_localize(None)
                   - pd.Timedelta(days=int(min_bars * 1.6) + 20)).strftime("%Y-%m-%d")
        start = max(start, bounded)
    return fx_data.load_panel(symbols, start, interval=interval, use_cache=use_cache)
