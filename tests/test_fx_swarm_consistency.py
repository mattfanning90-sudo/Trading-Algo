import random

import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import genome as gm
from trading_algo.forex.agents import AgentPool, PairContext
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE, get_pair


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE[:3], start="2018-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def _some_genome(archetype):
    return gm.Genome(archetype, fast=12, slow=60, window=30, z=2.0,
                     atr_window=14, adx_min=20.0, adx_gate=False, symbols=())


@pytest.mark.parametrize("archetype", gm.ARCHETYPES)
def test_champion_signal_in_range_and_no_nan(panel, params, archetype):
    g = _some_genome(archetype)
    agent = g.to_agent()
    for sym in panel:
        sig = agent.generate(panel[sym], PairContext(get_pair(sym)), params)
        assert sig.between(-1.0, 1.0).all(), f"{archetype} out of range"
        assert not sig.isna().any()
        assert sig.index.equals(panel[sym].index)


def test_champion_respects_symbol_subset(panel, params):
    g = gm.Genome("trend", fast=12, slow=60, window=30, z=2.0, atr_window=14,
                  adx_min=20.0, adx_gate=False, symbols=("EURUSD",))
    agent = g.to_agent()
    other = [s for s in panel if s != "EURUSD"][0]
    off = agent.generate(panel[other], PairContext(get_pair(other)), params)
    assert (off == 0.0).all()                      # not in its universe -> flat


def test_champion_is_causal(panel, params):
    g = _some_genome("trend")
    agent = g.to_agent()
    sym = "EURUSD"
    ctx = PairContext(get_pair(sym))
    base = agent.generate(panel[sym], ctx, params)
    k = len(panel[sym]) // 2
    spiked = panel[sym].copy()
    spiked.iloc[k, spiked.columns.get_loc("close")] *= 1.2
    after = agent.generate(spiked, ctx, params)
    pd.testing.assert_series_equal(base.iloc[:k], after.iloc[:k])


def test_signal_panel_equals_pool_evaluation(panel, params):
    """Invariant #3: signal_panel (breeder path) == AgentPool (live path) exactly."""
    g = _some_genome("momentum")
    sp = gm.signal_panel(g, panel, params)                       # breeder path
    pool = AgentPool([g.to_agent()], max_workers=1)              # live path
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    ev = pool.evaluate(panel, contexts, params)
    for sym in panel:
        live = ev[sym][g.to_agent().name]
        np.testing.assert_allclose(sp[sym].values, live.values, rtol=1e-9, atol=1e-12)
