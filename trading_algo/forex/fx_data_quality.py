"""Pre-signal FX data-quality gate.

The FX side mirrors the equity sleeve's ``data_quality`` (trading_algo/
data_quality.py): signals are only as good as the prices feeding them, and
``fx_data._align`` outer-joins every pair onto a union calendar and forward-fills
gaps. A delisted or frozen feed therefore has its last close carried forward
*indefinitely* — so without a gate a dead pair silently sits in the panel at a
stale mark and stays in the target book run after run.

This is the ONE FX validator ``fx_book.run_once`` calls right before
``fx_strategy.compute_targets`` (via ``explain.decide_and_explain``), so it only
trims the *candidate* universe fed to the single weight function — it never
re-weights (invariant #3 stays intact) and never looks ahead (invariant #1: only
trailing closes are read).

Checks, as-of the latest bar:
  * dead price — latest close is NaN / <= 0
  * staleness  — the trailing run of identical closes (the number of consecutive
                 forward-filled bars) reaches ``STALE_BARS``. Kept deliberately
                 conservative so a normal quiet FX weekend / holiday gap — a few
                 identical ffill closes on a mixed FX+crypto calendar — never
                 trips it; only a genuinely stuck/delisted feed does.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# A frozen feed carries its last close forward forever, so its trailing run of
# identical closes grows without bound. A live pair prints a fresh close every
# bar; even a 3-day holiday weekend on a mixed FX+crypto calendar forward-fills
# at most ~3-4 identical closes, so a threshold of 6 leaves comfortable headroom
# (matches the equity gate's "6 identical closes" staleness rule).
STALE_BARS = 6


@dataclass
class QualityReport:
    """Symbols excluded as-of the latest bar, and why."""
    excluded: set = field(default_factory=set)
    reasons: dict = field(default_factory=dict)

    def flag(self, symbol: str, reason: str) -> None:
        # keep the first (most specific) reason per symbol
        if symbol not in self.excluded:
            self.excluded.add(symbol)
            self.reasons[symbol] = reason


def _trailing_identical_run(col: pd.Series) -> int:
    """Length of the final run of identical (valid) closes at the end of `col`."""
    valid = col.dropna()
    if valid.empty:
        return 0
    values = valid.to_numpy()
    last = values[-1]
    run = 0
    for v in values[::-1]:
        if v == last:
            run += 1
        else:
            break
    return run


def assess(closes: pd.DataFrame, *, stale_bars: int = STALE_BARS) -> QualityReport:
    """Flag columns whose latest close is untrustworthy (trailing data only)."""
    report = QualityReport()
    if closes is None or closes.empty:
        return report
    for s in closes.columns:
        col = closes[s]
        if col.dropna().empty:
            report.flag(s, "no data")
            continue
        # Dead check on the LATEST close — the exact value the book would mark and
        # trade against (a missing latest print must not be traded at a stale mark).
        last = float(col.iloc[-1])
        if not np.isfinite(last) or last <= 0:
            report.flag(s, f"dead price ({last})")
            continue
        run = _trailing_identical_run(col)
        if run >= stale_bars:
            report.flag(s, f"stale ({run} unchanged closes)")
            continue
    return report


def eligible(closes: pd.DataFrame, base: set | None = None, *,
             stale_bars: int = STALE_BARS) -> tuple[set | None, QualityReport]:
    """The eligible candidate set after the quality gate, and the report.

    Returns ``(base, empty_report)`` unchanged when nothing is flagged — so a
    clean run is bit-for-bit identical to no gate at all. When symbols are
    flagged, returns ``(universe - flagged)``, intersected with `base` when one
    is supplied.
    """
    report = assess(closes, stale_bars=stale_bars)
    if not report.excluded:
        return base, report
    universe = set(closes.columns) if base is None else set(base)
    return universe - report.excluded, report
