import random

from trading_algo.forex import genome as gm
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


def _rng():
    return random.Random(42)


def test_archetypes_are_the_four_per_symbol_families():
    assert gm.ARCHETYPES == ("trend", "breakout", "meanrev", "momentum")


def test_random_genome_respects_bounds_and_orders_windows():
    rng = _rng()
    for _ in range(200):
        g = gm.random_genome(rng)
        assert g.archetype in gm.ARCHETYPES
        lo, hi = gm.GENE_BOUNDS["fast"]
        assert lo <= g.fast <= hi
        assert g.slow > g.fast                      # slow strictly longer
        assert gm.GENE_BOUNDS["window"][0] <= g.window <= gm.GENE_BOUNDS["window"][1]
        assert gm.GENE_BOUNDS["z"][0] <= g.z <= gm.GENE_BOUNDS["z"][1]
        assert isinstance(g.adx_gate, bool)
        assert all(s in DEFAULT_UNIVERSE for s in g.symbols)
        assert len(set(g.symbols)) == len(g.symbols)  # no dupes


def test_gid_is_stable_and_content_addressed():
    rng = _rng()
    g = gm.random_genome(rng)
    assert g.gid == g.gid and len(g.gid) == 10
    # same genes -> same gid; a changed gene -> different gid
    import dataclasses
    same = dataclasses.replace(g)
    assert same.gid == g.gid
    diff = dataclasses.replace(g, window=g.window + 1)
    assert diff.gid != g.gid


def test_random_genome_is_deterministic_under_seed():
    a = [gm.random_genome(random.Random(7)) for _ in range(3)]
    b = [gm.random_genome(random.Random(7)) for _ in range(3)]
    assert [x.gid for x in a] == [x.gid for x in b]


def test_mutate_changes_at_least_one_gene_and_stays_valid():
    rng = _rng()
    g = gm.random_genome(rng)
    m = gm.mutate(g, rng, rate=1.0)
    assert m.gid != g.gid
    assert m.slow > m.fast
    assert m.archetype in gm.ARCHETYPES


def test_crossover_inherits_only_parent_genes():
    rng = _rng()
    a, b = gm.random_genome(rng), gm.random_genome(rng)
    c = gm.crossover(a, b, rng)
    assert c.archetype in (a.archetype, b.archetype)
    assert c.window in (a.window, b.window)
    assert c.slow > c.fast


def test_describe_is_archetype_aware_and_readable():
    g = gm.Genome("breakout", fast=10, slow=50, window=34, z=2.0,
                  atr_window=14, adx_min=22.0, adx_gate=True, symbols=("BTCUSD",))
    d = g.describe()
    assert "breakout" in d and "34" in d and "adx" in d.lower() and "BTCUSD" in d
