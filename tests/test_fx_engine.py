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
