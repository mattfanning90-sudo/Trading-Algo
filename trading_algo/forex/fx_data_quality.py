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
  * bar capability (`assess_panel` only) — the instrument has no intrabar range
                 at all (a close-only source; see `bar_quality`). ``assess``
                 structurally CANNOT see this: it reads the closes matrix, and a
                 close-only source's closes are perfectly healthy — it is the
                 missing high/low that corrupts ADX/ATR/Donchian. That is why
                 the panel-level entry point exists and why `fx_book` calls it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import bar_quality
from . import fx_data

# A frozen feed carries its last close forward forever, so its trailing run of
# identical closes grows without bound. A live pair prints a fresh close every
# bar; even a 3-day holiday weekend on a mixed FX+crypto calendar forward-fills
# at most ~3-4 identical closes, so a threshold of 6 leaves comfortable headroom
# (matches the equity gate's "6 identical closes" staleness rule).
#
# Measured worst case, for whoever tunes this next: FX forward-filled onto the
# 24/7 crypto calendar gives a trailing run of 3 over a plain weekend, and 5 when
# a market holiday adjoins one (Easter 2026: Fri 3 + Mon 6 Apr; Christmas 2025:
# Thu 25 + Fri 26 Dec). That is ONE bar of margin. It is not raised here because
# loosening a live safety gate to buy margin trades one failure mode (a healthy
# major frozen out for a week) for a worse one (a genuinely stuck feed traded two
# bars longer) — but if a source with a thinner publication calendar than Yahoo's
# ever feeds the FX legs, revisit this before it fires.
STALE_BARS = 6


@dataclass
class QualityReport:
    """Symbols excluded as-of the latest bar, and why.

    `close_only` is reported separately from `excluded` because the two are not
    the same statement: a close-only instrument has *good prices* (it marks the
    book correctly) and only bad *bars*. Whether that gets it excluded is the
    book's policy decision, but the fact is always recorded.
    """
    excluded: set = field(default_factory=set)
    reasons: dict = field(default_factory=dict)
    close_only: set = field(default_factory=set)

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


def assess_panel(panel: dict[str, pd.DataFrame], *,
                 policy: str = bar_quality.REFUSE,
                 closes: pd.DataFrame | None = None,
                 stale_bars: int = STALE_BARS) -> QualityReport:
    """`assess` over the panel's closes PLUS the bar-capability check.

    This is the entry point `fx_book.run_once` uses, because it is the one place
    that sees the *bars* rather than just the closes. `policy` is the book's
    `close_only_signals`:

      * ``"refuse"``  — raise `CloseOnlyBarsError` naming the instruments. The
        book does not trade this cycle. This is the default and it is what makes
        a misconfiguration fail on the first cycle rather than the tenth.
      * ``"exclude"`` — flag them like any other bad feed, so the caller's
        existing "freeze these names" path (print + notify + persist + trim the
        candidate universe) handles them with no new code path.
      * ``"allow"``   — record them in `report.close_only` and let them through;
        the caller must wrap the decision in `bar_quality.allow_close_only()`.

    `closes` lets a caller pass the matrix it already computed (fx_book has it)
    instead of rebuilding it.
    """
    # Validate the policy FIRST: a typo must fail on every run, not only on the
    # days a close-only instrument happens to be in the panel.
    mode = bar_quality.check_policy(policy)
    if not panel:
        return QualityReport()
    if closes is None:
        closes = fx_data.closes(panel)
    report = assess(closes, stale_bars=stale_bars)
    flat = bar_quality.close_only_symbols(panel)
    if not flat:
        return report
    report.close_only.update(flat)
    if mode == bar_quality.REFUSE:
        raise bar_quality.CloseOnlyBarsError(
            f"{', '.join(flat)} have no intrabar range (close-only source: one "
            f"observed price per bar). Refusing to score them — ADX/ATR/Donchian "
            f"would compute a close-to-close statistic under a range-based name "
            f"and change what this book trades without changing any risk or cost "
            f"number. Set close_only_signals='exclude' to trade without them, "
            f"'allow' to accept the degraded reading deliberately, or point the "
            f"book at a bar source. See docs/DATA_FEEDS.md.")
    if mode == bar_quality.EXCLUDE:
        for sym in flat:
            report.flag(sym, bar_quality.CLOSE_ONLY_REASON)
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
