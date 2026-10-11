"""Backlog F18 / foundation P0-H: schema-validated paper state files.

Covers the acceptance criteria:
  AC1 invalid file -> load raises (with the gate on), no trade.
  AC2 a rejected file fails safe (halt), never silently resets equity/trades.
  AC3 an old-format file migrates (is upgraded, not lost).
"""
import json

import pytest

from trading_algo import config as cfg
from trading_algo import paper_trade, pnl, state_schema
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


# --- fail-safe default (B1) ------------------------------------------------
def test_validate_state_files_defaults_to_fail_safe():
    """B1: before real capital, state validation defaults ON. A corrupted-but-
    parseable file must HALT the run, not fail open and silently reset a book."""
    assert cfg.VALIDATE_STATE_FILES is True


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


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_are_not_valid_numbers(bad):
    s = _good_state()
    s["sleeves"]["US"]["cash"] = bad
    assert validate_state(s), "persisted monetary values must be finite JSON numbers"


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


# --- v3: the on-disk version is a contract (Task 12b) -----------------------
# Tasks 10-13d changed the book's shape. v3 names it; code older than a file
# must refuse it rather than relabel it, and a v3 book that holds no v3-only
# fact can be handed back to v2 code. The key list is the spec, so it is
# written out here rather than imported from the code under test.
V3_SLEEVE_KEYS = {"pending_target", "last_decision_date", "last_dividend_date",
                  "last_interest_date", "opened_on"}
ARCHIVE = "state/archive/2026-09-pre-truth"


def _v2_state() -> dict:
    s = _good_state()
    s["schema_version"] = 2
    return s


def _v3_state() -> dict:
    """A v3 book as v3 code holds it: a v2 file, migrated."""
    s, _ = migrate_state(_v2_state())
    return s


def _cash_event(side: str) -> dict:
    return {"date": "2026-10-02", "region": "US", "ticker": "CASH", "side": side,
            "shares": 1, "fill": 4.11, "commission": 0.0, "stamp_duty": 0.0,
            "currency": "USD"}


def test_a_v2_file_migrates_to_v3_and_validates():
    migrated, applied = migrate_state(_v2_state())
    assert migrated["schema_version"] == 3
    assert validate_state(migrated) == []
    sleeve = migrated["sleeves"]["US"]
    # Backfilled only where None and absent read the same to every reader.
    for key in ("last_decision_date", "last_dividend_date", "last_interest_date"):
        assert key in sleeve and sleeve[key] is None, key
    assert applied


@pytest.mark.parametrize("seeded", ["absent", None, "2026-10-01"])
def test_migration_never_backfills_opened_on_or_pending_target(seeded):
    """Absent opened_on means a LEGACY sleeve (never stamped); None means a new
    one whose first decision is still to come. Backfilling None would stamp a
    legacy book's next decision as its first. Absent pending_target means
    nothing pending — `_fill_pending` pops it rather than setting None."""
    s = _v2_state()
    if seeded != "absent":
        s["sleeves"]["US"]["opened_on"] = seeded
    sleeve = migrate_state(s)[0]["sleeves"]["US"]
    assert "pending_target" not in sleeve
    if seeded == "absent":
        assert "opened_on" not in sleeve
    else:
        assert sleeve["opened_on"] == seeded


def test_migration_keeps_a_v3_value_it_would_otherwise_backfill():
    s = _v2_state()
    s["sleeves"]["US"]["last_interest_date"] = "2026-10-02"
    assert migrate_state(s)[0]["sleeves"]["US"]["last_interest_date"] == "2026-10-02"


def test_a_v2_book_runs_exactly_as_it_did_before_the_bump(tmp_path, monkeypatch,
                                                          paper_cycle):
    """Two copies of one v2 book run in lockstep on the same synthetic bars:
    one as v3 code migrates it (stamped 2, backfilled), one as the code before
    the bump held it (no v3 keys at all). Every fill, mark and cash figure
    must agree — the backfilled defaults mean exactly what absence did."""
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    paper_trade.init_account("v2", capital=100_000, synthetic=True,
                             allocations={"US": 1.0})
    book = json.loads((tmp_path / "paper_state_v2.json").read_text())
    for sleeve in book["sleeves"].values():          # the shape v2 code wrote
        for key in V3_SLEEVE_KEYS:
            sleeve.pop(key, None)
    as_v2 = {**book, "schema_version": 2}
    as_before = {**book, "account": "before",
                 "schema_version": state_schema.STATE_SCHEMA_VERSION}
    (tmp_path / "paper_books.db").unlink()           # load these, not init's row
    _write(tmp_path, "v2", as_v2)
    _write(tmp_path, "before", as_before)

    loaded = paper_trade.load_state("v2")
    assert loaded["schema_version"] == 3 and validate_state(loaded) == []

    for _ in range(3):                               # decide, fill, hold
        paper_cycle("v2", sessions=1)
        paper_trade.run_daily("before", synthetic=True)   # the same bar
    a, b = paper_trade.load_state("v2"), paper_trade.load_state("before")
    assert any(pnl.is_fill(t) for t in a["trades"]), "the run must trade"
    for key in ("trades", "equity_history", "sleeve_history", "allocations"):
        assert a[key] == b[key], key
    for k in a["sleeves"]:
        assert a["sleeves"][k] == b["sleeves"][k], k


def test_a_file_newer_than_the_code_is_left_alone_and_refused(monkeypatch, tmp_path):
    """Code older than a file must not relabel it as its own version and read it
    by its own rules: that is how a code-only rollback reads DIV rows as SELLs."""
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", True)
    newer_v = 4
    newer = _good_state()
    newer["schema_version"] = newer_v
    newer["sleeves"]["US"].pop("realized_pnl")       # a gap old code would "fill"
    before = json.loads(json.dumps(newer))

    migrated, applied = migrate_state(json.loads(json.dumps(newer)))
    assert migrated == before and applied == []      # not touched, not re-stamped

    _write(tmp_path, "newer", newer)
    with pytest.raises(StateValidationError) as exc:
        paper_trade.load_state("newer")
    msg = str(exc.value)
    assert f"schema v{newer_v}" in msg
    assert f"reads v{state_schema.STATE_SCHEMA_VERSION}" in msg
    assert "newer code" in msg and "restore" in msg
    on_disk = json.loads((tmp_path / "paper_state_newer.json").read_text())
    assert on_disk == before                         # nothing written back


def test_shadow_mode_still_never_relabels_a_newer_file(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "VALIDATE_STATE_FILES", False)
    newer = _good_state()
    newer["schema_version"] = 4
    _write(tmp_path, "newer", newer)
    assert paper_trade.load_state("newer")["schema_version"] == 4


@pytest.mark.parametrize("stamp", ["3", 3.0, True, 0, -1])
def test_a_stamp_that_is_not_a_version_number_is_refused(stamp):
    """Versions start at 1; no stamp at all is the pre-schema file. Anything
    else was not written by this code's lineage and is not its to relabel."""
    s = _good_state()
    s["schema_version"] = stamp
    assert migrate_state(s)[0]["schema_version"] == stamp
    assert any("schema" in e for e in validate_state(s))


def test_downgrade_of_a_book_holding_only_a_staged_target_is_exact():
    """A staged target is a decision not yet filled. v2 code never reads one:
    it is stripped, and v2 code holds the book until the next month's
    rebalance. Nothing else changes, so the round trip is exact."""
    s = _v3_state()
    s["sleeves"]["US"]["pending_target"] = {
        "date": "2026-10-01", "weights": {"AAPL": 0.5}, "frozen": []}
    original = json.loads(json.dumps(s))

    down = state_schema.downgrade_state(s)
    assert down["schema_version"] == 2
    for k, sleeve in down["sleeves"].items():
        assert not V3_SLEEVE_KEYS & set(sleeve), k
    assert validate_state(down) == []
    assert s == original                             # the input is untouched

    expected = json.loads(json.dumps(s))
    del expected["sleeves"]["US"]["pending_target"]
    assert migrate_state(down)[0] == expected        # the round trip


def test_downgrade_strips_window_markers_and_an_undecided_open():
    """Markers v2 code would leave stale are dropped, so v3 re-arms them; an
    `opened_on` still None (no decision yet) must come back as legacy, because
    v2 code may make that first decision."""
    s = _v3_state()
    s["sleeves"]["US"].update(last_decision_date="2026-10-01",
                              last_dividend_date="2026-10-02",
                              last_interest_date="2026-10-02", opened_on=None)
    sleeve = state_schema.downgrade_state(s)["sleeves"]["US"]
    assert not V3_SLEEVE_KEYS & set(sleeve)


@pytest.mark.parametrize("what", ["DIV row", "INT row", "new cash event", "opened_on"])
def test_downgrade_refuses_a_book_holding_a_v3_only_fact(what, monkeypatch):
    """Cash that arrived without a fill has no v2 form (v2 reads the row as a
    SELL), and an `opened_on` once stripped can never be re-stamped. Refuse,
    and send the operator to the archive."""
    s = _v3_state()
    if what == "DIV row":
        s["trades"].append(_cash_event(pnl.DIVIDEND))
    elif what == "INT row":
        s["trades"].append(_cash_event(pnl.INTEREST))
    elif what == "new cash event":                   # asks pnl, not a literal list
        monkeypatch.setattr(pnl, "CASH_EVENTS", pnl.CASH_EVENTS | {"FEE"})
        s["trades"].append(_cash_event("FEE"))
    else:
        s["sleeves"]["US"]["opened_on"] = "2026-10-01"
    original = json.loads(json.dumps(s))

    with pytest.raises(StateValidationError) as exc:
        state_schema.downgrade_state(s)
    assert ARCHIVE in str(exc.value)
    assert s == original


def test_downgrade_refuses_what_it_does_not_know():
    newer = _v3_state()
    newer["schema_version"] = 4
    with pytest.raises(StateValidationError, match="v4"):
        state_schema.downgrade_state(newer)
    with pytest.raises(StateValidationError, match="v1"):
        state_schema.downgrade_state(_v3_state(), to=1)
