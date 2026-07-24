"""Schema + migration for paper-trading state files (backlog F18 / foundation P0-H).

`paper_state_{account}.json` is the source of truth for a live paper (and, once
promoted, real) book. It is read and written on every run and read by the future
promotion gate (F10). A crashed mid-write or a hand-edit can silently corrupt it;
without a check, the next run trades on garbage or — worse — resets equity.

This module is pure stdlib (no numpy/pandas) so it can run anywhere the engine
does. It provides:

  * ``validate_state(state)``  -> list[str]   (empty == valid)
  * ``migrate_state(state)``   -> (state, applied)  additive, non-destructive
  * ``StateValidationError``   raised by callers that choose to fail safe

The design rule (AC2 in the backlog): an invalid file must FAIL SAFE — the caller
raises and halts rather than reinitialising equity/trades. Migration only ever
*adds* missing optional keys with safe defaults; it never drops or rewrites data,
so an older pre-schema file is upgraded, not lost.
"""
from __future__ import annotations

from numbers import Number

# Bump when the on-disk shape changes; migrate_state stamps it so old files can
# be recognised and upgraded rather than rejected.
# v2: per-book reporting group + strategy/risk profile fields
#     (group / profile / param_overrides / max_drawdown_stop).
STATE_SCHEMA_VERSION = 2


class StateValidationError(Exception):
    """Raised when a state file fails validation and the caller fails safe."""


def _is_number(x) -> bool:
    # bool is a subclass of int; a flag is not a monetary amount.
    return isinstance(x, Number) and not isinstance(x, bool)


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
    if not isinstance(state, dict):
        return state, applied

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
        state["schema_version"] = STATE_SCHEMA_VERSION
        if not applied:
            applied.append("stamp schema_version")
    return state, applied
