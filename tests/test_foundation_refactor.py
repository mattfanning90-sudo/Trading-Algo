"""Foundational refactor guards (R1/R2/R3/I1).

These pin the invariants of the chief-engineer refactor so the shared base,
the unit-explicit cooldown, the folded-in region side-tables and the new
account lock cannot silently regress. Everything here is behavior-preserving —
the numbers must match what the pre-refactor side-tables produced.
"""
from __future__ import annotations

import fcntl
import os

import pytest

from trading_algo import config as cfg
from trading_algo import fx, profiles, storage
from trading_algo import data_quality as dq
from trading_algo.regions import get_region
from trading_algo.forex import fx_config as fxcfg


# --- R1: shared risk/params base -------------------------------------------
def test_strategy_and_fx_params_share_a_risk_base():
    """Both parameter dataclasses descend from the one shared RiskParams base,
    so the vol-targeting knobs (target_vol/vol_lookback/max_gross/…) are defined
    in ONE place."""
    assert issubclass(cfg.StrategyParams, cfg.RiskParams)
    assert issubclass(fxcfg.FXParams, cfg.RiskParams)
    for field in ("target_vol", "vol_lookback", "avg_correlation",
                  "max_gross", "max_vol_scale"):
        assert field in cfg.RiskParams.__dataclass_fields__


def test_public_param_defaults_unchanged():
    """The base must not change any existing default (behavior-preserving)."""
    sp = cfg.StrategyParams()
    assert (sp.target_vol, sp.vol_lookback, sp.avg_correlation,
            sp.max_gross, sp.max_vol_scale) == (0.12, 63, 0.6, 1.0, 1.5)
    fp = fxcfg.FXParams()
    assert (fp.target_vol, fp.vol_lookback, fp.avg_correlation,
            fp.max_gross, fp.max_vol_scale) == (0.10, 30, 0.30, 3.0, 3.0)


def test_with_overrides_returns_same_subclass():
    assert isinstance(cfg.StrategyParams().with_overrides(top_n=3), cfg.StrategyParams)
    assert isinstance(fxcfg.FXParams().with_overrides(max_gross=2.0), fxcfg.FXParams)


def test_profile_registries_share_one_lookup_pattern():
    """Both named-profile registries route through the shared lookup helper and
    keep their public accessors + error semantics."""
    assert profiles.get_profile("ultra").key == "ultra"
    assert fxcfg.profile("balanced").target_vol == 0.10
    assert set(fxcfg.profile_names()) >= {"balanced", "conservative", "intraday"}
    # equity registry stays CLI-friendly (SystemExit); fx registry raises KeyError
    with pytest.raises(SystemExit):
        profiles.get_profile("nope")
    with pytest.raises(KeyError):
        fxcfg.profile("nope")


# --- R2: unit-explicit, shared cooldown ------------------------------------
def test_cooldown_carries_an_explicit_unit():
    assert cfg.DRAWDOWN_COOLDOWN.unit == cfg.COOLDOWN_MARKET_DAYS
    assert cfg.DRAWDOWN_COOLDOWN.length == 21
    # FX cooldown is measured in BARS, not market days
    assert fxcfg.FXParams().cooldown.unit == cfg.COOLDOWN_BARS


def test_cooldown_lengths_unchanged_for_existing_profiles():
    """Effective cooldown must not move for any existing profile."""
    assert cfg.DRAWDOWN_COOLDOWN_DAYS == 21
    assert fxcfg.profile("balanced").drawdown_cooldown_days == 10
    assert fxcfg.profile("intraday").drawdown_cooldown_days == 240
    assert fxcfg.profile("intraday").cooldown.length == 240
    # the shared interpreter reads a Cooldown into its decrement-step count
    assert cfg.cooldown_steps(fxcfg.profile("intraday").cooldown) == 240
    assert cfg.cooldown_steps(cfg.DRAWDOWN_COOLDOWN) == 21


# --- R3: side-tables folded into the Region/currency record ----------------
def test_jump_threshold_lives_on_the_region_record():
    assert get_region("FTSE").jump_threshold == 0.30      # GBP tighter
    for key in ("US", "ASX", "TSX"):
        assert get_region(key).jump_threshold == 0.50
    # data_quality reads it off the record (no currency side-table)
    assert dq._jump_threshold(get_region("FTSE")) == 0.30
    assert dq._jump_threshold(get_region("US")) == 0.50


def test_synth_fx_level_sourced_from_region_record():
    """The synthetic FX anchors come from the Region records, not a hardcoded
    currency dict. Region currencies (USD/GBP/CAD) resolve to their record's
    anchor; the base is identity."""
    assert fx.synth_level("AUD") == 1.0
    assert fx.synth_level("USD") == get_region("US").synthetic_fx_anchor
    assert fx.synth_level("GBP") == get_region("FTSE").synthetic_fx_anchor
    assert fx.synth_level("CAD") == get_region("TSX").synthetic_fx_anchor
    # anchors are unchanged from the old side-table values (behavior-preserving)
    tbl = fx.synthetic_fx(["AUD", "USD", "GBP", "CAD"],
                          start="2020-01-01", end="2020-02-01")
    assert (tbl["AUD"] == 1.0).all()
    assert (tbl[["USD", "GBP", "CAD"]] > 0).all().all()


def test_fx6_prefixes_are_derived_not_hardcoded():
    """The dashboard's pair-like detector derives its currency/asset prefixes
    from the pair registry rather than a frozen literal."""
    from trading_algo.dashboard import registry
    prefixes = set(registry._pair_prefixes())
    assert {"EUR", "GBP", "USD", "BTC", "ETH"} <= prefixes
    assert registry._is_pairlike("EURUSD")
    assert registry._is_pairlike("BTCUSD")
    assert not registry._is_pairlike("AAPL")


def test_equity_universes_map_removed_dead_code():
    """The unused convenience map is gone; the per-region lists remain."""
    from trading_algo import universes
    assert not hasattr(universes, "UNIVERSES")
    assert universes.US and universes.FTSE and universes.ASX and universes.TSX


# --- I1: account lock ------------------------------------------------------
def test_account_lock_is_exclusive(tmp_path):
    lock_dir = str(tmp_path)
    with storage.account_lock("acct", lock_dir=lock_dir):
        # while held, a second exclusive acquire on the same lockfile must fail
        path = os.path.join(lock_dir, "acct.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    # released on exit: the same lock can be re-acquired
    with storage.account_lock("acct", lock_dir=lock_dir):
        pass


def test_account_lock_releases_on_exception(tmp_path):
    lock_dir = str(tmp_path)
    with pytest.raises(ValueError):
        with storage.account_lock("acct", lock_dir=lock_dir):
            raise ValueError("boom")
    # not left locked
    with storage.account_lock("acct", lock_dir=lock_dir):
        pass


def test_account_lock_creates_missing_dir(tmp_path):
    lock_dir = str(tmp_path / "nested" / "locks")
    with storage.account_lock("acct", lock_dir=lock_dir):
        assert os.path.isdir(lock_dir)
