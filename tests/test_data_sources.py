"""Pluggable market-data sources (yahoo / crypto / oanda / alpaca / openbb).

Everything here runs offline on synthetic data, so it never needs the optional
live-feed dependencies (ccxt / oandapyV20 / alpaca-py / openbb) or a network.
"""
import pandas as pd
import pytest

from trading_algo.forex import (alpaca_data, feeds, fx_book, oanda_data,
                                 openbb_data, run_backtest)
from trading_algo.forex import pairs
from trading_algo.forex.agents import AgentPool


# --- source resolution ------------------------------------------------------
def test_sources_listed():
    assert feeds.SOURCES == ["yahoo", "crypto", "oanda", "alpaca", "openbb"]


def test_resolve_source_exchange_implies_crypto():
    assert feeds.resolve_source(None, None) == "yahoo"
    assert feeds.resolve_source(None, "binance") == "crypto"   # back-compat
    assert feeds.resolve_source("oanda", None) == "oanda"


def test_resolve_unknown_source_raises():
    with pytest.raises(SystemExit):
        feeds.resolve_source("bloomberg", None)


def test_default_universe_per_source():
    assert feeds.default_universe("oanda") == list(pairs.PAIRS)
    assert feeds.default_universe("alpaca") == pairs.EQUITY_UNIVERSE
    assert feeds.default_universe("openbb") == pairs.EQUITY_UNIVERSE
    assert "BTCUSD" in feeds.default_universe("crypto")
    assert feeds.default_universe("yahoo") == pairs.DEFAULT_UNIVERSE
    # the hf_crypto profile forces the crypto universe even under yahoo
    assert "BTCUSD" in feeds.default_universe("yahoo", "hf_crypto")


# --- equity registry --------------------------------------------------------
def test_equities_registered_but_not_in_default_universe():
    assert pairs.EQUITY_UNIVERSE and "AAPL" in pairs.EQUITY_UNIVERSE
    aapl = pairs.get_pair("AAPL")
    assert aapl.quote == "USD" and aapl.pip == 0.01
    assert aapl.swap_long_pips == 0.0          # equity carry not modelled
    assert aapl.spread_fraction(195.0) > 0
    # equities stay OUT of the default FX+crypto universe
    assert "AAPL" not in pairs.DEFAULT_UNIVERSE


# --- OANDA symbol mapping ---------------------------------------------------
def test_oanda_instrument_mapping():
    assert oanda_data.instrument("EURUSD") == "EUR_USD"
    assert oanda_data.instrument("USDJPY") == "USD_JPY"
    assert oanda_data.OANDA_UNIVERSE == list(pairs.PAIRS)


# --- synthetic panels per source (offline; no optional deps needed) ---------
@pytest.mark.parametrize("mod,syms,tf", [
    (oanda_data, ["EURUSD", "GBPUSD"], "1h"),
    (alpaca_data, ["AAPL", "MSFT"], "1h"),
    (openbb_data, ["AAPL", "SPY"], "1d"),
])
def test_source_synthetic_panel_shape(mod, syms, tf):
    panel = mod.synthetic_panel(syms, timeframe=tf, days=4)
    assert set(panel) == set(syms)
    for df in panel.values():
        assert list(df.columns) == ["open", "high", "low", "close"]
        assert len(df) > 10


def test_feeds_load_dispatches_synthetic():
    # crypto -> minute bars in the thousands; alpaca -> equities; oanda -> FX
    crypto = feeds.load(["BTCUSD"], synthetic=True, interval="1m", source="crypto")
    assert crypto["BTCUSD"]["close"].iloc[0] > 1000
    eq = feeds.load(["AAPL"], synthetic=True, interval="1h", source="alpaca")
    assert 50 < eq["AAPL"]["close"].iloc[0] < 1000
    fx = feeds.load(["EURUSD"], synthetic=True, interval="1d", source="oanda")
    assert 0.5 < fx["EURUSD"]["close"].iloc[0] < 2.0


# --- end-to-end through the book + backtest (synthetic) ---------------------
def test_book_runs_on_alpaca_equities(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("eq", 5_000, "intraday",
                         symbols=pairs.EQUITY_UNIVERSE, source="alpaca")
    s0 = fx_book.load_state("eq")
    assert s0["source"] == "alpaca"
    fx_book.run_once("eq", synthetic=True, pool=AgentPool(max_workers=1),
                     interval="1h", source="alpaca")
    s = fx_book.load_state("eq")
    assert s["equity_history"]
    # an equity book trades equities only — no FX majors leaked in
    assert set(s["symbols"]) == set(pairs.EQUITY_UNIVERSE)


def test_init_cli_sets_source_and_universe(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.main(["--init", "--account", "fx1", "--source", "oanda", "--profile", "intraday"])
    s = fx_book.load_state("fx1")
    assert s["source"] == "oanda"
    assert set(s["symbols"]) == set(pairs.PAIRS)


def test_run_backtest_alpaca_synthetic(capsys):
    run_backtest.main(["--synthetic", "--source", "alpaca", "--bar", "1h"])
    out = capsys.readouterr().out
    assert "FX backtest [balanced]" in out
    assert "AAPL" in out               # equity universe attribution


def test_daily_yahoo_book_unchanged(tmp_path, monkeypatch):
    """Regression: the default daily path stays yahoo + FX majors, plain date key."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("d", 5_000, "balanced")
    s0 = fx_book.load_state("d")
    assert s0["source"] == "yahoo"
    fx_book.run_once("d", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("d")
    assert s["last_bar_date"] and " " not in s["last_bar_date"]
    assert "EURUSD" in s["symbols"]
