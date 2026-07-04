"""Economic-calendar 'news' correlation for the daily summary.

The honest version of "what news moved it": we only surface a catalyst when a
**real, high-impact scheduled economic release** (CPI, a rate decision, jobs, GDP …)
landed on a currency you **actually traded** that day. No event → nothing shown
("only if there is one"), and it's always framed as *correlation, not proven cause*.

This is the least-noisy news signal for FX: scheduled releases are verifiable and
currency-tagged, unlike free-text headlines. It is **best-effort and graceful** —
with no API key, no network, or no matching event it returns ``[]`` and the daily
summary simply omits the section. Set ``NEWS_API_KEY`` (a Financial Modeling Prep
key, or override the provider) to enable it; see docs/DATA_FEEDS.md.
"""
from __future__ import annotations

import copy
import functools
import os

# Currencies an economic calendar covers (crypto has no scheduled releases).
FIAT = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}

_FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"


def _key(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("NEWS_API_KEY") or os.environ.get("FMP_API_KEY")


def _get_calendar(start: str, end: str, key: str, timeout: float) -> tuple:
    """GET the FMP economic_calendar for the `[start, end]` range with the
    graceful never-raise contract — any failure is ()."""
    try:
        import requests
        resp = requests.get(_FMP_URL, params={"from": start, "to": end, "apikey": key},
                            timeout=timeout)
        rows = resp.json() if resp.ok else []
    except Exception:
        return ()
    return tuple(rows or [])


@functools.lru_cache(maxsize=8)
def _fetch_rows_cached(date: str, key: str, timeout: float) -> tuple:
    """The single-day provider fetch (memoised per date/key) used by the
    daily-summary callers. Same never-raise contract as `_get_calendar`.
    """
    return _get_calendar(date, date, key, timeout)


@functools.lru_cache(maxsize=16)
def _fetch_range_cached(start: str, end: str, key: str, timeout: float) -> tuple:
    """Ranged provider fetch (memoised per range/key). FMP's endpoint takes a
    from/to range, so a whole window is ONE HTTP call, not one-per-day —
    keeping the hourly CI well clear of the free tier's 250 calls/day.
    """
    return _get_calendar(start, end, key, timeout)


def _fetch_rows(date: str, key: str, timeout: float = 8.0) -> list:
    """Shared FMP fetch for `calendar_feed` and `economic_events` — the provider
    endpoint/params and the graceful-[] failure contract are written ONCE here.
    Deep-copies on the way out so callers can't mutate the cache."""
    return [copy.deepcopy(e) for e in _fetch_rows_cached(date, key, timeout)]


def calendar_range(currencies, start: str, end: str, *, key: str | None = None,
                   high_only: bool = True, timeout: float = 8.0) -> list[dict]:
    """High-impact (or medium+, if ``high_only=False``) scheduled releases for
    `currencies` across the ``[start, end]`` date range, dated — the raw material
    for a news feed and for marking *when* a catalyst hit on a price chart.

    Returns ``[{date, time, currency, event, impact, actual, estimate, previous}]``
    sorted by timestamp, or ``[]`` with no key / no network / nothing relevant.
    Never raises. One provider call for the whole window (see `_fetch_range_cached`).
    """
    k = _key(key)
    want = {c.upper() for c in (currencies or []) if c.upper() in FIAT}
    if not k or not want or not start or not end:
        return []
    ok_imp = ("high", "3", "holiday") if high_only else \
             ("high", "medium", "3", "2", "holiday")
    out: list[dict] = []
    for e in (copy.deepcopy(x) for x in _fetch_range_cached(start, end, k, timeout)):
        try:
            cur = str(e.get("currency") or e.get("country") or "").upper()
            imp = str(e.get("impact") or "").strip().lower()
            if cur not in want or imp not in ok_imp:
                continue
            ts = str(e.get("date") or "")
            actual, estimate, previous = e.get("actual"), e.get("estimate"), e.get("previous")
            bias = predicted_impact(e.get("event"), cur, actual, estimate, previous)
            out.append({"date": ts[:10], "time": ts[11:16] if len(ts) >= 16 else "",
                        "currency": cur, "event": e.get("event"),
                        "impact": "high" if _is_high(e.get("impact")) else "medium",
                        "actual": actual, "estimate": estimate, "previous": previous,
                        "bias": bias["bias"], "bias_text": bias["text"]})
        except Exception:
            continue
    out.sort(key=lambda x: (x["date"], x["time"] or "99:99"))
    return out


def _is_high(impact) -> bool:
    return str(impact or "").strip().lower() in ("high", "3", "holiday")


# --- predicted currency impact --------------------------------------------
# Indicator polarity: does a HIGHER number strengthen (+1) or weaken (−1) the
# currency? Growth/inflation/rates prints are hawkish when they beat; labour
# slack (unemployment, jobless claims) is the inverse. Order matters —
# "unemployment" is checked before the generic "employment"/growth terms.
_NEG_KEYS = ("unemployment", "jobless", "initial claims", "continuing claims")
_POS_KEYS = ("cpi", "inflation", "ppi", "gdp", "retail", "payroll", "nonfarm",
             "employment change", "pmi", "ism", "confidence", "sentiment",
             "durable", "industrial production", "interest rate", "rate decision",
             "cash rate", "bank rate", "wage", "earnings", "housing starts",
             "building permits", "current account", "factory orders")
_WATCH_KEYS = ("speech", "minutes", "testimony", "press conference", "statement",
               "meeting", "holiday", "member", "governor", "chair")


def _polarity(event: str):
    """+1 (higher = stronger currency), −1 (higher = weaker), 0 (speech/watch),
    or None (unclassified indicator)."""
    n = str(event or "").lower()
    if any(k in n for k in _WATCH_KEYS):
        return 0
    if any(k in n for k in _NEG_KEYS):
        return -1
    if any(k in n for k in _POS_KEYS):
        return 1
    return None


def _num(x):
    """Parse a calendar figure ('3.1%', '180K', '1.2M', '-4,500') to a float,
    or None."""
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace("%", "")
    mult = 1.0
    if s[-1:].upper() in ("K", "M", "B"):
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1].upper()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def predicted_impact(event: str, currency: str, actual=None, estimate=None,
                     previous=None) -> dict:
    """A plain read of how a release likely affects its currency.

    Returns ``{bias, text}`` where bias ∈ {positive, negative, neutral, watch,
    unknown} relative to `currency`:
      * actual vs estimate known → the REALISED read (a beat on a hawkish print
        is currency-positive; on a labour-slack print, negative);
      * only a forecast (upcoming) → the CONVENTION (which way a beat pushes);
      * a speech / minutes → ``watch`` ("watch tone");
      * an unclassified indicator → ``unknown``.
    """
    ccy = str(currency or "").upper()
    pol = _polarity(event)
    if pol == 0:
        return {"bias": "watch", "text": "WATCH TONE"}
    a, e = _num(actual), _num(estimate)
    if pol is not None and a is not None and e is not None:
        s = 0 if a == e else (1 if a > e else -1)
        d = pol * s
        if d > 0:
            return {"bias": "positive", "text": f"{ccy} POSITIVE"}
        if d < 0:
            return {"bias": "negative", "text": f"{ccy} NEGATIVE"}
        return {"bias": "neutral", "text": "INLINE"}
    if pol is not None and (e is not None or _num(previous) is not None):
        return {"bias": "watch",
                "text": f"{'HIGHER' if pol > 0 else 'LOWER'} → {ccy}+"}
    return {"bias": "unknown", "text": ""}


def calendar_feed(currencies, date: str, *, key: str | None = None,
                  timeout: float = 8.0) -> list[dict]:
    """The daily currency news feed: ALL medium/high-impact scheduled releases
    for `currencies` on `date`, with times — what's on the tape today for the
    currencies the book trades. Same graceful contract as `economic_events`:
    ``[]`` with no key / no network / nothing relevant; never raises.
    """
    k = _key(key)
    want = {c.upper() for c in (currencies or []) if c.upper() in FIAT}
    if not k or not want or not date:
        return []
    out: list[dict] = []
    for e in _fetch_rows(date, k, timeout):
        try:
            cur = str(e.get("currency") or e.get("country") or "").upper()
            imp = str(e.get("impact") or "").strip().lower()
            if cur in want and imp in ("high", "medium", "3", "2", "holiday"):
                ts = str(e.get("date") or "")
                out.append({"time": ts[11:16] if len(ts) >= 16 else "",
                            "currency": cur, "event": e.get("event"),
                            "impact": "high" if _is_high(e.get("impact")) else "medium",
                            "actual": e.get("actual"), "estimate": e.get("estimate"),
                            "previous": e.get("previous")})
        except Exception:
            continue
    out.sort(key=lambda x: (x["time"] or "99:99"))
    return out


def economic_events(currencies, date: str, *, key: str | None = None,
                    timeout: float = 8.0) -> list[dict]:
    """High-impact scheduled releases for `currencies` on `date` (YYYY-MM-DD).

    Returns a list of ``{currency, event, impact, actual, estimate, previous}`` for
    the matching high-impact events, or ``[]`` when there's no key, no network, or
    nothing relevant. Never raises — a news outage must not break the dashboard.
    """
    k = _key(key)
    want = {c.upper() for c in (currencies or []) if c.upper() in FIAT}
    if not k or not want or not date:
        return []
    out: list[dict] = []
    for e in _fetch_rows(date, k, timeout):
        try:
            cur = str(e.get("currency") or e.get("country") or "").upper()
            if cur in want and _is_high(e.get("impact")):
                out.append({"currency": cur, "event": e.get("event"),
                            "impact": e.get("impact"),
                            "actual": e.get("actual"), "estimate": e.get("estimate"),
                            "previous": e.get("previous")})
        except Exception:
            continue
    return out
