"""Multi-account FX paper book: lifecycle, isolation, idempotency."""
import pytest

from trading_algo.forex import fx_book
from trading_algo.forex.agents import AgentPool


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def pool():
    return AgentPool(max_workers=1)


def test_init_and_run(isolated_state, pool):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["equity"] > 0
    assert state["last_bar_date"] is not None
    assert state["equity_history"]
    assert state["positions"]            # took positions on first run
    assert len(state["trades"]) > 0


def test_accounts_are_isolated(isolated_state, pool):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.init_account("partner", 5_000, "conservative")
    fx_book.run_once("matt", synthetic=True, pool=pool)
    fx_book.run_once("partner", synthetic=True, pool=pool)
    a = fx_book.load_state("matt")
    b = fx_book.load_state("partner")
    assert a["profile"] == "balanced"
    assert b["profile"] == "conservative"
    # conservative caps gross leverage tighter than balanced
    gross_a = sum(abs(v) for v in a["positions"].values())
    gross_b = sum(abs(v) for v in b["positions"].values())
    assert gross_b <= gross_a + 1e-9
    assert set(fx_book.list_accounts()) == {"matt", "partner"}


def test_run_is_idempotent_same_bar(isolated_state, pool):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=pool)
    s1 = fx_book.load_state("matt")
    fx_book.run_once("matt", synthetic=True, pool=pool)   # same latest bar
    s2 = fx_book.load_state("matt")
    assert s1["equity"] == s2["equity"]
    assert len(s1["trades"]) == len(s2["trades"])
    assert len(s1["equity_history"]) == len(s2["equity_history"])


def test_init_does_not_overwrite_without_force(isolated_state):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.init_account("matt", 9_999, "aggressive")     # should be ignored
    assert fx_book.load_state("matt")["initial_capital"] == 5_000
    fx_book.init_account("matt", 9_999, "aggressive", force=True)
    assert fx_book.load_state("matt")["initial_capital"] == 9_999


def test_conservative_profile_lower_gross_than_aggressive(isolated_state, pool):
    fx_book.init_account("c", 5_000, "conservative")
    fx_book.init_account("a", 5_000, "aggressive")
    fx_book.run_once("c", synthetic=True, pool=pool)
    fx_book.run_once("a", synthetic=True, pool=pool)
    gc = sum(abs(v) for v in fx_book.load_state("c")["positions"].values())
    ga = sum(abs(v) for v in fx_book.load_state("a")["positions"].values())
    assert gc <= ga + 1e-9
