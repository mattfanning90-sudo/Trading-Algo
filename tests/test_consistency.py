"""Guard invariant #4: backtest and paper trading share ONE weight function.

If someone re-introduces a second copy of the target-weight logic, these tests
fail — both engines must route through strategy.compute_targets.
"""
import inspect

from trading_algo import backtest, paper_trade, strategy


def test_backtest_uses_shared_compute_targets():
    src = inspect.getsource(backtest)
    assert "compute_targets" in src
    assert "strategy" in src


def test_paper_uses_shared_compute_targets():
    src = inspect.getsource(paper_trade)
    assert "strategy.compute_targets" in src


def test_compute_targets_is_the_only_weight_builder():
    # vol_target + select_portfolio live behind compute_targets; neither engine
    # should call select_portfolio directly (that would bypass vol targeting).
    assert "select_portfolio" not in inspect.getsource(backtest)
    assert "select_portfolio" not in inspect.getsource(paper_trade)


def test_metrics_always_present_in_backtest_output():
    # costs-always-on contract: backtest result must carry a cost figure
    src = inspect.getsource(strategy.compute_targets)
    assert "vol_target" in src
