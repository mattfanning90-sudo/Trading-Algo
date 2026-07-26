# Paper/Live Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one durable, idempotent execution path that gives equity, FX, and crypto identical mechanics in deterministic simulation, broker paper/practice, and broker live modes.

**Architecture:** Keep the fast vectorized research backtests unchanged. Add a focused `trading_algo.execution` package containing venue-neutral domain types, an append-only event ledger, a pure lifecycle/accounting reducer, a deterministic simulator, reconciliation, and one execution kernel. IBKR, OANDA, and CCXT implement a shared adapter contract; existing paper/live entry points become thin callers of the kernel.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`/`decimal`/`enum`/`hashlib`/`json`/`sqlite3`, pandas only at existing strategy boundaries, `ib_insync` (optional), `oandapyV20` (optional), `ccxt` (optional), pytest, Ruff.

## Global Constraints

- Preserve all six invariants in `CLAUDE.md`: no lookahead, costs always on, one target-weight function, whole equity shares, synthetic results are mechanics-only, and sleeve-local currencies.
- The same `OrderIntent` must map to at most one venue order.
- Fills—not requested quantities—drive cash, positions, lots, fees, and P&L.
- Unknown venue state quarantines only affected instruments; unrelated instruments continue.
- Paper/live differences are limited to endpoint, credentials, account, and explicit mode configuration.
- Live submission remains disabled by default and requires the existing promotion gate plus an explicit live action.
- Execution events are permanent accounting/audit records. Operational errors are diagnostic records and cannot directly change accounting projections.
- Credentials, tokens, secrets, and raw authentication headers must be redacted before persistence, export, notification, or dashboard rendering.
- Do not instantiate the execution kernel from vectorized backtests or parameter sweeps.
- Every production change follows red-green-refactor and ends with the focused tests, Ruff, and an intentional commit.

## File Map

New execution package:

- `trading_algo/execution/models.py` — venue-neutral immutable domain records and enums.
- `trading_algo/execution/ids.py` — deterministic intent, correlation, and event identifiers.
- `trading_algo/execution/adapter.py` — the venue adapter protocol and capability record.
- `trading_algo/execution/ledger.py` — append-only intents/events plus rebuildable projections and quarantines.
- `trading_algo/execution/errors.py` — structured operational errors, redaction, storage, and querying.
- `trading_algo/execution/reducer.py` — pure order lifecycle and fill-accounting state transitions.
- `trading_algo/execution/planner.py` — target weights to venue-neutral order intents.
- `trading_algo/execution/simulator.py` — deterministic fill/reject/partial-fill adapter.
- `trading_algo/execution/reconcile.py` — venue/internal comparison and instrument quarantine decisions.
- `trading_algo/execution/kernel.py` — reconcile, plan, persist, submit/recover, and apply events.
- `trading_algo/execution/adapters/ibkr.py` — IBKR equities and FX.
- `trading_algo/execution/adapters/oanda.py` — OANDA FX.
- `trading_algo/execution/adapters/ccxt.py` — CCXT spot crypto.
- `trading_algo/execution/reporting.py` — read-only event/error/quarantine payload builders.
- `trading_algo/execution/replay.py` — deterministic replay and golden-scenario runner.
- `trading_algo/execution_replay.py` — CLI compatibility entry point required by the spec.

Existing integration points:

- `trading_algo/paper_trade.py`, `trading_algo/engine.py` — equity target generation and execution-mode selection.
- `trading_algo/execution_ibkr.py` — backward-compatible wrapper over the IBKR adapter/kernel.
- `trading_algo/forex/fx_book.py`, `trading_algo/forex/engine.py` — FX target generation and execution-mode selection.
- `trading_algo/forex/crypto_exec.py` — backward-compatible wrapper over the CCXT adapter/kernel.
- `trading_algo/config.py`, `trading_algo/forex/fx_config.py` — safe execution defaults and ledger locations.
- `trading_algo/dashboard/server.py`, `trading_algo/dashboard/static/app.js`, `trading_algo/dashboard/static/index.html`, `trading_algo/dashboard/static/styles.css` — operator event/error/quarantine view.

---

### Task 1: Venue-Neutral Domain Contract and Deterministic IDs

**Files:**
- Create: `trading_algo/execution/__init__.py`
- Create: `trading_algo/execution/models.py`
- Create: `trading_algo/execution/ids.py`
- Create: `trading_algo/execution/adapter.py`
- Test: `tests/test_execution_models.py`

**Interfaces:**
- Produces: `ExecutionMode`, `AssetClass`, `Side`, `OrderType`, `OrderStatus`, `EventType`.
- Produces: `Instrument`, `DecisionSnapshot`, `OrderIntent`, `ExecutionEvent`, `VenueOrder`, `VenueSnapshot`, `OrderProjection`, `PositionProjection`, `AccountProjection`.
- `Instrument` uses `settlement_currency` for all assets and optional
  `base_currency`/`quote_currency` only for FX and crypto pairs; mapping keys in
  snapshots and projections are canonical `Instrument.key` strings.
- Produces: `intent_id_for(...) -> str`, `idempotency_key_for(...) -> str`, `event_id_for(...) -> str`.
- Produces: `VenueCapabilities` and `VenueAdapter` protocol.

- [ ] **Step 1: Write failing deterministic-ID and serialization tests**

```python
# tests/test_execution_models.py
from datetime import datetime, timezone
from decimal import Decimal

from trading_algo.execution.ids import idempotency_key_for, intent_id_for
from trading_algo.execution.models import (
    AssetClass, DecisionSnapshot, ExecutionMode, Instrument,
    OrderIntent, OrderType, Side,
)


def _instrument() -> Instrument:
    return Instrument(
        venue="IBKR", symbol="AAPL", asset_class=AssetClass.EQUITY,
        settlement_currency="USD", base_currency=None, quote_currency=None,
        quantity_step=Decimal("1"), min_quantity=Decimal("1"),
        min_notional=Decimal("1"),
    )


def test_intent_identity_is_deterministic_and_snapshot_scoped():
    kwargs = dict(
        account="full", strategy="momentum", instrument=_instrument(),
        side=Side.BUY, quantity=Decimal("10"), order_type=OrderType.MARKET,
        snapshot_id="snap-1",
    )
    assert intent_id_for(**kwargs) == intent_id_for(**kwargs)
    assert intent_id_for(**kwargs) != intent_id_for(**{**kwargs, "snapshot_id": "snap-2"})
    assert idempotency_key_for(intent_id_for(**kwargs), "IBKR").startswith("IBKR-")


def test_order_intent_json_round_trip_preserves_decimal_and_time():
    as_of = datetime(2026, 7, 25, 6, 0, tzinfo=timezone.utc)
    snapshot = DecisionSnapshot(
        snapshot_id="snap-1", as_of=as_of,
        prices={"IBKR:AAPL": Decimal("210.25")},
        source_timestamps={"prices": as_of},
    )
    intent = OrderIntent.create(
        account="full", strategy="momentum", mode=ExecutionMode.PAPER,
        instrument=_instrument(), side=Side.BUY, quantity=Decimal("10"),
        order_type=OrderType.MARKET, decision_price=Decimal("210.25"),
        currency="USD", snapshot=snapshot,
    )
    assert OrderIntent.from_record(intent.to_record()) == intent
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest -q tests/test_execution_models.py`

Expected: FAIL because `trading_algo.execution` does not exist.

- [ ] **Step 3: Implement immutable records and canonical identifiers**

```python
# trading_algo/execution/ids.py
def _digest(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def intent_id_for(*, account, strategy, instrument, side, quantity,
                  order_type, snapshot_id) -> str:
    return "int-" + _digest({
        "account": account, "strategy": strategy,
        "instrument": instrument.key, "side": side.value,
        "quantity": str(quantity), "order_type": order_type.value,
        "snapshot_id": snapshot_id,
    })


def idempotency_key_for(intent_id: str, venue: str) -> str:
    return f"{venue.upper()}-{intent_id}"
```

```python
# trading_algo/execution/adapter.py
@dataclass(frozen=True)
class VenueCapabilities:
    client_order_id: bool
    streaming_events: bool
    fractional_quantities: bool
    supports_shorting: bool


class VenueAdapter(Protocol):
    name: str
    mode: ExecutionMode
    capabilities: VenueCapabilities

    def snapshot(self, account: str,
                 instruments: set[Instrument] | None = None) -> VenueSnapshot: ...
    def normalize(self, intent: OrderIntent) -> OrderIntent: ...
    def recover(self, account: str, idempotency_key: str) -> tuple[ExecutionEvent, ...]: ...
    def submit(self, intent: OrderIntent) -> ExecutionEvent: ...
    def cancel(self, intent: OrderIntent, venue_order_id: str) -> ExecutionEvent: ...
    def poll_events(self, account: str, since: datetime | None) -> tuple[ExecutionEvent, ...]: ...
```

Implement `to_record()`/`from_record()` using ISO-8601 UTC timestamps and
decimal strings; do not serialize Python enum reprs or floats.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `pytest -q tests/test_execution_models.py && ruff check trading_algo/execution tests/test_execution_models.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/execution tests/test_execution_models.py
git commit -m "feat(execution): define venue-neutral execution contract"
```

---

### Task 2: Durable Event Ledger, Projections, and Structured Error Log

**Files:**
- Create: `trading_algo/execution/ledger.py`
- Create: `trading_algo/execution/errors.py`
- Test: `tests/test_execution_ledger.py`
- Test: `tests/test_execution_observability.py`

**Interfaces:**
- Consumes: Task 1 records and their `to_record()`/`from_record()` methods.
- Produces: `ExecutionLedger(path: str)`.
- Produces: `persist_intent(intent) -> OrderIntent`, `append_event(event) -> bool`,
  `events(...) -> list[ExecutionEvent]`, and
  `load_projection(account) -> AccountProjection`.
- Produces: `set_quarantine(account, instrument, reason, correlation_id)`, `clear_quarantine(...)`, `quarantines(account)`.
- Produces: `OperationalError`, `OperationalErrorLog(path)`, `sanitize(value)`.

- [ ] **Step 1: Write failing ledger/idempotency/error-redaction tests**

```python
# tests/test_execution_ledger.py
def test_duplicate_intent_and_event_are_idempotent(tmp_path, sample_intent, fill_event):
    ledger = ExecutionLedger(str(tmp_path / "execution.db"))
    assert ledger.persist_intent(sample_intent) == sample_intent
    assert ledger.persist_intent(sample_intent) == sample_intent
    assert ledger.append_event(fill_event) is True
    assert ledger.append_event(fill_event) is False
    assert len(ledger.events(intent_id=sample_intent.intent_id)) == 1
```

```python
# tests/test_execution_observability.py
def test_errors_are_structured_and_sanitized(tmp_path):
    log = OperationalErrorLog(str(tmp_path / "operations.db"))
    record = OperationalError.create(
        severity="ERROR", component="ccxt", operation="submit",
        category="VENUE_TIMEOUT", retryable=True,
        correlation_id="corr-1", account="crypto",
        context={
            "apiKey": "top-secret",  # pragma: allowlist secret
            "headers": {"Authorization": "Bearer secret-token"},  # pragma: allowlist secret
            "symbol": "BTC/USDT",
        },
    )
    log.append(record)
    got = log.query(correlation_id="corr-1")[0]
    text = json.dumps(got.to_record())
    assert got.category == "VENUE_TIMEOUT"
    assert "top-secret" not in text
    assert "secret-token" not in text
    assert "[REDACTED]" in text
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q tests/test_execution_ledger.py tests/test_execution_observability.py`

Expected: FAIL because ledger and error-log classes do not exist.

- [ ] **Step 3: Implement SQLite schemas and redaction**

Create `intents`, `events`, `account_projections`, and `quarantines` tables in
`execution.db`. Enforce `UNIQUE(intent_id)`, `UNIQUE(idempotency_key)`, and
`UNIQUE(event_id)`; create indexes on `(account, occurred_at)`,
`(instrument_key, occurred_at)`, and `correlation_id`.

Create a separate `errors` table in `operations.db`, indexed by time, severity,
account, venue, instrument, and correlation ID.

```python
_SECRET_KEYS = {
    "apikey", "api_key", "secret", "password", "token",
    "authorization", "cookie", "set-cookie",
}


def sanitize(value):
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if str(k).lower() in _SECRET_KEYS else sanitize(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value
```

Use WAL, foreign keys, busy timeout, explicit transactions, and JSON payload
schema versions. `append_event` returns `False` only for the same `event_id`;
other integrity errors must raise.

- [ ] **Step 4: Test filtering and permanent event retention**

Add tests that query events by account/venue/instrument/correlation ID, query
errors by severity/time, and prove deleting old operational errors does not
delete execution events. Name the acceptance tests exactly:

- `test_execution_events_are_correlated_and_queryable`;
- `test_errors_are_structured_and_sanitized`;
- `test_observability_redacts_secrets`.

Run: `pytest -q tests/test_execution_ledger.py tests/test_execution_observability.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/execution/ledger.py trading_algo/execution/errors.py \
  tests/test_execution_ledger.py tests/test_execution_observability.py
git commit -m "feat(execution): add durable event and error logs"
```

---

### Task 3: Pure Order Lifecycle and Fill Accounting Reducer

**Files:**
- Create: `trading_algo/execution/reducer.py`
- Test: `tests/test_order_lifecycle.py`
- Test: `tests/test_execution_accounting.py`

**Interfaces:**
- Consumes: `OrderIntent`, `ExecutionEvent`, `AccountProjection`.
- Produces: `apply_event(projection: AccountProjection, intent: OrderIntent, event: ExecutionEvent) -> AccountProjection`.
- Produces: `InvalidTransition(RuntimeError)`.
- Produces: valid transition table for every `EventType`.

- [ ] **Step 1: Write the failing lifecycle matrix**

```python
# tests/test_order_lifecycle.py
@pytest.mark.parametrize(
    ("start", "event_type", "expected"),
    [
        (OrderStatus.PLANNED, EventType.SUBMITTED, OrderStatus.SUBMITTED),
        (OrderStatus.SUBMITTED, EventType.ACKNOWLEDGED, OrderStatus.ACKNOWLEDGED),
        (OrderStatus.ACKNOWLEDGED, EventType.PARTIAL_FILL, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.PARTIALLY_FILLED, EventType.PARTIAL_FILL, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.PARTIALLY_FILLED, EventType.FILL, OrderStatus.FILLED),
        (OrderStatus.ACKNOWLEDGED, EventType.REJECTED, OrderStatus.REJECTED),
        (OrderStatus.ACKNOWLEDGED, EventType.CANCELLED, OrderStatus.CANCELLED),
        (OrderStatus.ACKNOWLEDGED, EventType.EXPIRED, OrderStatus.EXPIRED),
    ],
)
def test_valid_order_transitions(start, event_type, expected, projection, intent):
    projection.orders[intent.intent_id] = OrderProjection.for_intent(intent, status=start)
    event = ExecutionEvent.create(intent, event_type=event_type)
    assert apply_event(projection, intent, event).orders[intent.intent_id].status == expected


def test_terminal_order_rejects_new_fill(projection, intent):
    projection.orders[intent.intent_id] = OrderProjection.for_intent(
        intent, status=OrderStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        apply_event(projection, intent, ExecutionEvent.fill(
            intent, quantity=Decimal("1"), price=Decimal("100")))
```

- [ ] **Step 2: Write failing partial-fill accounting assertions**

```python
# tests/test_execution_accounting.py
def test_partial_fill_drives_accounting(projection, buy_intent):
    projection.balances["USD"] = Decimal("10000")
    submitted = ExecutionEvent.create(buy_intent, EventType.SUBMITTED)
    partial = ExecutionEvent.fill(
        buy_intent, event_type=EventType.PARTIAL_FILL,
        quantity=Decimal("4"), price=Decimal("101"),
        fee=Decimal("1"), fee_currency="USD",
    )
    state = apply_event(apply_event(projection, buy_intent, submitted),
                        buy_intent, partial)
    assert state.positions[buy_intent.instrument.key].quantity == Decimal("4")
    assert state.balances["USD"] == Decimal("9595")
    assert state.orders[buy_intent.intent_id].filled_quantity == Decimal("4")
    assert state.orders[buy_intent.intent_id].remaining_quantity == Decimal("6")
```

- [ ] **Step 3: Run tests and verify RED**

Run: `pytest -q tests/test_order_lifecycle.py tests/test_execution_accounting.py`

Expected: FAIL because reducer is absent.

- [ ] **Step 4: Implement the reducer**

Use a transition table keyed by `(OrderStatus, EventType)`. Reserve expected
notional on submission; consume reservation from actual fill price/quantity;
release the remainder on rejection/cancellation/expiry. Update weighted average
cost for buys and FIFO lots/realized P&L for reductions. Reject overfills,
negative long-only balances, duplicate fill IDs, wrong currencies, and invalid
transitions.

```python
def apply_event(projection, intent, event):
    state = copy.deepcopy(projection)
    order = state.orders.get(intent.intent_id) or OrderProjection.for_intent(intent)
    next_status = _next_status(order.status, event.event_type)
    if event.event_type in (EventType.PARTIAL_FILL, EventType.FILL):
        _apply_fill(state, order, intent, event)
    elif event.event_type in TERMINAL_WITHOUT_FILL:
        _release_reservation(state, order)
    order.status = next_status
    order.updated_at = event.occurred_at
    state.orders[intent.intent_id] = order
    return state
```

- [ ] **Step 5: Verify tests and commit**

Run: `pytest -q tests/test_order_lifecycle.py tests/test_execution_accounting.py`

Expected: PASS.

```bash
git add trading_algo/execution/reducer.py tests/test_order_lifecycle.py \
  tests/test_execution_accounting.py
git commit -m "feat(execution): add lifecycle and fill accounting reducer"
```

---

### Task 4: Transactional Projection Updates and Crash Replay

**Files:**
- Modify: `trading_algo/execution/ledger.py`
- Create: `trading_algo/execution/replay.py`
- Create: `trading_algo/execution_replay.py`
- Test: `tests/test_execution_recovery.py`

**Interfaces:**
- Consumes: `reducer.apply_event`.
- Produces: `ExecutionLedger.apply_event(intent, event) -> AccountProjection`.
- Produces: `ExecutionLedger.rebuild(account) -> AccountProjection`.
- Produces: `replay_scenario(path: str) -> AccountProjection`.

- [ ] **Step 1: Write failing atomicity and replay tests**

```python
def test_event_and_projection_commit_atomically(tmp_path, intent, fill_event, monkeypatch):
    ledger = ExecutionLedger(str(tmp_path / "execution.db"))
    ledger.persist_intent(intent)
    monkeypatch.setattr(ledger, "_write_projection",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("disk")))
    with pytest.raises(RuntimeError, match="disk"):
        ledger.apply_event(intent, fill_event)
    assert ledger.events(intent_id=intent.intent_id) == []


def test_replay_rebuilds_identical_projections(tmp_path, filled_scenario):
    ledger = ExecutionLedger(str(tmp_path / "execution.db"))
    for intent, events in filled_scenario:
        ledger.persist_intent(intent)
        for event in events:
            before_restart = ledger.apply_event(intent, event)
    assert ledger.rebuild(intent.account) == before_restart
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_execution_recovery.py`

Expected: FAIL because transactional apply/rebuild do not exist.

- [ ] **Step 3: Implement transactionally coupled event/projection writes**

Within one `BEGIN IMMEDIATE` transaction: insert the event, load the account
projection, apply the pure reducer, persist the projection, then commit. On any
exception, roll back both event and projection.

`rebuild(account)` starts from `AccountProjection.empty(account)`, orders events
by `(occurred_at, persisted_at, event_id)`, applies them, and verifies the result
against the stored projection.

The CLI accepts exactly:

```bash
python -m trading_algo.execution_replay --scenario PATH [--db PATH]
```

and prints normalized JSON with balances, positions, orders, and quarantines.

- [ ] **Step 4: Verify crash replay and commit**

Run: `pytest -q tests/test_execution_recovery.py`

Expected: PASS.

```bash
git add trading_algo/execution/ledger.py trading_algo/execution/replay.py \
  trading_algo/execution_replay.py tests/test_execution_recovery.py
git commit -m "feat(execution): make event projections crash replayable"
```

---

### Task 5: Target Planner, Venue Normalization, and Real-World Quantity Rules

**Files:**
- Create: `trading_algo/execution/planner.py`
- Test: `tests/test_execution_planner.py`
- Test: `tests/test_execution_parity.py`

**Interfaces:**
- Consumes: `DecisionSnapshot`, `VenueSnapshot`, `Instrument`, `ExecutionMode`.
- Produces: `PlanningRules(long_only, max_order_notional, cash_buffer, frozen_instruments)`.
- Produces: `plan_intents(account, strategy, mode, targets, decision, venue, rules) -> tuple[OrderIntent, ...]`.

- [ ] **Step 1: Write failing planner tests**

```python
def test_equity_quantities_are_whole_and_sells_never_exceed_holdings():
    intents = plan_intents(
        account="full", strategy="momentum", mode=ExecutionMode.PAPER,
        targets={AAPL: Decimal("0")},
        decision=snapshot(prices={AAPL.key: Decimal("210")}),
        venue=venue_snapshot(
            equity=Decimal("10000"), positions={AAPL.key: Decimal("7")}),
        rules=PlanningRules(long_only=True),
    )
    assert intents[0].side is Side.SELL
    assert intents[0].quantity == Decimal("7")
    assert intents[0].quantity % 1 == 0


def test_frozen_instrument_is_omitted_but_unrelated_instrument_continues():
    intents = plan_intents(
        account="full", strategy="momentum", mode=ExecutionMode.PAPER,
        targets={AAPL: Decimal("0.5"), MSFT: Decimal("0.5")},
        decision=snapshot(prices={AAPL.key: Decimal("200"), MSFT.key: Decimal("400")}),
        venue=venue_snapshot(equity=Decimal("10000")),
        rules=PlanningRules(frozen_instruments=frozenset({AAPL.key})),
    )
    assert [i.instrument for i in intents] == [MSFT]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_execution_planner.py tests/test_execution_parity.py`

Expected: FAIL because planner is absent.

- [ ] **Step 3: Implement deterministic planning**

For each instrument in the union of targets and current positions:

1. Skip frozen/quarantined instruments.
2. Convert target weight to target notional using venue equity in the
   instrument's quote currency.
3. Compute delta against reconciled current quantity valued at decision price.
4. Clamp long-only targets and sells to available holdings.
5. Quantize down to `quantity_step`.
6. Enforce minimum quantity/notional, cash buffer, and max order notional.
7. Sort by `instrument.key` before creating deterministic IDs.

Do not import pandas or call strategy functions.

- [ ] **Step 4: Add normalized-mode parity test**

Build identical decision/venue fixtures for simulator, paper, and live modes.
Compare `OrderIntent.normalized_record()` after excluding only `mode`.

Run: `pytest -q tests/test_execution_planner.py tests/test_execution_parity.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/execution/planner.py tests/test_execution_planner.py \
  tests/test_execution_parity.py
git commit -m "feat(execution): plan normalized venue-ready intents"
```

---

### Task 6: Deterministic Simulator and Adapter Contract Suite

**Files:**
- Create: `trading_algo/execution/simulator.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/contract.py`
- Create: `tests/adapters/test_adapter_contract.py`
- Create: `tests/adapters/test_simulator_adapter.py`
- Test: `tests/test_execution_parity.py`

**Interfaces:**
- Consumes: `VenueAdapter` protocol and domain records.
- Produces: `FillInstruction`, `SimulationScenario`, `SimulatorAdapter`.
- Produces: reusable `assert_adapter_contract(adapter_factory, fixtures)` test
  helper and the shared acceptance suite in
  `tests/adapters/test_adapter_contract.py`.

- [ ] **Step 1: Write failing simulator lifecycle tests**

```python
def test_simulator_emits_scripted_partial_then_final_fill(intent):
    adapter = SimulatorAdapter(
        account="full",
        snapshot=venue_snapshot(equity=Decimal("10000")),
        scenario=SimulationScenario({
            intent.intent_id: (
                FillInstruction.partial(Decimal("4"), Decimal("101"), fee=Decimal("1")),
                FillInstruction.fill(Decimal("6"), Decimal("102"), fee=Decimal("1")),
            )
        }),
    )
    submitted = adapter.submit(intent)
    events = adapter.poll_events(intent.account, since=None)
    assert submitted.event_type is EventType.SUBMITTED
    assert [e.event_type for e in events] == [EventType.PARTIAL_FILL, EventType.FILL]
    assert sum(e.quantity for e in events) == intent.quantity
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/adapters/test_simulator_adapter.py`

Expected: FAIL because simulator is absent.

- [ ] **Step 3: Implement deterministic simulator**

The simulator must never use random values unless a scenario supplies an
explicit seed. Default behavior is immediate full fill at decision price plus
the injected cost model. Scripted scenarios support delayed acknowledgement,
partial fill, rejection, cancellation, expiry, disconnect before response, and
crash after venue acceptance.

`recover(idempotency_key)` returns the existing submitted/ack/fill chain and
never creates an event.

- [ ] **Step 4: Build and run the adapter contract suite**

The reusable contract asserts snapshot shape, normalization idempotence,
submission idempotency, recovery, cancellation, event uniqueness, and secret
redaction. Run it against `SimulatorAdapter`.

Run: `pytest -q tests/adapters/test_adapter_contract.py tests/adapters/test_simulator_adapter.py tests/test_execution_parity.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/execution/simulator.py tests/adapters tests/test_execution_parity.py
git commit -m "feat(execution): add deterministic broker simulator"
```

---

### Task 7: Reconciliation, Instrument Quarantine, and the Execution Kernel

**Files:**
- Create: `trading_algo/execution/reconcile.py`
- Create: `trading_algo/execution/kernel.py`
- Modify: `trading_algo/execution/ledger.py`
- Test: `tests/test_reconciliation.py`
- Test: `tests/test_execution_kernel.py`
- Modify: `tests/test_execution_recovery.py`

**Interfaces:**
- Produces: `DifferenceKind`, `ReconciliationDifference`, `ReconciliationReport`.
- Produces: `reconcile(internal, venue, pending_orders) -> ReconciliationReport`.
- Produces: `ExecutionResult(intents, events, quarantined, projection)`.
- Produces: `VenueResponseUnknown(RuntimeError)` for ambiguous venue acceptance.
- Produces: `ExecutionKernel(ledger, error_log, adapter, live_authorizer)`.
- Produces: `ExecutionKernel.execute(account, strategy, targets, decision, rules) -> ExecutionResult`.
- `live_authorizer(account, adapter, intents) -> None` raises when live is not approved.

- [ ] **Step 1: Write failing instrument-scoped reconciliation tests**

```python
def test_mismatch_quarantines_only_affected_instrument(ledger, internal, venue):
    internal.positions[AAPL.key] = PositionProjection(quantity=Decimal("10"))
    venue.positions[AAPL.key] = Decimal("9")
    report = reconcile(internal, venue, pending_orders=())
    assert report.unexplained_instruments == frozenset({AAPL.key})

    kernel = kernel_with_simulator(ledger, venue)
    result = kernel.execute(
        account="full", strategy="momentum",
        targets={AAPL: Decimal("0.5"), MSFT: Decimal("0.5")},
        decision=snapshot_for(AAPL, MSFT), rules=PlanningRules(),
    )
    assert result.quarantined == (AAPL.key,)
    assert [i.instrument.key for i in result.intents] == [MSFT.key]
```

- [ ] **Step 2: Write failing timeout/idempotent-recovery test**

```python
def test_crash_replay_is_idempotent(kernel, accepting_then_timing_out_adapter, target):
    with pytest.raises(VenueResponseUnknown):
        kernel.execute(**target)
    result = kernel.execute(**target)
    assert accepting_then_timing_out_adapter.submit_calls == 1
    assert result.events[-1].event_type is EventType.FILL
```

- [ ] **Step 3: Run tests and verify RED**

Run: `pytest -q tests/test_reconciliation.py tests/test_execution_kernel.py tests/test_execution_recovery.py`

Expected: FAIL because reconciliation/kernel do not exist.

- [ ] **Step 4: Implement reconciliation classifications**

Classify differences as:

- `MATCH`;
- `EXPECTED_PENDING` when known open order quantities explain the delta;
- `RECOVERABLE_EVENT_GAP` when venue fills exist but local events do not;
- `UNEXPLAINED` otherwise.

Use instrument quantity steps and currency minor units as tolerances; do not use
one global float epsilon. Persist quarantine events with correlation and
causation IDs.

- [ ] **Step 5: Implement kernel ordering and failure policy**

Kernel order:

1. Fetch venue snapshot.
2. Load/rebuild internal projection.
3. Reconcile and persist quarantine/repair events.
4. Plan non-quarantined intents.
5. Call live authorization before any live venue side effect.
6. Persist each intent.
7. Recover by idempotency key before submit.
8. Submit only when recovery proves no venue order exists.
9. Append normalized events and apply projections.
10. Poll terminal events; reconcile touched instruments.
11. Log structured failures and quarantine only the affected instrument when
    venue acceptance is ambiguous.

- [ ] **Step 6: Verify and commit**

Run: `pytest -q tests/test_reconciliation.py tests/test_execution_kernel.py tests/test_execution_recovery.py`

Expected: PASS.

```bash
git add trading_algo/execution/reconcile.py trading_algo/execution/kernel.py \
  trading_algo/execution/ledger.py tests/test_reconciliation.py \
  tests/test_execution_kernel.py tests/test_execution_recovery.py
git commit -m "feat(execution): reconcile and execute intents idempotently"
```

---

### Task 8: IBKR Adapter for Equities and FX

**Files:**
- Create: `trading_algo/execution/adapters/__init__.py`
- Create: `trading_algo/execution/adapters/ibkr.py`
- Modify: `trading_algo/execution_ibkr.py`
- Modify: `tests/test_execution_ibkr.py`
- Create: `tests/adapters/test_ibkr_adapter.py`
- Modify: `tests/adapters/test_adapter_contract.py`
- Create: `tests/integration/test_ibkr_adapter.py`

**Interfaces:**
- Consumes: `VenueAdapter`, `OrderIntent`, `ExecutionEvent`, `VenueSnapshot`.
- Produces: `IBKRConfig(host, port, client_id, account, mode)`.
- Produces: `IBKRAdapter(config, ib_factory=None)`.
- Keeps: `execution_ibkr.rebalance(...)` as a compatibility wrapper returning normalized order dictionaries.

- [ ] **Step 1: Write failing adapter-contract tests using the existing fake IB**

```python
def test_ibkr_adapter_contract(fake_ib_factory, adapter_contract):
    adapter = IBKRAdapter(
        IBKRConfig("127.0.0.1", 7497, 17, "DU123", ExecutionMode.PAPER),
        ib_factory=fake_ib_factory,
    )
    adapter_contract(adapter)


def test_ibkr_uses_order_ref_for_recovery(fake_ib_factory, intent):
    adapter = ibkr_adapter(fake_ib_factory)
    first = adapter.submit(intent)
    recovered = adapter.recover(intent.account, intent.idempotency_key)
    assert fake_ib_factory.instance.place_order_calls == 1
    assert recovered[0].venue_order_id == first.venue_order_id
    assert fake_ib_factory.instance.last_order.orderRef == intent.idempotency_key
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/adapters/test_adapter_contract.py tests/adapters/test_ibkr_adapter.py tests/test_execution_ibkr.py`

Expected: FAIL because `IBKRAdapter` is absent.

- [ ] **Step 3: Implement snapshots, contracts, events, and recovery**

Use injected `IB` clients. Batch account summary, positions, open trades, and
fills once per snapshot. Normalize:

- equities with `Stock(symbol, exchange, currency)`;
- FX with `Forex(pair)`/IDEALPRO cash contracts;
- `orderRef = intent.idempotency_key`;
- IB status/fill callbacks into shared event types;
- commissions from execution commission reports;
- quantity and currency from qualified contracts.

Search open trades, completed orders, and recent fills by `orderRef` before
submitting. A timeout with no reliable recovery result raises
`VenueResponseUnknown` and never resubmits in that cycle.

- [ ] **Step 4: Convert the legacy wrapper**

`execution_ibkr.rebalance` builds a decision/venue snapshot, plans intents, and
executes through `ExecutionKernel`. Preserve its current arguments and dry-run
default; translate `ExecutionResult` to the legacy list-of-dicts response.

- [ ] **Step 5: Add opt-in paper integration test**

Mark with `@pytest.mark.broker_sandbox`; skip unless
`IBKR_PAPER_INTEGRATION=1`. Use port 7497 only, submit a deliberately tiny
marketable order in an explicitly configured test instrument, cancel if still
open, and reconcile to zero unexplained differences.

- [ ] **Step 6: Verify and commit**

Run: `pytest -q tests/adapters/test_ibkr_adapter.py tests/test_execution_ibkr.py`

Expected: PASS.

```bash
git add trading_algo/execution/adapters trading_algo/execution_ibkr.py \
  tests/adapters/test_adapter_contract.py tests/adapters/test_ibkr_adapter.py \
  tests/integration/test_ibkr_adapter.py tests/test_execution_ibkr.py
git commit -m "feat(execution): route IBKR equities and FX through parity adapter"
```

---

### Task 9: OANDA Practice/Live FX Adapter

**Files:**
- Create: `trading_algo/execution/adapters/oanda.py`
- Modify: `trading_algo/forex/oanda_data.py`
- Create: `tests/adapters/test_oanda_adapter.py`
- Modify: `tests/adapters/test_adapter_contract.py`
- Create: `tests/integration/test_oanda_adapter.py`

**Interfaces:**
- Produces: `OANDAConfig(token, account_id, environment, mode)`.
- Produces: `OANDAAdapter(config, api_factory=None)`.
- Reuses: `forex.oanda_data.instrument(symbol)`.

- [ ] **Step 1: Write failing OANDA adapter contract tests**

```python
def test_oanda_adapter_contract(fake_oanda_factory, adapter_contract):
    adapter = OANDAAdapter(
        OANDAConfig("token", "001-001", "practice", ExecutionMode.PAPER),
        api_factory=fake_oanda_factory,
    )
    adapter_contract(adapter)


def test_oanda_client_extension_is_idempotency_key(fake_oanda_factory, intent):
    adapter = oanda_adapter(fake_oanda_factory)
    adapter.submit(intent)
    body = fake_oanda_factory.api.last_request.data
    assert body["order"]["clientExtensions"]["id"] == intent.idempotency_key
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/adapters/test_adapter_contract.py tests/adapters/test_oanda_adapter.py`

Expected: FAIL because OANDA execution adapter is absent.

- [ ] **Step 3: Implement OANDA snapshot and lifecycle normalization**

Use OANDA account summary, open positions, pending orders, order create/cancel,
and transactions-since-ID endpoints. Persist the last transaction ID in adapter
cursor metadata. Map:

- order-create transaction → acknowledged;
- order-fill transaction → partial/final fill using units and remaining units;
- cancel/reject transactions → cancelled/rejected;
- financing and commission fields → fee/carry events.

Use `clientExtensions.id` for the idempotency key. Reject live environment when
`mode != LIVE`, and reject `mode == LIVE` unless the kernel live authorizer has
already passed.

- [ ] **Step 4: Add opt-in practice integration**

Skip unless `OANDA_PRACTICE_INTEGRATION=1`, `OANDA_API_TOKEN`, and
`OANDA_ACCOUNT_ID` exist. Use environment `practice`, a configured tiny unit
count, then reconcile the resulting transaction and position.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q tests/adapters/test_oanda_adapter.py tests/test_fx.py`

Expected: PASS.

```bash
git add trading_algo/execution/adapters/oanda.py trading_algo/forex/oanda_data.py \
  tests/adapters/test_adapter_contract.py tests/adapters/test_oanda_adapter.py \
  tests/integration/test_oanda_adapter.py
git commit -m "feat(execution): add OANDA practice and live parity adapter"
```

---

### Task 10: CCXT Spot-Crypto Adapter

**Files:**
- Create: `trading_algo/execution/adapters/ccxt.py`
- Modify: `trading_algo/forex/crypto_exec.py`
- Modify: `tests/test_crypto_exec.py`
- Create: `tests/adapters/test_ccxt_adapter.py`
- Modify: `tests/adapters/test_adapter_contract.py`
- Create: `tests/integration/test_ccxt_adapter.py`

**Interfaces:**
- Produces: `CCXTConfig(exchange, api_key, secret, password, sandbox, mode)`.
- Produces: `CCXTAdapter(config, exchange_factory=None)`.
- Keeps: `crypto_exec.plan_orders` and `crypto_exec.rebalance` as compatibility wrappers over planner/kernel.

- [ ] **Step 1: Write failing adapter and unknown-submit tests**

```python
def test_ccxt_adapter_contract(fake_exchange_factory, adapter_contract):
    adapter = CCXTAdapter(ccxt_test_config(), exchange_factory=fake_exchange_factory)
    adapter_contract(adapter)


def test_ccxt_timeout_without_client_order_lookup_quarantines_instrument(
        kernel, exchange_without_client_id_recovery, crypto_target):
    with pytest.raises(VenueResponseUnknown):
        kernel.execute(**crypto_target)
    assert kernel.ledger.is_quarantined("crypto", "BINANCE:BTC/USDT")
    assert exchange_without_client_id_recovery.create_order_calls == 1
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/adapters/test_adapter_contract.py tests/adapters/test_ccxt_adapter.py tests/test_crypto_exec.py`

Expected: FAIL because `CCXTAdapter` is absent.

- [ ] **Step 3: Implement exchange capability handling**

Load markets once and cache precision/minimums. Use `clientOrderId` only when the
exchange supports it; recover through open/closed orders and trades. If the
exchange cannot reliably query a timed-out submission by client ID, raise
`VenueResponseUnknown`, quarantine the instrument, and require reconciliation.
Never blindly submit a second order.

Map balances, spot holdings, open orders, trades, partial fills, fees, and
cancel/reject statuses into shared records. Keep spot long-only; planner and
simulator must enforce the same constraint. Live short/perpetual execution
remains rejected.

- [ ] **Step 4: Replace duplicate crypto planning/execution**

Keep public compatibility functions, but delegate their behavior to
`plan_intents`, `CCXTAdapter`, and `ExecutionKernel`. Ensure synthetic/dry-run
uses `SimulatorAdapter` and never constructs authenticated CCXT.

- [ ] **Step 5: Add opt-in sandbox integration**

Skip unless `CCXT_SANDBOX_INTEGRATION=1` and exchange-specific sandbox
credentials exist. Enable sandbox mode before loading markets, submit below the
configured canary cap, poll/cancel, and reconcile.

- [ ] **Step 6: Verify and commit**

Run: `pytest -q tests/adapters/test_ccxt_adapter.py tests/test_crypto_exec.py`

Expected: PASS.

```bash
git add trading_algo/execution/adapters/ccxt.py trading_algo/forex/crypto_exec.py \
  tests/adapters/test_adapter_contract.py tests/adapters/test_ccxt_adapter.py \
  tests/integration/test_ccxt_adapter.py tests/test_crypto_exec.py
git commit -m "feat(execution): route crypto through CCXT parity adapter"
```

---

### Task 11: Equity Paper/Live Migration to the Shared Kernel

**Files:**
- Modify: `trading_algo/config.py`
- Modify: `trading_algo/paper_trade.py`
- Modify: `trading_algo/engine.py`
- Modify: `trading_algo/state_schema.py`
- Create: `tests/test_equity_execution_parity.py`
- Modify: `tests/test_paper_trade.py`
- Modify: `tests/test_consistency.py`

**Interfaces:**
- Produces: `paper_trade.build_execution_request(account, region, targets, prices, as_of, mode)`.
- Extends: `paper_trade.run_daily(account, synthetic, execution_mode="simulator", venue="ibkr")`.
- Extends CLI: `engine --execution-mode simulator|paper|live --venue ibkr`.
- Default remains `simulator`; `live` requires explicit flag and promotion gate.

- [ ] **Step 1: Write failing golden equity parity test**

```python
def test_equity_paper_and_live_build_identical_intents(
        isolated_state, synth_asx, fake_ibkr_snapshot):
    targets = strategy.compute_targets(
        synth_asx[0], synth_asx[1], get_region("ASX").params)
    sim = paper_trade.build_execution_request(
        "full", get_region("ASX"), targets, synth_asx[0],
        synth_asx[0].index[-1], ExecutionMode.SIMULATOR)
    live = paper_trade.build_execution_request(
        "full", get_region("ASX"), targets, synth_asx[0],
        synth_asx[0].index[-1], ExecutionMode.LIVE)
    assert normalized_intents(sim) == normalized_intents(live)
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_equity_execution_parity.py tests/test_paper_trade.py tests/test_consistency.py`

Expected: FAIL because the equity engine bypasses the execution kernel.

- [ ] **Step 3: Add equity simulator fill model**

Translate existing equity paper assumptions into deterministic simulator events:
whole shares, region commission floors, slippage in fill price, UK stamp duty
on buys, frozen data-quality names, and local-currency cash. Use existing
`fees.py` functions; delete no cost logic until parity tests prove identical
state and P&L.

- [ ] **Step 4: Route `run_daily` through the kernel**

Retain `strategy.compute_targets` unchanged. Replace direct mutation in
`rebalance_sleeve` with:

1. execution request construction;
2. simulator/IBKR adapter selection;
3. kernel execution;
4. read-optimized paper-state snapshot updated from ledger projection.

Add a one-time migration event for legacy positions/cash. Re-running migration
must be idempotent.

- [ ] **Step 5: Wire safe CLI modes**

`--execution-mode live` must also require `--live`, pass promotion evidence,
and refuse synthetic data. `paper` selects IBKR paper port. No argument keeps
today's simulator behavior.

- [ ] **Step 6: Verify and commit**

Run: `pytest -q tests/test_equity_execution_parity.py tests/test_paper_trade.py tests/test_consistency.py tests/test_execution_ibkr.py`

Expected: PASS with existing target and paper-accounting invariants unchanged.

```bash
git add trading_algo/config.py trading_algo/paper_trade.py trading_algo/engine.py \
  trading_algo/state_schema.py tests/test_equity_execution_parity.py \
  tests/test_paper_trade.py tests/test_consistency.py
git commit -m "feat(equity): unify paper and live execution flow"
```

---

### Task 12: FX and Crypto Paper/Live Migration

**Files:**
- Modify: `trading_algo/forex/fx_config.py`
- Modify: `trading_algo/forex/fx_book.py`
- Modify: `trading_algo/forex/engine.py`
- Modify: `trading_algo/forex/crypto_exec.py`
- Create: `tests/test_fx_execution_parity.py`
- Modify: `tests/test_fx_book.py`
- Modify: `tests/test_fx_consistency.py`
- Modify: `tests/test_crypto_exec.py`

**Interfaces:**
- Produces: `fx_book.build_execution_request(account, targets, panel, state, mode, venue)`.
- Extends: `fx_book.run_once(..., execution_mode="simulator", execution_venue=None)`.
- Extends CLI: `forex.engine --execution-mode simulator|paper|live --execution-venue ibkr|oanda|ccxt`.

- [ ] **Step 1: Write failing cross-venue FX parity test**

```python
@pytest.mark.parametrize("venue", ["ibkr", "oanda"])
def test_fx_simulator_paper_live_intents_match(venue, fx_panel, fx_state):
    targets = fx_strategy.compute_targets(fx_panel, profile("balanced"))
    requests = [
        fx_book.build_execution_request(
            "matt", targets, fx_panel, fx_state, mode, venue)
        for mode in (ExecutionMode.SIMULATOR, ExecutionMode.PAPER, ExecutionMode.LIVE)
    ]
    assert normalized_intents(requests[0]) == normalized_intents(requests[1])
    assert normalized_intents(requests[1]) == normalized_intents(requests[2])
```

- [ ] **Step 2: Write failing crypto spot-constraint parity test**

```python
def test_crypto_short_is_clamped_in_simulator_paper_and_live(crypto_state, panel):
    targets = pd.Series({"BTCUSD": -0.5, "ETHUSD": 0.5})
    for mode in ExecutionMode:
        request = fx_book.build_execution_request(
            "crypto", targets, panel, crypto_state, mode, "ccxt")
        assert all(i.instrument.symbol != "BTC/USDT" for i in request.intents)
```

- [ ] **Step 3: Run and verify RED**

Run: `pytest -q tests/test_fx_execution_parity.py tests/test_fx_book.py tests/test_fx_consistency.py tests/test_crypto_exec.py`

Expected: FAIL because FX/crypto paper books mutate weights directly.

- [ ] **Step 4: Implement FX/crypto simulator cost models**

FX fills use existing pair spreads, carry/funding, quote-currency conversion,
and signed quantities. Crypto spot fills use CCXT market precision/minimums,
fee currency, and long-only constraints. Preserve `fx_strategy.compute_targets`
as the only source of targets.

- [ ] **Step 5: Route `fx_book.run_once` and crypto CLI through the kernel**

Simulator remains the default. IBKR/OANDA paper practice and CCXT sandbox use
the same request. Live requires `--live`, promotion authorization, and
non-synthetic data. Update book snapshots from ledger projections and add
idempotent legacy-position migration events.

- [ ] **Step 6: Verify and commit**

Run: `pytest -q tests/test_fx_execution_parity.py tests/test_fx_book.py tests/test_fx_consistency.py tests/test_crypto_exec.py`

Expected: PASS.

```bash
git add trading_algo/forex/fx_config.py trading_algo/forex/fx_book.py \
  trading_algo/forex/engine.py trading_algo/forex/crypto_exec.py \
  tests/test_fx_execution_parity.py tests/test_fx_book.py \
  tests/test_fx_consistency.py tests/test_crypto_exec.py
git commit -m "feat(fx): unify FX and crypto paper live execution"
```

---

### Task 13: Event, Error, and Quarantine Operator View

**Files:**
- Create: `trading_algo/execution/reporting.py`
- Modify: `trading_algo/dashboard/server.py`
- Modify: `trading_algo/dashboard/static/index.html`
- Modify: `trading_algo/dashboard/static/app.js`
- Modify: `trading_algo/dashboard/static/styles.css`
- Create: `tests/test_execution_reporting.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Produces: `build_events_payload(ledger, filters)`, `build_errors_payload(error_log, filters)`, `build_quarantines_payload(ledger, account)`.
- Adds read-only routes:
  - `/api/execution/events?account=&venue=&instrument=&correlation_id=&limit=`
  - `/api/execution/errors?account=&severity=&venue=&instrument=&correlation_id=&limit=`
  - `/api/execution/quarantines?account=`

- [ ] **Step 1: Write failing reporting/redaction API tests**

```python
def test_execution_events_are_correlated_and_queryable(populated_execution_logs):
    payload = build_events_payload(
        populated_execution_logs.ledger,
        {"account": "full", "correlation_id": "corr-1", "limit": "50"},
    )
    assert payload["events"]
    assert {e["correlation_id"] for e in payload["events"]} == {"corr-1"}


def test_observability_redacts_secrets(dashboard_server, secret_error):
    response = dashboard_server.get("/api/execution/errors?account=crypto")
    text = response.body.decode()
    assert response.status == 200
    assert "secret-token" not in text
    assert "[REDACTED]" in text
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_execution_reporting.py tests/test_dashboard.py`

Expected: FAIL because reporting/routes are absent.

- [ ] **Step 3: Implement bounded read-only reporting**

Validate filter names and cap `limit` at 500. Return stable JSON records with
UTC timestamps and correlation/causation IDs. Apply redaction again at the
output boundary. Do not expose raw venue payloads by default.

- [ ] **Step 4: Add the dashboard execution-log view**

Add one `EXECUTION` tab with:

- current quarantines first;
- event/error toggle;
- time, severity, venue, instrument, and correlation filters;
- order lifecycle, retryability, and resulting action;
- copyable correlation ID;
- no mutating “clear” or “retry” button in this task.

Use existing dashboard utilities/styles; keep localhost-only behavior.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q tests/test_execution_reporting.py tests/test_dashboard.py tests/test_dashboard_terminal.py`

Expected: PASS.

```bash
git add trading_algo/execution/reporting.py trading_algo/dashboard \
  tests/test_execution_reporting.py tests/test_dashboard.py
git commit -m "feat(dashboard): expose execution events errors and quarantines"
```

---

### Task 14: Golden Parity Replay, Efficiency Guard, and Rollout Gates

**Files:**
- Create: `tests/fixtures/execution/equity_partial_fill.json`
- Create: `tests/fixtures/execution/fx_reject_retry.json`
- Create: `tests/fixtures/execution/crypto_timeout_reconcile.json`
- Create: `tests/test_execution_efficiency.py`
- Create: `tests/test_execution_live_gate.py`
- Create: `tests/test_execution_golden.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/HOW_IT_WORKS.md`
- Modify: `docs/DATA_FEEDS.md`
- Modify: `docs/CRYPTO_HF.md`
- Modify: `docs/specs/paper-live-parity.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: deterministic cross-asset golden scenarios and opt-in
  `broker_sandbox` pytest marker.
- Produces: documented rollout commands for simulator, paper/practice, and
  explicitly promoted live modes.

- [ ] **Step 1: Write failing efficiency and live-gate tests**

```python
import trading_algo.execution.kernel as execution_kernel

from trading_algo.promotion import PromotionError


def test_vectorized_backtest_does_not_construct_execution_kernel(monkeypatch, synth_asx):
    monkeypatch.setattr(
        execution_kernel, "ExecutionKernel",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("kernel constructed")),
    )
    run_backtest(synth_asx[0], synth_asx[1], get_region("ASX"))


@pytest.mark.parametrize("venue", ["ibkr", "oanda", "ccxt"])
def test_live_submission_requires_promotion_and_explicit_live(venue, live_kernel):
    with pytest.raises(PromotionError):
        live_kernel(venue=venue, explicit_live=False).execute(**sample_target())
    with pytest.raises(PromotionError):
        live_kernel(venue=venue, explicit_live=True,
                    promotion_state={"passed": False}).execute(**sample_target())
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_execution_efficiency.py tests/test_execution_live_gate.py`

Expected: FAIL until final mode/gate wiring is complete.

- [ ] **Step 3: Add three literal golden scenario fixtures**

Each JSON fixture contains fixed timestamps, decimal-string quantities/prices,
snapshot, starting venue state, target weights, scripted venue events, and
literal expected final balances/positions/order states/quarantines. Do not
generate expected values with production functions.

- [ ] **Step 4: Add golden replay tests**

```python
@pytest.mark.parametrize("fixture", sorted(EXECUTION_FIXTURES.glob("*.json")))
def test_golden_execution_scenario(fixture):
    expected = json.loads(fixture.read_text())
    actual = replay_scenario(str(fixture)).to_record()
    assert actual == expected["expected_projection"]
```

Run: `pytest -q tests/test_execution_golden.py`

Expected: PASS after hand-checking each expected monetary result.

- [ ] **Step 5: Register sandbox marker and document exact operations**

Add to `pyproject.toml`:

```toml
markers = [
    "broker_sandbox: opt-in integration requiring paper/sandbox credentials",
]
```

Document:

```bash
# Deterministic parity, no network
python -m trading_algo.engine --once --account full --execution-mode simulator

# Broker paper/practice
python -m trading_algo.engine --once --account full --execution-mode paper --venue ibkr
python -m trading_algo.forex.engine --once --execution-mode paper --execution-venue oanda

# Live remains explicit and promoted
python -m trading_algo.engine --once --account full \
  --execution-mode live --venue ibkr --live
```

Explain that “100%” means deterministic, idempotent, recoverable outcomes—not
external venue uptime.

- [ ] **Step 6: Run complete verification**

Run:

```bash
pytest -q tests/test_execution_models.py tests/test_execution_ledger.py \
  tests/test_execution_observability.py tests/test_order_lifecycle.py \
  tests/test_execution_accounting.py tests/test_execution_recovery.py \
  tests/test_execution_planner.py tests/test_execution_parity.py \
  tests/test_reconciliation.py tests/test_execution_kernel.py \
  tests/adapters tests/test_equity_execution_parity.py \
  tests/test_fx_execution_parity.py tests/test_execution_reporting.py \
  tests/test_execution_efficiency.py tests/test_execution_live_gate.py \
  tests/test_execution_golden.py
pytest -q
ruff check trading_algo tests
python -m trading_algo.execution_replay \
  --scenario tests/fixtures/execution/equity_partial_fill.json
```

Expected: all tests pass, Ruff passes, and replay JSON equals the committed
literal projection.

- [ ] **Step 7: Update spec evidence and commit**

Mark an acceptance criterion ✅ only when its named verification has passed.
Keep `status: agreed` until every AC passes; then ask the user before changing
the spec to `done`.

```bash
git add tests/fixtures/execution tests/test_execution_efficiency.py \
  tests/test_execution_live_gate.py tests/test_execution_golden.py \
  pyproject.toml README.md docs
git commit -m "test(execution): verify end to end paper live parity"
```

---

## Program Completion Gate

The delivery is complete only when:

1. AC-1 through AC-15 in `docs/specs/paper-live-parity.md` have passing evidence.
2. Simulator, IBKR, OANDA, and CCXT pass the common adapter contract.
3. The three literal golden scenarios replay exactly after a cold restart.
4. Existing equity/FX target-consistency and costs-on tests remain unchanged and
   green.
5. Full pytest and Ruff pass.
6. Paper/practice sandbox evidence is recorded for the deployed version.
7. Live remains disabled until the user separately authorizes a canary.

## Spec Coverage Map

| Acceptance criteria | Implemented and verified in |
|---|---|
| AC-1, AC-8 | Tasks 5, 11, and 12 mode-parity tests |
| AC-2, AC-11 | Tasks 4 and 7 crash recovery, idempotency, and cold replay |
| AC-3, AC-4 | Task 3 lifecycle matrix and fill-accounting tests |
| AC-5, AC-6 | Task 7 reconciliation and instrument-quarantine tests |
| AC-7 | Tasks 6, 8, 9, and 10 common adapter-contract suite |
| AC-9, AC-10 | Task 14 efficiency and live-gate tests |
| AC-12 | Task 14 golden replay, sandbox marker, full pytest, and Ruff gates |
| AC-13, AC-14, AC-15 | Tasks 2 and 13 durable logs, structured errors, correlation, querying, and redaction |

No acceptance criterion depends solely on documentation or manual inspection.
