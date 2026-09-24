# Measurement Truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every reported number in this repo mean what it says, so a strategy result can be told apart from a measurement artefact.

**Architecture:** One measurement semantic layer (`metrics.py`) owns every number a human reads, enforced by a syntax-tree test. One reconciliation bridge (`attribution.py`) walks from the backtest's return to the paper book's return line by line and must sum to an identity — that identity is how every later fix is proved. Then the convention fixes, then the mechanism fixes, then regeneration, deploy and a clean book restart. Point-in-time index membership runs as a parallel wave because it never touches a live book.

**Tech Stack:** Python 3.11+, pandas, numpy, pytest. No new runtime dependencies. One new module (`trading_algo/risk_breaker.py`); everything else extends what exists.

**Spec:** `docs/superpowers/specs/2026-09-24-measurement-truth-design.md` — read it first. This plan argues from it and the two travel together.

## Global Constraints

Every task's requirements implicitly include this section.

- **One defect, one change.** No drive-by refactoring of code a fix passes through. Adjacent problems become findings, not diffs. (Spec §13.)
- **Fix the concept, not the call sites.** Six Sharpe implementations are one missing definition, not six bugs. Prefer removing a special case to adding one.
- **Every change carries its test and its number** — the test written first, then the bridge line it moved, before and after. A fix with no measured movement is not finished.
- **A fix needing more than ~30 lines is a design signal.** Say so in the task rather than pushing through.
- **No new module unless no existing one can host it.** `metrics.py` and `attribution.py` are the homes; `risk_breaker.py` is the one sanctioned new module.
- **Diffs stay readable in one screen.** Matt reads every diff; that is the review mechanism, and it only works if each commit is one idea.
- **Never write to `state/`.** Any command that might must run with `FX_STATE_DIR` and `MOMENTUM_STATE_DIR` exported to a scratch directory first.
- **Python 3.11+**, type hints, `from __future__ import annotations` at the top of new modules.
- **Tests** live in `tests/test_<module>.py`, plain pytest, no network (monkeypatch or fixtures).
- **Every commit ends with:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Progress

**Update this table as each task lands, and tick the task's own `- [ ]` steps.**
A compacted session reads this first to find where execution stopped.

| Phase | Tasks | Status |
|---|---|---|
| 0 — deploy what already exists | merge branch → `main` | ✅ **done** 2026-09-24 (`06464ca`) |
| 1 — measurement semantic layer | 1-4 | ⬜ not started |
| 2 — reconciliation bridge | 5-8 | ⬜ not started |
| 3 — conventions | 9-13 | ⬜ not started |
| 4 — mechanism | 14-20 | ⬜ not started |
| 5 — regeneration, deploy, restart | 21-26 | ⬜ not started |
| W2 — survivorship (needs data) | 27 | ⬜ blocked on membership data |

**Execution log** — one line per task, appended as it completes:

- **2026-09-24 — Phase 0, merge to `main` (`06464ca`).** `main` had diverged by 25 bot
  state commits (books advancing daily, no code), so the live state was merged onto
  the branch first and `main` then fast-forwarded. Full gate green before the push:
  pytest 1121 passed, ruff clean, bandit 0 medium+, detect-secrets clean, regression
  gate matches baseline, and `state/` untouched by the suite. The September
  remediation is now deployed; the schedulers pick it up on their next run.

---

## Before you edit: line numbers in this plan are stale

The drafting agents found HEAD had moved during the work and **every line number inherited from the review is stale**. Verified examples:

| What | Stale reference | Actual at drafting time |
|---|---|---|
| whole-share `int()` truncation | `paper_trade.py:476` | `paper_trade.py:598-602` |
| micro mode | `paper_trade.py:460-469` | `paper_trade.py:548-561` |
| paper drawdown breaker | `paper_trade.py:905-920` | `paper_trade.py:900-926` |

**Grep for the code. Do not trust a line number in this plan or the spec.**

## Order is load-bearing

Each phase changes the input to the next. Doing regeneration before conventions means doing it twice; re-baselining the regression gate before the mechanism fixes hides a real regression behind a deliberate one.

| Phase | Tasks | Gate to pass |
|---|---|---|
| 1 — semantic layer | 1-4 | No independent metric implementation outside `metrics.py`; suite green |
| 2 — bridge | 5-8 | Bridge identity holds to 1bp on the current books |
| 3 — conventions | 9-13 | Bridge shows the dividend and cash-interest lines at zero |
| 4 — mechanism | 14-20 | A test per defect; the regression gate re-baselined **once**, here, with the bridge as evidence |
| 5 — deploy | 21-27 | Schedulers on new code; books reopened clean; PIT delta published per region |

**Task 23 (the training/traded universe split) must land before Task 26 (the restart)** or the reopened FX books come back up on 16 symbols, re-creating the defect the restart exists to clear.

---

# Phase 1 — The instruments: a measurement semantic layer

One module owns every number a human reads. Nothing later in this plan can be proved without it, and the annualisation default alone fixes the intraday-metrics defect everywhere with no call-site edits.

---

### Task 1: The measurement semantic layer (`trading_algo/metrics.py`)

**Files:**
- Modify: `trading_algo/metrics.py:1-56` (docstring, imports, and `compute_metrics`)
- Modify: `trading_algo/forex/marks.py:26-39` (docstring sentence + imports) and `trading_algo/forex/marks.py:259-270` (the function body becomes a re-export)
- Modify: `trading_algo/forex/fx_config.py:17-26` (import block; delete the local `ANNUALIZATION = 252`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (this is the first).
- Produces:
  - `trading_algo.metrics.ANNUALIZATION = 252`
  - `trading_algo.metrics.periods_per_year(idx: pd.DatetimeIndex) -> float`
  - `trading_algo.metrics.Measure` (frozen dataclass: `value`, `name`, `periods_per_year`, `risk_free=None`, `n=0`, `stderr=None`, `.label() -> str`)
  - `trading_algo.metrics.annualised_vol(rets: pd.Series, *, periods_per_year: float) -> float`
  - `trading_algo.metrics.sharpe(rets: pd.Series, *, periods_per_year: float, risk_free: float | None) -> Measure`
  - `trading_algo.metrics.cagr(equity: pd.Series, *, periods_per_year: float) -> Measure`
  - `trading_algo.metrics.max_drawdown(equity: pd.Series) -> Measure`
  - `trading_algo.metrics.compute_metrics(rets, equity, risk_free=RISK_FREE, currency="AUD", periods_per_year: float | None = None) -> dict` — return-dict keys unchanged
  - `trading_algo.forex.marks.periods_per_year` stays importable (re-export, same object)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics.py` (the file already imports `math`, `numpy as np`, `pandas as pd` and `from trading_algo.metrics import benchmark_stats, compute_metrics` — extend that import line as shown):

```python
# --- append to tests/test_metrics.py ---
# (change the existing import line to:)
# from trading_algo.metrics import (ANNUALIZATION, Measure, annualised_vol,
#                                   benchmark_stats, cagr, compute_metrics,
#                                   max_drawdown, periods_per_year, sharpe)
import pytest

from trading_algo.metrics import (ANNUALIZATION, Measure, annualised_vol, cagr,
                                  max_drawdown, periods_per_year, sharpe)


def test_periods_per_year_derived_from_bar_spacing():
    """THE defect this layer exists to kill: 60-minute bars annualised at 252."""
    daily = pd.date_range("2026-01-01", periods=100, freq="B")
    hourly = pd.date_range("2026-01-01", periods=100, freq="h")
    minute = pd.date_range("2026-01-01", periods=100, freq="min")
    assert periods_per_year(daily) == 252
    assert periods_per_year(hourly) == pytest.approx(24 * 365.25)      # 8766
    assert periods_per_year(minute) == 24 * 365.25                     # capped
    assert periods_per_year(pd.DatetimeIndex([])) == 252               # degenerate


def test_compute_metrics_annualises_an_hourly_book_at_its_own_spacing():
    """Same per-bar returns, two indexes: the hourly one must annualise at
    8766, i.e. sqrt(8766/252) ~ 5.9x the daily vol — with NO call-site change."""
    rng = np.random.default_rng(7)
    r = pd.Series(rng.normal(0.0002, 0.01, 500))
    eq = 10_000.0 * (1 + r).cumprod()
    hourly = pd.date_range("2026-01-01", periods=500, freq="h")
    daily = pd.date_range("2026-01-01", periods=500, freq="B")
    m_h = compute_metrics(r.set_axis(hourly), eq.set_axis(hourly))
    m_d = compute_metrics(r.set_axis(daily), eq.set_axis(daily))
    assert m_h["AnnVol"] == pytest.approx(m_d["AnnVol"] * math.sqrt(8766 / 252),
                                          rel=1e-3)
    # and an explicit convention still wins over the derived one
    m_forced = compute_metrics(r.set_axis(hourly), eq.set_axis(hourly),
                               periods_per_year=252)
    assert m_forced["AnnVol"] == pytest.approx(m_d["AnnVol"], rel=1e-9)


def test_compute_metrics_keys_are_unchanged():
    """No consumer breaks: the dict keys are frozen by this test."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    equity = 100.0 * (1 + rets).cumprod()
    m = compute_metrics(rets, equity)
    assert set(m) == {"CAGR", "AnnVol", "Sharpe (vs 3.5%)", "Sortino",
                      "MaxDrawdown", "Calmar", "WinRate(days)",
                      "FinalEquity (AUD)"}


def test_measure_label_names_the_convention():
    """A consumer cannot print a Sharpe without saying WHICH Sharpe."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    assert sharpe(rets, periods_per_year=252,
                  risk_free=0.035).label() == "Sharpe (vs 3.5%, 252/yr)"
    assert sharpe(rets, periods_per_year=8766.0,
                  risk_free=None).label() == "Sharpe (8766/yr)"
    equity = 100.0 * (1 + rets).cumprod()
    assert cagr(equity, periods_per_year=252).label() == "CAGR (252/yr)"
    assert max_drawdown(equity).label() == "MaxDrawdown"   # not annualised


def test_primitives_agree_with_compute_metrics():
    """The dict is assembled FROM the primitives — not a parallel formula."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    equity = 100.0 * (1 + rets).cumprod()
    m = compute_metrics(rets, equity)
    assert m["AnnVol"] == round(annualised_vol(rets, periods_per_year=252), 4)
    assert m["Sharpe (vs 3.5%)"] == round(
        sharpe(rets, periods_per_year=252, risk_free=0.035).value, 2)
    assert m["MaxDrawdown"] == round(max_drawdown(equity).value, 4)
    assert m["CAGR"] == round(cagr(equity, periods_per_year=252).value, 4)
    assert isinstance(sharpe(rets, periods_per_year=252, risk_free=0.035), Measure)
    assert ANNUALIZATION == 252


def test_marks_still_re_exports_periods_per_year():
    """Moving the function must not break the FX callers that import it."""
    from trading_algo.forex import marks
    assert marks.periods_per_year is periods_per_year
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: FAIL at collection with `ImportError: cannot import name 'ANNUALIZATION' from 'trading_algo.metrics'`.

- [ ] **Step 3: Implement**

Replace `trading_algo/metrics.py` lines 1-56 (the docstring, imports, `metric`, and `compute_metrics`) with the following. `benchmark_stats` at lines 59-87 is **not touched** — it is a separate measure and a separate defect (see concerns).

```python
"""Performance statistics for a return / equity series.

THE measurement semantic layer (measurement-truth design §4): one module owns
every number a human reads. A measure is a labelled record — value, convention,
sample size — not a bare float whose annualisation the reader has to guess.

`periods_per_year` lives here (moved from `forex/marks.py`, which re-exports it)
because the annualisation convention and the measures that use it are one idea,
and having them in two places is how a 60-minute book came to be annualised at
252 trading days.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import RISK_FREE

# Daily bars: ~252 trading days a year. The DEFAULT annualisation, used when a
# series carries no usable DatetimeIndex to derive one from. Moved here from
# forex/fx_config.py, which now re-exports it.
ANNUALIZATION = 252


def periods_per_year(idx: pd.DatetimeIndex) -> float:
    """Periods-per-year implied by a DatetimeIndex's median bar spacing.

    Calendar-time convention (the project decision — see `forex/marks.py`'s
    module docstring): >= 12h spacing -> ``ANNUALIZATION`` (252); faster ->
    ``365.25*86400/secs`` capped at hourly (``24*365.25``), so a minute book does
    not pretend to 525,960 independent observations a year. Empty/degenerate
    indexes fall back to daily spacing (-> 252).
    """
    med = idx.to_series().diff().median() if len(idx) else pd.NaT
    secs = med.total_seconds() if pd.notna(med) and med.total_seconds() > 0 else 86400.0
    return min(ANNUALIZATION if secs >= 43200 else 365.25 * 86400.0 / secs, 24 * 365.25)


# Bound once so a `periods_per_year=` PARAMETER can shadow the public name
# inside a function without the function losing access to itself.
_ppy = periods_per_year


def _ppy_of(idx) -> float:
    """The annualisation a series' OWN index implies; 252 when it has no dates."""
    return _ppy(idx) if isinstance(idx, pd.DatetimeIndex) else float(ANNUALIZATION)


@dataclass(frozen=True)
class Measure:
    """One measured number plus the convention that produced it.

    `periods_per_year` is NaN for a measure that is not annualised (drawdown);
    `risk_free` is None when no risk-free rate was subtracted.
    """
    value: float
    name: str
    periods_per_year: float
    risk_free: float | None = None
    n: int = 0
    stderr: float | None = None

    def label(self) -> str:
        """e.g. "Sharpe (vs 3.5%, 252/yr)", "CAGR (252/yr)", "MaxDrawdown"."""
        parts: list[str] = []
        if self.risk_free is not None:
            parts.append(f"vs {self.risk_free:.1%}")
        if self.periods_per_year == self.periods_per_year:       # not NaN
            parts.append(f"{self.periods_per_year:g}/yr")
        return f"{self.name} ({', '.join(parts)})" if parts else self.name


def metric(metrics: dict, prefix: str):
    """Read one figure out of a `compute_metrics` dict by PREFIX.

    Two labels below carry their parameters — "Sharpe (vs 4.0%)" and
    "FinalEquity (AUD)" — so an exact-key lookup silently misses them. The
    reader lives next to the writer so callers don't each have to rediscover
    that; every consumer of these dicts routes through here."""
    return next((v for k, v in metrics.items() if k.startswith(prefix)), None)


# ---------------------------------------------------------------------------
# Primitives — every reported vol / Sharpe / CAGR / drawdown derives from these
# ---------------------------------------------------------------------------
def annualised_vol(rets: pd.Series, *, periods_per_year: float) -> float:
    """Annualised standard deviation of a return series (ddof=1).

    A single observation has no sample dispersion, so this is 0.0 rather than a
    silent ddof=1 nan."""
    r = rets.dropna()
    return float(r.std() * np.sqrt(periods_per_year)) if len(r) > 1 else 0.0


def sharpe(rets: pd.Series, *, periods_per_year: float,
           risk_free: float | None) -> Measure:
    """Annualised Sharpe. `risk_free=None` subtracts nothing and says so in the
    label. `stderr` is Lo's approximation, in the same annualised units."""
    r = rets.dropna()
    vol = annualised_vol(r, periods_per_year=periods_per_year)
    excess = float(r.mean() * periods_per_year)
    if risk_free is not None:
        excess -= risk_free
    value = float(excess / max(vol, 1e-9))
    se = None
    if len(r) > 2:
        sr_pp = value / np.sqrt(periods_per_year)
        se = float(np.sqrt((1.0 + 0.5 * sr_pp ** 2) / (len(r) - 1))
                   * np.sqrt(periods_per_year))
    return Measure(value=value, name="Sharpe",
                   periods_per_year=float(periods_per_year),
                   risk_free=risk_free, n=len(r), stderr=se)


def cagr(equity: pd.Series, *, periods_per_year: float) -> Measure:
    """Compound annual growth rate of an equity curve.

    `n` counts equity OBSERVATIONS, not intervals — the convention this repo has
    always used. A curve of N marks spans N-1 periods, so this runs one period
    fast (0.03% of the exponent on a 14-year daily backtest). Preserved
    deliberately so moving the formula here changes no published number; the
    off-by-one is recorded as a finding, not fixed in this task.
    """
    if len(equity) < 1 or float(equity.iloc[0]) == 0.0:
        return Measure(value=float("nan"), name="CAGR",
                       periods_per_year=float(periods_per_year), n=len(equity))
    n = len(equity)
    value = float((equity.iloc[-1] / equity.iloc[0]) ** (periods_per_year / n) - 1.0)
    return Measure(value=value, name="CAGR",
                   periods_per_year=float(periods_per_year), n=n)


def max_drawdown(equity: pd.Series) -> Measure:
    """Deepest peak-to-trough fall of an equity curve, as a negative fraction.

    Not annualised, so `periods_per_year` is NaN and `label()` omits it."""
    dd = equity / equity.cummax() - 1.0
    return Measure(value=float(dd.min()), name="MaxDrawdown",
                   periods_per_year=float("nan"), n=len(equity))


def compute_metrics(rets: pd.Series, equity: pd.Series,
                    risk_free: float = RISK_FREE,
                    currency: str = "AUD",
                    periods_per_year: float | None = None) -> dict:
    """Annualised performance summary assembled from the primitives above.

    `periods_per_year=None` derives the convention from `rets.index`, so a
    60-minute book annualises at 8766 and a daily one at 252 with NO call-site
    change — that single default is the fix for the hourly-annualised-at-252
    defect. A series with no DatetimeIndex falls back to 252.

    The returned KEYS are unchanged (pinned by
    `tests/test_metrics.py::test_compute_metrics_keys_are_unchanged`).
    """
    rets = rets.dropna()
    if len(rets) == 0 or equity.iloc[0] == 0:
        return {"error": "insufficient data"}

    ppy = _ppy_of(rets.index) if periods_per_year is None else float(periods_per_year)
    ann_ret = cagr(equity, periods_per_year=ppy).value
    ann_vol = annualised_vol(rets, periods_per_year=ppy)
    sr = sharpe(rets, periods_per_year=ppy, risk_free=risk_free)
    excess = float(rets.mean() * ppy - risk_free)
    downside = annualised_vol(rets[rets < 0], periods_per_year=ppy)
    # No (or too few) losing days -> downside deviation is 0/undefined. Fall
    # back to total volatility so Sortino stays finite instead of a silent nan
    # from the max(nan, 1e-9) idiom.
    if not (downside > 0):
        downside = ann_vol
    sortino = excess / downside if downside > 0 else float("nan")
    max_dd = max_drawdown(equity).value
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("nan")

    return {
        "CAGR": round(float(ann_ret), 4),
        "AnnVol": round(float(ann_vol), 4),
        f"Sharpe (vs {risk_free:.1%})": round(float(sr.value), 2),
        "Sortino": round(float(sortino), 2),
        "MaxDrawdown": round(max_dd, 4),
        "Calmar": round(float(calmar), 2),
        "WinRate(days)": round(float((rets > 0).mean()), 3),
        f"FinalEquity ({currency})": round(float(equity.iloc[-1]), 0),
    }
```

In `trading_algo/forex/marks.py`, delete the import at line 37 and replace lines 259-270 (the whole `periods_per_year` definition) with the re-export:

```python
# ---------------------------------------------------------------------------
# Annualisation (see module docstring: calendar-time IS the convention)
# ---------------------------------------------------------------------------
from ..metrics import periods_per_year  # noqa: F401,E402  (moved; re-exported for callers)
```

and change line 37 from `from .fx_config import ANNUALIZATION` to nothing (delete the line — `ANNUALIZATION` is no longer used in this module), and amend the docstring sentence at lines 28-30 from

```
kept deliberately for simplicity and internal consistency. ``periods_per_year``
below is the ONE implementation (moved verbatim from the dashboard's ``_ppy``);
book-side prints (``fx_book.status``) and the dashboard both route through it.
```

to

```
kept deliberately for simplicity and internal consistency. ``periods_per_year``
now lives in ``trading_algo.metrics`` (the measurement semantic layer) and is
re-exported below, so the FX and equity stacks annualise through ONE function;
book-side prints (``fx_book.status``) and the dashboard both route through it.
```

In `trading_algo/forex/fx_config.py`, delete lines 25-26 (`# Daily FX bars: ...` and `ANNUALIZATION = 252`) and add the re-export to the existing import block (after the `from ..config import (...)` group, before `from . import bar_quality`):

```python
from ..metrics import ANNUALIZATION  # noqa: F401  (moved to metrics.py; re-exported)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: PASS (all tests, old and new).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "metrics or marks or backtest or property or fx_book or dashboard"`
Expected: PASS. Watch specifically for an import cycle from the new `fx_config -> ..metrics` edge; `trading_algo/metrics.py` imports only `.config`, `numpy` and `pandas`, so there is none, but the FX collection is where it would surface. `tests/test_fx_marks.py::test_periods_per_year_pins_the_convention` and `tests/test_fx_dashboard_units.py` (`dashboard._ppy is marks.periods_per_year`) must stay green **unchanged** — the re-export binds the same function object. No existing test's premise changes in this task.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/metrics.py trading_algo/forex/marks.py trading_algo/forex/fx_config.py tests/test_metrics.py
git commit -m "feat(metrics): derive annualisation from the bar spacing

One module owns the convention: periods_per_year moves from forex/marks
(re-exported there) and compute_metrics derives it from rets.index by
default, so a 60-minute book annualises at 8766 instead of 252 with no
call-site change. Measures return a labelled record, not a bare float.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Route the paper tearsheet through the primitives

**Files:**
- Modify: `trading_algo/tearsheet.py:12-39` (imports and `_annualised`) and `trading_algo/tearsheet.py:64-67` (the headline rows)
- Test: `tests/test_tearsheet.py`

**Interfaces:**
- Consumes: `trading_algo.metrics.periods_per_year(idx) -> float`, `metrics.annualised_vol(rets, *, periods_per_year) -> float`, `metrics.sharpe(rets, *, periods_per_year, risk_free) -> Measure`, `metrics.max_drawdown(equity) -> Measure`, `metrics.ANNUALIZATION`
- Produces: `tearsheet._annualised(equity_history) -> dict` now carries the extra keys `"periods_per_year": float` and `"sharpe_label": str` alongside the existing `"ann_vol"`, `"max_drawdown"`, `"sharpe"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tearsheet.py` (add `import pandas as pd` to the top of the file — it currently imports only `from trading_algo import attribution, tearsheet`):

```python
# --- append to tests/test_tearsheet.py ---
import pandas as pd

from trading_algo import metrics


def test_tearsheet_routes_through_the_metrics_layer():
    """No local 252 and no hand-rolled drawdown loop: every figure on the
    tearsheet is the metrics primitive's value, to the last bit."""
    eh = _state()["equity_history"]
    rets = attribution.equity_returns(eh)
    ppy = metrics.periods_per_year(rets.index)
    equity = pd.Series([float(v) for _, v in eh],
                       index=pd.to_datetime([d for d, _ in eh])).sort_index()

    stats = tearsheet._annualised(eh)
    assert stats["periods_per_year"] == ppy
    assert stats["ann_vol"] == metrics.annualised_vol(rets, periods_per_year=ppy)
    assert stats["max_drawdown"] == metrics.max_drawdown(equity).value
    assert stats["sharpe"] == round(
        metrics.sharpe(rets, periods_per_year=ppy,
                       risk_free=0.035).value, 2)


def test_tearsheet_rows_name_their_convention():
    """A reader cannot be shown a Sharpe without being told which Sharpe."""
    md = tearsheet.account_tearsheet(_state())
    assert "| Sharpe (vs 3.5%, 252/yr) |" in md
    assert "| Ann. vol (252/yr) |" in md
    assert "≈daily" not in md                     # the vague old label is gone


def test_tearsheet_annualises_an_hourly_history_at_its_own_spacing():
    """A book marked hourly must not be annualised as if its marks were days."""
    idx = pd.date_range("2026-06-01", periods=60, freq="h")
    vals = [10_000.0 * (1.0 + 0.0004 * ((i % 7) - 3)) ** i for i in range(60)]
    eh = [[d.strftime("%Y-%m-%d %H:%M"), v] for d, v in zip(idx, vals)]
    stats = tearsheet._annualised(eh)
    assert stats["periods_per_year"] > 2000       # hourly ppy ~ 8766, not 252
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_tearsheet.py -v`
Expected: FAIL — `test_tearsheet_routes_through_the_metrics_layer` raises `KeyError: 'periods_per_year'`, and `test_tearsheet_rows_name_their_convention` fails with `AssertionError: assert '| Sharpe (vs 3.5%, 252/yr) |' in ...` (the page still says `≈daily`).

- [ ] **Step 3: Implement**

In `trading_algo/tearsheet.py`, replace the import block at lines 12-20 and `_annualised` at lines 23-39 with:

```python
from __future__ import annotations

import argparse
import os

import pandas as pd

from . import attribution
from . import config as cfg
from . import metrics
from . import paper_trade


def _annualised(equity_history: list) -> dict:
    """Ann. vol, max drawdown and Sharpe from the equity history.

    Every figure comes from the measurement layer (`trading_algo.metrics`) at
    the annualisation the history's OWN mark spacing implies — a daily book
    still reads 252, an hourly one does not pretend to.
    """
    rets = attribution.equity_returns(equity_history)
    if len(rets) < 2:
        return {"ann_vol": None, "max_drawdown": None, "sharpe": None,
                "periods_per_year": float(metrics.ANNUALIZATION),
                "sharpe_label": "Sharpe"}
    ppy = metrics.periods_per_year(rets.index)
    vol = metrics.annualised_vol(rets, periods_per_year=ppy)
    equity = pd.Series([float(v) for _, v in equity_history],
                       index=pd.to_datetime([d for d, _ in equity_history])
                       ).sort_index()
    mdd = metrics.max_drawdown(equity).value
    sr = metrics.sharpe(rets, periods_per_year=ppy, risk_free=cfg.RISK_FREE)
    return {"ann_vol": vol, "max_drawdown": mdd,
            "sharpe": round(sr.value, 2) if vol > 0 else None,
            "periods_per_year": ppy, "sharpe_label": sr.label()}
```

and replace the headline rows at lines 64-67 with:

```python
    if stats["max_drawdown"] is not None:
        ppy = stats["periods_per_year"]
        out += [f"| Max drawdown | {stats['max_drawdown']:.2%} |",
                f"| Ann. vol ({ppy:g}/yr) | {stats['ann_vol']:.1%} |",
                f"| {stats['sharpe_label']} | {stats['sharpe']} |"]
```

Note the two behaviour changes this carries, both deliberate and both visible in the diff: `import math` is dropped (nothing else in the module used it), and the drawdown is now measured on the **date-sorted** curve, matching `attribution.equity_returns`, instead of raw list order. A paper book's `equity_history` is appended chronologically, so on every real state the number is identical.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_tearsheet.py -v`
Expected: PASS.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "tearsheet or attribution or metrics"`
Expected: PASS. No existing test's premise changes — `tests/test_tearsheet.py`'s four original tests assert on the headline/sleeve/cost sections and the exact total-return string, none of which this task moves.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/tearsheet.py tests/test_tearsheet.py
git commit -m "refactor(tearsheet): measure through the metrics layer

Deletes the local sqrt(252), the local *252 excess and the hand-rolled
drawdown loop; the Sharpe and vol rows now carry the convention that
produced them instead of the vague '~daily'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Route `attribution.tracking_error` through the primitives

**Files:**
- Modify: `trading_algo/attribution.py:20-28` (imports and the `TRADING_DAYS` constant) and `trading_algo/attribution.py:47-55` (`tracking_error`)
- Test: `tests/test_attribution.py`

**Interfaces:**
- Consumes: `trading_algo.metrics.annualised_vol(rets, *, periods_per_year) -> float`, `trading_algo.metrics.ANNUALIZATION`
- Produces: `attribution.tracking_error(realized_ret: pd.Series, predicted_ret: pd.Series, periods_per_year: int = metrics.ANNUALIZATION) -> dict` — the public signature (name, position, default value 252) is unchanged; `attribution.TRADING_DAYS` no longer exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_attribution.py`:

```python
# --- append to tests/test_attribution.py ---
from trading_algo import metrics


def test_tracking_error_is_the_metrics_annualised_vol():
    """The annualised std of the difference IS annualised_vol — not a second
    copy of sqrt(252) living in attribution.py."""
    idx = pd.bdate_range("2026-01-05", periods=40)
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0004, 0.009, 40), index=idx)
    p = pd.Series(rng.normal(0.0004, 0.009, 40), index=idx)
    te = attribution.tracking_error(r, p)
    expected = metrics.annualised_vol(r - p,
                                      periods_per_year=metrics.ANNUALIZATION)
    assert te["tracking_error_bps"] == round(expected * 1e4, 1)
    assert te["n_obs"] == 40


def test_tracking_error_honours_an_explicit_convention():
    """The public signature is unchanged: callers can still pass their own."""
    idx = pd.date_range("2026-01-05", periods=40, freq="h")
    rng = np.random.default_rng(4)
    r = pd.Series(rng.normal(0.0, 0.002, 40), index=idx)
    p = pd.Series(rng.normal(0.0, 0.002, 40), index=idx)
    daily = attribution.tracking_error(r, p)["tracking_error_bps"]
    hourly = attribution.tracking_error(r, p, periods_per_year=8766)["tracking_error_bps"]
    assert hourly > daily * 5          # sqrt(8766/252) ~ 5.9


def test_no_second_annualisation_constant_in_attribution():
    """One definition of 252 in the repo, and it is metrics.ANNUALIZATION."""
    assert not hasattr(attribution, "TRADING_DAYS")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_attribution.py -v`
Expected: FAIL — `test_no_second_annualisation_constant_in_attribution` fails with `AssertionError: assert not True` (`attribution.TRADING_DAYS` is still defined at line 28).

- [ ] **Step 3: Implement**

In `trading_algo/attribution.py`, replace lines 20-28:

```python
from __future__ import annotations

import pandas as pd

from . import metrics

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0
```

(`import math` goes: line 54 was its only use. `TRADING_DAYS` goes: `metrics.ANNUALIZATION` is the one definition.)

Then replace `tracking_error` at lines 47-55:

```python
def tracking_error(realized_ret: pd.Series, predicted_ret: pd.Series,
                   periods_per_year: int = metrics.ANNUALIZATION) -> dict:
    """Annualised std of (realized - predicted) per-period returns on common dates.

    The annualisation is the measurement layer's (`metrics.annualised_vol`), so
    a tracking error and a vol on this repo's pages can never mean two
    different things."""
    df = pd.concat([realized_ret.rename("r"), predicted_ret.rename("p")], axis=1).dropna()
    if len(df) < 2:
        return {"tracking_error_bps": None, "n_obs": int(len(df))}
    te = metrics.annualised_vol(df["r"] - df["p"],
                                periods_per_year=periods_per_year)
    return {"tracking_error_bps": round(te * 1e4, 1), "n_obs": int(len(df))}
```

`metrics.annualised_vol` uses pandas' default `ddof=1`, exactly as `diff.std(ddof=1)` did, so every existing tracking-error number is unchanged to the last bit. This task does **not** touch `attribution_report`'s hand-computed total returns — those are a different measure and a different defect, and the reconciliation bridge (Tasks 5-8) owns that file next.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_attribution.py -v`
Expected: PASS.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "attribution or tearsheet or metrics or verify"`
Expected: PASS. `tests/test_attribution.py::test_tracking_error_zero_when_identical` must still give exactly `0.0` — `annualised_vol` on an all-zero difference returns `0.0`, so its premise is unchanged.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/attribution.py tests/test_attribution.py
git commit -m "refactor(attribution): annualise tracking error through metrics

Deletes attribution's own TRADING_DAYS = 252 and its sqrt(); the public
tracking_error signature and every current number are unchanged.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Delete the JavaScript Sharpe, and enforce the single source

**Files:**
- Modify: `trading_algo/forex/dashboard.py:29-43` (add the `metrics` import), `trading_algo/forex/dashboard.py:247-264` (`_curve_metrics`), new `_period_metrics` after it, `trading_algo/forex/dashboard.py:955-975` (the payload dict), and `trading_algo/forex/dashboard.py:1504-1535` (the `_PAGE` metrics block)
- Modify: `tests/test_fx_dashboard_units.py:128` (a premise this task deliberately changes)
- Create: `tests/test_metrics_single_source.py`
- Test: `tests/test_metrics_single_source.py`, `tests/test_fx_dashboard_units.py`

**Interfaces:**
- Consumes: `trading_algo.metrics.sharpe(rets, *, periods_per_year, risk_free) -> Measure`, `metrics.annualised_vol(rets, *, periods_per_year) -> float`, `metrics.max_drawdown(equity) -> Measure`
- Produces:
  - `dashboard._curve_metrics(dates, values, *, ppy: float | None = None) -> dict` (new keyword; existing positional callers unaffected)
  - `dashboard._PERIOD_DAYS: tuple[int, ...] = (0, 7, 30, 90)`
  - `dashboard._period_metrics(curve: list[dict], ppy: float) -> dict[str, dict]`
  - payload keys `"book_period_metrics"` and `"bench_period_metrics"`

**Size note (spec §13):** this task is at the ~30-line signal, because deleting the client-side `compute()` forces the period slicing to move into Python — there is nowhere else for it to go once the page stops doing maths. That is the concept fix, not sprawl. Step 6 therefore lands it as **two commits**, one idea each: route `_curve_metrics`, then delete the JavaScript.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metrics_single_source.py`:

```python
"""Design §4 enforcement: ONE module owns every number a human reads.

The same trick `tests/test_consistency.py` uses for the weight primitives,
applied to the measurement primitives. An AST walk over `trading_algo/**.py`
fails when any module outside `metrics.py` computes a Sharpe, a drawdown or an
annualisation for itself — because six Sharpe implementations are not six bugs,
they are one missing definition, and the repo's own Sharpe study measured two
of the surviving conventions disagreeing by 0.45 on a single series.

Two allowlists, and the difference between them matters:

* `_MEASUREMENT_INPUTS` is PERMANENT. A vol that sizes a position, a per-period
  Sharpe that feeds PSR/DSR, a gradient's own sigma and an evolutionary fitness
  score are not numbers a human reads — spec §4's boundary keeps their inline
  maths. Any of their values that IS displayed must be re-derived through the
  layer for display.
* `_PENDING_DISPLAY_SITES` is a list of open findings: display sites that still
  compute for themselves and have not been routed yet. Emptying it is the goal.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "trading_algo"

# Measurement INPUTS — permanent (spec §4: optimisation internals keep their
# inline maths). "*" exempts the whole module.
_MEASUREMENT_INPUTS: dict[str, object] = {
    "forex/nn.py": "*",                 # sharpe_net loss needs its own gradient
    "forex/evolve.py": "*",             # fitness IS a selection score
    "forex/research.py": {"run_research"},        # candidate-edge search score
    "validation.py": {"sharpe_ratio",             # per-period SR: the PSR/DSR input
                      "probabilistic_sharpe_ratio"},
    "signals.py": {"realised_vol"},               # the vol-targeting denominator
    "crowding.py": {"crowding_report"},           # regime gate input
    "data_quality.py": {"assess"},                # feed-health gate input
    "forex/dashboard.py": {"_min_track_record_days"},   # Bailey/LdP MinTRL
}

# Open findings — display sites not yet routed through the layer. Each is a
# number a human reads, computed locally. Remove an entry when its task lands.
_PENDING_DISPLAY_SITES: dict[str, set[str]] = {
    "paper_trade.py": {"status"},            # CLI 'Ann. vol' / 'Max drawdown'
    "forex/fx_book.py": {"status"},          # same two prints, FX side
    "forex/dashboard.py": {"_risk_costs"},   # the drawdown curve on the page
    "forex/ml_backtest.py": {"_annual_metrics"},   # walk-forward report card
}


def _allowed(rel: str) -> object:
    a = _MEASUREMENT_INPUTS.get(rel)
    b = _PENDING_DISPLAY_SITES.get(rel)
    if a == "*":
        return "*"
    return set(a or set()) | set(b or set())


def _attr_call(node, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name)


def _is_252(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == 252


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, kind) for every independent measurement computation."""
    out: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if _attr_call(n, "sqrt") and n.args and _is_252(n.args[0]):
            out.append((n.lineno, "sqrt(252)"))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult) and (
                _is_252(n.left) or _is_252(n.right)):
            out.append((n.lineno, "* 252"))
        elif _attr_call(n, "cummax"):
            out.append((n.lineno, "cummax() — a drawdown"))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            den = n.right
            if isinstance(den, ast.BinOp) and isinstance(den.op, ast.Mult):
                den = den.left                    # the `sd * sqrt(ann)` shape
            if _attr_call(den, "std") and any(_attr_call(k, "mean")
                                              for k in ast.walk(n.left)):
                out.append((n.lineno, "mean/std — a Sharpe"))
    return out


def _owner(tree: ast.AST, line: int) -> str:
    """Innermost function enclosing `line`, or '<module>'."""
    best = None
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if f.lineno <= line <= (f.end_lineno or f.lineno):
                if best is None or f.lineno > best.lineno:
                    best = f
    return best.name if best else "<module>"


def test_no_independent_metric_implementation():
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "metrics.py":
            continue                       # THE definition
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allow = _allowed(rel)
        for line, kind in _offences(tree):
            fn = _owner(tree, line)
            if allow == "*" or fn in allow:
                continue
            bad.append(f"trading_algo/{rel}:{line} in {fn}() — {kind}")
    assert not bad, (
        "These compute a measure for themselves instead of calling "
        "trading_algo.metrics:\n  " + "\n  ".join(bad))


def test_allowlisted_modules_exist():
    """An allowlist that names a deleted file silently stops guarding it."""
    for rel in {**_MEASUREMENT_INPUTS, **_PENDING_DISPLAY_SITES}:
        assert (ROOT / rel).is_file(), rel


def test_fx_page_has_no_javascript_sharpe():
    """The page renders values computed in Python; it does no maths of its own."""
    from trading_algo.forex import dashboard
    page = dashboard._PAGE
    assert "Math.sqrt" not in page              # no JS vol / Sharpe
    assert "RF=0.035" not in page               # no second cash rate
    assert "book_period_metrics" in page        # it reads Python's numbers
```

And append to `tests/test_fx_dashboard_units.py`:

```python
# --- append to tests/test_fx_dashboard_units.py ---
def test_period_metrics_come_from_python(isolated):
    """The 1W/1M/3M/ALL table is computed server-side, per period, at the
    curve's own annualisation — the page only renders it."""
    fx_book.init_account("matt", 5_000, "balanced")
    p = dashboard.build_payload("matt", synthetic=True)
    bm = p["book_period_metrics"]
    assert set(bm) == {"0", "7", "30", "90"}
    assert bm["0"] == p["book_metrics"]          # ALL == the header tile
    assert p["bench_period_metrics"]["0"] == p["bench_metrics"]


def test_curve_metrics_route_through_the_metrics_layer():
    """No local Sharpe: the page's numbers ARE trading_algo.metrics'."""
    from trading_algo import metrics
    from trading_algo.forex.fx_config import FX_RISK_FREE
    idx = pd.date_range("2026-06-01", periods=40, freq="B")
    rng = np.random.default_rng(11)
    vals = list(10_000 * np.cumprod(1 + rng.normal(0.0003, 0.008, 40)))
    m = dashboard._curve_metrics([d.strftime("%Y-%m-%d") for d in idx], vals)
    s = pd.Series(vals, index=idx, dtype=float)
    r = s.pct_change().dropna()
    assert m["sharpe"] == round(metrics.sharpe(r, periods_per_year=252,
                                               risk_free=FX_RISK_FREE).value, 2)
    assert m["vol"] == round(metrics.annualised_vol(r, periods_per_year=252), 4)
    assert m["max_dd"] == round(metrics.max_drawdown(s).value, 4)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_metrics_single_source.py -v tests/test_fx_dashboard_units.py::test_period_metrics_come_from_python -v`
Expected: FAIL —
- `test_no_independent_metric_implementation` fails listing `trading_algo/forex/dashboard.py:259 in _curve_metrics() — mean/std — a Sharpe` and `trading_algo/forex/dashboard.py:262 in _curve_metrics() — cummax() — a drawdown`;
- `test_fx_page_has_no_javascript_sharpe` fails with `assert 'Math.sqrt' not in page`;
- `test_period_metrics_come_from_python` fails with `KeyError: 'book_period_metrics'`.

- [ ] **Step 3: Implement**

**3a — route `_curve_metrics`.** Add the import to `trading_algo/forex/dashboard.py`'s block (after `import pandas as pd`, before `from . import feeds`):

```python
from .. import metrics
```

Replace `_curve_metrics` at lines 247-264 with:

```python
# The period buttons on the equity chart: ALL, 1W, 1M, 3M (in days; 0 = ALL).
_PERIOD_DAYS: tuple[int, ...] = (0, 7, 30, 90)


def _curve_metrics(dates, values, *, ppy: float | None = None) -> dict:
    """Return / Sharpe / vol / drawdown / win-rate for one equity curve.

    Every figure is `trading_algo.metrics`' — the page has no maths of its own.
    `ppy` pins the annualisation (so a one-week SLICE of an hourly book is still
    annualised at the whole curve's spacing); omitted, it is derived from the
    curve's own index.
    """
    if not values or len(values) < 2:
        return {}
    # format='mixed': one documented --bar 60m override run on a daily book mixes
    # 'YYYY-MM-DD' and 'YYYY-MM-DD HH:MM' keys — must not raise on pandas 2/3.
    s = pd.Series(values, index=pd.to_datetime(dates, format="mixed"), dtype=float)
    out = {"total_return": round(float(s.iloc[-1] / s.iloc[0] - 1.0), 4)}
    r = s.pct_change().dropna()
    if len(r) >= 5 and r.std() > 0:
        # Annualise by the curve's ACTUAL bar spacing — an hourly book
        # (daytrader) must not be annualised as if its bars were daily.
        p = _ppy(s.index) if ppy is None else ppy
        out["sharpe"] = round(metrics.sharpe(r, periods_per_year=p,
                                             risk_free=FX_RISK_FREE).value, 2)
        out["vol"] = round(metrics.annualised_vol(r, periods_per_year=p), 4)
        out["max_dd"] = round(metrics.max_drawdown(s).value, 4)
        out["win_rate"] = round(float((r > 0).mean()), 3)
    return out


def _period_metrics(curve: list, ppy: float) -> dict:
    """The metrics table for each period button, computed HERE.

    The page used to recompute Sharpe, vol and drawdown in JavaScript on the
    sliced curve, with its own hardcoded cash rate and a POPULATION standard
    deviation — two different numbers for one curve. The slice happens in Python
    now and the page only renders."""
    out: dict[str, dict] = {}
    if not curve:
        return out
    times = pd.to_datetime([p["time"] for p in curve], format="mixed")
    last = times[-1]
    for days in _PERIOD_DAYS:
        keep = (range(len(curve)) if not days else
                [i for i, t in enumerate(times)
                 if t >= last - pd.Timedelta(days=days)])
        out[str(days)] = _curve_metrics([curve[i]["time"] for i in keep],
                                        [curve[i]["value"] for i in keep],
                                        ppy=ppy)
    return out
```

Add the two payload keys in the returned dict at line 969, right after `"book_curve": book_curve, "book_metrics": book_metrics,`:

```python
        "book_period_metrics": _period_metrics(book_curve, book_ppy),
        "bench_period_metrics": _period_metrics(bench_curve, bench_ppy),
```

(`book_ppy` and `bench_ppy` are both already computed above the return statement.)

**3b — delete the JavaScript.** In `_PAGE`, replace lines 1504-1513 (from `const el=document.getElementById('eqchart')` through the `const BOOK_ANN=...` line):

```js
  const el=document.getElementById('eqchart'), mEl=document.getElementById('metrics');
  const BOOK=DASH.book_curve||[], BENCH=DASH.bench_curve||[];
  // The metrics table is computed in PYTHON (trading_algo.metrics), per period,
  // and shipped as book_period_metrics / bench_period_metrics. There is no
  // JavaScript Sharpe: the page that used to recompute it disagreed with the
  // header tile, had its own cash rate and used a population sd.
  const BOOKM=DASH.book_period_metrics||{}, BENCHM=DASH.bench_period_metrics||{};
  // Kept only to decide whether the mixed-cadence legend below applies.
  const BOOK_ANN=DASH.book_ppy||252, BENCH_ANN=DASH.bench_ppy||252;
```

Delete the whole `function compute(series,ann){...}` block (lines 1516-1526). Then change the body of `apply` at line 1533 to look the numbers up instead of computing them:

```js
  function apply(days){const bk=rebase(cut(BOOK,days)),bh=rebase(cut(BENCH,days));
    benchS.setData(lwc(bh)); bookS.setData(lwc(bk)); c.timeScale().fitContent();
    const key=String(days||0); renderMetrics(BOOKM[key]||{},BENCHM[key]||{});}
```

`ROWS`, `renderMetrics`, the chart construction, the period buttons, the crosshair readout and the `BOOK_ANN!==BENCH_ANN` legend are all unchanged.

**3c — update the one test whose premise this changes.** `tests/test_fx_dashboard_units.py:128` asserts `"compute(bh,BENCH_ANN)" in html`, which pinned the JavaScript that no longer exists. Replace that single line with:

```python
    assert "Math.sqrt" not in html                     # no JS Sharpe at all
    assert "book_period_metrics" in html               # Python's numbers instead
```

The neighbouring assertions (`"book_ppy" in html and "bench_ppy" in html`, `"ANN=252" not in html`, `"compute(bh,252)" not in html`, `"BOOK_ANN!==BENCH_ANN" in html`, `"annualised for its own bar spacing" in html`) all still hold and stay as they are.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_metrics_single_source.py -v tests/test_fx_dashboard_units.py -v`
Expected: PASS.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "dashboard or metrics or multiasset or marks"`
Expected: PASS. Two premises change and are handled **in this task**: `tests/test_fx_dashboard_units.py:128` (rewritten in Step 3c) is the only edit; `tests/test_multiasset_day.py::test_curve_metrics_annualise_by_actual_spacing` and `tests/test_fx_dashboard.py::test_curve_metrics_mixed_date_formats` call `_curve_metrics` positionally and keep passing because `ppy` is keyword-only with a `None` default.

Then run the whole suite once, since this task adds a repo-wide gate:
Run: `python3 -m pytest tests/ -q`
Expected: PASS. If `test_no_independent_metric_implementation` names a site not in either list, do **not** widen the allowlist to make it green — that is a real finding; record it and stop.

- [ ] **Step 6: Commit** (two commits, one idea each)

```bash
git add trading_algo/forex/dashboard.py tests/test_fx_dashboard_units.py
git commit -m "refactor(fx-dashboard): measure the equity curve through metrics

_curve_metrics now calls the measurement layer and slices each period
server-side, so the page has a number to render for every period button.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

git add trading_algo/forex/dashboard.py tests/test_fx_dashboard_units.py tests/test_metrics_single_source.py
git commit -m "fix(fx-dashboard): delete the JavaScript Sharpe

The page recomputed Sharpe, vol and drawdown in the browser with its own
hardcoded cash rate and a population standard deviation, printing a
different Sharpe from the header tile for the same curve. It now renders
the Python values. An AST test fails on any future independent Sharpe,
drawdown or annualisation outside trading_algo/metrics.py.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

# Phase 2 — The instruments: the reconciliation bridge

A signed, line-by-line walk from the backtest's return to the paper book's, which must sum to an identity. This is the master test: every later task names the line it should move and proves it moved.

---

### Task 5: Tracking diagnosis must match the live rebalance path

**Files:**
- Modify: `trading_algo/paper_trade.py:1125-1143`
- Test: `tests/test_paper_trade.py`

**Interfaces:**
- Consumes: `data_quality.eligible(prices, region, asof, base=None) -> tuple[set | None, QualityReport]`; `strategy.compute_targets(prices, index_prices, p, asof=None, eligible=None, capacity=None) -> pd.Series`
- Produces: nothing new. `_print_tracking_diagnosis(account: str, state: dict) -> None` keeps its signature; only its computation changes.

The live rebalance path at `trading_algo/paper_trade.py:849-854` does this:

```python
elig, dq = data_quality.eligible(prices, region, prices.index[-1])
targets = strategy.compute_targets(prices, index_px, params, eligible=elig)
```

The diagnosis at `trading_algo/paper_trade.py:1137-1138` does this instead:

```python
target = strategy.compute_targets(
    px, ix, _account_params(state, region)).abs().sum()
```

No `eligible=`, no `asof=`. It therefore selects from names the data-quality gate froze, and it selects on today's bar rather than the bar the book was actually built on. Measured on `full` 2026-09-19 this printed "FTSE holds 54.6% against an 80.1% target"; the gated, correctly-dated target was 56.8%. The bridge in Task 6 is uninterpretable while this number is a phantom.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paper_trade.py`:

```python
def test_diagnosis_uses_the_same_gate_and_date_as_the_live_path(monkeypatch, capsys):
    """The tracking diagnosis must ask `compute_targets` the same question the
    live rebalance asks (paper_trade.py:849-854): the data-quality gate's
    eligible set, evaluated as of the sleeve's last rebalance date. Without
    both, the 'exposure gap' it prints is an artefact of comparing a gated book
    built in June to an ungated target computed in September."""
    from trading_algo import data, data_quality, strategy

    region = get_region("US")
    px, ix = data.synthetic_region(region, start="2024-01-01", end="2026-01-01")
    monkeypatch.setattr(data, "load_region", lambda *a, **k: (px, ix))

    gated = set(px.columns) - {px.columns[0]}     # the gate froze one name
    calls = {}

    def fake_eligible(prices, reg, asof, base=None):
        calls["eligible_asof"] = asof
        return gated, data_quality.QualityReport()

    monkeypatch.setattr(data_quality, "eligible", fake_eligible)

    real = strategy.compute_targets

    def spy(prices, index_prices, p, asof=None, eligible=None, capacity=None):
        calls["asof"] = asof
        calls["eligible"] = eligible
        return real(prices, index_prices, p, asof=asof, eligible=eligible,
                    capacity=capacity)

    monkeypatch.setattr(strategy, "compute_targets", spy)

    rebal = px.index[-40]
    state = {"sleeves": {"US": {"positions": {px.columns[1]: 10}, "cash": 5_000.0,
                                "last_rebalance_date": rebal.strftime("%Y-%m-%d")}},
             "equity_history": [["2025-01-02", 100_000.0]]}

    pt._print_tracking_diagnosis("test", state)

    # the diagnosis swallows exceptions, so prove it did not silently bail
    out = capsys.readouterr().out
    assert "diagnosis unavailable" not in out, out
    assert calls.get("eligible") == gated, "target computed outside the quality gate"
    assert calls.get("asof") == rebal, "target computed at today's bar, not the rebalance"
    assert calls.get("eligible_asof") == rebal, "quality gate assessed at the wrong date"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_paper_trade.py::test_diagnosis_uses_the_same_gate_and_date_as_the_live_path -v`

Expected: FAIL with `AssertionError: assert None == {'ABBV', 'ABT', ...}` on the `calls.get("eligible") == gated` line — `calls` is `{'asof': None, 'eligible': None}` because today's code passes neither argument.

- [ ] **Step 3: Implement**

Replace `trading_algo/paper_trade.py:1125-1143` (from the inner import down to the closing paren of the per-sleeve `print`) with:

```python
    from . import data, data_quality, strategy
    from .regions import get_region

    print("  Tracking-error diagnosis (before blaming execution):")
    for k, sl in (state.get("sleeves") or {}).items():
        try:
            region = get_region(k)
            px, ix = data.load_region(region, cfg.START)
            # Ask exactly what the live rebalance asked (see run_daily): the
            # gated candidate set, as of the bar the book was actually built on.
            # Today's bar with no gate compares two different questions and
            # invents an exposure gap.
            asof = px.index[-1]
            lrd = sl.get("last_rebalance_date")
            if lrd:
                loc = px.index.searchsorted(pd.Timestamp(lrd), side="right") - 1
                if loc >= 0:
                    asof = px.index[loc]
            elig, _dq = data_quality.eligible(px, region, asof)
            # Positions have not moved since `asof` (rebalances are monthly), so
            # marking them at the as-of bar is the book as executed.
            invested = sum(n * float(px[t].loc[asof])
                           for t, n in (sl.get("positions") or {}).items()
                           if t in px.columns)
            eq = invested + float(sl.get("cash", 0.0))
            target = strategy.compute_targets(
                px, ix, _account_params(state, region),
                asof=asof, eligible=elig).abs().sum()
            held = invested / eq if eq else 0.0
            gap = (held / target - 1.0) if target else 0.0
            print(f"    [{k}] held gross {held:6.1%} vs target {target:6.1%}"
                  f"  ({gap:+.0%} of target)   as of {asof.date()}"
                  f"   last rebalance {sl.get('last_rebalance_date') or 'never'}")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_paper_trade.py::test_diagnosis_uses_the_same_gate_and_date_as_the_live_path -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "paper_trade or consistency or live_path"`

Expected: PASS. No existing test asserts on the diagnosis output, so no test premise changes here.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/paper_trade.py tests/test_paper_trade.py
git commit -m "fix(paper): diagnose tracking against the gated, correctly-dated target

The diagnosis called compute_targets with no eligibility gate and at today's
bar, while the live rebalance passes data_quality.eligible and rebalances
monthly. That is what printed FTSE 54.6% held against an 80.1% target on
2026-09-19; the gated target as of that sleeve's rebalance date was 56.8%.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The reconciliation bridge — `attribution.reconcile`

**Files:**
- Modify: `trading_algo/attribution.py:20-27` (imports), append at end of file
- Test: `tests/test_attribution.py`

**Interfaces:**
- Consumes: `tca.implementation_shortfall(decision: float, fill: float, shares: float, side: str) -> float`; `regions.get_region(key).commission_bps`
- Produces:
  - `attribution.BRIDGE_TOLERANCE: float = 1e-4`
  - `attribution.BRIDGE_LINES: tuple[str, ...]` = `("dividends", "cash_interest", "fill_convention", "commission_floor", "stamp_duty", "exposure_gap", "rebalance_timing", "fx_translation")`
  - `attribution.reconcile(paper_state: dict, predicted_equity: pd.Series) -> dict` with keys `start`, `end`, `backtest_return`, `paper_return`, `lines` (exactly `BRIDGE_LINES`), `notes` (`dict[str, str]`), `residual`, `identity_ok`
  - `attribution._commission_bps(region_key: str | None) -> float | None`

**The sign convention, stated once and enforced by the test.** Every line is a signed return fraction of the book's equity at the window start, positive when the cause put the *backtest* ahead of the paper book. The identity is `backtest_return − Σ(lines) − paper_return = residual`, and `identity_ok` is `abs(residual) <= BRIDGE_TOLERANCE`.

**A line is non-zero only where the two engines treat the cause DIFFERENTLY.** A cost both engines charge identically (stamp duty; the slippage inside `fees.round_trip_cost_rate`) nets to zero and is reported as `0.0` with its measured paper-side amount in `notes`, so the reader still sees the money without the arithmetic double-counting it. `exposure_gap`, `rebalance_timing` and `fx_translation` need price data that the locked `(paper_state, predicted_equity)` signature does not carry, so they are `0.0` with a note and their content lands in `residual` — which is exactly what spec §5 asks for ("a line that cannot be explained lands in `residual` rather than being absorbed"). No target is set for the residual.

**Size note (spec §13):** `reconcile` lands at about fifty-five lines including its docstring, over the ~30-line signal. That is deliberate and I am saying so rather than pushing through silently: it is a new named deliverable from spec §5, not a fix passing through existing code, and its body is one flat loop plus a dict assembly. If it grows past this, the shape to reach for is a `_line_*` function per bridge line, not more branches inside the loop.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_attribution.py`:

```python
def _bridge_state(end_equity: float) -> dict:
    """A book whose ONLY divergence from the backtest is the commission floor.

    One ASX buy: 10 shares at 10.00 = 100 notional. The backtest charges
    commission_bps (8bps) = 0.08; the paper book paid the A$5 min_commission
    floor. The excess is 4.92 AUD, i.e. 4.92e-5 of a 100,000 AUD book.
    decision == fill, so there is no implementation shortfall.
    """
    return {
        "initial_capital_base": 100_000.0,
        "base_currency": "AUD",
        "fx_snapshot": {"AUD": 1.0},
        "equity_history": [["2026-01-05", 100_000.0], ["2026-06-30", end_equity]],
        "trades": [{"date": "2026-02-02", "region": "ASX", "ticker": "BHP.AX",
                    "side": "BUY", "shares": 10, "decision": 10.0, "fill": 10.0,
                    "commission": 5.0, "stamp_duty": 0.0, "currency": "AUD"}],
    }


def _bridge_predicted() -> pd.Series:
    return pd.Series([100_000.0, 101_000.0],
                     index=pd.to_datetime(["2026-01-05", "2026-06-30"]))


def test_bridge_reports_exactly_the_named_lines():
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    assert tuple(rep["lines"]) == attribution.BRIDGE_LINES
    assert rep["start"] == "2026-01-05" and rep["end"] == "2026-06-30"
    # dividends and cash interest are not credited to the paper book yet; they
    # must read zero AND say why, rather than silently absorbing the divergence.
    assert rep["lines"]["dividends"] == 0.0
    assert rep["lines"]["cash_interest"] == 0.0
    assert rep["notes"]["dividends"] and rep["notes"]["cash_interest"]


def test_bridge_identity_holds_when_every_cause_is_named():
    """backtest_return - sum(lines) - paper_return == residual, and the residual
    is inside tolerance when the only divergence is an explained one."""
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    assert rep["lines"]["commission_floor"] == pytest.approx(4.92e-5, abs=1e-12)
    identity = (rep["backtest_return"] - sum(rep["lines"].values())
                - rep["paper_return"])
    assert rep["residual"] == pytest.approx(identity, abs=1e-15)
    assert abs(rep["residual"]) <= attribution.BRIDGE_TOLERANCE
    assert rep["identity_ok"] is True


def test_bridge_puts_an_unexplained_gap_in_the_residual():
    """A divergence no line accounts for must land in residual and fail the
    identity — never be absorbed into a line to make the table balance."""
    rep = attribution.reconcile(_bridge_state(100_500.0), _bridge_predicted())
    assert rep["identity_ok"] is False
    assert rep["residual"] == pytest.approx(0.01 - 4.92e-5 - 0.005, abs=1e-12)


def test_bridge_counts_the_floor_excess_only_above_the_modelled_bps():
    """A trade large enough that commission_bps exceeds the floor contributes
    nothing: there is no divergence from the backtest's model to explain."""
    state = _bridge_state(101_000.0)
    state["trades"][0].update({"shares": 100_000, "fill": 10.0,
                               "commission": 800.0})   # 1,000,000 * 8bps
    rep = attribution.reconcile(state, _bridge_predicted())
    assert rep["lines"]["commission_floor"] == 0.0


def test_bridge_refuses_a_window_it_cannot_walk():
    state = _bridge_state(100_995.08)
    apart = pd.Series([100_000.0, 101_000.0],
                      index=pd.to_datetime(["2027-01-05", "2027-06-30"]))
    with pytest.raises(ValueError, match="common"):
        attribution.reconcile(state, apart)
```

`tests/test_attribution.py` already imports `numpy as np`, `pandas as pd`, `pytest` and `attribution`; nothing new is needed at the top of the file.

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_attribution.py -v -k bridge`

Expected: FAIL — all five collect and error with `AttributeError: module 'trading_algo.attribution' has no attribute 'BRIDGE_LINES'` / `... has no attribute 'reconcile'`.

- [ ] **Step 3: Implement**

First change the import block at `trading_algo/attribution.py:20-27` from:

```python
from __future__ import annotations

import math

import pandas as pd

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0
TRADING_DAYS = 252
```

to:

```python
from __future__ import annotations

import math

import pandas as pd

from . import tca
from .regions import get_region

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0
TRADING_DAYS = 252

# The reconciliation bridge (spec §5). 1 basis point on total return.
BRIDGE_TOLERANCE = 1e-4

BRIDGE_LINES = ("dividends", "cash_interest", "fill_convention", "commission_floor",
                "stamp_duty", "exposure_gap", "rebalance_timing", "fx_translation")
```

Then append to the end of `trading_algo/attribution.py`:

```python
def _commission_bps(region_key: str | None) -> float | None:
    """The bps rate the BACKTEST charges for this region, or None if unknown."""
    try:
        return float(get_region(region_key).commission_bps)
    except Exception:
        return None


def reconcile(paper_state: dict, predicted_equity: pd.Series) -> dict:
    """Walk from the backtest's total return to the paper book's, line by line.

    Every line is a SIGNED return fraction of the book's equity at the window
    start, positive when the cause put the BACKTEST ahead:

        backtest_return - sum(lines) - paper_return = residual

    A line is non-zero only where the two engines treat that cause DIFFERENTLY.
    A cost both charge identically (stamp duty; the slippage inside
    `fees.round_trip_cost_rate`) nets to zero and is reported as 0.0 with its
    measured paper-side amount in `notes`, so the reader sees the money without
    the arithmetic double-counting it. Anything not yet named lands in
    `residual` rather than being absorbed into a line — spec §5 sets NO target
    for the residual; the honest sequence is fix, measure, then judge.

    Local-currency amounts are converted with the book's CURRENT `fx_snapshot`,
    not the rate on the trade date; that approximation is part of what the
    `fx_translation` line will eventually carry, and until then it sits in the
    residual.
    """
    eh = paper_state.get("equity_history") or []
    paper = pd.Series({pd.Timestamp(d): float(v) for d, v in eh}).sort_index()
    pred = predicted_equity.dropna().sort_index()
    common = paper.index.intersection(pred.index)
    if len(common) < 2:
        raise ValueError("bridge needs >= 2 dates common to the book and the backtest")

    start, end = common[0], common[-1]
    paper_return = float(paper.loc[end] / paper.loc[start] - 1.0)
    backtest_return = float(pred.loc[end] / pred.loc[start] - 1.0)
    start_equity = float(paper.loc[start])

    snap = paper_state.get("fx_snapshot") or {}
    shortfall = floor_excess = duty = 0.0
    for t in paper_state.get("trades") or []:
        d = pd.Timestamp(t.get("date"))
        if d < start or d > end:
            continue
        shares, fill = float(t.get("shares") or 0.0), t.get("fill")
        if not shares or fill is None:
            continue
        rate = float(snap.get(t.get("currency")) or 1.0)
        if t.get("decision"):
            shortfall += tca.implementation_shortfall(
                t["decision"], fill, shares, t.get("side", "BUY")) * rate
        bps = _commission_bps(t.get("region"))
        if bps is not None:
            modelled = shares * float(fill) * bps / 1e4
            floor_excess += max(0.0, float(t.get("commission") or 0.0) - modelled) * rate
        duty += float(t.get("stamp_duty") or 0.0) * rate

    lines = {name: 0.0 for name in BRIDGE_LINES}
    lines["fill_convention"] = shortfall / start_equity
    lines["commission_floor"] = floor_excess / start_equity

    notes = {
        "dividends": "0.0 — the paper ledger credits no dividend yet; the "
                     "backtest's prices are already total-return",
        "cash_interest": "0.0 — the paper book accrues no interest on idle cash "
                         "yet; the backtest applies fees.idle_cash_credit",
        "stamp_duty": f"0.0 — both engines charge it; the paper book paid "
                      f"{duty:,.2f} (base) over this window",
        "exposure_gap": "0.0 — needs the gated target as of each rebalance, "
                        "which needs prices this signature does not carry",
        "rebalance_timing": "0.0 — needs the month-end counterfactual book",
        "fx_translation": "0.0 — both engines translate; the difference (and "
                          "this bridge's current-rate approximation) is not yet isolated",
    }

    residual = backtest_return - sum(lines.values()) - paper_return
    return {"start": str(start.date()), "end": str(end.date()),
            "backtest_return": backtest_return, "paper_return": paper_return,
            "lines": lines, "notes": notes, "residual": residual,
            "identity_ok": abs(residual) <= BRIDGE_TOLERANCE}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_attribution.py -v -k bridge`

Expected: PASS (5 passed)

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "attribution or tca or tearsheet or dashboard_api_book"`

Expected: PASS. `attribution.py` gains two module-level imports (`tca`, `regions.get_region`); neither imports `attribution`, so there is no cycle. No existing test's premise changes.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/attribution.py tests/test_attribution.py
git commit -m "feat(attribution): reconciliation bridge from backtest return to paper return

A signed, line-by-line walk with the identity as its acceptance criterion:
backtest_return - sum(lines) - paper_return = residual, holding to 1bp.
Lines are non-zero only where the two engines differ, so a cost both charge
nets to zero and is reported in notes; anything not yet named lands in the
residual instead of being absorbed. dividends and cash_interest read zero
with a note until the paper ledger credits them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `format_bridge`, `paper_trade --reconcile`, and the monthly trigger

**Files:**
- Modify: `trading_algo/attribution.py` (append `format_bridge` at end of file)
- Modify: `trading_algo/paper_trade.py:1151` (insert `reconcile_status` between `_print_tracking_diagnosis` and `promotion_status`), `trading_algo/paper_trade.py:1185-1186` (argparse), `trading_algo/paper_trade.py:1207-1208` (dispatch)
- Modify: `.github/workflows/monthly-report.yml:67-68`
- Test: `tests/test_attribution.py`, `tests/test_paper_trade.py`

**Interfaces:**
- Consumes: `attribution.reconcile(paper_state: dict, predicted_equity: pd.Series) -> dict`; `attribution.BRIDGE_LINES`; `attribution.BRIDGE_TOLERANCE`; `portfolio_backtest.run_portfolio_backtest(regions=None, synthetic=False, start=cfg.START, end=None, point_in_time=False, params=None, allocations=None) -> dict`
- Produces:
  - `attribution.format_bridge(report: dict, currency: str = "AUD", initial: float = 0.0) -> str`
  - `paper_trade.reconcile_status(account: str, synthetic: bool) -> None`
  - CLI flag `python -m trading_algo.paper_trade --account <a> --reconcile [--synthetic]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_attribution.py`:

```python
def test_format_bridge_prints_every_line_and_the_verdict():
    rep = attribution.reconcile(_bridge_state(100_995.08), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD", 100_000.0)
    for name in attribution.BRIDGE_LINES:
        assert name.replace("_", " ") in text, f"{name} missing from the table"
    assert "backtest return" in text and "paper return" in text
    assert "residual" in text
    assert "identity HOLDS" in text
    assert "AUD" in text
    # the commission floor cost A$4.92 on a 100,000 book -> the money column
    assert "5" in text


def test_format_bridge_says_so_when_the_identity_fails():
    rep = attribution.reconcile(_bridge_state(100_500.0), _bridge_predicted())
    text = attribution.format_bridge(rep, "AUD", 100_000.0)
    assert "identity DOES NOT HOLD" in text
    assert "note [dividends]" in text       # the unnamed lines explain themselves
```

Append to `tests/test_paper_trade.py`:

```python
def test_reconcile_flag_dispatches_to_the_bridge(monkeypatch):
    """`--reconcile` must reach reconcile_status, alongside --tca/--attribution."""
    seen = {}
    monkeypatch.setattr(pt, "reconcile_status",
                        lambda account, synthetic: seen.update(
                            account=account, synthetic=synthetic))
    pt.main(["--account", "full", "--reconcile", "--synthetic"])
    assert seen == {"account": "full", "synthetic": True}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_attribution.py::test_format_bridge_prints_every_line_and_the_verdict tests/test_paper_trade.py::test_reconcile_flag_dispatches_to_the_bridge -v`

Expected: FAIL — `AttributeError: module 'trading_algo.attribution' has no attribute 'format_bridge'`, and `AttributeError: <module 'trading_algo.paper_trade'> has no attribute 'reconcile_status'`.

- [ ] **Step 3: Implement**

Append to the end of `trading_algo/attribution.py`:

```python
def format_bridge(report: dict, currency: str = "AUD", initial: float = 0.0) -> str:
    """Fixed-width text table: line name, return fraction, bps/yr, base amount.

    `initial` is the book's starting equity in `currency`; pass 0.0 to print the
    return and bps columns only. The verdict line states whether the named lines
    close the identity to BRIDGE_TOLERANCE, and every zero line carries its note
    so a reader can see WHY it is zero rather than assuming it is nothing.
    """
    start, end = pd.Timestamp(report["start"]), pd.Timestamp(report["end"])
    years = max((end - start).days / 365.25, 1e-9)
    rows = [("backtest return", report["backtest_return"])]
    rows += [(n.replace("_", " "), report["lines"][n]) for n in BRIDGE_LINES]
    rows += [("residual", report["residual"]), ("paper return", report["paper_return"])]

    width = 22 + 11 + 10 + 14
    out = [f"  window {report['start']} -> {report['end']}",
           f"{'line':<22}{'return':>11}{'bps/yr':>10}{currency:>14}",
           "-" * width]
    for name, val in rows:
        out.append(f"{name:<22}{val:>10.4%} {val / years * 1e4:>9.0f}"
                   f"{val * initial:>14,.0f}")
    out.append("-" * width)
    out.append(f"identity {'HOLDS' if report['identity_ok'] else 'DOES NOT HOLD'} "
               f"— residual {report['residual'] * 1e4:+.2f}bps "
               f"(tolerance {BRIDGE_TOLERANCE * 1e4:.0f}bp)")
    for name, note in (report.get("notes") or {}).items():
        out.append(f"  note [{name}] {note}")
    return "\n".join(out)
```

Insert into `trading_algo/paper_trade.py` at line 1151 (between the end of `_print_tracking_diagnosis` and `def promotion_status`):

```python
def reconcile_status(account: str, synthetic: bool) -> None:
    """Print the backtest -> paper reconciliation bridge (spec §5).

    Same predicted curve `--attribution` builds: a backtest over the SAME window
    the book traded, sliced to the book's dates and re-based to its starting
    equity — no hindsight refetch (invariant #1).
    """
    from .portfolio_backtest import run_portfolio_backtest

    state = load_state(account)
    eh = state.get("equity_history", [])
    print("=" * 52)
    print(f"  Reconciliation bridge — account '{account}'")
    print("=" * 52)
    if len(eh) < 2:
        print("  Not enough history yet — run a daily update first.")
        return
    try:
        res = run_portfolio_backtest(synthetic=synthetic, end=eh[-1][0],
                                     allocations=state.get("allocations"))
        eq = res["equity"]
        window = eq[eq.index >= pd.Timestamp(eh[0][0])]
        predicted = window / float(window.iloc[0]) * float(state["initial_capital_base"])
        rep = attribution.reconcile(state, predicted)
    except Exception as exc:        # real data needs network; synthetic is offline
        print(f"  bridge unavailable: {exc}")
        return
    print(attribution.format_bridge(
        rep, state.get("base_currency", cfg.BASE_CURRENCY),
        float(state["initial_capital_base"])))


```

Insert into the argparse block, immediately after the `--attribution` argument at `trading_algo/paper_trade.py:1185-1186`:

```python
    ap.add_argument("--reconcile", action="store_true",
                    help="reconciliation bridge: the signed, line-by-line walk "
                         "from the backtest's return to this book's (spec §5)")
```

Insert into the dispatch chain, immediately after the `elif args.attribution:` branch at `trading_algo/paper_trade.py:1207-1208`:

```python
    elif args.reconcile:
        reconcile_status(args.account, args.synthetic)
```

In `.github/workflows/monthly-report.yml`, change the comment and loop at lines 67-68 from:

```yaml
            # F11 (execution TCA), F3 (live vs backtest), F10 (promotion gate).
            # Fenced: these print fixed-width tables, not markdown.
            for report in tca attribution promotion; do
```

to:

```yaml
            # F11 (execution TCA), F3 (live vs backtest), the §5 reconciliation
            # bridge, F10 (promotion gate).
            # Fenced: these print fixed-width tables, not markdown.
            for report in tca attribution reconcile promotion; do
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_attribution.py::test_format_bridge_prints_every_line_and_the_verdict tests/test_attribution.py::test_format_bridge_says_so_when_the_identity_fails tests/test_paper_trade.py::test_reconcile_flag_dispatches_to_the_bridge -v`

Expected: PASS (3 passed)

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "attribution or paper_trade or workflow_sanity"`

Expected: PASS. `tests/test_workflow_sanity.py` asserts nothing about the report loop's contents, so the workflow edit changes no test premise.

Then confirm the CLI end to end, offline, in a scratch state dir — never against `state/`:

```bash
export MOMENTUM_STATE_DIR=/tmp/bridge-check FX_STATE_DIR=/tmp/bridge-check
mkdir -p "$MOMENTUM_STATE_DIR"
python3 -m trading_algo.paper_trade --account bridge --init --capital 100000 --synthetic
python3 -m trading_algo.paper_trade --account bridge --synthetic
python3 -m trading_algo.paper_trade --account bridge --reconcile --synthetic
```

Expected: a bridge table with all eight lines, a residual, and an `identity DOES NOT HOLD` verdict — a one-bar book has almost no named causes, so a large residual here is the correct, honest output, not a failure.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/attribution.py trading_algo/paper_trade.py tests/test_attribution.py tests/test_paper_trade.py .github/workflows/monthly-report.yml
git commit -m "feat(paper): ship the bridge as --reconcile and run it monthly

format_bridge prints the walk as a fixed-width table in return, bps/yr and
base currency, with every zero line carrying the note that says why it is
zero. --reconcile builds the same no-hindsight predicted curve --attribution
uses, and joins the monthly report loop next to tca/attribution/promotion.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Make the regression gate cost-sensitive

**Files:**
- Modify: `trading_algo/ci_regression.py:32-38` (TOL), `trading_algo/ci_regression.py:41-60` (`synthetic_metrics`)
- Modify: `trading_algo/_regression_baseline.json`
- Test: `tests/test_backtest_regression.py`

**Interfaces:**
- Consumes: `backtest.run_backtest(...)["total_cost_fraction"]` (already returned, `trading_algo/backtest.py:208`); `fees.turnover_cost(region, turnover, buy_turnover, impact=0.0) -> float`
- Produces: `ci_regression.TOL["total_cost_fraction"] = 1e-4`; a `total_cost_fraction` entry in each sleeve of `synthetic_metrics()` and of `_regression_baseline.json`

**The defect, measured.** `TOL["CAGR"] = 0.02` and deleting every transaction cost moves the synthetic sleeves' CAGR by 0.0022 (US) to 0.0114 (FTSE) — all inside tolerance. Verified by monkeypatching `backtest.fees.turnover_cost` to return `0.0` and running `ci_regression.compare(baseline, current)`: it returns `[]`. The gate cannot see a dropped cost, which is precisely the regression it is about to be asked to guard against while stages 2-4 move every cost in the repo.

**Why a hand-edit rather than `--update`.** Adding a key is not a re-baseline. `--update` would rewrite all twelve committed numbers in the same diff, hiding whether any of them moved. Spec §11 and §14 allow exactly one re-baseline, at P4, with evidence attached; this is not it. The four existing values stay byte-identical and four lines are added.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_backtest_regression.py`:

```python
def test_baseline_records_a_nonzero_cost_per_sleeve():
    """Without a cost line in the baseline, `compare` has nothing to compare:
    CAGR alone moves less than TOL when every transaction cost is deleted."""
    baseline = ci_regression.load_baseline()
    for key, sleeve in baseline["sleeves"].items():
        assert "total_cost_fraction" in sleeve, f"{key} has no cost line"
        assert sleeve["total_cost_fraction"] > 0, f"{key} back-tested at zero cost"


def test_gate_fails_when_transaction_costs_are_deleted(monkeypatch):
    """Invariant #2 (costs always on), enforced by the gate rather than by
    convention. Deleting every transaction cost passes on CAGR/Vol/Sharpe/DD
    alone — measured: the sleeve CAGRs move 0.0022 to 0.0114 against TOL 0.02."""
    from trading_algo import backtest

    monkeypatch.setattr(backtest.fees, "turnover_cost", lambda *a, **k: 0.0)
    drift = ci_regression.compare(ci_regression.load_baseline(),
                                  ci_regression.synthetic_metrics())
    assert any("total_cost_fraction" in d for d in drift), (
        "the gate did not notice that every transaction cost was removed: "
        + ("\n".join(drift) or "(no drift at all)"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_backtest_regression.py::test_baseline_records_a_nonzero_cost_per_sleeve tests/test_backtest_regression.py::test_gate_fails_when_transaction_costs_are_deleted -v`

Expected: both FAIL. The first with `AssertionError: ASX has no cost line`. The second with `AssertionError: the gate did not notice that every transaction cost was removed: (no drift at all)`.

- [ ] **Step 3: Implement**

Change `trading_algo/ci_regression.py:32-38` from:

```python
TOL = {
    "CAGR": 0.02,
    "AnnVol": 0.02,
    "MaxDrawdown": 0.02,
    "Sharpe": 0.15,
}
```

to:

```python
TOL = {
    "CAGR": 0.02,
    "AnnVol": 0.02,
    "MaxDrawdown": 0.02,
    "Sharpe": 0.15,
    # Cumulative cost drag, as a fraction of NAV summed over the run. TIGHT on
    # purpose: deleting every transaction cost moves the synthetic sleeves' CAGR
    # by only 0.0022-0.0114, well inside the 0.02 above, so CAGR alone cannot see
    # a dropped cost. This line is what enforces invariant #2 in CI.
    "total_cost_fraction": 1e-4,
}
```

Change the `sleeves` comprehension in `synthetic_metrics()` at `trading_algo/ci_regression.py:53-56` from:

```python
        "sleeves": {
            k: {"CAGR": s["metrics"]["CAGR"], "MaxDrawdown": s["metrics"]["MaxDrawdown"]}
            for k, s in res["sleeves"].items()
        },
```

to:

```python
        "sleeves": {
            k: {"CAGR": s["metrics"]["CAGR"],
                "MaxDrawdown": s["metrics"]["MaxDrawdown"],
                # run_backtest already returns this; rounded to keep the
                # committed baseline readable and diffable.
                "total_cost_fraction": round(float(s["total_cost_fraction"]), 6)}
            for k, s in res["sleeves"].items()
        },
```

Then hand-edit `trading_algo/_regression_baseline.json`, adding ONLY the four new lines (every existing number stays exactly as it is):

```json
{
  "portfolio": {
    "CAGR": 0.0911,
    "AnnVol": 0.0511,
    "Sharpe": 1.05,
    "MaxDrawdown": -0.0864
  },
  "sleeves": {
    "ASX": {
      "CAGR": 0.0802,
      "MaxDrawdown": -0.1071,
      "total_cost_fraction": 0.087191
    },
    "US": {
      "CAGR": 0.112,
      "MaxDrawdown": -0.1099,
      "total_cost_fraction": 0.028602
    },
    "FTSE": {
      "CAGR": 0.1119,
      "MaxDrawdown": -0.099,
      "total_cost_fraction": 0.148817
    },
    "TSX": {
      "CAGR": 0.0772,
      "MaxDrawdown": -0.1519,
      "total_cost_fraction": 0.04798
    }
  }
}
```

Those four values are the measured output of `synthetic_metrics()` at HEAD, reproducible run-to-run.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_backtest_regression.py::test_baseline_records_a_nonzero_cost_per_sleeve tests/test_backtest_regression.py::test_gate_fails_when_transaction_costs_are_deleted -v`

Expected: PASS. The second test's `drift` lists all four sleeves, e.g. `sleeves.FTSE.total_cost_fraction: 0.148817 -> 0.0 (|Δ| > tol 0.0001)`.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/test_backtest_regression.py -q`

Expected: PASS. `test_synthetic_backtest_matches_baseline` would fail with `sleeves.ASX.total_cost_fraction: new metric not in baseline` if the JSON were not updated in Step 3 — that is the check that the hand-edit is complete, and it needs no change to the test itself.

Then confirm the gate's own CLI agrees, in a scratch state dir:

```bash
export MOMENTUM_STATE_DIR=/tmp/gate-check FX_STATE_DIR=/tmp/gate-check
python3 -m trading_algo.ci_regression --check
```

Expected: `OK: synthetic backtest matches baseline within tolerance.`

- [ ] **Step 6: Commit**

```bash
git add trading_algo/ci_regression.py trading_algo/_regression_baseline.json tests/test_backtest_regression.py
git commit -m "test(ci): make the regression gate fail when a cost is dropped

Deleting every transaction cost moved the synthetic sleeves' CAGR by 0.0022
to 0.0114 against TOL 0.02, so compare() returned no drift at all. The gate
now records cumulative cost drag per sleeve at 1e-4 tolerance, which is what
enforces invariant #2 in CI before stages 2-4 start moving costs.

The four existing baseline numbers are untouched: this adds a key, it is not
the single re-baseline spec §14 reserves for P4.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

# Phase 3 — Conventions: what both engines compute

Next-day-close fills, dividends, cash interest, and the per-order commission floor. These change headline numbers deliberately. Do NOT re-baseline the regression gate here; Task 20 does it once.

---

### Task 9: Next-day-close fills in the backtest

**Files:**
- Modify: `trading_algo/backtest.py:1-15` (module docstring — it states the old convention)
- Modify: `trading_algo/backtest.py:121-198` (the daily loop)
- Test: `tests/test_backtest.py`
- Modify (premise changed by this task): `tests/test_backtest_regression.py:47-55`

**Interfaces:**
- Consumes: nothing from earlier tasks. `fees.turnover_cost(region, turnover, buy_turnover, impact=0.0)` and `fees.idle_cash_credit(net_exposure, days, annual_rate)` as they stand today.
- Produces: `run_backtest(...)["weights"][D]` now means **the book at D's close** (post-trade), i.e. the book that earns D+1's return. Previously it meant the book that earned D's own return. Task 5–8's bridge reads this key; nothing else in `trading_algo/` does (grepped: only tests consume it).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_backtest.py`:

```python
def test_return_is_earned_by_the_previous_bars_book(synth_asx, asx_region):
    """Today's return belongs to the book held THROUGH today, i.e. the one
    recorded at yesterday's close.

    A target decided at D_k's close cannot be filled at that close — that is the
    very price that produced the signal. It is executed at D_{k+1}'s close, so
    the first bar it earns a return on is D_{k+2}. Restated as an identity the
    simulator must satisfy on every bar:

        returns[D] == weights_hist[D_prev] . rets[D] - cost[D] + interest[D]

    Before this change the right-hand book was `weights_hist[D]`, which is the
    same thing as filling at the signal's own close.
    """
    import pytest

    from trading_algo import config as cfg
    from trading_algo import fees

    prices, index_px = synth_asx
    res = run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    weights_hist = res["weights"]
    costs = res["costs"]
    rets = prices.pct_change(fill_method=None)

    dates = list(res["returns"].index)
    checked = 0
    for prev, today in zip(dates, dates[1:]):
        book = weights_hist.get(prev)
        if book is None or book.empty:
            continue
        day = rets.loc[today].reindex(book.index).fillna(0.0)
        expected = float((book * day).sum())
        expected -= float(costs.get(today, 0.0))
        if cfg.CREDIT_IDLE_CASH and cfg.CASH_RATE_ANNUAL:
            expected += fees.idle_cash_credit(
                float(book.sum()), (today - prev).days, cfg.CASH_RATE_ANNUAL)
        assert float(res["returns"].loc[today]) == pytest.approx(expected, abs=1e-12), (
            f"bar {today.date()} was not earned by the book recorded at "
            f"{prev.date()}'s close")
        checked += 1
    assert checked > 100, "fixture produced too few invested bars to prove anything"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_backtest.py::test_return_is_earned_by_the_previous_bars_book -v`

Expected: FAIL — `assert 0.0034... == approx(0.0031...)` with the message `bar 2014-xx-xx was not earned by the book recorded at 2014-xx-xx's close`. Today `weights_hist[D]` is the *pre-drift* book that earned bar D, so the previous bar's record is a different (undrifted) set of weights and the products disagree.

- [ ] **Step 3: Implement**

Replace the module docstring's first bullet, `trading_algo/backtest.py:4-10`:

```python
- No lookahead, next-day-CLOSE execution: weights are decided at month-end D_k
  using data ≤ D_k, staged, and EXECUTED at the close of the next trading bar
  D_{k+1}. They are never filled at D_k's own close — that is the price that
  produced the signal, and no real order can touch it. The first bar a new
  target earns a return on is therefore D_{k+2}. The drawdown breaker stages a
  liquidation the same way, for the same reason. Targets come from the shared
  `strategy.compute_targets` — the same function paper trading uses.
```

Replace `trading_algo/backtest.py:121-198` (the whole `for i in range(1, len(dates)):` body up to and including the drift block) with:

```python
    for i in range(1, len(dates)):
        today = dates[i]

        # --- 1. today's return is earned by the book we were ALREADY holding ---
        # A target staged at the prior close is not in the book yet: it is filled
        # below, at TODAY's close, so it sits out this bar.
        day_rets = rets.loc[today].reindex(current_w.index).fillna(0.0)
        gross = float((current_w * day_rets).sum())
        r = gross
        # Interest on whatever was NOT invested over the day. `current_w` is the
        # book held THROUGH today, and its NET sum is the exposure — see
        # fees.idle_cash_credit on why net, not gross. Without this a flat book
        # earns 0% while metrics still charge it the RISK_FREE hurdle, which is a
        # double penalty worth +0.22 to +0.34 Sharpe (docs/SHARPE_RESEARCH.md §1).
        if CREDIT_IDLE_CASH and CASH_RATE_ANNUAL:
            interest = fees.idle_cash_credit(
                float(current_w.sum()), (today - dates[i - 1]).days,
                CASH_RATE_ANNUAL)
            r += interest
            total_cash_interest += interest

        # Drift the held weights to today's close — that is the book the fill
        # starts from, and the prices it happens at.
        if not current_w.empty:
            grown = current_w * (1 + day_rets)
            nav_growth = 1 + gross
            current_w = grown / nav_growth if nav_growth != 0 else grown

        # --- 2. execute the staged target AT TODAY'S CLOSE --------------------
        if pending is not None:
            names = current_w.index.union(pending.index)
            delta = (pending.reindex(names, fill_value=0.0)
                     - current_w.reindex(names, fill_value=0.0))
            turnover = float(delta.abs().sum())
            buy_turnover = float(delta.clip(lower=0).sum())
            # F6: per-name square-root market impact (fraction of NAV), added to
            # the one shared cost entrypoint (R1). Zero unless IMPACT_COEF is set.
            impact = 0.0
            if IMPACT_COEF and advd is not None:
                a = advd.loc[:today]
                if len(a):
                    a = a.iloc[-1]
                    v = (vols_frame.loc[:today].iloc[-1]
                         if vols_frame is not None and len(vols_frame.loc[:today])
                         else None)
                    nav = equity[-1]
                    for name, dw in delta[delta.abs() > 0].items():
                        impact += abs(dw) * fees.square_root_impact(
                            abs(dw) * nav, a.get(name), (v.get(name) if v is not None else float("nan")),
                            IMPACT_COEF)
            cost = fees.turnover_cost(region, turnover, buy_turnover, impact=impact)
            turnover_log.append((today, turnover))
            cost_log.append((today, cost))
            total_cost += cost
            r -= cost
            current_w = pending
            pending = None

        daily_ret.append(r)
        equity.append(equity[-1] * (1 + r))
        # The book as it stands at TODAY's close — the one that earns tomorrow's
        # return. A freshly executed target is recorded on its EXECUTION bar.
        weights_hist[today] = current_w

        # --- drawdown circuit breaker (decision at close, execute t+1) ---
        peak = max(peak, equity[-1])
        if halted:
            halt_days += 1
            cooldown -= 1
            if cooldown <= 0:
                halted = False
        elif max_drawdown_stop is not None and equity[-1] / peak - 1 <= -max_drawdown_stop:
            halted = True
            cooldown = cooldown_days
            halt_events += 1

        if halted:
            pending = CASH                       # liquidate at the NEXT close
        elif today in weight_schedule:
            # A target decided as-of `today` (D_k) is staged now and EXECUTED at
            # the close of D_{k+1}; it first earns a return on D_{k+2}.
            pending = weight_schedule[today]
```

Note the three structural points, which are the whole change: the return is computed before the fill, the fill diffs against the *drifted* book, and `weights_hist` now records the post-fill close book. The drift block moved up and its local `nav` was renamed `nav_growth` so it cannot shadow the impact block's NAV, which now legitimately sits after it.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_backtest.py::test_return_is_earned_by_the_previous_bars_book -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "backtest or consistency or parity or idle_cash or impact or adv_cap or portfolio or pit_impact or walkforward or delisting or sweep"`

Expected: PASS except `tests/test_backtest_regression.py::test_synthetic_backtest_matches_baseline`, whose premise this task deliberately changes — the committed baseline was produced under the old fill convention and CAGR now moves by roughly 0.4pp/yr (spec §2). Per spec §6 the baseline is re-cut **once**, after stage 3, so do not regenerate it here. Mark it as knowingly stale instead, in `tests/test_backtest_regression.py:22-26`:

```python
@pytest.mark.xfail(
    reason="Baseline predates the next-day-close fill convention (Task 9) and "
           "the per-order commission floor (Task 13). Re-cut once in Task 20, "
           "with the bridge report attached — spec §6. Remove this marker there.",
    strict=False)
def test_synthetic_backtest_matches_baseline(current):
    baseline = ci_regression.load_baseline()
    drift = ci_regression.compare(baseline, current)
    assert not drift, "synthetic backtest drifted from baseline:\n" + "\n".join(drift)
```

Also update the two docstrings in that file whose statement of the convention is now wrong — `tests/test_backtest_regression.py:47-55`, the docstring of `test_target_first_affects_equity_at_t_plus_one` (its assertions still hold and must not change):

```python
def test_target_first_affects_equity_at_t_plus_one(synth_asx, asx_region):
    """Execution-timing invariant: a month-end target computed as-of D_k must be
    EXECUTED at D_{k+1}'s close, and must never be in the book at D_k or earlier
    (no lookahead — invariant #1).

    `weights_hist[D]` is the book as it stands at D's CLOSE — after any fill on
    that bar, and therefore the book that earns bar D+1's return. So a target
    appearing at `weights_hist[D_{k+1}]` is direct evidence that it was filled at
    D_{k+1}'s close, one bar after the signal. Pinned here so neither the t+2 lag
    bug nor a return to same-close fills can come back.
    """
```

If `tests/test_circuit_breaker_trips_and_limits_drawdown` fails on the `tight["metrics"]["MaxDrawdown"] >= off[...]` line, that is a real consequence, not noise: a halted book now carries one extra bar of exposure before it can liquidate. Report it rather than loosening the assertion — it belongs to Task 14, which owns the breaker.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/backtest.py tests/test_backtest.py tests/test_backtest_regression.py
git commit -m "fix(backtest): execute staged targets at the next bar's close

A target decided at D_k's close was being filled at that same close — the
price that produced the signal. It is now executed at D_{k+1}'s close: the
bar's return accrues to the book actually held, the fill diffs against the
drifted book, and weights_hist[D] becomes the book at D's close.

The regression baseline moves with this and is re-cut once, later, per
spec section 6; the gate is marked xfail until then.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Next-day-close fills in the paper book

**Files:**
- Modify: `trading_algo/paper_trade.py:686-706` (add two helpers above `_should_rebalance`)
- Modify: `trading_algo/paper_trade.py:814-873` (the per-sleeve branch of `_run_daily_locked`)
- Test: `tests/test_paper_trade.py`
- Modify: `tests/conftest.py` (add the `paper_cycle` fixture)
- Modify (premise changed by this task): `tests/test_paper_trade.py`, `tests/test_consistency.py:150-187`, `tests/test_dashboard_api_book.py:15-28`, `tests/test_dashboard.py:16-22`, `tests/test_dashboard_valuation.py:8-14`, `tests/test_dashboard_terminal.py:54`, `tests/test_dashboard_colour_convention.py:305,323,398,413,496`, `tests/test_experimental_books.py:125,136,151,153,155`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `paper_trade._stage_target(sleeve: dict, today: str, targets: pd.Series, frozen: set[str] | None = None) -> None`
  - `paper_trade._fill_pending(region, sleeve: dict, px: pd.Series, today: str, trade_log: list) -> bool`
  - New sleeve state key `sleeve["pending_target"] = {"date": str, "weights": dict[str, float], "frozen": list[str]}`, absent when nothing is staged. Task 5–8's bridge reads `sleeve["pending_target"]["date"]` as the decision date and `sleeve["last_rebalance_date"]` as the **fill** date.
  - `tests/conftest.py::paper_cycle` — `cycle(account, sessions=2) -> state`.

This is the one structural change in the block. It is ~35 lines of production code, above the ~30-line signal in spec §13, and the reason is stated rather than pushed through: a decision has to survive a process restart to be filled on a later run, so it must be persisted, and persisting it is the change. There is no smaller version that still moves the fill off the decision's own bar.

- [ ] **Step 1: Write the failing test**

First add the shared fixture to `tests/conftest.py` (the frozen synthetic panel returns the same last bar on every call, so without it no test could ever reach a fill bar):

```python
@pytest.fixture
def paper_cycle(monkeypatch):
    """Advance a paper book across DISTINCT synthetic sessions.

    Fills land on the bar after the decision, so a book needs two runs on two
    different bars to open a position — and `data.synthetic_region` hands back
    the same fixed panel on every call. This serves that panel one session
    longer each time the book is advanced.

    `cycle(account)` runs one full decide -> fill cycle. Calling
    `paper_trade.run_daily` directly stays on the CURRENT session, which is how
    a test reproduces the engine firing several times in one day.
    """
    from trading_algo import paper_trade as pt

    real = pt.latest_region_data
    clock = {"session": 0}
    headroom = 8                      # sessions of runway before the panel ends

    def advancing(region, synthetic):
        prices, index_px = real(region, synthetic)
        if not synthetic:
            return prices, index_px
        cut = min(len(prices), len(prices) - headroom + clock["session"])
        return prices.iloc[:cut], index_px.loc[:prices.index[cut - 1]]

    monkeypatch.setattr(pt, "latest_region_data", advancing)

    def cycle(account, sessions=2):
        for _ in range(sessions):
            pt.run_daily(account, synthetic=True)
            clock["session"] += 1
        return pt.load_state(account)

    return cycle
```

Then append to `tests/test_paper_trade.py`:

```python
def test_a_decision_fills_on_the_next_session_not_its_own(account, paper_cycle):
    """The book decides at the latest close and fills at the NEXT one.

    Filling at the close that produced the signal is not executable by any real
    broker, and it is what made the paper book's fills disagree with the
    backtest's staged targets.
    """
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})

    state = paper_cycle(account, sessions=1)          # session 1: decide only
    sleeve = state["sleeves"]["US"]
    assert state["trades"] == [], "a target must not fill at the close that produced it"
    assert not sleeve["positions"]
    pending = sleeve.get("pending_target")
    assert pending and pending["weights"], "the target must be staged for the next close"
    decided_on = pending["date"]

    state = paper_cycle(account, sessions=1)          # session 2: fill
    sleeve = state["sleeves"]["US"]
    assert state["trades"], "the staged target must fill on the next session"
    assert "pending_target" not in sleeve
    assert sleeve["positions"]
    for t in state["trades"]:
        assert t["date"] > decided_on, (
            f"{t['ticker']} filled on {t['date']}, the bar its own signal used")
    assert sleeve["last_rebalance_date"] > decided_on


def test_a_second_run_on_the_same_bar_does_not_fill(account, paper_cycle):
    """The engine fires up to three times a day, once per regional close. A
    pending target waits for a new SESSION, not merely for the next run."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    paper_cycle(account, sessions=1)
    pt.run_daily(account, synthetic=True)             # same bar, second pass
    state = pt.load_state(account)
    assert state["trades"] == []
    assert state["sleeves"]["US"].get("pending_target")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_paper_trade.py::test_a_decision_fills_on_the_next_session_not_its_own tests/test_paper_trade.py::test_a_second_run_on_the_same_bar_does_not_fill -v`

Expected: FAIL on the first assertion of each — `AssertionError: a target must not fill at the close that produced it` (`state["trades"]` is non-empty after one session, because `rebalance_sleeve` runs inline at `paper_trade.py:868`).

- [ ] **Step 3: Implement**

Insert immediately above `def _should_rebalance` at `trading_algo/paper_trade.py:686`:

```python
def _stage_target(sleeve: dict, today: str, targets: pd.Series,
                  frozen: set[str] | None = None) -> None:
    """Record a decision made at TODAY's close, for filling at the next one.

    The book cannot trade at the price that produced its own signal: a target
    decided on bar D_k is executable no earlier than D_{k+1}'s close, which is
    exactly what `backtest.py` stages. Persisting it in the sleeve is what lets
    the decision survive to the next run — the engine is a fresh process each
    time, so an in-memory target would simply be lost.
    """
    sleeve["pending_target"] = {
        "date": today,
        "weights": {str(t): float(w) for t, w in targets.items()},
        "frozen": sorted(frozen or ()),
    }


def _fill_pending(region, sleeve: dict, px: pd.Series, today: str,
                  trade_log: list) -> bool:
    """Execute the target staged on an EARLIER bar, at today's close.

    Returns True when a fill was attempted. A target staged on today's own bar
    is left alone: that is the whole convention, and the engine runs up to three
    times a day against the same bar.
    """
    pending = sleeve.get("pending_target")
    if not pending or str(pending.get("date", "")) >= today:
        return False
    targets = pd.Series(pending.get("weights") or {}, dtype=float)
    rebalance_sleeve(region, sleeve, targets, px, today, trade_log,
                     frozen=set(pending.get("frozen") or ()))
    sleeve["last_rebalance_date"] = today          # the FILL date, not the decision's
    sleeve.pop("pending_target", None)
    return True
```

Then replace `trading_algo/paper_trade.py:814-873` — from `params = _account_params(state, region)` down to and including the `else: status = "held" if ...` line — with:

```python
        params = _account_params(state, region)
        status = None                       # why the sleeve ended this run as it did

        if halted and (sleeve["positions"] or sleeve.get("pending_target")):
            # A halted book's only legal target is cash. Overwrite whatever was
            # staged before the breaker tripped so a stale buy can never fill,
            # and stage the liquidation for the next close — a breaker decides at
            # a close like everything else, and cannot fill at that same close.
            print(f"  [{k}] ⛔ drawdown halt — staging a liquidation for the "
                  f"next close.")
            _stage_target(sleeve, today, pd.Series(dtype=float))

        # Execute the target staged on an EARLIER bar, at today's close. Every
        # fill in this book therefore lands one session after its decision.
        if rate_ok and _fill_pending(region, sleeve, px_today, today,
                                     state["trades"]):
            rebalanced_this_run = True

        if halted:
            # Do NOT stamp last_rebalance_month while halted: that would calendar-
            # pin the sleeve flat until the next month even after the cooldown
            # clears. Re-entry is cooldown-driven (month/date cleared on resume).
            status = "cash:halted"
        elif not rate_ok:
            # No base-currency rate → the min-viable gate and the mark can't be
            # sized. Hold cash rather than let `NaN < MIN_VIABLE` (False) fall
            # through and trade on an unvaluable book.
            print(f"  [{k}] ⚠ no valuation rate for {region.currency} — holding cash.")
            notifications.notify(
                "fx_unavailable",
                f"[{account}] {k} valuation rate for {region.currency} unavailable "
                f"— holding cash this run",
                level="alert", account=account, region=k, currency=region.currency)
            status = "cash:fx-unavailable"
        elif _should_rebalance(sleeve, today, this_month):
            # Reached only when rate_ok is True (the `not rate_ok` branch above
            # was skipped), so `rate` is a positive float, not None.
            assert rate is not None
            eq_base_pre = sleeve_equity_local(sleeve, px_today) * rate
            if eq_base_pre < cfg.MIN_VIABLE_EQUITY_BASE:
                print(f"  [{k}] below min viable size "
                      f"({eq_base_pre:,.0f} {cfg.BASE_CURRENCY}) — holding cash.")
                status = "cash:below-min"
            else:
                elig, dq = data_quality.eligible(prices, region, prices.index[-1])
                if dq.excluded:
                    print(f"  [{k}] data-quality: freezing "
                          + ", ".join(f"{t} ({dq.reasons[t]})" for t in sorted(dq.excluded)))
                targets = strategy.compute_targets(prices, index_px, params,
                                                   eligible=elig)
                if targets.empty:
                    reason = _empty_target_reason(prices, index_px, params, elig)
                    print(f"  [{k}] flat — {reason} (holding cash).")
                    status = f"cash:{reason}"
                    # Persist WHY across the days that follow. The daily status
                    # is overwritten with the generic 'cash:idle' on every
                    # non-rebalance day, which erases the difference between
                    # "the regime gate said cash" (correct, and the audit should
                    # stay quiet) and "the feed was broken" (an emergency).
                    sleeve["last_flat_reason"] = reason
                else:
                    status = "rebalanced"
                    sleeve.pop("last_flat_reason", None)
                _stage_target(sleeve, today, targets, frozen=dq.excluded)
            sleeve["last_rebalance_month"] = this_month
        else:
            status = "held" if sleeve["positions"] else "cash:idle"
```

Three consequences worth stating in the commit: `rebalanced_this_run` now means *a fill happened*, not *a decision happened*, which is the correct gate for the cash-only allocation true-up below it; `last_rebalance_date` is stamped at the fill, `last_rebalance_month` at the decision, so `_should_rebalance`'s existing `MIN_REBALANCE_GAP_DAYS` guard keeps a month-boundary decision from landing a day after the previous fill; and a halted book overwrites its pending target before anything can fill it.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_paper_trade.py::test_a_decision_fills_on_the_next_session_not_its_own tests/test_paper_trade.py::test_a_second_run_on_the_same_bar_does_not_fill -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "paper or dashboard or consistency or experimental or alloc_rebalance or breaker_alert or verify or promotion"`

Every test that opened a position with a single `run_daily` now needs a full cycle. The mechanical conversion, at each failing site: add `paper_cycle` to the test's parameters and replace `pt.run_daily(<acct>, synthetic=True)` with `paper_cycle(<acct>)`. The sites are:

- `tests/test_paper_trade.py:56, 84, 99, 106, 117, 136, 151, 159, 174, 191, 207, 264, 292, 309, 330, 381, 397, 414, 429`
- `tests/test_consistency.py:171`
- `tests/test_dashboard.py:21`, `tests/test_dashboard_valuation.py:13`, `tests/test_dashboard_terminal.py:54`
- `tests/test_dashboard_api_book.py:22, 24`
- `tests/test_dashboard_colour_convention.py:305, 323, 398, 413, 496`
- `tests/test_experimental_books.py:125, 136, 151, 153, 155`

Leave `tests/test_alloc_rebalance.py:72` and `tests/test_breaker_alert.py:23, 34` alone — neither asserts on a fill.

Four tests need more than the substitution, and all four are premise changes this task owns:

`tests/test_paper_trade.py:49-68` — one run no longer produces a ledger:
```python
def test_init_refuses_to_overwrite_a_live_book(account, paper_cycle):
    pt.init_account(account, capital=100_000, synthetic=True)
    paper_cycle(account)                     # decide, then fill
    before = pt.load_state(account)
    assert before["trades"], "need a book with history to prove it is protected"
    ...
```

`tests/test_paper_trade.py:157-168` — the halt liquidation is staged, so it needs its own cycle:
```python
def test_drawdown_halt_liquidates(account, paper_cycle):
    """A halted book stages a liquidation and fills it at the NEXT close — a
    breaker decides at a close like anything else and cannot fill at that one."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)                             # opens positions
    state = pt.load_state(account)
    state["risk_halted"] = True
    state["halt_cooldown"] = 5
    pt.save_state(account, state)
    state = paper_cycle(account)                     # stage, then fill the exit
    assert all(not s["positions"] for s in state["sleeves"].values())
    assert state["risk_halted"] is True
```

`tests/test_paper_trade.py:170-184` — keep the two bare `run_daily` calls (they must share a session) but open the book with a cycle:
```python
def test_cooldown_counts_market_days_not_runs(account, paper_cycle):
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)
    state["risk_halted"] = True
    state["halt_cooldown"] = 3
    state.pop("halt_last_day", None)
    pt.save_state(account, state)
    # Two runs land on the SAME synthetic report date -> one day of cooldown.
    pt.run_daily(account, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["halt_cooldown"] == 2               # dropped by ONE, not two
    assert state["risk_halted"] is True
```

`tests/test_consistency.py:150-187` — the spy captures the DECISION bar's prices, but the book is now built at the FILL bar's prices, so relative sizing must be checked against the prices actually executed. Those are in the ledger (`decision` is the pre-slippage close on the fill bar):
```python
    paper_trade.init_account("golden", 1_000_000, synthetic=True, allocations={"US": 1.0})
    paper_cycle("golden")                    # decide, then fill

    state = paper_trade.load_state("golden")
    sleeve = state["sleeves"]["US"]
    targets = seen["w"]
    held = sleeve["positions"]

    # No name invented outside the shared weight function.
    assert set(held).issubset(set(targets.index))

    # Sizing is compared at the prices the fills actually used — the book is
    # built one session after the decision, so the decision bar's prices are no
    # longer the ones it was sized at.
    fill_px = {t["ticker"]: float(t["decision"]) for t in state["trades"]
               if t.get("decision")}
    if not targets.empty and held and all(t in fill_px for t in held):
        values = {t: held[t] * fill_px[t] for t in held}
        top_by_weight = targets.sort_values(ascending=False).index[0]
        top_by_value = max(values, key=values.get)
        assert top_by_weight == top_by_value, (
            "relative sizing not preserved: paper's biggest holding isn't the "
            "biggest compute_targets weight")
```
(add `paper_cycle` to that test's parameters and drop the now-unused `seen["px"]` capture from the spy).

Expected after the conversions: PASS.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/paper_trade.py tests/conftest.py tests/test_paper_trade.py tests/test_consistency.py tests/test_dashboard.py tests/test_dashboard_valuation.py tests/test_dashboard_terminal.py tests/test_dashboard_api_book.py tests/test_dashboard_colour_convention.py tests/test_experimental_books.py
git commit -m "fix(paper): fill a decision at the next close, not its own

The book was executing at the same close that produced the signal, which
no broker can do and which the backtest never did. A decision is now
staged in the sleeve as pending_target and filled on the next session;
last_rebalance_date records the fill, last_rebalance_month the decision.
A halted book overwrites whatever was staged before it can fill.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Credit dividends in the paper ledger

**Files:**
- Modify: `trading_algo/data.py:170` (add `_download_dividends` and `dividends` after `load_prices`)
- Modify: `trading_algo/paper_trade.py` (add `_credit_dividends` above `_should_rebalance`; call it in the sleeve loop)
- Modify: `trading_algo/pnl.py:116-120` (`build_lots` skips non-fill rows)
- Modify: `trading_algo/verify.py:155-165` and `trading_algo/verify.py:318-323`
- Test: `tests/test_data_sources.py`, `tests/test_paper_trade.py`, `tests/test_pnl.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `paper_trade._stage_target` / `_fill_pending` from Task 9–10 only in the sense that the credit runs before them in the loop; no call.
- Produces:
  - `data.dividends(tickers: list[str], start: str, end: str | None = None, use_cache: bool = True) -> pd.DataFrame` — index = ex-date, columns = tickers, values = cash dividend per share in the quote's own units.
  - `data._download_dividends(tickers, start, end)` — the network seam, monkeypatched in tests.
  - `paper_trade._credit_dividends(region, sleeve: dict, today: str, trade_log: list, synthetic: bool) -> float` (local currency).
  - Ledger rows with `side == "DIV"`; sleeve key `sleeve["last_dividend_date"]`. Task 5–8's bridge reads the `dividends` line by summing `shares * fill` over DIV rows.

This task is the block's largest: four files, because a new ledger row type is only correct if every ledger reader knows it is not a fill. Each of the three reader edits is one to three lines and none of them is a refactor of code the fix passes through.

- [ ] **Step 1: Write the failing test**

`tests/test_data_sources.py`:
```python
def test_dividends_are_shaped_per_ticker_and_cached(tmp_path, monkeypatch):
    """A per-ticker dividend series, with load_prices' cache discipline: a
    closed window is immutable history and is never re-downloaded."""
    import pandas as pd
    import pytest

    from trading_algo import data

    pytest.importorskip("pyarrow")
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))

    idx = pd.date_range("2026-06-01", periods=3, freq="D")
    raw = pd.DataFrame(
        {("Dividends", "AAA"): [0.0, 0.5, 0.0],
         ("Dividends", "BBB"): [0.0, 0.0, 0.0],
         ("Close", "AAA"): [10.0, 10.0, 10.0],
         ("Close", "BBB"): [20.0, 20.0, 20.0]}, index=idx)
    raw.columns = pd.MultiIndex.from_tuples(raw.columns)

    calls = []

    def fake(tickers, start, end):
        calls.append((tuple(tickers), start, end))
        return raw

    monkeypatch.setattr(data, "_download_dividends", fake)

    df = data.dividends(["AAA", "BBB"], "2026-06-01", "2026-06-04")
    assert list(df.columns) == ["AAA", "BBB"]
    assert len(df) == 1, "rows with no dividend anywhere are dropped"
    assert float(df.iloc[0]["AAA"]) == pytest.approx(0.5)
    assert float(df.iloc[0]["BBB"]) == 0.0

    data.dividends(["AAA", "BBB"], "2026-06-01", "2026-06-04")
    assert len(calls) == 1, "a closed window must be served from cache"
```

`tests/test_paper_trade.py`:
```python
def test_dividends_are_credited_to_cash_and_ledgered(monkeypatch):
    """The paper book marks at the UNADJUSTED close, so an ex-dividend drop is
    booked as a loss while the cash never arrives — the backtest, running on
    yfinance's adjusted series, is a total-return number. This credit is what
    makes the two engines measure the same thing."""
    from trading_algo.regions import get_region

    region = get_region("US")
    sleeve = {"currency": "USD", "cash": 1_000.0,
              "positions": {"AAA": 100, "BBB": -50},
              "last_dividend_date": "2026-06-01"}
    div = pd.DataFrame({"AAA": [0.5], "BBB": [0.2]},
                       index=pd.DatetimeIndex(["2026-06-10"]))
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: div)

    log = []
    got = pt._credit_dividends(region, sleeve, "2026-06-15", log, synthetic=False)

    # long 100 @ 0.5 = +50; a SHORT owes the dividend: -50 @ 0.2 = -10
    assert got == pytest.approx(40.0)
    assert sleeve["cash"] == pytest.approx(1_040.0)
    assert sleeve["last_dividend_date"] == "2026-06-15"
    rows = {r["ticker"]: r for r in log}
    assert rows["AAA"]["side"] == "DIV" and rows["AAA"]["date"] == "2026-06-10"
    assert rows["AAA"]["commission"] == 0.0
    assert "decision" not in rows["AAA"], "a dividend is not a fill; TCA must skip it"
    assert rows["BBB"]["shares"] == -50


def test_first_run_credits_nothing_and_arms_the_window(monkeypatch):
    """With no prior mark there is no window to credit over, and back-crediting
    a book's whole history from a feed is not something to do silently."""
    from trading_algo.regions import get_region

    sleeve = {"currency": "USD", "cash": 1_000.0, "positions": {"AAA": 100}}
    monkeypatch.setattr(pt.data, "dividends",
                        lambda t, s, e: (_ for _ in ()).throw(AssertionError("no fetch")))
    log = []
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-15", log,
                                synthetic=False) == 0.0
    assert sleeve["last_dividend_date"] == "2026-06-15"
    assert log == []
```

`tests/test_pnl.py`:
```python
def test_a_dividend_row_is_not_a_lot():
    """DIV moves cash, never shares. Treated as a fill it reads as a SELL and
    would close the whole position."""
    from trading_algo import pnl

    trades = [
        {"date": "2026-06-01", "region": "US", "ticker": "AAA", "side": "BUY",
         "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0,
         "currency": "USD"},
        {"date": "2026-06-15", "region": "US", "ticker": "AAA", "side": "DIV",
         "shares": 10, "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0,
         "currency": "USD"},
    ]
    open_lots, realized = pnl.build_lots(trades)
    assert realized == []
    assert sum(abs(lot[0]) for lot in open_lots[("US", "AAA")]) == 10
```

`tests/test_verify.py`:
```python
def test_a_dividend_credits_cash_without_changing_the_position():
    """10_000 AUD at 1.5 = 6_666.67 USD funded; buy 10 @ 100 + 1 fee, then a
    50c dividend on all 10 shares."""
    sleeve = {"currency": "USD", "cash": 6_666.666666 - 1001.0 + 5.0,
              "positions": {"AAPL": 10.0}}
    out = verify.reconcile_equity("t", equity_book(
        [{"date": "2026-07-06", "region": "US", "ticker": "AAPL", "side": "BUY",
          "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0},
         {"date": "2026-07-20", "region": "US", "ticker": "AAPL", "side": "DIV",
          "shares": 10, "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0}],
        {"US": sleeve}))
    assert out == []


def test_a_dividend_is_not_an_uncosted_trade():
    """Invariant #2 is about fills. A dividend has no commission because nothing
    was traded."""
    book = equity_book(
        [{"date": "2026-07-06", "region": "US", "ticker": "AAPL", "side": "BUY",
          "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0},
         {"date": "2026-07-20", "region": "US", "ticker": "AAPL", "side": "DIV",
          "shares": 10, "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0}], {})
    assert verify.check_costs_charged("t", book) == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_data_sources.py::test_dividends_are_shaped_per_ticker_and_cached tests/test_paper_trade.py::test_dividends_are_credited_to_cash_and_ledgered tests/test_paper_trade.py::test_first_run_credits_nothing_and_arms_the_window tests/test_pnl.py::test_a_dividend_row_is_not_a_lot tests/test_verify.py::test_a_dividend_credits_cash_without_changing_the_position tests/test_verify.py::test_a_dividend_is_not_an_uncosted_trade -v`

Expected: FAIL — `AttributeError: module 'trading_algo.data' has no attribute '_download_dividends'` and `... has no attribute 'dividends'`; `AttributeError: module 'trading_algo.paper_trade' has no attribute '_credit_dividends'`; in `test_pnl` `KeyError: ('US', 'AAA')` (the DIV row closed the lot); in `test_verify` `assert out == []` fails with `position-mismatch`, and `check_costs_charged` returns `{'uncosted-trade'}`.

- [ ] **Step 3: Implement**

Add to `trading_algo/data.py`, after `load_prices` ends at line 170:

```python
def _download_dividends(tickers: list[str], start: str, end: str | None):
    """Primary dividend source (Yahoo via yfinance). The network seam, kept
    separate exactly like `_download_primary` so tests can replace it."""
    import yfinance as yf  # imported lazily so the package works offline

    return yf.download(tickers, start=start, end=end, actions=True,
                       auto_adjust=False, progress=False)


def dividends(tickers: list[str], start: str, end: str | None = None,
              use_cache: bool = True) -> pd.DataFrame:
    """Cash dividends per share (index=ex-date, cols=tickers). Raw Yahoo units.

    Units are the quote's own — an LSE name pays pence, exactly as it is priced
    — so the caller applies the same scale it applies to that region's prices.

    Same cache discipline as `load_prices`: a closed window (`end` given) is
    immutable history and never expires; an open-ended request means "up to now"
    and is good for CACHE_TTL_HOURS.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = _cache_path(f"div:v1:{start}:{end}:" + ",".join(sorted(tickers)))
    if use_cache and os.path.exists(cache_file) and _cache_is_fresh(cache_file, end):
        df = pd.read_parquet(cache_file)
        have = [t for t in tickers if t in df.columns]
        if have:
            return df.loc[start:end, have]

    raw = _download_dividends(list(tickers), start, end)
    if raw is None or not len(raw):
        return pd.DataFrame(columns=list(tickers))
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        if "Dividends" not in cols.get_level_values(0):
            return pd.DataFrame(columns=list(tickers))
        div = raw["Dividends"]
    elif "Dividends" in cols:
        div = raw[["Dividends"]].rename(columns={"Dividends": tickers[0]})
    else:
        return pd.DataFrame(columns=list(tickers))
    if isinstance(div, pd.Series):
        div = div.to_frame(tickers[0])
    div = div.reindex(columns=list(tickers)).fillna(0.0)
    div = div.loc[(div != 0).any(axis=1)]          # keep only actual ex-dates
    try:
        div.to_parquet(cache_file)
    except Exception:
        pass  # parquet engine optional; caching is a nicety, not a requirement
    return div.loc[start:end]
```

Add to `trading_algo/paper_trade.py`, above `_stage_target`:

```python
def _credit_dividends(region, sleeve: dict, today: str, trade_log: list,
                      synthetic: bool) -> float:
    """Credit cash for dividends that went ex since the last run.

    The paper book marks at the latest UNADJUSTED close, so every ex-dividend
    price drop is booked as a loss and the cash never arrives — while the
    backtest runs on yfinance's adjusted series and is therefore already a
    total-return number. This is the credit that makes the two engines measure
    the same thing.

    A SHORT position OWES the dividend, so the signed share count is used and the
    credit comes out negative for a short leg. Recorded as a `DIV` ledger row so
    `pnl.build_lots` skips it (it moves cash, not shares) and the blotter shows
    it; no `decision` key, because nothing was executed and TCA must not count
    it as a fill.

    Synthetic data has no dividend feed, and invariant #5 says synthetic results
    are plumbing only — so it returns zero offline rather than inventing one.
    """
    last = sleeve.get("last_dividend_date")
    sleeve["last_dividend_date"] = today
    held = {t: sh for t, sh in sleeve["positions"].items() if sh}
    if synthetic or not last or not held or last >= today:
        return 0.0
    try:
        div = data.dividends(sorted(held), last, today)
    except Exception as exc:
        print(f"  [{region.key}] ⚠ dividend feed unavailable ({exc}) — "
              f"none credited this run")
        return 0.0
    lo, hi = pd.Timestamp(last), pd.Timestamp(today)
    total = 0.0
    for t, shares in held.items():
        if t not in div.columns:
            continue
        col = div[t]
        for stamp, raw_dps in col[(col.index > lo) & (col.index <= hi)].items():
            dps = float(raw_dps) * region.price_scale
            if dps <= 0:
                continue
            amount = shares * dps
            sleeve["cash"] += amount
            trade_log.append({
                "date": str(pd.Timestamp(stamp).date()), "region": region.key,
                "ticker": t, "side": "DIV", "shares": shares,
                "fill": round(dps, 6), "commission": 0.0, "stamp_duty": 0.0,
                "currency": region.currency})
            total += amount
            print(f"    DIV  {shares:>7} {t:<10} @ {dps:.4f} "
                  f"= {amount:>10,.2f} {region.currency}")
    return total
```

Call it in `_run_daily_locked`, immediately after `params = _account_params(state, region)` and before the halt/fill block added in Task 10:

```python
        # Cash events first: they accrue to the book AS IT STOOD over the period,
        # before any of this run's trades change it.
        _credit_dividends(region, sleeve, today, state["trades"], synthetic)
```

`trading_algo/pnl.py:116-120`, inside `build_lots`:
```python
    for t in trades:
        if t.get("side") not in ("BUY", "SELL"):
            continue            # DIV and other cash events move cash, never lots
        key = (t["region"], t["ticker"])
```

`trading_algo/verify.py:155-165`, inside `reconcile_equity`'s replay loop, as the first branch:
```python
        for t in trades:
            shares = float(t.get("shares", 0))
            fill = float(t.get("fill", 0.0))
            fee = float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0))
            if t.get("side") == "DIV":
                cash += shares * fill    # a dividend credits cash; shares unchanged
                continue
            if t.get("side") == "BUY":
```

`trading_algo/verify.py:318-320`, in `check_costs_charged`:
```python
    trades = state.get("trades") or []
    fills = [t for t in trades if t.get("side") in ("BUY", "SELL")]
    free = [t for t in fills if float(t.get("commission", 0.0)) <= 0.0]
    if not free:
        return []
    return [Finding(ERROR, account, "uncosted-trade",
                    f"{len(free)} of {len(fills)} equity fills booked with zero "
                    "commission — invariant #2 says costs are always on",
                    {"example": free[0]})]
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_data_sources.py::test_dividends_are_shaped_per_ticker_and_cached tests/test_paper_trade.py::test_dividends_are_credited_to_cash_and_ledgered tests/test_paper_trade.py::test_first_run_credits_nothing_and_arms_the_window tests/test_pnl.py::test_a_dividend_row_is_not_a_lot tests/test_verify.py::test_a_dividend_credits_cash_without_changing_the_position tests/test_verify.py::test_a_dividend_is_not_an_uncosted_trade -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "pnl or verify or paper or data_sources or tca or dashboard"`

Expected: PASS. `tests/test_verify.py::test_fractional_shares_and_free_trades_break_the_invariants` still passes — its book holds a BUY, which is still counted. No synthetic test exercises the dividend path at all (the helper returns zero offline), so no fixture moves.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/data.py trading_algo/paper_trade.py trading_algo/pnl.py trading_algo/verify.py tests/test_data_sources.py tests/test_paper_trade.py tests/test_pnl.py tests/test_verify.py
git commit -m "feat(paper): credit dividends to the book, as the backtest already does

The book marks at the unadjusted close, so every ex-dividend drop was a
loss with no cash behind it, while the backtest runs on an adjusted
series and is a total-return number. Dividends now land in cash on the
ex-date as a DIV ledger row: build_lots skips it, the blotter shows it,
and the verify reconciliation credits it without moving shares. A short
leg pays the dividend rather than receiving it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Pay interest on idle cash in the paper book

**Files:**
- Modify: `trading_algo/paper_trade.py` (add `_accrue_cash_interest` above `_stage_target`; call it in the sleeve loop)
- Modify: `trading_algo/verify.py:180` (ledger replay must add the accrual back)
- Test: `tests/test_idle_cash_interest.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `fees.idle_cash_credit(net_exposure: float, days: float, annual_rate: float) -> float` — the definition `backtest.py:164` already calls. No second definition of what cash earns.
- Produces: `paper_trade._accrue_cash_interest(sleeve: dict, px: pd.Series, today: str) -> float` (local currency); sleeve keys `sleeve["last_interest_date"]` and cumulative `sleeve["interest_accrued"]`. Task 5–8's bridge reads `interest_accrued` for the `cash_interest` line.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_idle_cash_interest.py`:

```python
# --- wired into the paper book --------------------------------------------
def test_paper_book_earns_the_same_rate_the_backtest_pays():
    """`full` is 72.8% cash. Paying 0% on that while the backtest credits
    CASH_RATE_ANNUAL is ~255bps/yr of divergence against a 200bps tracking
    budget — the single biggest engine disagreement in the review."""
    from trading_algo import paper_trade as pt

    sleeve = {"currency": "USD", "cash": 7_000.0, "positions": {"AAA": 30},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})                  # equity 10_000, 70% cash

    credit = pt._accrue_cash_interest(sleeve, px, "2026-07-01")

    # 30 calendar days, ACT/365, on the CASH balance only
    assert credit == pytest.approx(7_000.0 * cfg.CASH_RATE_ANNUAL * 30 / 365)
    assert sleeve["cash"] == pytest.approx(7_000.0 + credit)
    assert sleeve["interest_accrued"] == pytest.approx(credit)
    assert sleeve["last_interest_date"] == "2026-07-01"


def test_paper_interest_is_the_backtest_definition_not_a_second_one():
    """Same function, same answer — the point of the change is one definition."""
    from trading_algo import paper_trade as pt

    sleeve = {"currency": "USD", "cash": 2_500.0, "positions": {"AAA": 75},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})                  # equity 10_000, Σw = 0.75
    credit = pt._accrue_cash_interest(sleeve, px, "2026-06-04")
    assert credit == pytest.approx(
        fees.idle_cash_credit(0.75, 3, cfg.CASH_RATE_ANNUAL) * 10_000.0)


def test_paper_interest_does_not_double_accrue_within_one_session():
    """The engine fires up to three times a day, once per regional close."""
    from trading_algo import paper_trade as pt

    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {},
              "last_interest_date": "2026-06-01"}
    px = pd.Series(dtype=float)
    first = pt._accrue_cash_interest(sleeve, px, "2026-06-02")
    second = pt._accrue_cash_interest(sleeve, px, "2026-06-02")
    assert first > 0 and second == 0.0


def test_a_short_book_that_owes_cash_is_never_credited():
    """Negative cash is a margin debit; the borrow charge is a separate,
    unmodelled cost and must not appear here as a negative credit."""
    from trading_algo import paper_trade as pt

    sleeve = {"currency": "USD", "cash": -2_000.0, "positions": {"AAA": 120},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})
    assert pt._accrue_cash_interest(sleeve, px, "2026-07-01") == 0.0
```

Top of that file already imports `numpy`, `pandas`, `pytest`, `cfg` and `fees`.

Append to `tests/test_verify.py`:
```python
def test_accrued_interest_is_not_an_unledgered_write():
    """Interest accrues straight to cash — there is no counterparty trade — so
    the ledger replay must add it back or a correctly-credited book looks like
    something wrote outside the ledger."""
    sleeve = {"currency": "USD", "cash": 6_666.666666 - 1001.0 + 500.0,
              "positions": {"AAPL": 10.0}, "interest_accrued": 500.0}
    out = verify.reconcile_equity("t", equity_book(
        [{"date": "2026-07-06", "region": "US", "ticker": "AAPL", "side": "BUY",
          "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0}],
        {"US": sleeve}))
    assert out == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_idle_cash_interest.py tests/test_verify.py::test_accrued_interest_is_not_an_unledgered_write -v`

Expected: FAIL — `AttributeError: module 'trading_algo.paper_trade' has no attribute '_accrue_cash_interest'` on the four new interest tests, and `assert out == []` failing with a `cash-drift` finding on the verify test (500 exceeds the `max(1.0, 0.02 * 6165)` tolerance).

- [ ] **Step 3: Implement**

Add to `trading_algo/paper_trade.py`, directly above `_credit_dividends`:

```python
def _accrue_cash_interest(sleeve: dict, px: pd.Series, today: str) -> float:
    """Credit interest on the sleeve's idle cash since its last run.

    Routed through the SAME `fees.idle_cash_credit` the backtest calls, so there
    is one definition of what cash earns rather than two that can drift. ACT/365
    on CALENDAR days, so a weekend accrues three. The sleeves sit 56-66% in cash
    and the reported Sharpe subtracts RISK_FREE as a hurdle; paying 0% on that
    cash while charging the full hurdle penalises the book twice
    (docs/SHARPE_RESEARCH.md §1).

    Returns the credit in the sleeve's local currency.
    """
    last = sleeve.get("last_interest_date")
    sleeve["last_interest_date"] = today
    if not (cfg.CREDIT_IDLE_CASH and cfg.CASH_RATE_ANNUAL) or not last:
        return 0.0
    try:
        days = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except (ValueError, TypeError):
        return 0.0
    equity = sleeve_equity_local(sleeve, px)
    if days <= 0 or equity <= 0:
        return 0.0
    # Σw, NET and signed — see fees.idle_cash_credit on why net, not gross.
    net_exposure = (equity - float(sleeve["cash"])) / equity
    credit = fees.idle_cash_credit(net_exposure, days, cfg.CASH_RATE_ANNUAL) * equity
    if credit <= 0.0:
        return 0.0
    sleeve["cash"] += credit
    sleeve["interest_accrued"] = sleeve.get("interest_accrued", 0.0) + credit
    return credit
```

Call it in `_run_daily_locked`, immediately before the `_credit_dividends` call added in Task 11:

```python
        # Cash events first: they accrue to the book AS IT STOOD over the period,
        # before any of this run's trades change it.
        _accrue_cash_interest(sleeve, px_today, today)
        _credit_dividends(region, sleeve, today, state["trades"], synthetic)
```

In `trading_algo/verify.py`, immediately after the replay loop and before `stored_cash` is read (`verify.py:180`):
```python
        pos = {k: v for k, v in pos.items() if abs(v) > 1e-9}
        # Interest accrues straight to cash — there is no counterparty trade and
        # so no ledger row. Add it back, or a correctly-credited book reads as an
        # unledgered write.
        cash += float(sleeve.get("interest_accrued", 0.0))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_idle_cash_interest.py tests/test_verify.py::test_accrued_interest_is_not_an_unledgered_write -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "idle_cash or paper or verify or dashboard or state_schema or promotion"`

Expected: PASS. `state_schema.validate_state` ignores unknown sleeve keys, so `last_interest_date` / `interest_accrued` need no migration; confirm by watching `tests/test_state_schema.py` and `tests/test_state_defaults.py` stay green.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/paper_trade.py trading_algo/verify.py tests/test_idle_cash_interest.py tests/test_verify.py
git commit -m "feat(paper): pay interest on idle cash, through the backtest's own rule

The backtest has credited fees.idle_cash_credit since 57676c4; the books
accrued nothing. On a book that is 72.8 percent cash that alone is about
255bps/yr of divergence against a 200bps tracking budget. Both engines
now call the one function, on calendar days, and the verify replay adds
the accrual back so the credit is not read as an unledgered write.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Charge the per-order commission floor in the backtest

**Files:**
- Modify: `trading_algo/fees.py:48-56`
- Modify: `trading_algo/backtest.py` (the `fees.turnover_cost(...)` call inside the execution block written in Task 9)
- Test: `tests/test_fees.py`, `tests/test_backtest.py`

**Interfaces:**
- Consumes: `fees.commission(region, notional) -> float` (unchanged; it already applies `region.min_commission`).
- Produces: `fees.turnover_cost(region, turnover, buy_turnover, impact=0.0, *, nav: float | None = None, n_names: int = 0) -> float`. The two new arguments are keyword-only, so every existing positional caller — including `tests/test_property_invariants.py:183-184`, which passes `impact` positionally — is unaffected.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fees.py`:

```python
import pytest


def test_turnover_cost_charges_the_per_order_commission_floor():
    """July finding H3: the backtest charged commission_bps only, so a book too
    small for bps to reach the floor was modelled as trading almost free, while
    the paper book paid A$5 an order through fees.commission."""
    asx = get_region("ASX")                      # 8bps, A$5 floor, no stamp duty
    nav, n_names, turnover, buys = 10_000.0, 5, 0.5, 0.25

    floored = fees.turnover_cost(asx, turnover, buys, nav=nav, n_names=n_names)
    bps_only = fees.turnover_cost(asx, turnover, buys)

    # each order is 0.5 * 10_000 / 5 = A$1,000 -> 8bps is 80c, so the floor binds
    assert floored > bps_only
    assert floored == pytest.approx(
        turnover * asx.slippage_bps / 1e4
        + n_names * asx.min_commission / nav
        + buys * asx.stamp_duty_bps / 1e4)


def test_large_orders_pay_bps_not_the_floor():
    """Above the floor the model is exactly what it always was."""
    us = get_region("US")
    nav, n_names, turnover = 10_000_000.0, 4, 0.8
    got = fees.turnover_cost(us, turnover, 0.0, nav=nav, n_names=n_names)
    assert got == pytest.approx(
        turnover * (us.commission_bps + us.slippage_bps) / 1e4)


def test_turnover_cost_without_nav_is_unchanged():
    """Every existing caller keeps the prior model, bit for bit."""
    us = get_region("US")
    assert fees.turnover_cost(us, 0.4, 0.2) == pytest.approx(
        0.4 * fees.round_trip_cost_rate(us) + 0.2 * us.stamp_duty_bps / 1e4)
```

Append to `tests/test_backtest.py`:

```python
def test_small_book_pays_more_than_bps_because_of_the_floor(synth_asx, asx_region):
    """Same weights, same turnover, different NAV: the per-order floor is the
    dominant cost on a small book and invisible on a large one. Invariant #2 is
    about charging what the book would actually pay."""
    prices, index_px = synth_asx
    big = run_backtest(prices, index_px, asx_region, initial_capital=10_000_000)
    small = run_backtest(prices, index_px, asx_region, initial_capital=20_000)
    assert small["turnover"].sum() == pytest.approx(big["turnover"].sum())
    assert small["total_cost_fraction"] > big["total_cost_fraction"] * 1.2
```

(`tests/test_backtest.py` needs `import pytest` added at the top alongside `import numpy as np`.)

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_fees.py tests/test_backtest.py::test_small_book_pays_more_than_bps_because_of_the_floor -v`

Expected: FAIL — `TypeError: turnover_cost() got an unexpected keyword argument 'nav'` on the two new fee tests, and `assert 0.0123 > 0.0148` (equal cost fractions, NAV-independent) on the backtest test.

- [ ] **Step 3: Implement**

Replace `trading_algo/fees.py:48-56`:

```python
def turnover_cost(region: Region, turnover: float, buy_turnover: float,
                  impact: float = 0.0, *,
                  nav: float | None = None, n_names: int = 0) -> float:
    """The ONE backtest cost entrypoint (refactor R1): commission + slippage on
    turnover, asymmetric stamp duty on buys, plus an optional market-impact term
    (fraction of NAV) from F6.

    Given `nav` and `n_names` — the book's equity and how many names the
    rebalance actually traded — commission is charged PER ORDER through
    `commission()`, which applies `region.min_commission`. Without them the bps
    rate is used on its own, which is the prior model exactly, so any caller
    that cannot size an order keeps the number it always got.

    Charging bps alone (July finding H3) modelled a small book as trading almost
    free while the paper engine paid the floor on every order.
    """
    duty = buy_turnover * region.stamp_duty_bps / 1e4
    if nav and n_names > 0:
        per_name_notional = turnover * float(nav) / n_names
        commission_frac = n_names * commission(region, per_name_notional) / float(nav)
        return (turnover * region.slippage_bps / 1e4
                + commission_frac + duty + impact)
    return turnover * round_trip_cost_rate(region) + duty + impact
```

In `trading_algo/backtest.py`, inside the execution block written in Task 9, replace the `cost = fees.turnover_cost(...)` line with:

```python
            # The per-order commission floor needs the book's size and how many
            # names this rebalance actually moved — bps alone models a small
            # book as trading almost free (July finding H3).
            cost = fees.turnover_cost(
                region, turnover, buy_turnover, impact=impact,
                nav=equity[-1], n_names=int((delta.abs() > 0).sum()))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_fees.py tests/test_backtest.py::test_small_book_pays_more_than_bps_because_of_the_floor -v`

Expected: PASS

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "fees or backtest or property_invariants or portfolio or impact or sweep or walkforward or tax"`

Expected: PASS, with `tests/test_backtest_regression.py::test_synthetic_backtest_matches_baseline` still xfail from Task 9 — this task moves the same number further in the same direction and the baseline is re-cut once, in Task 20. `tests/test_property_invariants.py::test_equity_turnover_cost_monotone_nonneg` must stay green unchanged: it calls `turnover_cost(region, turn, buys, impact)` positionally and the new arguments are keyword-only.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/fees.py trading_algo/backtest.py tests/test_fees.py tests/test_backtest.py
git commit -m "fix(fees): charge the per-order commission floor in the backtest

turnover_cost charged commission_bps only, so a book too small for bps
to reach min_commission was modelled as trading almost free while the
paper engine paid the floor on every order through fees.commission.
Given nav and the number of names traded it now charges per order
through that same function; without them the prior model is unchanged.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

# Phase 4 — Mechanism: what the books actually do

The defects that change behaviour rather than measurement. Ends with the single, evidenced re-baselining of the regression gate.

---

### Task 14: The one drawdown-breaker definition (`risk_breaker.py`) across all four engines

**Files:**
- Create: `trading_algo/risk_breaker.py`
- Create: `tests/test_risk_breaker.py`
- Modify: `trading_algo/backtest.py:173-183` (the breaker block) and `:20-24` (imports)
- Modify: `trading_algo/paper_trade.py:900-926` (the breaker block) and `:38-43` (imports)
- Modify: `trading_algo/forex/fx_backtest.py:175-185` (the breaker block) and `:26-30` (imports)
- Modify: `trading_algo/forex/fx_book.py:578-589` (the breaker block) and its import block
- Test: `tests/test_risk_breaker.py`, `tests/test_backtest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `trading_algo/risk_breaker.py` with
  `update_peak(peak: float, equity: float, *, halted: bool) -> float` and
  `breaker_step(*, equity: float, peak: float, halted: bool, cooldown: int, stop: float | None, cooldown_len: int) -> tuple[float, bool, int]`.

The defect: every engine writes `peak = max(peak, equity)` unconditionally, so the high-water mark keeps the level the book had *before* the crash. After the cooldown expires the book is still below that mark, re-trips on the very next bar, and is off permanently. The `matt` FX backtest shows 381 halts and 3,804 flat bars from this. `tests/test_backtest.py:36` only asserts `halts >= 1`, which a permanently-latched breaker satisfies perfectly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_risk_breaker.py`:

```python
"""The one drawdown-breaker definition, shared by all four engines.

The defect this pins: `peak = max(peak, equity)` is never lowered, so a book
still below its pre-crash high after a full cooldown re-trips on the next bar
and stays off forever. The high-water mark must not rise while the book is
halted (it is in cash; it did not earn that high), and on RESUME it is re-based
to the equity the book actually has, so the cooled-off book is measured against
where it is rather than where it used to be.
"""
import math

import pandas as pd

from trading_algo import risk_breaker
from trading_algo.backtest import run_backtest


def test_peak_does_not_rise_while_halted():
    assert risk_breaker.update_peak(100.0, 120.0, halted=False) == 120.0
    assert risk_breaker.update_peak(100.0, 120.0, halted=True) == 100.0


def test_a_book_that_has_not_recovered_may_trade_after_the_cooldown():
    """The test tests/test_backtest.py:36 fails to make.

    Equity falls 30% from 100 and then sits perfectly still. After the cooldown
    the book must be allowed to trade again, and must NOT re-trip on a flat
    curve — it has no new drawdown from where it now stands.
    """
    peak, halted, cooldown = 100.0, False, 0
    # The drop trips the breaker.
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is True and cooldown == 3

    # Three flat bars of cooling off.
    for _ in range(3):
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
            stop=0.25, cooldown_len=3)
    assert halted is False, "the cooldown expired; the book must be tradeable"
    assert peak == 70.0, "on resume the high-water mark re-bases to the equity"

    # Ten more flat bars: a book going nowhere must not be re-halted.
    for _ in range(10):
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
            stop=0.25, cooldown_len=3)
        assert halted is False, "re-tripped on a flat curve — the peak latched"


def test_a_genuine_new_drawdown_after_a_resume_still_halts():
    """Re-basing the peak must not disarm the breaker — only re-aim it."""
    peak, halted, cooldown = 100.0, True, 1
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is False and peak == 70.0
    # A fresh 30% fall from the NEW mark trips it again.
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=49.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is True and cooldown == 3


def test_a_disabled_stop_never_halts():
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=1.0, peak=100.0, halted=False, cooldown=0,
        stop=None, cooldown_len=3)
    assert halted is False and peak == 100.0


def _crash_then_flat_market(n_pre=420, n_crash=5, n_post=240, n_names=6):
    """A market that rises, crashes ~45% in a week, then drifts up gently and
    never regains its old high. Deterministic — no RNG, no network."""
    n = n_pre + n_crash + n_post
    idx = pd.bdate_range("2015-01-01", periods=n)
    out = {}
    for i in range(n_names):
        lvl = 100.0
        path = []
        for j in range(n):
            if j < n_pre:
                step = 0.0006 + 0.00002 * i
            elif j < n_pre + n_crash:
                step = -0.115
            else:
                step = 0.0004 + 0.00002 * i
            lvl *= 1 + step + 0.004 * math.sin((j + 7 * i) * 0.7)
            path.append(lvl)
        out[f"N{i}"] = path
    prices = pd.DataFrame(out, index=idx)
    return prices, prices.mean(axis=1)


def test_backtest_breaker_does_not_latch_on_a_market_that_never_recovers(asx_region):
    """One crash must produce ONE halt, not a halt every cooldown forever.

    With the peak latched at the pre-crash high the book is permanently below
    it, so it re-trips the bar after every cooldown expires: ~40 halts over the
    240 post-crash bars. With the mark re-based at resume the flat book has no
    drawdown and is never halted again.
    """
    prices, index_px = _crash_then_flat_market()
    res = run_backtest(prices, index_px, asx_region,
                       max_drawdown_stop=0.05, cooldown_days=5)
    assert res["drawdown_halts"] >= 1, "the crash must trip the breaker at all"
    assert res["drawdown_halts"] <= 2, (
        f"{res['drawdown_halts']} halts from one crash — the high-water mark "
        "latched and the book re-trips forever")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_risk_breaker.py -v`

Expected: the first four tests FAIL with `ModuleNotFoundError: No module named 'trading_algo.risk_breaker'`; `test_backtest_breaker_does_not_latch_on_a_market_that_never_recovers` FAILS with `AssertionError: 40 halts from one crash — the high-water mark latched and the book re-trips forever` (the exact count depends on the path; it is far above 2).

- [ ] **Step 3: Implement**

Create `trading_algo/risk_breaker.py`:

```python
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
"""
from __future__ import annotations


def update_peak(peak: float, equity: float, *, halted: bool) -> float:
    """The high-water mark for one bar.

    While HALTED the peak does not rise: the book is in cash and has not earned
    a new high. The RESUME re-base lives in `breaker_step`, which is the only
    place that can see the halt end.
    """
    if halted:
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
            # pre-halt high.
            return float(equity), False, 0
        return peak, True, cooldown
    if stop is not None and peak > 0 and equity / peak - 1 <= -float(stop):
        return peak, True, int(cooldown_len)
    return peak, False, int(cooldown)
```

Then the four call sites.

**`trading_algo/backtest.py`** — add `risk_breaker` to the import at line 20:

```python
from . import data_quality, fees, risk_breaker
```

and replace lines 173-183:

```python
        # --- drawdown circuit breaker (decision at close, execute t+1) ---
        # One definition, shared with paper_trade and both FX engines. The peak
        # does not rise while halted and is re-based on resume — see
        # risk_breaker for why a latched mark is a permanent off-switch.
        if halted:
            halt_days += 1
        was_halted = halted
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=equity[-1], peak=peak, halted=halted, cooldown=cooldown,
            stop=max_drawdown_stop, cooldown_len=cooldown_days)
        if halted and not was_halted:
            halt_events += 1
```

**`trading_algo/forex/fx_backtest.py`** — add to the import at line 30:

```python
from ..metrics import compute_metrics
from .. import risk_breaker
```

and replace lines 175-185:

```python
        # Drawdown circuit breaker (decision at close, flat from next bar).
        # Shared definition — the peak does not rise while halted and is
        # re-based on resume (risk_breaker).
        if halted:
            halt_days += 1
        was_halted = halted
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=equity[-1], peak=peak, halted=halted, cooldown=cooldown,
            stop=p.max_drawdown_stop, cooldown_len=p.drawdown_cooldown_days)
        if halted and not was_halted:
            halt_events += 1
```

**`trading_algo/forex/fx_book.py`** — add `risk_breaker` to the package-relative imports, then replace lines 578-589:

```python
    # --- drawdown breaker --------------------------------------------------
    # Shared definition (trading_algo/risk_breaker): the peak does not rise
    # while halted and is re-based at resume, so a book still under its old
    # high after the cooldown is not instantly re-halted forever.
    was_halted = bool(state.get("risk_halted", False))
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=equity,
        peak=float(state.get("peak_equity", equity)),
        halted=was_halted,
        cooldown=int(state.get("halt_cooldown", 0) or 0),
        stop=p.max_drawdown_stop,
        cooldown_len=p.drawdown_cooldown_days)
    state["halt_cooldown"] = cooldown
    if halted and not was_halted:
        print(f"  [{account}] ⛔ drawdown {equity / peak - 1:.1%} breached "
              f"{p.max_drawdown_stop:.0%} — flattening for "
              f"{p.drawdown_cooldown_days} bars.")
```

`state["peak_equity"] = float(peak)` and `state["risk_halted"] = halted` at lines 680-681 are unchanged and still read the same locals.

**`trading_algo/paper_trade.py`** — add `risk_breaker` to the import at line 40:

```python
from . import (attribution, data, data_quality, fees, fx, notifications, pnl,
               profiles, promotion, risk_breaker, storage, strategy, tca)
```

and replace lines 906-926 (the `if not all_valued: … else: …` body):

```python
    if not all_valued:
        print("  ⚠ incomplete valuation this run — breaker & history untouched.")
    elif halted and state.get("halt_last_day") == report_date:
        # The engine fires up to 3x a day. Multiple runs on ONE market day count
        # once, so leave the countdown — and the mark — as the first run left
        # them.
        state["peak_equity_base"] = risk_breaker.update_peak(
            peak, combined, halted=True)
    else:
        was = halted
        peak, now_halted, cooldown = risk_breaker.breaker_step(
            equity=combined, peak=peak, halted=halted,
            cooldown=int(state.get("halt_cooldown", 0) or 0),
            stop=stop, cooldown_len=cfg.DRAWDOWN_COOLDOWN_DAYS)
        state["peak_equity_base"] = peak
        state["halt_cooldown"] = cooldown
        state["risk_halted"] = now_halted
        if was or now_halted:
            state["halt_last_day"] = report_date
        if now_halted and not was:
            print(f"  ⛔ drawdown {combined / peak - 1:.1%} breached "
                  f"{stop:.0%} stop — halting for "
                  f"{cfg.DRAWDOWN_COOLDOWN_DAYS} market days.")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_risk_breaker.py -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "breaker or backtest or paper_trade or fx_book or fx_backtest or property_invariants"`

Expected: PASS.

Two premises to watch, both in tests that already exist:
- `tests/test_paper_trade.py::test_cooldown_counts_market_days_not_runs` and `::test_drawdown_halt_liquidates` must still pass unchanged — the same-market-day branch above preserves exactly their behaviour (cooldown 3 → 2 over two same-date runs; cooldown 5 → 4 and still halted). If either fails, the same-day branch is wrong, not the test.
- `tests/test_backtest.py:39` asserts `tight["metrics"]["MaxDrawdown"] >= off["metrics"]["MaxDrawdown"]`. Re-basing the peak lets a halted book re-enter, so in principle a tight-stop run can now end deeper than the no-stop run. Run it before touching it. If it fails, that is the fix working and the assertion's premise is wrong: replace it in THIS task with the claim that actually holds —

```python
    # The breaker caps the drawdown measured from each HALT-CYCLE's own peak,
    # not from the whole sample's peak (it re-bases on resume), so the sample
    # maxDD is no longer bounded by the no-stop run. What must hold is that it
    # halted and sat out.
    assert tight["drawdown_halt_days"] > 0
```

- [ ] **Step 6: Commit**

```bash
git add trading_algo/risk_breaker.py tests/test_risk_breaker.py \
        trading_algo/backtest.py trading_algo/paper_trade.py \
        trading_algo/forex/fx_backtest.py trading_algo/forex/fx_book.py \
        tests/test_backtest.py
git commit -m "fix(risk): re-base the drawdown high-water mark on resume

The breaker's peak rose but never fell, so a book still below its pre-crash
high when the cooldown expired re-tripped on the next bar and never traded
again (381 halts / 3,804 flat bars on the matt FX backtest). Four engines
carried the same six lines; they now share one definition in risk_breaker.py,
where the peak does not rise while halted and is re-based to the equity at
resume. A fresh fall from the new mark still halts.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: FTSE names quoted in USD get their own scale, and the mismatch is reported

**Files:**
- Modify: `trading_algo/regions.py:18-55` (the `Region` record), `:97-116` (the FTSE entry), and end of file (add `scale_for`)
- Modify: `trading_algo/data.py:173-196` (`load_region`'s scaling)
- Modify: `trading_algo/data_quality.py:106-135` (`assess`)
- Test: `tests/test_regions.py`, `tests/test_data_quality.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Region.quote_overrides: dict[str, float]` (default `{}`) and `regions.scale_for(region: Region, ticker: str) -> float`.

The defect: Yahoo reports `CPG.L` and `IHG.L` in **USD**; every other FTSE name reports **GBp** (pence). `regions.py:111` applies `price_scale=0.01` to all of them, so dollars get divided by 100. The `full` book bought 593 `IHG.L` at "£1.63" and 724 `CPG.L` at "£0.32" against real prices near £120 and £24.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regions.py`:

```python
def test_ftse_usd_quoted_names_carry_their_own_scale():
    """Yahoo reports CPG.L and IHG.L in USD, every other FTSE name in GBp.

    A blanket price_scale of 0.01 divided those two by 100 — which is how the
    `full` book came to buy 593 IHG.L at "£1.63" against a real price near £120.
    """
    from trading_algo.regions import scale_for
    ftse = get_region("FTSE")
    assert ftse.price_scale == 0.01                  # the region DEFAULT is pence
    assert scale_for(ftse, "HSBA.L") == 0.01         # ...and applies to GBp names
    assert scale_for(ftse, "CPG.L") == 1.0           # already a major unit (USD)
    assert scale_for(ftse, "IHG.L") == 1.0
    assert scale_for(get_region("US"), "AAPL") == 1.0
    assert set(ftse.quote_overrides) == {"CPG.L", "IHG.L"}
```

Append to `tests/test_data_quality.py`:

```python
def test_currency_mismatch_is_reported_not_silently_rescaled(ftse):
    """A London name quoted in USD is REJECTED, never divided by 100.

    The name still needs its own price scale so any existing holding marks
    correctly, but it must not be selected into a GBP sleeve while its quote
    currency disagrees with the sleeve's (invariant #6).
    """
    df = _clean_frame(cols=("CPG.L", "IHG.L", "HSBA.L"))
    report = data_quality.assess(df, ftse, df.index[-1])
    assert report.excluded == {"CPG.L", "IHG.L"}
    assert "currency" in report.reasons["CPG.L"]
    assert "HSBA.L" not in report.excluded


def test_load_region_does_not_divide_a_usd_quoted_name_by_100(monkeypatch, ftse):
    """The price path applies the per-TICKER scale, not one blanket region scale."""
    import pandas as pd

    from trading_algo import data

    idx = pd.bdate_range("2026-06-01", periods=5)
    frame = pd.DataFrame({"CPG.L": 2400.0, "HSBA.L": 900.0, "^FTSE": 8000.0},
                         index=idx)
    monkeypatch.setattr(data, "load_prices",
                        lambda *a, **k: frame.copy())
    prices, index_px = data.load_region(ftse, "2026-06-01",
                                        tickers=["CPG.L", "HSBA.L"])
    assert prices["HSBA.L"].iloc[-1] == 9.0          # 900 GBp -> £9
    assert prices["CPG.L"].iloc[-1] == 2400.0        # USD, untouched by /100
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_regions.py::test_ftse_usd_quoted_names_carry_their_own_scale tests/test_data_quality.py -v -k "currency or usd_quoted"`

Expected: FAIL with `ImportError: cannot import name 'scale_for' from 'trading_algo.regions'`, and for the data-quality tests `AssertionError: assert set() == {'CPG.L', 'IHG.L'}` / `assert 24.0 == 2400.0`.

- [ ] **Step 3: Implement**

**`trading_algo/regions.py`** — add the field after `price_scale` (line 33):

```python
    price_scale: float             # multiply raw Yahoo price by this to get `currency`
```

becomes, with a new optional field placed among the defaulted fields (after `constituents_file`, line 36):

```python
    constituents_file: str | None = None   # optional point-in-time membership (CSV/parquet)
    # Per-TICKER quote scale, overriding `price_scale` for names the feed does
    # not quote in the region's usual unit. FTSE: Yahoo reports CPG.L and IHG.L
    # in USD while every other name reports GBp, so a blanket 0.01 divides
    # dollars by 100. A value of 1.0 means "already in a major unit"; the
    # CURRENCY mismatch that remains is reported by data_quality, never
    # silently rescaled — converting USD to GBP needs an FX rate this layer
    # does not have, and inventing one would break invariant #6.
    quote_overrides: dict[str, float] = field(default_factory=dict)
```

In the FTSE entry, after `price_scale=0.01,` (line 111):

```python
        price_scale=0.01,              # pence (GBX) -> pounds (GBP)
        quote_overrides={"CPG.L": 1.0, "IHG.L": 1.0},   # Yahoo quotes these in USD
```

And at the end of the module, beside `get_region`:

```python
def scale_for(region: Region, ticker: str) -> float:
    """The quote scale for one ticker: its override, else the region default."""
    return region.quote_overrides.get(ticker, region.price_scale)
```

**`trading_algo/data.py`** — change the import at line 19 and the scaling at line 196:

```python
from .regions import Region, scale_for
```

```python
    # Per-TICKER scale. `region.price_scale` is the region's DEFAULT quote unit;
    # a name the feed quotes in another currency carries its own scale in
    # `region.quote_overrides`. Dividing dollars by 100 is what put 593 IHG.L
    # into the `full` book at "£1.63".
    scale = pd.Series({c: scale_for(region, c) for c in prices.columns},
                      dtype=float)
    prices = prices.mul(scale, axis=1)
```

Update the docstring at lines 178-180 to match:

```python
    Universe prices are scaled per ticker by `regions.scale_for` — the region's
    `price_scale` (pence -> pounds for the LSE) unless the name carries its own
    override. The regime index is left in native points (the regime filter is
    scale-invariant, so it doesn't need converting).
```

**`trading_algo/data_quality.py`** — in `assess`, immediately after `jump_thr = _jump_threshold(region)` (line 120):

```python
    # A name the feed quotes in another currency than the sleeve's is not a bad
    # price — it is the WRONG price for this book. It carries its own scale in
    # `region.quote_overrides` so any existing holding still marks correctly,
    # but it must not be selected into the sleeve: converting it would need an
    # FX rate, and holding it unconverted breaks invariant #6.
    overrides = dict(getattr(region, "quote_overrides", {}) or {})
```

and at the top of the per-column loop (line 133, before the `n_valid` check):

```python
    for j, t in enumerate(prices.columns):
        if t in overrides:
            report.flag(t, f"quote currency mismatch (scale {overrides[t]} vs "
                           f"region default {region.price_scale})")
            continue
        if n_valid[j] < 2:
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_regions.py::test_ftse_usd_quoted_names_carry_their_own_scale tests/test_data_quality.py -v -k "currency or usd_quoted"`

Expected: PASS.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "regions or data_quality or data_fallback or backtest or tax"`

Expected: PASS. `tests/test_regions.py::test_ftse_quirks` (line 38, `ftse.price_scale == 0.01`) is unchanged and must still pass — the region default did not move.

This task deliberately shrinks the FTSE candidate set by two names, which moves the synthetic FTSE sleeve's numbers and therefore `trading_algo/_regression_baseline.json`. Do **not** update the baseline here; Task 20 re-baselines once, with the bridge attached. If `python3 -m pytest tests/ -q -k regression` fails on drift, that is expected and Task 20 owns it.

Not fixed here, recorded as a finding: `trading_algo/tax.py:126-137,170` scales **dividends** by `region.price_scale` through a per-REGION injectable callable (`price_scale=lambda region_key: …`, monkeypatched by `tests/test_tax.py:102,125,134`). `CPG.L` and `IHG.L` dividends are therefore still divided by 100. Making that callable per-ticker changes a signature three tests bind to, which is a second defect and a second diff.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/regions.py trading_algo/data.py trading_algo/data_quality.py \
        tests/test_regions.py tests/test_data_quality.py
git commit -m "fix(regions): scale FTSE prices per ticker, not per region

Yahoo quotes CPG.L and IHG.L in USD while every other FTSE name is in GBp, so
the blanket price_scale=0.01 divided dollars by 100 — the full book bought 593
IHG.L at '£1.63' against a real price near £120. Region gains quote_overrides
and scale_for(); the price path applies the per-ticker scale, and the currency
mismatch that remains is reported by the data-quality gate rather than silently
rescaled, so the name is rejected from a GBP sleeve instead of converted with
an FX rate this layer does not have.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Whole shares round to nearest, not toward zero

**Files:**
- Modify: `trading_algo/paper_trade.py:597-602` (the `desired` loop) and add `whole_shares` above `rebalance_sleeve` (before line 541)
- Test: `tests/test_paper_trade.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `paper_trade.whole_shares(equity: float, weights: pd.Series, px: pd.Series, max_weight: float) -> dict[str, int]`.

The defect: `paper_trade.py:602` is `desired[t] = int((equity * w) / price)`, which always truncates toward zero. A US$930 name at a 5% weight of a US$10k sleeve targets 0.538 shares and buys **none** — removing 16-47% of the US sleeve's target every month, systematically and only on expensive names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paper_trade.py`:

```python
def test_whole_shares_round_to_nearest_not_toward_zero():
    """int() is a systematic short: 0.538 shares of a US$930 name bought none,
    removing 16-47% of the US sleeve's target every month."""
    got = pt.whole_shares(10_000.0, pd.Series({"EXP": 0.05}),
                          pd.Series({"EXP": 930.0}), 0.15)
    assert got == {"EXP": 1}          # 0.538 shares -> 1, not 0


def test_rounding_down_is_still_right_below_the_half():
    got = pt.whole_shares(10_000.0, pd.Series({"EXP": 0.04}),
                          pd.Series({"EXP": 930.0}), 0.15)
    assert got == {"EXP": 0}          # 0.430 shares -> 0


def test_round_up_refused_when_it_breaches_the_single_name_cap():
    """1.5 shares at the 15% cap must not become 2 shares at 20%."""
    got = pt.whole_shares(10_000.0, pd.Series({"BIG": 0.15}),
                          pd.Series({"BIG": 1_000.0}), 0.15)
    assert got == {"BIG": 1}


def test_round_up_refused_when_the_cash_is_not_there():
    """With the cap out of the way, a round-up must still be paid for.

    The truncated book costs 666 of 1,000, so exactly ONE of the two names can
    round up; the other is refused for want of money, not conviction.
    """
    got = pt.whole_shares(1_000.0, pd.Series({"A": 0.5, "B": 0.5}),
                          pd.Series({"A": 333.0, "B": 333.0}), 1.0)
    assert sorted(got.values()) == [1, 2]


def test_rounding_toward_nearest_applies_to_a_short_leg_too():
    """A short of -0.6 shares is -1, not 0 — the bias was in `int`, not in sign."""
    got = pt.whole_shares(10_000.0, pd.Series({"S": -0.06}),
                          pd.Series({"S": 1_000.0}), 0.15)
    assert got == {"S": -1}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_paper_trade.py -v -k "whole_shares or round_up or rounding"`

Expected: FAIL with `AttributeError: module 'trading_algo.paper_trade' has no attribute 'whole_shares'`.

- [ ] **Step 3: Implement**

In `trading_algo/paper_trade.py`, insert above `def rebalance_sleeve(...)` (line 541):

```python
def whole_shares(equity: float, weights: pd.Series, px: pd.Series,
                 max_weight: float) -> dict[str, int]:
    """Target share counts, rounded to NEAREST rather than toward zero.

    `int()` truncation is a systematic short, and only on expensive names: a
    US$930 name at a 5% weight of a US$10k sleeve targets 0.538 shares and buys
    none, which removed 16-47% of the US sleeve's target every month. Rounding
    to nearest removes the bias — but a round-UP spends money the book may not
    have and can push a name past its single-name cap. So each round-up is paid
    for out of the cash the truncated book leaves behind (largest fraction
    first, so conviction decides who gets the last share), and refused if it
    takes the name past `max(max_weight, |w|)` of equity. A short sale credits
    rather than consumes cash, so it draws on no budget; the cap still applies.

    Invariant #4 is preserved: every value returned is an int.
    """
    exact: dict[str, float] = {}
    for t, w in weights.items():
        price = px.get(t)
        if price and price == price and price > 0:
            exact[t] = (float(equity) * float(w)) / float(price)
    desired = {t: int(q) for t, q in exact.items()}          # toward zero
    budget = float(equity) - sum(q * float(px[t]) for t, q in desired.items())
    for t in sorted(exact, key=lambda k: -abs(exact[k] - desired[k])):
        if abs(exact[t] - desired[t]) < 0.5:
            continue                       # nearest IS the truncation
        price = float(px[t])
        up = desired[t] + (1 if exact[t] > 0 else -1)
        cap = max(float(max_weight), abs(float(weights[t]))) * float(equity)
        cost = price if up > 0 else 0.0    # a short sale credits, not spends
        if cost <= budget and abs(up) * price <= cap:
            desired[t] = up
            budget -= cost
    return desired
```

Replace lines 597-602:

```python
    dust = min(200.0, equity * 0.05)
    desired = whole_shares(equity, targets, px,
                           getattr(region.params, "max_weight", 1.0))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_paper_trade.py -v -k "whole_shares or round_up or rounding"`

Expected: PASS (5 tests).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "paper_trade or property_invariants or experimental_books or data_quality or live_path_equivalence or book_parity"`

Expected: PASS. `tests/test_property_invariants.py::test_paper_rebalance_holds_whole_shares` is the load-bearing one — every holding must still be an `int`, and `whole_shares` returns only ints.

Not changed here, recorded as a finding: `_fit_leg` (`paper_trade.py:472`) and `notional` (`:519`) inside `fit_long_short_to_lots` also use `int()`, but as a *feasibility probe* — "can this leg be held at all" — not as the executed size. They now under-estimate the executed notional by up to one share per name, which makes the hedge check conservative rather than wrong. Changing them is a second diff on a different question.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/paper_trade.py tests/test_paper_trade.py
git commit -m "fix(paper): round whole shares to nearest, not toward zero

int() truncation was a systematic short that only bit expensive names: a US\$930
name at a 5% weight of a US\$10k sleeve targeted 0.538 shares and bought none,
removing 16-47% of the US sleeve's target every month. whole_shares() rounds to
nearest and pays for each round-up out of the cash the truncated book leaves
behind, refusing any that breaches the single-name cap. Invariant #4 holds:
every holding is still an integer.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Micro mode tests affordability against the slice it actually buys with

**Files:**
- Modify: `trading_algo/paper_trade.py:548-561` (the micro-mode block)
- Test: `tests/test_paper_trade.py`

**Interfaces:**
- Consumes: `paper_trade.whole_shares(equity, weights, px, max_weight)` from Task 16 (the rounding it feeds).
- Produces: nothing new; `rebalance_sleeve`'s signature is unchanged.

The defect: `paper_trade.py:553-557` calls a name affordable if `px[t] <= equity / 1.05` — against the **whole sleeve balance** — then buys it with `0.97 / len(picks)` of that balance. With three picks each name gets 32% of the sleeve, so anything priced above a third of the sleeve buys zero shares. The `small` book has placed two trades in its life and none since 2026-07-01.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paper_trade.py`:

```python
def _micro_sleeve(cash):
    return {"currency": "USD", "cash": cash, "positions": {},
            "cost_basis": {}, "realized_pnl": 0.0,
            "last_rebalance_month": None, "last_rebalance_date": None}


def test_micro_mode_buys_the_slice_it_tested_affordability_against():
    """The `small` book bought nothing since 2026-07-01: three names were each
    called affordable against the WHOLE balance, then bought with a third of it.

    At US$900 across three US$400-ish names, concentrating into three gives each
    US$291 and buys zero shares. The affordability test must use the same slice
    the purchase uses, so it settles on two names it can actually hold.
    """
    region = get_region("US")
    sleeve = _micro_sleeve(900.0)
    px = pd.Series({"AAA": 400.0, "BBB": 410.0, "CCC": 420.0})
    targets = pd.Series({"AAA": 0.34, "BBB": 0.33, "CCC": 0.33})
    trades: list = []
    pt.rebalance_sleeve(region, sleeve, targets, px, "2026-07-01", trades)

    assert trades, "micro mode bought nothing it had already called affordable"
    assert sum(sleeve["positions"].values()) >= 2
    assert sleeve["cash"] >= 0, "bought more than the sleeve could pay for"


def test_micro_mode_still_holds_cash_when_nothing_fits_any_slice():
    """A sleeve too small for even one whole share stays in cash, quietly."""
    region = get_region("US")
    sleeve = _micro_sleeve(50.0)
    px = pd.Series({"AAA": 400.0, "BBB": 410.0})
    targets = pd.Series({"AAA": 0.5, "BBB": 0.5})
    trades: list = []
    pt.rebalance_sleeve(region, sleeve, targets, px, "2026-07-01", trades)
    assert trades == [] and sleeve["positions"] == {}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_paper_trade.py::test_micro_mode_buys_the_slice_it_tested_affordability_against -v`

Expected: FAIL with `AssertionError: micro mode bought nothing it had already called affordable` (`trades == []`).

- [ ] **Step 3: Implement**

Replace `trading_algo/paper_trade.py:548-561`:

```python
    # Micro-account mode: too small to hold the full book in whole shares.
    # Only for a long-only book — a long/short (market-neutral) book must keep
    # both legs, so concentrating it would break the hedge.
    #
    # The affordability test uses the SAME per-name slice the purchase uses.
    # Testing `price <= equity` and then buying with `0.97/len(picks) * equity`
    # is why the `small` book placed two trades in its life: three US$400 names
    # each looked affordable against a US$900 balance, then got US$291 apiece
    # and bought zero shares. Fewer names means a bigger slice, so try the
    # widest book first and narrow until the slice can actually buy a share.
    long_only = targets.empty or bool((targets >= 0).all())
    if long_only and equity < MICRO_THRESHOLD and not targets.empty:
        picks: list = []
        for k in range(max(1, min(3, int(equity // 40))), 0, -1):
            slice_value = 0.97 * equity / k
            affordable = [t for t in targets.index
                          if px.get(t) and px[t] == px[t] and px[t] > 0
                          and px[t] <= slice_value]
            if len(affordable) >= k:
                picks = affordable[:k]
                break
        if picks:
            targets = pd.Series(0.97 / len(picks), index=picks)
            print(f"    ⚠ micro mode: concentrating into {picks}")
        else:
            targets = pd.Series(dtype=float)
            print("    ⚠ no affordable names — staying in cash")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_paper_trade.py -v -k micro_mode`

Expected: PASS (2 tests).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "paper_trade or property_invariants or dashboard_terminal or experimental_books"`

Expected: PASS. `tests/test_paper_trade.py::test_micro_account_does_not_crash` (a US$100 book) must still pass — with no name affordable at any slice it takes the cash branch, which is unchanged. `tests/test_property_invariants.py` keeps cash above `MICRO_THRESHOLD` on purpose (line 230), so it never enters this branch.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/paper_trade.py tests/test_paper_trade.py
git commit -m "fix(paper): test micro-mode affordability against the slice it buys with

Names were called affordable against the whole sleeve balance and then bought
with 0.97/len(picks) of it, so three US\$400 names in a US\$900 sleeve each got
US\$291 and bought zero shares — the `small` book has placed two trades in its
life and none since 2026-07-01. The test now uses the same per-name slice as
the purchase and narrows the book until the slice can buy a whole share.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: A ticker that fails to load is reported, not silently dropped

**Files:**
- Modify: `trading_algo/data_quality.py` (add `missing_names` after `assess`, around line 177)
- Modify: `trading_algo/data.py:17-19` (imports) and `:203` (`load_region`, after the all-NaN row drop)
- Test: `tests/test_data_quality.py`

**Interfaces:**
- Consumes: `data_quality.QualityReport` (already exists, `paper_trade.py:849` reads `.excluded` / `.reasons`).
- Produces: `data_quality.missing_names(region, prices: pd.DataFrame, requested: list[str]) -> QualityReport`.

The defect: `data.py:165` ends `load_prices` with `.dropna(axis=1, how="all")`. A ticker that never downloaded — delisted, renamed, mistyped in `universes.py` — loses its column and simply ceases to exist. `assess` judges *columns*, so it cannot see it: a broken universe looks like a smaller universe.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_data_quality.py`:

```python
@pytest.fixture
def captured_alerts(monkeypatch):
    from trading_algo import notifications
    got = []
    notifications.register_channel("cap", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "cap")
    return got


def test_missing_names_reports_a_ticker_that_never_loaded(us, captured_alerts):
    """`assess` judges COLUMNS, so a ticker whose download failed is invisible
    to it — data.load_prices drops the all-NaN column and the name ceases to
    exist. A delisted or mistyped symbol in universes.py then looks like a
    smaller universe rather than a broken one."""
    df = _clean_frame(cols=("A", "B"))
    report = data_quality.missing_names(us, df, ["A", "B", "GONE", "ALSOGONE"])
    assert report.excluded == {"GONE", "ALSOGONE"}
    assert "no data" in report.reasons["GONE"]
    alerts = [p for p in captured_alerts if p["event"] == "dead_tickers"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "alert"
    assert sorted(alerts[0]["tickers"]) == ["ALSOGONE", "GONE"]


def test_missing_names_is_silent_when_everything_loaded(us, captured_alerts):
    df = _clean_frame(cols=("A", "B"))
    report = data_quality.missing_names(us, df, ["A", "B"])
    assert report.excluded == set()
    assert not [p for p in captured_alerts if p["event"] == "dead_tickers"]


def test_load_region_reports_the_names_it_dropped(monkeypatch, us, captured_alerts):
    """The drop happens inside load_prices; load_region is where the region is
    known, so that is where it is reported."""
    import pandas as pd

    from trading_algo import data

    idx = pd.bdate_range("2026-06-01", periods=5)
    # "DEAD" was requested but never came back.
    frame = pd.DataFrame({"AAPL": 200.0, "^GSPC": 5000.0}, index=idx)
    monkeypatch.setattr(data, "load_prices", lambda *a, **k: frame.copy())
    data.load_region(us, "2026-06-01", tickers=["AAPL", "DEAD"])
    alerts = [p for p in captured_alerts if p["event"] == "dead_tickers"]
    assert len(alerts) == 1 and alerts[0]["tickers"] == ["DEAD"]
    assert alerts[0]["region"] == "US"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_data_quality.py -v -k "missing_names or reports_the_names"`

Expected: FAIL with `AttributeError: module 'trading_algo.data_quality' has no attribute 'missing_names'`.

- [ ] **Step 3: Implement**

In `trading_algo/data_quality.py`, after `assess` (line 177):

```python
def missing_names(region, prices: pd.DataFrame, requested: list[str]) -> QualityReport:
    """Requested names with no column at all — they never loaded.

    `assess` judges COLUMNS. A ticker whose download failed has none:
    `data.load_prices` drops the all-NaN column and the name simply ceases to
    exist, so a delisted, renamed or mistyped symbol in `universes.py` reads as
    a smaller universe rather than a broken one. Returns a report flagging
    exactly those names and alerts once per call, so a dead ticker is visible
    in the run log and in the notification channel instead of nowhere.
    """
    absent = [t for t in requested if t not in prices.columns]
    report = QualityReport()
    for t in absent:
        report.flag(t, "no data loaded")
    if absent:
        from . import notifications
        key = getattr(region, "key", "?")
        notifications.notify(
            "dead_tickers",
            f"{key}: {len(absent)} universe name(s) returned no data and were "
            f"dropped: {', '.join(absent)} — a delisted, renamed or mistyped "
            "symbol in universes.py, not a smaller universe",
            level="alert", region=getattr(region, "key", None), tickers=absent)
    return report
```

In `trading_algo/data.py`, add to the imports (after line 18):

```python
from . import config as cfg
from . import data_quality
from .regions import Region, scale_for
```

and in `load_region`, immediately after `prices = prices.dropna(how="all")` (line 203):

```python
    prices = prices.dropna(how="all")
    # A requested ticker with no column never loaded — `load_prices` drops the
    # all-NaN column and it vanishes. Report it here, where the region is known.
    data_quality.missing_names(region, prices, universe)
    _warn_if_stale(region, prices, end)
```

`load_region`'s `(prices, index_px)` signature is deliberately unchanged — the names are returned by `missing_names` itself, and widening the tuple would touch every caller for a reporting change.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_data_quality.py -v -k "missing_names or reports_the_names"`

Expected: PASS (3 tests).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "data_quality or data_fallback or data_sources or delisting or constituents or notifications"`

Expected: PASS. Check the import addition did not create a cycle: `data_quality` imports only `config` (and `notifications` lazily), and never imports `data`.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/data_quality.py trading_algo/data.py tests/test_data_quality.py
git commit -m "fix(data): report the tickers that never loaded

load_prices ends with dropna(axis=1), so a delisted, renamed or mistyped symbol
loses its column and ceases to exist — and assess() judges columns, so it
cannot see one that is gone. A broken universe read as a smaller universe.
data_quality.missing_names names them and alerts once; load_region calls it,
where the region is known.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: A suspected split on a held name halts that sleeve and alerts

**Files:**
- Modify: `trading_algo/data_quality.py` (add `SPLIT_FACTORS`, `SPLIT_TOLERANCE`, `suspected_split` after `missing_names`)
- Modify: `trading_algo/paper_trade.py:806-810` (insert the check before `px_by_region[k] = px_today`)
- Test: `tests/test_data_quality.py`, `tests/test_paper_trade.py`

**Interfaces:**
- Consumes: `data_quality.QualityReport` conventions; `notifications.notify(event, message, level=…, **fields)`; `paper_trade._record_sleeve_status(sleeve, today, status)`.
- Produces: `data_quality.suspected_split(prices: pd.DataFrame, held, *, tolerance: float = SPLIT_TOLERANCE) -> dict[str, tuple[float, float]]` (ticker -> (observed ratio, matched factor)), plus `SPLIT_FACTORS` and `SPLIT_TOLERANCE`.

The defect: a split is not a bad price. The feed is healthy, every existing check passes, and only the **share count** is now wrong. `assess`'s impossible-move check uses `|ret| > jump_threshold`, and a 2-for-1 split is exactly `ret = -0.50` against a US threshold of `0.50` — it misses by the boundary. Detection only: **adjusting share counts is out of scope** (spec §10). A wrong adjustment corrupts the book permanently; a halt is reversible.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_data_quality.py`:

```python
def test_suspected_split_matches_a_known_factor_on_a_held_name():
    """A 2-for-1 split halves the close. The feed is healthy — only the share
    count is now wrong — and `assess`'s impossible-move check misses it exactly:
    ret = -0.50 is not > the 0.50 threshold."""
    df = _clean_frame(cols=("A", "B"))
    df.iloc[-1, df.columns.get_loc("A")] = df.iloc[-2, df.columns.get_loc("A")] / 2
    found = data_quality.suspected_split(df, {"A": 100, "B": 50})
    assert set(found) == {"A"}
    ratio, factor = found["A"]
    assert ratio == pytest.approx(0.5, abs=0.01)
    assert factor == pytest.approx(0.5)


def test_suspected_split_ignores_a_name_we_do_not_hold():
    """Detection protects a SHARE COUNT. A name we hold none of has none."""
    df = _clean_frame(cols=("A", "B"))
    df.iloc[-1, df.columns.get_loc("A")] = df.iloc[-2, df.columns.get_loc("A")] / 2
    assert data_quality.suspected_split(df, {"B": 50}) == {}
    assert data_quality.suspected_split(df, {"A": 0}) == {}


def test_suspected_split_ignores_an_ordinary_crash():
    """A 28% fall is a market, not a corporate action: it matches no factor."""
    df = _clean_frame(cols=("A",))
    df.iloc[-1, 0] = df.iloc[-2, 0] * 0.72
    assert data_quality.suspected_split(df, {"A": 100}) == {}


def test_reverse_split_is_detected_too():
    df = _clean_frame(cols=("A",))
    df.iloc[-1, 0] = df.iloc[-2, 0] * 3
    found = data_quality.suspected_split(df, {"A": 100})
    assert found and found["A"][1] == pytest.approx(3.0)
```

Append to `tests/test_paper_trade.py`:

```python
def test_suspected_split_halts_the_sleeve_without_touching_share_counts(
        monkeypatch, tmp_path):
    """Detection, not adjustment (spec §10): the sleeve sits the run out and
    alerts, and the share count is left exactly as it was. A wrong adjustment
    corrupts the book permanently; a halt is reversible."""
    from trading_algo import notifications

    captured: list = []
    notifications.register_channel("cap", captured.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "cap")
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))

    pt.init_account("t", 100_000, synthetic=True, allocations={"US": 1.0})
    pt.run_daily("t", synthetic=True)                     # opens positions
    before = pt.load_state("t")
    held_before = dict(before["sleeves"]["US"]["positions"])
    assert held_before, "fixture precondition: the first run must open positions"
    victim = sorted(t for t, sh in held_before.items() if sh)[0]

    real = pt.latest_region_data

    def spliced(region, synthetic):
        prices, index_px = real(region, synthetic)
        prices = prices.copy()
        prices.iloc[-1, prices.columns.get_loc(victim)] *= 0.5   # 2-for-1 split
        return prices, index_px

    monkeypatch.setattr(pt, "latest_region_data", spliced)
    pt.run_daily("t", synthetic=True)

    after = pt.load_state("t")
    assert after["sleeves"]["US"]["last_status"]["status"] == "split-halt"
    assert after["sleeves"]["US"]["positions"] == held_before, \
        "share counts must NOT be adjusted — detection only"
    alerts = [p for p in captured if p["event"] == "suspected_split"]
    assert len(alerts) == 1 and alerts[0]["level"] == "alert"
    assert alerts[0]["region"] == "US" and victim in alerts[0]["tickers"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_data_quality.py -v -k split` then `python3 -m pytest tests/test_paper_trade.py::test_suspected_split_halts_the_sleeve_without_touching_share_counts -v`

Expected: the data-quality tests FAIL with `AttributeError: module 'trading_algo.data_quality' has no attribute 'suspected_split'`; the paper-trade test FAILS with `AssertionError: assert 'rebalanced' == 'split-halt'` (or `'held'`, depending on the run's rebalance state).

- [ ] **Step 3: Implement**

In `trading_algo/data_quality.py`, after `missing_names`:

```python
# Close-to-close ratios that a corporate action produces rather than a market.
# Forward splits divide the price (2-for-1 -> 0.5); reverse splits multiply it.
SPLIT_FACTORS = (0.5, 1.0 / 3.0, 0.25, 2.0 / 3.0, 1.5, 2.0, 3.0, 4.0)
SPLIT_TOLERANCE = 0.02          # relative match, so 0.49-0.51 counts as a half


def suspected_split(prices: pd.DataFrame, held,
                    *, tolerance: float = SPLIT_TOLERANCE) -> dict:
    """Held names whose latest close-to-close ratio matches a known split factor.

    A split is not a bad price. The feed is healthy, every other check here
    passes, and the only thing now wrong is the SHARE COUNT — which no price
    check can see. `assess`'s impossible-move test misses it by the boundary:
    a 2-for-1 split is exactly ret = -0.50 against a 0.50 threshold.

    DETECTION ONLY. Adjusting the share count is deliberately out of scope
    (spec §10): a wrong adjustment corrupts the book permanently, while halting
    the sleeve is reversible and visible. Restricted to names actually HELD,
    because a share count is the only thing at risk.

    Returns {ticker: (observed ratio, matched factor)}; empty when nothing
    matches, which is the overwhelmingly common case.
    """
    out: dict[str, tuple[float, float]] = {}
    if prices is None or len(prices) < 2:
        return out
    for t, shares in dict(held or {}).items():
        if not shares or t not in prices.columns:
            continue
        valid = prices[t].dropna()
        if len(valid) < 2:
            continue
        prev, last = float(valid.iloc[-2]), float(valid.iloc[-1])
        if prev <= 0 or last <= 0:
            continue
        ratio = last / prev
        for f in SPLIT_FACTORS:
            if abs(ratio - f) <= tolerance * f:
                out[t] = (ratio, f)
                break
    return out
```

In `trading_algo/paper_trade.py`, insert between the partial-gap comment (ending line 808) and `px_by_region[k] = px_today` (line 810):

```python
            # A PARTIAL gap is usually one name going dark for good (a delisting),
            # not an outage. Skipping the sleeve on that would freeze it forever,
            # so keep trading — only the valuation is held back.

        # A suspected split is the opposite failure to a bad price: the feed is
        # healthy and only our SHARE COUNT is wrong, so every other gate waves
        # it through while the book silently means half what it says. Halt the
        # sleeve and alert; do NOT adjust the share count (spec §10) — a wrong
        # adjustment corrupts the book permanently, a halt is reversible. The
        # status carries no "cash:" prefix because the sleeve is not flat.
        splits = data_quality.suspected_split(prices, sleeve["positions"])
        if splits:
            detail = ", ".join(
                f"{t} x{ratio:.3f}≈{factor:.3g}" for t, (ratio, factor) in
                sorted(splits.items()))
            print(f"  [{k}] ⛔ suspected split on a held name ({detail}) — "
                  f"halting this sleeve for the run; share counts NOT adjusted.")
            notifications.notify(
                "suspected_split",
                f"[{account}] {k}: close-to-close ratio matches a known split "
                f"factor on held name(s) {detail} on {today}. The sleeve is "
                "halted for this run and share counts are NOT adjusted — "
                "confirm the corporate action and correct the book by hand.",
                level="alert", account=account, region=k, last_bar=today,
                tickers=sorted(splits))
            _record_sleeve_status(sleeve, today, "split-halt")
            all_valued = False
            continue

        px_by_region[k] = px_today
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_data_quality.py -v -k split && python3 -m pytest tests/test_paper_trade.py::test_suspected_split_halts_the_sleeve_without_touching_share_counts -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "data_quality or paper_trade or verify or dashboard_api_book"`

Expected: PASS.

One premise this task changes: `tests/test_paper_trade.py:196-200` lists `_KNOWN_STATUSES` and `test_every_sleeve_records_a_status` asserts membership. Add the new status **in this task**:

```python
_KNOWN_STATUSES = {
    "rebalanced", "held", "cash:idle", "cash:halted", "cash:below-min",
    "cash:regime-off", "cash:no-eligible-names", "cash:data-quality",
    "cash:insufficient-names", "split-halt",
}
```

(`"unpriced"` is already missing from that set — a pre-existing gap the healthy synthetic run never reaches. Left alone; it is a separate finding, not this diff.)

- [ ] **Step 6: Commit**

```bash
git add trading_algo/data_quality.py trading_algo/paper_trade.py \
        tests/test_data_quality.py tests/test_paper_trade.py
git commit -m "feat(data-quality): halt a sleeve on a suspected split, never adjust it

A split is not a bad price: the feed is healthy, every gate passes, and only
the share count is wrong — and the impossible-move check misses it by the
boundary (a 2-for-1 is exactly ret = -0.50 against a 0.50 threshold).
suspected_split matches the close-to-close ratio against known split factors on
HELD names only; paper_trade halts that sleeve for the run and alerts. Share
counts are deliberately not adjusted (spec §10): a wrong adjustment corrupts
the book permanently, a halt is reversible.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Re-baseline the regression gate once, with the bridge as evidence

**Files:**
- Modify: `trading_algo/_regression_baseline.json`
- Test: `tests/test_backtest_regression.py` (run, not modified)

**Interfaces:**
- Consumes: every change from Tasks 9-19; `python -m trading_algo.paper_trade --reconcile` and `attribution.format_bridge(report, currency, initial)` from Tasks 5-8; `ci_regression.synthetic_metrics()`, `ci_regression.compare(baseline, current)`, `ci_regression.TOL`.
- Produces: an updated `trading_algo/_regression_baseline.json`. No code interface.

This is the block's one deliberate re-baseline (spec §6, §11, §14: "`ci_regression` is re-baselined once (P4) and never silently"). It is not a TDD task — there is no new behaviour, only a recorded number — so the steps are: prove the suite is green first, capture the movement, explain each moved number from the bridge, then write the file.

**Do not start this task until Tasks 9-19 are all committed.** Re-baselining while a defect fix is still outstanding buries it.

- [ ] **Step 1: Prove the whole suite is green on the new code**

```bash
export MOMENTUM_STATE_DIR=$(mktemp -d) FX_STATE_DIR=$(mktemp -d)
cd /Users/matthewfanning/Trading-Algo
python3 -m pytest tests/ -q
```

Expected: every test passes **except** the regression gate itself. If anything else fails, stop: a failing behavioural test is a defect, and re-baselining would hide it.

- [ ] **Step 2: Capture the movement, before and after**

```bash
export MOMENTUM_STATE_DIR=$(mktemp -d) FX_STATE_DIR=$(mktemp -d)
cd /Users/matthewfanning/Trading-Algo
cp trading_algo/_regression_baseline.json /tmp/baseline-before.json
python3 -m trading_algo.ci_regression --check   # expect exit 1, and READ the drift list
python3 -m trading_algo.ci_regression --show > /tmp/baseline-after.json
diff <(python3 -m json.tool /tmp/baseline-before.json) \
     <(python3 -m json.tool /tmp/baseline-after.json)
```

Expected: `--check` exits 1 and names each drifted metric as `path: old -> new (|Δ| > tol N)`. Keep that output — it is half of the commit message. Metrics that did **not** drift past `TOL` still move in the file; the diff is the full record.

- [ ] **Step 3: Attribute every moved number to a named cause**

```bash
export MOMENTUM_STATE_DIR=$(mktemp -d) FX_STATE_DIR=$(mktemp -d)
cd /Users/matthewfanning/Trading-Algo
python3 -m trading_algo.paper_trade --reconcile --account full > /tmp/bridge.txt
cat /tmp/bridge.txt
```

Expected: the fixed-width bridge table from `attribution.format_bridge`, with lines `dividends`, `cash_interest`, `fill_convention`, `commission_floor`, `stamp_duty`, `exposure_gap`, `rebalance_timing`, `fx_translation`, a `residual`, and `identity_ok: True`.

Then write, in the commit message body, one line per moved metric naming the task that moved it and the bridge line it corresponds to. The expected causes from this group, for cross-checking that nothing moved for a reason nobody can name:

| moved number | cause | bridge line |
|---|---|---|
| FTSE CAGR / MaxDrawdown | Task 15 excludes `CPG.L` and `IHG.L` from the candidate set | `exposure_gap` |
| every sleeve's CAGR | Task 13's commission floor in the backtest | `commission_floor` |
| every sleeve's CAGR / Sharpe | Task 9's next-day-close fills | `fill_convention` |
| portfolio Sharpe / AnnVol | Tasks 10-11 (dividends, cash interest) reaching both engines | `dividends`, `cash_interest` |
| any sleeve's MaxDrawdown | Task 14: a re-based peak lets a halted book re-enter | none — a risk-control change, state it as such |

**If a metric moved and no bridge line explains it, stop and do not re-baseline.** An unexplained move is the regression this gate exists to catch. `identity_ok: False` is the same stop.

- [ ] **Step 4: Write the new baseline**

```bash
export MOMENTUM_STATE_DIR=$(mktemp -d) FX_STATE_DIR=$(mktemp -d)
cd /Users/matthewfanning/Trading-Algo
python3 -m trading_algo.ci_regression --update
git diff trading_algo/_regression_baseline.json
```

Expected: `Baseline updated -> …/_regression_baseline.json`, and a diff whose every changed number appears in the Step 3 table.

- [ ] **Step 5: Run it and watch the gate pass**

```bash
export MOMENTUM_STATE_DIR=$(mktemp -d) FX_STATE_DIR=$(mktemp -d)
cd /Users/matthewfanning/Trading-Algo
python3 -m trading_algo.ci_regression --check
python3 -m pytest tests/ -q
```

Expected: `OK: synthetic backtest matches baseline within tolerance.` and a fully green suite, the regression gate included.

- [ ] **Step 6: Commit**

Paste the real `--check` drift list and the real bridge table into the body — the placeholders below are the shape, not the content.

```bash
git add trading_algo/_regression_baseline.json
git commit -m "chore(regression): re-baseline once after the mechanism fixes

The block's one deliberate re-baseline (spec §6, §11, §14). Every moved number
below is attributed to a named change and to the bridge line that carries it;
nothing moved for a reason this message cannot name.

Drift (ci_regression --check against the old baseline):
  <paste the exact 'path: old -> new (|Δ| > tol N)' lines>

Reconciliation bridge (paper_trade --reconcile --account full):
  <paste the format_bridge table, including residual and identity_ok>

Attribution:
  FTSE CAGR/MaxDrawdown  <- CPG.L and IHG.L excluded as currency mismatches
                            (Task 15); bridge line: exposure_gap
  all sleeve CAGR        <- per-order commission floor now charged in the
                            backtest (Task 13); bridge line: commission_floor
  all sleeve CAGR/Sharpe <- next-day-close fills (Task 9); bridge line:
                            fill_convention
  portfolio Sharpe/Vol   <- dividends and cash interest in both engines
                            (Tasks 10-11); bridge lines: dividends, cash_interest
  sleeve MaxDrawdown     <- the breaker re-bases its peak at resume (Task 14),
                            so a halted book re-enters; a risk-control change,
                            not a cost line

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

# Phase 5 — Regeneration, deploy, restart, and survivorship

Every published number rebuilt by current code, the branch merged so the schedulers run it, the books archived and reopened clean, and point-in-time membership wired when its data arrives.

---

### Task 21: Total-return benchmark in the portfolio backtest

**Files:**
- Modify: `trading_algo/regions.py:36` (add one `Region` field), and the four `REGIONS` entries at `:59`, `:78`, `:96`, `:119`
- Modify: `trading_algo/portfolio_backtest.py:116-123` (the benchmark block) and `:125-137` (the result dict)
- Modify: `trading_algo/report.py:38`
- Modify: `trading_algo/run_backtest.py:134`
- Test: `tests/test_portfolio_backtest.py`

**Interfaces:**
- Consumes: `trading_algo.fx.align_fx(fx, index, currency) -> pd.Series`; `trading_algo.data.load_prices(tickers, start, end, cache_key=None, use_cache=True) -> pd.DataFrame`; `trading_algo.metrics.compute_metrics(rets, equity, risk_free=RISK_FREE, currency="AUD", periods_per_year=None) -> dict` (Task 1's signature; this task passes only `currency=`, exactly as today)
- Produces: `Region.benchmark_ticker: str | None = None`; `portfolio_backtest.BENCHMARK_LABELS: dict[str, str]`; `portfolio_backtest._blend(index_by_region: dict[str, tuple[pd.Series, str]], union: pd.DatetimeIndex, fx_tbl: pd.DataFrame, proxies: dict[str, pd.Series]) -> pd.Series`; `portfolio_backtest._total_return_proxies(regions: list[str], start: str, end: str | None, synthetic: bool) -> dict[str, pd.Series]`; new result keys `"benchmark_kind"`, `"benchmark_label"`, `"benchmark_proxies"`, `"benchmark_price_index"`, `"benchmark_price_index_metrics"`

The defect (spec §2, first bullet): `data.py:117` downloads strategy prices with `auto_adjust=True`, so the strategy compounds dividends, while `portfolio_backtest.py:116-123` builds the benchmark from the raw `^AXJO` / `^GSPC` / `^FTSE` / `^GSPTSE` price indices, which do not. The strategy is measured against a handicapped opponent. The fix is one dividend-reinvesting ETF proxy per region, with the price-index blend kept and labelled so neither can be mistaken for the other.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portfolio_backtest.py`:

```python
# --- total-return benchmark (the strategy's own prices are auto_adjust=True) --
def test_every_region_declares_a_total_return_proxy():
    """A price index is not the strategy's opponent: the strategy compounds
    dividends (data.py downloads with auto_adjust=True) and the index does not.
    Each region names a dividend-reinvesting ETF that tracks the same market."""
    from trading_algo.regions import REGIONS
    assert {k: r.benchmark_ticker for k, r in REGIONS.items()} == {
        "ASX": "STW.AX", "US": "SPY", "FTSE": "ISF.L", "TSX": "XIU.TO"}


def test_blend_prefers_the_total_return_proxy_where_one_exists():
    """`_blend` is scale-free (it takes a pct_change), so a pence-quoted proxy
    needs no price_scale. A reinvesting proxy compounds ABOVE the price index of
    the same market; the blend has to show that."""
    import pandas as pd
    from trading_algo import portfolio_backtest as pb

    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    price = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    total = pd.Series([100.0, 102.0, 104.0, 106.0, 108.0], index=idx)
    fx_tbl = pd.DataFrame({"AUD": [1.0] * 5}, index=idx)
    by_region = {"ASX": (price, "AUD")}

    price_ret = pb._blend(by_region, idx, fx_tbl, {})
    tr_ret = pb._blend(by_region, idx, fx_tbl, {"ASX": total})
    assert abs(float((1 + price_ret).prod()) - 1.04) < 1e-9
    assert float((1 + tr_ret).prod()) > float((1 + price_ret).prod())


def test_offline_benchmark_falls_back_to_the_price_index_and_says_so():
    """Synthetic has no ETF history. Falling back is fine; falling back
    silently is not — the label has to carry the word the reader needs."""
    result = run_portfolio_backtest(synthetic=True, start="2018-01-01",
                                    end="2021-01-01")
    assert result["benchmark_kind"] == "price-index"
    assert "NO dividends" in result["benchmark_label"]
    assert result["benchmark_proxies"] == {}
    assert result["benchmark"].equals(result["benchmark_price_index"])
    assert "CAGR" in result["benchmark_price_index_metrics"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_portfolio_backtest.py -v -k "proxy or blend or price_index"`
Expected: FAIL — `AttributeError: 'Region' object has no attribute 'benchmark_ticker'`, `AttributeError: module 'trading_algo.portfolio_backtest' has no attribute '_blend'`, and `KeyError: 'benchmark_kind'`.

- [ ] **Step 3: Implement**

In `trading_algo/regions.py`, add the field next to `constituents_file` (line 36):

```python
    constituents_file: str | None = None   # optional point-in-time membership (CSV/parquet)
    # Dividend-REINVESTING total-return proxy for this market, used only by the
    # portfolio benchmark. The strategy's own prices are auto_adjust=True, so a
    # price index (`index_ticker`) is not its opponent — it is the same market
    # with the dividends removed. Deliberately NOT in `all_tickers`: this is a
    # yardstick, never a tradable name.
    benchmark_ticker: str | None = None
```

and give each region its proxy (one line per entry, beside `index_ticker`):

```python
        index_ticker="^AXJO",          # S&P/ASX 200
        benchmark_ticker="STW.AX",     # SPDR S&P/ASX 200 ETF (distributions reinvested)
```
```python
        index_ticker="^GSPC",          # S&P 500
        benchmark_ticker="SPY",        # SPDR S&P 500 ETF
```
```python
        index_ticker="^FTSE",          # FTSE 100
        benchmark_ticker="ISF.L",      # iShares Core FTSE 100 UCITS ETF
```
```python
        index_ticker="^GSPTSE",        # S&P/TSX Composite
        benchmark_ticker="XIU.TO",     # iShares S&P/TSX 60 Index ETF
```

In `trading_algo/portfolio_backtest.py`, add the two helpers and the label table just below `_sleeve_base_returns` (after line 32):

```python
# What the benchmark actually is, in words, so no consumer can print a number
# without printing what it was measured against.
BENCHMARK_LABELS = {
    "total-return": "total-return ETF proxies, AUD buy & hold",
    "price-index": "price indices only — NO dividends, AUD buy & hold",
    "mixed": "ETF proxies where available, price index elsewhere — mixed basis",
}


def _blend(index_by_region: dict, union: pd.DatetimeIndex,
           fx_tbl: pd.DataFrame, proxies: dict[str, pd.Series]) -> pd.Series:
    """Equal-weight AUD return of one series per region. A region present in
    `proxies` uses its total-return proxy; the rest fall back to the price
    index. Scale-free — this takes a pct_change, so a pence-quoted proxy needs
    no price_scale."""
    parts = []
    for key, (idx, ccy) in index_by_region.items():
        px = proxies.get(key, idx)
        mult = fx.align_fx(fx_tbl, px.index, ccy)
        parts.append((px * mult).pct_change(fill_method=None)
                     .reindex(union).fillna(0.0))
    return sum(parts) / len(parts)


def _total_return_proxies(regions: list[str], start: str, end: str | None,
                          synthetic: bool) -> dict[str, pd.Series]:
    """Closes of each region's dividend-reinvesting ETF proxy. Empty offline or
    synthetic — the caller then falls back to the price index and SAYS SO."""
    if synthetic:
        return {}
    want = {k: get_region(k).benchmark_ticker for k in regions
            if get_region(k).benchmark_ticker}
    if not want:
        return {}
    try:
        df = data.load_prices(sorted(set(want.values())), start, end,
                              cache_key=f"benchmark:{start}:{end}")
    except Exception:
        return {}
    return {k: df[t].dropna() for k, t in want.items()
            if t in df.columns and df[t].notna().any()}
```

Replace `trading_algo/portfolio_backtest.py:116-123` with:

```python
    # Benchmark: the strategy's prices are dividend-adjusted, so the benchmark
    # must be too. PRIMARY = equal-weight dividend-reinvesting ETF proxies in
    # AUD. The price-only index blend is kept as a clearly-labelled SECONDARY,
    # so both are reported and neither can be read as the other.
    proxies = _total_return_proxies(regions, start, end, synthetic)
    bench_ret = _blend(index_by_region, union, fx_tbl, proxies)
    price_ret = _blend(index_by_region, union, fx_tbl, {})
    covered = [k for k in regions if k in proxies]
    bench_kind = ("total-return" if len(covered) == len(regions)
                  else "price-index" if not covered else "mixed")
    bench_equity = cfg.INITIAL_CAPITAL * (1 + bench_ret).cumprod()
    price_equity = cfg.INITIAL_CAPITAL * (1 + price_ret).cumprod()
```

and add these five keys to the returned dict, immediately after `"benchmark_stats": ...` (line 135):

```python
        "benchmark_kind": bench_kind,
        "benchmark_label": BENCHMARK_LABELS[bench_kind],
        "benchmark_proxies": {k: get_region(k).benchmark_ticker for k in covered},
        "benchmark_price_index": price_equity,
        "benchmark_price_index_metrics": compute_metrics(
            price_ret, price_equity, currency=cfg.BASE_CURRENCY),
```

In `trading_algo/report.py:38`, change the hardcoded heading:

```python
        out += [f"## vs Benchmark ({result.get('benchmark_label', 'unlabelled')})", "",
```

In `trading_algo/run_backtest.py:134`, change the hardcoded heading:

```python
        print(f"\n  vs Benchmark ({result.get('benchmark_label', 'unlabelled')}):")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_portfolio_backtest.py -v -k "proxy or blend or price_index"`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "portfolio or report or backtest_store or regions"`
Expected: PASS. `tests/test_portfolio_backtest.py::test_portfolio_has_benchmark` is unchanged and must still pass — `benchmark` and `benchmark_stats` keep their names and meaning; only the series behind them changes when real data is available.

Size note for the owner (spec §13): this diff is ~32 lines across four files. It is one idea — "report the benchmark the strategy actually competes with, and label both" — and splitting the label off would ship an unlabelled number for one commit, which is the exact defect. Flagging rather than pushing through silently.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/regions.py trading_algo/portfolio_backtest.py trading_algo/report.py trading_algo/run_backtest.py tests/test_portfolio_backtest.py
git commit -m "fix(benchmark): measure the strategy against a total-return index

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: FX books leave the headline AUM, and say why

**Files:**
- Modify: `trading_algo/profiles.py:30-32` (reporting-group constants)
- Modify: `trading_algo/dashboard/overview.py:83` (and its import block at `:12-15`)
- Modify: `trading_algo/forex/fx_config.py` (add `REVIEW_NOTICE` after `ACCOUNTS`, line 317)
- Modify: `trading_algo/dashboard/fx_api.py:348-352` (payload)
- Modify: `trading_algo/dashboard/static/app.js:3588-3595` (FX branch of `contentHTML`)
- Modify: `trading_algo/forex/dashboard.py:1164` (after `</header>`) and `:1806-1821` (`render` substitutions)
- Test: `tests/test_dashboard_overview.py` (new), `tests/test_dashboard_fx_api.py` (new)

**Interfaces:**
- Consumes: `trading_algo.dashboard.overview.build_overview(regime_hints=None) -> dict`; `trading_algo.dashboard.fx_api.build_fx_snapshot(account: str) -> dict`; `trading_algo.forex.fx_book.init_account(account, capital, profile, symbols=None, bar="1d", source="yahoo", close_only_signals=None, force=False)`
- Produces: `trading_algo.profiles.FX: str = "FX"`; `trading_algo.forex.fx_config.REVIEW_NOTICE: str`; FX snapshot key `"review_notice"`

The defect (spec §15.1): `overview.py:83` reads `group = str(state.get("group") or "CORE").upper() if kind == "equity" else "CORE"` — the `else` branch forces every non-equity book into CORE, which is why four books back-testing at Sharpe −2.06 / −2.14 / −5.56 / −6.25 sum into the headline AUM. The EXPERIMENTAL equity books already have the mechanism; the FX books just never got a group.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_overview.py`:

```python
"""The all-accounts rollup: which books move the headline AUM.

CORE is the number the owner watches. An unproven book gets its own total and
is excluded — the EXPERIMENTAL equity books already work that way. The FX books
were forced into CORE, so four books back-testing at Sharpe -2 to -6 inflated
the headline. They keep their own total; it is not this one.
"""
import pytest

from trading_algo import profiles
from trading_algo.dashboard import overview


def _equity_state():
    return {"initial_capital_base": 100_000.0,
            "equity_history": [["2026-01-01", 100_000.0],
                               ["2026-01-02", 101_000.0]],
            "peak_equity_base": 101_000.0,
            "sleeves": {"US": {"positions": {"AAPL": 3}}},
            "trades": [], "group": "CORE"}


def _fx_state():
    return {"initial_capital": 5_000.0, "equity": 4_200.0,
            "equity_history": [["2026-01-01", 5_000.0],
                               ["2026-01-02", 4_200.0]],
            "peak_equity": 5_000.0, "positions": {"EURUSD": 0.3}}


@pytest.fixture
def two_books(monkeypatch):
    entries = [
        {"key": "FULL", "account": "full", "kind": "equity", "micro": False,
         "label": "FULL · EQUITIES", "sub": "EQUITIES · 4 REGIONS · MONTHLY"},
        {"key": "MATT", "account": "matt", "kind": "fx", "micro": False,
         "label": "FX · MATT", "sub": "FX + CRYPTO · DAILY BARS"},
    ]
    states = {"full": _equity_state(), "matt": _fx_state()}
    monkeypatch.setattr(overview.registry, "discover_accounts", lambda: entries)
    monkeypatch.setattr(overview, "_load", lambda e: states[e["account"]])
    return overview.build_overview()


def test_fx_books_report_under_their_own_group(two_books):
    by_key = {c["key"]: c for c in two_books["accounts"]}
    assert by_key["FULL"]["group"] == profiles.CORE
    assert by_key["MATT"]["group"] == profiles.FX


def test_fx_books_do_not_inflate_the_headline_aum(two_books):
    """The headline is CORE only. The FX book's A$4,200 must not appear in it,
    and must still be reported under a total of its own."""
    assert two_books["totals"]["aum"] == pytest.approx(101_000.0)
    assert two_books["totals"]["books"] == 1
    fx_group = next(g for g in two_books["groups"] if g["name"] == profiles.FX)
    assert fx_group["aum"] == pytest.approx(4_200.0)
    assert fx_group["books"] == 1
```

Create `tests/test_dashboard_fx_api.py`:

```python
"""The FX snapshot must carry the review notice, with the real figures."""
from trading_algo import paper_trade as pt
from trading_algo.dashboard import fx_api
from trading_algo.forex import fx_book
from trading_algo.forex import fx_config as fxcfg


def test_the_fx_snapshot_states_the_books_are_under_review(tmp_path, monkeypatch):
    """Spec 15.2: the FX dashboard says plainly that these books back-test
    negative and are under review, with the numbers. One definition, so the
    terminal SPA and the published static page cannot drift apart."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    fx_book.init_account("matt", 5_000.0, "balanced")

    snap = fx_api.build_fx_snapshot("matt")
    assert snap["review_notice"] == fxcfg.REVIEW_NOTICE
    assert "UNDER REVIEW" in fxcfg.REVIEW_NOTICE.upper()
    for sharpe in ("−2.06", "−2.14", "−5.56", "−6.25"):
        assert sharpe in fxcfg.REVIEW_NOTICE
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_dashboard_overview.py tests/test_dashboard_fx_api.py -v`
Expected: FAIL — `AttributeError: module 'trading_algo.profiles' has no attribute 'FX'`, and `AttributeError: module 'trading_algo.forex.fx_config' has no attribute 'REVIEW_NOTICE'`.

- [ ] **Step 3: Implement**

In `trading_algo/profiles.py`, extend the group constants at line 30:

```python
# Reporting groups. CORE is the headline AUM; everything else is a side total.
CORE = "CORE"
EXPERIMENTAL = "EXPERIMENTAL"
# The FX books. Not a BookProfile (those are equity presets) — a reporting group
# they belong to by KIND, so the FX side gets the same ring-fence the geared and
# market-neutral equity books already have.
FX = "FX"
```

In `trading_algo/dashboard/overview.py`, add `profiles` to the import block (line 12-15):

```python
from .. import config as cfg
from .. import paper_trade
from .. import profiles
from ..forex import fx_book
from . import registry
```

and replace line 81-83:

```python
    # Reporting group: CORE books sum into the headline AUM; any other group
    # (EXPERIMENTAL, FX) is broken out into its own separate total. FX books
    # used to be forced to CORE, which is how four books back-testing at Sharpe
    # −2 to −6 ended up inside the number the owner watches. They keep a total;
    # it is not that one.
    group = (str(state.get("group") or profiles.CORE).upper() if kind == "equity"
             else profiles.FX)
```

In `trading_algo/forex/fx_config.py`, add after the `ACCOUNTS` dict (line 317):

```python
# What every FX surface has to say about these books, in ONE place so the
# terminal SPA and the published static page cannot state different things.
# Figures: docs/FULL_SYSTEM_REVIEW_2026-09.md F18, real data, HEAD code,
# isolated state. Two known artefacts inflate them (the fixed-dollar crypto
# spread and the breaker latch) and both are named in the text rather than
# quietly discounted from it.
REVIEW_NOTICE = (
    "UNDER REVIEW — these books back-test NEGATIVE and are excluded from the "
    "headline AUM. Backtest Sharpe: matt −2.06, partner −2.14, multiasset "
    "−5.56, daytrader −6.25. Two known cost/risk artefacts inflate those "
    "losses (a fixed-dollar crypto spread and a drawdown breaker that never "
    "resets its high-water mark); with both corrected the agents' gross Sharpe "
    "is near zero. No edge has been demonstrated. Paper money only."
)
```

In `trading_algo/dashboard/fx_api.py`, add one key to the returned payload, next to `"sub"` (line 352):

```python
        "sub": entry["sub"],
        "review_notice": fxcfg.REVIEW_NOTICE,
```

In `trading_algo/dashboard/static/app.js`, add the banner helper just above `function fxBookHTML(page) {` (line 2000):

```javascript
/* Spec 15.2 — every FX tab carries the same sentence, from Python. */
function fxReviewBannerHTML(page) {
  if (!page.review_notice) return '';
  return `<div style="padding:10px 18px;background:#1a1206;border-bottom:1px solid ${AMB};font-size:10px;line-height:1.6;color:${AMB};letter-spacing:.04em">${esc(page.review_notice)}</div>`;
}
```

and prefix the FX branch of `contentHTML()` (line 3588):

```javascript
  if (page.kind === 'fx') {
    const banner = fxReviewBannerHTML(page);
    /* POSITIONS = the ensemble's decision book, then the same book in money */
    if (S.tab === 'POSITIONS') return banner + agentPositionsHTML(page) + fxLedgerHTML(page);
    if (S.tab === 'BACKTEST') return banner + agentBacktestHTML(page);
    if (S.tab === 'METHOD') return banner + agentMethodHTML(page);
    if (S.tab === 'SWARM') return banner + swarmHTML(page);
    return banner + agentKpisHTML(page) + agentCurveAttrHTML(page)
      + fxBookHTML(page) + chartSectionHTML(page);
  }
```

In `trading_algo/forex/dashboard.py`, add the placeholder immediately after `</header>` (line 1164):

```html
</header>
<div class="review">__REVIEW__</div>
```

and add the substitution to `render()`'s `repl` dict (after the `__HALT__` entry, line 1811):

```python
        "__REVIEW__": _esc_html(fx_config.REVIEW_NOTICE),
```

If `dashboard.py` does not already import the config module under that name, use the name it does import it as; the constant is `REVIEW_NOTICE` on `trading_algo.forex.fx_config`. If no HTML-escaping helper exists in that module, substitute the literal string — it contains no `<`, `>` or `&`.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_dashboard_overview.py tests/test_dashboard_fx_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "dashboard or overview or fx_api or registry"`
Expected: PASS. No existing test asserts that an FX book is in CORE — checked with `grep -rn "CORE" tests/`. If one appears, its premise is the defect and it is updated here, not worked around.

- [ ] **Step 6: Commit** (two commits: the ring-fence, then what the page says)
```bash
git add trading_algo/profiles.py trading_algo/dashboard/overview.py tests/test_dashboard_overview.py
git commit -m "fix(dashboard): give the FX books their own reporting group

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

git add trading_algo/forex/fx_config.py trading_algo/dashboard/fx_api.py trading_algo/dashboard/static/app.js trading_algo/forex/dashboard.py tests/test_dashboard_fx_api.py
git commit -m "docs(dashboard): state on the FX books that they back-test negative

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 23: Split the training universe from the traded one

**Files:**
- Modify: `trading_algo/forex/pairs.py:100-131` and `:152-160` (via cherry-pick of `f15a118`)
- Modify: `trading_algo/forex/__init__.py:24,32` (conflict resolution, see Step 3)
- Modify: `trading_algo/forex/train.py:37,253-263` (via cherry-pick)
- Modify: `tests/test_fx_pairs.py:7-12` and `:133-134` (via cherry-pick), plus the new guard test
- Test: `tests/test_fx_pairs.py`

**Interfaces:**
- Consumes: `trading_algo.forex.fx_book.init_account(account, capital, profile, symbols=None, ...)`, `trading_algo.forex.fx_book.load_state(account) -> dict`, `trading_algo.forex.pairs.resolve_universe(name)`
- Produces: `trading_algo.forex.pairs.TRAINING_UNIVERSE: list[str]` (16 symbols); `trading_algo.forex.pairs.DEFAULT_UNIVERSE: list[str]` narrowed to 10; `UNIVERSES["training"]`; `trading_algo.forex.TRAINING_UNIVERSE` re-export

**THIS MUST LAND BEFORE TASK 26.** `fx_book.run_once` (`fx_book.py:411-416`) merges `DEFAULT_UNIVERSE` into every unlocked book as a **union** — it adds and never removes. So a book re-initialised while `DEFAULT_UNIVERSE` still holds 16 symbols reopens on 16 and can never shed them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fx_pairs.py`:

```python
# ---------------------------------------------------------------------------
# The guard: a data decision must not move money
# ---------------------------------------------------------------------------
def test_a_fresh_unlocked_book_opens_on_the_traded_universe(tmp_path, monkeypatch):
    """`fx_book.run_once` merges DEFAULT_UNIVERSE into every unlocked book, so a
    symbol added to that list starts holding paper capital on the next scheduled
    run, with no further review. The six G10 crosses were registered to give the
    neural layer rows; they must never reach a book.

    The merge is a UNION and never removes, so this only holds for a book opened
    AFTER the split — which is exactly why the books are re-initialised.
    """
    from trading_algo.forex import fx_book

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("guard", 5_000.0, "balanced")
    state = fx_book.load_state("guard")

    assert state["universe_locked"] is False
    assert list(state["symbols"]) == list(pairs.DEFAULT_UNIVERSE)
    assert len(state["symbols"]) == 10
    assert not (set(state["symbols"]) & set(pairs.CROSSES)), \
        "a training-only cross reached a live book"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_fx_pairs.py::test_a_fresh_unlocked_book_opens_on_the_traded_universe -v`
Expected: FAIL — `assert 16 == 10`, and the cross-intersection assertion fires with `{'EURGBP', 'EURJPY', 'GBPJPY', 'AUDJPY', 'AUDNZD', 'EURAUD'}`.

- [ ] **Step 3: Implement — cherry-pick `f15a118`, resolving one known conflict**

```bash
git cherry-pick f15a1188d72c516ade68d3ad1561aa15975dbcb2
```

This **will conflict in `trading_algo/forex/__init__.py` and nowhere else.** `f15a118`'s hunk carries `MetaLabeler` in its context, and this branch has since removed `MetaLabeler` along with the meta bundle. Verified: `git diff f15a118^ HEAD -- <file>` is empty for every test file the pick touches and for `docs/research/COST_AWARE_OBJECTIVE_RESULT.md`; `pairs.py` and `train.py` diverge only in regions the pick does not touch.

Resolve `trading_algo/forex/__init__.py` by keeping HEAD's `MetaLabeler`-free imports and taking only the `TRAINING_UNIVERSE` addition — the final file reads:

```python
from .ml_agent import ModelBundle, NeuralAgent, default_neural_agents
from .nn import MLP
from .pairs import DEFAULT_UNIVERSE, PAIRS, TRAINING_UNIVERSE, get_pair

__all__ = [
    "AgentPool", "TrendAgent", "BreakoutAgent", "MeanReversionAgent",
    "MomentumAgent", "CarryAgent", "default_agents",
    "FXParams", "profile", "profile_names",
    "compute_targets", "target_weights_history",
    "PAIRS", "DEFAULT_UNIVERSE", "TRAINING_UNIVERSE", "get_pair",
    # deep-learning layer
    "MLP", "NeuralAgent", "ModelBundle", "default_neural_agents",
]
```

Then:

```bash
git add trading_algo/forex/__init__.py
git cherry-pick --continue
```

The pick brings, in `trading_algo/forex/pairs.py`, `DEFAULT_UNIVERSE: list[str] = [*PAIRS, *CRYPTO]` (10), `TRAINING_UNIVERSE: list[str] = [*DEFAULT_UNIVERSE, *CROSSES]` (16), and `UNIVERSES["training"]`; and in `trading_algo/forex/train.py`, `_load(TRAINING_UNIVERSE, ...)` for fitting with the grade taken on the `DEFAULT_UNIVERSE` slice of the same panel, because `promotion.clears_floor` is a deployment gate and its number has to describe the portfolio that actually runs.

It also rewrites the two pinning tests the brief names: `tests/test_fx_pairs.py:7-12` becomes `test_default_universe_is_majors_plus_crypto` (10 symbols) and `:133-134` becomes `test_training_universe_includes_the_registered_crosses` asserting `len(pairs.TRAINING_UNIVERSE) == 16`, plus a new `test_training_universe_is_a_superset_of_what_is_traded`.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_fx_pairs.py -v`
Expected: PASS, including the new guard test and the three universe tests the pick rewrote.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "fx_pairs or fx_ml or multiasset or close_only or genome or fx_engine or fx_consistency"`
Expected: PASS.

`tests/test_close_only_bars.py:452` and `:467` assert `state["symbols"] == list(DEFAULT_UNIVERSE)` — both are written against the symbol and follow the narrowed list automatically; neither needs editing. (The brief expected literal pins there; there are none. The only literals were in `tests/test_fx_pairs.py`, and the cherry-pick fixes them.) `tests/test_fx_backtest.py:75` asserts `len(res["attribution"]) == len(DEFAULT_UNIVERSE)` — also symbolic. If any of these fails, do not relax it: it means something else pins 16.

- [ ] **Step 6: Commit**
```bash
git add tests/test_fx_pairs.py
git commit -m "test(fx): guard that a fresh book opens on the traded universe only

The universe merge in fx_book.run_once is a union and never removes, so this
holds only for a book opened after the split — which is why the books are
re-initialised in the restart task.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```
(The cherry-pick itself is already a commit; this second commit carries only the guard test.)

---

### Task 24: Regenerate every published number, and record the two missing findings

**Files:**
- Modify: `trading_algo/dashboard/backtest_store.py:26,119-127` (stamp the code the cache came from)
- Modify: `docs/FORENSIC_AUDIT_2026-09.md` (append two sections before "A trap worth recording", line 363)
- Regenerate (build outputs, committed): `state/backtest_equity.json`, `obsidian/Reference.md`, any tearsheet under `reports/`
- Test: `tests/test_backtest_store_export.py`

**Interfaces:**
- Consumes: `trading_algo.manifest._git_commit() -> str`; `trading_algo.dashboard.backtest_store.export_equity(synthetic=False, point_in_time=False, sweep=False, report_out=None, out_path=None) -> str`
- Produces: dashboard cache key `"git_commit"`

The defect (spec §1, third row; §12 gate P5): `state/backtest_equity.json` was generated **2026-07-24** and predates the code that writes it — it has no `benchmark_stats`, no `allocations`, no `fx_rebalance_cost`, all of which `export_equity` has produced since. The cache carried no way to tell. Stamping the commit makes staleness readable instead of inferable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_backtest_store_export.py`:

```python
def test_the_cache_records_the_code_it_was_generated_by(exported):
    """P5's gate is 'no published number older than its code', and the cache
    had no way to say. `state/backtest_equity.json` was written 2026-07-24 and
    lacks three blocks the exporter has produced since; nothing could tell.
    A commit stamp makes that readable rather than inferable."""
    from trading_algo import manifest

    assert exported["git_commit"] == manifest._git_commit()
    assert exported["git_commit"] != "unknown"
    assert len(exported["git_commit"]) == 40
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_backtest_store_export.py::test_the_cache_records_the_code_it_was_generated_by -v`
Expected: FAIL — `KeyError: 'git_commit'`.

- [ ] **Step 3: Implement**

In `trading_algo/dashboard/backtest_store.py`, add the import beside the existing ones (line 26):

```python
from .. import config as cfg
from .. import paper_trade
from ..manifest import _git_commit
from ..metrics import metric as _metric
```

and stamp it into the payload, next to `generated_at` (line 121):

```python
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # The commit this cache was computed by. P5's gate is "no published
        # number older than its code"; the July cache predated three blocks the
        # exporter now writes and nothing on it said so.
        "git_commit": _git_commit(),
```

In `docs/FORENSIC_AUDIT_2026-09.md`, insert these two sections immediately before `## A trap worth recording` (line 363). Both are numbers read out of the repo's own state files, not recollections:

```markdown
## The 2026-08-27 phantom liquidation (recorded here late)

The whole `full` book was liquidated on a drawdown that never happened, and
neither this document nor `LIVE_BOOK_AUDIT.md` recorded it until now. From
`state/paper_state_full.json`, block `corrections`:

| date | equity as booked | equity on real closes | FTSE sleeve as booked | FTSE sleeve corrected |
|---|---|---|---|---|
| 2026-08-27 | A$74,552.44 | A$99,086.23 | A$7,910.89 | A$32,444.69 |
| 2026-08-28 | A$74,325.61 | A$98,819.82 | A$7,891.45 | A$32,385.67 |

**Cause.** The FTSE sleeve was marked at cash only, on an all-NaN price row.
Equity read A$74,552 against a true A$99,086, the 25% drawdown breaker read a
−26.2% drawdown against a true **−2.24%**, tripped, and sixteen names were sold.
The book sat in cash until 16 September; re-entry cost £46 of UK stamp duty.

**Fix.** PR #93 — valuation now refuses an unpriced book (`paper_trade.py`
unpriced-holdings guard). State was corrected on 2026-09-15T21:15:33Z by
replaying holdings from the trade ledger and marking them at the real closes,
with `risk_halted` cleared and the correction recorded in the state file.

**Why it is written here.** The fix is good; the silence was not. An audit that
does not carry its own incidents is not an audit.

## The swarm permutation result: p = 0.4726

From `state/permtest_matt.json`, run 2026-09-19T21:26:21 on the `matt` book,
real data:

| field | value |
|---|---|
| statistic | `best_holdout_sharpe` |
| real result | **0.0599** |
| permutations | 200 |
| **p-value** | **0.4726** |
| seed | 0 (held fixed across the real run and every permutation) |
| window | 2020-04-10 → 2026-09-19, 2,354 bars |
| window limited by | `SOLUSD` (4,840 bars dropped to get one shared timeline) |
| search | 12 generations, population 40, 25% holdout |

**What it means in plain words.** The genetic swarm's search over the `matt`
book is indistinguishable from the same search run on shuffled data: roughly
half of 200 fake markets produced a best genome at least as good as the real
one. This is a statement about the *search*, not a refutation of every possible
FX edge — and its scope is the trimmed 2020–2026 window above, not the full
history. Method and its caveats: `docs/specs/swarm-insample-permutation.md`.
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_backtest_store_export.py::test_the_cache_records_the_code_it_was_generated_by -v`
Expected: PASS

- [ ] **Step 5: Run the affected suite, then regenerate every published number**

Run: `python3 -m pytest tests/ -q -k "backtest_store or export or manifest or tearsheet"`
Expected: PASS

Then regenerate, **in this order** (each stage feeds the next, per spec §6):

```bash
# 1. the dashboard's backtest cache — real data, current code
python -m trading_algo.dashboard.backtest_store

# 2. the monthly tearsheet per live book
mkdir -p reports
for a in full small ultra experimental; do
  python -m trading_algo.tearsheet --account "$a" --out "reports/${a}_2026-09.md"
done

# 3. the code-derived vault notes (obsidian/Reference.md and the vault copies)
python tools/build_obsidian_vault.py
python tools/build_vault_notes.py
```

Confirm the cache is no longer stale before committing:

```bash
python3 -c "
import json, subprocess
d = json.load(open('state/backtest_equity.json'))
head = subprocess.run(['git','rev-parse','HEAD'], capture_output=True, text=True).stdout.strip()
print('generated_at', d['generated_at'])
print('git_commit  ', d['git_commit'], 'HEAD ok:', d['git_commit'] == head)
print('has benchmark_stats:', 'benchmark_stats' in d, '| benchmark_kind:', d.get('benchmark_kind'))
"
```
Expected: today's date, `HEAD ok: True`, `has benchmark_stats: True`, and `benchmark_kind: total-return` (Task 21 landed).

Hand-copied numbers that this run supersedes and that must be re-read from the new cache and corrected by hand, with the new figure and its date: `docs/SHARPE_RESEARCH.md` (the sleeve Sharpe tables at lines 58, 165, 218, 350), `docs/MONTE_CARLO_RESEARCH.md` (line 237), and `CLAUDE.md`'s TSX line ("raw Sharpe 0.948, haircut 0.608, maxDD −15.6%"). Each correction states the old number, the new one, and which bridge line moved it (spec §13).

- [ ] **Step 6: Commit**
```bash
git add trading_algo/dashboard/backtest_store.py tests/test_backtest_store_export.py
git commit -m "feat(backtest-store): stamp the commit a published cache came from

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

git add docs/FORENSIC_AUDIT_2026-09.md
git commit -m "docs(audit): record the phantom liquidation and the permutation result

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

git add state/backtest_equity.json obsidian/ reports/ docs/SHARPE_RESEARCH.md docs/MONTE_CARLO_RESEARCH.md CLAUDE.md
git commit -m "chore(published): regenerate every number on current code

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 25: Merge to main and confirm the schedulers picked up the new code

**Files:**
- No source file changes. This task moves a branch.

**Interfaces:**
- Consumes: everything Tasks 1–24 produced. Produces: nothing new.

The defect (spec §2, last bullet): `.github/workflows/day-paper.yml:10` records that `schedule:` fires **only on the default branch**. `paper-trade.yml:16` (`30 21 * * 1-5`), `fx-paper.yml:22` (`0 23 * * 1-5`) and `monthly-report.yml:12` (`0 3 1 * *`) are the same. The live books have therefore been running pre-remediation code. At the time of drafting, `git log --oneline main..HEAD | wc -l` is **46** commits (the spec's "41" was counted three days earlier), plus everything this plan adds.

This task changes no code, so it has no unit test. Its test is the full suite on `main` and the first scheduled run's log, both of which are run below.

- [ ] **Step 1: Establish the failing condition**

```bash
git fetch origin
echo "unmerged commits: $(git log --oneline origin/main..HEAD | wc -l)"
git log --oneline origin/main..HEAD | head -20
```
Expected: a non-zero count. That number is the defect — every one of those commits is a fix the live books are not running.

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/ -q` on the **branch**, then confirm the books are on old code:
```bash
git rev-parse HEAD origin/main
```
Expected: the two SHAs differ, and `origin/main` is an ancestor of `HEAD` (no divergence) — verify with `git merge-base --is-ancestor origin/main HEAD && echo "fast-forwardable"`. If that prints nothing, `main` has moved independently and this becomes a merge, not a fast-forward; resolve before continuing.

- [ ] **Step 3: Implement — merge**

```bash
python3 -m pytest tests/ -q          # must be green on the branch BEFORE merging
git checkout main
git pull --ff-only origin main
git merge --no-ff feat/dormant-feature-remediation -m "merge: measurement truth — the books run the fixed code

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
python3 -m pytest tests/ -q          # and green on main AFTER
```

Do **not** push until both suites are green. Then, per the repo's working preference (ask before pushing to remote), confirm with the owner and:

```bash
git push origin main
```

- [ ] **Step 4: Run it and watch it pass**

```bash
git fetch origin
echo "unmerged commits: $(git log --oneline origin/main..feat/dormant-feature-remediation | wc -l)"
```
Expected: `unmerged commits: 0`.

- [ ] **Step 5: Confirm the schedulers are running the new code**

The next scheduled fires are `day-paper` hourly at `:07` on weekdays, `paper-trade` at 21:30 UTC, and `fx-paper` at 23:00 UTC. After the first one lands:

```bash
gh run list --branch main --limit 10
gh run view --log $(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId') | head -40
```
Expected: the run's head SHA equals the merge commit, and the log shows the new code — concretely, the FX book log line reads `over 10 instruments` (Task 23's narrowed `DEFAULT_UNIVERSE`), not `over 16 instruments`. That single line is the cheapest proof the scheduler is on the merged code.

Then confirm the committed state came back with it:
```bash
git pull --ff-only origin main
python3 -c "
import json, glob, os
for p in sorted(glob.glob('state/fx_state_*.json')):
    d = json.load(open(p))
    print(os.path.basename(p), 'symbols:', len(d.get('symbols') or []))
"
```
Expected on an **existing** book: still 10 or more — the merge alone cannot shrink a running book, because `fx_book.run_once` merges as a union. That is Task 26's job, and it is the reason Task 26 exists.

- [ ] **Step 6: Commit**

Nothing to commit — the merge commit created in Step 3 is this task's artefact. Record the scheduler evidence:
```bash
git log --oneline -1 main
gh run list --branch main --limit 3
```

---

### Task 26: Archive the books, then reopen them clean

**Files:**
- Create: `state/archive/2026-09-pre-truth/` (every state file and DB, plus `README.md`)
- Modify: nothing in `trading_algo/`
- Test: `tests/test_paper_trade_init.py` (new)

**Interfaces:**
- Consumes: `trading_algo.paper_trade.init_account(account, capital, synthetic, allocations=None, profile=None, force=False)`, `trading_algo.paper_trade.load_state(account) -> dict`, `trading_algo.paper_trade.STATE_DIR`, `trading_algo.config.ALLOCATIONS`; `trading_algo.forex.fx_book.init_defaults(synthetic, force=False)`
- Produces: no new names.

**Depends on Task 23.** `fx_book.run_once` merges `DEFAULT_UNIVERSE` into every unlocked book as a union, so a book reopened before the split reopens on 16 symbols and can never shed the six crosses.

The books are, from disk today: `full` A$100,000 across `{ASX, US, FTSE}` at a third each, 81 trades; `small` A$1,000 US-only, 2 trades; `ultra` A$10,000 US-only `ultra` profile, 23 trades; `experimental` A$10,000 US-only `experimental` profile, 14 trades; FX `matt` A$5,000 balanced, `partner` A$5,000 conservative, `daytrader` A$10,000 intraday/60m, `multiasset` A$10,000 balanced universe-locked. `full` will come back with **four** sleeves, because `config.ALLOCATIONS` funds `ASX/US/FTSE/TSX` at 0.25 each and a fresh `--init` reads `ALLOCATIONS` (spec §8) — the running book could not, since its allocations were baked in at its own `--init`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_paper_trade_init.py`:

```python
"""What a freshly opened book is funded across.

D4 restarts every book so there is one record under one set of rules. `full`
reopens with FOUR sleeves: ALLOCATIONS funds TSX at 25% and a fresh --init
reads ALLOCATIONS, which the running book (baked at a 3-way split) could not.
"""
import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade as pt


def test_a_fresh_book_is_funded_across_every_allocated_region(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)

    state = pt.load_state("full")
    assert set(state["sleeves"]) == set(cfg.ALLOCATIONS)
    assert set(cfg.ALLOCATIONS) == {"ASX", "US", "FTSE", "TSX"}
    for w in state["allocations"].values():
        assert w == pytest.approx(0.25)
    assert state["trades"] == []          # a fresh book starts with no record


def test_init_refuses_to_overwrite_a_live_book(tmp_path, monkeypatch):
    """All P&L is derived from the trade ledger, so --init over a live book
    destroys the only record. The archive step exists because of this."""
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    with pytest.raises(SystemExit):
        pt.init_account("full", capital=100_000, synthetic=True)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_paper_trade_init.py -v`
Expected: both PASS on current code — this pair is a **characterisation** test, pinning the behaviour the restart relies on before the restart is performed. If `test_a_fresh_book_is_funded_across_every_allocated_region` fails with a 3-key sleeve set, `ALLOCATIONS` does not carry TSX and the restart must not proceed until it does.

- [ ] **Step 3: Implement — archive, then reopen**

Archive first. Nothing here is reversible once `--force` runs.

```bash
mkdir -p state/archive/2026-09-pre-truth
cp state/paper_state_*.json state/archive/2026-09-pre-truth/
cp state/fx_state_*.json    state/archive/2026-09-pre-truth/
cp state/paper_books.db     state/archive/2026-09-pre-truth/
cp state/fx_books.db        state/archive/2026-09-pre-truth/
cp state/permtest_matt.json state/archive/2026-09-pre-truth/
cp state/swarm_log_*.json state/champions_*.json state/archive/2026-09-pre-truth/
ls -la state/archive/2026-09-pre-truth/
```

Write `state/archive/2026-09-pre-truth/README.md`:

```markdown
# Paper books as at 2026-09, archived before the measurement-truth restart

These are the books as they stood before the restart of spec decision **D4**
(`docs/superpowers/specs/2026-09-24-measurement-truth-design.md`). They are kept
because P&L is derived from the trade ledger and a ledger is the only record a
book has. **Nothing here is a performance claim.** Every one of these books was
measured under rules the repo has since corrected.

## What was wrong with them

- **Dividends were never credited.** `paper_trade.py` marked positions at the
  latest close and no code path paid a dividend into cash. Every ex-dividend
  price drop in every book above is booked as a loss that never reversed.
- **Cash interest was never paid.** `fees.idle_cash_credit` existed in the
  backtester only. `full` was 72.8% cash: about 255bps/yr of pure divergence.
- **Fills used the signal's own close** rather than the next day's, in both
  engines. Worth roughly 0.38–0.40pp/yr of CAGR on ASX and US.
- **Two FTSE names were priced in the wrong currency.** `CPG.L` and `IHG.L` are
  quoted by Yahoo in USD; `regions.py` applied the blanket pence→pounds scale to
  every FTSE name. `full` bought 593 `IHG.L` at "£1.63" (2026-06-11) and 724
  `CPG.L` at "£0.32" (2026-07-01) against real prices near £120 and £24. Every
  FTSE valuation in this archive is wrong by that amount.
- **The drawdown breaker latched.** Its high-water mark never fell after a halt,
  so a book below its old peak re-tripped after every cooldown.
- **The 2026-08-27 phantom liquidation.** The whole `full` book was sold on a
  −26.2% drawdown that was really −2.24%, caused by an all-NaN FTSE price row.
  Corrected in state 2026-09-15; fixed by PR #93. Full record:
  `docs/FORENSIC_AUDIT_2026-09.md`.
- **`full` traded three sleeves, not four.** TSX was funded at 25% in
  `config.ALLOCATIONS` but a running book keeps the allocations baked in at its
  own `--init`, so it never received capital.
- **The FX books held 16 instruments, not 10.** Six G10 crosses were registered
  to give the neural layer training rows and reached the books as a side effect.
- **`experimental` could not form its short leg**, so the market-neutral book was
  not market-neutral.

## Where the successors are

`state/paper_state_*.json` and `state/fx_state_*.json` at the repo root, reopened
on the same capital and the same allocations. The promotion clock
(`MIN_PROMOTION_REBALANCES = 6`) restarts from zero: the accepted cost of D4.
```

Then reopen. **`--force` destroys the trade ledger** — do not run it until `ls state/archive/2026-09-pre-truth/` shows all eight state files plus both DBs.

```bash
python -m trading_algo.paper_trade --account full         --init --capital 100000 --force
python -m trading_algo.paper_trade --account small        --init --capital 1000   --regions US --force
python -m trading_algo.paper_trade --account ultra        --init --capital 10000  --profile ultra        --force
python -m trading_algo.paper_trade --account experimental --init --capital 10000  --profile experimental --force

# All four FX books come from fx_config.ACCOUNTS in one sweep; --force is
# required because init_defaults SKIPS an existing book silently.
python -m trading_algo.forex.paper --init --force
```

- [ ] **Step 4: Run it and watch it pass**

```bash
python3 -c "
import json, glob, os
for p in sorted(glob.glob('state/paper_state_*.json')):
    d = json.load(open(p))
    print(os.path.basename(p), '| sleeves', sorted(d['sleeves']),
          '| capital', d['initial_capital_base'], '| trades', len(d.get('trades') or []))
for p in sorted(glob.glob('state/fx_state_*.json')):
    d = json.load(open(p))
    print(os.path.basename(p), '| symbols', len(d.get('symbols') or []),
          '| capital', d.get('initial_capital'), '| trades', len(d.get('trades') or []))
"
```
Expected: `paper_state_full.json | sleeves ['ASX', 'FTSE', 'TSX', 'US'] | capital 100000.0 | trades 0`; every other equity book at its stated capital with 0 trades; `fx_state_matt/partner/daytrader` at **10** symbols each (`multiasset` stays on its locked 10-symbol universe); every FX book at 0 trades.

Then prove the books are healthy end to end:
```bash
python -m trading_algo.verify --strict
```
Expected: exit 0. A funded sleeve that has never traded reads `regime-off` (INFO) or `data-quality` (ERROR); on a book opened minutes ago, INFO is the correct verdict.

- [ ] **Step 5: Run the affected suite**

Run: `python3 -m pytest tests/ -q -k "paper_trade or init or verify or state_schema or overview"`
Expected: PASS. Any test asserting `full` holds three sleeves has its premise deliberately changed by this task and is updated here to `set(cfg.ALLOCATIONS)` — symbolically, never to a hardcoded four.

- [ ] **Step 6: Commit**
```bash
git add state/archive/2026-09-pre-truth/
git commit -m "chore(state): archive the pre-truth paper books with what was wrong

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

git add state/ tests/test_paper_trade_init.py
git commit -m "chore(state): reopen every paper book clean under the new rules

full comes up with four sleeves: ALLOCATIONS funds TSX at 25% and a fresh
--init reads ALLOCATIONS, which the running book could not. The promotion
clock restarts from zero — the accepted cost of D4.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 27: Point-in-time membership, and the survivorship bias it measures

**BLOCKED ON DATA. The precondition, stated explicitly:** this task cannot complete until a point-in-time index-membership file exists for each of `ASX`, `US`, `FTSE`, `TSX` in the format `constituents.py:10-16` documents — a CSV or parquet with `date,ticker` columns, one row per (snapshot, member), month-end snapshots, covering `config.START` (2012-01-01) to today, and **including names that have since been delisted**. Today no region sets `constituents_file` (`regions.py:36`), so every backtest selects from today's survivors: zero of 125 US, 56 ASX and 55 TSX names stopped printing in 14.7 years, which is not what happened.

Steps 1–4 below do **not** need that data and should be done now — they build and test the per-region measurement that the data will feed. Steps 5–6 are gated on it. Spec §7's off-ramp applies per region: if one region's data cannot be sourced at sensible cost, that region publishes a *bound* instead (the equal-weight-universe versus total-return-index gap, already computed: ASX +9.4pp/yr, TSX +5.4, US +2.5, FTSE ≈0), and the block does not stall.

**Files:**
- Modify: `trading_algo/run_backtest.py:152-172` (`pit_impact` and `run_compare_pit`)
- Modify (gated on data): `trading_algo/regions.py` — `constituents_file=` on each of the four entries
- Modify (gated on data): `trading_algo/config.py:345` — `DELISTING_REPLACEMENT_RETURN`
- Create (gated on data): `docs/research/SURVIVORSHIP_BIAS.md`
- Test: `tests/test_run_backtest.py` (new)

**Interfaces:**
- Consumes: `trading_algo.portfolio_backtest.run_portfolio_backtest(regions=None, synthetic=False, start=cfg.START, end=None, point_in_time=False, params=None, allocations=None) -> dict`; `trading_algo.constituents.get_membership(region) -> MembershipTable | None`; `trading_algo.constituents.synthetic_membership(region, start, end, seed=None) -> MembershipTable`
- Produces: `run_backtest.pit_impact(synthetic: bool) -> dict` gains a `"per_region": dict[str, dict[str, float]]` entry, each value `{"static_cagr": float, "pit_cagr": float, "delta": float}`

The gap: `--compare-pit` exists but reports the **portfolio** CAGR delta only (`run_backtest.py:152-161`). The brief and spec §7.4 require the bias per region, because the off-ramp is per region and the bound differs by an order of magnitude across them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_backtest.py`:

```python
"""The survivorship-bias measurement.

Spec section 7: the bias is the PIT-versus-non-PIT difference, and it is
reported PER REGION because the off-ramp is per region and the measured bound
ranges from about zero (FTSE) to +9.4pp/yr (ASX).
"""
import pytest

from trading_algo import config as cfg
from trading_algo import run_backtest as rb


@pytest.fixture(scope="module")
def impact():
    """Synthetic membership exercises the PIT machinery offline. Invariant 5:
    the NUMBERS here are meaningless; only the shape is under test."""
    return rb.pit_impact(synthetic=True)


def test_pit_impact_reports_a_delta_for_every_funded_region(impact):
    assert set(impact["per_region"]) == set(cfg.ALLOCATIONS)


def test_each_regions_delta_is_its_own_two_cagrs(impact):
    """A delta that is not the difference of the two numbers printed beside it
    is a number nobody can check."""
    for key, row in impact["per_region"].items():
        assert set(row) == {"static_cagr", "pit_cagr", "delta"}
        assert row["static_cagr"] - row["pit_cagr"] == pytest.approx(
            row["delta"], abs=1e-12), key


def test_the_portfolio_delta_is_still_reported(impact):
    for k in ("static_cagr", "pit_cagr", "delta"):
        assert isinstance(impact[k], float)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_run_backtest.py -v`
Expected: FAIL — `KeyError: 'per_region'` on all three tests.

- [ ] **Step 3: Implement**

Replace `trading_algo/run_backtest.py:152-161` (`pit_impact`) with:

```python
def pit_impact(synthetic: bool) -> dict:
    """F1: quantify the survivorship bias — CAGR of the static (current-universe)
    backtest minus the point-in-time backtest, for the portfolio AND for each
    sleeve. A positive delta is the inflation the current universe carries.

    Per region because spec section 7's off-ramp is per region: a region whose
    PIT data cannot be sourced publishes a measured BOUND instead, and the two
    cannot share one number."""
    static = run_portfolio_backtest(synthetic=synthetic, point_in_time=False)
    pit = run_portfolio_backtest(synthetic=synthetic, point_in_time=True)
    s_cagr = float(static["metrics"]["CAGR"])
    p_cagr = float(pit["metrics"]["CAGR"])
    per_region: dict[str, dict[str, float]] = {}
    for key, sleeve in static["sleeves"].items():
        if key not in pit["sleeves"]:
            continue
        s = float(sleeve["metrics"]["CAGR"])
        p = float(pit["sleeves"][key]["metrics"]["CAGR"])
        per_region[key] = {"static_cagr": s, "pit_cagr": p, "delta": s - p}
    return {"static_cagr": s_cagr, "pit_cagr": p_cagr, "delta": s_cagr - p_cagr,
            "per_region": per_region}
```

and extend `run_compare_pit` (line 164-172) with the per-region table, after the existing portfolio lines:

```python
    print("  (positive delta = the current universe flatters returns)")
    if imp["per_region"]:
        print("\n  Per sleeve (standalone, local currency):")
        for key, row in imp["per_region"].items():
            print(f"    {key:<5} static {row['static_cagr']:>+7.2%}  "
                  f"PIT {row['pit_cagr']:>+7.2%}  bias {row['delta']:>+7.2%}")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 -m pytest tests/test_run_backtest.py -v`
Expected: PASS (3 passed)

Then see the shape of the real output offline:
```bash
FX_STATE_DIR=/tmp/pit-scratch MOMENTUM_STATE_DIR=/tmp/pit-scratch \
  python -m trading_algo.run_backtest --compare-pit --synthetic
```
Expected: the portfolio block, then one line per sleeve for ASX, US, FTSE, TSX, each under the `⚠ SYNTHETIC DATA` banner.

- [ ] **Step 5: Run the affected suite — then, ONCE THE DATA EXISTS, wire and publish**

Run: `python3 -m pytest tests/ -q -k "run_backtest or constituents or delisting or portfolio"`
Expected: PASS.

**Everything below is gated on the precondition at the top of this task.** Do not do it with fabricated membership: `constituents.synthetic_membership` is labelled "OFFLINE TESTING ONLY — it does not represent real index history" and invariant 5 forbids reporting it as performance.

Once each region's `date,ticker` file is in hand, drop them under `data/constituents/` and point the four regions at them in `trading_algo/regions.py`, one line per entry beside `universe=`:

```python
        constituents_file="data/constituents/asx200.csv",
```
```python
        constituents_file="data/constituents/sp500.csv",
```
```python
        constituents_file="data/constituents/ftse100.csv",
```
```python
        constituents_file="data/constituents/tsx60.csv",
```

Then enable the delisting correction in `trading_algo/config.py:345` — it is gated behind the PIT path (`portfolio_backtest.py:47-48`: `apply_delisting = point_in_time and cfg.DELISTING_REPLACEMENT_RETURN is not None`), so it is a perfect no-op until both are true:

```python
# Shumway's measured delisting return (~-30% NYSE/AMEX, ~-55% Nasdaq). A held
# name that delists with no further price is booked at this return rather than
# vanishing at its last good close, which is the single largest remaining way a
# PIT backtest can still flatter itself.
DELISTING_REPLACEMENT_RETURN: float | None = -0.30
```

Measure, and publish, with real data:

```bash
python -m trading_algo.run_backtest --point-in-time            # the corrected run
python -m trading_algo.run_backtest --compare-pit              # the bias, per region
python -m trading_algo.dashboard.backtest_store --point-in-time
```

Write `docs/research/SURVIVORSHIP_BIAS.md` carrying, for each region: the membership source and its coverage window, the number of snapshots and the number of names that were ever members versus members today, the static CAGR, the PIT CAGR, and the delta — plus, for any region that took the off-ramp, the measured bound instead (ASX +9.4pp/yr, TSX +5.4, US +2.5, FTSE ≈0) and an explicit statement that it is a bound and not a measurement. Then relabel every surviving non-PIT number in `README.md`, `CLAUDE.md` and `docs/SHARPE_RESEARCH.md` as survivorship-biased and therefore an upper bound, naming the per-region delta as the size of the bias.

- [ ] **Step 6: Commit**
```bash
git add trading_algo/run_backtest.py tests/test_run_backtest.py
git commit -m "feat(survivorship): report the point-in-time bias per region

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

and, once the data lands (a separate commit, so the wiring and the measurement are distinguishable):
```bash
git add data/constituents/ trading_algo/regions.py trading_algo/config.py docs/research/SURVIVORSHIP_BIAS.md
git commit -m "feat(survivorship): wire point-in-time membership and publish the bias

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

# Appendix: what the drafters found when the brief met the real code

Each drafter opened the files it was writing tasks about. These are the places where the brief was wrong, incomplete, or ran into something worth knowing before you start. Read the group's entries before starting its tasks.

## Phase 1 — The instruments: a measurement semantic layer

- CAGR has an off-by-one that the contract's `cagr(equity, *, periods_per_year)` signature forces me to preserve rather than fix. Today `metrics.py:30` computes the exponent as `252 / len(rets)` where `rets` is the DROPNA'd return series; the textbook exponent is `ppy / (len(equity) - 1)`. I verified every call site passes matched-length series — `backtest.py:200-201` (`ret_series` and `eq` both indexed `dates[1:]`), `portfolio_backtest.py:112-113` (`returns = equity.pct_change().fillna(0.0)`), `forex/fx_backtest.py:209` — so defining `cagr` with `n = len(equity)` is bit-for-bit identical TODAY, and the task says so in the docstring. But it bakes a known-wrong convention into the new primitive. It is a one-line fix worth about 0.03% of the exponent on a 14-year daily run and much more on a short paper book; it belongs in the same block, as its own task, with the bridge line it moves.
- `metrics.benchmark_stats` (trading_algo/metrics.py:59-87) still contains four independent `* 252` and one `np.sqrt(252)` — `bench_cagr`, `strat_cagr`, `alpha` and the tracking error. It is inside metrics.py, so the AST test exempts it, and it ignores the new `periods_per_year` default entirely: the portfolio backtest's benchmark stats are hardcoded to 252 regardless of bar spacing. My brief does not cover it and I did not touch it, but it is the same defect as Task 1's, hiding behind the module boundary. It needs a task: `benchmark_stats(..., periods_per_year: float | None = None)` routed through `cagr`/`annualised_vol`.
- The AST test cannot pass with only `forex/nn.py` and `forex/evolve.py` allowlisted, as the brief states. I ran the checker over the real tree: after Tasks 1-4 land, ten further functions still compute a measure locally — `signals.realised_vol:63`, `crowding.crowding_report:62-63`, `data_quality.assess:156`, `validation.sharpe_ratio:85`, `validation.probabilistic_sharpe_ratio:98`, `forex/research.run_research:127`, `forex/dashboard._min_track_record_days:519`, `forex/dashboard._risk_costs:562`, `forex/fx_book.status:758`, `forex/ml_backtest._annual_metrics:97,103`, `paper_trade.status:991-992`. I split them into a permanent `_MEASUREMENT_INPUTS` list (spec §4's boundary: sizing/gating/statistical inputs, not reported figures) and a `_PENDING_DISPLAY_SITES` list of four genuine open findings. The pending four are numbers a human reads and must be routed by a later task in this block; until then the gate is weaker than the spec's wording implies.
- `paper_trade.status:991-992` and `forex/fx_book.status:758` print 'Ann. vol' and 'Max drawdown' from their own maths, and `paper_trade` hardcodes `np.sqrt(252)` where `fx_book` at least derives ppy. `forex/ml_backtest._annual_metrics:97-103` computes a Sharpe, a CAGR and a MaxDD for the walk-forward report card. These are four ~2-line fixes and exactly the 'display values re-derived through the layer' the spec asks for, but they sit outside my brief's four files and outside any other group's brief I was shown. If nobody owns them, P1's gate ('no independent metric implementation outside the module') is not actually met.
- Lo's standard error in `Measure.stderr` reimplements the `(1.0 + 0.5 * sr ** 2) / (len(r) - 1)` expression that already exists at `trading_algo/validation.py:131`, inside `probabilistic_sharpe_ratio`. Extracting it from validation.py would be drive-by refactoring of a function this change does not otherwise touch (spec §13), so I duplicated the formula and am recording it rather than fixing it. If the reviewer would rather have one copy, the extraction is its own small task.
- Task 4 deletes JavaScript that computed metrics on the SLICED curve with a population standard deviation (`/r.length`) and a hardcoded `RF=0.035`, while Python uses ddof=1 and `FX_RISK_FREE`. Moving the computation to Python therefore CHANGES the displayed 1W/1M/3M numbers on every FX page — that is the defect being fixed, not a regression, but it is a published number moving and belongs in the bridge evidence for P1. The ALL column is unaffected (the new `_period_metrics['0']` equals the existing `book_metrics`), which the test pins.
- `trading_algo/forex/fx_config.py` gains an import of `trading_algo.metrics`, a new package edge from the FX subsystem into the equity stack's measurement module. `metrics.py` imports only `.config`, numpy and pandas, so there is no cycle, but `fx_config` is imported very early by most FX modules and this is the first time it pulls in the top-level package's measurement layer. Step 5 of Task 1 is where a cycle would surface; if it does, the fallback is to leave `ANNUALIZATION = 252` defined in metrics.py only and delete the fx_config name outright (nothing but `marks.py` imported it, and `marks.py` stops needing it in the same task).
- Task 2 changes the tearsheet's drawdown to be measured on the date-SORTED equity curve (matching `attribution.equity_returns`, which already sorts) instead of raw `equity_history` list order. Every real paper book appends chronologically so the number is identical, but a state file with out-of-order marks would now report a different max drawdown. I judged matching the returns series the right behaviour and flagged it in the task text rather than preserving the unsorted loop.

## Phase 2 — The instruments: the reconciliation bridge

- The locked signature `reconcile(paper_state, predicted_equity)` cannot compute three of its eight lines. `exposure_gap` needs the gated target gross per rebalance date, `rebalance_timing` needs the month-end counterfactual book, and `fx_translation` needs trade-date FX — all of which need price data the two arguments do not carry. Task 6 returns 0.0 for them with a note and lets the residual hold their content (which spec §5 explicitly sanctions), but the bridge will NOT close to 1bp on the real books until those three are computable. The natural seam is for Task 5's now-correct diagnosis to persist its per-sleeve (held gross, target gross, asof) into `state["tracking_diagnosis"]` on each run, which `reconcile` could then read without changing its signature. I deliberately did not add that to Task 5 — it would push a surgical fix into a state-schema change — but whoever owns the exposure-gap line will need it. Flagging so it is a decision, not a surprise.
- The contract's return dict for `reconcile` does not list a `notes` key, but the brief requires dividends and cash_interest to "return 0.0 with a recorded note". Task 6 therefore adds `"notes": dict[str, str]` to the returned dict. Any parallel task consuming `reconcile`'s output should treat `notes` as present; `format_bridge` reads it with `report.get("notes") or {}` so an older report without it still renders.
- `attribution.reconcile` is ~55 lines including its docstring, above spec §13's ~30-line design signal. Called out inside Task 6 as required. It is a new deliverable rather than a fix passing through existing code, and the body is one flat loop plus a dict assembly; the shape to reach for if it grows is a `_line_*` function per bridge line.
- trading_algo/attribution.py:20-24 currently imports only `math` and `pandas`. Task 6 adds `from . import tca` and `from .regions import get_region` at module level. Verified no cycle (tca.py:23 imports regions; neither imports attribution), but it does mean importing `attribution` now pulls in `regions` and therefore `config` — relevant if any consumer relied on attribution being config-free.
- The bridge converts local-currency trade amounts with the book's CURRENT `state["fx_snapshot"]`, not the rate on the trade date, because state stores only the latest snapshot. On `full` (four currencies, ~3 months of history) that approximation is small but real, and it lands in `residual` rather than in `fx_translation`. Noted in the docstring and in the `fx_translation` note. If it turns out to be material, the fix is to stamp the trade-date rate onto each trade record in paper_trade.rebalance_sleeve — a state-schema change, and a separate defect.
- trading_algo/paper_trade.py:1125 does `from . import data, strategy` INSIDE `_print_tracking_diagnosis`, even though both are already imported at module level (line 40). Task 5 extends that line to include `data_quality` rather than deleting it, per "no drive-by refactoring of code a fix passes through". Recording it as a finding: the inner import is dead weight and should be removed by whoever next has a reason to touch that function.
- `ci_regression.compare()` reports an unknown key as drift ("new metric not in baseline"), so Task 8's code change and its `_regression_baseline.json` hand-edit MUST land in the same commit or `test_synthetic_backtest_matches_baseline` fails. The task says so, but it is the one ordering in this group that cannot be split.
- Task 8's `total_cost_fraction` is cumulative cost drag per SLEEVE only. There is no portfolio-level cost number in `run_portfolio_backtest`'s return dict, and computing one would mean adding an allocation-weighted sum — a new computation, out of scope for a gate fix. The four sleeve lines are sufficient: zeroing costs fires all four.

## Phase 3 — Conventions: what both engines compute

- The spec's line reference for the paper fill defect is stale at HEAD. Spec §2 and my brief both cite `paper_trade.py:523` as 'fills at the same close the decision used'; line 523 at 11f876e is inside `fit_long_short_to_lots` (`la = _fit_leg(longs, ...)`). The real sites are `trading_algo/paper_trade.py:649` (`fill = price * (1 + np.sign(delta) * region.slippage_bps / 1e4)`) and the two `rebalance_sleeve` call sites at `trading_algo/paper_trade.py:820` and `trading_algo/paper_trade.py:868`. Task 10 is written against the real ones.
- Task 10's test churn is large and unavoidable. `data.synthetic_region` returns a FIXED panel, so every synthetic run has the same `prices.index[-1]`; a pending target guarded on 'a strictly later bar' can never fill in a test that calls `run_daily` once. 27 call sites across 8 test files (`tests/test_paper_trade.py`, `test_consistency.py:171`, `test_dashboard.py:21`, `test_dashboard_valuation.py:13`, `test_dashboard_terminal.py:54`, `test_dashboard_api_book.py:22,24`, `test_dashboard_colour_convention.py:305,323,398,413,496`, `test_experimental_books.py:125,136,151,153,155`) need the `paper_cycle` fixture. The production diff stays inside one screen; the test diff does not.
- Task 10 delays the breaker's liquidation by one session. `trading_algo/paper_trade.py:816-822` currently liquidates a halted book in the same run; staged, it exits one close later. I chose parity with `backtest.py:186` (`pending = CASH`) deliberately — you cannot liquidate at the close that made the decision — but it is a real, if small, increase in exposure for a live book, and it may push `tests/test_backtest.py:39` (`tight MaxDrawdown >= off MaxDrawdown`) over the line on the synthetic fixture. If it does, that is a finding for Task 14 (the breaker), not something to paper over by loosening the assertion.
- `trading_algo/ci_regression.py` runs as its own CI step (`python -m trading_algo.ci_regression --check`), so the pytest xfail I add in Task 9 does NOT keep the workflow green between Task 9 and Task 20. Whoever owns the pipeline needs to know that the 'Backtest regression gate' step is expected red for the duration of stage 2, or that step must be temporarily skipped. Spec §6 accepts the stale baseline; it does not say what CI does meanwhile.
- Dividend double-counting is a live hazard for any future backtest-side credit. `trading_algo/data.py:117` downloads with `auto_adjust=True`, so the backtest's `pct_change` series ALREADY includes dividends — it is a total-return series. Task 11 is correct only because it credits the PAPER book, which marks at the latest unadjusted close and stores its equity history rather than recomputing it. Crediting dividends anywhere in `backtest.py` would double-count them.
- Task 11 scales dividends by `region.price_scale`, which is wrong for exactly the two names Task 15 is about. `CPG.L` and `IHG.L` are quoted in USD but the FTSE region applies `price_scale=0.01` to every name (`trading_algo/regions.py:111`), so their dividends would be divided by 100 like their prices. After Task 15 lands, `_credit_dividends` must switch from `region.price_scale` to `regions.scale_for(region, ticker)`. Flagging it so Task 15's drafter picks up the second call site.
- Dividend share counts are the CURRENT position, not the position held on the ex-date. `_credit_dividends` credits `sleeve['positions'][t]` for every ex-date in the window since the last run. With daily runs that window is 1-3 days and the two are almost always the same, but after a run gap (stale feed, a weekend outage) a position opened after an ex-date would be credited for it. Stated in the helper's docstring; the exact fix is a position history the book does not keep.
- `data.dividends` reliability for ASX and LSE names is unmeasured — spec §11 lists it as an open question and Task 11 does not answer it. The helper fails soft (prints and credits nothing on any exception), so a bad feed under-credits silently rather than corrupting the book. Before P3's gate is called passed, the dividend bridge line should be checked against a broker statement or an index total-return series for at least one FTSE and one ASX name.
- Task 11 also assumes a Yahoo ex-date always falls on a session the region's calendar considers open. If it does not, `verify.check_closed_market` (`trading_algo/verify.py:243`, which reads `t.get('ticker')` and so covers equity rows) will raise `closed-market-trade` ERROR on a DIV row and, since verify's strict gate runs after every scheduled paper run, fail the job. I did not add an exemption because I have no evidence it happens; if it fires in production the fix is to skip non-fill rows in that check, in the same shape as the `check_costs_charged` edit.
- Task 13 sizes the commission floor off `equity[-1]`, the PRIOR close's NAV, because that is what the brief specifies and what the F6 impact block beside it already uses. Under Task 9's convention the fill happens at today's close, so the strictly correct NAV is `equity[-1] * (1 + r)`. The difference is one bar's return on a cost term and is far below the 1bp bridge tolerance, but it is an approximation rather than an identity, and both call sites should move together if anyone tightens it.
- `sleeve['interest_accrued']` is a stored cumulative rather than a ledger row, so unlike dividends it is not reconstructible from the trade log. I chose it to avoid ~250 INT rows per sleeve per year in the blotter, and taught `verify.reconcile_equity` to add it back. The consequence: if that field is ever lost or hand-edited, the cash reconciliation silently re-balances around the wrong number, where a ledgered event could not. Task 5-8's bridge should read it and cross-check it against its own accrual rather than trusting it.
- `rebalanced_this_run` changes meaning in Task 10, from 'a decision was made' to 'a fill happened'. It gates the cash-only allocation true-up at `trading_algo/paper_trade.py:889`. The new meaning is the right one (cash should be trued up after trading, not after deciding), but it does shift when `PAPER_ALLOCATION_REBALANCE` fires by one session for anyone who turns that flag on.

## Phase 4 — Mechanism: what the books actually do

- HEAD is 6fa6f84 ("docs(spec): record the implementation constraints for the measurement block"), not 11f876e as the brief states, and EVERY line number in my brief is stale. Real locations: the `int()` whole-share rounding is paper_trade.py:598-602, not :476; micro mode is paper_trade.py:548-561, not :460-469; the paper breaker is paper_trade.py:900-926, not :905-920; fees.commission is called from paper_trade.py:653. backtest.py:173-183, forex/fx_backtest.py:175-185 and forex/fx_book.py:578-589 are close enough to the brief. data.py:165 is the dropna inside load_prices, but the region-aware place to report it is load_region at data.py:203.
- Task 15 leaves a real, verified defect unfixed by design (§13 "one defect, one change"): trading_algo/tax.py:126-137 and :170 scale DIVIDENDS by region.price_scale through a per-REGION injectable callable `price_scale(region_key) -> float`. CPG.L and IHG.L dividends are therefore still divided by 100 in the withholding-drag report. Making it per-ticker changes a signature that tests/test_tax.py:102, :125 and :134 monkeypatch with one-arg lambdas — a second defect and a second diff. Recorded as a finding in the task text.
- Task 16 changes the executed share count but deliberately does NOT change the two other `int()` truncations in the same file: `_fit_leg` at paper_trade.py:472 and `notional` at paper_trade.py:519, both inside fit_long_short_to_lots. They are a feasibility probe rather than an executed size, so after Task 16 they under-estimate the executed notional by up to one share per name. The long/short hedge check becomes conservative rather than wrong, but the two now disagree.
- Task 16 reads `region.params.max_weight`, but rebalance_sleeve never receives the account's effective params — paper_trade.py:815 computes them with `_account_params(state, region)` and passes only `targets` down. A profiled book (profiles.py) that overrides max_weight will have its override ignored by the rounding guard. Fixing that means widening rebalance_sleeve's signature, which is a separate change.
- Task 15 makes data_quality.assess exclude CPG.L and IHG.L from the FTSE candidate set on every run, including the synthetic one (data.synthetic_region builds columns from region.universe). That moves the synthetic FTSE sleeve's CAGR and MaxDrawdown and will fail `python -m trading_algo.ci_regression --check` from Task 15 onward until Task 20 lands. Expected and intended, but it means the regression gate is red across Tasks 15-19.
- Task 20 cannot produce the commit message the brief requires unless Tasks 5-8 have landed `paper_trade --reconcile` and `attribution.format_bridge`. Neither exists at HEAD (attribution.py has no `reconcile`). If that group slips, Task 20 has no evidence to attach and should block rather than re-baseline bare.
- tests/test_backtest.py:39 asserts `tight["metrics"]["MaxDrawdown"] >= off["metrics"]["MaxDrawdown"]`. Re-basing the breaker's peak (Task 14) lets a halted book re-enter, so a tight-stop run can in principle now end deeper than the no-stop run on the synthetic ASX path. I have written Task 14 Step 5 to run it first and only replace the assertion if it actually fails, with the replacement spelled out — but it is a premise that may legitimately break.
- tests/test_paper_trade.py:196-200's _KNOWN_STATUSES already omits the "unpriced" status that paper_trade.py:804 can emit, so the set is not authoritative today. Task 19 adds "split-halt" to it; "unpriced" is left as a pre-existing finding rather than a drive-by fix.
- Task 14's integration test (test_backtest_breaker_does_not_latch_on_a_market_that_never_recovers) asserts a halt COUNT, not that the book visibly trades again, because on the constructed path the 200-day trend filter legitimately keeps the book in cash after the crash. The "allowed to trade" claim from the brief is proved by the risk_breaker unit test instead. The integration test still separates the two behaviours cleanly (~1 halt vs ~40), but it is an indirect observable and depends on DEFAULT_PARAMS (min_history_days=300, stock_trend_ma=200, max_gross=1.0) not changing.

## Phase 5 — Regeneration, deploy, restart, and survivorship

- Task 21 adds `Region.benchmark_ticker`, which is NOT in the locked contract. Task 15 also edits the same dataclass block in `trading_algo/regions.py:36` to add `quote_overrides` and `scale_for`. Both additions are defaulted fields so they are semantically independent, but they touch adjacent lines and will conflict textually. Sequence Task 15 before Task 21, or expect a one-hunk merge.
- The brief says `tests/test_close_only_bars.py:457-466` pins the wide FX universe as correct. It does not. Lines 452 and 467 assert `state["symbols"] == list(DEFAULT_UNIVERSE)` symbolically and follow the narrowed list with no edit. The only literal pins are `tests/test_fx_pairs.py:8-12` and the `assert len(pairs.DEFAULT_UNIVERSE) == 16` at `tests/test_fx_pairs.py:134`, and commit f15a118 already rewrites both. Task 23 is therefore smaller than the brief assumes.
- The cherry-pick in Task 23 WILL conflict, in exactly one file. `git diff f15a118^ HEAD -- trading_algo/forex/__init__.py` shows this branch removed `MetaLabeler` from both the import and `__all__`, and f15a118's hunk carries `MetaLabeler` in its context lines. The resolution is given verbatim in the task. All other touched files apply: `tests/test_fx_pairs.py`, `tests/test_fx_ml.py`, `tests/test_multiasset_day.py` and `docs/research/COST_AWARE_OBJECTIVE_RESULT.md` are byte-identical to the commit's parent, and `pairs.py` / `train.py` diverge only outside the picked hunks.
- Task 27's brief assumes the PIT-versus-non-PIT delta can simply be published. It cannot today: `run_backtest.pit_impact` (`trading_algo/run_backtest.py:152-161`) computes the PORTFOLIO CAGR delta only, with no per-sleeve breakdown, and `--compare-pit` prints three lines. A real code change is needed before any data arrives, so I made that Steps 1-4 (unblocked) and gated only the wiring and publication on data.
- Task 24's 'docs tables' surface is materially larger than the brief implies. Hand-copied numbers live in `docs/SHARPE_RESEARCH.md` (lines 58, 165, 218, 350), `docs/MONTE_CARLO_RESEARCH.md:237` (currently UNTRACKED in git) and `CLAUDE.md`'s TSX line ('raw Sharpe 0.948, haircut 0.608, maxDD -15.6%'). None of these is generated; each must be re-read off the new cache and corrected by hand with its before/after, which is real work the brief's four commands do not cover.
- The FX review banner has TWO rendering surfaces, not one: the terminal SPA (`trading_algo/dashboard/static/app.js`, served by `dashboard/fx_api.py`) and the PUBLISHED static page (`trading_algo/forex/dashboard.py`, which `scripts/build_site.sh` exports to `public/fx_{account}.html` on every day-paper and fx-paper run). The public one is the page a reader actually lands on. I put the text in `forex/fx_config.REVIEW_NOTICE` so both consume one definition, but it does mean Task 22 touches six files and needs two commits.
- `state/backtest_equity.json` was generated 2026-07-24 and predates three blocks the exporter now writes (`benchmark_stats`, `allocations`, `fx_rebalance_cost`). The BACKTEST tab is currently rendering a payload shape the code no longer produces, so the staleness is not merely numerical - some fields the frontend reads are absent entirely. Worth checking the tab renders at all before and after regeneration.
- Task 26's FX restart needs `--force`. `fx_book.init_defaults` (`forex/fx_book.py:329-337`) SKIPS an existing book silently rather than raising, unlike the equity `init_account` which raises SystemExit. Without `--force` the restart will report success and change nothing. Separately, `--force` rewrites the JSON state but I found no code path that purges the corresponding rows from `state/fx_books.db` / `state/paper_books.db`; the archive copies both DBs, but whether stale rows survive into the reopened books needs a check on the first clean run.
- Task 21's diff is ~32 lines across four files, at the spec section 13 threshold. I judged it one idea and kept it as one commit, because splitting the label off would ship an unlabelled benchmark number for the intervening commit - which is the exact defect. Flagging per the constraint rather than pushing through silently.
- Two facts in the brief have drifted. HEAD is 6fa6f84, not 11f876e (three doc commits landed after the review). And `git log --oneline main..HEAD | wc -l` is 46, not the spec's 41, so Task 25 merges more than the spec's risk table anticipated.
- Confirmed the Task 23 -> Task 26 ordering the brief flags, and the mechanism is worse than 'reopen on 16': `fx_book.run_once` at `forex/fx_book.py:411-416` merges via `list(dict.fromkeys([*state["symbols"], *DEFAULT_UNIVERSE]))`, a UNION that never removes. A book reopened on 16 symbols can never shed the crosses by any later config change - only by another `--init --force`.

