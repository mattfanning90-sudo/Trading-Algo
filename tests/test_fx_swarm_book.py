import random

from trading_algo.forex import champions, fx_book
from trading_algo.forex import genome as gm
from trading_algo.forex.agents import AgentPool, default_agents


def test_champion_pool_expands_roster_for_a_daily_book(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    rng = random.Random(0)
    champions.save_roster("matt", [gm.random_genome(rng) for _ in range(2)],
                          meta={"pbo": 0.1})
    fx_book.init_account("matt", 5_000, "balanced")
    pool = fx_book.champion_pool("matt")
    assert isinstance(pool, AgentPool)
    assert len(pool.agents) == len(default_agents()) + 2


def test_run_once_with_champions_uses_expanded_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(fx_book, "_CHAMPION_POOLS", {})   # no stale roster leaking in
    rng = random.Random(1)
    champions.save_roster("matt", [gm.random_genome(rng) for _ in range(1)],
                          meta={"pbo": 0.1})
    fx_book.init_account("matt", 5_000, "balanced")
    # should not raise; a champion is now part of the voting roster
    fx_book.run_once("matt", synthetic=True, use_champions=True)
    state = fx_book.load_state("matt")
    assert "positions" in state
