"""The $10k day-trading book and the stock+bond multi-asset book.

daytrader  — intraday profile, 60m bars, hourly cadence (per-book bar).
multiasset — US equities + bond ETFs + an AUDUSD overlay, daily bars, with a
             LOCKED universe (the FX+crypto default must not leak in).
All offline/synthetic — no network.
"""
import pytest

from trading_algo.forex import fx_book
from trading_algo.forex import fx_config as cfg
from trading_algo.forex import pairs
from trading_algo.forex.agents import AgentPool


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


# --- registry ---------------------------------------------------------------
def test_bond_etfs_registered():
    assert pairs.BOND_UNIVERSE == ["TLT", "IEF", "AGG", "SHY"]
    tlt = pairs.get_pair("TLT")
    assert tlt.quote == "USD" and tlt.pip == 0.01
    assert tlt.swap_long_pips == 0.0                    # financing not modelled
    assert tlt.spread_fraction(95.0) > 0


def test_multi_asset_universe_composition():
    u = pairs.MULTI_ASSET_UNIVERSE
    assert set(pairs.EQUITY_UNIVERSE) <= set(u)          # stocks
    assert set(pairs.BOND_UNIVERSE) <= set(u)            # bonds
    assert "AUDUSD" in u                                 # AUD hub / currency overlay
    assert "EURUSD" not in u and "BTCUSD" not in u       # no FX majors / crypto
    # the default FX book universe is untouched
    assert pairs.DEFAULT_UNIVERSE == [*pairs.PAIRS, *pairs.CRYPTO]


# --- config-driven book creation ---------------------------------------------
def test_init_defaults_creates_all_four_books(isolated):
    fx_book.init_defaults(synthetic=True)
    assert set(fx_book.list_accounts()) == {"matt", "partner", "daytrader", "multiasset"}
    day = fx_book.load_state("daytrader")
    assert day["initial_capital"] == 10_000.0            # the $10k funding
    assert day["profile"] == "intraday" and day["bar"] == "60m"
    assert day["symbols"] == pairs.DEFAULT_UNIVERSE      # FX+crypto, day cadence
    ma = fx_book.load_state("multiasset")
    assert ma["initial_capital"] == 10_000.0
    assert ma["bar"] == "1d" and ma["universe_locked"] is True
    assert set(ma["symbols"]) == set(pairs.MULTI_ASSET_UNIVERSE)
    # legacy books keep the open (un-locked) universe behaviour
    assert fx_book.load_state("matt")["universe_locked"] is False


# --- per-book cadence ----------------------------------------------------------
def test_daytrader_runs_on_its_own_hourly_bar(isolated):
    fx_book.init_defaults(synthetic=True)
    # interval=None -> the book's stored bar (60m) drives the run
    fx_book.run_once("daytrader", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("daytrader")
    assert s["last_bar_date"] and " " in s["last_bar_date"]     # hourly key
    assert s["equity_history"]


def test_multiasset_universe_stays_locked(isolated):
    fx_book.init_defaults(synthetic=True)
    fx_book.run_once("multiasset", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("multiasset")
    assert s["last_bar_date"] and " " not in s["last_bar_date"]  # daily key
    assert set(s["symbols"]) == set(pairs.MULTI_ASSET_UNIVERSE)  # no FX/crypto leak
    for t in s["trades"]:
        assert t["pair"] in pairs.MULTI_ASSET_UNIVERSE


def test_run_all_uses_each_books_cadence(isolated):
    fx_book.init_defaults(synthetic=True)
    fx_book.run_all(synthetic=True, pool=AgentPool(max_workers=1))   # interval=None
    assert " " in fx_book.load_state("daytrader")["last_bar_date"]   # hourly
    assert " " not in fx_book.load_state("matt")["last_bar_date"]    # daily
    assert " " not in fx_book.load_state("multiasset")["last_bar_date"]


def test_cli_init_stores_bar(isolated):
    """--init --account must pass --bar through to the stored state (issue: the
    CLI parsed --bar but silently dropped it, pinning every CLI book to 1d)."""
    fx_book.main(["--init", "--account", "day2", "--profile", "intraday",
                  "--bar", "60m"])
    assert fx_book.load_state("day2")["bar"] == "60m"
    fx_book.main(["--init", "--account", "day3", "--profile", "intraday"])
    assert fx_book.load_state("day3")["bar"] == "1d"


# --- drawdown cooldown scaled to the bar cadence -------------------------------
def test_intraday_cooldown_rescaled_to_hourly_bars():
    # counter decrements once per NEW BAR: 240 hourly bars = 10 trading days
    assert cfg.profile("intraday").drawdown_cooldown_days == 240
    assert cfg.profile("balanced").drawdown_cooldown_days == 10


def test_halted_book_decrements_cooldown_once_per_bar(isolated):
    fx_book.init_defaults(synthetic=True)
    cooldown = cfg.profile("intraday").drawdown_cooldown_days
    s = fx_book.load_state("daytrader")
    s["risk_halted"] = True
    s["halt_cooldown"] = cooldown
    fx_book.save_state("daytrader", s)
    fx_book.run_once("daytrader", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("daytrader")
    assert s["halt_cooldown"] == cooldown - 1              # exactly one bar
    assert s["risk_halted"] is True                        # still cooling off
    assert s["positions"] == {}                            # flat while halted


def test_explicit_bar_still_overrides(isolated):
    fx_book.init_defaults(synthetic=True)
    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1),
                     interval="60m")
    assert " " in fx_book.load_state("matt")["last_bar_date"]


# --- dashboard renders the hourly book ----------------------------------------
def test_dashboard_handles_hourly_book(isolated):
    from trading_algo.forex import dashboard
    fx_book.init_defaults(synthetic=True)
    fx_book.run_once("daytrader", synthetic=True, pool=AgentPool(max_workers=1))
    p = dashboard.build_payload("daytrader", synthetic=True)
    assert p["books"] == ["daytrader", "matt", "multiasset", "partner"]
    assert p["book_curve"]                                # hourly keys accepted
    out = dashboard.export_account("daytrader", synthetic=True)
    assert "const toT" in out                              # LWC time normaliser
    assert "fmtT" in out


def test_curve_metrics_annualise_by_actual_spacing():
    """Hourly bars must not be annualised as if daily (√252 on hourly returns)."""
    from trading_algo.forex.dashboard import _curve_metrics
    import numpy as np
    rng = np.random.default_rng(0)
    vals = list(5000 * np.cumprod(1 + rng.normal(0, 0.001, 200)))
    daily = [f"2025-{1+i//28:02d}-{1+i%28:02d}" for i in range(200)]
    hourly = [f"2025-01-{1+i//24:02d} {i%24:02d}:00" for i in range(200)]
    v_d = _curve_metrics(daily, vals)["vol"]
    v_h = _curve_metrics(hourly, vals)["vol"]
    # same per-bar returns, ~√(24×365/252) ≈ 5.9x higher annualised vol hourly
    assert v_h > v_d * 3
