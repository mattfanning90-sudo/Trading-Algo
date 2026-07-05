"""ML agents, dataset assembly, model bundles and the honest comparison."""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import features, ml_backtest
from trading_algo.forex.agents import PairContext
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.ml_agent import ModelBundle, NeuralAgent, pooled_dataset
from trading_algo.forex.nn import MLP, StandardScaler
from trading_algo.forex.pairs import get_pair


@pytest.fixture
def panel():
    return synthetic_panel(["EURUSD", "USDJPY"], start="2017-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


# ---- features ------------------------------------------------------------
def test_features_are_causal(panel):
    bars = panel["EURUSD"]
    f1 = features.build_features(bars, pair=get_pair("EURUSD"))
    k = len(bars) // 2
    spiked = bars.copy()
    spiked.iloc[k, spiked.columns.get_loc("close")] *= 1.2
    f2 = features.build_features(spiked, pair=get_pair("EURUSD"))
    pd.testing.assert_frame_equal(f1.iloc[:k], f2.iloc[:k])


def test_triple_barrier_labels_binary(panel):
    bars = panel["EURUSD"]
    from trading_algo.forex import indicators as ind
    atr = ind.atr(bars["high"], bars["low"], bars["close"], 14)
    side = pd.Series(1.0, index=bars.index)
    y = features.triple_barrier_labels(bars["close"], atr, side, max_h=10)
    vals = set(np.unique(y.dropna()))
    assert vals.issubset({0.0, 1.0})


# ---- pooled dataset ------------------------------------------------------
def test_pooled_dataset_sharpe(panel, params):
    X, y, t, pairs, cols = pooled_dataset(panel, params, label="sharpe", horizon=1)
    assert len(X) == len(y) == len(t) == len(pairs)
    assert X.shape[1] == len(cols)
    assert np.isfinite(X).all()
    assert set(np.unique(pairs)) <= {"EURUSD", "USDJPY"}


def test_pooled_dataset_meta_is_binary(panel, params):
    X, y, t, pairs, cols = pooled_dataset(panel, params, label="meta", horizon=1)
    assert "tilt" in cols and any(c.startswith("ag_") for c in cols)
    assert set(np.unique(y)).issubset({0.0, 1.0})


# ---- model bundle --------------------------------------------------------
def test_model_bundle_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, 5))
    scaler = StandardScaler().fit(X)
    m = MLP([5, 6, 1], task="sharpe", seed=0)
    m.fit(scaler.transform(X), rng.normal(size=(60, 1)), epochs=10, batch_size=60, lr=1e-2)
    bundle = ModelBundle("sharpe", [f"f{i}" for i in range(5)], [m], scaler)
    p1 = bundle.predict(X)
    path = tmp_path / "b.json"
    bundle.save(str(path))
    p2 = ModelBundle.load(str(path)).predict(X)
    np.testing.assert_allclose(p1, p2, rtol=1e-10)


# ---- neural agent --------------------------------------------------------
def test_neural_agent_flat_without_model(panel, params):
    agent = NeuralAgent(bundle=None)
    sig = agent.generate(panel["EURUSD"], PairContext(get_pair("EURUSD")), params)
    assert (sig == 0.0).all()


def test_neural_agent_signal_in_range_and_causal(panel, params):
    # tiny trained bundle on the pooled set
    X, y, t, pairs, cols = pooled_dataset(panel, params, label="sharpe", horizon=1)
    scaler = StandardScaler().fit(X)
    m = MLP([len(cols), 8, 1], task="sharpe", seed=0)
    m.fit(scaler.transform(X), y.reshape(-1, 1), epochs=20, batch_size=100000, lr=1e-2)
    agent = NeuralAgent(ModelBundle("sharpe", cols, [m], scaler))

    bars = panel["EURUSD"]
    sig = agent.generate(bars, PairContext(get_pair("EURUSD")), params)
    assert sig.between(-1, 1).all()
    k = len(bars) // 2 + 100
    spiked = bars.copy()
    spiked.iloc[k, spiked.columns.get_loc("close")] *= 1.2
    sig2 = agent.generate(spiked, PairContext(get_pair("EURUSD")), params)
    pd.testing.assert_series_equal(sig.iloc[:k], sig2.iloc[:k])


# ---- comparison harness --------------------------------------------------
def test_run_ml_backtest_rule_based(panel, params):
    res = ml_backtest.run_ml_backtest(panel, params, include_ml=False, n_folds=4)
    assert 0.0 <= res["pbo"] <= 1.0
    assert res["n_trials"] >= 7              # 5 agents + 2 ensembles
    for name, m in res["metrics"].items():
        assert {"Sharpe", "PSR", "DSR"} <= set(m)
        assert 0.0 <= m["PSR"] <= 1.0
    assert "Probability of Backtest Overfitting" in ml_backtest.format_report(res)


def test_neural_oos_signal_runs(panel, params):
    sigp = ml_backtest.neural_oos_signal(panel, params, n_folds=3, min_train=200,
                                         epochs=15)
    assert set(sigp.columns) == {"EURUSD", "USDJPY"}
    assert sigp.abs().max().max() <= 1.0 + 1e-9
