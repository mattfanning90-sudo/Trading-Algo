import random

import pytest

from trading_algo.forex import champions, evolve
from trading_algo.forex import genome as gm
from trading_algo.forex.agents import default_agents
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE[:4], start="2016-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def _pop(n, seed=0):
    rng = random.Random(seed)
    return [gm.random_genome(rng) for _ in range(n)]


def test_gate_with_huge_n_trials_promotes_nothing(panel, params):
    """Deflating by a very large trial count should reject noise finalists."""
    _, hold, final = evolve.breed(panel, params, generations=2, pop_size=8, seed=1)
    finalists = [g for g, _ in final]
    passed, pbo = champions.gate(finalists, hold, params, n_trials=100000,
                                 dsr_min=0.95, pbo_max=0.5)
    assert passed == []
    assert 0.0 <= pbo <= 1.0


def test_rotation_cap_and_stable_core(panel, params):
    prev = _pop(3, seed=1)
    passed = _pop(5, seed=2)
    new = champions.apply_rotation(prev, passed, rotation_cap=2, top_k=6)
    # at most `rotation_cap` newcomers enter this cycle
    added = [g for g in new if g.gid not in {x.gid for x in prev}]
    assert len(added) <= 2
    assert len(new) <= 6


def test_rotation_admits_higher_dsr_newcomers_when_full():
    """A full roster must not freeze out strictly-better newcomers (bounded churn)."""
    prev = _pop(6, seed=10)                 # full roster (top_k == 6)
    better = _pop(2, seed=11)               # 2 fresh, higher-ranked candidates
    passed = better + prev                  # `passed` is best-first: newcomers rank above survivors
    new = champions.apply_rotation(prev, passed, rotation_cap=2, top_k=6)
    prev_gids = {g.gid for g in prev}
    admitted = [g for g in new if g.gid not in prev_gids]
    assert admitted, "higher-DSR newcomers frozen out despite rotation_cap headroom"
    assert len(admitted) <= 2                # rotation cap respected
    assert len(new) <= 6                     # top_k respected


def test_champions_agents_prepends_the_stable_core(tmp_path, monkeypatch, panel, params):
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    from trading_algo.forex import fx_book
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    champions.save_roster("matt", _pop(2, seed=9), meta={"pbo": 0.1})
    roster = champions.champions_agents("matt")
    assert len(roster) == len(default_agents()) + 2
    assert [a.name for a in roster[:5]] == [a.name for a in default_agents()]


def test_roster_round_trips(tmp_path, monkeypatch):
    from trading_algo.forex import fx_book
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    original = _pop(3, seed=4)
    champions.save_roster("matt", original, meta={"pbo": 0.2})
    back = champions.load_roster("matt")
    assert [g.gid for g in back] == [g.gid for g in original]
