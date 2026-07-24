"""Deep-learning agents and the meta-labeling sizing layer.

Two ways deep learning plugs into the existing ecosystem — *augmenting* the five
technical agents, never replacing them:

* `NeuralAgent` — a 6th agent whose signal is the position output of a Sharpe-loss
  MLP (trained offline, frozen for live use). It reads the same OHLC frame and
  emits a [-1, 1] signal like any other agent, so it drops straight into the
  `AgentPool` / ensemble.
* `MetaLabeler` — a secondary classifier (López de Prado meta-labeling) that
  predicts the probability the ensemble's *side* is right and maps it to a size
  in [0, 1] via bet-sizing. It scales positions; it never flips them.

Both are seed-ensembled (`ModelBundle` holds several MLPs trained from different
seeds and averages them — NN training is high-variance, averaging cuts it).
Models are trained walk-forward and persisted to JSON so live prediction is a
fast, frozen forward pass with no lookahead.

`pooled_dataset` assembles a single cross-pair training set — pooling pairs gives
the model far more (and cross-sectionally richer) data than any single pair, which
matters a lot given how little signal daily FX carries.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ensemble, features
from .agents import Agent, AgentPool, PairContext, default_agents
from .fx_config import FXParams
from .fx_data import closes
from .nn import MLP, StandardScaler
from .pairs import get_pair


# ---------------------------------------------------------------------------
# Persisted model bundle (seed ensemble + scaler + feature spec)
# ---------------------------------------------------------------------------
@dataclass
class ModelBundle:
    task: str                       # "sharpe" | "binary"
    feature_cols: list[str]
    models: list[MLP]
    scaler: StandardScaler
    meta: dict = field(default_factory=dict)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Seed-averaged prediction on raw (unscaled) features."""
        Xs = self.scaler.transform(np.asarray(X, dtype=float))
        return np.mean([m.predict(Xs) for m in self.models], axis=0)

    def to_dict(self) -> dict:
        return {"task": self.task, "feature_cols": self.feature_cols,
                "models": [m.to_dict() for m in self.models],
                "scaler": self.scaler.to_dict(), "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "ModelBundle":
        return cls(task=d["task"], feature_cols=d["feature_cols"],
                   models=[MLP.from_dict(m) for m in d["models"]],
                   scaler=StandardScaler.from_dict(d["scaler"]), meta=d.get("meta", {}))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path: str) -> "ModelBundle":
        with open(path) as f:
            return cls.from_dict(json.load(f))


def _frame_for_pair(bundle: ModelBundle, bars: pd.DataFrame, sym: str) -> pd.DataFrame:
    """Build the bundle's feature columns for one pair (order-aligned, 0-filled)."""
    feats = features.build_features(bars, pair=get_pair(sym))
    return feats.reindex(columns=bundle.feature_cols).replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------------------
# Neural agent (Sharpe-loss position network)
# ---------------------------------------------------------------------------
class NeuralAgent(Agent):
    """A learned 6th agent. With no model loaded it returns a flat (zero) signal,
    so the ecosystem runs unchanged until a model is trained and attached."""
    name = "neural"

    def __init__(self, bundle: ModelBundle | None = None):
        self.bundle = bundle

    def generate(self, bars, ctx, p):
        idx = bars.index
        if self.bundle is None:
            return pd.Series(0.0, index=idx)
        feats = _frame_for_pair(self.bundle, bars, ctx.pair.symbol)
        X = feats.to_numpy()
        ok = np.isfinite(X).all(axis=1)
        sig = np.zeros(len(idx))
        if ok.any():
            sig[ok] = self.bundle.predict(X[ok]).ravel()
        return pd.Series(sig, index=idx).clip(-1.0, 1.0)


# ---------------------------------------------------------------------------
# Meta-labeling sizing layer
# ---------------------------------------------------------------------------
class MetaLabeler:
    """Scales (never flips) a primary tilt by the meta-model's confidence."""

    def __init__(self, bundle: ModelBundle | None = None):
        self.bundle = bundle

    def size(self, bars: pd.DataFrame, sym: str, base_signals: pd.DataFrame,
             tilt: pd.Series) -> pd.Series:
        from .validation import bet_size_from_prob
        if self.bundle is None:
            return tilt
        feats = features.build_features(bars, agent_signals=base_signals,
                                        pair=get_pair(sym)).assign(tilt=tilt)
        feats = feats.reindex(columns=self.bundle.feature_cols)
        X = feats.to_numpy()
        ok = np.isfinite(X).all(axis=1)
        mult = np.zeros(len(tilt))
        if ok.any():
            prob = self.bundle.predict(X[ok]).ravel()
            mult[ok] = np.clip(bet_size_from_prob(prob), 0.0, 1.0)
        return tilt * pd.Series(mult, index=tilt.index)


def default_neural_agents(bundle: ModelBundle | None = None) -> list[Agent]:
    """The five technical agents plus the neural agent."""
    return [*default_agents(), NeuralAgent(bundle)]


# ---------------------------------------------------------------------------
# Pooled cross-pair dataset assembly
# ---------------------------------------------------------------------------
def pooled_dataset(panel: dict[str, pd.DataFrame], p: FXParams, *,
                   label: str = "sharpe", horizon: int = 1,
                   include_agents: bool = False, pt_mult: float = 1.5,
                   sl_mult: float = 1.0, max_h: int = 10
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Assemble a pooled (all pairs) training set.

    label="sharpe": y is the forward `horizon`-bar return (NeuralAgent target).
    label="meta"  : y is the triple-barrier outcome of the ensemble's side
                    (MetaLabeler target); features include the agent signals and
                    the ensemble tilt.

    Returns (X, y, time_index, pair_index, feature_cols), all NaN rows dropped.
    """
    from . import indicators as ind

    sig_panel = None
    tilts = None
    if include_agents or label == "meta":
        pool = AgentPool(max_workers=1)
        contexts = {s: PairContext(get_pair(s)) for s in panel}
        sig_panel = pool.evaluate(panel, contexts, p)
        rets = closes(panel).pct_change(fill_method=None)
        tilts = ensemble.ensemble_tilts(sig_panel, rets, p)

    Xs, ys, ts, ps = [], [], [], []
    cols: list[str] | None = None
    for sym, bars in panel.items():
        ag = sig_panel[sym] if (sig_panel is not None) else None
        feats = features.build_features(bars, agent_signals=ag, pair=get_pair(sym))
        if label == "sharpe":
            y = bars["close"].pct_change(horizon, fill_method=None).shift(-horizon)
        else:  # meta
            assert tilts is not None  # label == "meta" always populates tilts above
            side = np.sign(tilts[sym]).replace(0.0, np.nan)
            atr = ind.atr(bars["high"], bars["low"], bars["close"], p.atr_window)
            y = features.triple_barrier_labels(bars["close"], atr, side,
                                               pt_mult, sl_mult, max_h)
            feats = feats.assign(tilt=tilts[sym])
            feats = feats[side.notna()]          # only where the primary fired
            y = y[side.notna()]
        X, y = features.align_xy(feats, y)
        if len(X) == 0:
            continue
        if cols is None:
            cols = list(X.columns)
        Xs.append(X[cols].to_numpy())
        ys.append(y.to_numpy())
        ts.append(X.index.to_numpy())
        ps.append(np.full(len(X), sym))

    if not Xs:
        return np.empty((0, 0)), np.empty(0), np.empty(0), np.empty(0), []
    assert cols is not None  # non-empty Xs means the first iteration set cols
    return (np.vstack(Xs), np.concatenate(ys), np.concatenate(ts),
            np.concatenate(ps), cols)
