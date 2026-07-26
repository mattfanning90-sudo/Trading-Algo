"""Frankfurter — keyless ECB reference rates. Real FX data, **close-only**.

Frankfurter (https://frankfurter.dev) is a free, open-source, **keyless** API that
serves the European Central Bank's daily reference rates. That is the point of
wiring it in: OANDA gives real FX but needs `OANDA_API_TOKEN`; Frankfurter needs
nothing, so a live book can have *real* FX prices with zero credential setup.

What you actually get — and what you do NOT
-------------------------------------------
The ECB publishes **one reference rate per currency per working day**, fixed
around 14:15 CET and published ~16:00 CET. So a bar here is:

* a genuine, citable market observation (the ECB concertation fixing), and
* a **single price** — there is no open, no high, no low, and no intraday.

This module therefore returns frames whose ``open``/``high``/``low`` all equal
``close``. That is not a fabricated candle: it is the honest statement that the
bar has exactly one observed price. Such bars are **not signal-grade** — the
range indicators compute a plausible-looking but different statistic on them —
so `bar_quality.py` owns the detection and the refusal (with the measurements,
and `docs/DATA_FEEDS.md` has the strategy consequences). Frames are tagged with
`PANEL_ATTRS` as corroboration; detection is structural and needs no tag.

Also out of scope, by construction:

* **No crypto.** ECB publishes fiat only — BTCUSD/ETHUSD/SOLUSD must route to
  `crypto_data` (ccxt). Asking for them here raises.
* **No intraday.** Daily fixings only — a 60m book cannot be fed from here.
  Asking for any non-daily timeframe raises.
* **Fiat outside the ECB set** (e.g. RUB, suspended in 2022) raises.

Lookahead note: the fixing dated D is only knowable after ~16:00 CET on D. The
system's contract (signal at t, execute t+1) is satisfied, but do not treat a
same-day bar as available at that day's open.

HTTP is stdlib `urllib.request`: `requests` is **not** a declared dependency of
this project (it only arrives transitively via yfinance), so nothing here
imports it. Every network call goes through one injectable `fetch` callable, so
the whole adapter is testable offline; `synthetic_panel()` is the no-network
twin and reproduces the close-only degeneracy on purpose.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

import pandas as pd

from . import fx_data
from .pairs import PAIRS, get_pair

# The user-supplied endpoint. Overridable per call (`base_url=`) or per process
# (FRANKFURTER_URL) — see `_build_url` for the v1/v2 URL-shape difference.
DEFAULT_URL = "https://api.frankfurter.dev/v2/rates"

# ECB daily reference-rate currencies (the euro plus the ~30 quoted against it).
# The live authority is the API's `/currencies` endpoint; this list is pinned so
# an unsupported currency fails offline, loudly, instead of on the first call.
# Deliberately absent: HRK (withdrawn when Croatia joined the euro, 2023) and
# RUB (ECB suspended publication in March 2022).
ECB_CURRENCIES: frozenset[str] = frozenset({
    "EUR", "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR",
    "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD",
    "ZAR",
})

# Natural universe: the seven FX majors (every leg is an ECB currency). Crosses
# work too — `supported()` is the general test.
FRANKFURTER_UNIVERSE: list[str] = list(PAIRS)

# The only timeframes this source can serve. One fixing per working day.
DAILY_TIMEFRAMES: frozenset[str] = frozenset({"1d", "B"})

# Metadata stamped onto every returned frame's `.attrs` — provenance, plus the
# close-only declaration `bar_quality` treats as corroboration for its own
# structural check (`.attrs` do not survive every round-trip, so they can add the
# verdict but are never required to reach it).
PANEL_ATTRS: dict[str, object] = {
    "source": "frankfurter",
    "close_only": True,
    "bar": "ecb_daily_reference_rate",
    "provenance": "ECB daily reference rates (~16:00 CET fixing) via frankfurter.dev",
}

# The offline twin must NOT inherit the live provenance. It fabricates prices, and
# a frame asserting it holds an "ECB reference fixing" it never received is the
# fabrication invariant #5 forbids — the more so because this string is meant to
# be *displayed* (docs/DATA_FEEDS.md "Charts" has the FX dashboard printing it
# beside the line it draws). Same close-only SHAPE, honest origin.
SYNTHETIC_ATTRS: dict[str, object] = {
    **PANEL_ATTRS,
    "synthetic": True,
    "bar": "synthetic_close_only",
    "provenance": "SYNTHETIC close-only bars — offline pipeline test only, "
                  "NOT ECB data and never performance",
}

# ECB rates are published against the euro; requesting base=EUR and deriving
# every pair locally means (a) ONE request for the whole universe and (b) crosses
# that are exactly consistent with the majors (no server-side rounding drift).
VIA_CURRENCY = "EUR"

_FIELDS = ["open", "high", "low", "close"]
_TIMEOUT = 20.0


class FrankfurterError(RuntimeError):
    """Anything this source cannot serve or cannot parse — always raised, never
    swallowed into an empty/forward-filled panel."""


# ---------------------------------------------------------------------------
# What this source can and cannot serve
# ---------------------------------------------------------------------------
def is_ecb_currency(code: str) -> bool:
    """True if `code` is in the ECB daily reference-rate set."""
    return code.upper() in ECB_CURRENCIES


def unsupported_reason(symbol: str) -> str | None:
    """Why `symbol` can't come from here, or None if it can."""
    try:
        pair = get_pair(symbol)
    except KeyError as e:
        return str(e)
    if pair.asset_class != "fx":
        return (f"{symbol} is {pair.asset_class}, not fiat FX — the ECB publishes "
                f"fiat reference rates only"
                + (" (route crypto to crypto_data/ccxt)"
                   if pair.asset_class == "crypto" else ""))
    missing = [c for c in (pair.base, pair.quote) if not is_ecb_currency(c)]
    if missing:
        return (f"{symbol}: {', '.join(missing)} is outside the ECB reference set "
                f"({len(ECB_CURRENCIES)} currencies)")
    return None


def supported(symbol: str) -> bool:
    """True if Frankfurter can serve `symbol` (fiat pair, both legs ECB)."""
    return unsupported_reason(symbol) is None


def _mark(panel: dict[str, pd.DataFrame],
          attrs: dict[str, object] | None = None) -> dict[str, pd.DataFrame]:
    """Stamp the close-only provenance onto every frame.

    `attrs` selects which provenance: live `PANEL_ATTRS` or `SYNTHETIC_ATTRS`.
    """
    for df in panel.values():
        df.attrs.update(PANEL_ATTRS if attrs is None else attrs)
    return panel


# ---------------------------------------------------------------------------
# HTTP (stdlib only) + URL construction
# ---------------------------------------------------------------------------
def _http_get(url: str) -> str:
    """GET `url` and return the body. Any failure becomes a FrankfurterError.

    Deliberately the *only* place this module touches the network, and it is
    injectable (`fetch=`) so no test ever makes a real call.
    """
    # Pin the scheme: `urlopen` will happily follow file:// or ftp:// if a bad
    # FRANKFURTER_URL ever reaches here, and market data must come off the wire.
    if urllib.parse.urlparse(url).scheme != "https":
        raise FrankfurterError(f"refusing a non-https market-data URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "trading-algo/fx"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310 - scheme pinned to https above
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise FrankfurterError(
            f"frankfurter HTTP {e.code} for {url} — if the /v2 endpoint shape has "
            f"moved, pin the documented v1 form with FRANKFURTER_URL="
            f"https://api.frankfurter.dev/v1 or base_url=") from e
    except Exception as e:                       # URLError, timeout, DNS, TLS …
        raise FrankfurterError(f"frankfurter request failed for {url}: {e!r}") from e


def _build_url(base_url: str, base_ccy: str, currencies: list[str],
               start: str, end: str) -> str:
    """Build the rates URL for one request covering ALL currencies.

    Two documented URL shapes exist and they differ only in where the date range
    goes: v1 puts it in the path (`/v1/2024-01-01..2024-01-31`), while the v2
    `/rates` endpoint takes query parameters. We build whichever matches
    `base_url`, so a caller can pin either without editing code.
    """
    root = base_url.rstrip("/")
    query: dict[str, str] = {"base": base_ccy}
    wanted = sorted(c for c in currencies if c != base_ccy)
    if wanted:
        query["symbols"] = ",".join(wanted)
    if "/v1" in root:
        return f"{root}/{start}..{end}?{urllib.parse.urlencode(query)}"
    query["start_date"], query["end_date"] = start, end
    return f"{root}?{urllib.parse.urlencode(query)}"


# ---------------------------------------------------------------------------
# Parsing — one normaliser for both documented envelopes
# ---------------------------------------------------------------------------
def _parse_rates(payload: str | bytes) -> tuple[str, dict[str, dict[str, float]]]:
    """Normalise a Frankfurter response to ``(base, {date: {ccy: rate}})``.

    The documented time-series body is
    ``{"amount":1,"base":"EUR","rates":{"2024-01-02":{"USD":1.09,…},…}}``; both
    API versions share it and differ only in the URL shape (see `_build_url`).
    Any other shape raises rather than being coerced into an empty/1-bar panel.

    `amount` is honoured: the API can quote per N units, and silently treating
    ``amount=100`` as 1 would scale every price by 100.
    """
    try:
        body = json.loads(payload)
    except Exception as e:
        snippet = (payload if isinstance(payload, str) else payload.decode("utf-8", "replace"))[:200]
        raise FrankfurterError(f"frankfurter returned non-JSON: {snippet!r}") from e
    if not isinstance(body, dict) or "rates" not in body:
        raise FrankfurterError(
            f"unexpected frankfurter payload — expected a dict with a 'rates' key, "
            f"got keys {sorted(body) if isinstance(body, dict) else type(body).__name__}. "
            f"The endpoint shape may have changed; see DEFAULT_URL.")
    raw = body["rates"]
    if not isinstance(raw, dict) or not raw:
        raise FrankfurterError("frankfurter returned no rates for the requested "
                               "range/currencies (empty 'rates').")

    amount = float(body.get("amount", 1.0) or 1.0)
    base = str(body.get("base", "") or "")

    # Every request here asks for a date RANGE, so the body must be the time
    # series `date -> {ccy: rate}`. A flat `ccy -> rate` body is the single-day
    # /latest envelope, i.e. the server ignored the range we asked for — accepting
    # it would quietly return one bar where a warm-up window was requested.
    first = next(iter(raw.values()))
    if not isinstance(first, dict):
        raise FrankfurterError(
            f"unexpected frankfurter 'rates' shape — values are "
            f"{type(first).__name__}, expected a date -> rates object. A flat "
            f"currency -> rate body means the endpoint ignored the requested date "
            f"range (a /latest response); pin the documented form with "
            f"FRANKFURTER_URL=https://api.frankfurter.dev/v1.")

    out: dict[str, dict[str, float]] = {}
    for day, rates in raw.items():
        if not isinstance(rates, dict):
            raise FrankfurterError(f"frankfurter rates for {day!r} is "
                                   f"{type(rates).__name__}, expected an object.")
        row: dict[str, float] = {}
        for ccy, value in rates.items():
            # `float(True) == 1.0` — a bool must not slip through as a rate.
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise FrankfurterError(f"frankfurter rate {ccy}={value!r} on {day} "
                                       f"is not a number.")
            try:
                row[str(ccy).upper()] = float(value) / amount
            except (TypeError, ValueError) as e:
                raise FrankfurterError(f"frankfurter rate {ccy}={value!r} on {day} "
                                       f"is not a number.") from e
        out[str(day)] = row
    return base, out


def _rate_frame(base_ccy: str, parsed: dict[str, dict[str, float]]) -> pd.DataFrame:
    """``{date: {ccy: rate}}`` -> DataFrame of *units of ccy per 1 base_ccy*.

    The request base is injected as an exact 1.0 column so pairs that involve it
    (EURUSD when we requested base=EUR) need no special case downstream.
    """
    df = pd.DataFrame.from_dict(parsed, orient="index")
    try:
        df.index = pd.to_datetime(df.index)
    except Exception as e:                     # date keys that aren't dates
        raise FrankfurterError(f"frankfurter returned unparseable date keys "
                               f"{list(df.index)[:5]}: {e!r}") from e
    df = df.sort_index()
    df[base_ccy] = 1.0
    return df


def _derive_pair(symbol: str, rates: pd.DataFrame) -> pd.Series:
    """Price series for `symbol` = quote units per 1 base unit.

    With `rates[X]` = units of X per 1 request-base (EUR), the pair price is

        price(BASE/QUOTE) = rates[QUOTE] / rates[BASE]

    Direction check (EUR base, 2024-01-02: USD 1.0956, JPY 155.15, AUD 1.6216):
      * EURUSD = 1.0956 / 1.0     = 1.0956  USD per EUR   ✔ (~1.1, not ~0.9)
      * USDJPY = 155.15 / 1.0956  = 141.61  JPY per USD   ✔ (~142, not ~0.007)
      * AUDUSD = 1.0956 / 1.6216  = 0.6756  USD per AUD   ✔ (<1, not ~1.48)
    Inverting this would silently negate every P&L downstream, so it is pinned by
    a worked example in tests/test_frankfurter_data.py.
    """
    pair = get_pair(symbol)
    for ccy in (pair.base, pair.quote):
        if ccy not in rates.columns:
            raise FrankfurterError(
                f"frankfurter response has no {ccy} column, needed for {symbol} "
                f"(returned: {sorted(rates.columns)}).")
    # Drop days where either leg is missing rather than forward-filling a stale
    # fixing: a gap in the ECB calendar is real information, not a bad row.
    both = rates[[pair.base, pair.quote]].dropna()
    return both[pair.quote] / both[pair.base]


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------
def load_ohlcv(symbols: list[str], timeframe: str = "1d", limit: int = 750,
               start: str | None = None, end: str | None = None,
               base_url: str | None = None,
               fetch: Callable[[str], str] | None = None,
               ) -> dict[str, pd.DataFrame]:
    """Daily ECB-fixing panel for `symbols`, aligned like every other source.

    Returns the usual ``dict[symbol -> DataFrame[open, high, low, close]]`` — but
    **open == high == low == close** on every bar, because the ECB publishes one
    rate per working day. Each frame carries `PANEL_ATTRS`; anything that reads
    high/low refuses these bars (`bar_quality`).

    `limit` bounds the number of bars (the warm-up window) when `start` is not
    given; pass `start`/`end` (``YYYY-MM-DD``) for an explicit historical range,
    which is returned in full (`limit` does not truncate it).
    `fetch` injects the transport (url -> body) for offline testing.

    Raises `FrankfurterError` for a non-daily `timeframe`, for any symbol this
    source cannot serve (crypto, equities, non-ECB currencies), and for an
    unparseable/empty response. It never returns a silently empty panel.
    """
    if timeframe not in DAILY_TIMEFRAMES:
        raise FrankfurterError(
            f"frankfurter is daily-only (ECB publishes one reference rate per "
            f"working day); timeframe {timeframe!r} is not available. Known: "
            f"{sorted(DAILY_TIMEFRAMES)}. Use crypto/oanda/alpaca for intraday.")
    if not symbols:
        raise FrankfurterError("frankfurter: no symbols requested.")

    bad = {s: unsupported_reason(s) for s in symbols if not supported(s)}
    if bad:
        detail = "; ".join(f"{s}: {why}" for s, why in bad.items())
        raise FrankfurterError(
            f"frankfurter cannot serve {', '.join(bad)} — {detail}. Route these "
            f"symbols to another source (per-symbol routing) rather than asking "
            f"one provider for the whole book.")

    start_s, end_s = _date_range(start, end, limit)
    currencies = sorted({c for s in symbols
                         for c in (get_pair(s).base, get_pair(s).quote)})
    url = _build_url(base_url or os.environ.get("FRANKFURTER_URL", DEFAULT_URL),
                     VIA_CURRENCY, currencies, start_s, end_s)

    body = (fetch or _http_get)(url)
    got_base, parsed = _parse_rates(body)
    if got_base and got_base.upper() != VIA_CURRENCY:
        # A server-side rebase we didn't ask for would invert/scale every pair.
        raise FrankfurterError(f"frankfurter answered base={got_base!r}, expected "
                               f"{VIA_CURRENCY!r} — refusing to derive pairs.")

    rates = _rate_frame(VIA_CURRENCY, parsed)
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        close = _derive_pair(sym, rates)
        if close.empty:
            raise FrankfurterError(f"frankfurter returned no usable rates for {sym} "
                                   f"between {start_s} and {end_s}.")
        # `limit` caps the warm-up window only. An EXPLICIT start date is a
        # deliberate range (a backtest); silently tailing it to 750 bars would
        # quietly shorten someone's history.
        if start is None:
            close = close.tail(int(limit))
        # One fixing per day: the bar's only observed price IS its open, high,
        # low and close. Flagged (not disguised) via PANEL_ATTRS — see `_mark`.
        frames[sym] = pd.DataFrame({f: close for f in _FIELDS})
    # `_align` is a no-op here (all pairs share the one ECB publication calendar)
    # but keeps the panel contract identical to every other source.
    return _mark(fx_data._align(frames))


def _date_range(start: str | None, end: str | None, limit: int) -> tuple[str, str]:
    """Resolve the fetch window; `limit` bars back from `end` when no `start`."""
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.now("UTC").tz_localize(None)
    if start:
        start_ts = pd.Timestamp(start)
    else:
        # ~1.55 calendar days per working day, plus margin for TARGET holidays.
        start_ts = end_ts - pd.Timedelta(days=int(max(int(limit), 1) * 1.6) + 20)
    if start_ts > end_ts:
        raise FrankfurterError(f"frankfurter: start {start_ts.date()} is after "
                               f"end {end_ts.date()}.")
    return start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Offline twin
# ---------------------------------------------------------------------------
def synthetic_panel(symbols: list[str], timeframe: str = "1d", days: int = 5,
                    seed: int | None = None) -> dict[str, pd.DataFrame]:
    """Offline synthetic panel (pipeline testing only — never performance).

    A *faithful* twin: it applies the same symbol/timeframe validation and
    flattens open/high/low onto close, so an offline run reproduces the
    close-only degeneracy instead of passing on a true range that live data
    would never provide. `days` is accepted for signature parity with the other
    sources and ignored — this source has no intraday mode.

    Faithful in shape, honest about origin: the frames carry `SYNTHETIC_ATTRS`,
    never the live ECB provenance (invariant #5).
    """
    if timeframe not in DAILY_TIMEFRAMES:
        raise FrankfurterError(f"frankfurter is daily-only; timeframe "
                               f"{timeframe!r} is not available (synthetic twin "
                               f"mirrors the live constraint).")
    bad = [s for s in symbols if not supported(s)]
    if bad:
        raise FrankfurterError(f"frankfurter cannot serve {', '.join(bad)} — "
                               f"{unsupported_reason(bad[0])}.")
    panel = fx_data.synthetic_panel(symbols, seed=seed)
    out = {sym: pd.DataFrame({f: df["close"] for f in _FIELDS})
           for sym, df in panel.items()}
    return _mark(out, SYNTHETIC_ATTRS)
