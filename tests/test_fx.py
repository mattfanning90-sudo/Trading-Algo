"""FX multipliers + base-currency conversion."""
import pandas as pd
import pytest

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


# --- one base-per-local rule for every money figure -------------------------
_SNAP = {"USD": 1.5, "GBP": float("nan"), "CAD": 0.0, "NZD": -1.5,
         "CHF": float("inf"), "HKD": "n/a"}


@pytest.mark.parametrize("ccy, want", [
    ("AUD", 1.0),        # the base currency itself, in the snapshot or not
    ("USD", 1.5),        # a finite positive rate
    ("GBP", None),       # NaN: a pair that failed with no prior to carry
    ("CAD", None),       # zero
    ("NZD", None),       # negative
    ("CHF", None),       # infinite: 1 / a zero quote is not a rate
    ("HKD", None),       # not a number
    ("JPY", None),       # omitted from the snapshot
    (None, None),        # a row that names no currency
])
def test_base_per_local_is_a_finite_positive_rate_or_none(ccy, want):
    """The dashboard's and the bridge's rule, in one place: 1.0 only for the
    base currency, a finite positive snapshot rate otherwise, and NO rate —
    never parity — for anything else."""
    assert fx.base_per_local(_SNAP, ccy, "AUD") == want


def test_base_per_local_with_no_snapshot_prices_only_the_base():
    assert fx.base_per_local(None, "AUD", "AUD") == 1.0
    assert fx.base_per_local(None, "USD", "AUD") is None
