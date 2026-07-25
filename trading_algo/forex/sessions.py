"""Per-symbol market sessions — is this instrument's venue actually open?

The FX book trades a mixed universe: FX majors, crypto, US equities and bond
ETFs. They do NOT share a calendar. Crypto is genuinely 24/7; FX runs one
continuous week from ~Sun 22:00 UTC to ~Fri 22:00 UTC; US cash equities and bond
ETFs trade a weekday session only.

Why this matters, concretely: `fx_data._align` outer-joins every symbol onto a
union calendar and forward-fills. With crypto in the universe the calendar has
bars all weekend, so a shut FX pair keeps serving its Friday close. Without a
gate the book re-weights that pair against a frozen price — paying half the
dealing spread each time for exposure that cannot capture a move, because there
is no move to capture. (See `docs/LIVE_BOOK_AUDIT.md` for the live evidence: one
Saturday, a book took EURUSD long→short→flat, every leg filled at the same
Friday close.)

The gate is per SYMBOL, not per run. A mixed book must keep trading crypto
through the weekend while its FX legs sit frozen — an all-or-nothing run-level
check would either halt crypto or permit the dead-price FX churn.

Closed instruments are *frozen*, never flattened: you cannot liquidate on a venue
that is shut. `fx_book` carries their current weight through untouched.

Timestamps are treated as UTC, matching `engine.fx_market_open` and the Yahoo
panel the books are built from.
"""
from __future__ import annotations

from datetime import datetime

from .pairs import ALL_PAIRS, Pair

# US cash session in UTC. Deliberately conservative — a symbol is only treated as
# open well inside the session, so a bar stamped at the boundary is frozen rather
# than traded at a price that may not be live. 14:30-21:00 UTC is 09:30-16:00 ET
# during US summer time; the window is not DST-adjusted, which costs at most an
# hour of eligibility in winter and never opens a shut market.
_EQUITY_OPEN_UTC = 14
_EQUITY_CLOSE_UTC = 21

# The FX week: opens ~22:00 UTC Sunday, closes ~22:00 UTC Friday.
_FX_EDGE_HOUR = 22


def is_open(pair: Pair | str, ts: datetime) -> bool:
    """True if `pair`'s venue is trading at `ts` (treated as UTC)."""
    p = ALL_PAIRS.get(pair) if isinstance(pair, str) else pair
    if p is None:                      # unknown symbol: don't invent a session
        return True
    weekday, hour = ts.weekday(), ts.hour        # Mon=0 .. Sun=6

    if p.asset_class == "crypto":
        return True

    if p.asset_class == "fx":
        if weekday == 5:                          # Saturday: shut all day
            return False
        if weekday == 6:                          # Sunday: reopens ~22:00 UTC
            return hour >= _FX_EDGE_HOUR
        if weekday == 4:                          # Friday: closes ~22:00 UTC
            return hour < _FX_EDGE_HOUR
        return True

    # equity / bond ETFs — weekday cash session only.
    if weekday >= 5:
        return False
    return _EQUITY_OPEN_UTC <= hour < _EQUITY_CLOSE_UTC


def closed_symbols(symbols, ts: datetime) -> set[str]:
    """The subset of `symbols` whose venue is shut at `ts`."""
    return {s for s in symbols if not is_open(s, ts)}


def session_report(symbols, ts: datetime) -> str:
    """One-line, human-readable summary of what is frozen and why."""
    shut = closed_symbols(symbols, ts)
    if not shut:
        return "all venues open"
    by_class: dict[str, list[str]] = {}
    for s in sorted(shut):
        p = ALL_PAIRS.get(s)
        by_class.setdefault(p.asset_class if p else "unknown", []).append(s)
    return "; ".join(f"{cls} shut ({', '.join(names)})"
                     for cls, names in sorted(by_class.items()))
