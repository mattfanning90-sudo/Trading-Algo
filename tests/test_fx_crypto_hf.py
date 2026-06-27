"""High-frequency *capable* crypto path: data layer, profile, book + backtest.

This is minute-scale systematic crypto, NOT microsecond HFT — see
docs/CRYPTO_HF.md. Everything here runs offline on synthetic minute bars, so it
never needs `ccxt` or a network.
"""
import pandas as pd

from trading_algo.forex import crypto_data, fx_book, run_backtest
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_config import profile, profile_names


def test_hf_crypto_profile_exists():
    assert "hf_crypto" in profile_names()
    p = profile("hf_crypto")
    assert p.bar == "1m"
    assert p.ema_slow < profile("balanced").ema_slow         # shorter windows
    assert p.rebalance_min_delta > profile("balanced").rebalance_min_delta  # wider churn band


def test_crypto_symbol_maps():
    # Canonical id -> exchange spot / perpetual markets line up one-to-one.
    assert set(crypto_data.SPOT) == set(crypto_data.PERP) == set(crypto_data.CRYPTO_UNIVERSE)
    assert crypto_data.SPOT["BTCUSD"] == "BTC/USDT"
    assert crypto_data.PERP["BTCUSD"].endswith(":USDT")       # perp suffix


def test_synthetic_crypto_panel_is_minute_bars():
    panel = crypto_data.synthetic_crypto_panel(crypto_data.CRYPTO_UNIVERSE, days=2)
    assert set(panel) == set(crypto_data.CRYPTO_UNIVERSE)
    idx = panel["BTCUSD"].index
    assert len(idx) > 100
    # genuine intraday timestamps, and many bars share a calendar date
    assert len(set(idx.hour)) > 1
    assert idx.normalize().duplicated().any()
    # crypto runs hotter than FX: BTC level is in the thousands, not ~1.0
    assert panel["BTCUSD"]["close"].iloc[0] > 1000


def test_book_runs_via_exchange_synthetic(tmp_path, monkeypatch):
    """The --exchange path uses crypto_data; synthetic keeps it offline (no ccxt)."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("chf", 5_000, "hf_crypto",
                         symbols=crypto_data.CRYPTO_UNIVERSE)
    s0 = fx_book.load_state("chf")
    assert s0["symbols"] == crypto_data.CRYPTO_UNIVERSE       # crypto-only universe

    fx_book.run_once("chf", synthetic=True, pool=AgentPool(max_workers=1),
                     interval="1m", exchange="binance")
    s = fx_book.load_state("chf")
    assert s["last_bar_date"] and " " in s["last_bar_date"]   # minute timestamp key
    assert s["equity_history"]
    # a crypto book trades its own symbols only — no FX majors leaked in
    assert set(s["symbols"]) == set(crypto_data.CRYPTO_UNIVERSE)


def test_init_hf_crypto_via_cli(tmp_path, monkeypatch):
    """`paper --init --profile hf_crypto` should seed the crypto universe."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.main(["--init", "--account", "chf2", "--profile", "hf_crypto"])
    s = fx_book.load_state("chf2")
    assert s["profile"] == "hf_crypto"
    assert set(s["symbols"]) == set(crypto_data.CRYPTO_UNIVERSE)


def test_run_backtest_hf_crypto_synthetic(capsys):
    """End-to-end CLI: hf_crypto profile + 1m bars + synthetic, no network."""
    run_backtest.main(["--synthetic", "--profile", "hf_crypto", "--bar", "1m"])
    out = capsys.readouterr().out
    assert "FX backtest [hf_crypto]" in out
    assert "BTCUSD" in out                                    # crypto universe attribution


def test_daily_fx_book_unchanged(tmp_path, monkeypatch):
    """Regression: the default daily FX path keeps a plain date key, no exchange."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("d", 5_000, "balanced")
    fx_book.run_once("d", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("d")
    assert s["last_bar_date"] and " " not in s["last_bar_date"]
    # daily book still picks up the FX majors (union with DEFAULT_UNIVERSE)
    assert "EURUSD" in s["symbols"]
