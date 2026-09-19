"""Tiingo daily prices — the F14 secondary price source.

Yahoo is the primary feed and it does fail: a 403 or an empty frame takes a
whole sleeve to cash on a day nothing was actually wrong. `data._try_fallback`
has existed since F14 to cover exactly that, and nothing was ever registered in
it, so it always returned None. A `TIINGO_API_SECRET` has existed just as long
with no code referencing it. These two gaps fit each other.

Self-registers on import; `data._try_fallback` imports this module on demand
when `config.DATA_FALLBACK_SOURCE == "tiingo"`, so selecting the source is all
a caller has to do.

HONEST LIMITS — read before relying on this:
  * **US listings only.** Tiingo's daily endpoint does not serve the `.AX` or
    `.L` suffixed tickers the ASX and FTSE sleeves trade, so this is redundancy
    for the US sleeve and nothing else. It returns None for a ticker it cannot
    serve rather than inventing a column.
  * Prices are `adjClose` (split- and dividend-adjusted), matching the
    `auto_adjust=True` Yahoo primary.
  * Fallback data still flows through the F7 data-quality gate downstream, so a
    poor secondary cannot slip bad prints into a rebalance.
  * No token in the environment = a silent no-op, never an error: a laptop with
    no credential must still run.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pandas as pd

BASE = "https://api.tiingo.com/tiingo/daily"
TIMEOUT_SECONDS = 20


def _token() -> str | None:
    return os.environ.get("TIINGO_API_SECRET") or os.environ.get("TIINGO_API_KEY")


def _series(ticker: str, start: str, end: str | None, token: str) -> pd.Series | None:
    params = {"startDate": start, "token": token, "format": "json"}
    if end:
        params["endDate"] = end
    url = f"{BASE}/{urllib.parse.quote(ticker)}/prices?{urllib.parse.urlencode(params)}"
    # Pin the scheme: `urlopen` will happily follow file:// or ftp://, and market
    # data must come off the wire (same guard as frankfurter_data._http_get).
    if urllib.parse.urlparse(url).scheme != "https":
        return None
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # nosec B310 - scheme pinned to https above
            rows = json.loads(resp.read().decode())
    except Exception:
        return None                      # a dead secondary must not raise
    if not rows:
        return None
    idx, vals = [], []
    for r in rows:
        px = r.get("adjClose", r.get("close"))
        if px is None:
            continue
        idx.append(pd.Timestamp(str(r["date"])[:10]))
        vals.append(float(px))
    if not vals:
        return None
    return pd.Series(vals, index=pd.DatetimeIndex(idx)).sort_index()


def load(tickers: list[str], start: str, end: str | None = None):
    """Adjusted closes for whatever Tiingo can serve, or None if nothing/no token."""
    token = _token()
    if not token:
        return None
    cols = {}
    for t in tickers:
        s = _series(t, start, end, token)
        if s is not None:
            cols[t] = s
    if not cols:
        return None
    return pd.DataFrame(cols).sort_index()


def _register() -> None:
    from . import data
    data.register_fallback("tiingo", load)


_register()
