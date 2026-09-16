"""The ONE promotion gate for every learned component in the FX subsystem.

Phase 1 of the champion/challenger design
(`docs/superpowers/specs/2026-09-16-champion-challenger-learning-design.md`):
the absolute floor, and the loaders that read it.

Why this module exists
----------------------
The subsystem had two promotion paths that failed in opposite directions:

* the swarm gate demands DSR >= 0.95 — roughly a 3.8 annualised Sharpe against a
  best-bred 0.96 — and has promoted nothing in three breeding cycles;
* the ML path had **no gate at all**. `train.py` graded the model honestly every
  week and `ml_pool` loaded whatever was on disk, because nothing connected the
  grade to the decision. A model measured at -0.62 Sharpe / DSR 0.00 traded the
  live books for weeks on the strength of being the most recent file.

Neither is learning. The first cannot improve because nothing passes; the second
cannot improve because nothing is compared.

The floor, and why it is separate from "beat the incumbent"
-----------------------------------------------------------
A relative test alone promotes the less-bad of two losing models — it would have
promoted a -0.44 Sharpe challenger precisely because the incumbent was -0.62.
The floor is the answer to "is this worth trading at all?", asked before "is it
better than what we have?". Both are required; this module is the first.

Refusing is the safe direction. "No champion" is a supported state: the lane runs
the five hand-written technical agents, which is what the books did before any
model existed and what `NeuralAgent` already does with no bundle loaded.
"""
from __future__ import annotations

# Net-of-cost out-of-sample Sharpe a learned component must exceed to trade.
# 0.0 means "must not lose money out of sample" — deliberately the weakest
# defensible bar, because the point of Phase 1 is to stop harm, not to pick
# winners. Raise it to demand a real margin.
FLOOR_SHARPE = 0.0


def clears_floor(evaluation: dict | None,
                 floor: float = FLOOR_SHARPE) -> tuple[bool, str]:
    """Does a recorded out-of-sample evaluation clear the absolute floor?

    Returns `(ok, reason)` — the reason is always populated so the caller can say
    out loud why a model was refused. A refusal nobody can explain gets worked
    around; a refusal with a number attached gets fixed.

    An ABSENT or incomplete evaluation is a refusal, not a pass. A model with no
    recorded grade was never graded, and "we did not check" must never read the
    same as "it passed" — that equivalence is the whole defect this closes.

    `evaluation` is the `meta["evaluation"]` dict a trained bundle carries:
    at minimum `{"sharpe": <net OOS annualised Sharpe>}`.
    """
    if not evaluation:
        return False, "no recorded evaluation — the model was never graded"
    sharpe = evaluation.get("sharpe")
    if sharpe is None:
        return False, "evaluation records no out-of-sample Sharpe"
    try:
        sharpe = float(sharpe)
    except (TypeError, ValueError):
        return False, f"evaluation Sharpe is not a number ({sharpe!r})"
    if sharpe != sharpe:                                   # NaN
        return False, "evaluation Sharpe is NaN"
    if sharpe <= floor:
        return False, (f"net out-of-sample Sharpe {sharpe:+.2f} does not clear "
                       f"the floor of {floor:+.2f}")
    return True, f"net out-of-sample Sharpe {sharpe:+.2f} clears {floor:+.2f}"
