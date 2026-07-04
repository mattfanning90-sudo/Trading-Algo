# The product team — four roles, four lenses

This project is run as a **standing product team of four roles**. They are not
job titles for hiring — they are the four *lenses* every backlog item is put
through before it can ship. Each role has explicit decision rights and a veto
scoped to what it is accountable for. Every item in
[`backlog/backlog.json`](backlog/backlog.json) carries a `role_reviews` block
with one verdict per role; an item cannot leave the **groomed** state until all
four have weighed in (enforced by [`validate.py`](validate.py)).

The lenses are deliberately adversarial. The Product Owner wants outcomes, the
Data Scientist wants evidence, the Chief Engineer wants the invariants held, and
the Algo Trader wants the book protected. A feature that survives all four is one
we can put real capital behind.

---

## Product Owner
**Owns:** what we build and in what order, and why it matters to the end user
(the person deciding whether to trust this book with money).

- Frames each item as a **problem + hypothesis**, not a solution.
- Sets priority via **RICE** and sequences dependencies.
- Defines the **user-facing** meaning of "done" (an outcome, not a task).
- **Decision right:** ranks the backlog; can park or reject an item.
- **Veto:** an item with no articulable user/business value.

Critique lens: *"Does this change a decision the user actually makes, and is it
the most valuable thing we could do next?"*

## Data Scientist / Quant Researcher
**Owns:** statistical validity and the evidence standard.

- Demands the **experiment** that would justify a feature and the **numeric
  acceptance thresholds** that prove it worked.
- Guards against the biases catalogued in
  [`docs/research/COMBATING_BACKTEST_BIAS.md`](../docs/research/COMBATING_BACKTEST_BIAS.md):
  survivorship, look-ahead, cost-omission, and **multiple-testing / overfitting**
  (Deflated Sharpe, PBO, purged walk-forward — already implemented for FX in
  `trading_algo/forex/validation.py` and reusable on the equity side).
- **Decision right:** sets the metric and threshold in each acceptance criterion.
- **Veto:** a claimed edge that isn't deflated for trials or confirmed
  out-of-sample.

Critique lens: *"What's the evidence, what would falsify it, and what number
tells us it worked?"*

## Chief Engineer
**Owns:** architecture, the CLAUDE.md invariants, and how a feature reaches
production without breaking anything.

- Maps each item to the **modules it plugs into** and the **invariants it
  risks**, and specifies the guardrail that protects them.
- Picks the **rollout mechanism** (config knob / feature flag / new module / CI
  gate) and the **test strategy**.
- Enforces invariant #3 (one weight function, `strategy.compute_targets`) — any
  item that touches sizing routes through it, never a second copy.
- **Decision right:** approves the rollout plan and effort estimate.
- **Veto:** a design that violates an invariant or lacks a rollback.

Critique lens: *"Where does this plug in, which invariant could it break, and how
do we turn it off if it goes wrong?"*

## Algo Trader (the desk)
**Owns:** the live book — P&L, real-world slippage, and the kill switches.

- Judges whether an item **moves the needle** on live returns/risk or is
  reporting theatre.
- Specifies the **live guardrail / kill-switch** behaviour and the operational
  acceptance required before anything touches real money.
- Champions the **paper → live promotion gate** and the pre-trade checks
  (min-account-size, liquidity/ADV, staleness).
- **Decision right:** the go-live call, gated on the promotion checklist.
- **Veto:** anything that would put capital at risk without a guardrail, or that
  sizes off survivorship-biased numbers.

Critique lens: *"Does this protect or grow the book, and what stops it hurting me
when it misbehaves live?"*

---

## How the lenses combine

| Stage | Who leads | Gate |
|---|---|---|
| Intake / research | Product Owner + Data Scientist | Problem is real, evidence attached |
| Grooming / critique | **All four** | Four `role_reviews` recorded |
| Ready | Chief Engineer | Rollout plan + acceptance criteria complete |
| Build | Chief Engineer | Invariants held, tests green |
| Rollout | Algo Trader | Guardrail metrics hold across stages |
| Done | Product Owner | User-facing outcome met |

A verdict of `no` from the role that **owns** the relevant risk (e.g. the Chief
Engineer on an invariant break, or the Algo Trader on an unguarded live path) is
a blocking veto, not just an opinion. Everything else is resolved by the Product
Owner's ranking.
