"""ML agents, dataset assembly, model bundles and the honest comparison."""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import features, ml_backtest
from trading_algo.forex import indicators as ind
from trading_algo.forex.agents import PairContext
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.ml_agent import ModelBundle, NeuralAgent, pooled_dataset
from trading_algo.forex.nn import MLP, StandardScaler
from trading_algo.forex.pairs import DEFAULT_UNIVERSE, get_pair


@pytest.fixture
def panel():
    return synthetic_panel(["EURUSD", "USDJPY"], start="2017-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


@pytest.fixture
def mixed_panel():
    """FX majors + crypto — the universe the model is actually trained on.

    The `panel` fixture above is FX-only, so it cannot show the imbalance the
    vol-normalised target exists to fix: crypto's ~4x volatility is precisely
    what makes a raw forward-return target lopsided.
    """
    return synthetic_panel(list(DEFAULT_UNIVERSE), start="2017-01-01", end="2023-01-01")


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
    X, y, t, pairs, cols, vols = pooled_dataset(panel, params, label="sharpe", horizon=1)
    assert len(X) == len(y) == len(t) == len(pairs) == len(vols)
    assert X.shape[1] == len(cols)
    assert np.isfinite(X).all()
    assert set(np.unique(pairs)) <= {"EURUSD", "USDJPY"}


def test_pooled_dataset_meta_is_binary(panel, params):
    X, y, t, pairs, cols, _ = pooled_dataset(panel, params, label="meta", horizon=1)
    assert "tilt" in cols and any(c.startswith("ag_") for c in cols)
    assert set(np.unique(y)).issubset({0.0, 1.0})


def test_pooled_target_is_vol_normalised(mixed_panel):
    """Crypto moves ~4x harder than FX. With a raw forward-return target it is
    30% of this fixture's rows but 92% of the squared target the loss sees, so
    the model is trained almost entirely on the instruments the technical agents
    were measured to be WORST on. (Same imbalance on the live panel, which has
    a shorter crypto history: 24% of rows, 96.3% of the signal.) Normalising by
    trailing vol makes each instrument contribute in proportion to its rows."""
    p = profile("balanced")
    X, y, times, pairs, cols, vols = pooled_dataset(mixed_panel, p, label="sharpe",
                                                    horizon=1)

    tot = float((y ** 2).sum())
    crypto = [s for s in set(pairs)
              if getattr(get_pair(s), "asset_class", "fx") == "crypto"]
    mask = np.isin(pairs, crypto)
    share_rows = mask.mean()
    share_signal = float((y[mask] ** 2).sum()) / tot

    assert abs(share_signal - share_rows) < 0.15, (
        f"crypto is {share_rows:.0%} of rows but {share_signal:.0%} of the "
        f"signal — the target is not vol-normalised")
    assert len(vols) == len(y)
    assert (vols > 0).all()


def test_pooled_vols_are_positive_on_every_label(params):
    """A forward-filled dead price must never leak a NaN into `vols`.

    Trailing vol is exactly 0.0 across a dead stretch and `pooled_dataset` maps
    that to NaN. On the sharpe branch those rows drop out on their own, because
    the target divides by vol. The meta target never touches vol, and the row
    survives `align_xy`: `build_features` keeps the literal 0.0 in its `vol_20`
    column, and `bb_z` survives on a ~1e-8 floating-point residual in the level
    std. Without an explicit guard that row is returned carrying `vols = NaN`.
    verify.py exists to hunt dead-price rows; the dataset must not manufacture
    them. `vols > 0` is a contract of pooled_dataset on EVERY label.
    """
    panel = synthetic_panel(["EURUSD", "USDJPY", "BTCUSD"],
                            start="2017-01-01", end="2023-01-01")
    bars = panel["EURUSD"]
    lo, n = 800, 60                 # past the 504-bar value_z warm-up, or the rows
    dead = float(bars["close"].iloc[lo])      # drop for an unrelated reason
    bars.iloc[lo:lo + n, :] = dead

    # Guard the guard: if the construction stops producing exactly-zero vol the
    # assertions below would pass vacuously.
    assert (ind.realized_vol(bars["close"], 20) == 0.0).any(), \
        "fixture no longer produces a dead-price stretch — the test is vacuous"

    for label in ("sharpe", "meta"):
        X, y, t, pairs, cols, vols = pooled_dataset(panel, params, label=label,
                                                    horizon=1)
        assert len(vols) == len(y) == len(X)
        assert np.isfinite(vols).all(), f"{label}: NaN vol survived a dead price"
        assert (vols > 0).all(), f"{label}: non-positive vol in the returned rows"


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
    X, y, t, pairs, cols, _ = pooled_dataset(panel, params, label="sharpe", horizon=1)
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


# ---- the model factory behind the walk-forward ---------------------------
def _small_panel_index(n_rows):
    """An irregular (pair, timestamp) index covering exactly `n_rows` rows."""
    from trading_algo.forex.panel_index import build_panel_index
    times, pairs, t = [], [], 0
    while len(times) < n_rows:
        for sym in (["A", "B", "C"] if t % 2 == 0 else ["A", "B"]):
            if len(times) == n_rows:
                break
            times.append(t); pairs.append(sym)
        t += 1
    return build_panel_index(np.array(times), np.array(pairs),
                             np.full(n_rows, 0.2),
                             {"A": 0.001, "B": 0.002, "C": 0.003})


def test_sharpe_factory_defaults_to_the_pooled_objective():
    """Default stays the legacy row-separable objective, so the cost-aware one
    can be switched on deliberately and the two compared like for like."""
    make = ml_backtest._sharpe_factory(4)
    m = make()
    assert m.task == "sharpe"
    assert m.layer_sizes[0] == 4 and m.layer_sizes[-1] == 1
    assert make() is not m                  # a fresh model per fold


def test_sharpe_factory_takes_seed_positionally():
    """`neural_oos_signal` calls `_sharpe_factory(len(cols))` positionally; any
    new argument must go AFTER seed or that call silently changes meaning."""
    assert ml_backtest._sharpe_factory(4, 7)().seed == 7


def test_sharpe_factory_cost_aware_builds_the_net_objective():
    """`cost_aware=True` selects the net-of-turnover portfolio objective."""
    m = ml_backtest._sharpe_factory(4, 0, cost_aware=True)()
    assert m.task == "sharpe_net"
    assert m.layer_sizes[0] == 4 and m.layer_sizes[-1] == 1


def test_sharpe_factory_leaves_the_panel_index_unset_so_a_miss_is_loud():
    """The index describes the rows of ONE fold, which the factory cannot know,
    so the walk-forward fills it per fold. Shipping `None` rather than a
    placeholder is the whole point: a fold that never fills it fails by name."""
    m = ml_backtest._sharpe_factory(4, 0, cost_aware=True)()
    assert m.panel_index is None
    X = np.random.default_rng(0).normal(size=(12, 4))
    y = np.random.default_rng(1).normal(size=(12, 1))
    with pytest.raises(ValueError, match="panel_index"):
        m.fit(X, y, epochs=1, batch_size=12)

    m.panel_index = _small_panel_index(12)   # what the walk-forward will do
    m.fit(X, y, epochs=2, batch_size=12, lr=1e-2)
    assert np.abs(m.predict(X)).max() <= 1.0 + 1e-9


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


def test_an_empty_grade_never_overwrites_a_passing_one(tmp_path):
    """Grading with nothing must leave an existing grade alone.

    `--no-ml` skips the ML strategies, so `res["metrics"]["neural_oos"]` is
    absent. Stamping that as a grade would write all-None over a passing score
    and silently demote a working model — a downgrade caused by a reporting
    flag, not by any change in the model.
    """
    from trading_algo.forex import promotion, train
    _graded_bundle(tmp_path, {"sharpe": 0.8, "dsr": 0.97})
    path = str(tmp_path / "models" / "neural_sharpe.json")

    train.record_evaluation(path, None)

    ok, reason = promotion.clears_floor(ModelBundle.load(path).meta["evaluation"])
    assert ok, f"the passing grade must survive: {reason}"


# ---- the walk-forward actually trains the cost-aware objective --------------
def test_neural_oos_signal_trains_the_cost_aware_objective(panel, params, monkeypatch):
    """Each fold's index must describe THAT FOLD's rows. A whole-panel index
    satisfies every guard the factory can offer and then mis-addresses every row
    — silently. A fold always holds fewer rows than the panel, so assert it."""
    X, *_ = pooled_dataset(panel, params, label="sharpe", horizon=1)
    built, real = [], ml_backtest._sharpe_factory

    def spy(n_feat, seed=0, **kw):
        make = real(n_feat, seed, **kw)

        def make_one():
            m = make()
            built.append((m, kw))
            return m
        return make_one

    monkeypatch.setattr(ml_backtest, "_sharpe_factory", spy)
    sig = ml_backtest.neural_oos_signal(panel, params, n_folds=3, min_train=200,
                                        epochs=5)
    assert built, "the walk-forward must build a model per fold"
    for m, kw in built:
        assert kw.get("cost_aware") is True
        assert m.task == "sharpe_net"
        assert m.panel_index is not None                  # filled in per fold
        assert 0 < len(m.panel_index.group) < len(X)      # this fold, not the panel
        assert (m.panel_index.cost > 0).all()             # costs always on
    assert len({len(m.panel_index.group) for m, _ in built}) > 1, "expanding window"
    assert not sig.dropna(how="all").empty


def test_neural_oos_signal_asks_for_a_validation_split_and_early_stopping(
        panel, params, monkeypatch):
    """`patience` alone does nothing: `fit` only early-stops when a validation
    set is supplied. The walk-forward carves one out of the fold's training rows,
    so the request must carry both."""
    seen, real = {}, ml_backtest.walk_forward_predict

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(ml_backtest, "walk_forward_predict", spy)
    ml_backtest.neural_oos_signal(panel, params, n_folds=3, min_train=200, epochs=3)
    assert seen["val_frac"] > 0
    assert seen["fit_kwargs"]["patience"] >= 1
    assert seen["index_factory"] is not None
    assert seen["fit_kwargs"]["batch_size"] >= 10 ** 6    # sharpe_net is full-batch


# ---- the grade itself must not be a single draw ----------------------------
def test_neural_oos_signal_is_seed_ensembled(panel, params):
    """A single seed is a single draw.

    `_sharpe_factory` defaulted to seed=0, so every out-of-sample number ever
    reported for this model — including the -0.62 Sharpe that let it trade the
    live books — came from one initialisation with unmeasured variance.
    Production already seed-ensembles the DEPLOYED bundle (`ModelBundle` averages
    several seeds); the grade describing it must be averaged the same way.
    """
    one = ml_backtest.neural_oos_signal(panel, params, n_folds=3, min_train=200,
                                        epochs=5, seeds=1)
    three = ml_backtest.neural_oos_signal(panel, params, n_folds=3, min_train=200,
                                          epochs=5, seeds=3)
    assert not one.empty and not three.empty
    assert one.shape == three.shape
    # Averaging is only meaningful if the seeds differ: a "3-seed" run that
    # reproduced seed 0 exactly would be the same single draw wearing a label.
    assert not np.allclose(one.fillna(0).to_numpy(), three.fillna(0).to_numpy()), \
        "averaging three seeds must not reproduce a single seed exactly"
    # ...and the ensemble must not quietly lose coverage: same tested rows.
    assert (one.notna().to_numpy() == three.notna().to_numpy()).all()
