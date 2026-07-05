# Product — the operating model

This directory is the **product operating model** for the Trading-Algo platform:
a standing team of four roles, a continuous evidence-graded backlog, and the
schemas we use to roll features out safely. It is the answer to "what should we
build next, why do we believe it, how will we know it worked, and how do we ship
it without breaking the book?"

Nothing here changes trading behaviour. It is the process layer that decides and
governs what the trading code becomes.

## Contents

| File | What it is |
|---|---|
| [`roles.md`](roles.md) | The four roles — Product Owner, Data Scientist, Chief Engineer, Algo Trader — their decision rights, vetoes, and critique lens |
| [`PROCESS.md`](PROCESS.md) | The continuous backlog loop, state machine, Definition of Ready/Done, RICE, invariant mapping, rollout discipline, cadence |
| [`backlog/backlog.json`](backlog/backlog.json) | The live backlog — every item researched, critiqued by all four roles, scored, with acceptance criteria and a rollout plan |
| [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) | The Architect + Chief Engineer sustainability review: shared foundations, prerequisite refactors, phased build order, invariant guards |
| [`backlog/build_plan.json`](backlog/build_plan.json) | Machine-readable build plan — the 19 items sequenced into 5 dependency-ordered phases behind 9 foundations |
| [`schema/backlog_item.schema.json`](schema/backlog_item.schema.json) | JSON Schema (draft 2020-12) for one backlog item |
| [`schema/rollout.schema.json`](schema/rollout.schema.json) | JSON Schema for a rollout plan |
| [`schema/build_plan.schema.json`](schema/build_plan.schema.json) | JSON Schema for the build plan |
| [`validate.py`](validate.py) | Zero-dependency validator + CI gate over the backlog *and* the build plan |

## Quick start

```bash
python product/validate.py          # validate the backlog (exit non-zero on failure)
```

`tests/test_product_backlog.py` runs the same validation under `pytest -q`, so a
malformed or under-specified item fails CI.

## The four lenses (see roles.md)

Every item is put through four adversarial lenses before it can ship:

- **Product Owner** — *"Is this the most valuable thing next, and what does the
  user's 'done' look like?"*
- **Data Scientist** — *"What's the evidence, what would falsify it, and what
  number proves it worked?"*
- **Chief Engineer** — *"Where does it plug in, which invariant could it break,
  and how do we turn it off?"*
- **Algo Trader** — *"Does it protect or grow the book, and what stops it hurting
  me live?"*

An item cannot leave `groomed` until all four have recorded a verdict — enforced
by `validate.py`.

## The backlog at a glance

19 items across seven epics, seeded from the open roadmap in
[`HANDOFF.md`](../HANDOFF.md), the bias research in
[`docs/research/COMBATING_BACKTEST_BIAS.md`](../docs/research/COMBATING_BACKTEST_BIAS.md),
and a file-grounded read of the codebase. Highest-RICE, ready-to-build items:

| id | Title | Epic | RICE | Status |
|---|---|---|---:|---|
| F10 | Paper→live promotion gate + min-account-size check | risk-and-guardrails | 19.2 | groomed |
| F12 | Drawdown circuit-breaker telemetry & alerting | risk-and-guardrails | 18.0 | ready |
| F16 | CI backtest regression gate | platform-and-ci | 16.0 | ready |
| F2 | Deflated Sharpe + PBO gate (equity) | statistical-validity | 14.0 | ready |
| F7 | Data-quality gate before signals | data-integrity | 10.0 | ready |
| F15 | Pre-trade ADV / liquidity cap | execution-quality | 9.6 | groomed |
| F1 | Point-in-time constituents (kill survivorship bias) | data-integrity | 9.0 | ready |

The full list, evidence, four-role critiques, acceptance criteria and rollout
plans are in [`backlog/backlog.json`](backlog/backlog.json). RICE is recomputed
by the validator, so the ranking can't drift from its inputs.

## Are we building this sustainably?

Yes — and it's checked, not asserted. The whole backlog was run through the
Architect and the Chief Engineer ([`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md)),
producing a machine-readable [`build_plan.json`](backlog/build_plan.json) that
sequences all 19 items into **5 dependency-ordered phases behind 9 shared
foundations**. The key finding: four features are actually platform primitives
(F7, F11, F17, F18) and are built first; four prerequisite refactors (R1–R4)
collapse divergence that already exists before it spreads — most importantly
**R4**, which upgrades `test_consistency.py` from a text grep to numeric
weight/cost equality so invariant #3 (one weight function) can't be bypassed.

```
Phase 0 — Foundations & safety        F7  F16 F17 F18   (+ R1 R2 R3 R4)
Phase 1 — Statistical validity        F8  F2  F19
Phase 2 — Data integrity              F1  F13 F14
Phase 3 — Execution realism & capacity F6 F15 F11 F12 F9
Phase 4 — Live readiness & reporting  F3  F10 F4  F5
```

`validate.py` asserts every item is placed exactly once and no feature is
scheduled before something it depends on, so the plan can't silently rot as items
move.

## How this connects to the code

The backlog is grounded in the real codebase, not invented: F1 builds on the
existing `constituents.py` PIT mechanism; F2/F8 reuse the FX subsystem's
`forex/validation.py` and `forex/walkforward.py`; F3/F15 must route through
`strategy.compute_targets` (invariant #3); F10 hardens `execution_ibkr.py`; F12
wires the existing `config` drawdown breaker to a notification channel. See each
item's `evidence` and the Chief Engineer review for the exact plug-in points.
