"""SQLite book store + crash-safe JSON writer, and the dual-write wiring
in paper_trade / fx_book."""
import json
import os

import pytest

from trading_algo import storage
from trading_algo import paper_trade as pt
from trading_algo.forex import fx_book


# --- storage module ---------------------------------------------------------
def test_db_round_trip(tmp_path):
    db = str(tmp_path / "books.db")
    assert storage.db_load(db, "matt") is None          # nothing yet
    storage.db_save(db, "matt", {"equity": 100.0, "positions": {"AAPL": 3}})
    got = storage.db_load(db, "matt")
    assert got == {"equity": 100.0, "positions": {"AAPL": 3}}


def test_db_upsert_overwrites(tmp_path):
    db = str(tmp_path / "books.db")
    storage.db_save(db, "a", {"v": 1})
    storage.db_save(db, "a", {"v": 2})
    assert storage.db_load(db, "a") == {"v": 2}
    assert storage.db_accounts(db) == ["a"]             # one row, not two


def test_db_accounts_and_has(tmp_path):
    db = str(tmp_path / "books.db")
    assert storage.db_accounts(db) == []                # missing DB → empty
    assert storage.db_has(db, "x") is False
    storage.db_save(db, "b", {})
    storage.db_save(db, "a", {})
    assert storage.db_accounts(db) == ["a", "b"]        # sorted
    assert storage.db_has(db, "a") is True


def test_atomic_write_json_is_valid_and_leaves_no_tmp(tmp_path):
    path = str(tmp_path / "book.json")
    storage.atomic_write_json(path, {"hello": "world"})
    assert json.load(open(path)) == {"hello": "world"}
    assert not os.path.exists(path + ".tmp")            # temp cleaned up by rename


def test_atomic_write_overwrites_prior_content(tmp_path):
    path = str(tmp_path / "book.json")
    storage.atomic_write_json(path, {"v": 1})
    storage.atomic_write_json(path, {"v": 2})
    assert json.load(open(path)) == {"v": 2}


# --- paper_trade wiring -----------------------------------------------------
def test_paper_dual_writes_db_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.save_state("test", {"base_currency": "AUD", "equity_history": []})
    # source of truth is the DB...
    assert storage.db_has(pt._db_path(), "test")
    # ...and the JSON fallback exists for the dashboards / CI globs
    assert os.path.exists(pt._state_file("test"))
    assert pt.load_state("test")["base_currency"] == "AUD"


def test_paper_reads_legacy_json_when_db_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    # a book that predates the DB: JSON only, no DB row
    with open(pt._state_file("old"), "w") as f:
        json.dump({"base_currency": "AUD"}, f)
    assert not os.path.exists(pt._db_path())
    assert pt.load_state("old")["base_currency"] == "AUD"
    assert pt.account_exists("old")


def test_paper_load_prefers_db_over_stale_json(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.save_state("test", {"tag": "fresh"})
    # simulate a stale JSON left behind; DB must win
    with open(pt._state_file("test"), "w") as f:
        json.dump({"tag": "stale"}, f)
    assert pt.load_state("test")["tag"] == "fresh"


def test_paper_missing_account_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        pt.load_state("nope")


# --- fx_book wiring ---------------------------------------------------------
def test_fx_list_accounts_sees_db_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.save_state("matt", {"profile": "balanced"})        # dual-write
    # a legacy JSON-only book
    with open(fx_book._state_file("legacy"), "w") as f:
        json.dump({"profile": "balanced"}, f)
    assert set(fx_book.list_accounts()) == {"matt", "legacy"}


def test_fx_dual_write_and_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.save_state("partner", {"profile": "aggressive"})
    assert storage.db_has(fx_book._db_path(), "partner")
    assert os.path.exists(fx_book._state_file("partner"))
    assert fx_book.load_state("partner")["profile"] == "aggressive"
    assert fx_book.account_exists("partner")
