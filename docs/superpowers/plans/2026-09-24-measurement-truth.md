# Measurement Truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** DRAFTING IN PROGRESS — Tasks 1-8 and 14-20 are written. Tasks 9-13
(conventions) and 21-27 (regeneration, deploy, restart, survivorship) were still
being drafted when this file was written; see "Gaps" below. Do not start Task 9
until it is written.

**Goal:** Make every reported number in this repo mean what it says, so a strategy
result can be told apart from a measurement artefact.

**Architecture:** One measurement semantic layer (`metrics.py`) that owns every
number a human reads, enforced by an AST test; one reconciliation bridge
(`attribution.py`) that walks from the backtest's return to the paper book's
return line by line and must sum to an identity; then the convention and mechanism
fixes the bridge can prove, then regeneration, deploy and a clean book restart.
Point-in-time index membership runs as a parallel wave because it never touches a
live book.

**Tech Stack:** Python 3.11+, pandas, numpy, pytest, plain stdlib elsewhere. No
new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-24-measurement-truth-design.md` — read it
first; this plan argues from it and the two travel together.

## Global Constraints

- **One defect, one change.** No drive-by refactoring of code a fix passes through. Adjacent problems become findings, not diffs. (Spec §13.)
- **Fix the concept, not the call sites.** Prefer removing a special case to adding one.
- **Every change carries its test and its number** — test first, then the bridge line it moved, before and after.
- **A fix needing more than ~30 lines is a design signal.** Say so in the task rather than pushing through.
- **No new module unless no existing one can host it.** `metrics.py` and `attribution.py` are the homes; `risk_breaker.py` is the one sanctioned new module.
- **Diffs stay readable in one screen.** Matt reads every diff; that is the review mechanism.
- **Never write to `state/`.** Any command that might must run with `FX_STATE_DIR` and `MOMENTUM_STATE_DIR` exported to a scratch directory.
- **Python 3.11+, type hints, `from __future__ import annotations`** at the top of new modules.
- **Every commit ends with:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Line numbers are stale — re-locate before editing

The drafting agents found that HEAD has moved (it is now `6fa6f84`, not `11f876e`)
and **every line number inherited from the review is stale**. Verified real
locations at the time of drafting:

| What | Stale reference | Actual |
|---|---|---|
| whole-share `int()` truncation | `paper_trade.py:476` | `paper_trade.py:598-602` |
| micro mode | `paper_trade.py:460-469` | `paper_trade.py:548-561` |
| paper drawdown breaker | `paper_trade.py:905-920` | `paper_trade.py:900-926` |

**Grep for the code, do not trust a line number in this plan or the spec.**

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

## Gaps — not yet drafted

**Tasks 9-13 — Conventions.** Next-day-close fills in `backtest.py` and
`paper_trade.py`, dividends credited to the paper ledger, cash interest in the
paper books via the existing `fees.idle_cash_credit`, and the per-order commission
floor charged in the backtest. Task 10 (paper fill timing) is the one structural
change in the block: the book must decide on the latest close and fill on the next
run's close, which means persisting a pending target in sleeve state.

**Tasks 21-27 — Regeneration, deploy, restart, survivorship.** Total-return
benchmark; FX books out of the headline AUM via their own reporting group; the
`TRAINING_UNIVERSE` / `DEFAULT_UNIVERSE` split (cherry-pick `f15a118`, which **must
land before the restart** or the reopened books come up on 16 symbols); regenerate
every published number; merge to `main`; archive and restart the books; and wire
point-in-time membership once the data is sourced.

---

## Known deviations and concerns raised during drafting

These are the drafters' own findings, where the brief met the real code. Each is
worth reading before starting the task it belongs to.


### From tasks1-4

- CAGR has an off-by-one that the contract's `cagr(equity, *, periods_per_year)` signature forces me to preserve rather than fix. Today `metrics.py:30` computes the exponent as `252 / len(rets)` where `rets` is the DROPNA'd return series; the textbook exponent is `ppy / (len(equity) - 1)`. I verified every call site passes matched-length series — `backtest.py:200-201` (`ret_series` and `eq` both indexed `dates[1:]`), `portfolio_backtest.py:112-113` (`returns = equity.pct_change().fillna(0.0)`), `forex/fx_backtest.py:209` — so defining `cagr` with `n = len(equity)` is bit-for-bit identical TODAY, and the task says so in the docstring. But it bakes a known-wrong convention into the new primitive. It is a one-line fix worth about 0.03% of the exponent on a 14-year daily run and much more on a short paper book; it belongs in the same block, as its own task, with the bridge line it moves.
- `metrics.benchmark_stats` (trading_algo/metrics.py:59-87) still contains four independent `* 252` and one `np.sqrt(252)` — `bench_cagr`, `strat_cagr`, `alpha` and the tracking error. It is inside metrics.py, so the AST test exempts it, and it ignores the new `periods_per_year` default entirely: the portfolio backtest's benchmark stats are hardcoded to 252 regardless of bar spacing. My brief does not cover it and I did not touch it, but it is the same defect as Task 1's, hiding behind the module boundary. It needs a task: `benchmark_stats(..., periods_per_year: float | None = None)` routed through `cagr`/`annualised_vol`.
- The AST test cannot pass with only `forex/nn.py` and `forex/evolve.py` allowlisted, as the brief states. I ran the checker over the real tree: after Tasks 1-4 land, ten further functions still compute a measure locally — `signals.realised_vol:63`, `crowding.crowding_report:62-63`, `data_quality.assess:156`, `validation.sharpe_ratio:85`, `validation.probabilistic_sharpe_ratio:98`, `forex/research.run_research:127`, `forex/dashboard._min_track_record_days:519`, `forex/dashboard._risk_costs:562`, `forex/fx_book.status:758`, `forex/ml_backtest._annual_metrics:97,103`, `paper_trade.status:991-992`. I split them into a permanent `_MEASUREMENT_INPUTS` list (spec §4's boundary: sizing/gating/statistical inputs, not reported figures) and a `_PENDING_DISPLAY_SITES` list of four genuine open findings. The pending four are numbers a human reads and must be routed by a later task in this block; until then the gate is weaker than the spec's wording implies.
- `paper_trade.status:991-992` and `forex/fx_book.status:758` print 'Ann. vol' and 'Max drawdown' from their own maths, and `paper_trade` hardcodes `np.sqrt(252)` where `fx_book` at least derives ppy. `forex/ml_backtest._annual_metrics:97-103` computes a Sharpe, a CAGR and a MaxDD for the walk-forward report card. These are four ~2-line fixes and exactly the 'display values re-derived through the layer' the spec asks for, but they sit outside my brief's four files and outside any other group's brief I was shown. If nobody owns them, P1's gate ('no independent metric implementation outside the module') is not actually met.
- Lo's standard error in `Measure.stderr` reimplements the `(1.0 + 0.5 * sr ** 2) / (len(r) - 1)` expression that already exists at `trading_algo/validation.py:131`, inside `probabilistic_sharpe_ratio`. Extracting it from validation.py would be drive-by refactoring of a function this change does not otherwise touch (spec §13), so I duplicated the formula and am recording it rather than fixing it. If the reviewer would rather have one copy, the extraction is its own small task.
- Task 4 deletes JavaScript that computed metrics on the SLICED curve with a population standard deviation (`/r.length`) and a hardcoded `RF=0.035`, while Python uses ddof=1 and `FX_RISK_FREE`. Moving the computation to Python therefore CHANGES the displayed 1W/1M/3M numbers on every FX page — that is the defect being fixed, not a regression, but it is a published number moving and belongs in the bridge evidence for P1. The ALL column is unaffected (the new `_period_metrics['0']` equals the existing `book_metrics`), which the test pins.
- `trading_algo/forex/fx_config.py` gains an import of `trading_algo.metrics`, a new package edge from the FX subsystem into the equity stack's measurement module. `metrics.py` imports only `.config`, numpy and pandas, so there is no cycle, but `fx_config` is imported very early by most FX modules and this is the first time it pulls in the top-level package's measurement layer. Step 5 of Task 1 is where a cycle would surface; if it does, the fallback is to leave `ANNUALIZATION = 252` defined in metrics.py only and delete the fx_config name outright (nothing but `marks.py` imported it, and `marks.py` stops needing it in the same task).
- Task 2 changes the tearsheet's drawdown to be measured on the date-SORTED equity curve (matching `attribution.equity_returns`, which already sorts) instead of raw `equity_history` list order. Every real paper book appends chronologically so the number is identical, but a state file with out-of-order marks would now report a different max drawdown. I judged matching the returns series the right behaviour and flagged it in the task text rather than preserving the unsorted loop.

### From tasks5-8

- The locked signature `reconcile(paper_state, predicted_equity)` cannot compute three of its eight lines. `exposure_gap` needs the gated target gross per rebalance date, `rebalance_timing` needs the month-end counterfactual book, and `fx_translation` needs trade-date FX — all of which need price data the two arguments do not carry. Task 6 returns 0.0 for them with a note and lets the residual hold their content (which spec §5 explicitly sanctions), but the bridge will NOT close to 1bp on the real books until those three are computable. The natural seam is for Task 5's now-correct diagnosis to persist its per-sleeve (held gross, target gross, asof) into `state["tracking_diagnosis"]` on each run, which `reconcile` could then read without changing its signature. I deliberately did not add that to Task 5 — it would push a surgical fix into a state-schema change — but whoever owns the exposure-gap line will need it. Flagging so it is a decision, not a surprise.
- The contract's return dict for `reconcile` does not list a `notes` key, but the brief requires dividends and cash_interest to "return 0.0 with a recorded note". Task 6 therefore adds `"notes": dict[str, str]` to the returned dict. Any parallel task consuming `reconcile`'s output should treat `notes` as present; `format_bridge` reads it with `report.get("notes") or {}` so an older report without it still renders.
- `attribution.reconcile` is ~55 lines including its docstring, above spec §13's ~30-line design signal. Called out inside Task 6 as required. It is a new deliverable rather than a fix passing through existing code, and the body is one flat loop plus a dict assembly; the shape to reach for if it grows is a `_line_*` function per bridge line.
- trading_algo/attribution.py:20-24 currently imports only `math` and `pandas`. Task 6 adds `from . import tca` and `from .regions import get_region` at module level. Verified no cycle (tca.py:23 imports regions; neither imports attribution), but it does mean importing `attribution` now pulls in `regions` and therefore `config` — relevant if any consumer relied on attribution being config-free.
- The bridge converts local-currency trade amounts with the book's CURRENT `state["fx_snapshot"]`, not the rate on the trade date, because state stores only the latest snapshot. On `full` (four currencies, ~3 months of history) that approximation is small but real, and it lands in `residual` rather than in `fx_translation`. Noted in the docstring and in the `fx_translation` note. If it turns out to be material, the fix is to stamp the trade-date rate onto each trade record in paper_trade.rebalance_sleeve — a state-schema change, and a separate defect.
- trading_algo/paper_trade.py:1125 does `from . import data, strategy` INSIDE `_print_tracking_diagnosis`, even though both are already imported at module level (line 40). Task 5 extends that line to include `data_quality` rather than deleting it, per "no drive-by refactoring of code a fix passes through". Recording it as a finding: the inner import is dead weight and should be removed by whoever next has a reason to touch that function.
- `ci_regression.compare()` reports an unknown key as drift ("new metric not in baseline"), so Task 8's code change and its `_regression_baseline.json` hand-edit MUST land in the same commit or `test_synthetic_backtest_matches_baseline` fails. The task says so, but it is the one ordering in this group that cannot be split.
- Task 8's `total_cost_fraction` is cumulative cost drag per SLEEVE only. There is no portfolio-level cost number in `run_portfolio_backtest`'s return dict, and computing one would mean adding an allocation-weighted sum — a new computation, out of scope for a gate fix. The four sleeve lines are sufficient: zeroing costs fires all four.

### From tasks14-20

- HEAD is 6fa6f84 ("docs(spec): record the implementation constraints for the measurement block"), not 11f876e as the brief states, and EVERY line number in my brief is stale. Real locations: the `int()` whole-share rounding is paper_trade.py:598-602, not :476; micro mode is paper_trade.py:548-561, not :460-469; the paper breaker is paper_trade.py:900-926, not :905-920; fees.commission is called from paper_trade.py:653. backtest.py:173-183, forex/fx_backtest.py:175-185 and forex/fx_book.py:578-589 are close enough to the brief. data.py:165 is the dropna inside load_prices, but the region-aware place to report it is load_region at data.py:203.
- Task 15 leaves a real, verified defect unfixed by design (§13 "one defect, one change"): trading_algo/tax.py:126-137 and :170 scale DIVIDENDS by region.price_scale through a per-REGION injectable callable `price_scale(region_key) -> float`. CPG.L and IHG.L dividends are therefore still divided by 100 in the withholding-drag report. Making it per-ticker changes a signature that tests/test_tax.py:102, :125 and :134 monkeypatch with one-arg lambdas — a second defect and a second diff. Recorded as a finding in the task text.
- Task 16 changes the executed share count but deliberately does NOT change the two other `int()` truncations in the same file: `_fit_leg` at paper_trade.py:472 and `notional` at paper_trade.py:519, both inside fit_long_short_to_lots. They are a feasibility probe rather than an executed size, so after Task 16 they under-estimate the executed notional by up to one share per name. The long/short hedge check becomes conservative rather than wrong, but the two now disagree.
- Task 16 reads `region.params.max_weight`, but rebalance_sleeve never receives the account's effective params — paper_trade.py:815 computes them with `_account_params(state, region)` and passes only `targets` down. A profiled book (profiles.py) that overrides max_weight will have its override ignored by the rounding guard. Fixing that means widening rebalance_sleeve's signature, which is a separate change.
- Task 15 makes data_quality.assess exclude CPG.L and IHG.L from the FTSE candidate set on every run, including the synthetic one (data.synthetic_region builds columns from region.universe). That moves the synthetic FTSE sleeve's CAGR and MaxDrawdown and will fail `python -m trading_algo.ci_regression --check` from Task 15 onward until Task 20 lands. Expected and intended, but it means the regression gate is red across Tasks 15-19.
- Task 20 cannot produce the commit message the brief requires unless Tasks 5-8 have landed `paper_trade --reconcile` and `attribution.format_bridge`. Neither exists at HEAD (attribution.py has no `reconcile`). If that group slips, Task 20 has no evidence to attach and should block rather than re-baseline bare.
- tests/test_backtest.py:39 asserts `tight["metrics"]["MaxDrawdown"] >= off["metrics"]["MaxDrawdown"]`. Re-basing the breaker's peak (Task 14) lets a halted book re-enter, so a tight-stop run can in principle now end deeper than the no-stop run on the synthetic ASX path. I have written Task 14 Step 5 to run it first and only replace the assertion if it actually fails, with the replacement spelled out — but it is a premise that may legitimately break.
- tests/test_paper_trade.py:196-200's _KNOWN_STATUSES already omits the "unpriced" status that paper_trade.py:804 can emit, so the set is not authoritative today. Task 19 adds "split-halt" to it; "unpriced" is left as a pre-existing finding rather than a drive-by fix.
- Task 14's integration test (test_backtest_breaker_does_not_latch_on_a_market_that_never_recovers) asserts a halt COUNT, not that the book visibly trades again, because on the constructed path the 200-day trend filter legitimately keeps the book in cash after the crash. The "allowed to trade" claim from the brief is proved by the risk_breaker unit test instead. The integration test still separates the two behaviours cleanly (~1 halt vs ~40), but it is an indirect observable and depends on DEFAULT_PARAMS (min_history_days=300, stock_trend_ma=200, max_gross=1.0) not changing.
