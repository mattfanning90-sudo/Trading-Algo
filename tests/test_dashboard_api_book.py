"""The dashboard state payload must describe THE BOOK IT IS SHOWING, not the
globals — and its observability blocks must degrade to null, never to a
plausible-looking zero.

Pins the two safety-relevant misreports fixed here (a profiled book's drawdown
breaker and its strategy knobs), the keys deliberately deleted, and the shape of
each analytic on both a funded book and a book that has never traded.
"""
import pytest

import trading_algo.paper_trade as pt
from trading_algo import config as cfg
from trading_algo.dashboard import api


@pytest.fixture()
def books(tmp_path, monkeypatch):
    """One default book, one `ultra`-profile book (3x gross, breaker DISABLED),
    and one funded-but-never-traded book."""
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=300_000, synthetic=True)
    pt.run_daily("full", synthetic=True)
    pt.init_account("ultra", capital=10_000, synthetic=True, profile="ultra")
    pt.run_daily("ultra", synthetic=True)
    pt.init_account("fresh", capital=10_000, synthetic=True,
                    allocations={"US": 1.0})       # no run_daily -> zero trades
    return tmp_path


# ---------------------------------------------------------------------------
# bug 1 — the breaker on screen must be the book's own
# ---------------------------------------------------------------------------
def test_breaker_is_per_book(books):
    """Key ABSENT -> the global default; key present with value None -> the
    breaker is genuinely OFF and the payload must say null, not 0.25."""
    assert api.build_snapshot("full", synthetic=True)["breaker"] == pytest.approx(0.25)
    assert api.build_snapshot("ultra", synthetic=True)["breaker"] is None


def test_breaker_matches_what_paper_trade_enforces(books):
    """The screen and the engine must resolve the same threshold — a dashboard
    that disagrees with the risk control is worse than no dashboard."""
    for account in ("full", "ultra"):
        state = pt.load_state(account)
        snap = api.build_snapshot(account, synthetic=True)
        assert snap["breaker"] == pt._account_drawdown_stop(state)


# ---------------------------------------------------------------------------
# bug 2 — the knobs on screen must be the book's own
# ---------------------------------------------------------------------------
def test_params_reflect_profile_overrides(books):
    ultra = api.build_snapshot("ultra", synthetic=True)["params"]
    prof_overrides = pt.load_state("ultra")["param_overrides"]
    # every persisted override is present and is the value the book runs
    for k, v in prof_overrides.items():
        assert ultra[k] == v, k
    # ...and the un-overridden knobs still come from the shared defaults
    assert ultra["lookback_days"] == cfg.DEFAULT_PARAMS.lookback_days
    # the METHOD tab's knob set is always covered
    assert {"lookback_days", "skip_days", "top_n", "max_weight", "target_vol",
            "vol_lookback", "max_gross", "stock_trend_ma", "index_trend_ma",
            "rebalance"} <= set(ultra)


def test_params_default_book_matches_globals(books):
    full = api.build_snapshot("full", synthetic=True)["params"]
    for k, v in full.items():
        assert v == getattr(cfg.DEFAULT_PARAMS, k), k


def test_target_vol_kpi_is_the_books_own(books):
    """The OVERVIEW vol-target tile followed cfg.DEFAULT_PARAMS, so a 35%-vol
    book advertised 12%."""
    assert api.build_snapshot("ultra", synthetic=True)["kpis"]["target_vol"] \
        == pytest.approx(0.35)
    assert api.build_snapshot("full", synthetic=True)["kpis"]["target_vol"] \
        == pytest.approx(cfg.DEFAULT_PARAMS.target_vol)


# ---------------------------------------------------------------------------
# bug 3 — dead payload stays dead
# ---------------------------------------------------------------------------
def test_unrendered_keys_stay_deleted(books):
    snap = api.build_snapshot("full", synthetic=True)
    assert "recent_trades" not in snap        # strict subset of `blotter`
    assert "blotter" in snap
    assert isinstance(snap["benchmark_curve"], list)   # kept: the chart needs it


def test_benchmark_curve_uses_the_fx_cache(books, monkeypatch):
    """The uncached FX fetch per /api/state hit is gone. Synthetic runs never
    call load_fx, so assert on the argument the real path would pass."""
    seen = {}

    def spy(currencies, start, end=None, base="AUD", use_cache=True):
        seen["use_cache"] = use_cache
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(api.fx, "load_fx", spy)
    api._benchmark_curve({"US": (None, "USD")},
                         [("2026-01-01", 1.0)], 1000.0, synthetic=False)
    assert seen["use_cache"] is True


# ---------------------------------------------------------------------------
# analytics — present and shaped on a real book, null-safe on a thin one
# ---------------------------------------------------------------------------
def test_crowding_per_sleeve(books):
    for s in api.build_snapshot("full", synthetic=True)["sleeves"]:
        c = s["crowding"]
        assert {"avg_correlation", "dispersion", "vol_ratio", "below_200dma",
                "crash_setup", "elevated", "reasons", "n_names"} <= set(c)
        assert isinstance(c["elevated"], bool)
        # unavailable metrics are null, never 0.0
        assert c["avg_correlation"] is None or isinstance(c["avg_correlation"], float)


def test_crowding_uses_the_books_own_top_n(books):
    """`ultra` holds a 6-name book; its crash monitor must read the same set."""
    ultra = api.build_snapshot("ultra", synthetic=True)["sleeves"][0]
    assert ultra["crowding"]["n_names"] == 6


def test_tca_per_region(books):
    tca = api.build_snapshot("full", synthetic=True)["tca"]
    assert isinstance(tca["alerts"], list)
    us = tca["US"]
    assert us["n_fills"] > 0 and us["currency"] == "USD"
    # paper fills are modelled, so realized == modelled by construction
    assert us["realized_slippage_bps"] == pytest.approx(us["modelled_slippage_bps"])


def test_attribution_never_fabricates_a_backtest(books):
    a = api.build_snapshot("full", synthetic=True)["attribution"]
    assert a["cost_drag_by_region"]["US"]["cost_drag_bps"] > 0   # measurable
    # no predicted curve was supplied -> these are UNMEASURED, and say so
    assert a["divergence"] is None
    assert a["tracking_error_bps"] is None


def test_promotion_checklist(books):
    p = api.build_snapshot("full", synthetic=True)["promotion"]
    assert p["ready"] is False                       # one month of paper history
    assert set(p["checks"]) == {"track_record", "capacity", "schema_valid",
                                "not_halted", "overfitting", "tracking"}
    assert p["reasons"] and p["rebalance_months"] >= 1


def test_analytics_degrade_on_a_book_with_no_trades(books):
    """A never-traded book must render, not 500 — and must not invent numbers."""
    snap = api.build_snapshot("fresh", synthetic=True)
    assert snap["tca"] == {"alerts": []}                # nothing to measure
    assert snap["attribution"]["cost_drag_by_region"] == {}
    assert snap["promotion"]["ready"] is False
    assert snap["sleeves"][0]["crowding"] is not None


def test_snapshot_survives_a_broken_analytic(books, monkeypatch):
    """One analytic blowing up must never take the whole endpoint down."""
    def boom(*_a, **_k):
        raise ValueError("thin book")

    monkeypatch.setattr(api.tca, "tca_report", boom)
    monkeypatch.setattr(api.crowding, "crowding_report", boom)
    monkeypatch.setattr(api.attribution, "attribution_report", boom)
    monkeypatch.setattr(api.promotion, "promotion_check", boom)
    snap = api.build_snapshot("full", synthetic=True)
    assert snap["tca"] is None and snap["attribution"] is None
    assert snap["promotion"] is None
    assert all(s["crowding"] is None for s in snap["sleeves"])
    assert snap["kpis"]["total_equity"] > 0            # the book itself is intact
