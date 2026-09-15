# Cost-Aware Neural Objective Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train the FX neural agent on the Sharpe ratio of the **portfolio return series net of trading costs**, so it learns which moves are worth paying for — instead of learning a gross signal whose entire edge is then consumed by turnover.

**Architecture:** Replace the pooled cross-sectional Sharpe objective with a portfolio-level one that aggregates positions per timestamp and charges the half-spread on every position change. The target is vol-normalised so every instrument contributes comparably. The analytic gradient is hand-derived and verified against finite differences before any model is trained with it.

**Tech Stack:** Pure NumPy (no new dependencies — `nn.py` is a from-scratch MLP by design), pandas for panel assembly, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-champion-challenger-learning-design.md` (this plan is its Phase 2)

## Global Constraints

- **No new dependencies.** `requirements.txt` is numpy/pandas/yfinance/pyarrow only. `nn.py` is deliberately from-scratch so the project runs offline and in CI.
- **pandas >= 3.0.5** (`requirements.txt`). Local envs below this fail `tests/test_fx_sessions.py` for unrelated reasons.
- **Invariant #1 — no lookahead.** Every feature at bar *t* uses data ≤ *t*. Labels look forward and are training-only.
- **Invariant #2 — costs always on.** No metric is ever reported gross.
- **Invariant #3 — one weight function.** This plan changes how the neural *agent* produces a signal. It must not add a second copy of `fx_strategy.compute_targets`.
- **Invariant #5 — synthetic is a pipeline test, never performance.** No synthetic number may be presented as a result or stamped as a model grade.
- **Ruff clean** (`python3 -m ruff check trading_algo tests`) — CI runs it as a gate.
- **Measured baseline to beat:** vol-normalised target, current objective → gross OOS Sharpe **+0.60**, net after half-spread **−0.44**. Raw target → net **−0.65**.

---

## The objective, derived

This section is the reference for Task 3. Get it wrong and every later task
silently trains on a broken signal, so it is written out in full.

### Notation

Rows are sorted by `(pair, time)`. Row *i* has pair `p(i)` and timestamp `t(i)`.

| symbol | meaning |
|---|---|
| `w_i = tanh(z_i)` | the position for row *i*, in [−1, 1] |
| `r_i` | vol-normalised forward return: `raw_forward_return / trailing_vol` |
| `k_i` | cost coefficient: `half_spread_{p(i)} / vol_i` — the spread expressed in the *same* vol-normalised units as `r_i` |
| `prev(i)` | previous row for the same pair, or −1 if this is the pair's first row |
| `m_t` | number of rows at timestamp *t* |
| `T` | number of distinct timestamps |
| `A` | annualisation factor, `sqrt(periods_per_year)` |

**Why vol-normalised, and why costs must be normalised too.** `risk.py` applies
vol targeting downstream, scaling positions roughly inversely with volatility.
So what the book actually earns is approximately `w · r_raw / vol` — which *is*
the vol-normalised return. Training on it therefore matches the live book rather
than fighting it. The cost must be divided by the same `vol_i`, or the objective
compares a normalised return against an unnormalised cost and mis-prices every
trade.

### Forward

Net portfolio return at timestamp *t*:

```
N_t = (1/m_t) · Σ_{i : t(i)=t} [ w_i·r_i − k_i·|w_i − w_{prev(i)}| ]
```

The position before a pair's first row is **0**, so its first bar pays
`k_i·|w_i|` — you pay to open.

```
μ = (1/T) Σ_t N_t
var = (1/T) Σ_t (N_t − μ)²          (population variance)
σ = sqrt(var + ε)
L = −A · μ / σ                       (negative Sharpe; lower is better)
```

### Backward

The outer derivative is identical in form to the existing `task="sharpe"`
gradient, with `n → T` and `pnl → N` (verified against `nn.py:168-177`):

```
dL/dN_t = −A / (T·σ) · [ 1 − μ·(N_t − μ)/(var + ε) ]
```

Each `w_i` enters the objective **twice**: once through its own row, and once
through the *next* row's cost term, which lands at a different timestamp.

```
dL/dw_i =  dL/dN_{t(i)} · (1/m_{t(i)}) · ( r_i − k_i·sign(w_i − w_{prev(i)}) )
         + dL/dN_{t(j)} · (1/m_{t(j)}) · ( k_j·sign(w_j − w_i) )      where j = next(i)
```

The second term is dropped when *i* is the last row for its pair. `sign(0)` is
taken as 0 — the standard subgradient at the kink of `|·|`.

Finally, chain through the output tanh:

```
dL/dz_i = dL/dw_i · (1 − w_i²)
```

**The second term is the part that is easy to forget.** Omitting it produces a
gradient that is wrong but plausible-looking — the model still trains, just
toward the wrong thing. Task 3's numerical gradient check exists to catch
exactly that.

---

## File Structure

| file | responsibility |
|---|---|
| `trading_algo/forex/panel_index.py` | **new** — turns `(times, pairs, spreads, vols)` into the index arrays the objective needs: timestamp groups, per-pair prev/next links, cost coefficients. Pure, no model knowledge. |
| `trading_algo/forex/nn.py` | **modify** — add `task="sharpe_net"`: forward loss, analytic gradient, and the `panel_index` the two read. |
| `trading_algo/forex/ml_agent.py` | **modify** — `pooled_dataset` returns vol-normalised targets and the arrays `panel_index` needs. |
| `trading_algo/forex/ml_backtest.py` | **modify** — `_sharpe_factory` builds `sharpe_net` models; `neural_oos_signal` passes the index and a validation split. |
| `trading_algo/forex/pairs.py` | **modify** — `DEFAULT_UNIVERSE` gains the six unused crosses. |
| `trading_algo/forex/fx_config.py` | **modify** — `START` moves to 2003-12-01. |
| `tests/test_fx_nn.py` | **modify** — the gradient check and loss tests. |
| `tests/test_fx_ml.py` | **modify** — dataset-level tests (vol normalisation, the null). |

---

### Task 1: Vol-normalised target

**Files:**
- Modify: `trading_algo/forex/ml_agent.py` (`pooled_dataset`, the `label="sharpe"` branch)
- Test: `tests/test_fx_ml.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `pooled_dataset(..., label="sharpe")` returns `y` as
  `forward_return / trailing_vol`, and additionally returns a `vols` array.
  New signature: `(X, y, times, pairs, cols, vols)` — a 6-tuple, was a 5-tuple.
  Every caller must be updated in this task.

- [ ] **Step 1: Write the failing test**

```python
def test_pooled_target_is_vol_normalised(panel):
    """Crypto moves ~4x harder than FX. With a raw forward-return target it is
    24% of the rows but 96.3% of the squared target the loss sees, so the model
    is trained almost entirely on the instruments the technical agents were
    measured to be WORST on. Normalising by trailing vol makes each instrument
    contribute in proportion to its row count."""
    from trading_algo.forex.pairs import get_pair
    p = profile("balanced")
    X, y, times, pairs, cols, vols = pooled_dataset(panel, p, label="sharpe", horizon=1)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_ml.py::test_pooled_target_is_vol_normalised -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 6, got 5)`

- [ ] **Step 3: Write minimal implementation**

In `ml_agent.pooled_dataset`, replace the `label == "sharpe"` target and thread
a `vols` array through. The existing loop already computes per-symbol frames:

```python
    Xs, ys, ts, ps, vs = [], [], [], [], []
    ...
    for sym, bars in panel.items():
        ag = sig_panel[sym] if (sig_panel is not None) else None
        feats = features.build_features(bars, agent_signals=ag, pair=get_pair(sym))
        vol = ind.realized_vol(bars["close"], 20).replace(0.0, np.nan)
        if label == "sharpe":
            raw = bars["close"].pct_change(horizon, fill_method=None).shift(-horizon)
            # Vol-normalised: the downstream risk layer scales positions ~1/vol,
            # so this is what the live book actually earns, and it stops the
            # highest-vol instruments dominating the loss.
            y = (raw / vol).replace([np.inf, -np.inf], np.nan)
        else:  # meta — unchanged, triple-barrier labels are already scale-free
            ...
        X, y = features.align_xy(feats, y)
        if len(X) == 0:
            continue
        ...
        vs.append(vol.reindex(X.index).to_numpy())

    return (np.vstack(Xs), np.concatenate(ys), np.concatenate(ts),
            np.concatenate(ps), cols, np.concatenate(vs))
```

Update all **four** existing call sites to unpack six values (verified
2026-09-16 — a missed one fails loudly with a tuple-unpack error, which is the
good case, but find them up front):
- `ml_backtest.py:94` (`neural_oos_signal`) → `X, y, t, pairs, cols, vols = ...`
- `ml_backtest.py:114` (`meta_oos_signal`) → `X, y, t, pairs, cols, _ = ...`
- `train.py:62` (`train_models`, sharpe) → `Xn, yn, _, _, cols_n, _ = ...`
- `train.py:71` (`train_models`, meta) → `Xm, ym, _, _, cols_m, _ = ...`

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_fx_ml.py -v`
Expected: PASS, and no other test in the file breaks on the tuple change.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/ml_agent.py trading_algo/forex/ml_backtest.py \
        trading_algo/forex/train.py tests/test_fx_ml.py
git commit -m "feat(ml): vol-normalise the neural target

Crypto was 24% of training rows but 96.3% of the squared target the loss
saw, because the target was the raw forward return and crypto moves ~4x
harder than FX. All seven FX pairs contributed 3.7% between them.

Normalising by trailing vol also matches the live book: risk.py scales
positions ~1/vol downstream, so w*r/vol is what the book actually earns."
```

---

### Task 2: Panel index

**Files:**
- Create: `trading_algo/forex/panel_index.py`
- Test: `tests/test_fx_nn.py`

**Interfaces:**
- Consumes: the `times`, `pairs`, `vols` arrays from Task 1.
- Produces:
  ```python
  @dataclass(frozen=True)
  class PanelIndex:
      group: np.ndarray       # (n,) int64  — timestamp group id, 0..T-1
      n_groups: int           # T
      group_size: np.ndarray  # (T,) float64 — m_t per group
      prev: np.ndarray        # (n,) int64  — previous row for same pair, -1 if first
      nxt: np.ndarray         # (n,) int64  — next row for same pair, -1 if last
      cost: np.ndarray        # (n,) float64 — k_i = half_spread / vol_i

  def build_panel_index(times, pairs, vols, half_spreads: dict[str, float]) -> PanelIndex
  ```

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_nn.py::test_panel_index_links_rows_within_each_pair -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading_algo.forex.panel_index'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Row bookkeeping for the cost-aware portfolio objective.

`nn.MLP` sees a flat matrix of rows. The net-portfolio-Sharpe objective needs
two structures that matrix does not carry: which rows share a TIMESTAMP (they
are averaged into one portfolio return) and which row precedes each row within
its OWN pair's timeline (turnover is charged against that predecessor).

Pure bookkeeping — no model, no training, no pandas in the hot path — so it can
be tested exhaustively on hand-built arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PanelIndex:
    group: np.ndarray        # (n,) timestamp group id
    n_groups: int
    group_size: np.ndarray   # (T,) rows per timestamp
    prev: np.ndarray         # (n,) previous row for the same pair, -1 if none
    nxt: np.ndarray          # (n,) next row for the same pair, -1 if none
    cost: np.ndarray         # (n,) half-spread in vol-normalised units


def build_panel_index(times, pairs, vols, half_spreads) -> PanelIndex:
    """Index a flat (row -> pair, timestamp) panel for the portfolio objective.

    `half_spreads` maps a pair symbol to its half-spread as a fraction of price.
    The cost coefficient is divided by the same trailing vol the target was
    normalised by, so cost and return are in identical units — otherwise the
    objective compares a normalised return against a raw cost and mis-prices
    every trade.
    """
    times = np.asarray(times)
    pairs = np.asarray(pairs)
    vols = np.asarray(vols, dtype=float)

    _, group = np.unique(times, return_inverse=True)
    group = group.astype(np.int64)
    n_groups = int(group.max()) + 1 if len(group) else 0
    group_size = np.bincount(group, minlength=n_groups).astype(float)

    n = len(pairs)
    prev = np.full(n, -1, dtype=np.int64)
    nxt = np.full(n, -1, dtype=np.int64)
    for sym in np.unique(pairs):
        rows = np.flatnonzero(pairs == sym)
        rows = rows[np.argsort(times[rows], kind="stable")]
        prev[rows[1:]] = rows[:-1]
        nxt[rows[:-1]] = rows[1:]

    safe_vol = np.where(vols > 0, vols, np.nan)
    spread = np.array([float(half_spreads.get(s, 0.0)) for s in pairs])
    cost = np.nan_to_num(spread / safe_vol, nan=0.0, posinf=0.0)

    return PanelIndex(group=group, n_groups=n_groups, group_size=group_size,
                      prev=prev, nxt=nxt, cost=cost)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_fx_nn.py::test_panel_index_links_rows_within_each_pair -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/panel_index.py tests/test_fx_nn.py
git commit -m "feat(ml): panel index for the cost-aware objective

Links each row to its predecessor within its own pair's timeline (for
turnover) and groups rows by timestamp (for the portfolio return). Cost is
expressed in the same vol-normalised units as the target."
```

---

### Task 3: The net-portfolio-Sharpe objective and its gradient

This is the highest-risk task in the plan. The gradient is hand-derived and a
wrong-but-plausible version will still train — just toward the wrong thing. The
numerical check in Step 1 is the only thing standing between that and a silently
broken model, so it is written **before** the implementation.

**Files:**
- Modify: `trading_algo/forex/nn.py`
- Test: `tests/test_fx_nn.py`

**Interfaces:**
- Consumes: `PanelIndex` from Task 2.
- Produces:
  - `nn.sharpe_net_loss(w, r, idx, ann) -> float`
  - `nn.sharpe_net_grad(w, r, idx, ann) -> np.ndarray` — `dL/dw`, shape `(n, 1)`
  - `MLP(task="sharpe_net")`, reading `self.panel_index` and `self.ann`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_fx_nn.py -k "sharpe_net" -v`
Expected: FAIL — `AttributeError: module 'trading_algo.forex.nn' has no attribute 'sharpe_net_grad'`

- [ ] **Step 3: Write minimal implementation**

Add to `nn.py`, above `class MLP`:

```python
def _net_portfolio_returns(w, r, idx):
    """Per-timestamp portfolio return, net of turnover. Shared by loss and grad
    so the two can never disagree about the forward pass."""
    w = w.reshape(-1)
    r = r.reshape(-1)
    prev_w = np.where(idx.prev >= 0, w[idx.prev], 0.0)   # opening from flat costs
    row = w * r - idx.cost * np.abs(w - prev_w)
    total = np.bincount(idx.group, weights=row, minlength=idx.n_groups)
    return total / idx.group_size


def sharpe_net_loss(w, r, idx, ann):
    """Negative annualised Sharpe of the NET portfolio return series.

    Aggregates positions per timestamp into one portfolio return, charges the
    half-spread on every position change, then takes mean/std OVER TIME. The
    previous objective took the Sharpe of a pooled (pair, timestamp) scatter,
    which is a different quantity and not the one a portfolio earns.
    """
    N = _net_portfolio_returns(w, r, idx)
    sigma = np.sqrt(N.var() + _EPS)
    return float(-ann * N.mean() / sigma)


def sharpe_net_grad(w, r, idx, ann):
    """dL/dw for `sharpe_net_loss`. See the derivation in the plan.

    Each w_i contributes through its own row AND through the next row's
    turnover term, which lands at a different timestamp. Both paths are here.
    """
    shape = w.shape
    w = w.reshape(-1)
    r = r.reshape(-1)
    N = _net_portfolio_returns(w.reshape(-1, 1), r.reshape(-1, 1), idx)
    T = idx.n_groups
    mu, var = N.mean(), N.var()
    sigma = np.sqrt(var + _EPS)
    dN = -ann / (T * sigma) * (1.0 - mu * (N - mu) / (var + _EPS))   # (T,)

    prev_w = np.where(idx.prev >= 0, w[idx.prev], 0.0)
    own = r - idx.cost * np.sign(w - prev_w)
    g = dN[idx.group] / idx.group_size[idx.group] * own

    # second path: w_i appears in the NEXT row's |w_next - w_i| term
    has_next = idx.nxt >= 0
    j = idx.nxt[has_next]
    g[has_next] += (dN[idx.group[j]] / idx.group_size[idx.group[j]]
                    * idx.cost[j] * np.sign(w[j] - w[has_next]))
    return g.reshape(shape)
```

In `MLP`, add the fields and branches. Add to the dataclass body:

```python
    panel_index: object | None = field(default=None, repr=False)
    ann: float = np.sqrt(252.0)
```

In `_activate_output`, treat `sharpe_net` like `sharpe` (tanh position):

```python
        if self.task in ("sharpe", "sharpe_net"):
            return _tanh(z)
```

In `_loss`:

```python
        elif self.task == "sharpe_net":
            data = sharpe_net_loss(out, y, self.panel_index, self.ann)
```

In `_out_grad`, before the existing `sharpe` branch:

```python
        if self.task == "sharpe_net":
            dpos = sharpe_net_grad(out, y, self.panel_index, self.ann)
            return dpos * (1.0 - out * out)    # chain through tanh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_fx_nn.py -v`
Expected: PASS, including the existing `nn` tests — the `sharpe` task is untouched.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/nn.py tests/test_fx_nn.py
git commit -m "feat(nn): net-portfolio-Sharpe objective with turnover in the loss

Measured: the neural agent's gross OOS Sharpe was +0.60 and its net Sharpe
-0.44. The entire edge was consumed by turnover, and post-hoc smoothing
recovered the cost but destroyed the edge with it (net ~0).

Two defects in the old objective, both fixed here:
  * it took the Sharpe of a pooled (pair, timestamp) scatter, not of a
    portfolio return series aggregated per timestamp then measured over time
  * it could not see turnover at all, being row-independent

The analytic gradient is verified against central finite differences to 1e-5
relative. Each w_i enters twice -- its own row, and the next row's turnover
term at a different timestamp -- and omitting the second path yields a
gradient that trains happily toward the wrong thing."
```

---

### Task 4: Batch the whole panel, and wire the objective in

The objective is defined over the *entire* panel: a mini-batch of shuffled rows
has no coherent timestamps or pair sequences. `neural_oos_signal` already trains
full-batch (`batch_size=100000` against ~30k rows), so this is already true in
practice — but it must be made explicit, or a future batch-size change silently
corrupts the objective.

**Files:**
- Modify: `trading_algo/forex/nn.py` (`fit`), `trading_algo/forex/ml_backtest.py`
- Test: `tests/test_fx_nn.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `ml_backtest._sharpe_factory(n_feat, seed, panel_index=None, ann=...)`.
  When `panel_index` is given the factory builds a `task="sharpe_net"` model.

- [ ] **Step 1: Write the failing test**

```python
def test_sharpe_net_refuses_to_train_in_mini_batches():
    """The objective is defined over the whole panel. A shuffled mini-batch has
    no coherent timestamps or pair sequences, so training on one would compute a
    meaningless loss without erroring -- the worst kind of failure."""
    from trading_algo.forex import nn
    w, r, idx = _toy_panel()
    X = np.random.default_rng(0).normal(0, 1, (len(r), 4))
    m = nn.MLP([4, 3, 1], task="sharpe_net", seed=0)
    m.panel_index = idx

    with pytest.raises(ValueError, match="full-batch"):
        m.fit(X, r, epochs=1, batch_size=4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_nn.py::test_sharpe_net_refuses_to_train_in_mini_batches -v`
Expected: FAIL — `DID NOT RAISE <class 'ValueError'>`

- [ ] **Step 3: Write minimal implementation**

At the top of `MLP.fit`, after `n = X.shape[0]`:

```python
        if self.task == "sharpe_net":
            if self.panel_index is None:
                raise ValueError("task='sharpe_net' requires .panel_index")
            if batch_size < n:
                raise ValueError(
                    "task='sharpe_net' must train full-batch: the objective is "
                    "defined over the whole panel, and a shuffled mini-batch has "
                    f"no coherent timestamps (batch_size={batch_size}, rows={n})")
```

In `ml_backtest.py`:

```python
def _sharpe_factory(n_feat: int, seed: int = 0, panel_index=None,
                    ann: float = float(np.sqrt(252.0))):
    """Cost-aware portfolio objective when a panel index is supplied, else the
    legacy pooled objective (kept so the two can be compared directly)."""
    if panel_index is None:
        return lambda: MLP([n_feat, 32, 1], hidden_act="tanh", task="sharpe",
                           l2=1e-3, dropout=0.1, seed=seed)

    def make():
        m = MLP([n_feat, 32, 1], hidden_act="tanh", task="sharpe_net",
                l2=1e-3, dropout=0.1, seed=seed)
        m.panel_index = panel_index
        m.ann = ann
        return m
    return make
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_fx_nn.py tests/test_fx_ml.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/nn.py trading_algo/forex/ml_backtest.py tests/test_fx_nn.py
git commit -m "feat(ml): full-batch guard and sharpe_net factory

The cost-aware objective is defined over the whole panel. A shuffled
mini-batch has no coherent timestamps or pair sequences, so it would compute
a meaningless loss WITHOUT erroring. fit() now refuses rather than silently
training on nonsense."
```

---

### Task 5: Walk-forward per fold, with a validation split

`walk_forward_predict` slices rows per fold, so each fold needs its **own**
`PanelIndex` built from that fold's rows. Reusing the whole-panel index would
index out of range or silently mis-group. This task also turns on the early
stopping that `fit()` already implements and nothing has ever used.

**Files:**
- Modify: `trading_algo/forex/walkforward.py`, `trading_algo/forex/ml_backtest.py`
- Test: `tests/test_fx_walkforward.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `walk_forward_predict(..., index_factory=None)` where
  `index_factory(row_positions: np.ndarray) -> PanelIndex` is called once per
  fold with that fold's **training** row positions; the returned index is
  assigned to the model before `fit`.

- [ ] **Step 1: Write the failing test**

```python
def test_each_fold_gets_its_own_panel_index():
    """A fold trains on a SUBSET of rows. Handing it the whole-panel index would
    mis-group timestamps and index past the end of the fold's arrays."""
    from trading_algo.forex import walkforward
    seen = []

    def index_factory(rows):
        seen.append(len(rows))
        class _Stub:
            n_groups = 1
            group = np.zeros(len(rows), dtype=np.int64)
            group_size = np.array([float(len(rows))])
            prev = np.full(len(rows), -1, dtype=np.int64)
            nxt = np.full(len(rows), -1, dtype=np.int64)
            cost = np.zeros(len(rows))
        return _Stub()

    n = 600
    X = np.random.default_rng(0).normal(0, 1, (n, 3))
    y = np.random.default_rng(1).normal(0, 1, n)
    t = np.arange(n)

    class _M:
        panel_index = None
        def fit(self, X, y, **kw): return self
        def predict(self, X): return np.zeros((len(X), 1))

    walkforward.walk_forward_predict(X, y, t, lambda: _M(), n_folds=3,
                                     min_train=100, index_factory=index_factory)

    assert len(seen) >= 2, "index_factory must be called once per scored fold"
    assert all(s > 0 for s in seen)
    assert len(set(seen)) > 1, "an expanding walk-forward grows the train set"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_walkforward.py::test_each_fold_gets_its_own_panel_index -v`
Expected: FAIL — `TypeError: walk_forward_predict() got an unexpected keyword argument 'index_factory'`

- [ ] **Step 3: Write minimal implementation**

In `walkforward.walk_forward_predict`, add the parameter and use it where the
model is built. Inside the fold loop, after `train_mask` is computed and before
`model.fit(...)`:

```python
        train_rows = np.flatnonzero(train_mask)
        model = model_factory()
        if index_factory is not None:
            model.panel_index = index_factory(train_rows)
```

In `ml_backtest.neural_oos_signal`, build the per-fold index and pass a
validation split so early stopping fires:

```python
def neural_oos_signal(panel, p, *, n_folds=6, embargo=5, min_train=400,
                      epochs=400) -> pd.DataFrame:
    X, y, t, pairs, cols, vols = ml_agent.pooled_dataset(
        panel, p, label="sharpe", horizon=1)
    if len(X) == 0:
        return pd.DataFrame()
    spreads = {s: marks.half_spread_fraction(get_pair(s), closes(panel)[s]).mean()
               for s in panel}

    def index_factory(rows):
        return build_panel_index(t[rows], pairs[rows], vols[rows], spreads)

    preds = walk_forward_predict(
        X, y, t, _sharpe_factory(len(cols), panel_index=_PENDING),
        n_folds=n_folds, label_horizon=1, embargo=embargo, min_train=min_train,
        index_factory=index_factory,
        fit_kwargs={"epochs": epochs, "batch_size": 10 ** 9, "lr": 1e-2,
                    "patience": 25})
    px = closes(panel)
    return _scatter(preds, t, pairs, px.index, px.columns)
```

`_PENDING` is a module-level sentinel object that makes `_sharpe_factory` build a
`sharpe_net` model whose `panel_index` the walk-forward then overwrites per fold:

```python
_PENDING = object()   # "sharpe_net, index supplied per fold by index_factory"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_fx_walkforward.py tests/test_fx_ml.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/walkforward.py trading_algo/forex/ml_backtest.py \
        tests/test_fx_walkforward.py
git commit -m "feat(ml): per-fold panel index and real early stopping

Each walk-forward fold trains on a subset of rows and needs its own index;
the whole-panel one would mis-group timestamps and index out of range.

Also passes patience so the early-stopping and best-weight-restore logic in
fit() finally runs. It has existed since the model was written and has never
been used -- neural_oos_signal never supplied a validation split, so training
was blind. Measured: train Sharpe 3.5 against validation 0.67."
```

---

### Task 6: Seed-ensembled evaluation

Every OOS number ever reported for this model — including the −0.62 that let it
trade — came from `seed=0`. A single draw.

**Files:**
- Modify: `trading_algo/forex/ml_backtest.py`
- Test: `tests/test_fx_ml.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: `neural_oos_signal(..., seeds: int = 3)` — averages the OOS signal
  across `seeds` independently-initialised walk-forward runs.

- [ ] **Step 1: Write the failing test**

```python
def test_neural_oos_signal_is_seed_ensembled(panel):
    """A single seed is a single draw. Production already seed-ensembles the
    DEPLOYED bundle but graded on one seed, so the number that gated live
    trading carried unmeasured variance."""
    p = profile("balanced")
    one = ml_backtest.neural_oos_signal(panel, p, n_folds=3, seeds=1)
    three = ml_backtest.neural_oos_signal(panel, p, n_folds=3, seeds=3)
    assert not one.empty and not three.empty
    assert one.shape == three.shape
    assert not np.allclose(one.fillna(0).to_numpy(), three.fillna(0).to_numpy()), \
        "averaging three seeds must not reproduce a single seed exactly"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_ml.py::test_neural_oos_signal_is_seed_ensembled -v`
Expected: FAIL — `TypeError: neural_oos_signal() got an unexpected keyword argument 'seeds'`

- [ ] **Step 3: Write minimal implementation**

Wrap the existing body in a seed loop and average the scattered panels:

```python
def neural_oos_signal(panel, p, *, n_folds=6, embargo=5, min_train=400,
                      epochs=400, seeds=3) -> pd.DataFrame:
    frames = []
    for s in range(seeds):
        frames.append(_neural_oos_once(panel, p, n_folds=n_folds, embargo=embargo,
                                       min_train=min_train, epochs=epochs, seed=s))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return sum(frames) / len(frames)
```

Rename the existing implementation to `_neural_oos_once(..., seed=0)` and pass
`seed` through to `_sharpe_factory`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_fx_ml.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/ml_backtest.py tests/test_fx_ml.py
git commit -m "feat(ml): seed-ensemble the out-of-sample evaluation

_sharpe_factory used seed=0, so every OOS number ever reported for this
model -- including the -0.62 Sharpe that let it trade the live books -- was
a single draw with unmeasured variance. Production already seed-ensembles
the DEPLOYED bundle; now the grade does too."
```

---

### Task 7: Data expansion

**Files:**
- Modify: `trading_algo/forex/pairs.py`, `trading_algo/forex/fx_config.py`
- Test: `tests/test_fx_pairs.py`

**Interfaces:**
- Consumes: nothing; independent of Tasks 1–6 and safely done in either order.
- Produces: `DEFAULT_UNIVERSE` of 16 symbols (7 majors + 3 crypto + 6 crosses);
  `fx_config.START = "2003-12-01"`.

- [ ] **Step 1: Write the failing test**

```python
def test_default_universe_includes_the_registered_crosses():
    """Six FX crosses are registered, priced, and were never used for training.
    The model overfits (train Sharpe 3.5 vs validation 0.67) and more data is
    the textbook remedy -- but they are combinations of the majors, so their
    marginal information is well below their row count. That caveat belongs in
    the evaluation, not in a decision to exclude them."""
    from trading_algo.forex.pairs import CROSSES, DEFAULT_UNIVERSE
    for sym in CROSSES:
        assert sym in DEFAULT_UNIVERSE, f"{sym} is registered but never trained on"
    assert len(DEFAULT_UNIVERSE) == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_fx_pairs.py::test_default_universe_includes_the_registered_crosses -v`
Expected: FAIL — `AssertionError: EURGBP is registered but never trained on`

- [ ] **Step 3: Write minimal implementation**

In `pairs.py`:

```python
DEFAULT_UNIVERSE: list[str] = [*PAIRS, *CRYPTO, *CROSSES]
```

In `fx_config.py`:

```python
# Yahoo carries the FX majors from 2003-12-01 (EURGBP from 1999); verified
# 2026-09-16. The model overfits at 24k rows, and history is the cheapest data
# there is.
START = "2003-12-01"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_fx_pairs.py tests/test_fx_book.py -v`
Expected: PASS. `fx_book.run_once` merges `DEFAULT_UNIVERSE` into existing books,
so live books pick the crosses up without losing history.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/forex/pairs.py trading_algo/forex/fx_config.py tests/test_fx_pairs.py
git commit -m "feat(fx): train on the six registered crosses and full history

10 of 25 registered instruments were used, starting 2015 against verified
history back to 2003-12-01. Roughly 4x the training data, against a model
with a measured train/validation gap of 3.5 vs 0.67.

Honest caveat for the evaluation: crosses are combinations of the majors, so
marginal information is well below marginal row count."
```

---

### Task 8: Measure it, and record the result either way

The point of the whole plan. This task produces a number, not a feature.

**Files:**
- Create: `docs/research/COST_AWARE_OBJECTIVE_RESULT.md`
- Test: none — this is a measurement, run against real data.

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: a committed before/after table and a verdict.

- [ ] **Step 1: Run the comparison on real data**

```bash
python3 -m trading_algo.forex.train --seeds 3 --folds 6 --out /tmp/after.md
```

Expected output includes the promotion-floor verdict added in PR #94.

- [ ] **Step 2: Record the result**

Write `docs/research/COST_AWARE_OBJECTIVE_RESULT.md` with this table filled in
from the run — and **compute the null first**: score a random signal through the
identical pipeline and confirm it lands at ~0 Sharpe. A result is only readable
against its null.

```markdown
| configuration                          | gross Sharpe | net Sharpe | turnover |
|----------------------------------------|--------------|------------|----------|
| raw target, pooled objective (before)  |        +0.45 |      -0.65 |     7934 |
| vol-normalised, pooled objective       |        +0.60 |      -0.44 |     7934 |
| vol-normalised, cost-aware objective   |          ... |        ... |      ... |
| random signal (the null)               |          ... |        ... |      ... |
```

- [ ] **Step 3: State the verdict plainly**

If net Sharpe clears 0 and the floor passes, say so and let the promotion gate
decide. If it does not, record that as a result — a credible null on a
correctly-specified objective is worth more than another round of tuning, and it
is the honest answer this repo is built to produce.

Do **not** tune parameters to reach a positive number. That is the failure mode
`validation.py` exists to prevent, and the trial count would have to deflate the
Deflated Sharpe anyway.

- [ ] **Step 4: Commit**

```bash
git add docs/research/COST_AWARE_OBJECTIVE_RESULT.md
git commit -m "docs: result of the cost-aware neural objective"
```

---

## Self-Review

**Spec coverage.** Phase 2 of the spec lists: vol-normalised target (Task 1),
portfolio-Sharpe objective (Tasks 2–4), validation split and early stopping
(Task 5), seed-ensembled evaluation (Task 6), reduced capacity, and the data
expansion (Task 7), reporting the train/validation gap before and after (Task 8).

**One spec item is deliberately not a task: "less capacity, not more."** The
capacity choice must be made *on the validation curve after* the objective is
fixed — the current 3.5/0.67 gap was measured under a misspecified objective and
a blind training loop, so narrowing the network now would be tuning against a
number that is about to change. Task 8 produces the curve that should drive it,
and the follow-up is a separate, evidence-led change.

**Placeholders.** None. Every code step carries real code; Task 8's table is
deliberately unfilled because it is the output of running it.

**Type consistency.** `pooled_dataset` returns a 6-tuple from Task 1 onward, and
all three callers are updated in that task. `PanelIndex` field names
(`group`, `n_groups`, `group_size`, `prev`, `nxt`, `cost`) are used identically
in Tasks 2, 3 and 5. `_sharpe_factory(n_feat, seed, panel_index, ann)` keeps its
positional prefix so the legacy call in `meta_oos_signal` is unaffected.

**Risk note for the executor.** Task 3 is the one that can fail silently. If the
finite-difference check does not pass to 1e-5, do not adjust the tolerance — the
gradient is wrong, and the most likely cause is the second path (`w_i` appearing
in the next row's turnover term at a different timestamp).
