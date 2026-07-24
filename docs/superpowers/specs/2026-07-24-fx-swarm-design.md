# FX Swarm — Evolutionary Agent Population (Design)

**Status:** Design agreed, pending implementation plan
**Date:** 2026-07-24
**Scope:** FX subsystem only (`trading_algo/forex/`). No change to the equity sleeves.

## 1. What this is

An **evolutionary population of trading agents** for the FX subsystem. Instead of
the five hand-written technical agents, a *population* of genomes is bred, scored,
and culled **offline over history**. Survivors that clear hard out-of-sample gates
are **auto-promoted** into the live FX roster, where the existing adaptive/hedge
ensemble ([`ensemble.py`](../../../trading_algo/forex/ensemble.py)) does its normal
per-bar up/down-weighting. The hand-written five remain as a stable core.

"Swarm" here means **population dynamics** — agents are born, compete, and die —
not agent-to-agent messaging and not an LLM crew.

## 2. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Meaning of "swarm" | Evolutionary population (born / scored / culled) |
| D2 | Genome (what varies) | Genetic programming — freely composed strategies |
| D3 | v1 genome radicalness | **Grammar-constrained "strategy DNA"** first; free-form tree-GP is v2 behind the same harness |
| D4 | Evolution loop location | **Offline breeder**, promote survivors (not online in the book) |
| D5 | Fitness | Out-of-sample, cost-aware, via purged walk-forward |
| D6 | Promotion to live | **Auto-promote on a schedule**, fenced by hard OOS gates + rotation cap |
| D7 | Diversity | Decorrelation-from-roster term folded into fitness |
| D8 | Dashboard | Dedicated **SWARM tab** |
| D9 | Animations | **B (lineage tree)** + **C (style-space murmuration)**, over real data |

## 3. Architecture

Everything new is additive and isolated. A bred agent is a **drop-in for the
existing `Agent` interface**, so it flows through the unchanged
signal → ensemble → `compute_targets` path (preserves invariants #1 and #3).

### New modules

- **`forex/genome.py`** — the genome and its expression.
  - `Genome` dataclass = the grammar-constrained DNA (see §4).
  - `Genome.to_agent() -> Agent` returns an object satisfying the existing
    `Agent.generate(bars, ctx, p) -> pd.Series in [-1, 1]` contract, so
    `AgentPool` evaluates bred agents exactly like the hand-written five.
  - Random init, mutation, and crossover operators live here (pure functions on
    `Genome`), plus a deterministic `Genome.describe()` → the plain-English
    label the dashboard shows.

- **`forex/evolve.py`** — the offline breeder.
  - Loop: initialise population → **evaluate fitness** (purged walk-forward,
    costs on) → tournament-select → crossover + mutate → cull → repeat for N
    generations.
  - Writes an **evolution log** (`state/swarm_log_{account}.json`): per-generation
    best/median/death-rate, births, deaths, promotions, and every genome ever
    evaluated with its scores.
  - CLI: `python -m trading_algo.forex.evolve --account matt [--synthetic]`.

- **`forex/champions.py`** — the promotion gate + live roster file.
  - Applies **DSR / PBO on an untouched hold-out** slice, enforces the
    **rotation cap** and **stable-core floor**, and writes
    `state/champions_{account}.json` (the file the engine reads to build the live
    roster).
  - `load_champions(account) -> list[Agent]` used by the engine/paper layer to
    extend the default roster.

### Data flow

```
history ──▶ evolve.py ──(purged WF, costs)──▶ fitness per genome
                │                                   │
                │                          swarm_log_{account}.json  ─▶ dashboard SWARM tab
                ▼
        survivors ──▶ champions.py ──(hold-out DSR/PBO + rotation cap)──▶ champions_{account}.json
                                                                              │
engine --once ──▶ default 5 agents + load_champions() ──▶ AgentPool ──▶ ensemble ──▶ compute_targets ──▶ book
```

## 4. The genome (v1: grammar-constrained "strategy DNA")

Fixed-shape chromosome — bounded so every phenotype is **causal-by-construction**
and human-readable, yet the space is millions of combinations:

- **archetype** ∈ {trend, breakout, meanrev, momentum, xsection}
- **params** — the evolvable numerics for that archetype (e.g. `ema_fast`,
  `ema_slow`, `atr_window`, `donchian_window`, `bb_window`, `bb_z`, `rsi_window`,
  `roc_window`) drawn from bounded ranges.
- **regime gate** — optional `adx` trend/range gate and/or a volatility filter
  (on/off + threshold).
- **universe subset** — the instruments this agent is allowed to trade (e.g.
  crypto-only, majors-only, a single pair).

All primitives are the existing causal indicators in
[`indicators.py`](../../../trading_algo/forex/indicators.py). `to_agent()` composes
them the same way the hand-written agents do.

> **v2 (not now):** replace `Genome` internals with a free-form typed expression
> tree over the same primitives. The breeder, gates, persistence, and dashboard
> are unchanged — only `genome.py` grows a second representation behind the same
> `to_agent()` / mutation / crossover surface.

## 5. Overfitting backbone (non-negotiable)

This is the heart of the design, because free-ish search over strategies is an
overfitting machine. Guardrails:

1. **Fitness is out-of-sample only** — every genome is scored with
   `walk_forward.walk_forward_predict`-style purged + embargoed folds
   ([`walkforward.py`](../../../trading_algo/forex/walkforward.py)); never
   in-sample.
2. **Honest N for the Deflated Sharpe** — DSR's trial count `N` =
   **every genome the breeder ever evaluated**, not just survivors. Undercounting
   trials makes DSR a lie. [`research.py`](../../../trading_algo/forex/research.py)
   already deflates by candidate count; the breeder feeds it a bigger, honest `N`.
3. **Untouched hold-out** — a final time slice the *evolution never sees*; the
   promotion gate ([`champions.py`](../../../trading_algo/forex/champions.py)) runs
   DSR/PBO there.
4. **Parsimony pressure** — a complexity penalty in fitness fights bloat.
5. **Decorrelation pressure (D7)** — a penalty for correlation to the current
   roster's signals, so the swarm breeds weakly-correlated members (what the
   ensemble rewards) rather than 40 clones of one trend edge.
6. **Costs always on** (invariant #2) — fitness uses
   `ml_backtest.strategy_returns` (half-spread per unit turnover).

## 6. Auto-promotion, fenced (D6)

A scheduled breeder run (new GitHub Action, mirroring `fx-paper.yml`) re-evolves
and rewrites `champions_{account}.json` — but the fences make one bad generation
survivable:

- Only genomes past the **hold-out DSR/PBO gate** may enter.
- **Rotation cap** — at most K swaps per cycle (e.g. 2).
- **Stable-core floor** — the hand-written five (or a minimum count) always
  remain, so the book can't be gutted.
- **Kill-switch** — a config flag disables auto-rotation and freezes the roster.
- It is **paper capital**, so the stakes are learning quality, not money.

## 7. Persistence (new state files)

- `state/swarm_log_{account}.json` — evolution history: per generation
  {best, median, deaths, births}, and a genome registry {id, dna, describe(),
  parents, fitness, gate scores, promoted_at}. Powers dashboard panels B and the
  roster/rotation tables.
- `state/champions_{account}.json` — the current live roster: list of promoted
  genome ids + their DNA + promotion metadata (DSR, PBO, N, generation).

Both follow the repo pattern (tracked JSON, committed back by the Action; the
repo is the ledger).

## 8. Dashboard — new SWARM tab

Fifth tab beside OVERVIEW / POSITIONS / BACKTEST / METHOD. All animations are
pure vanilla-JS canvas (zero-dependency, matching the existing dashboard) drawn
over **real** data from the two state files.

1. **Hero — C, style-space murmuration (live).** Current population plotted on a
   style map (trend↔mean-rev × fast↔slow), migrating toward the fitness hotspot —
   selection pressure made visible. Colour = archetype, size = ensemble weight,
   brightness = fitness.
2. **B — lineage tree (evolution story).** Ancestry over generations: branches
   sprout for births, wither grey for deaths (the visible graveyard), survivors
   glow at the frontier.
3. **Champion roster table.** One row per live agent: `Genome.describe()` in plain
   English, age (generations survived), **promotion DSR / PBO / N**, current
   ensemble weight.
4. **Rotation timeline.** Promotions in / retirements out over time — the audit
   trail of the book's changing DNA.
5. **Diversity heatmap.** Correlation among live champions' signals — proof the
   swarm is diverse, not cloned.
6. **Swarm vs. benchmarks.** Equity overlay: swarm roster vs. fixed hand-written
   ensemble vs. buy&hold — did evolution beat the roster we already had?

The existing **agent scorecard** and **attribution** generalise from "5 fixed
agents" to "current champions" with minimal change (their column set becomes the
live roster).

### Survivorship honesty on the fitness curve

Generational fitness is plotted **best AND median AND death-rate**, on the
**hold-out**, never in-sample — otherwise a survivors-only in-sample curve always
rises (a lie). Same discipline the equity side already applies with
point-in-time membership ([`constituents.py`](../../../trading_algo/constituents.py)).

## 9. Invariants preserved

- **#1 No lookahead** — genomes use only causal primitives; fitness is purged
  walk-forward; live signals at t use data ≤ t, trade t+1.
- **#2 Costs always on** — fitness and dashboard figures are net of spread (and
  labelled where gross, matching existing scorecard notes).
- **#3 One weight function** — bred agents emit `[-1,1]` signals into the same
  `AgentPool` → `ensemble` → `strategy.compute_targets` path; no second weight
  engine.
- **#4 Whole shares / commission floor** — unchanged; sizing still happens in the
  book layer.
- **#6 Local currency per sleeve** — FX-only; no cross-currency mixing added.

## 10. Non-goals (YAGNI)

- **Free-form tree-GP** — deferred to v2 behind the same harness (§4).
- **Equity sleeves** — they use a single momentum score, not an agent ensemble;
  the swarm is FX-only for now.
- **Online (in-book) breeding** — the loop runs offline; the book only *reads*
  the champions file.
- **Agent-to-agent messaging / LLM agents** — out of scope; "swarm" = population
  dynamics only.

## 11. Risks & open questions

- **R1 Compute** — fitness over a large population × purged folds is the cost
  centre. Mitigation: cap population/generations, reuse `AgentPool`'s thread
  parallelism, vectorised fitness.
- **R2 Overfitting despite gates** — the honest expected outcome (per
  `research.py`) is that *few or no* genomes clear the bar. That's a feature: the
  gate saying "nothing promoted this cycle" is a valid, healthy result.
- **R3 Rotation churn** — auto-rotation could thrash; the rotation cap + stable
  core bound it. Tune K conservatively.
- **Q1** — default population size, generation count, hold-out length? (Plan-time.)
- **Q2** — breeder cadence (weekly? monthly)? Start monthly, matching the
  strategy's slow horizon.

## 12. Testing strategy

- `genome.py` — mutation/crossover preserve validity; `to_agent()` output obeys
  the `[-1,1]` contract; `describe()` is stable.
- `evolve.py` — deterministic run under a fixed seed on synthetic data; the log
  schema round-trips; N (trial count) equals genomes evaluated.
- `champions.py` — gate rejects a known-overfit genome; rotation cap and
  stable-core floor are enforced; roster file round-trips.
- **Consistency** — a promoted genome produces identical signals in the
  breeder's evaluation and in the live `AgentPool` path (mirrors
  `tests/test_consistency.py`, invariant #3).
- All offline via `--synthetic` (invariant #5: synthetic = pipeline test only).

## 13. Rough phasing (for the plan)

1. `genome.py` + tests (DNA, operators, `to_agent`, `describe`).
2. `evolve.py` breeder + evolution log + `--synthetic` CLI + tests.
3. `champions.py` gate + roster file + tests; wire `load_champions` into the
   engine/paper roster.
4. Scheduled breeder GitHub Action (mirror `fx-paper.yml`).
5. Dashboard SWARM tab: data plumbing, then panels 3–6 (static), then animations
   B + C.

> Follow-up: a formal acceptance-criteria spec can be captured with this repo's
> own `/spec` system (`docs/specs/`) once the plan is written.
