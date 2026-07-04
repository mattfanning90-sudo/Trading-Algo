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


@functools.lru_cache(maxsize=8)
def _fetch_rows_cached(date: str, key: str, timeout: float) -> tuple:
    """The ONE provider fetch (memoised per date/key): GET the FMP calendar for
    `date` with the graceful never-raise contract — any failure is ().

    Memoisation matters operationally: a `dashboard --all` export calls the
    calendar twice per book (feed + catalysts) × 4 books = 8 identical GETs;
    caching collapses that to 1, keeping the hourly CI well clear of the FMP
    free tier's 250 calls/day. The exporter is a short-lived CLI process, so
    intra-process staleness is a non-issue.
    """
    try:
        import requests
        resp = requests.get(_FMP_URL, params={"from": date, "to": date, "apikey": key},
                            timeout=timeout)
        rows = resp.json() if resp.ok else []
    except Exception:
        return ()
    return tuple(rows or [])


def _fetch_rows(date: str, key: str, timeout: float = 8.0) -> list:
    """Shared FMP fetch for `calendar_feed` and `economic_events` — the provider
    endpoint/params and the graceful-[] failure contract are written ONCE here.
    Deep-copies on the way out so callers can't mutate the cache."""
    return [copy.deepcopy(e) for e in _fetch_rows_cached(date, key, timeout)]


def _is_high(impact) -> bool:
    return str(impact or "").strip().lower() in ("high", "3", "holiday")


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
