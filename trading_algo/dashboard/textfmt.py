"""Display text for the book screens — the ONE place numbers become words, so
the page never formats (or computes) money itself. Minus is U+2212; a money
move carries ▲/▼ so its direction never depends on colour alone."""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime

MINUS = "−"
CCY = {"AUD": "A$", "USD": "US$", "GBP": "£", "CAD": "C$", "EUR": "€", "JPY": "¥"}


def sign_char(v: float) -> str:
    return "+" if v > 0 else MINUS if v < 0 else ""


def direction(v: float | None) -> str:
    if v is None:
        return "flat"
    return "up" if v > 0 else "down" if v < 0 else "flat"


def arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "▶"


def money(v: float, ccy: str = "AUD", *, signed: bool = False) -> str:
    sym = CCY.get(ccy, f"{ccy} ")
    r = round(v, 2)                     # sign the value as shown: never "−A$0.00"
    sgn = sign_char(r) if signed else (MINUS if r < 0 else "")
    return f"{sgn}{sym}{abs(r):,.2f}"


def money_move(v: float, ccy: str = "AUD") -> str:
    """A change in money: arrow + sign, e.g. '▼ −A$47.62'."""
    return f"{arrow(v)} {money(v, ccy, signed=True)}"


def pct(v: float, dp: int = 1, *, signed: bool = True) -> str:
    r = round(v * 100, dp)              # sign the value as shown: never "−0.0%"
    sgn = sign_char(r) if signed else (MINUS if r < 0 else "")
    return f"{sgn}{abs(r):.{dp}f}%"


def points(v: float) -> str:
    return f"{abs(v) * 100:.1f} points"


def day(stamp) -> str:
    """'2026-10-13' / '2026-10-13 03:00' / ISO / datetime -> '13 Oct 2026'."""
    d = stamp if isinstance(stamp, (datetime, _date)) else datetime.strptime(str(stamp)[:10], "%Y-%m-%d")
    return f"{d.day} {d.strftime('%b %Y')}"
