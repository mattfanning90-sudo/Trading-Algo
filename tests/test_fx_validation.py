"""Overfitting-aware statistics: PSR, DSR, PBO, bet sizing."""
import numpy as np

from trading_algo.forex import validation as v


def test_norm_cdf_ppf_inverse():
    for p in (0.05, 0.25, 0.5, 0.84, 0.975):
        assert abs(v._norm_cdf(v._norm_ppf(p)) - p) < 1e-6
    assert abs(v._norm_cdf(0.0) - 0.5) < 1e-9


def _with_sharpe(rng, n, sr=0.05):
    """A return series standardised to exactly mean=sr, std=1 (so realized
    Sharpe is identical regardless of n)."""
    r = rng.normal(0, 1, n)
    r = (r - r.mean()) / r.std()
    return r + sr


def test_psr_increases_with_track_record():
    rng = np.random.default_rng(0)
    short = _with_sharpe(rng, 50)
    long = _with_sharpe(rng, 2000)
    # identical realized Sharpe, more data => more confident the true SR > 0
    assert v.probabilistic_sharpe_ratio(long) > v.probabilistic_sharpe_ratio(short)


def test_psr_in_unit_interval():
    rng = np.random.default_rng(1)
    r = rng.normal(0.02, 1.0, 500)
    p = v.probabilistic_sharpe_ratio(r)
    assert 0.0 <= p <= 1.0


def test_deflation_lowers_significance():
    rng = np.random.default_rng(2)
    r = rng.normal(0.06, 1.0, 1000)
    psr = v.probabilistic_sharpe_ratio(r)                     # N=1 benchmark 0
    dsr = v.deflated_sharpe_ratio(r, n_trials=50, sr_variance=0.02)
    assert dsr <= psr                                         # trying 50 configs raises the bar


def test_expected_max_sharpe_grows_with_trials():
    e10 = v.expected_max_sharpe(10, 0.01)
    e100 = v.expected_max_sharpe(100, 0.01)
    assert e100 > e10 > 0


def test_bet_size_monotonic_and_centered():
    p = np.array([0.5, 0.6, 0.7, 0.9])
    s = v.bet_size_from_prob(p)
    assert abs(s[0]) < 1e-9                 # p=0.5 -> no bet
    assert np.all(np.diff(s) > 0)           # more confidence -> bigger size
    assert s[-1] <= 1.0 and s[1] > 0


def test_pbo_high_for_pure_noise():
    """With only noise strategies, the in-sample best should not generalise."""
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, (600, 12))      # 12 worthless strategies
    pbo = v.pbo(noise, n_splits=10)
    assert 0.0 <= pbo <= 1.0
    assert pbo > 0.3                         # selection mostly fails to generalise


def test_pbo_low_for_one_dominant_strategy():
    rng = np.random.default_rng(4)
    M = rng.normal(0, 1, (600, 6))
    M[:, 0] += 0.3                           # strategy 0 has a real, persistent edge
    pbo = v.pbo(M, n_splits=10)
    assert pbo < 0.3                         # the genuinely-best one keeps winning OOS
