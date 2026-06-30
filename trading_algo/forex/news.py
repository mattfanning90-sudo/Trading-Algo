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

import os

# Currencies an economic calendar covers (crypto has no scheduled releases).
FIAT = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}

_FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"


def _key(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("NEWS_API_KEY") or os.environ.get("FMP_API_KEY")


def _is_high(impact) -> bool:
    return str(impact or "").strip().lower() in ("high", "3", "holiday")


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
    try:
        import requests
        resp = requests.get(_FMP_URL, params={"from": date, "to": date, "apikey": k},
                            timeout=timeout)
        rows = resp.json() if resp.ok else []
    except Exception:
        return []
    out: list[dict] = []
    for e in rows or []:
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
