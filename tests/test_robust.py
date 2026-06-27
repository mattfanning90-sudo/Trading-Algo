"""Overfitting controls: PSR / MinTRL / Deflated Sharpe / PBO."""
import numpy as np
import pandas as pd

from trading_algo import robust


def test_normal_helpers():
    assert abs(robust._norm_cdf(0.0) - 0.5) < 1e-9
    assert abs(robust._norm_ppf(0.975) - 1.959964) < 1e-3
    # round-trip
    for x in (-1.5, 0.3, 2.0):
        assert abs(robust._norm_ppf(robust._norm_cdf(x)) - x) < 1e-3


def test_psr_in_unit_interval_and_monotone():
    rng = np.random.default_rng(0)
    short = pd.Series(rng.normal(0.0005, 0.01, 250))
    long = pd.Series(rng.normal(0.0005, 0.01, 2500))
    a, b = robust.probabilistic_sharpe_ratio(short), robust.probabilistic_sharpe_ratio(long)
    assert 0.0 <= a <= 1.0 and 0.0 <= b <= 1.0
    assert b > a                      # more data → more confident in same edge


def test_min_track_record_length():
    rng = np.random.default_rng(1)
    pos = pd.Series(rng.normal(0.001, 0.01, 1000))
    assert robust.min_track_record_length(pos) > 0
    neg = pd.Series(rng.normal(-0.001, 0.01, 1000))
    assert robust.min_track_record_length(neg) == float("inf")


def test_expected_max_sharpe_grows_with_trials():
    assert robust.expected_max_sharpe(0.01, 2) < robust.expected_max_sharpe(0.01, 100)
    assert robust.expected_max_sharpe(0.0, 100) == 0.0


def test_deflated_sharpe_deflates():
    rng = np.random.default_rng(2)
    rets = pd.Series(rng.normal(0.0008, 0.01, 2000))
    trials = list(rng.normal(0.6, 0.4, 50))             # 50 annualised trial Sharpes
    psr = robust.probabilistic_sharpe_ratio(rets)
    dsr = robust.deflated_sharpe_ratio(rets, trials)
    assert 0.0 <= dsr["dsr"] <= 1.0
    assert dsr["n_trials"] == 50
    assert dsr["dsr"] <= psr + 1e-9                     # deflation never increases it


def test_pbo_low_when_one_config_dominates():
    rng = np.random.default_rng(3)
    T, N = 240, 10
    M = rng.normal(0, 0.01, (T, N))
    M[:, 0] += 0.01                                     # config 0 always best, every period
    res = robust.pbo_cscv(pd.DataFrame(M), n_splits=8)
    assert res["pbo"] < 0.1                             # robust selection → low PBO
    assert res["n_combinations"] == 70                 # C(8,4)


def test_pbo_around_half_for_noise():
    rng = np.random.default_rng(4)
    M = rng.normal(0, 0.01, (240, 12))                 # no genuine edge anywhere
    res = robust.pbo_cscv(pd.DataFrame(M), n_splits=8)
    assert 0.2 <= res["pbo"] <= 0.8                     # selection ≈ coin-flip
