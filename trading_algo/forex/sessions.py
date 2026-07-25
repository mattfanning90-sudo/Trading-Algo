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

The bar's INTERVAL matters, and getting it wrong is not a rounding error. A daily
bar is stamped at midnight — it names a whole trading DAY, not the instant
00:00. An equity's cash session never covers midnight, so asking "was AAPL's
venue open at 2026-07-22 00:00?" of a *daily* bar answers a question nobody
asked, and answers it "no" on every ordinary Wednesday. The live `multiasset`
book is exactly that: US equities and bonds on daily bars. So intraday session
hours are applied only to intraday bars; a daily bar asks the coarser and
correct question, "did this venue trade that day at all?".
"""
from __future__ import annotations

from datetime import datetime

from .pairs import ALL_PAIRS, Pair

# Intervals whose bar stamp names a DAY rather than an instant (see `_bar_key`).
DAILY_INTERVALS = ("1d", "B", "1D")

# US cash session in UTC, applied to INTRADAY bars only. Deliberately
# conservative — a symbol is only treated as open well inside the session, so a
# bar stamped at the boundary is frozen rather than traded at a price that may
# not be live. 14:30-21:00 UTC is 09:30-16:00 ET during US summer time; the
# window is not DST-adjusted, which costs at most an hour of eligibility in
# winter and never opens a shut market.
_EQUITY_OPEN_UTC = 14
_EQUITY_CLOSE_UTC = 21

# The FX week: opens ~22:00 UTC Sunday, closes ~22:00 UTC Friday.
_FX_EDGE_HOUR = 22


def is_daily(interval: str | None) -> bool:
    """True when `interval` names a whole day rather than an instant."""
    return (interval or "1d") in DAILY_INTERVALS


def _fx_week(ts: datetime) -> bool:
    """The FX week alone, ignoring instrument type — Sun ~22:00 → Fri ~22:00 UTC."""
    weekday, hour = ts.weekday(), ts.hour        # Mon=0 .. Sun=6
    if weekday == 5:                              # Saturday: shut all day
        return False
    if weekday == 6:                              # Sunday: reopens ~22:00 UTC
        return hour >= _FX_EDGE_HOUR
    if weekday == 4:                              # Friday: closes ~22:00 UTC
        return hour < _FX_EDGE_HOUR
    return True


def is_open(pair: Pair | str, ts: datetime, interval: str = "1d") -> bool:
    """True if `pair`'s venue is trading on the bar stamped `ts` (treated as UTC).

    `interval` is the BOOK's cadence. On a daily bar the question is "did this
    venue trade on this date"; only on an intraday bar is it "was it open at this
    instant". The FX and crypto answers are the same either way — a daily stamp of
    midnight sits inside the FX week on any weekday — so only the cash-equity
    branch consults it.
    """
    p = ALL_PAIRS.get(pair) if isinstance(pair, str) else pair
    if p is None:                      # unknown symbol: don't invent a session
        return True
    weekday, hour = ts.weekday(), ts.hour        # Mon=0 .. Sun=6

    if p.asset_class == "crypto":
        return True

    if p.asset_class == "fx":
        return _fx_week(ts)

    # equity / bond ETFs — weekday cash session only.
    if weekday >= 5:
        return False
    if is_daily(interval):
        # The bar names the whole weekday, and the cash session ran inside it.
        # Applying intraday hours to a midnight stamp would freeze every equity
        # on every ordinary trading day.
        return True
    return _EQUITY_OPEN_UTC <= hour < _EQUITY_CLOSE_UTC


def closed_symbols(symbols, ts: datetime, interval: str = "1d") -> set[str]:
    """The subset of `symbols` whose venue is shut on the bar stamped `ts`."""
    return {s for s in symbols if not is_open(s, ts, interval)}


def session_report(symbols, ts: datetime, interval: str = "1d") -> str:
    """One-line, human-readable summary of what is frozen and why."""
    shut = closed_symbols(symbols, ts, interval)
    if not shut:
        return "all venues open"
    by_class: dict[str, list[str]] = {}
    for s in sorted(shut):
        p = ALL_PAIRS.get(s)
        by_class.setdefault(p.asset_class if p else "unknown", []).append(s)
    return "; ".join(f"{cls} shut ({', '.join(names)})"
                     for cls, names in sorted(by_class.items()))


def parse_bar(stamp: str) -> datetime:
    """A book's bar key -> datetime: ``'YYYY-MM-DD'`` (daily) or
    ``'YYYY-MM-DD HH:MM'`` (intraday). Raises ``ValueError`` on anything else.

    The trading gate and `verify.check_market_hours` must read a persisted stamp
    identically — a gate that permits a fill the auditor then flags forever is
    worse than either alone — so this is the only parser either of them uses.
    """
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            pass
    raise ValueError(f"unparseable timestamp {stamp!r}")


def bar_interval(stamp: str) -> str:
    """Infer a persisted bar key's cadence, for auditing a ledger after the fact
    (the book's live `interval` isn't recorded on each trade)."""
    return "1d" if len(stamp.strip()) == 10 else "60m"


def bar_is_tradable(symbol: str, ts: datetime, kind: str = "fx",
                    interval: str | None = None) -> bool:
    """Auditor-facing form of `is_open`, for a ledger rather than a live universe.

    `verify.check_market_hours` reads persisted trades, which include the equity
    sleeves' regional tickers (``BHP.AX``, ``VOD.L``) — instruments `ALL_PAIRS`
    has never heard of. `is_open` deliberately declines to invent a session for an
    unknown symbol, because inside a live FX book that would freeze a name over a
    guess. An audit needs the opposite default: a weekend fill is worth flagging
    even on a symbol we can't classify, so unknowns fall back to the book's own
    `kind` — the FX week for an FX book, a plain weekday rule for a cash-equity
    one.

    Sharing this module with the gate is the point: the boundary that permits a
    fill and the boundary that flags one afterwards cannot drift apart.
    """
    if symbol in ALL_PAIRS:
        return is_open(symbol, ts, interval or "1d")
    if kind == "fx":
        return _fx_week(ts)
    return ts.weekday() < 5
