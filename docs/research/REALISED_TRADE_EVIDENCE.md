# What the books' own trades say — realised-outcome evidence

**Date:** 2026-09-16
**Status:** Finding recorded; root cause NOT yet established (see §6)
**Data:** 2,984 realised position-to-position outcomes from the four live FX
paper books (`state/fx_state_{matt,partner,daytrader,multiasset}.json`)

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
3. Outcome `pnl = target_weight × move`. Hit = `sign(weight) == sign(move)`.
4. Per agent, restrict to trades where that agent actually had an opinion
   (`|vote| > 0.1`) and correlate its vote with the realised move.

This measures the **realised holding period**, not a fixed forward horizon. That
is deliberate — it is what the book actually experienced — but it is also the
main confound (§6).

Script: `docs/research/` has no runner; the analysis is ~40 lines over
`state/fx_state_*.json` and is reproduced in §3 of this document's git history.

## 3. Result

```
2,984 realised position-to-position outcomes across 4 books

agent         IC vs realised   hit rate   mean P&L when it voted
trend                -0.0545      44.9%          -0.6 bp
breakout             -0.0297      44.3%          -0.4 bp
meanrev              -0.0986      51.1%          -0.2 bp
momentum             -0.0186      46.0%          -0.4 bp
carry                -0.0371      52.6%          -0.3 bp
neural               -0.0339      44.1%          -1.5 bp
```

```
regime        trades    mean P&L   hit rate
ranging        1,098      -0.2 bp     48.7%
trending       1,886      -0.5 bp     42.3%
```

## 4. The two things that matter

**Every agent has negative information coefficient.** Not scattered around zero
— all six, clustered between −0.02 and −0.10. Six independently-designed
strategies do not all land on the same side of zero by chance.

**The aggregate hit rate is ~44.6% over 2,984 trades.** The standard error of a
proportion at n=2,984 is ≈0.9%, so 44.6% sits roughly **6 standard deviations
below chance**. Whatever this is, it is not noise.

**The regime split is inverted.** The book does materially worse in `trending`
(42.3%) than in `ranging` (48.7%). The trend and breakout agents are ADX-gated
specifically so they act *only* in trending conditions. They do worse there.

## 5. What this does NOT license

**Do not invert the signals.** Anti-correlation this uniform is far more likely
to be a defect than an edge. Inverting a defect loses money in the other
direction while paying the same spread, and it would bake the bug in as a
feature — after which the real fix becomes invisible.

**This is not a claim that the agents have no edge.** It is a claim that
*something in the path from signal to realised outcome is wrong*, and that the
agents cannot be evaluated until it is found.

## 6. Candidate explanations (unresolved)

1. **Holding-period confound.** Outcomes are measured over the realised hold,
   and `verify.py` reports median holds of 4–5 bars against a 57-bar
   breakout/momentum horizon — 7–9% of the window, with ~80% of round-trips
   closing before their signal could resolve. So this may be grading signals
   before they have had a chance to be right. **Test:** re-score the same trades
   over a fixed forward horizon from the price panel, independent of when the
   book actually exited. If IC turns positive, the defect is the holding policy,
   not the signal.
2. **Sign or alignment error.** A one-bar offset between signal and execution,
   or a flipped convention anywhere in `position_policy.settle` →
   `fx_book.run_once`, would produce exactly this fingerprint. **Test:** replay a
   handful of trades by hand from stored indicators and confirm the sign of the
   position matches the sign of the ensemble tilt that produced it.
3. **Genuinely anti-correlated signals.** Possible but least likely, precisely
   because it is uniform across six different strategies.

(1) and (2) are distinguishable with data already on disk. Neither requires the
network.

## 7. Consequence for the learning loop

This finding reorders
[the champion/challenger design](../superpowers/specs/2026-09-16-champion-challenger-learning-design.md).

Building a promotion loop on top of a decision path that may be systematically
misaligned would optimise a broken pipeline — and worse, the loop would
faithfully promote whichever challenger best exploits the defect, making it
permanent and much harder to find.

A **Phase 0** is therefore added to that spec: establish why realised outcomes
run 6σ below chance, before any learning machinery is built on top of them.

## 8. The general lesson

The repo's invariants are strong on *signal* correctness — no lookahead, costs
always on, one weight function — and the test suite proves the maths on a clean
price matrix. `verify.py` was written because those proofs say nothing about the
books, and it has repeatedly been right (it flagged `never-traded: ASX` for 52
days, and the FTSE cash discrepancy on the day of the phantom loss).

This analysis is the same lesson one level further: **the ledger is evidence the
panel cannot give you**, and nothing was reading it. Any future strategy claim
should be checked against realised trades, not only against a backtest.
