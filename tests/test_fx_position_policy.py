"""The shared target -> position policy.

What this pins:
  * defaults reproduce the plain no-churn band EXACTLY (so turning the knobs to
    zero is a true no-op, and the live books' history stays interpretable);
  * hysteresis actually stops the open/close/open cycle a single threshold
    produces — the churn that showed up live as 4-bar holds against a 57-bar
    signal horizon;
  * the minimum hold protects a young position but NEVER traps a book: a sign
    reversal and the drawdown breaker both still get through;
  * a shut venue outranks everything, including a forced flatten.
"""
import pandas as pd
import pytest

from trading_algo.forex import position_policy as pp
from trading_algo.forex.fx_config import FXParams

PLAIN = FXParams()                                     # knobs off
HYST = FXParams(entry_threshold=0.08, exit_threshold=0.03, min_hold_bars=5)


def S(**kw):
    return pd.Series(kw, dtype=float)


# --- backward compatibility -------------------------------------------------

def test_defaults_reproduce_the_plain_band():
    """The pre-existing rule, verbatim: keep the current weight unless the
    target moves by at least rebalance_min_delta."""
    def old(positions, target, p):
        keys = set(positions) | set(target.index)
        return {k: (positions.get(k, 0.0)
                    if abs(float(target.get(k, 0.0)) - positions.get(k, 0.0))
                    < p.rebalance_min_delta else float(target.get(k, 0.0)))
                for k in keys}

    cases = [
        ({}, S(A=0.30)), ({}, S(A=0.001)),
        ({"A": 0.30}, S(A=0.0)), ({"A": 0.01}, S(A=0.0)),
        ({"A": 0.30}, S(A=0.31)), ({"A": 0.30}, S(A=0.10)),
        ({"A": 0.30}, S(A=-0.30)), ({"A": -0.20}, S(A=0.25)),
        ({"A": 0.30, "B": -0.10}, S(A=0.0, B=-0.40)),
    ]
    for held, tgt in cases:
        assert pp.settle(held, tgt, PLAIN) == old(held, tgt, PLAIN), (held, tgt)


# --- hysteresis -------------------------------------------------------------

def test_weak_target_does_not_open_a_position():
    assert pp.settle({}, S(A=0.05), HYST)["A"] == 0.0      # below entry 0.08
    assert pp.settle({}, S(A=0.20), HYST)["A"] == 0.20     # clears it


def test_open_position_survives_between_the_two_thresholds():
    """The whole point: a target that decays into the band keeps its position
    instead of closing and re-opening on the next tick."""
    held = {"A": 0.20}
    assert pp.settle(held, S(A=0.05), HYST)["A"] == 0.05   # above exit -> resize
    assert pp.settle(held, S(A=0.02), HYST)["A"] == 0.0    # at/below exit -> close


def test_hysteresis_kills_the_open_close_cycle_a_single_threshold_creates():
    """A target oscillating around one threshold churns; around the GAP it does
    not. This is the cost bug, reproduced and then fixed."""
    single = FXParams(entry_threshold=0.08, exit_threshold=0.08, rebalance_min_delta=0.0)
    osc = [0.09, 0.07, 0.09, 0.07, 0.09, 0.07]

    def round_trips(p):
        held, trips = {}, 0
        for t in osc:
            new = pp.settle(held, S(A=t), p, bars_held={})
            if held.get("A", 0.0) != 0.0 and new["A"] == 0.0:
                trips += 1
            held = new
        return trips

    gapped = FXParams(entry_threshold=0.08, exit_threshold=0.03, rebalance_min_delta=0.0)
    assert round_trips(single) == 3          # closes on every dip
    assert round_trips(gapped) == 0          # rides straight through


# --- minimum hold -----------------------------------------------------------

def test_min_hold_protects_a_young_position_from_closing():
    held, ages = {"A": 0.20}, {"A": 2}       # 2 bars old, min_hold_bars=5
    assert pp.settle(held, S(A=0.0), HYST, bars_held=ages)["A"] == 0.20
    assert pp.settle(held, S(A=0.0), HYST, bars_held={"A": 5})["A"] == 0.0


def test_min_hold_never_blocks_a_reversal():
    """A signal that has swung to the other side is new information, not noise
    around zero — trapping the book through it would be a risk bug."""
    out = pp.settle({"A": 0.20}, S(A=-0.30), HYST, bars_held={"A": 0})
    assert out["A"] == -0.30


def test_min_hold_does_not_block_resizing():
    """Only full closes are protected; the book can still be re-risked."""
    out = pp.settle({"A": 0.20}, S(A=0.10), HYST, bars_held={"A": 0})
    assert out["A"] == 0.10


def test_unknown_age_counts_as_seasoned():
    """Books opened before bars_held existed hold real positions of unknown age;
    assuming 'brand new' would freeze every one against its own exit signal."""
    assert pp.settle({"A": 0.20}, S(A=0.0), HYST, bars_held={})["A"] == 0.0


# --- overrides --------------------------------------------------------------

def test_breaker_flattens_through_hysteresis_and_min_hold():
    out = pp.settle({"A": 0.20, "B": -0.30}, S(), HYST,
                    bars_held={"A": 0, "B": 0}, force_flat=True)
    assert out == {"A": 0.0, "B": 0.0}


def test_shut_venue_outranks_even_a_forced_flatten():
    """You cannot fill on a closed exchange, breaker or not."""
    out = pp.settle({"A": 0.20, "B": 0.30}, S(), HYST,
                    frozen={"A"}, force_flat=True)
    assert out["A"] == 0.20 and out["B"] == 0.0


# --- ageing -----------------------------------------------------------------

def test_ages_advance_reset_and_drop():
    ages = pp.advance_ages({"A": 0.2, "B": 0.1}, {"A": 0.25, "B": -0.1, "C": 0.3},
                           {"A": 3, "B": 7})
    assert ages["A"] == 4        # same sign -> ages on
    assert ages["B"] == 0        # flipped   -> clock restarts
    assert ages["C"] == 0        # newly opened
    assert pp.advance_ages({"A": 0.2}, {}, {"A": 3}) == {}   # closed -> dropped


# --- config validation ------------------------------------------------------

def test_inverted_hysteresis_is_refused_at_construction():
    with pytest.raises(ValueError, match="exit_threshold"):
        FXParams(entry_threshold=0.02, exit_threshold=0.10)


def test_negative_knobs_are_refused():
    with pytest.raises(ValueError):
        FXParams(min_hold_bars=-1)
    with pytest.raises(ValueError):
        FXParams(entry_threshold=-0.1)
