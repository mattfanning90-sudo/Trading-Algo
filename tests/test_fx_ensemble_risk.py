"""Ensemble blending + risk sizing (vol target, per-pair cap, gross cap)."""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import ensemble, indicators as ind, marks, risk
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


def test_pair_vols_uses_bar_frequency_annualization():
    """pair_vols must annualise realised vol at the BAR frequency
    (marks.periods_per_year ~ 8766 for hourly bars), not a hardcoded 252.
    A 252 annualisation understates sub-daily vol ~6x, which saturates the
    vol-target scale and effectively turns vol targeting OFF on exactly the
    intraday/hf books that route real orders."""
    p = profile("intraday")                       # bar="60m"
    panel = synthetic_panel(DEFAULT_UNIVERSE, start="2023-01-01",
                            end="2023-04-01", freq="60m")
    vols = risk.pair_vols(panel, p)
    for s, df in panel.items():
        ppy = marks.periods_per_year(df.index)
        assert ppy > 8000, "hourly bars annualise ~8766, not 252"
        expected = ind.realized_vol(df["close"], p.vol_lookback, ann=ppy)
        pd.testing.assert_series_equal(vols[s], expected, check_names=False)
    # ...and this is materially different from the (buggy) 252 annualisation.
    naive252 = ind.realized_vol(panel["EURUSD"]["close"], p.vol_lookback, ann=252)
    assert not np.allclose(vols["EURUSD"].dropna(), naive252.dropna())


def test_vol_target_engages_on_60m_bars():
    """On 60m bars the vol-target scale must BIND below max_vol_scale under
    normal vol (vol targeting actually engages), whereas the old hardcoded 252
    understated vol enough that the scale saturated at max_vol_scale — pinning
    the book near 3x its intended risk."""
    p = profile("intraday")                       # target_vol=0.10, max_vol_scale=3.0
    panel = synthetic_panel(DEFAULT_UNIVERSE, start="2023-01-01",
                            end="2023-04-01", freq="60m")
    vols = risk.pair_vols(panel, p)
    idx = vols.index
    tilts = pd.DataFrame(0.0, index=idx, columns=vols.columns)
    tilts["AUDUSD"] = 0.15                         # a concentrated single-pair book

    w = risk.size_book(tilts, vols, p)
    # Reference book with saturation forced: a vanishing vol pins scale at the
    # max_vol_scale ceiling, so this is what a saturated (targeting-OFF) book
    # looks like.
    w_sat = risk.size_book(tilts, vols * 1e-3, p)
    aud, aud_sat = w["AUDUSD"].iloc[-1], w_sat["AUDUSD"].iloc[-1]
    # vol targeting engaged: the sized book is scaled BELOW the saturated ceiling
    assert aud < aud_sat - 1e-9
    # ...and lands strictly inside the per-pair cap (it is NOT the capped max the
    # old 252 annualisation would have driven it to).
    assert aud < p.per_pair_cap - 1e-6


def test_crypto_gross_cap_enforced():
    """Total crypto gross (Σ|w| over BTC/ETH/SOL) is capped as one correlated bet;
    FX legs are untouched by the crypto scaling; None disables the cap."""
    p = profile("balanced")            # crypto_gross_cap = 0.10 (Phase-0 bleed-stop)
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


def test_intraday_carries_defensive_crypto_cap():
    """B2: the daytrader book runs the 'intraday' profile over DEFAULT_UNIVERSE
    (FX + BTC/ETH/SOL). It must carry an EXPLICIT defensive crypto cap (0.10,
    matching 'balanced'), not silently inherit the loose 0.25 FXParams default —
    the FX technical agents have negative directional edge on crypto."""
    from trading_algo.forex.fx_config import FXParams, profile_names
    assert profile("intraday").crypto_gross_cap == pytest.approx(0.10)
    loose = FXParams().crypto_gross_cap                 # the loose default (0.25)
    for name in profile_names():
        cap = profile(name).crypto_gross_cap
        # crypto-ONLY books (hf_crypto) intentionally run uncapped (None); every
        # crypto-INCLUSIVE profile must set its own cap, never the loose default.
        assert cap != loose, f"{name} inherits the loose default crypto cap"


def test_phase0_directional_crypto_bleed_stop():
    """Phase-0 bleed-stop: the two directionally-crypto books (matt=balanced,
    partner=conservative) hard-cap crypto gross at a defensive level, because the
    FX technical agents have negative directional edge on crypto. Screaming-long
    crypto must be scaled to the defensive cap, not the old 0.25/0.15."""
    idx = pd.bdate_range("2020-01-01", periods=10)
    cols = DEFAULT_UNIVERSE
    tilts = pd.DataFrame(0.0, index=idx, columns=cols)
    tilts[["BTCUSD", "ETHUSD", "SOLUSD"]] = 0.9          # crypto screaming long
    vols = pd.DataFrame(0.10, index=idx, columns=cols)
    for name, defensive in (("balanced", 0.10), ("conservative", 0.05)):
        p = profile(name)
        assert p.crypto_gross_cap == pytest.approx(defensive), name
        w = risk.size_book(tilts, vols, p)
        crypto_gross = w[["BTCUSD", "ETHUSD", "SOLUSD"]].abs().sum(axis=1).max()
        assert crypto_gross <= defensive + 1e-9, name


# ---------------------------------------------------------------------------
# Round-2 item 3: per-asset-class gross caps (equity cluster / bond duration)
# ---------------------------------------------------------------------------
_EQ = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]
_BD = ["TLT", "IEF", "AGG", "SHY"]
_FX = ["EURUSD", "AUDUSD"]


def _tilt_matrix():
    """Stocks+bonds+fx tilts sized so the equity and bond class caps BIND while
    per-pair and max_gross do not (see the arithmetic in the assertions)."""
    idx = pd.bdate_range("2020-01-01", periods=30)
    cols = [*_EQ, *_BD, *_FX]
    tilts = pd.DataFrame(0.0, index=idx, columns=cols)
    tilts[_EQ] = 0.9
    tilts[_BD] = 0.9
    tilts[_FX] = 0.4
    vols = pd.DataFrame(0.10, index=idx, columns=cols)
    return tilts, vols


def _caps_off(p):
    return p.with_overrides(class_gross_caps=(("equity", None), ("bond", None)))


def test_class_gross_caps_enforced():
    """Equity-class gross <= equity cap and bond-class gross <= bond cap, with
    proportional within-class scaling; FX legs untouched by class caps."""
    p = profile("balanced")                    # equity 0.75 / bond 0.50
    caps = dict(p.class_gross_caps)
    tilts, vols = _tilt_matrix()
    w = risk.size_book(tilts, vols, p)
    w_off = risk.size_book(tilts, vols, _caps_off(p))

    # caps bind (the uncapped run exceeds them) and are enforced
    assert w_off[_EQ].abs().sum(axis=1).max() > caps["equity"]
    assert w_off[_BD].abs().sum(axis=1).max() > caps["bond"]
    assert w[_EQ].abs().sum(axis=1).max() <= caps["equity"] + 1e-9
    assert w[_BD].abs().sum(axis=1).max() <= caps["bond"] + 1e-9

    # proportional within-class scaling: every leg shrunk by the SAME factor
    eq_ratio = caps["equity"] / w_off[_EQ].abs().sum(axis=1)
    bd_ratio = caps["bond"] / w_off[_BD].abs().sum(axis=1)
    assert np.allclose(w[_EQ], w_off[_EQ].mul(eq_ratio, axis=0))
    assert np.allclose(w[_BD], w_off[_BD].mul(bd_ratio, axis=0))
    # ...so equal tilts stay equal within a class
    assert np.allclose(w["SPY"], w["QQQ"])
    assert np.allclose(w["TLT"], w["IEF"])

    # FX columns identical to the run with all class caps disabled
    for c in _FX:
        assert np.allclose(w[c], w_off[c])


def test_class_cap_none_disables_that_class():
    p = profile("balanced")
    tilts, vols = _tilt_matrix()
    p_eq_off = p.with_overrides(class_gross_caps=(("equity", None), ("bond", 0.50)))
    w = risk.size_book(tilts, vols, p_eq_off)
    w_off = risk.size_book(tilts, vols, _caps_off(p))
    # equity uncapped -> matches the fully-uncapped run and exceeds 0.75...
    assert np.allclose(w[_EQ], w_off[_EQ])
    assert w[_EQ].abs().sum(axis=1).max() > 0.75
    # ...while the bond cap still binds
    assert w[_BD].abs().sum(axis=1).max() <= 0.50 + 1e-9


def test_per_pair_and_max_gross_hold_after_class_scaling():
    """The final delever still enforces max_gross AFTER class scaling, and no
    leg breaches per_pair_cap."""
    p = profile("balanced").with_overrides(max_gross=1.0)
    tilts, vols = _tilt_matrix()
    w = risk.size_book(tilts, vols, p)
    assert w.abs().sum(axis=1).max() <= p.max_gross + 1e-6
    assert w.abs().max().max() <= p.per_pair_cap + 1e-9
    # class caps hold a fortiori after the (uniform) delever
    assert w[_EQ].abs().sum(axis=1).max() <= 0.75 + 1e-9
    assert w[_BD].abs().sum(axis=1).max() <= 0.50 + 1e-9


def test_crypto_knob_still_drives_crypto_class():
    """Back-compat: crypto is capped by `crypto_gross_cap` (not class_gross_caps),
    so overriding the legacy knob alone still moves the crypto cap."""
    p = profile("balanced")
    idx = pd.bdate_range("2020-01-01", periods=20)
    cols = ["BTCUSD", "ETHUSD", "EURUSD"]
    tilts = pd.DataFrame(0.0, index=idx, columns=cols)
    tilts[["BTCUSD", "ETHUSD"]] = 0.9
    tilts["EURUSD"] = 0.3
    vols = pd.DataFrame(0.10, index=idx, columns=cols)
    w_tight = risk.size_book(tilts, vols, p.with_overrides(crypto_gross_cap=0.10))
    assert w_tight[["BTCUSD", "ETHUSD"]].abs().sum(axis=1).max() <= 0.10 + 1e-9


def test_profile_class_cap_defaults():
    """Surface the round-2 cap defaults (pending user sign-off) as an explicit
    pin so a silent change can't slip in. FX has no entry (uncapped)."""
    for name, eq_cap, bd_cap in [("balanced", 0.75, 0.50),
                                 ("intraday", 0.75, 0.50),
                                 ("conservative", 0.60, 0.40),
                                 ("aggressive", 1.00, 0.75),
                                 ("hf_crypto", 0.75, 0.50)]:   # inert: crypto-only book
        caps = dict(profile(name).class_gross_caps)
        assert caps == {"equity": eq_cap, "bond": bd_cap}, name
        assert "fx" not in caps and "crypto" not in caps, name
