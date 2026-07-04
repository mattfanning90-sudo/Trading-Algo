"""FX pair registry sanity."""
import pytest

from trading_algo.forex import pairs


def test_default_universe_is_majors_plus_crypto():
    assert pairs.DEFAULT_UNIVERSE == ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                      "USDCAD", "USDCHF", "NZDUSD",
                                      "BTCUSD", "ETHUSD", "SOLUSD"]


def test_crypto_pairs_registered():
    btc = pairs.get_pair("BTCUSD")
    assert btc.base == "BTC" and btc.quote == "USD"
    assert btc.yahoo_ticker == "BTC-USD"
    assert pairs.get_pair("ETHUSD").yahoo_ticker == "ETH-USD"
    # crypto spot has no overnight swap
    assert btc.swap_long_pips == 0.0 and btc.swap_short_pips == 0.0


def test_jpy_pairs_have_larger_pip():
    assert pairs.get_pair("USDJPY").pip == 0.01
    assert pairs.get_pair("EURUSD").pip == 0.0001
    assert pairs.get_pair("USDJPY").is_jpy
    assert not pairs.get_pair("EURUSD").is_jpy


def test_spread_fraction_positive_and_scaled():
    p = pairs.get_pair("EURUSD")
    f = p.spread_fraction(1.08)
    assert f > 0
    # round-trip spread of 0.6 pips on a 1.08 price ~ 0.55 bps
    assert 0.00004 < f < 0.00007
    assert p.spread_fraction(0.0) == 0.0  # guard against bad price


def test_carry_sign_matches_side():
    jpy = pairs.get_pair("USDJPY")        # long earns (positive swap_long)
    assert jpy.carry_fraction(150.0, +1) > 0
    assert jpy.carry_fraction(150.0, -1) < 0
    assert jpy.carry_fraction(150.0, 0) == 0.0


def test_currencies_in():
    cur = pairs.currencies_in(["EURUSD", "USDJPY"])
    assert cur == {"EUR", "USD", "JPY"}


def test_unknown_pair_raises():
    with pytest.raises(KeyError):
        pairs.get_pair("ZZZUSD")


# ---------------------------------------------------------------------------
# asset_class (round-2 item 3: per-class gross caps in risk.size_book)
# ---------------------------------------------------------------------------
def test_every_pair_has_a_known_asset_class():
    for sym, p in pairs.ALL_PAIRS.items():
        assert p.asset_class in {"fx", "crypto", "equity", "bond"}, sym


def test_asset_class_group_membership():
    assert all(p.asset_class == "fx" for p in pairs.PAIRS.values())
    assert all(p.asset_class == "fx" for p in pairs.CROSSES.values())
    assert all(p.asset_class == "crypto" for p in pairs.CRYPTO.values())
    assert all(p.asset_class == "equity" for p in pairs.EQUITIES.values())
    assert all(p.asset_class == "bond" for p in pairs.BONDS.values())


def test_asset_class_spot_checks():
    assert pairs.get_pair("EURUSD").asset_class == "fx"
    assert pairs.get_pair("EURGBP").asset_class == "fx"
    assert pairs.get_pair("BTCUSD").asset_class == "crypto"
    assert pairs.get_pair("SPY").asset_class == "equity"
    assert pairs.get_pair("TLT").asset_class == "bond"


def test_unknown_asset_class_rejected():
    with pytest.raises(ValueError):
        pairs.Pair("XXXUSD", "XXX", "USD", "XXX-USD", 1.0, 1.0, 0.0, 0.0,
                   "commodity")


# ---------------------------------------------------------------------------
# Named universe presets + resolver
# ---------------------------------------------------------------------------
def test_resolve_universe_default_is_live_universe():
    assert pairs.resolve_universe(None) == pairs.DEFAULT_UNIVERSE
    assert pairs.resolve_universe("default") == pairs.DEFAULT_UNIVERSE
    # returns a copy, not the module list (mutating it must not leak)
    got = pairs.resolve_universe(None)
    got.append("EURUSD")
    assert "EURUSD" not in pairs.DEFAULT_UNIVERSE[7:]


def test_resolve_universe_majors_plus_crosses():
    u = pairs.resolve_universe("majors+crosses")
    assert u == pairs.resolve_universe("fx")          # aliases
    assert set(pairs.PAIRS).issubset(u)
    assert set(pairs.CROSSES).issubset(u)
    assert not (set(pairs.CRYPTO) & set(u))           # no crypto in the FX set


def test_resolve_universe_explicit_list_and_dedup():
    assert pairs.resolve_universe("EURUSD,GBPUSD,EURJPY") == \
        ["EURUSD", "GBPUSD", "EURJPY"]
    assert pairs.resolve_universe("eurusd, gbpusd") == ["EURUSD", "GBPUSD"]
    assert pairs.resolve_universe("EURUSD,EURUSD") == ["EURUSD"]  # de-duped


def test_resolve_universe_rejects_typos_and_empty():
    with pytest.raises(KeyError):
        pairs.resolve_universe("EURUSD,NOTAPAIR")
    with pytest.raises(ValueError):
        pairs.resolve_universe(",")
