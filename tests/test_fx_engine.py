"""FX engine: market-hours gate, bounded loop, benchmark."""
import asyncio
from datetime import datetime, timezone

import pytest

from trading_algo.forex import engine, fx_book
from trading_algo.forex.agents import AgentPool


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


def test_market_open_gate():
    # Saturday -> closed
    assert not engine.fx_market_open(datetime(2024, 1, 6, 12, tzinfo=timezone.utc))
    # Wednesday midday -> open
    assert engine.fx_market_open(datetime(2024, 1, 3, 12, tzinfo=timezone.utc))
    # Sunday 23:00 UTC -> open (week has begun)
    assert engine.fx_market_open(datetime(2024, 1, 7, 23, tzinfo=timezone.utc))
    # Friday 23:00 UTC -> closed (week has ended)
    assert not engine.fx_market_open(datetime(2024, 1, 5, 23, tzinfo=timezone.utc))


def test_run_loop_bounded(isolated_state):
    fx_book.init_account("matt", 5_000, "balanced")
    pool = AgentPool(max_workers=1)
    asyncio.run(engine.run_loop("matt", synthetic=True, pool=pool,
                                interval=0.0, max_cycles=2))
    assert fx_book.load_state("matt")["last_bar_date"] is not None


def test_benchmark_returns_positive_latency():
    med = engine.benchmark(synthetic=True, workers=1, runs=2)
    assert med > 0


# --- the ML gate: one mechanism pinning the neural pool to its training set ---
def test_ml_gate_pins_ml_pool_to_daily_default_books(isolated_state, monkeypatch):
    """With use_ml=True the gate ALWAYS decides: daily-bar unlocked-universe
    books (matt/partner) get the ML pool; daytrader (60m) and multiasset
    (universe_locked) get the EXACT caller pool (identity — locks the --workers
    guarantee). With use_ml=False the caller's pool is used unconditionally."""
    import pandas as pd

    from trading_algo.forex import pairs

    fx_book.init_defaults(synthetic=True)
    caller_pool = AgentPool(max_workers=2)          # sentinel: the --workers pool
    ml_sentinel = AgentPool(max_workers=1)          # sentinel: the neural pool
    ml_calls = []
    monkeypatch.setattr(fx_book, "ml_pool",
                        lambda models_dir=None: ml_calls.append(1) or ml_sentinel)
    monkeypatch.setattr(fx_book, "_ML_POOL", None)  # reset the process memo

    current = {"acct": None}
    orig_run_once = fx_book.run_once

    def spy_run_once(account, *a, **kw):
        current["acct"] = account
        return orig_run_once(account, *a, **kw)

    monkeypatch.setattr(fx_book, "run_once", spy_run_once)

    used: dict[str, tuple] = {}

    def fake_decide(panel, p, pool=None):
        used[current["acct"]] = (pool, set(panel))
        return pd.Series(dtype=float), {}

    monkeypatch.setattr(fx_book.explain, "decide_and_explain", fake_decide)

    fx_book.run_all(synthetic=True, use_ml=True, pool=caller_pool)
    assert used["matt"][0] is ml_sentinel
    assert used["partner"][0] is ml_sentinel
    assert used["daytrader"][0] is caller_pool      # 60m bar -> gated OUT
    assert used["multiasset"][0] is caller_pool     # locked universe -> gated OUT
    assert len(ml_calls) == 1                       # memoized: loaded once
    # The FX-trained pool was never asked to score anything outside the
    # default FX+crypto universe it was trained on (issue 40's assertion).
    for acct, (pool, syms) in used.items():
        if pool is ml_sentinel:
            assert syms <= set(pairs.DEFAULT_UNIVERSE)

    # Second pass, use_ml=False: caller's pool everywhere, ml_pool untouched.
    used.clear()
    fx_book.run_all(synthetic=True, use_ml=False, pool=caller_pool)
    assert set(used) == {"matt", "partner", "daytrader", "multiasset"}
    for acct, (pool, _syms) in used.items():
        assert pool is caller_pool, acct
    assert len(ml_calls) == 1                       # never called again
