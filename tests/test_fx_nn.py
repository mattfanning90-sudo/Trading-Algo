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


def _toy_panel(seed=0):
    """Three pairs over eight timestamps — small enough to difference by hand,
    irregular enough that group sizes and pair lengths differ."""
    from trading_algo.forex.panel_index import build_panel_index
    rng = np.random.default_rng(seed)
    times, pairs = [], []
    for t in range(8):
        for s in (["A", "B", "C"] if t % 2 == 0 else ["A", "B"]):
            times.append(t); pairs.append(s)
    times = np.array(times); pairs = np.array(pairs)
    vols = np.full(len(pairs), 0.2)
    idx = build_panel_index(times, pairs, vols, {"A": 0.001, "B": 0.002, "C": 0.003})
    r = rng.normal(0, 1, (len(pairs), 1))
    w = np.tanh(rng.normal(0, 1, (len(pairs), 1)))
    return w, r, idx


def _panel_of(n_rows):
    """An irregular panel with EXACTLY `n_rows` rows, so it can index a test
    matrix of a given height. The third pair is absent on odd timestamps, so
    group sizes differ, a pair's predecessor is not the adjacent row, and the
    truncated final timestamp holds a single row."""
    from trading_algo.forex.panel_index import build_panel_index
    times, pairs = [], []
    t = 0
    while len(times) < n_rows:
        for s in (["A", "B", "C"] if t % 2 == 0 else ["A", "B"]):
            if len(times) == n_rows:
                break
            times.append(t); pairs.append(s)
        t += 1
    vols = np.full(n_rows, 0.2)
    return build_panel_index(np.array(times), np.array(pairs), vols,
                             {"A": 0.001, "B": 0.002, "C": 0.003})


@pytest.mark.parametrize("task", ["regression", "binary", "sharpe", "sharpe_net"])
def test_gradient_check(task):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    if task == "binary":
        y = (rng.random((16, 1)) > 0.5).astype(float)
    else:                                   # regression target or forward returns
        y = rng.normal(size=(16, 1))
    kw = {}
    if task == "sharpe_net":
        # the panel objective needs to know which of the 16 rows share a
        # timestamp and which row precedes each within its own pair
        kw["panel_index"] = _panel_of(16)
    m = MLP([5, 7, 4, 1], hidden_act="tanh", task=task, l2=1e-3, dropout=0.0,
            seed=1, **kw)
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


# --- the net-portfolio-Sharpe objective ---------------------------------------

def test_sharpe_net_gradient_matches_finite_differences():
    """The analytic gradient must match central differences.

    Each w_i enters the loss TWICE — through its own row, and through the NEXT
    row's turnover term, which lands at a different timestamp. Omitting that
    second path yields a gradient that is wrong but trains happily. Only a
    numerical check catches it.
    """
    from trading_algo.forex import nn
    w, r, idx = _toy_panel()
    ann = np.sqrt(252.0)

    analytic = nn.sharpe_net_grad(w, r, idx, ann)

    h = 1e-6
    numeric = np.zeros_like(w)
    for i in range(len(w)):
        up, dn = w.copy(), w.copy()
        up[i, 0] += h
        dn[i, 0] -= h
        numeric[i, 0] = (nn.sharpe_net_loss(up, r, idx, ann)
                         - nn.sharpe_net_loss(dn, r, idx, ann)) / (2 * h)

    denom = np.maximum(np.abs(analytic) + np.abs(numeric), 1e-8)
    assert np.max(np.abs(analytic - numeric) / denom) < 1e-5, (
        "analytic gradient disagrees with finite differences")


def test_turnover_is_actually_charged():
    """A book that flips every bar must score worse than one that holds, for
    identical returns. Without this the cost term could be zero and the
    gradient check would still pass."""
    from trading_algo.forex import nn
    _, r, idx = _toy_panel()
    ann = np.sqrt(252.0)
    hold = np.full((len(r), 1), 0.5)
    flip = np.where(np.arange(len(r)).reshape(-1, 1) % 2 == 0, 0.5, -0.5)

    assert nn.sharpe_net_loss(flip, r, idx, ann) > nn.sharpe_net_loss(hold, r, idx, ann)


def test_sharpe_net_charges_the_opening_trade():
    """The position before a pair's first row is flat, so that row pays
    cost*|w|. A book that is flat everywhere except one pair's first bar must
    still be charged — otherwise the model opens for free."""
    from trading_algo.forex import nn
    _, r, idx = _toy_panel()
    n = len(r)
    zero = np.zeros((n, 1))
    opened = zero.copy()
    first = int(np.flatnonzero(idx.prev < 0)[0])
    opened[first, 0] = 1.0
    r0 = np.zeros((n, 1))                     # no gross return either way

    assert nn._net_portfolio_returns(zero, r0, idx).sum() == 0.0
    charged = nn._net_portfolio_returns(opened, r0, idx)
    # one open, then one close on the pair's next bar
    assert charged.sum() < 0.0
    assert charged[idx.group[first]] == pytest.approx(
        -idx.cost[first] / idx.group_size[idx.group[first]])


def test_sharpe_net_requires_a_panel_index():
    """`panel_index` describes the rows being trained on. Without it the loss
    has no notion of a timestamp or a predecessor, so fail loudly rather than
    quietly scoring something else."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    y = rng.normal(size=(16, 1))
    m = MLP([5, 4, 1], hidden_act="tanh", task="sharpe_net", seed=0)
    with pytest.raises(ValueError, match="panel_index"):
        m._loss(m._forward(X)[0], y)


def test_sharpe_net_fit_refuses_without_a_panel_index():
    """Fitting with no panel index must be a loud, NAMED failure and must leave
    the weights untouched.

    `ml_backtest._sharpe_factory(..., cost_aware=True)` deliberately builds the
    model with `panel_index=None` for the walk-forward to fill in per fold. That
    is only safe while this holds: a caller that forgets to fill it gets a
    ValueError naming the attribute it left out, and an untrained model —
    never one that quietly scored something other than the net portfolio."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    y = rng.normal(size=(16, 1))
    m = MLP([5, 4, 1], hidden_act="tanh", task="sharpe_net", seed=0)
    before = [w.copy() for w in m.W]
    with pytest.raises(ValueError, match="panel_index"):
        m.fit(X, y, epochs=1, batch_size=16)
    for w0, w1 in zip(before, m.W):          # refused, not partially trained
        assert np.array_equal(w0, w1)


def test_sharpe_net_refuses_shuffled_or_partial_batches():
    """`panel_index` is keyed on ROW POSITION, so a shuffled or partial batch
    would charge turnover against the wrong bar — silently. Training must be
    full-batch in the original row order, and anything else is an error, not a
    quietly different objective."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    y = rng.normal(size=(16, 1))
    m = MLP([5, 4, 1], hidden_act="tanh", task="sharpe_net", seed=0,
            panel_index=_panel_of(16))
    with pytest.raises(ValueError, match="full-batch"):
        m.fit(X, y, epochs=1, batch_size=4)
    with pytest.raises(ValueError, match="panel index"):
        m.fit(X, y, epochs=1, batch_size=16, X_val=X, y_val=y)


def test_sharpe_net_trains_in_original_row_order():
    """Full-batch training must not permute the rows: the loss is a property of
    the whole panel and `panel_index` addresses it positionally."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    y = rng.normal(size=(16, 1))
    idx = _panel_of(16)
    m = MLP([5, 6, 1], hidden_act="tanh", task="sharpe_net", l2=0.0, seed=0,
            panel_index=idx)
    before = m._loss(m._forward(X)[0], y)
    m.fit(X, y, epochs=60, batch_size=16, lr=1e-2)
    after = m._loss(m._forward(X)[0], y)
    assert after < before


def test_sharpe_net_panel_index_must_describe_the_training_rows():
    """The WHOLE-panel index passes the `is None` check and then mis-addresses
    every row: `group` names timestamps these rows do not hold and `prev` points
    past the end of the batch. Each walk-forward fold trains on a subset, so the
    only cheap thing that can catch a stale index is the row count."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5))
    y = rng.normal(size=(16, 1))
    m = MLP([5, 4, 1], hidden_act="tanh", task="sharpe_net", seed=0,
            panel_index=_panel_of(40))            # an index for a bigger panel
    before = [w.copy() for w in m.W]
    with pytest.raises(ValueError, match="describes 40 rows"):
        m.fit(X, y, epochs=1, batch_size=16)
    for w0, w1 in zip(before, m.W):               # refused, not partially trained
        assert np.array_equal(w0, w1)


def test_sharpe_net_validation_needs_an_index_of_its_own_rows():
    """Validation rows are a different panel — their own timestamps, their own
    predecessors. Without an index of their own they cannot be scored at all;
    with the wrong one they are scored against rows that are not there."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 5)); y = rng.normal(size=(16, 1))
    Xv = rng.normal(size=(8, 5)); yv = rng.normal(size=(8, 1))
    m = MLP([5, 4, 1], hidden_act="tanh", task="sharpe_net", seed=0,
            panel_index=_panel_of(16))
    with pytest.raises(ValueError, match="panel index"):
        m.fit(X, y, epochs=1, batch_size=16, X_val=Xv, y_val=yv)
    with pytest.raises(ValueError, match="describes 16 rows"):
        m.fit(X, y, epochs=1, batch_size=16, X_val=Xv, y_val=yv,
              val_panel_index=_panel_of(16))
    m.fit(X, y, epochs=2, batch_size=16, lr=1e-2, X_val=Xv, y_val=yv,
          val_panel_index=_panel_of(8))           # sized to the validation rows
    assert np.abs(m.predict(Xv)).max() <= 1.0 + 1e-9


def test_early_stopping_fires_and_restores_the_best_weights():
    """`fit` has implemented early stopping since it was written and nothing had
    ever used it: no caller passed a validation set, so `patience` was inert and
    training ran blind for every epoch it was given. Here it must actually stop
    short and leave the best-scoring weights in place, not the last ones."""
    rng = np.random.default_rng(0)
    n, nv, epochs = 24, 12, 300
    X = rng.normal(size=(n, 5)); y = rng.normal(size=(n, 1))
    Xv = rng.normal(size=(nv, 5)); yv = rng.normal(size=(nv, 1))
    vidx = _panel_of(nv)
    m = MLP([5, 8, 1], hidden_act="tanh", task="sharpe_net", l2=0.0, seed=0,
            panel_index=_panel_of(n))

    val_losses, steps = [], []
    real_loss, real_step = m._loss, m._adam_step

    def spy_loss(out, y_, panel=None):
        v = real_loss(out, y_, panel=panel)
        val_losses.append(v)
        return v

    def spy_step(*a, **kw):
        steps.append(1)
        return real_step(*a, **kw)

    m._loss, m._adam_step = spy_loss, spy_step
    m.fit(X, y, epochs=epochs, batch_size=n, lr=1e-2, X_val=Xv, y_val=yv,
          val_panel_index=vidx, patience=5)
    m._loss, m._adam_step = real_loss, real_step

    assert len(steps) < epochs, "patience must stop training short of `epochs`"
    assert len(val_losses) == len(steps), "the validation loss is scored each epoch"
    final = real_loss(m._forward(Xv)[0], yv, panel=vidx)
    assert final == pytest.approx(min(val_losses), abs=1e-9), "best weights restored"
