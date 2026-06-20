"""Crypto market data via ccxt — the retail "great equalizer".

Unlike FX/equities, crypto exchanges hand retail **institutional-grade data for
free**: 1-minute (and finer) OHLCV, trades, order books, and perpetual **funding
rates** straight from the public API — no membership, no vendor fees. That powers
the high-frequency-*capable* crypto path (second-to-minute scale).

Honesty (see docs/CRYPTO_HF.md): this is fast, high-turnover systematic trading,
NOT microsecond HFT competing with Wintermute/Jump — you cannot win the pure
latency race. The realistic, accessible edges are (a) minute-scale signals on
rich free data and (b) **funding-rate / cash-and-carry** harvesting.

`ccxt` is imported lazily (an optional dependency: `pip install ccxt`), so the
package still works offline; tests use the synthetic generator.
"""
from __future__ import annotations

import pandas as pd

from . import fx_data

# Our canonical symbol -> exchange spot / perpetual symbols.
SPOT = {"BTCUSD": "BTC/USDT", "ETHUSD": "ETH/USDT", "SOLUSD": "SOL/USDT"}
PERP = {"BTCUSD": "BTC/USDT:USDT", "ETHUSD": "ETH/USDT:USDT", "SOLUSD": "SOL/USDT:USDT"}
CRYPTO_UNIVERSE = list(SPOT)

_FIELDS = ["open", "high", "low", "close"]


def _exchange(name: str):
    try:
        import ccxt
    except ImportError as e:
        raise SystemExit("ccxt not installed — run `pip install ccxt` for live crypto "
                         "data, or use --synthetic offline.") from e
    return getattr(ccxt, name)({"enableRateLimit": True})


def load_ohlcv(symbols: list[str], timeframe: str = "1m", limit: int = 1000,
               exchange: str = "binance") -> dict[str, pd.DataFrame]:
    """OHLCV panel from a crypto exchange (public data, no API key needed).

    `timeframe` is a ccxt interval ("1m", "5m", "1h" …). `limit` is bars per pair.
    Returns the same panel shape as `fx_data` (aligned, ffilled).
    """
    ex = _exchange(exchange)
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        market = SPOT.get(sym, sym)
        try:
            raw = ex.fetch_ohlcv(market, timeframe=timeframe, limit=limit)
        except Exception as exc:                       # one bad symbol shouldn't kill the run
            print(f"  [crypto] {sym}: {exc!r}")
            continue
        if not raw:
            continue
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
        df.index = pd.to_datetime(df["ts"], unit="ms")
        frames[sym] = df[_FIELDS]
    return fx_data._align(frames)


def fetch_funding(symbols: list[str], exchange: str = "binance") -> dict[str, float]:
    """Latest perpetual funding rate per symbol (the cash-and-carry signal).

    Positive funding => longs pay shorts => a delta-neutral (long spot / short
    perp) book *earns* it. Best-effort; unsupported symbols are skipped.
    """
    ex = _exchange(exchange)
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            fr = ex.fetch_funding_rate(PERP.get(sym, sym))
            rate = fr.get("fundingRate")
            if rate is not None:
                out[sym] = float(rate)
        except Exception as exc:
            print(f"  [crypto] funding {sym}: {exc!r}")
    return out


def synthetic_crypto_panel(symbols: list[str], timeframe: str = "1m",
                           days: int = 3, seed: int | None = None
                           ) -> dict[str, pd.DataFrame]:
    """Offline synthetic minute-bar crypto panel (pipeline testing only)."""
    end = pd.Timestamp("2025-01-04")
    start = (end - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return fx_data.synthetic_panel(symbols, start=start, end=end.strftime("%Y-%m-%d"),
                                   seed=seed, freq=timeframe)
