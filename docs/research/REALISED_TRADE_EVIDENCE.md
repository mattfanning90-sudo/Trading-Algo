# What the books' own trades say — realised-outcome evidence

**Date:** 2026-09-16
**Status:** ⚠️ **CORRECTED 2026-09-16** — the original headline finding (a ~6σ
below-chance hit rate across every agent) was a **measurement artifact**. See §0.
The surviving finding is narrower and is stated in §4.
**Data:** 2,984 realised position-to-position outcomes from the four live FX
paper books (`state/fx_state_{matt,partner,daytrader,multiasset}.json`)

## 0. Correction — read this first

The first version of this document reported that every agent had negative
information coefficient and that the books' aggregate hit rate was ~44.6%,
roughly **6 standard deviations below chance**. That was wrong, and the error
was in the metric, not the data.

**The bug.** Hit was scored as `sign(weight) == sign(forward_return)`. When a
forward return is *exactly zero* that comparison is always False, so every flat
bar was silently counted as a **miss**. FX panels are built by outer-joining all
symbols onto a union calendar and forward-filling, so ~29% of every FX pair's
bars are identical to the previous close, and **36% of FX trades had a flat next
bar**.

**Why it looked so convincing.** A purely random signal, scored this way, lands
at `(1 − 0.36) × 50% ≈ 32%`. The measured FX figure was 30–36%. Crypto trades
24/7, has no forward-filling, no flat bars — and scored 50.5%. The "anomaly" was
present in exactly the instruments the metric was broken for, and absent in the
one it wasn't. That coincidence read as a smoking gun; it was the artifact
signing its own work.

**What should have caught it sooner.** A 4.5% hit rate on forward-filled bars —
wrong 95% of the time — is not a plausible trading result. Numbers that extreme
in either direction are measurement errors far more often than discoveries. The
sanity check that settles it costs one line: *what would a random signal score
under this metric?*

Everything below §1 is the corrected analysis.

## 1. Why this analysis exists

Every backtest in this repo scores strategies against a **price panel**. None of
them, until now, scored the books against **what the books actually did**.

That gap matters because the two can disagree. A price-panel backtest measures
the signal; the trade ledger measures the signal *plus* everything between the
signal and the fill — the holding policy, the session gate, the churn band,
whole-weight rounding, execution timing. If those layers introduce an error, the
panel backtest cannot see it and the ledger can.

The FX books happen to record exactly what is needed. Every trade carries:

```
date, pair, side, delta_weight, target_weight, price, aud_per_quote,
regime, why,
agents:     {trend, breakout, meanrev, momentum, carry, neural}  <- the vote vector
indicators: {price, ema_fast, ema_slow, adx, ...}                <- the readings
```

That is a labelled record of real decisions, with the inputs that caused them.
3,024 trades carry agent votes.

## 2. Method

Reproducible from committed state, no network required:

1. Group each book's trades by pair, sorted by date.
2. For consecutive trades `a → b` on the same pair, the position held over that
   interval is `a.target_weight`, and the realised move is `b.price / a.price − 1`.
3. **Exclude flat bars** (`move == 0`) rather than scoring them. This is the
   correction in §0 and it is not optional: 36% of FX trades land on a bar whose
   next close is forward-filled, and counting those as misses caps the maximum
   achievable hit rate at ~64% while dragging a random signal to ~32%.
4. Outcome `pnl = target_weight × move`. Hit = `sign(weight) == sign(move)`.
5. Per agent, restrict to trades where that agent actually had an opinion
   (`|vote| > 0.1`) and correlate its vote with the realised move.

**Always compute the null.** Before believing any result from this method, score
a random signal through the identical pipeline. If the null is not ~50%, the
metric is broken, not the strategy.

Cross-checks that should accompany any rerun: split by asset class (crypto has
no forward-filling and is the natural control), and split by book (the daily
books join cleanly to a daily panel; `daytrader` runs 60m bars and does not).

## 3. Result (corrected — flat bars excluded, not counted as misses)

```
1,983 scored outcomes (711 flat FX bars excluded)

agent               IC      hit    sigma
trend          -0.0407    44.9%     -3.4
breakout       +0.0379    48.1%     -1.7
meanrev        -0.0776    50.8%     +0.4
momentum       +0.0576    48.4%     -1.4
carry          -0.1230    55.2%     +3.1
neural         -0.0626    44.2%     -2.1
```

```
regime             n      hit    sigma
ranging          651    51.3%     +0.7
trending       1,332    44.7%     -3.9
```

By asset class, hit rate with flat bars excluded: **crypto 50.5% (+0.2σ)**,
**FX 46.5% (−2.5σ)**.

## 4. What actually survives

**The agents perform at or near chance.** ICs are mixed — breakout (+0.038) and
momentum (+0.058) are positive; trend, meanrev, carry and neural are negative.
Hit rates cluster between 44% and 55%. There is no systematic inversion and no
6σ anomaly. This is consistent with everything else measured: a thin gross edge
that trading costs consume.

**The regime split does survive correction, and is the one real finding here.**
The books hit 44.7% in `trending` (−3.9σ) against 51.3% in `ranging` (+0.7σ).
The trend and breakout agents are ADX-gated specifically so they act *only* in
trending conditions, and that is where the books do worst. The `trend` agent
itself is the weakest single agent at 44.9% (−3.4σ).

Caveat that must travel with this: six agents and two regimes were examined, so
a −3.9σ carries a multiple-comparison discount. It is worth investigating, not
worth acting on yet.

## 5. What this does NOT license

**Do not invert the signals.** This was written when the finding looked like a
uniform inversion, and it holds even more strongly now that it doesn't: there is
no systematic anti-correlation to exploit.

**Do not read the trending-regime result as proven.** It is one finding from a
family of comparisons on a few months of live history.

## 6. Candidate explanations (unresolved)

1. **~~Holding-period confound~~ — TESTED AND REJECTED.** The same trades were
   re-scored over fixed forward horizons from the price panel (1, 5, 10, 20 and
   57 bars) instead of the book's actual exit. IC does not improve with horizon;
   it gets *worse* (trend −0.034 → −0.341; carry −0.098 → −0.487 from 1d to
   57d). Giving the signals room to resolve does not rescue them. This is
   consistent with `policy_sweep`, which found that braking turnover made
   out-of-sample returns worse.
2. **~~Off-by-one alignment~~ — TESTED AND REJECTED.** Hit rate was measured at
   offsets −2, −1, 0, +1, +2 bars. No offset restores chance performance, past
   returns sit at ~52% (chance), and the stored trade price matches the panel
   close on the same date to a median of 0.036% — so the join is sound and there
   is no shifted window that explains it.
3. **Why the trending gate underperforms — OPEN.** The surviving finding (§4).
   Candidates: the ADX threshold mislabels regimes; the trend/breakout agents
   are miscalibrated for the regimes they are gated into; or trending periods
   simply coincide with higher spread cost. Testable from stored indicators —
   every trade carries the ADX reading that produced its regime label.

## 7. Consequence for the learning loop

This finding reorders
[the champion/challenger design](../superpowers/specs/2026-09-16-champion-challenger-learning-design.md).

The original concern — that a promotion loop would faithfully optimise a
systematically misaligned pipeline — no longer applies, because the misalignment
was in the measurement rather than the system. What remains is narrower: a regime
gate that appears to underperform in the conditions it is designed for.

A **Phase 0** is therefore added to that spec — but a much narrower one than the
original version of this document implied. There is no systematic inversion to
find. What remains is the trending-regime underperformance (§4), which is worth
understanding before a learning loop is built on top of a regime gate that may
be mislabelling its own conditions.

## 8. The general lesson

The repo's invariants are strong on *signal* correctness — no lookahead, costs
always on, one weight function — and the test suite proves the maths on a clean
price matrix. `verify.py` was written because those proofs say nothing about the
books, and it has repeatedly been right (it flagged `never-traded: ASX` for 52
days, and the FTSE cash discrepancy on the day of the phantom loss).

This analysis is the same lesson one level further: **the ledger is evidence the
panel cannot give you**, and nothing was reading it. Any future strategy claim
should be checked against realised trades, not only against a backtest.

And §0 is the lesson after that: **a new measurement needs its own null test
before its results are believed.** The question "what would a random signal score
under this metric?" would have caught the error in one line, before it became a
document, a spec amendment, and a blocking phase.
