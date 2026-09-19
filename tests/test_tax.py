"""After-tax reporting: CGT on realised round-trips + dividend withholding.

NOT a trading-path change and NOT tax advice. Tax is entity-specific (your
marginal rate, your structure, your residency), so it is a REPORTING layer over
the realised ledger — it never touches an equity curve or a weight.

Why it matters here: the books rebalance monthly, so essentially nothing is held
the 12 months an Australian CGT discount requires. A strategy whose gains are
all short-term is taxed at the full marginal rate, which can exceed every
transaction cost combined — and that makes HOLDING PERIOD a strategy parameter,
not just a cost.
"""
import pandas as pd
import pytest

from trading_algo import tax


def _rt(entry_date, exit_date, net, region="US", ticker="AAA", filled=10):
    """One realised round-trip in the shape pnl.build_lots emits."""
    return {"entry_date": entry_date, "date": exit_date, "net": net,
            "gross": net, "region": region, "ticker": ticker,
            "currency": "USD", "filled": filled, "entry": 100.0, "exit": 110.0}


def _state(round_trips):
    return {"_realized": round_trips}


# --- holding period --------------------------------------------------------
def test_holding_days_counts_calendar_days():
    assert tax.holding_days(_rt("2025-01-01", "2025-07-01", 100.0)) == 181


def test_a_position_held_over_twelve_months_is_discount_eligible():
    assert tax.is_discount_eligible(_rt("2024-01-01", "2025-06-01", 100.0))


def test_exactly_twelve_months_is_NOT_eligible():
    """The ATO requires MORE than 12 months, not at least — an off-by-one here
    would silently halve the tax on a whole cohort of trades."""
    assert not tax.is_discount_eligible(_rt("2024-01-01", "2024-12-31", 100.0))
    assert not tax.is_discount_eligible(_rt("2024-01-01", "2025-01-01", 100.0))
    assert tax.is_discount_eligible(_rt("2024-01-01", "2025-01-02", 100.0))


# --- CGT -------------------------------------------------------------------
def test_short_term_gain_is_taxed_at_the_full_marginal_rate():
    s = tax.cgt_summary([_rt("2025-01-01", "2025-03-01", 1_000.0)], marginal_rate=0.47)
    assert s["discounted_gain"] == 0.0
    assert s["net_capital_gain"] == pytest.approx(1_000.0)
    assert s["tax"] == pytest.approx(470.0)


def test_long_term_gain_gets_the_fifty_percent_discount():
    s = tax.cgt_summary([_rt("2024-01-01", "2025-06-01", 1_000.0)], marginal_rate=0.47)
    assert s["net_capital_gain"] == pytest.approx(500.0)
    assert s["tax"] == pytest.approx(235.0)


def test_losses_offset_gains():
    s = tax.cgt_summary([_rt("2025-01-01", "2025-03-01", 1_000.0),
                         _rt("2025-01-01", "2025-03-01", -400.0)], marginal_rate=0.47)
    assert s["net_capital_gain"] == pytest.approx(600.0)


def test_losses_are_applied_to_UNDISCOUNTED_gains_first():
    """The ATO lets you choose; applying losses to the non-discounted gain first
    is strictly better for the taxpayer, so that is what we report."""
    s = tax.cgt_summary([_rt("2025-01-01", "2025-03-01", 1_000.0),    # short
                         _rt("2024-01-01", "2025-06-01", 1_000.0),    # long
                         _rt("2025-01-01", "2025-03-01", -1_000.0)],  # loss
                        marginal_rate=0.47)
    # loss kills the short gain; the long gain is halved -> 500
    assert s["net_capital_gain"] == pytest.approx(500.0)


def test_a_net_loss_carries_forward_and_is_not_a_refund():
    s = tax.cgt_summary([_rt("2025-01-01", "2025-03-01", -900.0)], marginal_rate=0.47)
    assert s["tax"] == 0.0
    assert s["loss_carried_forward"] == pytest.approx(900.0)


# --- dividend withholding --------------------------------------------------
def test_withholding_charges_only_dividends_paid_while_held():
    divs = {"AAA": pd.Series([1.0, 1.0, 1.0],
                             index=pd.to_datetime(["2025-02-01", "2025-06-01",
                                                   "2025-09-01"]))}
    # held Jan->Jul: catches Feb and Jun, not Sep
    w = tax.withholding_drag([_rt("2025-01-01", "2025-07-01", 0.0, filled=10)],
                             dividends=lambda t: divs.get(t, pd.Series(dtype=float)),
                             rates={"US": 0.15})
    assert w["gross_dividends"] == pytest.approx(20.0)      # 2 payments x 10 shares
    assert w["withheld"] == pytest.approx(3.0)              # 15%


def test_regions_without_withholding_are_free():
    divs = {"AAA": pd.Series([1.0], index=pd.to_datetime(["2025-02-01"]))}
    w = tax.withholding_drag([_rt("2025-01-01", "2025-07-01", 0.0, region="FTSE")],
                             dividends=lambda t: divs.get(t, pd.Series(dtype=float)),
                             rates={"US": 0.15, "FTSE": 0.0},
                             price_scale=lambda r: 1.0)   # units pinned separately
    assert w["gross_dividends"] == pytest.approx(10.0)
    assert w["withheld"] == 0.0


def test_no_dividend_history_is_not_an_error():
    w = tax.withholding_drag([_rt("2025-01-01", "2025-07-01", 0.0)],
                             dividends=lambda t: pd.Series(dtype=float),
                             rates={"US": 0.15})
    assert w["withheld"] == 0.0


# --- currency-unit regression ---------------------------------------------
# LSE dividends come off Yahoo in PENCE, exactly like LSE prices — which is why
# the FTSE region carries price_scale=0.01. Forgetting to apply it to dividends
# overstates the FTSE sleeve's income 100x: the first run of this report claimed
# A$7,742 of dividends on a ~A$100k book in three months.
def test_lse_dividends_are_scaled_from_pence_to_pounds():
    divs = {"LLOY.L": pd.Series([2.0], index=pd.to_datetime(["2025-03-01"]))}
    w = tax.withholding_drag(
        [_rt("2025-01-01", "2025-07-01", 0.0, region="FTSE",
             ticker="LLOY.L", filled=1000)],
        dividends=lambda t: divs.get(t, pd.Series(dtype=float)),
        rates={"FTSE": 0.0}, price_scale=lambda r: 0.01 if r == "FTSE" else 1.0)
    assert w["gross_dividends"] == pytest.approx(20.0)     # 2p x 1000 = £20, not £2000


def test_us_dividends_are_not_scaled():
    divs = {"MRK": pd.Series([0.85], index=pd.to_datetime(["2025-03-01"]))}
    w = tax.withholding_drag(
        [_rt("2025-01-01", "2025-07-01", 0.0, region="US", ticker="MRK", filled=100)],
        dividends=lambda t: divs.get(t, pd.Series(dtype=float)),
        rates={"US": 0.15}, price_scale=lambda r: 1.0)
    assert w["gross_dividends"] == pytest.approx(85.0)
