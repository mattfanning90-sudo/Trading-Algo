# The continuous backlog process

A lightweight, evidence-first operating model for turning ideas into shipped
features without breaking the [CLAUDE.md invariants](../CLAUDE.md#invariants---do-not-break-these)
or putting capital at risk. It is deliberately the same discipline the trading
side applies to money — hypothesis, evidence, staged exposure, guardrails,
rollback — applied to *shipping*.

Everything here is machine-checkable: the backlog lives in
[`backlog/backlog.json`](backlog/backlog.json), the contract is
[`schema/backlog_item.schema.json`](schema/backlog_item.schema.json) +
[`schema/rollout.schema.json`](schema/rollout.schema.json), and
[`validate.py`](validate.py) fails CI if an item is under-specified.

---

## The loop

```
   intake ──► research ──► critique (4 lenses) ──► ready ──► build ──► rollout ──► measure
      ▲                                                                              │
      └──────────────────────  new evidence re-opens items  ◄─────────────────────┘
```

It is *continuous*: measurement feeds new evidence, which re-prioritises the
backlog. Items are never "closed and forgotten" — a guardrail breach or a live
result that contradicts the hypothesis re-opens the item.

## State machine

Each item's `status` moves through:

| Status | Meaning | Entry gate |
|---|---|---|
| `proposed` | Captured, not yet researched | A problem statement exists |
| `researching` | Gathering evidence | Assigned to PO + Data Scientist |
| `groomed` | Critiqued by all four roles | **All four `role_reviews` recorded** |
| `ready` | Buildable now | Acceptance criteria + rollout plan complete; no `no`-veto from the owning role |
| `in_progress` | Being built | Effort budgeted; branch open |
| `in_rollout` | Merged, staging through the rollout | Tests green; invariants held |
| `done` | Shipped and outcome met | Every acceptance criterion satisfied |
| `parked` | Valid but not now | PO decision; kept for revisit |
| `rejected` | Won't do | Recorded with the reason |

`validate.py` enforces the hard gates structurally (four reviews, evidence,
acceptance criteria, rollout completeness, RICE arithmetic).

## Definition of Ready (to build)

An item is `ready` only when **all** hold:

1. **Problem + hypothesis** are stated so the hypothesis can be falsified.
2. **≥ 1 evidence** entry, each with a `source` (repo path or citation) and a
   `confidence`. No assertion without a source.
3. **All four `role_reviews`** are present. No `no` verdict from the role that
   *owns* the risk (Chief Engineer on invariants, Algo Trader on live paths).
4. **Acceptance criteria** are binary; every `measurable: true` criterion has a
   `metric` (a threshold or a named test).
5. **`invariants_touched`** is filled in (use `["none"]` if it genuinely touches
   none) and each risked invariant has a guardrail in the rollout.
6. A **rollout plan**: mechanism, default-off flag (or a justified default-on),
   ≥ 1 stage with exit criteria, ≥ 1 guardrail metric, and a rollback.

## Definition of Done

* Every acceptance criterion is met; measurable ones are backed by a green test
  or a recorded metric.
* No invariant regressed (`tests/test_consistency.py` and the no-lookahead /
  costs-on tests stay green).
* The feature reached its final rollout stage with guardrail metrics intact.
* The user-facing outcome the Product Owner defined is observably true.

## Prioritisation — RICE

`score = reach × impact × confidence ÷ effort`

* **reach** 1–10 — how many books/sleeves/decisions it touches.
* **impact** ∈ {0.25, 0.5, 1, 2, 3} — Minimal → Massive.
* **confidence** ∈ {0.5, 0.8, 1.0} — how sure we are of reach × impact.
* **effort** person-weeks (S≈1, M≈3, L≈6).

The validator recomputes the score, so it can't drift from its inputs. Ties and
sequencing are broken by dependencies and the Product Owner's ranking.

## Invariant mapping (the engineering guardrails)

Every item declares which [invariants](../CLAUDE.md#invariants---do-not-break-these)
it risks. The common ones and how items protect them:

| Invariant | Typical risk | Guardrail pattern |
|---|---|---|
| `1-no-lookahead` | New feature/data uses t+1 info at t | Trailing-only; boundary tests; purge/embargo |
| `2-costs-always-on` | A cost path skipped | Costs charged on every path; regression vs baseline |
| `3-one-weight-function` | A second copy of sizing logic | Route through `strategy.compute_targets`; `test_consistency.py` |
| `4-whole-shares` | Fractional / underfunded orders | Whole-share rounding; commission floor; min-size gate |
| `5-synthetic-is-test-only` | Synthetic numbers presented as performance | Keep the SYNTHETIC banner; never promote synthetic metrics |
| `6-local-currency` | Currencies mixed inside a sleeve | Convert only in the portfolio/FX layer |

## Rollout discipline

Features stage through exposure exactly like capital does:

`shadow → synthetic → single-sleeve → paper → canary-live → full-live`

Each stage has an **exit criterion** and the rollout carries **guardrail
metrics** that must not regress. If a guardrail breaches, the **rollback**
(named in the plan) is executed. Default-off is the rule; a default-on feature
must justify it (e.g. a data-integrity gate like F7, or a CI gate like F16).

## Cadence

* **Weekly grooming** — the four roles critique `proposed`/`researching` items;
  record verdicts; promote to `groomed`/`ready`.
* **Continuous intake** — anyone files a `proposed` item (problem + hint of
  evidence); it enters the next grooming.
* **Per-merge** — `validate.py` runs in CI (see `tests/test_product_backlog.py`),
  so a malformed or under-specified backlog item fails the build.
* **Post-rollout review** — measurement feeds evidence back; contradicted
  hypotheses re-open items.

## How to add or change an item

1. Edit [`backlog/backlog.json`](backlog/backlog.json) (next free id; never reuse
   or renumber an id).
2. Fill the four `role_reviews` and at least one evidence entry.
3. Write binary acceptance criteria (metric on the measurable ones) and a rollout
   plan.
4. Run `python product/validate.py` until it prints OK.
5. Open a PR; CI runs the validator via `tests/test_product_backlog.py`.
