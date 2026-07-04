"""Backlog F18 / foundation P0-H: schema-validated paper state files.

Covers the acceptance criteria:
  AC1 invalid file -> load raises (with the gate on), no trade.
  AC2 a rejected file fails safe (halt), never silently resets equity/trades.
  AC3 an old-format file migrates (is upgraded, not lost).
"""
import json

import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade, state_schema
from trading_algo.state_schema import StateValidationError, migrate_state, validate_state


def _good_state() -> dict:
    return {
        "account": "t",
        "schema_version": state_schema.STATE_SCHEMA_VERSION,
        "base_currency": "AUD",
        "initial_capital_base": 100_000,
        "allocations": {"US": 1.0},
        "sleeves": {
            "US": {
                "currency": "USD",
                "cash": 100_000.0,
                "positions": {"AAPL": 10},
                "cost_basis": {"AAPL": 150.0},
                "realized_pnl": 0.0,
                "last_rebalance_month": "2026-06",
            }
        },
        "trades": [],
        "equity_history": [],
    }


# --- validate_state --------------------------------------------------------
def test_valid_state_passes():
    assert validate_state(_good_state()) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s.pop("account"), "account"),
    (lambda s: s.update(initial_capital_base=-1), "> 0"),
    (lambda s: s.update(allocations={}), "allocations"),
    (lambda s: s["sleeves"]["US"].update(cash="lots"), "cash"),
    (lambda s: s["sleeves"]["US"]["positions"].update(AAPL="ten"), "position"),
    (lambda s: s.update(trades="none"), "trades"),
    (lambda s: s.update(allocations={"FTSE": 1.0}), "no matching sleeve"),
])
def test_invalid_state_is_caught(mutate, needle):
    s = _good_state()
    mutate(s)
    errors = validate_state(s)
    assert any(needle in e for e in errors), f"expected an error mentioning {needle!r}: {errors}"


def test_bool_is_not_a_number():
    s = _good_state()
    s["sleeves"]["US"]["cash"] = True
    assert validate_state(s), "a bool must not pass as a monetary amount"


# --- migrate_state ---------------------------------------------------------
def test_migration_backfills_missing_sleeve_fields():
    old = {
        "account": "t",
        "base_currency": "AUD",
        "initial_capital_base": 100_000,
        "sleeves": {"US": {"currency": "USD", "cash": 100_000.0, "positions": {}}},
    }
    migrated, applied = migrate_state(old)
    assert applied, "expected migrations to be applied to an old file"
    assert migrated["schema_version"] == state_schema.STATE_SCHEMA_VERSION
    assert migrated["sleeves"]["US"]["cost_basis"] == {}
    assert migrated["sleeves"]["US"]["realized_pnl"] == 0.0
    assert "allocations" in migrated
    # A migrated old file is now valid.
    assert validate_state(migrated) == []


def test_migration_is_non_destructive():
    s = _good_state()
    s["sleeves"]["US"]["positions"]["AAPL"] = 42
    migrated, _ = migrate_state(s)
    assert migrated["sleeves"]["US"]["positions"]["AAPL"] == 42


# --- load_state wiring (fail-safe) -----------------------------------------
def _write(tmp_path, account, state):
    (tmp_path / f"paper_state_{account}.json").write_text(json.dumps(state))


def test_load_raises_on_invalid_when_gate_on(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", True)
    bad = _good_state()
    bad["sleeves"]["US"]["cash"] = "corrupt"
    _write(tmp_path, "bad", bad)
    with pytest.raises(StateValidationError):
        paper_trade.load_state("bad")


def test_load_warns_but_returns_when_gate_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", False)
    bad = _good_state()
    bad["sleeves"]["US"]["cash"] = "corrupt"
    _write(tmp_path, "bad", bad)
    state = paper_trade.load_state("bad")          # shadow mode: does not raise
    assert "shadow mode" in capsys.readouterr().out
    assert state["account"] == "t"                  # returned as-is, nothing reset


def test_invalid_load_never_resets_equity(monkeypatch, tmp_path):
    """AC2: a rejected file halts; it must not come back as a fresh zeroed book."""
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", True)
    bad = _good_state()
    bad["initial_capital_base"] = -5   # invalid
    _write(tmp_path, "bad", bad)
    with pytest.raises(StateValidationError):
        paper_trade.load_state("bad")
    # the file on disk is untouched — no silent reset/overwrite happened
    on_disk = json.loads((tmp_path / "paper_state_bad.json").read_text())
    assert on_disk["initial_capital_base"] == -5


def test_save_refuses_invalid_state_when_gate_on(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", True)
    s = _good_state()
    del s["sleeves"]["US"]["currency"]
    with pytest.raises(StateValidationError):
        paper_trade.save_state("bad", s)
    assert not (tmp_path / "paper_state_bad.json").exists()


def test_round_trip_with_gate_on(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", True)
    s = _good_state()
    paper_trade.save_state("ok", s)
    assert paper_trade.load_state("ok")["account"] == "t"
