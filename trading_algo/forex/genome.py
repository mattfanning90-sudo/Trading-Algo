"""The swarm genome: grammar-constrained "strategy DNA".

A `Genome` is a fixed-shape chromosome — an archetype (one of four per-symbol
rule families), the evolvable indicator windows/thresholds it uses, an optional
ADX regime gate, and the instrument subset it is allowed to trade. It is bounded
so every phenotype is causal-by-construction and human-readable, yet the space
is millions of combinations. `to_agent()` (Task 2) turns a genome into an object
satisfying the existing `Agent.generate` contract, so a bred agent is a drop-in
for `AgentPool` (invariant #3).

v1 deliberately excludes `carry` (static — nothing to evolve) and `xsection`
(needs a cross-sectional interface the per-symbol Agent can't express). Free-form
tree-GP is v2 behind this same module's `to_agent()` surface.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from .pairs import DEFAULT_UNIVERSE

ARCHETYPES: tuple[str, ...] = ("trend", "breakout", "meanrev", "momentum")

# (lo, hi) inclusive bounds for each numeric gene. Ranges are wide enough to span
# fast/slow regimes without producing degenerate (self-peeking or 1-bar) windows.
GENE_BOUNDS: dict[str, tuple] = {
    "fast": (5, 50),
    "slow": (20, 200),
    "window": (10, 120),
    "z": (0.5, 3.0),
    "atr_window": (7, 30),
    "adx_min": (10.0, 35.0),
}


@dataclass(frozen=True)
class Genome:
    archetype: str
    fast: int
    slow: int
    window: int
    z: float
    atr_window: int
    adx_min: float
    adx_gate: bool
    symbols: tuple[str, ...]      # () == trade every symbol

    @property
    def gid(self) -> str:
        """Stable, content-addressed 10-hex id (lineage key)."""
        key = (f"{self.archetype}|{self.fast}|{self.slow}|{self.window}|"
               f"{self.z:.4f}|{self.atr_window}|{self.adx_min:.4f}|"
               f"{int(self.adx_gate)}|{','.join(self.symbols)}")
        return hashlib.sha1(key.encode()).hexdigest()[:10]

    def describe(self) -> str:
        """Plain-English label for the dashboard roster table."""
        gate = f" · adx>{self.adx_min:.0f}" if self.adx_gate else ""
        scope = " · " + ",".join(self.symbols) if self.symbols else " · all"
        body = {
            "trend": f"trend · ema{self.fast}/{self.slow} · atr{self.atr_window}",
            "breakout": f"breakout · donchian{self.window}",
            "meanrev": f"meanrev · bb{self.window}·z{self.z:.1f}",
            "momentum": f"momentum · roc{self.window}·vol{self.fast}",
        }[self.archetype]
        return body + gate + scope


def _randint(rng: random.Random, gene: str) -> int:
    lo, hi = GENE_BOUNDS[gene]
    return rng.randint(int(lo), int(hi))


def _randfloat(rng: random.Random, gene: str) -> float:
    lo, hi = GENE_BOUNDS[gene]
    return round(rng.uniform(lo, hi), 4)


def _random_symbols(rng: random.Random) -> tuple[str, ...]:
    # 50% trade-all; otherwise a 1-3 symbol specialist subset (deterministic order).
    if rng.random() < 0.5:
        return ()
    k = rng.randint(1, 3)
    picks = rng.sample(DEFAULT_UNIVERSE, min(k, len(DEFAULT_UNIVERSE)))
    return tuple(s for s in DEFAULT_UNIVERSE if s in picks)   # canonical order


def random_genome(rng: random.Random) -> Genome:
    fast = _randint(rng, "fast")
    slow = max(_randint(rng, "slow"), fast + 5)               # slow strictly longer
    return Genome(
        archetype=rng.choice(ARCHETYPES),
        fast=fast, slow=slow,
        window=_randint(rng, "window"),
        z=_randfloat(rng, "z"),
        atr_window=_randint(rng, "atr_window"),
        adx_min=_randfloat(rng, "adx_min"),
        adx_gate=rng.random() < 0.5,
        symbols=_random_symbols(rng),
    )


def mutate(g: Genome, rng: random.Random, rate: float = 0.3) -> Genome:
    """Perturb each gene independently with probability `rate`. Guarantees a change
    via a BOUNDED retry loop (no unbounded recursion)."""
    import dataclasses
    for _ in range(8):
        ref = random_genome(rng)                              # source of fresh gene values
        fields = {
            "archetype": ref.archetype, "fast": ref.fast, "slow": ref.slow,
            "window": ref.window, "z": ref.z, "atr_window": ref.atr_window,
            "adx_min": ref.adx_min, "adx_gate": ref.adx_gate, "symbols": ref.symbols,
        }
        changes = {k: v for k, v in fields.items() if rng.random() < rate}
        if not changes:                                       # force at least one
            k = rng.choice(list(fields))
            changes = {k: fields[k]}
        out = dataclasses.replace(g, **changes)
        out = dataclasses.replace(out, slow=max(out.slow, out.fast + 5))
        if out.gid != g.gid:
            return out
    other = [a for a in ARCHETYPES if a != g.archetype]       # guaranteed-different fallback
    return dataclasses.replace(g, archetype=rng.choice(other))


def crossover(a: Genome, b: Genome, rng: random.Random) -> Genome:
    import dataclasses
    pick = lambda x, y: x if rng.random() < 0.5 else y
    child = dataclasses.replace(
        a,
        archetype=pick(a.archetype, b.archetype),
        fast=pick(a.fast, b.fast), slow=pick(a.slow, b.slow),
        window=pick(a.window, b.window), z=pick(a.z, b.z),
        atr_window=pick(a.atr_window, b.atr_window),
        adx_min=pick(a.adx_min, b.adx_min), adx_gate=pick(a.adx_gate, b.adx_gate),
        symbols=pick(a.symbols, b.symbols),
    )
    return dataclasses.replace(child, slow=max(child.slow, child.fast + 5))
