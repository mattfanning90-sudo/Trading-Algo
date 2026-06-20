"""Intraday (medium-frequency) mode: data, profile, and book bar-keying.

NB: this is medium-frequency, NOT high-frequency — see docs/HFT_REALITY.md.
"""
import pytest

from trading_algo.forex import fx_book
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_config import profile, profile_names
from trading_algo.forex.fx_data import synthetic_panel


def test_intraday_profile_exists():
    assert "intraday" in profile_names()
    p = profile("intraday")
    assert p.bar == "60m"
    assert p.ema_slow < profile("balanced").ema_slow      # shorter windows


def test_synthetic_intraday_bars():
    panel = synthetic_panel(["EURUSD"], start="2025-01-01", end="2025-01-10", freq="60m")
    idx = panel["EURUSD"].index
    assert len(idx) > 20
    assert len(set(idx.hour)) > 1                          # genuinely intraday timestamps
    # multiple bars share a calendar date
    assert idx.normalize().duplicated().any()


def test_bar_key_resolution():
    import pandas as pd
    ts = pd.Timestamp("2026-06-20 14:00")
    assert fx_book._bar_key(ts, "1d") == "2026-06-20"            # daily unchanged
    assert fx_book._bar_key(ts, "60m") == "2026-06-20 14:00"     # intraday keeps time


def test_book_runs_intraday(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("intr", 5_000, "intraday")
    fx_book.run_once("intr", synthetic=True, pool=AgentPool(max_workers=1), interval="60m")
    s = fx_book.load_state("intr")
    assert s["last_bar_date"] and " " in s["last_bar_date"]      # timestamp key, not just a date
    assert s["equity_history"]


def test_daily_book_unchanged(tmp_path, monkeypatch):
    """Daily path must keep using a plain date key (no regression for live books)."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("d", 5_000, "balanced")
    fx_book.run_once("d", synthetic=True, pool=AgentPool(max_workers=1))   # default interval=1d
    s = fx_book.load_state("d")
    assert s["last_bar_date"] and " " not in s["last_bar_date"]  # date only
