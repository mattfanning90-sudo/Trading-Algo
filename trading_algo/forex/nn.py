"""A small, correct deep-learning library in pure NumPy.

Why pure NumPy (no torch/tensorflow): this project ships only numpy/pandas, must
run in CI and fully offline, and the models here are small (a few thousand
parameters on ~20-40 engineered features). A from-scratch MLP with a real Adam
optimiser, dropout and L2 is genuinely "deep learning" (multi-layer, gradient
trained via back-propagation) while keeping the zero-heavy-dependency invariant.

What's here:
* `MLP` — a configurable multilayer perceptron supporting regression, binary and
  multiclass classification, with He/Glorot init, ReLU/tanh/sigmoid activations,
  inverted dropout, L2 weight decay, mini-batch Adam, and early stopping.
* `StandardScaler` — leakage-safe feature standardisation (fit on train only).

Correctness is pinned by `tests/test_fx_nn.py`, which includes a finite-
difference gradient check and an "overfit a tiny dataset" test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

_EPS = 1e-12


# ---------------------------------------------------------------------------
# Activations
# ---------------------------------------------------------------------------
def _relu(z):
    return np.maximum(0.0, z)


def _relu_grad(z):
    return (z > 0.0).astype(z.dtype)


def _tanh(z):
    return np.tanh(z)


def _tanh_grad_from_a(a):
    return 1.0 - a * a


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / (e.sum(axis=1, keepdims=True) + _EPS)


_ACT = {"relu": _relu, "tanh": _tanh, "sigmoid": _sigmoid, "linear": lambda z: z}


# ---------------------------------------------------------------------------
# Feature scaling (fit on training data only — no leakage)
# ---------------------------------------------------------------------------
@dataclass
class StandardScaler:
    mean_: np.ndarray | None = None
    std_: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> "StandardScaler":
        self.mean_ = X.mean(axis=0)
        self.std_ = X.std(axis=0)
        self.std_[self.std_ < _EPS] = 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean_) / self.std_

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)

    def to_dict(self) -> dict:
        if self.mean_ is None or self.std_ is None:
            raise ValueError("StandardScaler must be fit before serialising")
        return {"mean": self.mean_.tolist(), "std": self.std_.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "StandardScaler":
        return cls(mean_=np.asarray(d["mean"]), std_=np.asarray(d["std"]))


# ---------------------------------------------------------------------------
# Multilayer perceptron
# ---------------------------------------------------------------------------
@dataclass
class MLP:
    """Feedforward net trained by mini-batch Adam back-propagation.

    `layer_sizes`  : [n_in, h1, h2, ..., n_out]
    `hidden_act`   : "relu" | "tanh" | "sigmoid" (applied to every hidden layer)
    `task`         : "regression" (linear out, MSE)
                     "binary" (sigmoid out of width 1, BCE)
                     "multiclass" (softmax out, cross-entropy)
                     "sharpe" (tanh position out of width 1; loss = −Sharpe of
                       position·forward_return). This trains the network to
                       output a *position* that maximises risk-adjusted return
                       directly, rather than a return forecast you then threshold
                       — the design shown to beat MSE/classification objectives
                       in Lim–Zohren–Roberts (Deep Momentum Networks, 2019) and
                       Moody–Saffell (1998). For this task `y` is the forward
                       return, and `predict` returns a signal in [-1, 1].
    """
    layer_sizes: list[int]
    hidden_act: str = "relu"
    task: str = "binary"
    l2: float = 1e-4
    dropout: float = 0.0
    seed: int = 0
    W: list[np.ndarray] = field(default_factory=list)
    b: list[np.ndarray] = field(default_factory=list)
    _mW: list[np.ndarray] = field(default_factory=list, repr=False)
    _vW: list[np.ndarray] = field(default_factory=list, repr=False)
    _mb: list[np.ndarray] = field(default_factory=list, repr=False)
    _vb: list[np.ndarray] = field(default_factory=list, repr=False)
    _t: int = field(default=0, repr=False)

    def __post_init__(self):
        if not self.W:
            self._init_params()

    # -- initialisation ----------------------------------------------------
    def _init_params(self):
        rng = np.random.default_rng(self.seed)
        self.W, self.b = [], []
        n_layers = len(self.layer_sizes) - 1
        for i in range(n_layers):
            fan_in, fan_out = self.layer_sizes[i], self.layer_sizes[i + 1]
            is_hidden = i < n_layers - 1
            # He for ReLU hidden layers, Glorot otherwise.
            if is_hidden and self.hidden_act == "relu":
                scale = np.sqrt(2.0 / fan_in)
            else:
                scale = np.sqrt(1.0 / fan_in)
            self.W.append(rng.normal(0.0, scale, (fan_in, fan_out)))
            self.b.append(np.zeros(fan_out))
        self._reset_adam()

    def _reset_adam(self):
        self._mW = [np.zeros_like(w) for w in self.W]
        self._vW = [np.zeros_like(w) for w in self.W]
        self._mb = [np.zeros_like(b) for b in self.b]
        self._vb = [np.zeros_like(b) for b in self.b]
        self._t = 0

    # -- forward -----------------------------------------------------------
    def _out_act(self, z):
        if self.task == "regression":
            return z
        if self.task == "binary":
            return _sigmoid(z)
        if self.task == "sharpe":
            return _tanh(z)            # output is a position in [-1, 1]
        return _softmax(z)

    def _out_grad(self, out, y):
        """Pre-activation gradient at the output layer for the data loss.

        For regression+MSE, sigmoid+BCE and softmax+CE this collapses to the
        clean (out − y)/n. For the Sharpe objective the gradient flows through
        the Sharpe ratio of position·forward_return and the output tanh.
        """
        n = out.shape[0]
        if self.task == "sharpe":
            r = y                              # forward returns (n, 1)
            pnl = out * r
            mu = pnl.mean()
            var = pnl.var()                    # population variance
            sigma = np.sqrt(var + _EPS)
            k = np.sqrt(252.0)
            dpnl = -k * (1.0 / (n * sigma)) * (1.0 - mu * (pnl - mu) / (var + _EPS))
            dpos = dpnl * r
            return dpos * (1.0 - out * out)    # chain through tanh
        return (out - y) / n

    def _forward(self, X, train=False, rng=None):
        """Return (output, cache). Cache holds per-layer (a_prev, z, drop_mask)."""
        a = X
        cache = []
        n = len(self.W)
        for i in range(n):
            z = a @ self.W[i] + self.b[i]
            if i < n - 1:                                   # hidden layer
                a_new = _ACT[self.hidden_act](z)
                mask = None
                if train and self.dropout > 0.0:
                    keep = 1.0 - self.dropout
                    mask = (rng.random(a_new.shape) < keep) / keep
                    a_new = a_new * mask
                cache.append((a, z, mask))
                a = a_new
            else:                                            # output layer
                out = self._out_act(z)
                cache.append((a, z, None))
                a = out
        return a, cache

    # -- loss --------------------------------------------------------------
    def _loss(self, out, y):
        n = out.shape[0]
        if self.task == "regression":
            data = 0.5 * np.mean((out - y) ** 2)
        elif self.task == "binary":
            p = np.clip(out, _EPS, 1 - _EPS)
            data = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        elif self.task == "sharpe":
            pnl = out * y
            sigma = np.sqrt(pnl.var() + _EPS)
            data = -(pnl.mean() / sigma) * np.sqrt(252.0)   # negative Sharpe
        else:
            p = np.clip(out, _EPS, 1 - _EPS)
            data = -np.mean(np.sum(y * np.log(p), axis=1))
        reg = 0.5 * self.l2 * sum(np.sum(w * w) for w in self.W) / n
        return data + reg

    # -- backward ----------------------------------------------------------
    def _backward(self, out, y, cache):
        n = out.shape[0]
        grads_W = [None] * len(self.W)
        grads_b = [None] * len(self.b)
        dz = self._out_grad(out, y)          # task-specific output-layer gradient
        for i in reversed(range(len(self.W))):
            a_prev, z, mask = cache[i]
            grads_W[i] = a_prev.T @ dz + self.l2 * self.W[i] / n
            grads_b[i] = dz.sum(axis=0)
            if i > 0:
                da = dz @ self.W[i].T
                _, z_prev, mask_prev = cache[i - 1]
                if mask_prev is not None:
                    da = da * mask_prev
                if self.hidden_act == "relu":
                    dz = da * _relu_grad(z_prev)
                elif self.hidden_act == "tanh":
                    dz = da * _tanh_grad_from_a(_tanh(z_prev))
                else:  # sigmoid
                    s = _sigmoid(z_prev)
                    dz = da * s * (1 - s)
        return grads_W, grads_b

    # -- Adam step ---------------------------------------------------------
    def _adam_step(self, gW, gb, lr, b1=0.9, b2=0.999):
        self._t += 1
        bc1 = 1 - b1 ** self._t
        bc2 = 1 - b2 ** self._t
        for i in range(len(self.W)):
            self._mW[i] = b1 * self._mW[i] + (1 - b1) * gW[i]
            self._vW[i] = b2 * self._vW[i] + (1 - b2) * (gW[i] ** 2)
            self.W[i] -= lr * (self._mW[i] / bc1) / (np.sqrt(self._vW[i] / bc2) + 1e-8)
            self._mb[i] = b1 * self._mb[i] + (1 - b1) * gb[i]
            self._vb[i] = b2 * self._vb[i] + (1 - b2) * (gb[i] ** 2)
            self.b[i] -= lr * (self._mb[i] / bc1) / (np.sqrt(self._vb[i] / bc2) + 1e-8)

    # -- public API --------------------------------------------------------
    def _prep_y(self, y):
        y = np.asarray(y, dtype=float)
        if self.task != "multiclass" and y.ndim == 1:
            y = y.reshape(-1, 1)
        return y

    def fit(self, X, y, *, epochs=200, batch_size=64, lr=1e-3,
            X_val=None, y_val=None, patience=20, verbose=False) -> "MLP":
        X = np.asarray(X, dtype=float)
        y = self._prep_y(y)
        rng = np.random.default_rng(self.seed)
        n = X.shape[0]
        best_loss, best_state, wait = np.inf, None, 0
        has_val = X_val is not None and y_val is not None
        if has_val:
            X_val = np.asarray(X_val, dtype=float)
            y_val = self._prep_y(y_val)

        for epoch in range(epochs):
            order = rng.permutation(n)
            for start in range(0, n, batch_size):
                idx = order[start:start + batch_size]
                out, cache = self._forward(X[idx], train=True, rng=rng)
                gW, gb = self._backward(out, y[idx], cache)
                self._adam_step(gW, gb, lr)

            if has_val:
                vloss = self._loss(self._forward(X_val)[0], y_val)
                if vloss < best_loss - 1e-6:
                    best_loss, wait = vloss, 0
                    best_state = ([w.copy() for w in self.W], [b.copy() for b in self.b])
                else:
                    wait += 1
                    if wait >= patience:
                        if verbose:
                            print(f"  early stop @ epoch {epoch} (val {best_loss:.5f})")
                        break
            if verbose and epoch % 25 == 0:
                tl = self._loss(self._forward(X)[0], y)
                print(f"  epoch {epoch:4d}  train {tl:.5f}"
                      + (f"  val {vloss:.5f}" if has_val else ""))

        if best_state is not None:
            self.W, self.b = best_state
        return self

    def predict(self, X) -> np.ndarray:
        """Raw output: returns (regression) or probabilities (classification)."""
        out, _ = self._forward(np.asarray(X, dtype=float), train=False)
        return out

    def predict_proba(self, X) -> np.ndarray:
        if self.task in ("regression", "sharpe"):
            raise ValueError("predict_proba is for classification tasks")
        return self.predict(X)

    # -- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "layer_sizes": self.layer_sizes, "hidden_act": self.hidden_act,
            "task": self.task, "l2": self.l2, "dropout": self.dropout,
            "seed": self.seed,
            "W": [w.tolist() for w in self.W], "b": [b.tolist() for b in self.b],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MLP":
        m = cls(layer_sizes=d["layer_sizes"], hidden_act=d["hidden_act"],
                task=d["task"], l2=d["l2"], dropout=d["dropout"], seed=d["seed"],
                W=[np.asarray(w) for w in d["W"]], b=[np.asarray(b) for b in d["b"]])
        m._reset_adam()
        return m

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path: str) -> "MLP":
        with open(path) as f:
            return cls.from_dict(json.load(f))
