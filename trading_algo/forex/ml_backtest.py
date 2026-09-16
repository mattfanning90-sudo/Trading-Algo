"""Honest, no-lookahead evaluation of every strategy — including the DL ones.

This is the credibility centerpiece. It builds out-of-sample daily returns for:
  * each of the 5 technical agents,
  * the equal-weight and Hedge ensembles,
  * the **NeuralAgent** (Sharpe-loss net) trained *walk-forward* (purged+embargo),
  * the **meta-labeled ensemble** (ensemble side, sized by a walk-forward meta net),
then scores them with annualised Sharpe, the Probabilistic Sharpe Ratio, the
**Deflated Sharpe Ratio** (corrected for the number of strategies tried) and the
**Probability of Backtest Overfitting** across the whole comparison.

The neural/meta signals are produced by `walkforward.walk_forward_predict`, so a
prediction at time t comes only from a model trained on data before t — the same
no-lookahead guarantee the rule-based agents have by construction. Costs are
always on (half-spread per unit turnover).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import ensemble, marks, ml_agent, validation
from .agents import AgentPool, PairContext, default_agents
from .fx_config import FX_RISK_FREE, FXParams
from .fx_data import closes
from .nn import MLP
from .pairs import get_pair
from .panel_index import build_panel_index
from .walkforward import walk_forward_predict


# ---------------------------------------------------------------------------
# Turn a signal panel into an equal-weight, cost-aware daily return series
# ---------------------------------------------------------------------------
def strategy_returns(panel: dict[str, pd.DataFrame], signal_panel: pd.DataFrame,
                     p: FXParams) -> pd.Series:
    """Equal-weight across pairs; signal decided at t earns ret over t→t+1; half
    the dealing spread is charged on every change in a pair's signal."""
    px = closes(panel)
    sig = signal_panel.reindex(index=px.index, columns=px.columns).fillna(0.0).clip(-1, 1)
    rets = px.pct_change(fill_method=None).fillna(0.0)

    pos = sig.shift(1).fillna(0.0)                       # held over the next bar
    gross = (pos * rets).mean(axis=1)
    turn = pos.diff().abs().fillna(0.0)
    half_spread = pd.DataFrame(                          # THE shared half-spread
        {s: marks.half_spread_fraction(get_pair(s), px[s]) for s in px.columns},
        index=px.index)
    cost = (turn * half_spread).mean(axis=1)
    return (gross - cost).iloc[1:]


def _annual_metrics(ret: pd.Series, n_trials: int, sr_variance: float) -> dict:
    r = ret.dropna()
    if len(r) < 20 or r.std() == 0:
        return {"Sharpe": 0.0, "CAGR": 0.0, "PSR": 0.0, "DSR": 0.0}
    ppy = marks.periods_per_year(r.index)                # bar-aware (daily -> 252)
    sharpe = r.mean() / r.std() * np.sqrt(ppy)
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (ppy / len(r)) - 1
    return {
        "Sharpe": round(float(sharpe), 2),
        "CAGR": round(float(cagr), 4),
        "MaxDD": round(float((eq / eq.cummax() - 1).min()), 4),
        "PSR": round(validation.probabilistic_sharpe_ratio(r.to_numpy()), 3),
        "DSR": round(validation.deflated_sharpe_ratio(r.to_numpy(), n_trials,
                                                      sr_variance), 3),
    }


# ---------------------------------------------------------------------------
# Walk-forward neural / meta signal panels
# ---------------------------------------------------------------------------
def _sharpe_factory(n_feat: int, seed: int = 0, *, cost_aware: bool = False):
    """Build the walk-forward's neural model; everything but the objective is
    held identical so the two can be compared on the same folds.

    `cost_aware=True` selects the net-of-turnover portfolio objective. That one
    also needs a `panel_index` describing the rows of the fold it is about to
    train on, which this factory cannot know — so it deliberately leaves the
    attribute `None` for the caller to fill in per fold. No placeholder: `fit`
    refuses by name while the index is None, and that loud failure is the only
    thing standing between a forgotten fold and a silently meaningless loss.
    """
    task = "sharpe_net" if cost_aware else "sharpe"
    return lambda: MLP([n_feat, 32, 1], hidden_act="tanh", task=task,
                       l2=1e-3, dropout=0.1, seed=seed)


def _meta_factory(n_feat: int, seed: int = 0):
    return lambda: MLP([n_feat, 32, 16, 1], hidden_act="relu", task="binary",
                       l2=1e-2, dropout=0.3, seed=seed)


def _scatter(preds: np.ndarray, times: np.ndarray, pairs: np.ndarray,
             index: pd.Index, columns) -> pd.DataFrame:
    df = pd.DataFrame(index=index, columns=list(columns), dtype=float)
    s = pd.DataFrame({"t": times, "p": pairs, "v": preds}).dropna(subset=["v"])
    for sym, grp in s.groupby("p"):
        df.loc[pd.DatetimeIndex(grp["t"]), sym] = grp["v"].to_numpy()
    return df


def neural_oos_signal(panel, p, *, n_folds=6, embargo=5, min_train=400,
                      epochs=400, val_frac=0.2, seeds=3) -> pd.DataFrame:
    """Seed-ensembled walk-forward signal from the net-of-turnover objective.

    `_sharpe_factory` defaulted to seed=0, so every out-of-sample number ever
    reported for this model — including the -0.62 Sharpe that let it trade the
    live books — was a SINGLE DRAW of a high-variance training process, with the
    variance never measured. The deployed artefact has always been a seed
    ensemble (`ModelBundle` averages several `MLP`s); grading one draw and
    shipping an average judges the artefact by a model nobody deploys.

    So the grade is averaged the same way the artefact is: `seeds` independent
    walk-forward runs, identical in every respect but the initialisation,
    averaged into one signal panel. The fold geometry does not depend on the
    seed, so every run marks the same rows out-of-sample and the average keeps
    exactly their coverage.
    """
    frames = [_neural_oos_once(panel, p, seed=s, n_folds=n_folds, embargo=embargo,
                               min_train=min_train, epochs=epochs, val_frac=val_frac)
              for s in range(max(1, int(seeds)))]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return sum(frames) / len(frames)


def _neural_oos_once(panel, p, *, seed=0, n_folds=6, embargo=5, min_train=400,
                     epochs=400, val_frac=0.2) -> pd.DataFrame:
    """ONE walk-forward pass at a single initialisation. See `neural_oos_signal`.

    Two things this has to get right and nothing downstream can check:

    * **The index is per fold.** `walk_forward_predict` fits on a subset of rows,
      and `PanelIndex` is addressed by row position, so the index must be built
      from that subset. The whole-panel one would pass every guard and then group
      the wrong rows into a timestamp — a plausible number from a meaningless
      objective. Hence `index_factory`, which the walk-forward calls per block.
    * **Costs in the model's own units.** `idx.cost` is the half-spread divided by
      the same trailing vol the target was normalised by (`panel_index`), so the
      loss weighs a trade's cost against the return it is actually competing with.
      The half-spread itself comes from `marks.half_spread_fraction`, the one
      definition every cost path in the project derives from (invariant #2).
    """
    X, y, t, pairs, cols, vols = ml_agent.pooled_dataset(panel, p, label="sharpe",
                                                         horizon=1)
    if len(X) == 0:
        return pd.DataFrame()
    px = closes(panel)
    spreads = {s: float(marks.half_spread_fraction(get_pair(s), px[s]).mean())
               for s in panel}

    def index_factory(rows):
        """Index ONE block of rows, addressed to its own positions."""
        rows = np.asarray(rows)
        return build_panel_index(t[rows], pairs[rows], vols[rows], spreads)

    preds = walk_forward_predict(
        X, y, t, _sharpe_factory(len(cols), seed, cost_aware=True), n_folds=n_folds,
        label_horizon=1, embargo=embargo, min_train=min_train,
        index_factory=index_factory, val_frac=val_frac,
        # batch_size: "sharpe_net" must see the whole block at once, since the
        # index addresses it positionally. patience only bites because val_frac
        # gives the fold a validation block to score.
        fit_kwargs={"epochs": epochs, "batch_size": 10 ** 9, "lr": 1e-2,
                    "patience": 25})
    return _scatter(preds, t, pairs, px.index, px.columns)


def meta_oos_signal(panel, p, *, n_folds=6, embargo=5, min_train=400,
                    epochs=120) -> pd.DataFrame:
    """Ensemble tilt sized by the walk-forward meta-model's bet size."""
    pool = AgentPool(max_workers=1)
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    sig = pool.evaluate(panel, contexts, p)
    rets = closes(panel).pct_change(fill_method=None)
    tilts = ensemble.ensemble_tilts(sig, rets, p)

    X, y, t, pairs, cols, _ = ml_agent.pooled_dataset(panel, p, label="meta", horizon=1)
    if len(X) == 0:
        return tilts
    prob = walk_forward_predict(
        X, y, t, _meta_factory(len(cols)), n_folds=n_folds, label_horizon=10,
        embargo=embargo, min_train=min_train,
        fit_kwargs={"epochs": epochs, "batch_size": 64, "lr": 1e-3})
    size = pd.DataFrame(index=closes(panel).index, columns=list(closes(panel).columns),
                        dtype=float)
    sc = _scatter(validation.bet_size_from_prob(np.nan_to_num(prob, nan=0.5)),
                  t, pairs, size.index, size.columns).clip(lower=0.0)
    return (tilts * sc.reindex_like(tilts)).fillna(0.0)


# ---------------------------------------------------------------------------
# Full comparison
# ---------------------------------------------------------------------------
def run_ml_backtest(panel: dict[str, pd.DataFrame], p: FXParams, *,
                    include_ml: bool = True, n_folds: int = 6) -> dict:
    """Build OOS returns for every strategy and score them (Sharpe/PSR/DSR/PBO)."""
    pool = AgentPool(max_workers=1)
    contexts = {s: PairContext(get_pair(s)) for s in panel}
    sig = pool.evaluate(panel, contexts, p)
    rets = closes(panel).pct_change(fill_method=None)

    signal_panels: dict[str, pd.DataFrame] = {}
    for agent in default_agents():
        signal_panels[agent.name] = pd.DataFrame(
            {s: sig[s][agent.name] for s in panel})
    signal_panels["ens_equal"] = ensemble.ensemble_tilts(
        sig, rets, p.with_overrides(agent_weighting="equal"))
    signal_panels["ens_hedge"] = ensemble.ensemble_tilts(
        sig, rets, p.with_overrides(agent_weighting="hedge"))
    if include_ml:
        neural = neural_oos_signal(panel, p, n_folds=n_folds)
        if not neural.empty:
            signal_panels["neural_oos"] = neural
        meta = meta_oos_signal(panel, p, n_folds=n_folds)
        if not meta.empty:
            signal_panels["meta_oos"] = meta

    rets_by_strat = {name: strategy_returns(panel, sp, p)
                     for name, sp in signal_panels.items()}
    mat = pd.DataFrame(rets_by_strat).dropna()
    n_trials = mat.shape[1]
    per_period_sr = {c: validation.sharpe_ratio(mat[c].to_numpy()) for c in mat.columns}
    sr_variance = float(np.var(list(per_period_sr.values()))) if n_trials > 1 else 0.0

    metrics = {name: _annual_metrics(r, n_trials, sr_variance)
               for name, r in rets_by_strat.items()}
    pbo = validation.pbo(mat.to_numpy(), n_splits=min(10, max(2, len(mat) // 50)))
    return {
        "metrics": metrics,
        "returns": rets_by_strat,
        "pbo": pbo,
        "n_trials": n_trials,
        "risk_free": FX_RISK_FREE,
    }


def format_report(res: dict) -> str:
    lines = ["", "=== FX strategy comparison (out-of-sample, costs on) ===",
             f"{'strategy':<14}{'Sharpe':>8}{'CAGR':>9}{'MaxDD':>9}{'PSR':>7}{'DSR':>7}"]
    order = sorted(res["metrics"], key=lambda k: -res["metrics"][k].get("Sharpe", 0))
    for name in order:
        m = res["metrics"][name]
        lines.append(f"{name:<14}{m.get('Sharpe',0):>8.2f}{m.get('CAGR',0):>9.2%}"
                     f"{m.get('MaxDD',0):>9.2%}{m.get('PSR',0):>7.2f}{m.get('DSR',0):>7.2f}")
    lines += [
        f"\nStrategies compared (N): {res['n_trials']}",
        f"Probability of Backtest Overfitting (PBO): {res['pbo']:.2f}",
        "PSR>0.95 = SR>0 credible; DSR>0.95 = survives the multiple-testing bar; "
        "low PBO = selection generalises.",
    ]
    return "\n".join(lines)
