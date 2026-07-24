import pytest

from trading_algo.forex import evolve
from trading_algo.forex import genome as gm
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE[:4], start="2016-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def _g(archetype="trend"):
    return gm.Genome(archetype, fast=12, slow=60, window=30, z=2.0, atr_window=14,
                     adx_min=20.0, adx_gate=False, symbols=())


def test_split_history_is_disjoint_and_ordered(panel):
    breed, hold = evolve.split_history(panel, holdout_frac=0.25)
    sym = next(iter(panel))
    assert breed[sym].index.max() < hold[sym].index.min()          # no overlap in time
    total = len(panel[sym])
    assert abs(len(hold[sym]) / total - 0.25) < 0.05


def test_genome_returns_are_cost_netted_series(panel, params):
    r = evolve.genome_returns(_g(), panel, params)
    assert len(r) > 100 and not r.isna().any()


def test_fitness_is_deterministic(panel, params):
    base = evolve.roster_returns(panel, params)
    a = evolve.fitness(_g(), panel, params, folds=4, base_returns=base,
                       lambda_corr=1.0, lambda_turn=0.1)
    b = evolve.fitness(_g(), panel, params, folds=4, base_returns=base,
                       lambda_corr=1.0, lambda_turn=0.1)
    assert a.score == b.score and a.gid == b.gid == _g().gid


def test_decorrelation_penalty_lowers_score_for_a_clone(panel, params):
    """A genome scored against ITS OWN returns as the baseline is penalised more
    than the same genome scored against an uncorrelated baseline."""
    g = _g("momentum")
    own = evolve.genome_returns(g, panel, params)
    noise = own.sample(frac=1.0, random_state=0).reset_index(drop=True)
    noise.index = own.index                                   # shuffle -> decorrelated
    penalised = evolve.fitness(g, panel, params, folds=4, base_returns=own,
                               lambda_corr=2.0, lambda_turn=0.0)
    free = evolve.fitness(g, panel, params, folds=4, base_returns=noise,
                          lambda_corr=2.0, lambda_turn=0.0)
    assert penalised.score < free.score


def test_breed_is_deterministic_and_counts_every_trial(panel, params):
    log1, hold1, final1 = evolve.breed(panel, params, generations=3, pop_size=8, seed=1)
    log2, hold2, final2 = evolve.breed(panel, params, generations=3, pop_size=8, seed=1)
    assert [g.gid for g, _ in final1] == [g.gid for g, _ in final2]      # reproducible
    assert log1.n_trials == log2.n_trials
    assert log1.n_trials == len(log1.registry)                          # N = distinct genomes
    assert log1.n_trials >= 8                                            # at least the first gen


def test_breed_log_records_per_generation_stats(panel, params):
    log, _, _ = evolve.breed(panel, params, generations=3, pop_size=8, seed=2)
    assert len(log.generations) == 3
    g0 = log.generations[0]
    assert set(g0) >= {"gen", "best", "median", "deaths", "births", "best_gid"}
    assert g0["best"] >= g0["median"]


def test_breed_log_round_trips(panel, params):
    log, _, _ = evolve.breed(panel, params, generations=2, pop_size=6, seed=3)
    back = evolve.EvolutionLog.from_dict(log.to_dict())
    assert back.n_trials == log.n_trials
    assert back.generations == log.generations
    assert set(back.registry) == set(log.registry)
    assert back.finalists == log.finalists
    assert back.holdout_frac == log.holdout_frac


def test_breed_final_population_is_sorted_best_first(panel, params):
    _, _, final = evolve.breed(panel, params, generations=2, pop_size=8, seed=4)
    scores = [fr.score for _, fr in final]
    assert scores == sorted(scores, reverse=True)
