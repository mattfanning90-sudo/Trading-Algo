"""Parallel agent ecosystem: signal range, causality, concurrency determinism."""
import pandas as pd
import pytest

from trading_algo.forex import agents
from trading_algo.forex.agents import AgentPool, PairContext, default_agents
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE, get_pair


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE[:3], start="2018-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def test_default_roster_has_five_agents():
    roster = default_agents()
    assert len(roster) == 5
    assert {a.name for a in roster} == {"trend", "breakout", "meanrev",
                                        "momentum", "carry"}


def test_every_agent_signal_in_range(panel, params):
    sym = "EURUSD"
    ctx = PairContext(get_pair(sym))
    for agent in default_agents():
        sig = agent.generate(panel[sym], ctx, params)
        assert sig.between(-1.0, 1.0).all(), f"{agent.name} out of range"
        assert not sig.isna().any()


def test_agent_causality(panel, params):
    """A perturbed future bar must not change an agent's earlier signals."""
    sym = "EURUSD"
    ctx = PairContext(get_pair(sym))
    agent = agents.TrendAgent()
    base = agent.generate(panel[sym], ctx, params)
    k = len(panel[sym]) // 2
    spiked = panel[sym].copy()
    spiked.iloc[k, spiked.columns.get_loc("close")] *= 1.2
    after = agent.generate(spiked, ctx, params)
    pd.testing.assert_series_equal(base.iloc[:k], after.iloc[:k])


def test_pool_parallel_equals_sequential(panel, params):
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    seq = AgentPool(max_workers=1).evaluate(panel, contexts, params)
    par = AgentPool(max_workers=4).evaluate(panel, contexts, params)
    assert set(seq) == set(par)
    for sym in seq:
        pd.testing.assert_frame_equal(seq[sym], par[sym])


def test_carry_agent_is_constant(panel, params):
    sym = "USDJPY"
    sig = agents.CarryAgent().generate(panel[sym], PairContext(get_pair(sym)), params)
    assert sig.nunique() == 1          # static tilt
    assert sig.iloc[0] > 0             # USDJPY long earns carry
