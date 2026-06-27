"""Pluggable market-data providers with per-ticker routing and fallback.

The rest of the codebase speaks **Yahoo ticker conventions** (``AAPL``,
``BHP.AX``, ``AZN.L``, ``^GSPC``/``^AXJO``/``^FTSE``, ``AUDUSD=X``). Each provider
translates those to its own symbology and returns a DataFrame of adjusted-close
prices with the *Yahoo* tickers as columns, so callers never change.

`fetch_prices()` routes each requested ticker through a chain of providers
(primary first, then fallbacks), so a single mixed request — US names + LSE/ASX
names + FX — is served by whichever provider covers each symbol. Set the primary
with ``MOMENTUM_DATA_PROVIDER`` (``yfinance`` default; ``polygon``, ``stooq``);
yfinance + stooq are always appended as fallbacks.

Coverage at a glance:
- **yfinance** — everything (default); flaky / rate-limited.
- **stooq** — free, no key; US, LSE (.L) and ASX (.AX) EOD. Good redundancy.
- **polygon** — needs ``POLYGON_API_KEY``; US equities/ETFs, FX, US indices only
  (NO London/Australia listings — those fall through to yfinance/stooq).
- **tiingo** — needs ``TIINGO_API_KEY`` (free tier); US equities/ETFs, and
  **retains DELISTED tickers** (the free path to a survivorship-bias-free US
  backtest). Appended last so it prices delisted names yfinance/stooq drop.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.request

import pandas as pd

_UA = {"User-Agent": "Mozilla/5.0 (trading-algo data fetch)"}


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------
class PriceProvider:
    name = "base"

    def supports(self, ticker: str) -> bool:
        raise NotImplementedError

    def fetch(self, tickers: list[str], start: str, end: str | None) -> pd.DataFrame:
        """Return adjusted-close prices (index=date, columns=Yahoo tickers).
        May return a subset of `tickers` (whatever it could fetch)."""
        raise NotImplementedError


class YFinanceProvider(PriceProvider):
    name = "yfinance"

    def supports(self, ticker: str) -> bool:
        return True  # the catch-all

    def fetch(self, tickers, start, end):
        import yfinance as yf
        raw = None
        for attempt, wait in enumerate((5, 15, 30, 60)):
            try:
                raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                                  progress=False)["Close"]
                if raw is not None and len(raw):
                    break
            except Exception:
                if attempt == 3:
                    raise
            time.sleep(wait)
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(tickers[0])
        return raw.reindex(columns=tickers).dropna(how="all").dropna(axis=1, how="all")


class StooqProvider(PriceProvider):
    """Free EOD via stooq.com CSV (no key). One request per ticker."""
    name = "stooq"

    def supports(self, ticker: str) -> bool:
        return self._symbol(ticker) is not None

    @staticmethod
    def _symbol(ticker: str) -> str | None:
        if ticker.endswith("=X") or ticker.startswith("^"):
            return None                       # FX / indices: not handled here
        if ticker.endswith(".AX"):
            return ticker[:-3].lower() + ".au"
        if ticker.endswith(".L"):
            return ticker[:-2].lower() + ".uk"
        if "." not in ticker:                 # US listing
            return ticker.replace("-", ".").lower() + ".us"
        return None

    def fetch(self, tickers, start, end):
        cols = {}
        for t in tickers:
            sym = self._symbol(t)
            if not sym:
                continue
            try:
                raw = _http_get(f"https://stooq.com/q/d/l/?s={sym}&i=d")
                df = pd.read_csv(io.BytesIO(raw))
                if "Date" not in df or "Close" not in df or df.empty:
                    continue
                s = pd.Series(df["Close"].values,
                              index=pd.to_datetime(df["Date"]), name=t)
                cols[t] = s
            except Exception:
                continue
        if not cols:
            return pd.DataFrame()
        out = pd.DataFrame(cols).sort_index()
        return out.loc[start:end]


class PolygonProvider(PriceProvider):
    """Polygon.io daily aggregates (adjusted). Needs POLYGON_API_KEY.
    US equities/ETFs, FX (C:), and US indices (I:SPX) only."""
    name = "polygon"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("POLYGON_API_KEY")

    def supports(self, ticker: str) -> bool:
        return bool(self.api_key) and self._symbol(ticker) is not None

    @staticmethod
    def _symbol(ticker: str) -> str | None:
        if ticker.endswith(".AX") or ticker.endswith(".L"):
            return None                       # Polygon has no LSE/ASX listings
        if ticker.endswith("=X"):             # FX pair, e.g. AUDUSD=X -> C:AUDUSD
            return "C:" + ticker[:-2]
        if ticker == "^GSPC":
            return "I:SPX"                    # only the US index is available
        if ticker.startswith("^"):
            return None                       # ^AXJO / ^FTSE not on Polygon
        if "." not in ticker:                 # US equity/ETF (BRK-B -> BRK.B)
            return ticker.replace("-", ".")
        return None

    def fetch(self, tickers, start, end):
        end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
        cols = {}
        for t in tickers:
            sym = self._symbol(t)
            if not sym:
                continue
            url = (f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/"
                   f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={self.api_key}")
            try:
                data = json.loads(_http_get(url))
                results = data.get("results") or []
                if not results:
                    continue
                idx = pd.to_datetime([r["t"] for r in results], unit="ms")
                cols[t] = pd.Series([r["c"] for r in results], index=idx, name=t)
                time.sleep(0.02)              # be polite to the API
            except Exception:
                continue
        if not cols:
            return pd.DataFrame()
        return pd.DataFrame(cols).sort_index()


class TiingoProvider(PriceProvider):
    """Tiingo daily adjusted EOD. Needs ``TIINGO_API_KEY`` (free tier ~50
    symbols/hour). US equities/ETFs only — but, crucially, **it RETAINS DELISTED
    tickers**, which Yahoo purges. That makes it the free missing piece for a
    survivorship-bias-free US backtest: the point-in-time universe includes names
    that were later delisted, and Tiingo can still price them."""
    name = "tiingo"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TIINGO_API_KEY")

    def supports(self, ticker: str) -> bool:
        return bool(self.api_key) and self._symbol(ticker) is not None

    @staticmethod
    def _symbol(ticker: str) -> str | None:
        # US equities/ETFs only; no LSE/ASX/index/FX. BRK-B stays BRK-B.
        if ticker.startswith("^") or ticker.endswith("=X"):
            return None
        if ticker.endswith(".AX") or ticker.endswith(".L") or "." in ticker:
            return None
        return ticker.lower()

    def fetch(self, tickers, start, end):
        end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
        cols = {}
        for t in tickers:
            sym = self._symbol(t)
            if not sym:
                continue
            url = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
                   f"?startDate={start}&endDate={end}&format=csv&token={self.api_key}")
            try:
                df = pd.read_csv(io.BytesIO(_http_get(url)))
                if "date" not in df or "adjClose" not in df or df.empty:
                    continue
                cols[t] = pd.Series(df["adjClose"].values,
                                    index=pd.to_datetime(df["date"]), name=t)
                time.sleep(0.05)              # respect the free-tier rate limit
            except Exception:
                continue
        if not cols:
            return pd.DataFrame()
        return pd.DataFrame(cols).sort_index().loc[start:end]


_REGISTRY = {
    "yfinance": YFinanceProvider,
    "stooq": StooqProvider,
    "polygon": PolygonProvider,
    "tiingo": TiingoProvider,
}


def get_chain() -> list[PriceProvider]:
    """Provider chain: the configured primary, then yfinance + stooq fallbacks,
    plus tiingo last (only active when TIINGO_API_KEY is set — it's the fallback
    that can price *delisted* US names the others can't)."""
    primary = os.environ.get("MOMENTUM_DATA_PROVIDER", "yfinance").lower()
    order = [primary, "yfinance", "stooq"]
    if os.environ.get("TIINGO_API_KEY"):     # only useful with a key
        order.append("tiingo")
    seen, chain = set(), []
    for name in order:
        if name in _REGISTRY and name not in seen:
            seen.add(name)
            chain.append(_REGISTRY[name]())
    return chain


def fetch_prices(tickers: list[str], start: str, end: str | None,
                 chain: list[PriceProvider] | None = None) -> pd.DataFrame:
    """Fetch `tickers` by routing each through the provider chain (primary first,
    then fallbacks). Returns adjusted-close prices with Yahoo-ticker columns."""
    chain = chain or get_chain()
    out = pd.DataFrame()
    remaining = list(tickers)
    for provider in chain:
        if not remaining:
            break
        take = [t for t in remaining if provider.supports(t)]
        if not take:
            continue
        try:
            got = provider.fetch(take, start, end)
        except Exception:
            got = pd.DataFrame()
        if got is not None and not got.empty:
            out = got if out.empty else out.join(got, how="outer")
            remaining = [t for t in remaining if t not in out.columns]
    return out.reindex(columns=[t for t in tickers if t in out.columns])
