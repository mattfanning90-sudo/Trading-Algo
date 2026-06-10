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
