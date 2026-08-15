"""Target → position transition: the ONE place a target weight becomes a trade.

`fx_strategy.compute_targets` answers *"what should this book hold at bar t?"* as
a pure function of price history. It cannot answer *"should we trade to get
there?"*, because that depends on what is already held and for how long — path
state the weight function deliberately does not carry.

That transition is this module, and it is **shared**: `fx_backtest` and `fx_book`
both call `settle()`, so a rule that changes turnover changes it identically in
the simulation and in the live book. Two copies of this logic is exactly the
drift invariant #3 exists to prevent (the band used to be written out twice).

Three controls, applied per instrument, in order:

1. **Entry/exit hysteresis** (`entry_threshold` / `exit_threshold`) — open only
   on conviction, then keep holding until conviction is clearly gone. With a
   single threshold, a weight hovering at the boundary opens and closes over and
   over, paying half the spread each way; the gap between the two ends that.
2. **Minimum hold** (`min_hold_bars`) — a fresh position is not closed before
   its signal has had a chance to resolve. Two things still override it, because
   they are risk events rather than churn: a genuine reversal (the target flips
   sign) and a forced flatten (the drawdown breaker).
3. **No-churn band** (`rebalance_min_delta`) — ignore any remaining move too
   small to be worth the spread.

All three are **path-dependent but not forward-looking**: every decision uses the
current position, its age, and the target for the bar being decided. Nothing
here reads a future bar, so invariant #1 is preserved.

With `entry_threshold = exit_threshold = 0.0` and `min_hold_bars = 0` this
reduces exactly to the plain no-churn band that preceded it — pinned by
`tests/test_fx_position_policy.py::test_defaults_reproduce_the_plain_band`.
"""
from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .fx_config import FXParams

# A position whose age we do not know is treated as fully seasoned rather than
# freshly opened. Books that predate `bars_held` carry real positions of unknown
# age; assuming 0 would retroactively freeze every one of them for min_hold_bars
# and hold them against their own exit signal.
SEASONED = 10**9


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def settle(held: Mapping[str, float], target: pd.Series, p: FXParams, *,
           bars_held: Mapping[str, int] | None = None,
           frozen: set[str] | None = None,
           force_flat: bool = False) -> dict[str, float]:
    """The book to hold into the next bar, given the book held now.

    `frozen` names are held at their CURRENT weight whatever the target says.
    They are absent from `target` (trimmed from the candidate universe upstream)
    and without this would read as target 0.0 and be flattened — i.e. the book
    would "sell" on a venue that is closed. Holding through a shut session is
    what actually happens to a real position, so it is what happens here, and it
    outranks even `force_flat`: the breaker cannot fill on a closed venue either.

    `force_flat` is the drawdown breaker: go flat now, ignoring hysteresis and
    the minimum hold. Those exist to stop churn, not to trap a halted book.
    """
    frozen = frozen or set()
    ages = bars_held or {}
    out: dict[str, float] = {}

    for k in set(held) | set(target.index):
        cur = float(held.get(k, 0.0))
        if k in frozen:
            out[k] = cur
            continue
        if force_flat:
            out[k] = 0.0
            continue

        tgt = float(target.get(k, 0.0))
        reversal = cur != 0.0 and tgt != 0.0 and _sign(tgt) != _sign(cur)

        # 1. Hysteresis. Opening (or reversing onto the other side) demands
        #    `entry_threshold`; an existing position survives until the target
        #    decays to `exit_threshold`.
        if cur == 0.0 or reversal:
            want = tgt if abs(tgt) >= p.entry_threshold else 0.0
        else:
            want = 0.0 if abs(tgt) <= p.exit_threshold else tgt

        # 2. Minimum hold — protect a young position from being CLOSED. A
        #    reversal is exempt (see the docstring): a signal that has swung to
        #    the other side is new information, not noise around zero.
        if (want == 0.0 and cur != 0.0 and not reversal
                and ages.get(k, SEASONED) < p.min_hold_bars):
            want = cur

        # 3. No-churn band on whatever movement is left.
        out[k] = want if abs(want - cur) >= p.rebalance_min_delta else cur

    return out


def advance_ages(prev: Mapping[str, float], new: Mapping[str, float],
                 bars_held: Mapping[str, int] | None = None,
                 *, dust: float = 0.0) -> dict[str, int]:
    """Age every surviving position by one bar; reset the ones just (re)opened.

    Called once per settled bar. A position is "new" when it was flat before or
    has changed sign — both start a fresh signal, so both restart the clock.
    Closed positions drop out entirely rather than ageing at zero.
    """
    ages = bars_held or {}
    out: dict[str, int] = {}
    for k, w in new.items():
        if abs(w) <= dust:
            continue
        was = float(prev.get(k, 0.0))
        reopened = was == 0.0 or _sign(was) != _sign(w)
        out[k] = 0 if reopened else int(ages.get(k, SEASONED)) + 1
    return out
