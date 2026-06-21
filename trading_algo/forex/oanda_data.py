"""OANDA v20 market data — real-time FX from a free practice account.

OANDA gives you **real-time streaming FX prices on a free demo/practice account**
— which is exactly the gate that blocks live intraday FX on Yahoo (delayed,
history-limited). It isn't open-source *data* (no FX market is — there's no
central tape), but the access is free and the Python wrapper (`oandapyV20`) is
open-source, so it's the realistic free route for live intraday FX.

Credentials come from the environment (never the repo):
    OANDA_API_TOKEN   — your practice-account API token
    OANDA_ENV         — "practice" (default) or "live"

`oandapyV20` is imported lazily (optional dependency: `pip install oandapyV20`),
so the package still works offline; tests use the synthetic generator.

See docs/DATA_FEEDS.md.
"""
from __future__ import annotations

import os

import pandas as pd

from . import fx_data
from .pairs import PAIRS, get_pair

# FX majors are the natural OANDA universe (it also offers metals/CFDs).
OANDA_UNIVERSE = list(PAIRS)

# Yahoo-style interval -> OANDA granularity.
_GRAN = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
         "1h": "H1", "60m": "H1", "4h": "H4", "1d": "D"}

_FIELDS = ["open", "high", "low", "close"]


def instrument(symbol: str) -> str:
    """Canonical id -> OANDA instrument, e.g. EURUSD -> 'EUR_USD'."""
    p = get_pair(symbol)
    return f"{p.base}_{p.quote}"


def _client():
    try:
        from oandapyV20 import API
    except ImportError as e:
        raise SystemExit("oandapyV20 not installed — run `pip install oandapyV20` "
                         "for live OANDA FX, or use --synthetic offline.") from e
    token = os.environ.get("OANDA_API_TOKEN")
    if not token:
        raise SystemExit("set OANDA_API_TOKEN (a free practice-account token) to "
                         "use the oanda source; see docs/DATA_FEEDS.md.")
    env = os.environ.get("OANDA_ENV", "practice")
    return API(access_token=token, environment=env)


def load_ohlcv(symbols: list[str], timeframe: str = "1h", limit: int = 500,
               ) -> dict[str, pd.DataFrame]:
    """OHLC panel from OANDA (mid prices), aligned like every other source."""
    from oandapyV20.endpoints.instruments import InstrumentsCandles
    api = _client()
    gran = _GRAN.get(timeframe, "H1")
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        params = {"granularity": gran, "count": int(limit), "price": "M"}
        req = InstrumentsCandles(instrument=instrument(sym), params=params)
        try:
            api.request(req)
            candles = [c for c in req.response.get("candles", []) if c.get("complete")]
        except Exception as exc:                       # one bad symbol shouldn't kill the run
            print(f"  [oanda] {sym}: {exc!r}")
            continue
        if not candles:
            continue
        idx = pd.to_datetime([c["time"] for c in candles])
        mid = [c["mid"] for c in candles]
        df = pd.DataFrame({
            "open": [float(m["o"]) for m in mid],
            "high": [float(m["h"]) for m in mid],
            "low": [float(m["l"]) for m in mid],
            "close": [float(m["c"]) for m in mid],
        }, index=idx)
        frames[sym] = df[_FIELDS]
    return fx_data._align(frames)


def synthetic_panel(symbols: list[str], timeframe: str = "1d", days: int = 5,
                    seed: int | None = None) -> dict[str, pd.DataFrame]:
    """Offline synthetic FX panel (pipeline testing only)."""
    if timeframe in ("1d", "B"):
        return fx_data.synthetic_panel(symbols)
    return fx_data.synthetic_recent(symbols, timeframe=timeframe, days=days, seed=seed)
