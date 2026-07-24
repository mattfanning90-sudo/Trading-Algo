---
title: Paper and live execution parity
slug: paper-live-parity
status: draft
created: 2026-07-25
last-updated: 2026-07-25
owner: matt
---

# Paper and live execution parity

## Context
The equity, FX, and crypto algorithms share substantial research and target-weight
logic, but their post-decision paths do not yet model a complete real order
lifecycle. Paper state, IBKR orders, OANDA practice trading, and CCXT execution
need one durable contract for order intent, submission, acknowledgement, partial
fills, fills, rejection, cancellation, accounting, and reconciliation.

The required outcome is operational parity, not a claim that external venues are
available 100% of the time. When venue state is uncertain, correctness wins:
the affected instrument pauses while unrelated instruments continue.

## Goals
- **G-1**: Equity, FX, and crypto produce orders through one execution kernel
  whose behavior is identical in deterministic simulation, broker paper/practice,
  and broker live modes except for the configured venue endpoint.
- **G-2**: Every order intent and venue event is durable, replayable, idempotent,
  and recoverable after a process crash without duplicate orders; operators can
  query correlated execution events and structured operational errors.
- **G-3**: Internal cash, positions, lots, P&L, and pending orders reconcile
  against IBKR, OANDA, and CCXT; unexplained differences quarantine only the
  affected instrument.
- **G-4**: Paper/live parity covers IBKR equities and FX, OANDA FX, and CCXT
  crypto through a common adapter-conformance contract.
- **G-5**: Research throughput remains fast: vectorized backtests and parameter
  sweeps stay on their existing path, while parity validation, paper, and live
  use the event-driven execution kernel.
## Non-goals
- **NG-1**: No guarantee of uninterrupted broker, exchange, network, or market
  availability. The guarantee is safe, deterministic recovery.
- **NG-2**: No high-frequency or microsecond execution. The system remains
  suitable for monthly equity and minute-to-daily FX/crypto strategies.
- **NG-3**: No automatic correction trade for an unexplained reconciliation
  difference. The instrument remains quarantined until state is explained.
- **NG-4**: No new target-weight implementation. Existing shared strategy
  functions remain the only source of desired portfolio weights.
- **NG-5**: No automatic enablement of live capital. Existing promotion and
  explicit live-action gates remain mandatory.

## Acceptance criteria
| ID | Criterion (observable behaviour) | Verified by | Status |
|----|----------------------------------|-------------|--------|
| AC-1 | The same immutable market snapshot and reconciled starting state produce the same normalized order intents in simulator, paper/practice, and live modes | `tests/test_execution_parity.py::test_modes_produce_identical_normalized_intents` | ☐ |
| AC-2 | Replaying an accepted intent, including after a simulated crash between submission and acknowledgement, never submits a second venue order | `tests/test_execution_recovery.py::test_crash_replay_is_idempotent` | ☐ |
| AC-3 | The shared state machine handles submitted, acknowledged, partially-filled, filled, rejected, cancelled, and expired orders without an invalid transition | `tests/test_order_lifecycle.py` | ☐ |
| AC-4 | A partial fill updates reserved cash, positions, lots, fees, and realized/unrealized P&L from the fill event rather than the target order quantity | `tests/test_execution_accounting.py::test_partial_fill_drives_accounting` | ☐ |
| AC-5 | An unexplained position, cash, or open-order difference quarantines only affected instruments; an unrelated instrument can still generate and execute an intent | `tests/test_reconciliation.py::test_mismatch_quarantines_only_affected_instrument` | ☐ |
| AC-6 | Reconciliation clears a quarantine only after venue and internal state agree or an operator records an explicit resolution | `tests/test_reconciliation.py::test_quarantine_requires_verified_resolution` | ☐ |
| AC-7 | IBKR equity/FX, OANDA FX, and CCXT crypto adapters pass the same contract suite for snapshots, submission, cancellation, event normalization, and idempotency keys | `tests/adapters/test_adapter_contract.py` | ☐ |
| AC-8 | Paper/practice adapters use the same kernel and adapter code as live; configuration selects credentials/endpoints without a behavior branch in strategy, risk, accounting, or reconciliation | `tests/test_execution_parity.py::test_paper_and_live_share_execution_path` | ☐ |
| AC-9 | Vectorized backtests and sweeps do not instantiate the event execution kernel; a dedicated parity replay does | `tests/test_execution_efficiency.py` | ☐ |
| AC-10 | Live submission remains blocked by default and requires the existing promotion evidence plus an explicit live action | `tests/test_execution_live_gate.py` | ☐ |
| AC-11 | Restarting from the append-only ledger reconstructs the same orders, cash, positions, lots, P&L, and quarantines as the pre-restart projections | `tests/test_execution_recovery.py::test_replay_rebuilds_identical_projections` | ☐ |
| AC-12 | The complete regression suite, lint gate, deterministic parity scenarios, and broker-sandbox integration tests pass | verification plan below | ☐ |
| AC-13 | Every intent, venue event, state transition, reconciliation result, quarantine, and operator resolution is durably recorded with its account, venue, instrument, correlation ID, and event timestamp | `tests/test_execution_observability.py::test_execution_events_are_correlated_and_queryable` | ☐ |
| AC-14 | Adapter, reconciliation, persistence, and retry failures produce structured error records with severity, stable error category, retryability, correlation IDs, and sanitized diagnostic context | `tests/test_execution_observability.py::test_errors_are_structured_and_sanitized` | ☐ |
| AC-15 | API credentials, tokens, secrets, and raw authentication headers never appear in the event log, error log, exported diagnostics, or dashboard payloads | `tests/test_execution_observability.py::test_observability_redacts_secrets` | ☐ |

## Constraints & invariants
- **Invariant 1 — no lookahead**: every `OrderIntent` carries an immutable
  `as_of` snapshot identifier; decision inputs must be at or before that time.
- **Invariant 2 — costs always on**: commissions, spreads, stamp duty, venue
  fees, slippage, and funding/carry are normalized into fill/accounting events.
- **Invariant 3 — one weight function**: the execution kernel consumes targets;
  it never recomputes selection or sizing.
- **Invariant 4 — whole shares**: equity adapters normalize quantities to valid
  whole-share or venue lot increments before an intent becomes submittable.
- **Invariant 5 — synthetic honesty**: the deterministic simulator validates
  mechanics only and never supplies performance claims.
- **Invariant 6 — local-currency sleeves**: cash and fills remain in venue/local
  currency; AUD conversion occurs only in portfolio/reporting projections.
- Correctness takes priority over missed trades: ambiguous venue state pauses
  the affected instrument.
- One durable intent must map to at most one live venue order.
- The fast research backtester remains separate from the broker-realistic
  parity replay.
- Execution events are permanent accounting/audit records. Operational errors
  are diagnostic records and never directly change cash, positions, or P&L.

## Verification plan
```bash
pytest -q tests/test_execution_parity.py
pytest -q tests/test_order_lifecycle.py tests/test_execution_accounting.py
pytest -q tests/test_reconciliation.py tests/test_execution_recovery.py
pytest -q tests/test_execution_observability.py
pytest -q tests/adapters/test_adapter_contract.py
pytest -q tests/test_execution_efficiency.py tests/test_execution_live_gate.py
pytest -q
ruff check trading_algo tests
python -m trading_algo.execution_replay --scenario tests/fixtures/execution/golden.json
```

Broker-sandbox checks run only when their credentials are explicitly present:

```bash
pytest -q -m broker_sandbox tests/integration/test_ibkr_adapter.py
pytest -q -m broker_sandbox tests/integration/test_oanda_adapter.py
pytest -q -m broker_sandbox tests/integration/test_ccxt_adapter.py
```

## Open questions
None identified.

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-07-25 | All equity, FX, and crypto algorithms must reach paper/live parity in the same delivery | matt |
| 2026-07-25 | FX supports both IBKR and OANDA; crypto uses CCXT | matt |
| 2026-07-25 | Unexplained reconciliation differences quarantine only affected instruments | matt |
| 2026-07-25 | Correctness wins over trading on stale or uncertain state | matt |
| 2026-07-25 | Retain the fast research backtester and use one event kernel for parity, paper, and live | matt |
| 2026-07-25 | Add separate durable execution-event and structured operational-error logs | matt |
