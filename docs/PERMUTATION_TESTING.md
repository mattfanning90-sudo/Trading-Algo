# Deep Research → Design: Permutation Testing for this System

This document records the research behind permutation testing in this repo and
maps each finding to a concrete decision. Companion to
`obsidian/Concepts/Permutation Testing.md` (the plain-English explainer) and
`docs/MONTE_CARLO_RESEARCH.md` (the neighbouring method — read that one too,
they solve overlapping problems from different angles).

Every measured number below comes from our **own** US panel (117 names,
2012-01-03 → 2026-09-18, real cached prices) via a committed, seeded script, not
from the literature:

```bash
python scripts/measure_permutation_null.py --permutations 1000
```

> **Honesty note.** All Sharpe figures here are **gross of costs** and exist to
> diagnose a *test*, not to report performance (invariants #2 and #5). The
> headline result is a **negative** one: the published method, applied to this
> repo's strategy as-is, would have been wrong — and wrong in the direction that
> hides a real edge rather than inventing one.

Terms are glossed on first use; there is a full glossary at the bottom.

---

## 1. What the method is

A **backtest** — simulating a strategy on history — gives you one number. That
number means nothing until you know what the same number looks like when there
is *no edge to find*. Permutation testing builds that comparison out of your own
data: **shuffle** history into thousands of fake-but-realistic alternative
markets, re-run the identical strategy on each, and count how often luck matched
or beat you.

> **Null hypothesis** — the boring explanation you must rule out: *"there is no
> edge here, I just got lucky."* The **null distribution** is the spread of
> results that explanation produces.

Run $m$ shuffles, count $k$ that matched or beat the real result:

$$p = \frac{k+1}{m+1}$$

> **p-value** — the probability of a result this good by luck alone. Below 0.05
> is conventionally "acceptable evidence"; below 0.01 "strong"; above 0.10
> "probably noise".

The two `+1`s matter. Without them, beating all 1,000 shuffles reports p = 0 —
a claim of literal impossibility that 1,000 draws cannot support. The `+1`
counts the real result as one more draw, which is the honest floor.

**Verified:** the reference implementation
([neurotrader888/mcpt](https://github.com/neurotrader888/mcpt)) gets this right.
`range(1, 1000)` runs 999 shuffles with the counter pre-set to 1, giving exactly
$(k+1)/(m+1)$. This is a common bug; it is not one here.

## 2. The four steps, and the null each one uses

Taught as a ladder — each rung kills a different way of fooling yourself, and
you only climb if the rung below held.

| # | Step | The null being tested | What it kills |
|---|---|---|---|
| 1 | **In-sample excellence** | *none — not a test* | Ideas not worth the compute |
| 2 | **In-sample permutation** | No structure, *and* you searched the noise just as hard | **Selection bias** |
| 3 | **Walk-forward** | *none — not a test* | Parameter instability, regime decay |
| 4 | **Walk-forward permutation** | No structure after the walk-forward start | **Process overfitting** |

> **In-sample** — data you were allowed to look at while designing.
> **Out-of-sample** — data held back, never seen during design.
> **Selection bias** — looking good because you kept the best of many tries.
> **Walk-forward** — re-fit on a rolling window of the past, trade the slice
> immediately after, roll forward, repeat; then stitch all the traded slices
> into one out-of-sample equity curve.

**Step 1** is deliberately a low bar: optimise hard, in-sample, unconstrained.
It is a filter, not a test. If the best-case fantasy version is not good, stop.

**Step 2 is the clever one, and it is the piece we do not have.** On every
shuffle you re-run the *entire parameter search* and record the **best** result
found on that noise. You then compare best-against-best. The advantage you got
from searching hard is priced into the null by construction.

**Step 4** shuffles only from the walk-forward start onwards — real history
before it, noise after — then re-runs the whole rolling re-fit. It tests the
*method*, not a model: if your habit of re-optimising every month makes money on
noise, the habit is the problem. Published thresholds are p < 0.05 on one year
of out-of-sample, p < 0.01 on two or more.

### 2a. Why step 2 matters to us specifically

`docs/MONTE_CARLO_RESEARCH.md` §3 documents a problem we could not resolve. The
**Deflated Sharpe Ratio** (DSR) — our promotion gate, which discounts a Sharpe
by how many strategies you tried before finding it — needs a single number `N`
for "how many *independent* things did you try". We measured three estimators of
it and they disagreed **by 40×**, with nothing available to adjudicate between
them. §4 concluded that a bootstrap **SPA** test was the right long-term
replacement "because it answers the same question without requiring us to guess
N".

**The in-sample permutation test is the same escape through a different door.**
It never asks how many effective trials you had; it *re-runs the trials on
noise* and lets the answer emerge. Simulating the search rather than summarising
it removes the 40× ambiguity entirely.

## 3. What the shuffle actually preserves (measured)

The whole method rests on the shuffled market containing no edge. These are the
claims, checked rather than assumed — one shared shuffle of our US panel:

| Claim | Measured |
|---|---|
| Each name's volatility preserved | identical to **4.5e-17** |
| How names move together preserved | mean pairwise correlation **0.3781 → 0.3781** (max drift 5.2e-15) |
| Serial structure destroyed | volatility clustering **+0.348 → +0.023** |
| Each name's total return preserved | identical to **1.8e-14** |

> **Volatility** — how much prices jump around. **Correlation** — how closely
> two things move together, −1 to +1. **Volatility clustering** — the real
> market's habit of following wild days with wild days; measured here as the
> autocorrelation of absolute daily moves.

That last row is not advertised anywhere and it is the key to everything below.
Shuffling a set of numbers preserves their sum, so **every shuffle ends at
exactly the same price as the real series**. The null is a *bridge*, pinned at
both ends.

> [!check] This is a genuine strength
> Because each name's total rise survives, you can never pass this test just by
> being long during a bull market. The null already contains the **drift** (a
> market's general tendency to rise) and refuses to credit you for it.

**One shuffle must be shared by every name.** The reference implementation does
this, and it is what preserves the correlation row above. Shuffling each name on
its own clock also destroys co-movement — and since correlation is what sets a
portfolio's volatility, that hands the null a free diversification bonus. See
§5, row 1, for what that costs.

## 4. The problem: this is not a valid null for our strategy

The method was built for **time-series** strategies — one instrument judged
against its own past. This repo trades **cross-sectional** 12-1 momentum: names
are ranked *against each other* and we buy the winners.

> **Time-series** — comparing one thing to its own history.
> **Cross-sectional** — comparing things to each other at one moment in time.

The same property that makes the null strong for time-series makes it invalid
here. Each name's full-sample total return survives the shuffle. So a 252-day
window in the shuffled world is a random subset of that name's returns, which
approximates a *fixed fraction of its whole-sample total*. "What rose over the
last 12 months" therefore partly reveals "what rises over the entire sample" —
a peek at the future, handed to the strategy for free.

Running the repo's own spec (`lookback_days=252`, `skip_days=21`, `top_n=10`,
month-end rebalance) on 1,000 shuffles:

| Statistic under the as-published null | Null mean Sharpe |
|---|---|
| plain long-only top-10 | **+0.823** |
| equal-weight universe (i.e. "the market") | **+0.904** |
| momentum **minus** the market | **+0.329** |

The first number looks alarming but mostly is not: it is approximately the
market, and the market rising is legitimate and not momentum's doing. The honest
statistic is the third — momentum measured *relative to its own universe*, which
**must be zero under any valid null**. It is +0.329, and positive in **92% of
1,000 shuffles**.

> These are three separate Sharpe ratios, not an additive split. The Sharpe of
> (A − B) is not the Sharpe of A minus the Sharpe of B, because subtracting the
> market removes most of the volatility as well as most of the return.

The mechanism, measured directly: the correlation between a name's rank by
*shuffled* 12-month momentum and its rank by *full-sample* total return is
**+0.271**. The signal is reading the answer key.

**A null that earns +0.33 Sharpe is not a null.** Testing against it does not
invent an edge — it *buries* one.

## 5. Four candidate nulls, ranked by whether they are actually null

Same strategy, same data, 1,000 shuffles, seed 0. Real market-relative Sharpe is
**+0.460**. A valid null has a mean of zero and is positive about half the time.

| Null construction | Null mean | sd | % > 0 | p-value | Verdict |
|---|---|---|---|---|---|
| independent shuffle per name | +0.275 | 0.245 | 87% | 0.2348 | ✗ also destroys co-movement |
| **common shuffle (as published)** | +0.330 | 0.229 | 92% | 0.2807 | ✗ leaves each name's drift in |
| de-meaned, then shuffled | −0.278 | 0.268 | 15% | 0.0030 | ✗ manufactures mean reversion |
| **signal shuffle across names** | **−0.002** | 0.267 | **48%** | **0.0390** | ✓ centred on zero |

**The null construction moves the p-value from 0.003 to 0.281 — roughly 90× — on
identical data and an identical strategy.** Choosing it is not a detail; it is
the entire test.

**Row 1 — independent shuffles.** Worse than the published method, not better.
Destroying correlation lowers the null portfolio's volatility, so the null gets
a diversification bonus the real strategy never had.

**Row 3 — de-meaning looks like the obvious fix and is a trap.** Subtracting
each name's average daily return does remove the drift the signal was latching
onto. But combined with the bridge property from §3, it forces every name to
finish exactly where it started — which *manufactures* mean reversion. Momentum
is then systematically punished, the null sits at −0.278, and the resulting
p = 0.0030 is far too generous. A wrong answer in the flattering direction is
more dangerous than one in the harsh direction.

**Row 4 — shuffle the signal, not the prices.** Leave the price panel entirely
untouched. At each rebalance, shuffle **which name each momentum score is
attached to**. This breaks exactly one thing — "this name's past predicts this
name's future", which is the whole claim of momentum — and disturbs nothing
else. No drift reaches the signal, and no artificial reversion is introduced.
The null lands at −0.002 and is positive 48% of the time.

This is also where `docs/MONTE_CARLO_RESEARCH.md` already pointed: the last link
in its sources is
[Potter, *shuffle the signal, not the trades*](https://www.susanpotter.net/quant/monte-carlo-permutation-tests-strategy-significance/).
The measurements above are what turn that pointer into a decision.

### 5a. Sanity check — the method works where it was designed to

Single-instrument **Donchian breakout** (go long above the highest close of the
last N days, short below the lowest) on AAPL, best-of-10 lookbacks, with the
search re-run on every shuffle:

```
real +0.524 | null mean +0.551 | p = 0.5365
```

Properly behaved: the real result sits right in the middle of the null, so the
verdict is "indistinguishable from luck" — correct for a naive breakout rule.
The null is *positive* because the shuffle preserves AAPL's rise and a
long-biased rule collects it. That is the method working as intended.

## 6. Decisions

1. **Do not apply the published bar/return shuffle to the equity sleeves.** It
   is invalid for cross-sectional momentum, in the direction that hides edge.
   It remains correct for any time-series rule we test on a single instrument —
   much of `trading_algo/forex/` qualifies.
2. **The signal shuffle is the sanctioned null for cross-sectional work.**
   `scripts/measure_permutation_null.py` is the reference implementation.
3. **Build step 2 (in-sample permutation) before steps 3 and 4.** Step 2 is
   genuinely new capability and addresses the unresolved effective-`N` problem
   in `MONTE_CARLO_RESEARCH.md` §3. Steps 3 and 4 substantially duplicate what
   `sweep.py` and the PBO gate already do.
4. **Do not retire DSR/PBO in favour of this.** They answer overlapping
   questions with different assumptions; agreement between them is evidence,
   and disagreement should be treated as *unproven* rather than as a pass — the
   same rule `MONTE_CARLO_RESEARCH.md` §3 already sets.
5. **Record the null construction alongside any p-value we ever report.** Given
   the 90× spread in §5, a p-value without its null is not a result.

## 7. What this says about the US sleeve

Against the one properly centred null, 12-1 cross-sectional momentum on our US
panel scores **p = 0.0390, gross of costs**, over 2012–2026.

Real, but marginal — and "gross of costs" is doing real work in that sentence.
Invariant #2 says the costs-on number is the only one that counts, and costs can
only move this the wrong way. Treat it as "the edge survives a fair test",
**not** as "the edge is strong".

## 8. Limitations

- **One panel, one seed, one vintage.** US only; ASX/FTSE/TSX not measured.
  Run `--region ASX` to extend it.
- **Gross of costs throughout**, by design — this diagnoses a test, not a
  strategy.
- **Monte Carlo error.** At 1,000 shuffles a p-value near 0.05 carries roughly
  ±0.01 of sampling noise, so p = 0.039 and p = 0.052 are the same finding. Do
  not read the third decimal. Halving the error needs 4× the shuffles.
- **The OHLC gap subtlety is unmeasured.** Reading `bar_permute.py`, intrabar
  high/low/close are shuffled with one permutation and overnight gaps with a
  *separate* one. That zeroes the covariance between a bar's gap and its own
  intraday move, so permuted close-to-close volatility will not match real
  unless that covariance was already zero. Irrelevant to our daily close-based
  equity sleeves; it would matter for intraday FX. Our cached panels are
  close-only, so this was not measured.
- **The signal shuffle tests the ranking link only.** It validates "past
  relative performance predicts future relative performance". It does not
  exercise the regime filter, vol targeting, or `compute_targets` — a
  full-pipeline null is a larger piece of work.
- **Steps 2 and 4 are described here, not built.** Only the null-validity
  question is implemented.

## How to reproduce

```bash
python scripts/measure_permutation_null.py                   # US, 200 shuffles
python scripts/measure_permutation_null.py --permutations 1000
python scripts/measure_permutation_null.py --region ASX
python scripts/measure_permutation_null.py --synthetic       # offline, pipeline test only
```

## Glossary

| Term | Plain English |
|---|---|
| **Backtest** | Simulating a strategy on historical data. |
| **Permutation** | A reshuffle — same values, different order. |
| **Null / null distribution** | The range of results you'd get with no skill at all. |
| **p-value** | The chance luck alone matched or beat you. Smaller is better. |
| **In-sample / out-of-sample** | Data you designed against / data held back. |
| **Selection bias** | Looking good because you kept the best of many attempts. |
| **Overfitting** | Memorising noise in history instead of learning a rule. |
| **Walk-forward** | Re-fit on the past, trade the next slice, roll, repeat. |
| **Sharpe ratio** | Return ÷ risk, annualised. Reward per unit of volatility. |
| **Profit factor** | Money made ÷ money lost. Above 1 means net profitable. |
| **Drift** | A market's long-run tendency to rise. |
| **Volatility** | How much prices jump around. |
| **Volatility clustering** | Wild days follow wild days; calm follows calm. |
| **Correlation** | How closely two things move together (−1 to +1). |
| **Time-series** | Comparing one thing to its own past. |
| **Cross-sectional** | Comparing things to each other at one moment. |
| **Donchian breakout** | Buy above the N-day high, sell below the N-day low. |
| **DSR (Deflated Sharpe Ratio)** | A Sharpe discounted for how many strategies you tried first. |
| **PBO** | Probability of Backtest Overfitting — how often the in-sample winner loses out-of-sample. |
| **SPA (Superior Predictive Ability)** | A bootstrap test for "is the best of my N rules genuinely good?" |
| **Bootstrap** | Resampling your data *with* replacement to estimate uncertainty. |

## Key sources

- Masters, *Permutation and Randomization Tests for Trading System Development* (2020) — the book this method comes from
- Masters, *Testing and Tuning Market Trading Systems* (2018) — [Springer](https://link.springer.com/book/10.1007/978-1-4842-4173-8)
- [neurotrader888/mcpt](https://github.com/neurotrader888/mcpt) — the reference Python implementation (`bar_permute.py`, `insample_donchian_mcpt.py`, `walkforward_donchian_mcpt.py`)
- [Build Alpha — Monte Carlo permutation](https://www.buildalpha.com/monte-carlo-permutation/) and [walk-forward optimization](https://www.buildalpha.com/walk-forward-optimization/) — practitioner thresholds
- [Malizzi, *OHLC Bar Permutation*](https://joshmalizzi.substack.com/p/ohlc-bar-permutation-how-to-confirm) — the $(k+1)/(m+1)$ convention
- [Potter, *Monte Carlo permutation tests for strategy significance*](https://www.susanpotter.net/quant/monte-carlo-permutation-tests-strategy-significance/) — shuffle the signal, not the trades
- Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014) — [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) — the gate this complements
- White, *A Reality Check for Data Snooping*, Econometrica (2000); Hansen, *A Test for Superior Predictive Ability*, JBES (2005) — the bootstrap route to the same question
