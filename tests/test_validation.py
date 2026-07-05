"""Backlog F2 / F19 / foundation P0-E: shared overfitting-aware validation."""
import numpy as np
import pytest

from trading_algo import validation as val


@pytest.fixture
def rng():
    return np.random.default_rng(0)


# --- P0-E: one shared implementation ---------------------------------------
def test_forex_reexports_the_same_functions():
    from trading_algo.forex import validation as fx_val
    # identical function objects — no second copy of the math
    assert fx_val.sharpe_ratio is val.sharpe_ratio
    assert fx_val.deflated_sharpe_ratio is val.deflated_sharpe_ratio
    assert fx_val.pbo is val.pbo


# --- core stats behave ------------------------------------------------------
def test_dsr_decreases_with_more_trials(rng):
    r = rng.normal(0.001, 0.01, 500)
    dsr_1 = val.deflated_sharpe_ratio(r, n_trials=1)
    dsr_50 = val.deflated_sharpe_ratio(r, n_trials=50)
    assert dsr_50 <= dsr_1, "deflation must penalise more trials"


def test_psr_in_unit_interval(rng):
    r = rng.normal(0.0005, 0.01, 300)
    assert 0.0 <= val.probabilistic_sharpe_ratio(r) <= 1.0


# --- F19 Sharpe haircut -----------------------------------------------------
def test_haircut_equals_raw_for_single_trial(rng):
    r = rng.normal(0.001, 0.01, 400)
    hc = val.sharpe_haircut(r, n_trials=1)
    assert hc["haircut_sharpe_ann"] == hc["raw_sharpe_ann"]
    assert hc["deflation_ann"] == 0.0


def test_haircut_below_raw_for_many_trials(rng):
    r = rng.normal(0.001, 0.01, 400)
    hc = val.sharpe_haircut(r, n_trials=100)
    assert hc["haircut_sharpe_ann"] <= hc["raw_sharpe_ann"]
    assert hc["deflation_ann"] > 0.0


# --- F2 overfitting gate ----------------------------------------------------
def test_gate_single_config_skips_pbo(rng):
    r = rng.normal(0.001, 0.01, 300).reshape(-1, 1)
    gate = val.overfitting_gate(r, n_trials=1)
    assert gate["pbo"] is None
    assert gate["n_configs"] == 1
    assert isinstance(gate["passed"], bool)


def test_gate_multi_config_reports_pbo_and_trials(rng):
    M = rng.normal(0.0, 0.01, (400, 8))
    gate = val.overfitting_gate(M, n_trials=8)
    assert gate["pbo"] is not None and 0.0 <= gate["pbo"] <= 1.0
    assert 0.0 <= gate["dsr"] <= 1.0
    assert gate["n_trials"] == 8 and gate["n_configs"] == 8
    assert 0 <= gate["best_config"] < 8
    assert "DSR" in gate["verdict"] and "PBO" in gate["verdict"]


def test_pure_noise_selection_is_flagged_overfit(rng):
    # 20 columns of pure noise: the in-sample winner is luck, so PBO should be high
    M = rng.normal(0.0, 0.01, (600, 20))
    gate = val.overfitting_gate(M, n_trials=20)
    assert gate["pbo"] >= 0.4, f"expected high PBO on pure noise, got {gate['pbo']}"
    assert gate["passed"] is False
