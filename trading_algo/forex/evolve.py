"""The offline breeder: fitness + the breed→score→cull loop + evolution log.

Fitness is out-of-the-search-window honest in two ways: (1) each genome is scored
by the MEAN-minus-STD of its cost-aware per-period Sharpe across K sequential
folds of the breeding window — rewarding edges that persist across sub-periods,
not one lucky stretch; (2) a decorrelation penalty pushes the population away from
the existing roster so the swarm breeds weakly-correlated members (what the
ensemble rewards). The FINAL out-of-sample judgement (DSR/PBO on an untouched
hold-out) is the promotion gate's job (champions.py), not fitness's.

Because grammar genomes are RULE-BASED (no fitted parameters), there is no
per-fold model retraining to purge/embargo — OOS integrity comes from the
held-out slice the search never sees. That is why this module does not use the
purged-walk-forward machinery in `walkforward.py` (which exists for fitted ML
models).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import genome as gm
from . import validation
from .agents import AgentPool, PairContext, default_agents
from .ensemble import ensemble_tilts
from .fx_data import closes
from .marks import periods_per_year
from .ml_backtest import strategy_returns
from .pairs import get_pair


def split_history(panel: dict, holdout_frac: float) -> tuple[dict, dict]:
    """Split every symbol's frame at the same time boundary (breed | hold-out)."""
    idx = closes(panel).index
    cut = idx[int(len(idx) * (1.0 - holdout_frac))]
    breed = {s: df.loc[df.index < cut] for s, df in panel.items()}
    hold = {s: df.loc[df.index >= cut] for s, df in panel.items()}
    return breed, hold


def genome_returns(genome: gm.Genome, panel: dict, p) -> pd.Series:
    """Net-of-cost per-period returns of one genome's book (equal-weight, half-spread)."""
    sig = gm.signal_panel(genome, panel, p)
    return strategy_returns(panel, sig, p).dropna()


def roster_returns(panel: dict, p) -> pd.Series:
    """The default 5-agent ensemble's net returns — the decorrelation baseline."""
    pool = AgentPool(default_agents(), max_workers=1)
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    signals = pool.evaluate(panel, contexts, p)
    rets = closes(panel).pct_change(fill_method=None)
    tilts = ensemble_tilts(signals, rets, p)
    return strategy_returns(panel, tilts, p).dropna()


@dataclass(frozen=True)
class FitnessResult:
    gid: str
    score: float
    sharpe_ann: float
    sharpe_pp: float          # per-period Sharpe on the breed window (feeds DSR sr_variance)
    n_bars: int


def _turnover(genome: gm.Genome, panel: dict, p) -> float:
    sig = gm.signal_panel(genome, panel, p).reindex(columns=list(panel))
    return float(sig.shift(1).diff().abs().mean().mean())      # mean |Δsignal| per bar


def fitness(genome: gm.Genome, panel: dict, p, *, folds: int,
            base_returns: pd.Series | None, lambda_corr: float,
            lambda_turn: float) -> FitnessResult:
    r = genome_returns(genome, panel, p)
    if len(r) < folds * 5:
        return FitnessResult(genome.gid, -1e9, 0.0, 0.0, len(r))

    chunks = [c for c in np.array_split(r.values, folds) if len(c) >= 5]
    fold_sr = np.array([validation.sharpe_ratio(c) for c in chunks])
    consistency = float(fold_sr.mean() - fold_sr.std())         # reward stable edges

    corr_pen = 0.0
    if base_returns is not None:
        joined = pd.concat([r, base_returns], axis=1, join="inner").dropna()
        if len(joined) > 5 and joined.iloc[:, 1].std() > 0:
            c = float(np.corrcoef(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1])
            corr_pen = max(0.0, c)                              # only penalise positive overlap

    turn_pen = _turnover(genome, panel, p)
    score = consistency - lambda_corr * corr_pen - lambda_turn * turn_pen
    sr_pp = validation.sharpe_ratio(r.values)
    sharpe_ann = float(sr_pp * np.sqrt(periods_per_year(r.index)))
    return FitnessResult(genome.gid, round(score, 6), round(sharpe_ann, 4),
                         round(float(sr_pp), 6), len(r))
