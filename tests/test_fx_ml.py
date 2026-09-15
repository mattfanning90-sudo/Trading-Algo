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


# --- the promotion floor: a model must clear its own grade to trade ----------
def _graded_bundle(tmp_path, grade):
    """A minimal saved bundle whose meta carries (or omits) an OOS grade."""
    from trading_algo.forex.nn import MLP, StandardScaler
    cols = [f"f{i}" for i in range(4)]
    m = MLP([4, 3, 1], task="sharpe", seed=0)
    m.fit(np.zeros((12, 4)), np.zeros((12, 1)), epochs=1, batch_size=12)
    meta = {"seeds": 1, "n_samples": 12}
    if grade is not None:
        meta["evaluation"] = grade
    b = ModelBundle("sharpe", cols, [m], StandardScaler().fit(np.zeros((12, 4))), meta)
    d = tmp_path / "models"
    d.mkdir(exist_ok=True)
    b.save(str(d / "neural_sharpe.json"))
    return str(d)


def test_ml_pool_refuses_a_model_that_failed_its_own_grade(tmp_path):
    """A model whose recorded out-of-sample Sharpe is negative must not trade.

    This is the defect that let a measured -0.62 Sharpe / DSR 0.00 model trade
    the live books for weeks: `train.py` graded it honestly every week and
    `ml_pool` loaded it regardless, because nothing connected the grade to the
    decision. champions.py gates rigorously and promotes nothing; the ML path
    gated nothing and promoted everything.
    """
    from trading_algo.forex import fx_book
    md = _graded_bundle(tmp_path, {"sharpe": -0.62, "dsr": 0.0})

    pool = fx_book.ml_pool(models_dir=md)

    assert not any(a.name == "neural" for a in pool.agents), \
        "a model that failed its own grade must not reach the pool"
    assert len(pool.agents) == 5, "falls back to the five technical agents"


def test_ml_pool_refuses_an_ungraded_model(tmp_path):
    """No recorded grade means it was never evaluated — that is not a pass."""
    from trading_algo.forex import fx_book
    md = _graded_bundle(tmp_path, None)

    pool = fx_book.ml_pool(models_dir=md)

    assert not any(a.name == "neural" for a in pool.agents)


def test_ml_pool_accepts_a_model_that_cleared_the_floor(tmp_path):
    """The gate must not be a blanket refusal — a passing model still trades."""
    from trading_algo.forex import fx_book
    md = _graded_bundle(tmp_path, {"sharpe": 0.8, "dsr": 0.97})

    pool = fx_book.ml_pool(models_dir=md)

    assert any(a.name == "neural" for a in pool.agents)


def test_training_stamps_the_grade_onto_the_saved_model(tmp_path):
    """The grade must travel WITH the model, or the gate can never pass.

    `train_models` saves the bundle and `run_ml_backtest` grades it afterwards,
    so the score lived only in stdout and a markdown report. A gate that reads
    `meta["evaluation"]` would refuse every model forever — correct, but useless.
    """
    from trading_algo.forex import promotion, train
    _graded_bundle(tmp_path, None)               # saved, ungraded
    path = str(tmp_path / "models" / "neural_sharpe.json")
    assert not promotion.clears_floor(ModelBundle.load(path).meta.get("evaluation"))[0]

    train.record_evaluation(path, {"Sharpe": 0.8, "DSR": 0.97, "CAGR": 0.04})

    ok, reason = promotion.clears_floor(ModelBundle.load(path).meta["evaluation"])
    assert ok, reason


def test_synthetic_runs_never_stamp_a_passing_grade(tmp_path):
    """Invariant #5: synthetic numbers are a pipeline test, never performance.

    A synthetic run must not be able to promote a model, however good its
    fabricated Sharpe looks.
    """
    from trading_algo.forex import promotion, train
    _graded_bundle(tmp_path, None)
    path = str(tmp_path / "models" / "neural_sharpe.json")

    train.record_evaluation(path, {"Sharpe": 9.9, "DSR": 1.0}, synthetic=True)

    ok, _ = promotion.clears_floor(ModelBundle.load(path).meta["evaluation"])
    assert not ok, "a synthetic grade must never clear the floor"
