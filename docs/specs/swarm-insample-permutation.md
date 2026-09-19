---
title: In-sample permutation test for the swarm search
slug: swarm-insample-permutation
status: done
created: 2026-09-19
last-updated: 2026-09-19
owner: matt
---

# swarm-insample-permutation — In-sample permutation test for the swarm search

## Context

`evolve.breed` is a genuine search: 12 generations × 40 population, 424 distinct
genomes in the current registry snapshot. Keeping the best of 424 tries makes the
best look good even when none are good — the "luckiest of 424 coin-flippers"
problem, known as **selection bias**.

`champions.gate` corrects for this with the **Deflated Sharpe Ratio** (DSR), which
discounts a Sharpe by how many strategies were tried first. DSR needs a scalar
`n_trials` meaning the number of *independent* trials. `docs/MONTE_CARLO_RESEARCH.md`
§3 measured three accepted estimators of that number and found they **disagree by
40×**, with no measurement available to adjudicate between them. §4 concluded a
bootstrap SPA test was the right long-term replacement because it answers the
question without requiring a guess.

An **in-sample permutation test** is the same escape through a different door: it
re-runs the *entire search* on structureless data and compares best-against-best,
so the advantage gained from searching hard is priced into the null by
construction. No `n_trials` is ever needed.

Two facts make the swarm the right target, both verified 2026-09-19:

1. **The swarm is time-series, not cross-sectional.** `ChampionAgent.generate`
   ([genome.py:177](../../trading_algo/forex/genome.py#L177)) reads only its own
   pair's bars for every archetype. It never ranks symbols against each other. So
   the published bar-permutation null is **valid here unmodified** — the invalidity
   documented in `docs/PERMUTATION_TESTING.md` §4 applies only to the cross-sectional
   equity sleeves.
2. **The equity sleeves have no search to test.** `backtest.py` fits nothing;
   `sweep.py` deliberately declines to select, and nothing in the codebase consumes
   its `best_params`. With no N, step 2 has nothing to bite on there.

Touches: `trading_algo/forex/{evolve,champions,genome,fx_data}.py`,
`docs/PERMUTATION_TESTING.md`, `docs/MONTE_CARLO_RESEARCH.md` §3–4.

## Goals

- **G-1**: Produce a p-value for "the best genome bred on real data beats the best
  genome the *same search* finds on structureless data", with the search cost
  priced in by construction.
- **G-2**: Remove this verdict's dependence on a guessed `n_trials`, closing the
  40× ambiguity for the one question it most affects.
- **G-3**: Report that p-value alongside the existing DSR/PBO verdict without
  changing any promotion decision.
- **G-4**: Ship a reusable, verified OHLC panel permutation for the FX subsystem.

## Non-goals

- **NG-1**: Gating promotion on the p-value. Deliberate — the DSR/PBO verdict must
  be observed against the new one before either overrules the other, per the
  "disagreement means unproven" rule in `MONTE_CARLO_RESEARCH.md` §3.
- **NG-2**: Applying this to the equity sleeves. They run no search, and the null
  is invalid for cross-sectional momentum (`PERMUTATION_TESTING.md` §4).
- **NG-3**: Steps 3 and 4 (walk-forward and walk-forward permutation). Step 4 would
  be the natural follow-on for the FX **ML** layer, which does re-fit on rolling
  windows; the swarm breeder does not.
- **NG-4**: Generating strategies. This is a referee. Widening `research.py` is
  agreed as the *next* piece of work, not this one.

## Acceptance criteria

| ID   | Criterion (observable behaviour) | Verified by | Status |
|------|----------------------------------|-------------|--------|
| AC-1 | `permute_panel` preserves each symbol's total log return, per-bar volatility and every pairwise cross-symbol correlation to within 1e-10 | `pytest tests/test_permtest.py -k preserves` | ☑ |
| AC-2 | `permute_panel` destroys serial structure: \|return\| autocorrelation of a panel built with volatility clustering falls below 0.10 | `pytest tests/test_permtest.py -k destroys` | ☑ |
| AC-3 | With `start_index=k`, bars at positions `< k` are identical to the input | `pytest tests/test_permtest.py -k prefix` | ☑ |
| AC-4 | p-value equals exactly `(k+1)/(m+1)`; equals `1.0` when the real result is strictly worst; never returns 0 | `pytest tests/test_permtest.py -k pvalue` | ☑ |
| AC-5 | CLI writes `state/permtest_{account}.json` containing `p_value`, `n_permutations`, `real_best`, `perm_best`, `seed`, `statistic` | `pytest tests/test_permtest.py -k report` | ☑ |
| AC-6 | `champions.promote` surfaces `perm_pvalue` in its meta when the report exists, and the set of promoted genomes is identical with and without it | `pytest tests/test_permtest.py -k promote` | ☑ |
| AC-7 | The compared statistic is net of costs (invariant #2) | Verified: `evolve.genome_returns` → `strategy_returns` applies half-spread ([evolve.py:50](../../trading_algo/forex/evolve.py#L50)) | ☑ |
| AC-8 | End-to-end run completes offline on synthetic data at reduced budget | `python -m trading_algo.forex.permtest --synthetic --permutations 3 --quick` | ☑ |

## Constraints & invariants

- **Invariant #1 (no lookahead)** — preserved. The permutation rebuilds each bar
  causally from the previous close; it never reorders anything *within* a bar's
  construction, and signal generation is untouched. AC-3 guards the real prefix.
- **Invariant #2 (costs always on)** — preserved and verified (AC-7). The statistic
  is the breed's own net-of-cost return series.
- **Invariant #3 (one weight function)** — untouched. `permtest` computes no target
  weights; it calls the existing breed path only.
- **Invariant #5 (synthetic is a pipeline test)** — `--synthetic` is supported for
  smoke tests and its output is labelled as such; no synthetic p-value is ever
  reported as a verdict.
- **State isolation** — `permtest` must honour `FX_STATE_DIR`. Any `--synthetic`
  run writes only under it (see the standing hazard: synthetic runs clobbering
  live books).
- **Cost** — one breed is ~17s measured (0.036s × 480 evaluations), so 200
  permutations ≈ 1h and 1000 ≈ 4.7h. Offline/CI job, never on a live path.

## Verification plan

```bash
pytest -q tests/test_permtest.py
python -m trading_algo.forex.permtest --synthetic --permutations 3 --quick   # smoke
python -m trading_algo.forex.permtest --account matt --permutations 200      # real (~1h)
pytest -q                                                                     # full suite
```

## Open questions

- **Q-1**: Does shuffling bars across market sessions (`forex/sessions.py` — crypto
  24/7 vs FX Sun 22:00→Fri 22:00) bias the null? Signals are pure TA on bars, so it
  should be neutral, but this is unverified. Measure before trusting a real verdict.
- **Q-2b**: The common-window trim (below) costs **67% of the panel's history**
  on the live `matt` book — 2354 of 7194 bars, limited by SOLUSD's 2020 listing.
  The verdict therefore covers 2020→2026, a crypto-era window, not full history.
  Whether the same search scores differently over 2003→2026 is unmeasured, and
  would need the ragged-panel permutation in the decision log to find out.
- **Q-2**: The breed is stochastic. Fixing one seed across real and all permutations
  isolates the *data*'s contribution, but yields a single search trajectory rather
  than a distribution over them. Whether the verdict is stable across breeding seeds
  is unmeasured.
- **Q-3**: `bar_quality.py` refuses range-less bars. Whole-bar shuffling means a
  range-less bar stays range-less (pinned by
  `test_permutation_keeps_bars_internally_consistent`), but the interaction with
  `close_only_signals` policies is still untested.

## Decision log

| Date | Decision | Who |
|------|----------|-----|
| 2026-09-19 | Build step 2 on the FX swarm, not the equity sleeves — that is where the search, and the `n_trials` problem, actually are | matt |
| 2026-09-19 | Report the p-value alongside DSR/PBO; do not gate on it yet | matt |
| 2026-09-19 | Re-breed on every permutation rather than re-evaluating the existing 424 genomes. The cheap version is **invalid**: those genomes were bred *from* the real panel and are already fitted to it, so they underperform on noise, making the null too easy and the p-value too small — an error in the flattering direction. It is also not cheaper (424 vs 480 evaluations, both measured) | claude |
| 2026-09-19 | Hold the breeding seed fixed across the real run and every permutation, so the only variable is the data | claude |
| 2026-09-19 | **Trim a ragged panel to the window every symbol shares, and report the cost.** Instruments list at different times (majors 2003, AUDUSD 2006, BTC 2014, ETH 2017, SOL 2020), and a shared shuffle needs one common timeline. Alternatives rejected: forward-filling would put fabricated prices into the null; a per-symbol permutation over the union timeline preserves cross-symbol co-movement only under bookkeeping intricate enough to risk a subtly-wrong null, which is the worst possible failure here. The comparison stays fair because real and shuffled runs use the identical trimmed panel — what changes is the verdict's SCOPE, which the report states explicitly (see Q-2b) | claude |
| 2026-09-19 | Resolve the profile from `ACCOUNTS[account]["profile"]`, as `champions.main` does, instead of defaulting to `balanced`. Found pre-flight: the hardcoded default would have silently tested `partner` (conservative) and `daytrader` (intraday) under the wrong knobs | claude |
| 2026-09-19 | Build the referee first, then widen `research.py` — a wider search makes the `n_trials` problem worse, so the order matters | matt |
| 2026-09-19 | **Diverge from the reference algorithm: shuffle whole bars with ONE permutation**, rather than shuffling a bar's gap separately from its interior. Measured on our FX panels, `corr(gap, intrabar)` is −0.15 to −0.23, so the two-permutation scheme inflates the null's volatility by ~2.2% and shifts cross-symbol correlations by up to 0.026 — a rigged null. Cost: the within-bar gap↔interior relationship survives, which no close-based archetype can exploit. Revisit if an intraday strategy is added | claude |
