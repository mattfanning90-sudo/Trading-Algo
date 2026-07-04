"""Ensemble blending + risk sizing (vol target, per-pair cap, gross cap)."""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import ensemble, risk
from trading_algo.forex.agents import AgentPool, PairContext
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import closes, synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE, get_pair


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2018-01-01", end="2024-01-01")


@pytest.fixture
def params():
    return profile("balanced")


@pytest.fixture
def tilts(panel, params):
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    signals = AgentPool(max_workers=1).evaluate(panel, contexts, params)
    rets = closes(panel).pct_change(fill_method=None)
    return ensemble.ensemble_tilts(signals, rets, params)


def test_tilts_in_range(tilts):
    assert tilts.abs().max().max() <= 1.0 + 1e-9


def test_equal_vs_adaptive_differ(panel, params):
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    signals = AgentPool(max_workers=1).evaluate(panel, contexts, params)
    rets = closes(panel).pct_change(fill_method=None)
    eq = ensemble.ensemble_tilts(signals, rets, params.with_overrides(agent_weighting="equal"))
    ad = ensemble.ensemble_tilts(signals, rets, params.with_overrides(agent_weighting="adaptive"))
    assert not np.allclose(eq.fillna(0).values, ad.fillna(0).values)


def test_per_pair_cap_enforced(tilts, panel, params):
    vols = risk.pair_vols(panel, params)
    w = risk.size_book(tilts, vols, params)
    assert w.abs().max().max() <= params.per_pair_cap + 1e-9


def test_gross_leverage_cap_enforced(tilts, panel, params):
    vols = risk.pair_vols(panel, params)
    w = risk.size_book(tilts, vols, params)
    assert w.abs().sum(axis=1).max() <= params.max_gross + 1e-6


def test_vol_targeting_scales_down_high_vol():
    """Doubling realised vol should not increase the sized book."""
    p = profile("balanced")
    idx = pd.bdate_range("2020-01-01", periods=50)
    cols = DEFAULT_UNIVERSE
    tilts = pd.DataFrame(0.5, index=idx, columns=cols)
    lo = pd.DataFrame(0.08, index=idx, columns=cols)
    hi = pd.DataFrame(0.16, index=idx, columns=cols)
    w_lo = risk.size_book(tilts, lo, p).abs().sum(axis=1).iloc[-1]
    w_hi = risk.size_book(tilts, hi, p).abs().sum(axis=1).iloc[-1]
    assert w_hi <= w_lo + 1e-9


def test_crypto_gross_cap_enforced():
    """Total crypto gross (Σ|w| over BTC/ETH/SOL) is capped as one correlated bet;
    FX legs are untouched by the crypto scaling; None disables the cap."""
    p = profile("balanced")            # crypto_gross_cap = 0.25
    idx = pd.bdate_range("2020-01-01", periods=30)
    cols = DEFAULT_UNIVERSE
    tilts = pd.DataFrame(0.0, index=idx, columns=cols)
    tilts[["BTCUSD", "ETHUSD", "SOLUSD"]] = 0.9      # crypto screaming long
    tilts["EURUSD"] = 0.4
    vols = pd.DataFrame(0.10, index=idx, columns=cols)
    w = risk.size_book(tilts, vols, p)
    crypto = w[["BTCUSD", "ETHUSD", "SOLUSD"]].abs().sum(axis=1)
    assert crypto.max() <= p.crypto_gross_cap + 1e-9
    # crypto legs scaled proportionally (equal tilts stay equal)
    assert np.allclose(w["BTCUSD"], w["ETHUSD"])
    # the FX leg is NOT shrunk by the crypto cap (same as with cap disabled)
    w_off = risk.size_book(tilts, vols, p.with_overrides(crypto_gross_cap=None))
    assert np.allclose(w["EURUSD"], w_off["EURUSD"])
    # ...and with the cap off, crypto gross exceeds the capped level
    assert w_off[["BTCUSD", "ETHUSD", "SOLUSD"]].abs().sum(axis=1).max() > p.crypto_gross_cap


def test_hf_crypto_profile_uncapped():
    """The crypto-ONLY profile must not be strangled by the asset-class cap."""
    p = profile("hf_crypto")
    assert p.crypto_gross_cap is None
