"""FX multipliers + base-currency conversion."""
import pandas as pd

from trading_algo import fx


def test_synthetic_fx_shape_and_base():
    tbl = fx.synthetic_fx(["AUD", "USD", "GBP"], start="2020-01-01", end="2021-01-01")
    assert list(tbl.columns) == ["AUD", "USD", "GBP"]
    assert (tbl["AUD"] == 1.0).all()          # base currency is identity
    assert (tbl["USD"] > 0).all() and (tbl["GBP"] > 0).all()


def test_align_fx_identity_for_base():
    tbl = fx.synthetic_fx(["AUD", "USD"], start="2020-01-01", end="2020-06-01")
    idx = pd.bdate_range("2020-01-01", "2020-03-01")
    s = fx.align_fx(tbl, idx, "AUD")
    assert (s == 1.0).all()
    assert s.index.equals(idx)


def test_align_fx_foreign_positive():
    tbl = fx.synthetic_fx(["AUD", "USD"], start="2020-01-01", end="2020-06-01")
    idx = pd.bdate_range("2020-01-01", "2020-03-01")
    s = fx.align_fx(tbl, idx, "USD")
    assert (s > 0).all()
    assert not s.isna().any()


def test_fx_ticker_format():
    assert fx.fx_ticker("AUD", "USD") == "AUDUSD=X"
