"""Schema + migration for paper-trading state files (backlog F18 / foundation P0-H).

`paper_state_{account}.json` is the source of truth for a live paper (and, once
promoted, real) book. It is read and written on every run and read by the future
promotion gate (F10). A crashed mid-write or a hand-edit can silently corrupt it;
without a check, the next run trades on garbage or — worse — resets equity.

This module is pure stdlib (no numpy/pandas) so it can run anywhere the engine
does. It provides:

  * ``validate_state(state)``  -> list[str]   (empty == valid)
  * ``migrate_state(state)``   -> (state, applied)  additive, non-destructive
  * ``downgrade_state(state)`` -> v2 copy for a code rollback, or refuses
  * ``StateValidationError``   raised by callers that choose to fail safe

The design rule (AC2 in the backlog): an invalid file must FAIL SAFE — the caller
raises and halts rather than reinitialising equity/trades. Migration only ever
*adds* missing optional keys with safe defaults; it never drops or rewrites data,
so an older pre-schema file is upgraded, not lost.

The version is a contract. A file NEWER than this code is never migrated or
re-stamped: ``validate_state`` reports it and the caller halts.

Rollback
--------
Code and data roll back TOGETHER. Code from before v3 has no such guard: handed
a v3 file it re-stamps it as its own and reads every cash-event row (DIV, INT)
as a SELL, silently. So before running pre-v3 code, stop the schedulers and
either downgrade the book (this line writes its DB row and JSON together, and
``downgrade_state`` refuses, naming why, a book v2 cannot represent)::

    python3 -c "from trading_algo import paper_trade as p, state_schema as s; a = 'full'; p.save_state(a, s.downgrade_state(p.load_state(a)))"

or restore its JSON AND ``paper_books.db`` from the pre-change archive,
``state/archive/2026-09-pre-truth/`` (Task 26; its README has the command).
"""
from __future__ import annotations

import copy
import math

from . import pnl

# Bump when the on-disk shape changes; migrate_state stamps it so old files can
# be recognised and upgraded rather than rejected.
# v2: per-book reporting group + strategy/risk profile fields
#     (group / profile / param_overrides / max_drawdown_stop).
# v3: next-close fills, cash events and a per-sleeve open date
#     (sleeve pending_target / last_decision_date / last_dividend_date /
#     last_interest_date / opened_on; ledger rows whose side is in
#     pnl.CASH_EVENTS, which move cash and fill nothing).
STATE_SCHEMA_VERSION = 3

# The v3 sleeve keys, by what ABSENT means to the code that reads them.
# Backfilled with None: every reader does `.get()` then a truthiness test, so
# None and absent both read "not yet" (no fill decided, no window armed).
_V3_NONE_DEFAULTS = ("last_decision_date", "last_dividend_date", "last_interest_date")
# Never backfilled: absent means something no default can stand in for.
#   pending_target — absent IS "nothing pending" (`_fill_pending` pops it).
#   opened_on      — absent = a legacy sleeve, never stamped; None = a new one,
#                    stamped at its first decision. Backfilling None would stamp
#                    a legacy book's NEXT decision as its first.
_V3_SLEEVE_KEYS = (*_V3_NONE_DEFAULTS, "pending_target", "opened_on")
# Where a book v2 cannot represent comes back from (Task 26's archive).
_ARCHIVE = "state/archive/2026-09-pre-truth/"


class StateValidationError(Exception):
    """Raised when a state file fails validation and the caller fails safe."""


def _unknown_version(state: dict) -> bool:
    """True for a stamp this code did not write and cannot upgrade: newer than
    STATE_SCHEMA_VERSION, or not a version number (versions start at 1). No
    stamp at all is a pre-schema file, which migrates."""
    v = state.get("schema_version")
    if v is None:
        return False
    return not (isinstance(v, int) and not isinstance(v, bool)
                and 1 <= v <= STATE_SCHEMA_VERSION)


def _is_number(x) -> bool:
    # bool is a subclass of int; a flag is not a monetary amount.
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    return math.isfinite(x)


def _validate_sleeve(key: str, sleeve) -> list[str]:
    errs: list[str] = []
    tag = f"sleeve '{key}'"
    if not isinstance(sleeve, dict):
        return [f"{tag} must be an object"]
    if not isinstance(sleeve.get("currency"), str):
        errs.append(f"{tag} missing string 'currency'")
    if not _is_number(sleeve.get("cash")):
        errs.append(f"{tag} 'cash' must be a number")
    positions = sleeve.get("positions")
    if not isinstance(positions, dict):
        errs.append(f"{tag} 'positions' must be an object")
    else:
        for t, sh in positions.items():
            if not _is_number(sh):
                errs.append(f"{tag} position '{t}' must be a number, got {type(sh).__name__}")
    if not isinstance(sleeve.get("cost_basis", {}), dict):
        errs.append(f"{tag} 'cost_basis' must be an object")
    if not _is_number(sleeve.get("realized_pnl", 0.0)):
        errs.append(f"{tag} 'realized_pnl' must be a number")
    lrm = sleeve.get("last_rebalance_month", None)
    if lrm is not None and not isinstance(lrm, str):
        errs.append(f"{tag} 'last_rebalance_month' must be a string or null")
    return errs


def validate_state(state) -> list[str]:
    """Return a list of problems with a loaded state dict; empty means valid."""
    if not isinstance(state, dict):
        return ["state must be a JSON object"]
    if _unknown_version(state):
        # Alone: this code's rules cannot judge a shape it does not know.
        v = state["schema_version"]
        return [f"the file is schema v{v} but this code reads v{STATE_SCHEMA_VERSION} "
                "and older: it was written by newer code, and this code would misread "
                "it. It is left as it is. Run code at that version or newer, or restore "
                "the backup taken before that code first ran (see state_schema, "
                "'Rollback')."]

    errs: list[str] = []
    if not isinstance(state.get("account"), str):
        errs.append("missing string 'account'")
    if not isinstance(state.get("base_currency"), str):
        errs.append("missing string 'base_currency'")
    if not _is_number(state.get("initial_capital_base")):
        errs.append("'initial_capital_base' must be a number")
    elif state["initial_capital_base"] <= 0:
        errs.append("'initial_capital_base' must be > 0")

    allocations = state.get("allocations")
    if not isinstance(allocations, dict) or not allocations:
        errs.append("'allocations' must be a non-empty object")
    else:
        for k, v in allocations.items():
            if not _is_number(v):
                errs.append(f"allocation '{k}' must be a number")

    sleeves = state.get("sleeves")
    if not isinstance(sleeves, dict) or not sleeves:
        errs.append("'sleeves' must be a non-empty object")
    else:
        for k, sleeve in sleeves.items():
            errs += _validate_sleeve(k, sleeve)
        # every allocated region must have a sleeve
        if isinstance(allocations, dict):
            for k in allocations:
                if k not in sleeves:
                    errs.append(f"allocation '{k}' has no matching sleeve")

    for key in ("trades", "equity_history"):
        if not isinstance(state.get(key), list):
            errs.append(f"'{key}' must be a list")

    # Optional per-book shape fields (present on profiled books). Validate only
    # when present so pre-profile files stay valid.
    if "group" in state and not isinstance(state["group"], str):
        errs.append("'group' must be a string")
    if "param_overrides" in state and not isinstance(state["param_overrides"], dict):
        errs.append("'param_overrides' must be an object")
    if "profile" in state and state["profile"] is not None \
            and not isinstance(state["profile"], str):
        errs.append("'profile' must be a string or null")
    if "max_drawdown_stop" in state and state["max_drawdown_stop"] is not None \
            and not _is_number(state["max_drawdown_stop"]):
        errs.append("'max_drawdown_stop' must be a number or null")

    return errs


def migrate_state(state: dict) -> tuple[dict, list[str]]:
    """Additively upgrade an older/looser state dict to the current schema.

    Only fills in missing OPTIONAL keys with safe defaults; never drops or
    rewrites existing data. Returns (state, applied) where `applied` lists the
    migrations performed (empty if the file was already current).
    """
    applied: list[str] = []
    if not isinstance(state, dict) or _unknown_version(state):
        return state, applied        # not this code's to relabel: validate reports it

    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        # Backfill sleeve sub-fields that early files predate.
        for k, sleeve in (state.get("sleeves") or {}).items():
            if not isinstance(sleeve, dict):
                continue
            if "cost_basis" not in sleeve:
                sleeve["cost_basis"] = {}
                applied.append(f"sleeve '{k}': add cost_basis")
            if "realized_pnl" not in sleeve:
                sleeve["realized_pnl"] = 0.0
                applied.append(f"sleeve '{k}': add realized_pnl")
            if "last_rebalance_month" not in sleeve:
                sleeve["last_rebalance_month"] = None
                applied.append(f"sleeve '{k}': add last_rebalance_month")
        # allocations defaulted from the sleeve set if absent
        if "allocations" not in state and isinstance(state.get("sleeves"), dict):
            n = len(state["sleeves"]) or 1
            state["allocations"] = {k: 1.0 / n for k in state["sleeves"]}
            applied.append("add allocations from sleeve set")
        list_defaults: tuple[tuple[str, list], ...] = (
            ("trades", []), ("equity_history", []), ("sleeve_history", []))
        for key, default in list_defaults:
            if key not in state:
                state[key] = default
                applied.append(f"add {key}")
        # v2: reporting group + strategy shape. A pre-profile book is a plain
        # CORE, no-override book (max_drawdown_stop left absent → global default).
        if "group" not in state:
            state["group"] = "CORE"
            applied.append("add group")
        if "param_overrides" not in state:
            state["param_overrides"] = {}
            applied.append("add param_overrides")
        # v3: only the keys whose readers take None as absent (see above).
        for k, sleeve in (state.get("sleeves") or {}).items():
            if not isinstance(sleeve, dict):
                continue
            for key in _V3_NONE_DEFAULTS:
                if key not in sleeve:
                    sleeve[key] = None
                    applied.append(f"sleeve '{k}': add {key}")
        state["schema_version"] = STATE_SCHEMA_VERSION
        if not applied:
            applied.append("stamp schema_version")
    return state, applied


def downgrade_state(state: dict, to: int = 2) -> dict:
    """Return a copy of a v3 book that v2 code reads correctly, or raise
    StateValidationError naming why it cannot. An operator tool for rolling
    code back (see 'Rollback' above); nothing on a run path calls it.

    Exact when the book holds no v3-only FACT: the v3 sleeve keys are stripped
    and the book is stamped 2. What stripping costs:

      * last_decision_date / last_dividend_date / last_interest_date: v2 code
        neither reads nor maintains them. Left in, they would go stale under
        v2's trades and v3 code coming back would credit a window across them;
        stripped, v3 re-arms each window on its first run, as for any v2 book.
      * pending_target, a decision staged and not yet filled: v2 code never
        reads one. That month's fill is lost: `last_rebalance_month` already
        records the decision, so v2 code holds the book as it stands until the
        next month's rebalance (a sleeve never yet filled sits in cash, which
        v2's verify grades never-traded). A staged breaker exit is not lost:
        v2 code liquidates a halted book itself, from `risk_halted`.
      * opened_on still None (a sleeve that has not decided yet): v2 code may
        make that first decision, so the sleeve must come back as legacy
        (never stamped) rather than stamp a later decision as its first.

    REFUSES, rather than invent a lossy conversion, when any ledger row is a
    cash event (`pnl.is_cash_event`: cash that arrived without a fill, which
    v2 reads as a SELL), when any sleeve has `opened_on` set (stripped, it can
    never be re-stamped), or when the file is not v2/v3. The way back for such
    a book is the pre-change archive, restored with the code it belongs to.
    """
    if to != 2:
        raise StateValidationError(
            f"no downgrade to v{to}: this code knows one step, v3 -> v2")
    if not isinstance(state, dict) or _unknown_version(state):
        raise StateValidationError(validate_state(state)[0])
    out, _ = migrate_state(copy.deepcopy(state))
    why = []
    cash = [t for t in out.get("trades") or []
            if isinstance(t, dict) and pnl.is_cash_event(t)]
    if cash:
        sides = ", ".join(sorted({t["side"] for t in cash}))
        why.append(f"{len(cash)} cash-event row(s) ({sides}): cash that arrived "
                   "without a fill, which v2 code reads as a SELL")
    for k, sleeve in (out.get("sleeves") or {}).items():
        if isinstance(sleeve, dict) and sleeve.get("opened_on") is not None:
            why.append(f"sleeve '{k}' opened_on={sleeve['opened_on']}, which cannot "
                       "be re-stamped once stripped")
    if why:
        raise StateValidationError(
            f"cannot downgrade '{out.get('account')}' to v2 without losing data:\n  - "
            + "\n  - ".join(why)
            + f"\nRestore its JSON and paper_books.db from the pre-change archive, "
              f"{_ARCHIVE} (its README has the command), with the code it belongs to.")
    for sleeve in (out.get("sleeves") or {}).values():
        if isinstance(sleeve, dict):
            for key in _V3_SLEEVE_KEYS:
                sleeve.pop(key, None)
    out["schema_version"] = 2
    return out
