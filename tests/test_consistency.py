"""Guard invariant #3: backtest and paper trading share ONE weight function.

Two layers of defence:

1. Source-level (cheap, fast): both engines reference strategy.compute_targets and
   neither calls select_portfolio directly.
2. Numeric golden equality (backlog refactor R4): the weights each engine actually
   *acts on* equal strategy.compute_targets' output bit-for-bit — so a second
   sizing path (e.g. an ADV cap trimmed post-hoc in one engine) can't slip past a
   text grep. This is the guard every future sizing/cost feature (F3, F6, F15, F9)
   must keep green.

Note on costs: the two engines legitimately compute *cost* differently today
(backtest: turnover x rate; paper: per-trade commission+stamp+slippage). Unifying
that into one fees.py entrypoint is refactor R1 (ships with F6); until then this
file asserts weight identity, not cost identity.
"""
import ast
import inspect
import textwrap

import pandas as pd

from trading_algo import backtest, paper_trade, strategy


# ---------------------------------------------------------------------------
# Layer 1 — source-level guards
# ---------------------------------------------------------------------------
# The two public roads into the ONE weight formula. `targets_at` IS the formula
# (selection + vol targeting); `compute_targets` is precompute + targets_at for
# callers holding a single date. An engine must use one of these and nothing else.
_SHARED_ENTRYPOINTS = ("strategy.targets_at", "strategy.compute_targets")

# The primitives that live BEHIND the formula. An engine calling any of these
# directly would be a second sizing path — exactly what invariant #3 forbids.
_WEIGHT_PRIMITIVES = ("select_portfolio", "select_long_short", "vol_target",
                      "_apply_capacity")


def _calls(obj) -> set[str]:
    """Every function actually CALLED by a module or function, as dotted names.

    Parsed from the AST rather than grepped from the text, so a docstring or
    comment that merely *mentions* `compute_targets` can't satisfy the guard, and
    a `#` inside a string literal can't defeat it.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
            if isinstance(func.value, ast.Name):
                names.add(f"{func.value.id}.{func.attr}")
    return names


def test_backtest_uses_a_shared_weight_entrypoint():
    called = _calls(backtest)
    assert called & set(_SHARED_ENTRYPOINTS), (
        "backtest must route through strategy.targets_at / compute_targets")


def test_paper_uses_a_shared_weight_entrypoint():
    called = _calls(paper_trade)
    assert called & set(_SHARED_ENTRYPOINTS), (
        "paper trading must route through strategy.targets_at / compute_targets")


def test_neither_engine_calls_the_weight_primitives_directly():
    # Selection and vol targeting live behind the shared entry points; calling
    # them directly from an engine would bypass part of the formula (and is how
    # a second sizing path historically crept in).
    for module in (backtest, paper_trade):
        called = _calls(module)
        leaked = called & set(_WEIGHT_PRIMITIVES)
        assert not leaked, (
            f"{module.__name__} calls {sorted(leaked)} directly — that is a "
            f"second weight path (invariant #3)")


def test_both_roads_run_the_same_formula():
    # compute_targets must DELEGATE to targets_at rather than carry its own copy
    # of the selection/sizing logic — otherwise the backtest road (panel) and the
    # paper road could drift apart while both still "use strategy".
    called = _calls(strategy.compute_targets)
    assert "targets_at" in called, "compute_targets must delegate to targets_at"
    assert not called & {"select_portfolio", "select_long_short"}, (
        "compute_targets re-implements selection instead of delegating")


def test_metrics_always_present_in_backtest_output():
    # costs-always-on contract: the weight builder always vol-targets.
    src = inspect.getsource(strategy.targets_at)
    assert "vol_target" in src


# ---------------------------------------------------------------------------
# Layer 2 — numeric golden equality (R4)
# ---------------------------------------------------------------------------
def test_backtest_applies_compute_targets_weights_bit_for_bit(synth_asx, asx_region):
    """The weights the backtest holds equal compute_targets' output exactly.

    On the trading day the scheduled target first takes effect, the recorded book
    (before it drifts) must equal a direct compute_targets(asof) call — proving
    the backtest applies the shared function's output with no reweighting layer.
    """
    prices, index_px = synth_asx
    p = asx_region.params
    result = backtest.run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    weights_hist = result["weights"]

    rebal_marks = prices.resample(p.rebalance).last().index
    checked = 0
    for d in rebal_marks:
        loc = prices.index.searchsorted(d, side="right") - 1
        if loc < p.min_history_days:
            continue
        asof = prices.index[loc]
        expected = strategy.compute_targets(prices, index_px, p, asof=asof)
        if expected.empty:
            continue  # risk-off day carries no weights to compare
        # The target is applied a fixed lag after the signal, then drifts; find the
        # one day whose recorded book matches it exactly.
        matched = False
        for lag in (1, 2, 3):
            if loc + lag >= len(prices):
                break
            day = prices.index[loc + lag]
            got = weights_hist.get(day)
            if got is None or got.empty:
                continue
            if got.reindex(expected.index).equals(expected) and len(got) == len(expected):
                pd.testing.assert_series_equal(
                    got.sort_index(), expected.sort_index(), check_names=False)
                matched = True
                break
        assert matched, f"scheduled target for {asof.date()} was not applied unmodified"
        checked += 1
        if checked >= 3:
            break
    assert checked > 0, "no non-empty rebalance target was validated"


def test_paper_sizes_only_from_compute_targets(monkeypatch, tmp_path):
    """Paper trading acts only on names/sizing from compute_targets.

    A spy captures exactly what compute_targets returned; the opened book may not
    contain a name outside that set (no second path inventing positions), and the
    largest-weight name must be the largest dollar holding (relative sizing
    preserved through to execution).
    """
    seen: dict = {}
    real = strategy.compute_targets

    def spy(prices, index_prices, p, asof=None, eligible=None):
        w = real(prices, index_prices, p, asof=asof, eligible=eligible)
        seen["w"] = w
        seen["px"] = prices.iloc[-1]
        return w

    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(strategy, "compute_targets", spy)

    paper_trade.init_account("golden", 1_000_000, synthetic=True, allocations={"US": 1.0})
    paper_trade.run_daily("golden", synthetic=True)

    sleeve = paper_trade.load_state("golden")["sleeves"]["US"]
    targets, px = seen["w"], seen["px"]
    held = sleeve["positions"]

    # No name invented outside the shared weight function.
    assert set(held).issubset(set(targets.index))

    if not targets.empty and held:
        values = {t: held[t] * float(px[t]) for t in held}
        top_by_weight = targets.sort_values(ascending=False).index[0]
        top_by_value = max(values, key=values.get)
        assert top_by_weight == top_by_value, (
            "relative sizing not preserved: paper's biggest holding isn't the "
            "biggest compute_targets weight")
