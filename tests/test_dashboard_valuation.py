"""Dashboard valuation robustness — a failed FX pair must never NaN the AUM."""
import pytest

from trading_algo import paper_trade as pt
from trading_algo.dashboard import api


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    name = "dashval"
    pt.init_account(name, capital=300_000, synthetic=True)
    pt.run_daily(name, synthetic=True)
    return name


def _break_one_pair(monkeypatch, currency="USD"):
    """A single FX pair 403s: its column comes back all-NaN, exactly as when its
    Yahoo pair fails while the others succeed."""
    real = pt.fx.synthetic_fx

    def broken(currencies, *a, **k):
        tbl = real(currencies, *a, **k)
        tbl[currency] = float("nan")
        return tbl

    monkeypatch.setattr(pt.fx, "synthetic_fx", broken)


def test_build_snapshot_finite_aum_when_one_pair_fails(account, monkeypatch):
    """build_snapshot must yield a finite headline AUM even when one FX pair
    fails and there is no prior rate to carry — the residual behind the NaN
    sleeve equity + NaN AUM this branch fixes."""
    # Remove any prior USD rate so the failed pair has nothing to carry forward.
    state = pt.load_state(account)
    state["fx_snapshot"].pop("USD", None)
    pt.save_state(account, state)

    _break_one_pair(monkeypatch, "USD")
    snap = api.build_snapshot(account, synthetic=True)

    aum = snap["kpis"]["total_equity"]
    assert aum == aum and aum > 0, "headline AUM must be finite, not NaN"
    for s in snap["sleeves"]:
        assert s["equity_base"] == s["equity_base"], f"{s['key']} equity_base is NaN"
    # the healthy sleeves still carry real base value
    assert any(s["equity_base"] > 0 for s in snap["sleeves"])


def test_build_snapshot_carries_forward_a_failed_rate(account, monkeypatch):
    """With a prior good rate present, a failed pair is carried forward, so every
    sleeve keeps a finite base valuation."""
    _break_one_pair(monkeypatch, "USD")
    snap = api.build_snapshot(account, synthetic=True)
    aum = snap["kpis"]["total_equity"]
    assert aum == aum and aum > 0
    us = next(s for s in snap["sleeves"] if s["key"] == "US")
    assert us["fx_rate"] > 0                      # carried forward, not NaN/zero
    assert us["equity_base"] == us["equity_base"]
