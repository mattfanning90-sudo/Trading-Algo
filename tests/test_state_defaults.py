"""Local defaults discover the tracked paper books without environment setup."""
import os
from pathlib import Path
import subprocess
import sys


def test_default_state_directory_is_shared_and_dashboard_discovers_books():
    env = os.environ.copy()
    env.pop("MOMENTUM_STATE_DIR", None)
    env.pop("FX_STATE_DIR", None)
    expected = Path(__file__).resolve().parents[1] / "state"
    code = f"""
from pathlib import Path
from trading_algo import paper_trade, run_backtest
from trading_algo.dashboard import registry
from trading_algo.forex import fx_book

expected = Path({str(expected)!r}).resolve()
actual = [
    Path(paper_trade.STATE_DIR).resolve(),
    Path(fx_book.STATE_DIR).resolve(),
    Path(run_backtest._STATE_DIR).resolve(),
]
assert actual == [expected, expected, expected], (actual, expected)
assert registry.resolve("FULL") is not None
assert registry.resolve("MATT") is not None
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=expected.parent,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
