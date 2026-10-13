"""The drawdown circuit breaker — one definition, four engines.

`backtest.py`, `paper_trade.py`, `forex/fx_backtest.py` and `forex/fx_book.py`
each hand-rolled the same six lines, and each carried the same defect: the
high-water mark rose but never fell, so a book still below its pre-crash high
when the cooldown expired re-tripped on the very next bar and never traded
again. Four copies of one rule is one missing definition, not four bugs.

Two properties the copies did not have:

* **The peak does not rise while HALTED.** A halted book is in cash. It did not
  earn a new high, and crediting it one would only deepen the measured drawdown
  it has to climb out of.
* **On RESUME the peak is re-based to the equity at resume.** The cooldown is
  the whole of the punishment. Measuring a cooled-off book against a level it
  no longer has is a permanent off-switch wearing a cooldown's clothes.

Re-basing re-aims the breaker; it does not disarm it. A fresh fall of `stop`
from the NEW mark halts the book exactly as before.

So the stored mark (`peak_equity_base` on an equity book, `peak_equity` on an
FX book) is the high since inception OR THE LAST RESUME, not the all-time high.
"""
from __future__ import annotations

import math


def update_peak(peak: float, equity: float, *, halted: bool) -> float:
    """The high-water mark for one bar.

    While HALTED the peak does not rise: the book is in cash and has not earned
    a new high. The RESUME re-base lives in `breaker_step`, which is the only
    place that can see the halt end.

    Nor does a non-finite equity ever raise it: a NaN peak compares False
    against every stop (the breaker disarmed for good), an infinite one makes
    every later bar a 100% drawdown (latched for good).
    """
    if halted or not math.isfinite(equity):
        return float(peak)
    return max(float(peak), float(equity))


def breaker_step(*, equity: float, peak: float, halted: bool, cooldown: int,
                 stop: float | None, cooldown_len: int) -> tuple[float, bool, int]:
    """One bar of breaker state. Returns (peak, halted, cooldown).

    `stop` is the peak-to-trough fraction that halts the book (None disables the
    breaker entirely); `cooldown_len` is how many steps a halt lasts, in
    whatever unit the calling engine counts in (market days for the equity
    books, decision bars for FX — see `config.Cooldown`).
    """
    peak = update_peak(peak, equity, halted=halted)
    if halted:
        cooldown = int(cooldown) - 1
        if cooldown <= 0:
            # Resume: re-base the mark to what the book actually has, so a book
            # that has not recovered is not instantly re-tripped by its own
            # pre-halt high. Never to a non-finite equity: a NaN mark compares
            # False against every stop and would disarm the breaker for good.
            return (float(equity) if math.isfinite(equity) else peak), False, 0
        return peak, True, cooldown
    if stop is not None and peak > 0 and equity / peak - 1 <= -float(stop):
        return peak, True, int(cooldown_len)
    return peak, False, int(cooldown)
