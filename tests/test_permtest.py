"""In-sample permutation test for the swarm search (docs/specs/swarm-insample-permutation.md).

The thing under test is a NULL: a market with no exploitable structure, built by
shuffling our own bars. If the shuffle leaves an edge behind, every p-value
computed against it is rigged — so most of these assertions are about what the
shuffle preserves and destroys, not about the search.
"""
import json
import os

import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import bar_permute, evolve, permtest
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY"]


@pytest.fixture(scope="module")
def panel():
    return synthetic_panel(SYMBOLS, start="2020-01-01", end="2022-01-01")


def _log_close(df):
    return np.log(df["close"].to_numpy())


def _log_rets(df):
    return np.diff(_log_close(df))


# --- AC-1: what the shuffle must preserve ---------------------------------

def test_permutation_preserves_per_symbol_total_return_and_volatility(panel):
    out = bar_permute.permute_panel(panel, seed=0)
    for sym in panel:
        real, perm = _log_rets(panel[sym]), _log_rets(out[sym])
        assert real.sum() == pytest.approx(perm.sum(), abs=1e-10), f"{sym} drift changed"
        assert real.std() == pytest.approx(perm.std(), abs=1e-10), f"{sym} vol changed"


def test_permutation_preserves_cross_symbol_correlation(panel):
    """One shuffle shared by every symbol. Shuffling them independently would
    destroy co-movement, and co-movement is what sets portfolio volatility —
    that hands the null a free diversification bonus."""
    out = bar_permute.permute_panel(panel, seed=0)
    real = pd.DataFrame({s: _log_rets(panel[s]) for s in panel}).corr().to_numpy()
    perm = pd.DataFrame({s: _log_rets(out[s]) for s in panel}).corr().to_numpy()
    assert np.abs(real - perm).max() < 1e-10


def test_permutation_keeps_bars_internally_consistent(panel):
    """high >= max(open, close) and low <= min(open, close) must still hold:
    a bar's own shape is shuffled as a unit, never taken apart."""
    out = bar_permute.permute_panel(panel, seed=0)
    for sym, df in out.items():
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-12).all(), sym
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-12).all(), sym


# --- AC-2: what the shuffle must destroy ----------------------------------

def test_permutation_destroys_volatility_clustering():
    """Build a panel that definitely HAS clustering, then check it is gone."""
    rng = np.random.default_rng(0)
    n, omega, alpha, beta = 3000, 5e-6, 0.15, 0.80     # GARCH(1,1), var ~ 1e-4
    z = rng.standard_normal(n)
    r, var = np.empty(n), 1e-4
    for i in range(n):
        r[i] = np.sqrt(var) * z[i]
        var = omega + alpha * r[i] ** 2 + beta * var    # today's shock feeds tomorrow's vol
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.date_range("2015-01-01", periods=n, freq="D")
    df = pd.DataFrame({"open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close}, index=idx)

    before = pd.Series(np.abs(np.diff(np.log(close)))).autocorr(1)
    out = bar_permute.permute_panel({"X": df}, seed=0)
    after = pd.Series(np.abs(_log_rets(out["X"]))).autocorr(1)

    assert before > 0.15, f"fixture has no clustering to destroy (got {before:.3f})"
    assert abs(after) < 0.10, f"clustering survived the shuffle ({after:.3f})"


# --- AC-3: the real prefix ------------------------------------------------

def test_permutation_leaves_prefix_untouched(panel):
    """Step 4 needs real history before the walk-forward start. Guard it now so
    the capability is not quietly lost."""
    k = 100
    out = bar_permute.permute_panel(panel, seed=0, start_index=k)
    for sym in panel:
        pd.testing.assert_frame_equal(out[sym].iloc[:k], panel[sym].iloc[:k])


def test_permutation_changes_the_ordering(panel):
    """Guards against a no-op shuffle silently passing every other test."""
    out = bar_permute.permute_panel(panel, seed=0)
    sym = SYMBOLS[0]
    assert not np.allclose(_log_rets(panel[sym]), _log_rets(out[sym]))


def test_permutation_is_reproducible_for_a_seed(panel):
    a = bar_permute.permute_panel(panel, seed=7)
    b = bar_permute.permute_panel(panel, seed=7)
    for sym in panel:
        pd.testing.assert_frame_equal(a[sym], b[sym])


# --- AC-4: the p-value ----------------------------------------------------

def test_pvalue_is_k_plus_one_over_m_plus_one():
    assert permtest.p_value([1.0, 2.0, 3.0], real=2.5) == pytest.approx(2 / 4)


def test_pvalue_is_one_when_real_is_worst():
    assert permtest.p_value([1.0, 2.0, 3.0], real=0.0) == pytest.approx(1.0)


def test_pvalue_never_returns_zero():
    """p=0 would claim a certainty m shuffles cannot support."""
    assert permtest.p_value([1.0, 2.0, 3.0], real=99.0) == pytest.approx(1 / 4)
    assert permtest.p_value([], real=99.0) == pytest.approx(1.0)


# --- AC-5 / AC-8: the run and its report ----------------------------------

def test_run_reports_required_fields_and_consistent_pvalue(panel):
    p = profile("balanced") if callable(profile) else profile
    res = permtest.run(panel, p, permutations=2, seed=0, generations=1, pop_size=4)
    for key in ("p_value", "n_permutations", "real_best", "perm_best", "seed", "statistic"):
        assert key in res, f"missing {key}"
    assert res["n_permutations"] == 2
    assert len(res["perm_best"]) == 2
    assert res["p_value"] == pytest.approx(
        permtest.p_value(res["perm_best"], res["real_best"]))
    assert 0.0 < res["p_value"] <= 1.0


def test_run_is_reproducible_for_a_seed(panel):
    p = profile("balanced") if callable(profile) else profile
    kw = dict(permutations=2, seed=3, generations=1, pop_size=4)
    assert (permtest.run(panel, p, **kw)["perm_best"]
            == permtest.run(panel, p, **kw)["perm_best"])


def test_report_round_trips_to_disk(tmp_path, monkeypatch, panel):
    monkeypatch.setattr(permtest, "STATE_DIR", str(tmp_path))
    res = {"p_value": 0.25, "n_permutations": 3, "real_best": 1.0,
           "perm_best": [0.1, 0.2, 0.3], "seed": 0, "statistic": "best_holdout_sharpe"}
    path = permtest.write_report("acct", res)
    assert json.loads(open(path).read())["p_value"] == 0.25
    assert permtest.load_report("acct")["p_value"] == 0.25


def test_load_report_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(permtest, "STATE_DIR", str(tmp_path))
    assert permtest.load_report("nobody") is None


# --- AC-6: reports alongside, never gates ---------------------------------

def _isolate(tmp_path, monkeypatch):
    from trading_algo.forex import champions, fx_book
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    for mod in (evolve, champions):
        monkeypatch.setattr(mod, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(permtest, "STATE_DIR", str(tmp_path))
    return champions


def test_promote_surfaces_pvalue_without_changing_promotions(tmp_path, monkeypatch, panel):
    """'Report alongside' means exactly that: the permutation p-value appears in
    the meta, and the set of promoted genomes is untouched by its presence."""
    champions = _isolate(tmp_path, monkeypatch)
    p = profile("balanced") if callable(profile) else profile
    log, _, _ = evolve.breed(panel, p, generations=1, pop_size=4, seed=0)
    evolve.write_log("matt", log)

    without = champions.promote("matt", synthetic=True, profile_name="balanced")
    assert without.get("perm_pvalue") is None

    # promote() rotates against the roster it last saved, so clear it — otherwise
    # the second run starts from different state and the comparison proves nothing.
    os.remove(champions.champions_path("matt"))

    permtest.write_report("matt", {"p_value": 0.123, "n_permutations": 7,
                                   "real_best": 1.0, "perm_best": [0.1] * 7,
                                   "seed": 0, "statistic": permtest.STATISTIC})
    after = champions.promote("matt", synthetic=True, profile_name="balanced")

    assert after["perm_pvalue"] == 0.123
    assert after["perm_n"] == 7
    assert after["promoted"] == without["promoted"], "p-value must not gate"
    assert after["dsr"] == without["dsr"] and after["pbo"] == without["pbo"]
