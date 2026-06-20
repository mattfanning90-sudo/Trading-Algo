"""Low-latency multi-agent FX trading subsystem.

A parallel ecosystem of technical strategy *agents* (trend, breakout,
mean-reversion, momentum, carry) whose signals are blended by a performance-
weighted ensemble, sized by a volatility-targeting risk layer, and traded across
isolated multi-account paper books — backtest and live paper share one weight
function (`fx_strategy.compute_targets`).

See `trading_algo/forex/README.md` for the full tour.
"""
from __future__ import annotations

from .agents import (
    AgentPool,
    BreakoutAgent,
    CarryAgent,
    MeanReversionAgent,
    MomentumAgent,
    TrendAgent,
    default_agents,
)
from .fx_config import FXParams, profile, profile_names
from .fx_strategy import compute_targets, target_weights_history
from .ml_agent import MetaLabeler, ModelBundle, NeuralAgent, default_neural_agents
from .nn import MLP
from .pairs import DEFAULT_UNIVERSE, PAIRS, get_pair

__all__ = [
    "AgentPool", "TrendAgent", "BreakoutAgent", "MeanReversionAgent",
    "MomentumAgent", "CarryAgent", "default_agents",
    "FXParams", "profile", "profile_names",
    "compute_targets", "target_weights_history",
    "PAIRS", "DEFAULT_UNIVERSE", "get_pair",
    # deep-learning layer
    "MLP", "NeuralAgent", "MetaLabeler", "ModelBundle", "default_neural_agents",
]
