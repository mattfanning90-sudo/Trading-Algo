"""Selection-bias guard for the config tuner.

`tune.py` sorts a knob grid by IN-SAMPLE Active return and (used to) print a
pass/fail "Goal MET: beats benchmark by >= +2%" with no deflation — the exact
selection bias the rest of the system was built to penalize. These tests pin
that the verdict now folds in the Deflated Sharpe Ratio of the selected best
(n_trials = number of grid rows searched) and no longer claims success purely
from an undeflated in-sample maximum.
"""
from __future__ import annotations

import io
import itertools
from contextlib import redirect_stdout

from trading_algo import tune


def _grid_size() -> int:
    return len(list(itertools.product(*tune._GRID.values()))) * len(tune._ALLOCS)


def test_goal_verdict_requires_dsr_not_just_in_sample_active():
    # A huge in-sample Active that fails deflation must NOT be declared MET.
    losing = tune._goal_verdict(active=0.25, dsr=0.10)
    assert "MET" not in losing.replace("not yet", "")

    # Beating the benchmark AND clearing the DSR threshold -> MET.
    winning = tune._goal_verdict(active=0.25, dsr=0.99)
    assert "MET" in winning


def test_goal_verdict_active_alone_is_not_success():
    # Even a very strong Active return, with a DSR just under the bar, is a miss.
    v = tune._goal_verdict(active=1.0, dsr=tune._DSR_MIN - 0.01)
    assert "MET" not in v.replace("not yet", "")


def test_report_prints_deflated_sharpe_with_grid_as_n_trials():
    buf = io.StringIO()
    with redirect_stdout(buf):
        tune.main(["--synthetic"])
    out = buf.getvalue()

    # The report must surface the Deflated Sharpe of the selected config...
    assert "DSR" in out
    # ...deflated for the number of configs actually searched (the grid size).
    assert f"n_trials={_grid_size()}" in out

    # And on meaningless synthetic data it must not rubber-stamp a raw
    # in-sample Active as a met goal.
    goal_line = [ln for ln in out.splitlines() if "Goal" in ln]
    assert goal_line, "expected a Goal verdict line in the report"
    if "MET" in goal_line[0] and "not yet" not in goal_line[0]:
        # If it ever claims MET, a real DSR must back it — never Active alone.
        assert "DSR" in goal_line[0]
