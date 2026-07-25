"""When an instrument may be traded — the ONE session definition.

Two consumers must never disagree:

* ``fx_book._run_once_locked`` refuses to change a position on a bar whose market
  was shut, and
* ``verify.check_market_hours`` audits the persisted ledger for exactly that.

A second copy of these boundaries would let the gate and the audit drift — the
gate would allow a fill the verifier then flags forever, or vice versa — so both
import from here. This module is a leaf: it imports only ``pairs`` (itself a
leaf), so ``fx_book`` and ``verify`` can both use it without a cycle.

The boundaries
--------------
* **Crypto** genuinely trades 24/7 and is exempt.
* **FX** closes Friday ~22:00 UTC and reopens Sunday ~22:00 UTC.
* **Cash equities** (an equity ETF inside a multi-asset book) are shut all
  weekend. Their intraday session is *narrower* than the FX week and is NOT
  modelled here — see the caveat below.

Known limitation (deliberate)
-----------------------------
Weekend and FX-week boundaries are modelled; per-exchange intraday cash hours are
not. An equity ETF in a multi-asset book can therefore still be traded on a bar
stamped inside the FX week but outside its own cash session. Closing that gap
needs per-exchange calendars (the equity sleeve's ``calendars.py`` has them, keyed
by region, not by instrument), which is a design decision rather than a fix.
"""
from __future__ import annotations

from datetime import datetime

# FX rolls over at 22:00 UTC — Friday's close and Sunday's reopen.
FX_ROLLOVER_HOUR = 22


def parse_bar(stamp: str) -> datetime:
    """Parse a book's bar key — ``'YYYY-MM-DD'`` (daily) or ``'YYYY-MM-DD HH:MM'``
    (intraday). Raises ``ValueError`` on anything else.

    The gate and the audit must read a stamp identically, so this is the only
    parser either of them uses. Note a daily key carries no hour, so it parses to
    00:00 — which is why a Friday *daily* bar is inside the FX week (hour 0 < 22)
    while a Friday 23:00 *intraday* bar is not.
    """
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            pass
    raise ValueError(f"unparseable timestamp {stamp!r}")


def is_crypto(symbol: str) -> bool:
    """True when `symbol` is a crypto instrument (24/7, never gated)."""
    from .pairs import ALL_PAIRS

    pair = ALL_PAIRS.get(symbol)
    return bool(pair is not None and pair.asset_class == "crypto")


def bar_is_tradable(symbol: str, ts: datetime, kind: str = "fx") -> bool:
    """Whether `symbol` could really be traded on a bar stamped `ts` (UTC).

    `kind` is the book's kind: ``"fx"`` applies the Friday/Sunday 22:00 UTC
    rollover, anything else (a cash-equity book) treats the whole weekend as shut.

    Crypto is always tradable. A ``True`` here does not promise the instrument's
    own intraday session was open — see the module docstring's limitation.
    """
    if is_crypto(symbol):
        return True

    weekend = ts.weekday() >= 5
    if kind == "fx" and ts.weekday() == 4 and ts.hour >= FX_ROLLOVER_HOUR:
        weekend = True          # Friday after the FX close is already the weekend
    if kind == "fx" and ts.weekday() == 6 and ts.hour >= FX_ROLLOVER_HOUR:
        weekend = False         # Sunday from 22:00 UTC the FX week has restarted
    return not weekend
