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

import random
from dataclasses import dataclass, field

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
        if len(joined) > 5 and joined.iloc[:, 0].std() > 0 and joined.iloc[:, 1].std() > 0:
            c = float(np.corrcoef(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1])
            corr_pen = max(0.0, c)                              # only penalise positive overlap

    turn_pen = _turnover(genome, panel, p)
    score = consistency - lambda_corr * corr_pen - lambda_turn * turn_pen
    sr_pp = validation.sharpe_ratio(r.values)
    sharpe_ann = float(sr_pp * np.sqrt(periods_per_year(r.index)))
    return FitnessResult(genome.gid, round(score, 6), round(sharpe_ann, 4),
                         round(float(sr_pp), 6), len(r))


@dataclass
class EvolutionLog:
    generations: list[dict] = field(default_factory=list)
    registry: dict[str, dict] = field(default_factory=dict)   # gid -> {dna, describe, parents, fitness, sharpe_pp, born_gen}
    n_trials: int = 0
    finalists: list[str] = field(default_factory=list)        # best-first gids (OUTSIDE registry)
    holdout_frac: float = 0.25                                # the split the gate must reuse

    def to_dict(self) -> dict:
        return {"generations": self.generations, "registry": self.registry,
                "n_trials": self.n_trials, "finalists": self.finalists,
                "holdout_frac": self.holdout_frac}

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionLog":
        return cls(generations=list(d.get("generations", [])),
                   registry=dict(d.get("registry", {})),
                   n_trials=int(d.get("n_trials", 0)),
                   finalists=list(d.get("finalists", [])),
                   holdout_frac=float(d.get("holdout_frac", 0.25)))


def _dna(g: gm.Genome) -> dict:
    return {"archetype": g.archetype, "fast": g.fast, "slow": g.slow,
            "window": g.window, "z": g.z, "atr_window": g.atr_window,
            "adx_min": g.adx_min, "adx_gate": g.adx_gate,
            "symbols": list(g.symbols)}


def genome_from_dna(d: dict) -> gm.Genome:
    return gm.Genome(d["archetype"], int(d["fast"]), int(d["slow"]), int(d["window"]),
                     float(d["z"]), int(d["atr_window"]), float(d["adx_min"]),
                     bool(d["adx_gate"]), tuple(d["symbols"]))


def _score_population(pop, panel, p, *, folds, base, lambda_corr, lambda_turn):
    scored = [(g, fitness(g, panel, p, folds=folds, base_returns=base,
                          lambda_corr=lambda_corr, lambda_turn=lambda_turn)) for g in pop]
    scored.sort(key=lambda gf: (-gf[1].score, gf[0].gid))       # gid tiebreak = determinism
    return scored


def breed(panel: dict, p, *, generations: int, pop_size: int, seed: int,
          holdout_frac: float = 0.25, folds: int = 4, elite_frac: float = 0.2,
          lambda_corr: float = 1.0, lambda_turn: float = 0.1):
    rng = random.Random(seed)
    breed_panel, holdout_panel = split_history(panel, holdout_frac)
    base = roster_returns(breed_panel, p)
    log = EvolutionLog()
    seen: set[str] = set()

    def register(g, gen, parents):
        if g.gid not in log.registry:
            log.registry[g.gid] = {"dna": _dna(g), "describe": g.describe(),
                                   "parents": parents, "born_gen": gen}
        seen.add(g.gid)

    population = [gm.random_genome(rng) for _ in range(pop_size)]
    for g in population:
        register(g, 0, [])

    n_elite = max(1, int(pop_size * elite_frac))
    scored = _score_population(population, breed_panel, p, folds=folds, base=base,
                               lambda_corr=lambda_corr, lambda_turn=lambda_turn)

    for gen in range(generations):
        prev_gids = {g.gid for g, _ in scored}
        for g, fr in scored:
            log.registry[g.gid]["fitness"] = fr.score
            log.registry[g.gid]["sharpe_pp"] = fr.sharpe_pp
        best, med = scored[0][1].score, float(np.median([fr.score for _, fr in scored]))
        elite = [g for g, _ in scored[:n_elite]]
        # tournament selection over the top half
        pool_ = [g for g, _ in scored[: max(2, pop_size // 2)]]
        offspring = []
        parentage: dict[str, list[str]] = {}
        while len(offspring) < pop_size - n_elite:
            a, b = rng.choice(pool_), rng.choice(pool_)
            child = gm.mutate(gm.crossover(a, b, rng), rng)
            parentage.setdefault(child.gid, [a.gid, b.gid])
            offspring.append(child)
        population = elite + offspring
        births = sum(1 for g in population if g.gid not in prev_gids)
        deaths = sum(1 for gid in prev_gids if gid not in {g.gid for g in population})
        log.generations.append({"gen": gen, "best": round(best, 6),
                                 "median": round(med, 6), "births": births,
                                 "deaths": deaths, "best_gid": scored[0][0].gid})
        for g in population:
            register(g, gen + 1, parentage.get(g.gid, []))
        scored = _score_population(population, breed_panel, p, folds=folds, base=base,
                                   lambda_corr=lambda_corr, lambda_turn=lambda_turn)

    for g, fr in scored:                                       # record the final generation
        log.registry[g.gid]["fitness"] = fr.score
        log.registry[g.gid]["sharpe_pp"] = fr.sharpe_pp
    log.finalists = [g.gid for g, _ in scored]                # best-first list, NOT in registry
    log.holdout_frac = holdout_frac
    log.n_trials = len(seen)
    return log, holdout_panel, scored
