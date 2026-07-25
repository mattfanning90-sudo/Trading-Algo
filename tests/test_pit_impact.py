"""Backlog F1: quantify survivorship bias (static vs point-in-time CAGR),
and: the reported universe must be the ACHIEVED one, not the requested flag.
"""
from trading_algo import constituents, data, run_backtest
from trading_algo import portfolio_backtest as pb
from trading_algo.run_backtest import _universe_label


def test_pit_impact_reports_static_pit_and_delta():
    imp = run_backtest.pit_impact(synthetic=True)
    assert set(imp) == {"static_cagr", "pit_cagr", "delta"}
    assert imp["delta"] == imp["static_cagr"] - imp["pit_cagr"]
    for v in imp.values():
        assert isinstance(v, float)


# ---------------------------------------------------------------------------
# The reported universe must be the ACHIEVED one, never the requested flag
# ---------------------------------------------------------------------------
# `--point-in-time` ASKS for point-in-time membership. It only gets it if the
# region has a `constituents_file` and the file exists — and no region ships one,
# so on real data the fallback is the NORMAL outcome. run_single always reported
# the achieved state (backtest.py: `membership is not None`); the portfolio path
# reported the flag, so the documented headline command printed
# "survivorship-bias corrected" and stamped the run manifest PIT while every
# sleeve ran the static, survivorship-biased universe.


def test_pit_requested_but_unavailable_reports_not_achieved(monkeypatch, capsys):
    """The real-data shape: no constituents file anywhere."""
    monkeypatch.setattr(constituents, "get_membership", lambda region: None)
    # Take the real-data BRANCH (synthetic=False) but keep it offline: substitute
    # the offline generators for both price and FX loading, so the only thing under
    # test is which universe gets reported.
    monkeypatch.setattr(data, "load_region",
                        lambda region, start, end=None, **kw: data.synthetic_region(region))
    monkeypatch.setattr(pb.fx, "load_fx",
                        lambda currencies, start, end=None, base="AUD", **kw:
                            pb.fx.synthetic_fx(currencies, base=base))

    res = pb.run_portfolio_backtest(synthetic=False, point_in_time=True,
                                    regions=["US"])

    assert res["point_in_time"] is False, "must report the ACHIEVED universe"
    assert res["point_in_time_requested"] is True, "and remember what was asked"
    assert "survivorship-bias corrected" not in _universe_label(res["point_in_time"])
    assert "no constituents file" in capsys.readouterr().out, "must warn, not go quiet"


def test_pit_achieved_when_membership_really_exists():
    """Control: the synthetic path DOES build a membership table, so the PIT label
    is earned there — otherwise the test above would pass for the wrong reason."""
    res = pb.run_portfolio_backtest(synthetic=True, point_in_time=True,
                                    regions=["US"])
    assert res["point_in_time"] is True
    assert "survivorship-bias corrected" in _universe_label(res["point_in_time"])


def test_one_falling_back_sleeve_taints_the_whole_portfolio(monkeypatch):
    """A portfolio is only survivorship-corrected if ALL of it is: one sleeve
    without membership must flip the whole run's label."""
    real = constituents.synthetic_membership
    monkeypatch.setattr(
        constituents, "synthetic_membership",
        lambda region, *a, **k: None if region.key == "FTSE" else real(region, *a, **k))

    res = pb.run_portfolio_backtest(synthetic=True, point_in_time=True,
                                    regions=["US", "FTSE"])
    assert res["point_in_time"] is False
