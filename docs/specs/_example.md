---
title: Dashboard drawdown alert
slug: dashboard-drawdown-alert
status: example          # ← worked example of the schema, NOT a real spec
created: 2026-07-04
last-updated: 2026-07-04
owner: matt
---

> **This file is a worked example** showing what a fully populated spec looks
> like. It is not an agreed piece of work. Real specs live alongside it in
> `docs/specs/` with `status: draft/agreed/in-progress/done`.

# Dashboard drawdown alert

## Context
The paper-trading dashboard (`trading_algo/dashboard/`) shows combined AUD
equity but gives no visual warning as a sleeve approaches the drawdown
circuit breaker configured in `config.py`. An operator has to compute the
distance to the breaker by hand. Surfacing it directly makes the risk
control observable at a glance.

## Goals
- **G-1**: An operator can see each sleeve's current drawdown and its
  distance to the circuit-breaker threshold without leaving the dashboard.
- **G-2**: A sleeve within 2 percentage points of its breaker is visually
  flagged (amber), and a tripped breaker is unmistakable (red).

## Non-goals
- **NG-1**: No push/email notifications — this is an on-screen indicator
  only; alerting infrastructure is a separate piece of work.
- **NG-2**: No change to the circuit-breaker logic itself; the dashboard
  only reads state, it never influences trading.

## Acceptance criteria
| ID   | Criterion (observable behaviour) | Verified by | Status |
|------|----------------------------------|-------------|--------|
| AC-1 | Dashboard JSON API includes per-sleeve `drawdown` and `breaker_threshold` fields | `tests/test_dashboard.py::test_drawdown_fields` | ☐ |
| AC-2 | Sleeve tile turns amber when drawdown is within 2 pp of the threshold, red when at/past it | `tests/test_dashboard.py::test_alert_levels` | ☐ |
| AC-3 | Dashboard still renders when a sub-book state file is missing (no crash, tile shows "no data") | `tests/test_dashboard.py::test_missing_state` | ☐ |
| AC-4 | No new third-party dependencies (dashboard stays stdlib-only) | manual: inspect imports + `requirements.txt` diff | ☐ |

## Constraints & invariants
- Invariants touched: **none of 1–6** — the dashboard is read-only reporting.
  In particular invariant 6 still holds: drawdown is computed per sleeve in
  local currency; only the combined view is AUD.
- Dashboard must remain zero-dependency (stdlib server + vanilla SPA), per
  `CLAUDE.md` architecture notes.

## Verification plan
```bash
pytest -q tests/test_dashboard.py
python -m trading_algo.dashboard --account full   # manual: view tiles at :8787
```

## Open questions
- **Q-2**: Should the amber margin (2 pp) be a `config.py` knob or a
  dashboard constant?

## Decision log
| Date       | Decision | Who |
|------------|----------|-----|
| 2026-07-04 | Q-1: alert is visual-only; notifications are out of scope (→ NG-1) | matt |
