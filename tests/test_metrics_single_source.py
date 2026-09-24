"""Design §4 enforcement: ONE module owns every number a human reads.

The same trick `tests/test_consistency.py` uses for the weight primitives,
applied to the measurement primitives. An AST walk over `trading_algo/**.py`
fails when any module outside `metrics.py` computes a Sharpe, a drawdown or an
annualisation for itself — because six Sharpe implementations are not six bugs,
they are one missing definition, and the repo's own Sharpe study measured two
of the surviving conventions disagreeing by 0.45 on a single series.

Two allowlists, and the difference between them matters:

* `_MEASUREMENT_INPUTS` is PERMANENT. A vol that sizes a position, a per-period
  Sharpe that feeds PSR/DSR, a gradient's own sigma and an evolutionary fitness
  score are not numbers a human reads — spec §4's boundary keeps their inline
  maths. Any of their values that IS displayed must be re-derived through the
  layer for display.
* `_PENDING_DISPLAY_SITES` is a list of open findings: display sites that still
  compute for themselves and have not been routed yet. Emptying it is the goal.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "trading_algo"

# Measurement INPUTS — permanent (spec §4: optimisation internals keep their
# inline maths). "*" exempts the whole module.
_MEASUREMENT_INPUTS: dict[str, object] = {
    "forex/nn.py": "*",                 # sharpe_net loss needs its own gradient
    "forex/evolve.py": "*",             # fitness IS a selection score
    "forex/research.py": {"run_research"},        # candidate-edge search score
    "validation.py": {"sharpe_ratio",             # per-period SR: the PSR/DSR input
                      "probabilistic_sharpe_ratio"},
    "signals.py": {"realised_vol"},               # the vol-targeting denominator
    "crowding.py": {"crowding_report"},           # regime gate input
    "data_quality.py": {"assess"},                # feed-health gate input
    "forex/dashboard.py": {"_min_track_record_days"},   # Bailey/LdP MinTRL
}

# Open findings — display sites not yet routed through the layer. Each is a
# number a human reads, computed locally. Remove an entry when its task lands.
_PENDING_DISPLAY_SITES: dict[str, set[str]] = {
    "forex/ml_backtest.py": {"_annual_metrics"},   # walk-forward report card
}


def _allowed(rel: str) -> object:
    a = _MEASUREMENT_INPUTS.get(rel)
    b = _PENDING_DISPLAY_SITES.get(rel)
    if a == "*":
        return "*"
    return set(a or set()) | set(b or set())


def _attr_call(node, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name)


def _is_252(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == 252


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, kind) for every independent measurement computation."""
    out: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if _attr_call(n, "sqrt") and n.args and _is_252(n.args[0]):
            out.append((n.lineno, "sqrt(252)"))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult) and (
                _is_252(n.left) or _is_252(n.right)):
            out.append((n.lineno, "* 252"))
        elif _attr_call(n, "cummax"):
            out.append((n.lineno, "cummax() — a drawdown"))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            den = n.right
            if isinstance(den, ast.BinOp) and isinstance(den.op, ast.Mult):
                den = den.left                    # the `sd * sqrt(ann)` shape
            if _attr_call(den, "std") and any(_attr_call(k, "mean")
                                              for k in ast.walk(n.left)):
                out.append((n.lineno, "mean/std — a Sharpe"))
    return out


def _owner(tree: ast.AST, line: int) -> str:
    """Innermost function enclosing `line`, or '<module>'."""
    best = None
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if f.lineno <= line <= (f.end_lineno or f.lineno):
                if best is None or f.lineno > best.lineno:
                    best = f
    return best.name if best else "<module>"


def test_no_independent_metric_implementation():
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "metrics.py":
            continue                       # THE definition
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allow = _allowed(rel)
        for line, kind in _offences(tree):
            fn = _owner(tree, line)
            if allow == "*" or fn in allow:
                continue
            bad.append(f"trading_algo/{rel}:{line} in {fn}() — {kind}")
    assert not bad, (
        "These compute a measure for themselves instead of calling "
        "trading_algo.metrics:\n  " + "\n  ".join(bad))


def test_allowlisted_modules_exist():
    """An allowlist that names a deleted file silently stops guarding it."""
    for rel in {**_MEASUREMENT_INPUTS, **_PENDING_DISPLAY_SITES}:
        assert (ROOT / rel).is_file(), rel


def test_fx_page_has_no_javascript_sharpe():
    """The page renders values computed in Python; it does no maths of its own."""
    from trading_algo.forex import dashboard
    page = dashboard._PAGE
    assert "Math.sqrt" not in page              # no JS vol / Sharpe
    assert "RF=0.035" not in page               # no second cash rate
    assert "book_period_metrics" in page        # it reads Python's numbers
