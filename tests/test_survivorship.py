"""Free survivorship-bias-free data plumbing: Tiingo provider, wide-format
constituents (fja05680), and the delisting-return correction."""
import pandas as pd

from trading_algo import data
from trading_algo import providers as pv
from trading_algo.constituents import MembershipTable


# --- Tiingo provider (delisted-capable US source) --------------------------
def test_tiingo_symbol_translation():
    s = pv.TiingoProvider._symbol
    assert s("AAPL") == "aapl"
    assert s("BRK-B") == "brk-b"            # class shares kept (Yahoo hyphen)
    assert s("BHP.AX") is None and s("AZN.L") is None
    assert s("^GSPC") is None and s("AUDUSD=X") is None


def test_tiingo_requires_key():
    assert pv.TiingoProvider(api_key=None).supports("AAPL") is False
    assert pv.TiingoProvider(api_key="dummy").supports("AAPL") is True


def test_chain_includes_tiingo_only_with_key(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert "tiingo" not in {p.name for p in pv.get_chain()}
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    assert "tiingo" in {p.name for p in pv.get_chain()}


# --- Wide-format point-in-time constituents (fja05680) ----------------------
def test_wide_format_constituents_with_graveyard():
    df = pd.DataFrame({
        "date": ["1996-01-02", "2015-01-02", "2020-01-02"],
        "tickers": ['"AAPL,MSFT,ENRN"', '"AAPL,MSFT"', '"AAPL,MSFT,TSLA"'],
    })
    m = MembershipTable.from_wide_frame(df)
    assert len(m) == 3
    # ENRN (a delisted name) is present in 1996 but gone by 2015 — the graveyard
    assert "ENRN" in m.members_asof("1996-06-01")
    assert "ENRN" not in m.members_asof("2016-01-01")
    assert "TSLA" in m.members_asof("2021-01-01")
    assert "ENRN" in m.all_tickers          # union includes since-removed names


def test_wide_format_normalizes_class_shares():
    df = pd.DataFrame({"date": ["2020-01-02"], "tickers": ['"BRK.B,BF.B,AAPL"']})
    members = MembershipTable.from_wide_frame(df).members_asof("2020-06-01")
    assert "BRK-B" in members and "BF-B" in members   # dot -> Yahoo hyphen
    assert "BRK.B" not in members


def test_from_file_detects_wide(tmp_path):
    p = tmp_path / "sp500.csv"
    p.write_text('date,tickers\n2020-01-02,"AAPL,MSFT"\n')
    m = MembershipTable.from_file(str(p))
    assert m.members_asof("2020-06-01") == {"AAPL", "MSFT"}


# --- Delisting return -------------------------------------------------------
def test_apply_delisting_returns_books_terminal_loss():
    idx = pd.bdate_range("2020-01-01", periods=10)
    prices = pd.DataFrame({
        "SURV": range(10, 20),                         # trades to the end
        "DEAD": [5, 6, 7, 8, 9] + [float("nan")] * 5,  # stops at day 5
    }, index=idx, dtype=float)
    out = data.apply_delisting_returns(prices, still_listed={"SURV"}, default_return=-0.30)
    # SURV (still listed) untouched
    assert out["SURV"].equals(prices["SURV"])
    # DEAD gets one terminal point at last*0.7 on the next day, then stays NaN
    last_valid_loc = 4
    assert out["DEAD"].iloc[last_valid_loc + 1] == 9 * 0.7
    assert pd.isna(out["DEAD"].iloc[last_valid_loc + 2])


def test_apply_delisting_returns_ignores_still_listed_gap():
    idx = pd.bdate_range("2020-01-01", periods=6)
    prices = pd.DataFrame({"X": [1, 2, 3, float("nan"), float("nan"), float("nan")]},
                          index=idx, dtype=float)
    # X is in the current universe → treated as a data gap, not a delisting
    out = data.apply_delisting_returns(prices, still_listed={"X"})
    assert out["X"].dropna().tolist() == [1, 2, 3]
