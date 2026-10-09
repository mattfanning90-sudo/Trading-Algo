"""Shared fixtures."""
import pandas as pd
import pytest

from trading_algo import data
from trading_algo.config import DEFAULT_PARAMS
from trading_algo.regions import get_region


@pytest.fixture
def params():
    return DEFAULT_PARAMS


@pytest.fixture
def asx_region():
    return get_region("ASX")


@pytest.fixture
def synth_asx():
    """(prices, index) synthetic ASX history."""
    region = get_region("ASX")
    return data.synthetic_region(region, start="2014-01-01", end="2024-01-01")


@pytest.fixture
def small_frame():
    """A tiny deterministic price frame for signal causality checks."""
    idx = pd.bdate_range("2020-01-01", periods=400)
    cols = [f"S{i}" for i in range(6)]
    # monotone-ish trending series with different slopes
    data_ = {c: [100 * (1 + 0.0005 * (j + 1) * (i + 1) ** 0.5) for j in range(len(idx))]
             for i, c in enumerate(cols)}
    return pd.DataFrame(data_, index=idx)


@pytest.fixture
def paper_cycle(monkeypatch):
    """Advance a paper book across DISTINCT synthetic sessions.

    Fills land on the bar after the decision, so a book needs two runs on two
    different bars to open a position — and `data.synthetic_region` hands back
    the same fixed panel on every call. This serves that panel one session
    longer each time the book is advanced.

    `cycle(account)` runs one full decide -> fill cycle. Each cycled run first
    moves to a NEW session, so calling `paper_trade.run_daily` directly stays on
    the session the last cycled run used — which is how a test reproduces the
    engine firing several times in one day.
    """
    from trading_algo import paper_trade as pt

    real = pt.latest_region_data
    clock = {"session": 0}            # the session a bare run_daily lands on
    headroom = 8                      # sessions of runway before the panel ends

    def advancing(region, synthetic):
        prices, index_px = real(region, synthetic)
        if not synthetic:
            return prices, index_px
        cut = min(len(prices), len(prices) - headroom + clock["session"])
        return prices.iloc[:cut], index_px.loc[:prices.index[cut - 1]]

    monkeypatch.setattr(pt, "latest_region_data", advancing)

    def cycle(account, sessions=2):
        for _ in range(sessions):
            clock["session"] += 1     # a new bar, then the run on it
            pt.run_daily(account, synthetic=True)
        return pt.load_state(account)

    return cycle
