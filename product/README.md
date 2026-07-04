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
| [`schema/backlog_item.schema.json`](schema/backlog_item.schema.json) | JSON Schema (draft 2020-12) for one backlog item |
| [`schema/rollout.schema.json`](schema/rollout.schema.json) | JSON Schema for a rollout plan |
| [`validate.py`](validate.py) | Zero-dependency validator + CI gate over the backlog |

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

## How this connects to the code

The backlog is grounded in the real codebase, not invented: F1 builds on the
existing `constituents.py` PIT mechanism; F2/F8 reuse the FX subsystem's
`forex/validation.py` and `forex/walkforward.py`; F3/F15 must route through
`strategy.compute_targets` (invariant #3); F10 hardens `execution_ibkr.py`; F12
wires the existing `config` drawdown breaker to a notification channel. See each
item's `evidence` and the Chief Engineer review for the exact plug-in points.
