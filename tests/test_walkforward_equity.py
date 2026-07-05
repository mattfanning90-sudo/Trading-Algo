"""Backlog F8: purged & embargoed walk-forward CV for the equity sleeves."""
import pytest

from trading_algo import data, walkforward
from trading_algo.regions import get_region

SMALL_TOP_NS = [8, 10]
SMALL_LOOKBACKS = [189, 252]   # keep the grid small so the test is fast


@pytest.fixture(scope="module")
def synth_us():
    region = get_region("US")
    prices, index_px = data.synthetic_region(region)
    return region, prices, index_px


# --- fold + embargo mechanics ----------------------------------------------
def test_fold_edges_are_contiguous_and_cover_all():
    edges = walkforward.fold_edges(100, 6)
    assert edges[0][0] == 0 and edges[-1][1] == 100
    for (a, b), (c, d) in zip(edges, edges[1:]):
        assert b == c, "folds must be contiguous with no gap/overlap"


def test_embargo_drops_first_rows_of_each_fold():
    mask = walkforward.embargoed_oos_mask(30, n_folds=3, embargo=2)
    # folds are [0,10),[10,20),[20,30); each drops its first 2 rows
    assert mask.sum() == 24
    assert not mask[0] and not mask[1] and mask[2]
    assert not mask[10] and not mask[11] and mask[12]


def test_zero_embargo_keeps_everything():
    mask = walkforward.embargoed_oos_mask(30, n_folds=3, embargo=0)
    assert mask.all()


# --- CV matrix + gate integration ------------------------------------------
def test_cv_matrix_shape_and_metadata(synth_us):
    region, prices, index_px = synth_us
    cv = walkforward.cv_returns_matrix(
        prices, index_px, region, SMALL_TOP_NS, SMALL_LOOKBACKS,
        n_folds=6, embargo=21)
    assert cv is not None
    grid_size = len(SMALL_TOP_NS) * len(SMALL_LOOKBACKS)
    assert cv["n_configs"] == grid_size
    assert cv["matrix"].shape[1] == grid_size
    assert cv["matrix"].shape[0] == cv["n_obs"] > 0
    assert cv["embargo"] == 21 and cv["n_folds"] == 6


def test_purged_cv_report_runs_the_gate(synth_us):
    region, prices, index_px = synth_us
    rep = walkforward.purged_cv_report(
        prices, index_px, region, SMALL_TOP_NS, SMALL_LOOKBACKS)
    grid_size = len(SMALL_TOP_NS) * len(SMALL_LOOKBACKS)
    # F2 acceptance: n_trials defaults to the grid size
    assert rep["n_trials"] == grid_size and rep["grid_size"] == grid_size
    assert rep["pbo"] is not None and 0.0 <= rep["pbo"] <= 1.0
    assert 0.0 <= rep["dsr"] <= 1.0
    assert rep["embargo"] == walkforward.DEFAULT_EMBARGO
    assert isinstance(rep["passed"], bool)


def test_default_embargo_is_at_least_one_rebalance():
    # F8 acceptance: embargo >= 21 trading days (one monthly rebalance)
    assert walkforward.DEFAULT_EMBARGO >= 21
    assert walkforward.DEFAULT_N_FOLDS >= 6
