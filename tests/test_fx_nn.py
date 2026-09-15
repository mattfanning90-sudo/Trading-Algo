"""Correctness of the pure-NumPy deep-learning core (nn.py)."""
import numpy as np
import pytest

from trading_algo.forex.nn import MLP, StandardScaler


def _numeric_grad(model, X, y, eps=1e-5):
    """Finite-difference gradient of the loss w.r.t. every weight matrix."""
    grads = []
    for i in range(len(model.W)):
        g = np.zeros_like(model.W[i])
        it = np.nditer(model.W[i], flags=["multi_index"])
        while not it.finished:
            idx = it.multi_index
            orig = model.W[i][idx]
            model.W[i][idx] = orig + eps
            lp = model._loss(model._forward(X)[0], y)
            model.W[i][idx] = orig - eps
            lm = model._loss(model._forward(X)[0], y)
            model.W[i][idx] = orig
            g[idx] = (lp - lm) / (2 * eps)
            it.iternext()
        grads.append(g)
    return grads


@pytest.mark.parametrize("task", ["regression", "binary", "sharpe"])
def test_gradient_check(task):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    if task == "binary":
        y = (rng.random((16, 1)) > 0.5).astype(float)
    else:                                   # regression target or forward returns
        y = rng.normal(size=(16, 1))
    m = MLP([5, 7, 4, 1], hidden_act="tanh", task=task, l2=1e-3, dropout=0.0, seed=1)
    out, cache = m._forward(X, train=False)
    gW, _ = m._backward(out, m._prep_y(y), cache)
    gW_num = _numeric_grad(m, X, m._prep_y(y))
    for a, b in zip(gW, gW_num):
        np.testing.assert_allclose(a, b, rtol=1e-4, atol=1e-6)


def test_sharpe_net_learns_profitable_position():
    """With a learnable edge (return correlated with a feature), the Sharpe-loss
    net should output positions that are positively correlated with returns."""
    rng = np.random.default_rng(0)
    n = 600
    signal = rng.normal(size=(n, 1))
    fwd_ret = 0.4 * signal[:, 0] + rng.normal(0, 1.0, n)      # noisy but real edge
    X = np.hstack([signal, rng.normal(size=(n, 3))])
    m = MLP([4, 16, 1], hidden_act="tanh", task="sharpe", l2=1e-4, seed=0)
    m.fit(X, fwd_ret.reshape(-1, 1), epochs=300, batch_size=n, lr=1e-2)  # full-batch
    pos = m.predict(X)[:, 0]
    realized = pos * fwd_ret
    sharpe = realized.mean() / (realized.std() + 1e-9)
    assert (pos >= -1).all() and (pos <= 1).all()
    assert np.corrcoef(pos, signal[:, 0])[0, 1] > 0.3          # learned the edge
    assert sharpe > 0.05


def test_overfits_tiny_classification():
    """A flexible net must drive a small separable set to near-zero error."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 4))
    w = rng.normal(size=(4, 1))
    y = (X @ w + 0.1 * rng.normal(size=(40, 1)) > 0).astype(float)
    m = MLP([4, 16, 16, 1], hidden_act="relu", task="binary", l2=0.0, seed=0)
    m.fit(X, y, epochs=400, batch_size=40, lr=5e-3)
    acc = ((m.predict(X) > 0.5).astype(float) == y).mean()
    assert acc >= 0.95


def test_training_reduces_loss():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, 6))
    y = (X[:, :3].sum(axis=1, keepdims=True) > 0).astype(float)
    m = MLP([6, 12, 1], hidden_act="relu", task="binary", seed=0)
    before = m._loss(m._forward(X)[0], y)
    m.fit(X, y, epochs=100, batch_size=32, lr=3e-3)
    after = m._loss(m._forward(X)[0], y)
    assert after < before * 0.8


def test_predict_proba_in_unit_interval():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(30, 5))
    m = MLP([5, 8, 1], task="binary", seed=0)
    p = m.predict_proba(X)
    assert p.shape == (30, 1)
    assert (p >= 0).all() and (p <= 1).all()


def test_save_load_roundtrip(tmp_path):
    rng = np.random.default_rng(3)
    X = rng.normal(size=(10, 4))
    m = MLP([4, 6, 1], task="binary", seed=0)
    m.fit(X, (rng.random((10, 1)) > 0.5).astype(float), epochs=10, lr=1e-2)
    p1 = m.predict(X)
    path = tmp_path / "model.json"
    m.save(str(path))
    m2 = MLP.load(str(path))
    np.testing.assert_allclose(p1, m2.predict(X), rtol=1e-12)


def test_scaler_no_leakage():
    scaler = StandardScaler().fit(np.array([[0.0], [2.0], [4.0]]))
    # mean 2, std ~1.633; transform uses only fitted stats
    out = scaler.transform(np.array([[2.0]]))
    np.testing.assert_allclose(out, [[0.0]], atol=1e-9)
    d = scaler.to_dict()
    assert StandardScaler.from_dict(d).mean_[0] == 2.0


def test_early_stopping_restores_best():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(120, 5)); y = (X[:, 0:1] > 0).astype(float)
    Xv = rng.normal(size=(40, 5)); yv = (Xv[:, 0:1] > 0).astype(float)
    m = MLP([5, 10, 1], task="binary", seed=0)
    m.fit(X, y, X_val=Xv, y_val=yv, epochs=500, patience=10, lr=3e-3)
    # converged to a sane validation accuracy
    assert ((m.predict(Xv) > 0.5) == yv).mean() >= 0.8


# --- panel index: row bookkeeping for the cost-aware objective -----------------

def test_panel_index_links_rows_within_each_pair():
    """The cost term needs to know each row's predecessor in its OWN pair's
    timeline, and the portfolio return needs rows grouped by timestamp. Two
    pairs interleaved in time must not link to each other."""
    from trading_algo.forex.panel_index import build_panel_index
    times = np.array(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
                     dtype="datetime64[D]")
    pairs = np.array(["EURUSD", "GBPUSD", "EURUSD", "GBPUSD"])
    vols = np.array([0.10, 0.20, 0.10, 0.20])
    idx = build_panel_index(times, pairs, vols, {"EURUSD": 0.001, "GBPUSD": 0.002})

    assert idx.n_groups == 2
    assert list(idx.group) == [0, 0, 1, 1]
    assert list(idx.group_size) == [2.0, 2.0]
    assert list(idx.prev) == [-1, -1, 0, 1]       # row 2 follows row 0 (both EURUSD)
    assert list(idx.nxt) == [2, 3, -1, -1]
    assert idx.cost[0] == pytest.approx(0.001 / 0.10)
    assert idx.cost[1] == pytest.approx(0.002 / 0.20)


def test_panel_index_links_by_time_not_row_position():
    """`pooled_dataset` concatenates per symbol, so rows are NOT globally
    time-sorted. The predecessor must be the previous row in the pair's own
    TIMELINE, whatever order the rows arrive in — charging turnover against the
    wrong bar would price a trade that never happened. A pair present at only one
    timestamp has no neighbour in either direction."""
    from trading_algo.forex.panel_index import build_panel_index
    # EURUSD rows arrive out of order: Jan-02, Jan-01, Jan-03. GBPUSD appears once.
    times = np.array(["2024-01-02", "2024-01-01", "2024-01-03", "2024-01-01"],
                     dtype="datetime64[D]")
    pairs = np.array(["EURUSD", "EURUSD", "EURUSD", "GBPUSD"])
    vols = np.array([0.10, 0.10, 0.10, 0.20])
    idx = build_panel_index(times, pairs, vols, {"EURUSD": 0.001, "GBPUSD": 0.002})

    # groups are keyed on the timestamp, not the row order
    assert idx.n_groups == 3
    assert list(idx.group) == [1, 0, 2, 0]        # Jan-01 -> 0, Jan-02 -> 1, Jan-03 -> 2
    assert list(idx.group_size) == [2.0, 1.0, 1.0]
    # chronological EURUSD chain is row 1 -> row 0 -> row 2
    assert list(idx.prev) == [1, -1, 0, -1]
    assert list(idx.nxt) == [2, 0, -1, -1]
    # the lone GBPUSD row links to nothing in either direction
    assert idx.prev[3] == -1 and idx.nxt[3] == -1


def test_panel_index_cost_is_zero_when_it_cannot_be_priced():
    """Belt-and-braces for callers that build an index from something other than
    `pooled_dataset` (which already guarantees vols > 0): an unknown pair and a
    non-positive vol must charge nothing rather than emit NaN/inf, which would
    poison the whole batch's loss."""
    from trading_algo.forex.panel_index import build_panel_index
    times = np.array(["2024-01-01", "2024-01-02", "2024-01-03"],
                     dtype="datetime64[D]")
    pairs = np.array(["EURUSD", "EURUSD", "XAUUSD"])
    vols = np.array([0.10, 0.0, 0.20])           # row 1 has a dead-price vol
    idx = build_panel_index(times, pairs, vols, {"EURUSD": 0.001})

    assert np.isfinite(idx.cost).all()
    assert idx.cost[0] == pytest.approx(0.01)
    assert idx.cost[1] == 0.0                     # vol <= 0 -> uncharged, not inf
    assert idx.cost[2] == 0.0                     # pair absent from half_spreads
