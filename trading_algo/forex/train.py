"""Train the deep-learning models and evaluate everything out-of-sample.

What it does:
  1. Builds the pooled cross-pair dataset(s).
  2. Trains seed-ensembled `ModelBundle`s on ALL data (the frozen models the live
     `NeuralAgent` / `MetaLabeler` use) and saves them to `models/`.
  3. Runs the no-lookahead walk-forward comparison (`ml_backtest`) and prints /
     writes a report with Sharpe, Probabilistic & Deflated Sharpe, and PBO.

    python -m trading_algo.forex.train --synthetic                # offline
    python -m trading_algo.forex.train --out fx_ml_report.md      # real data
    python -m trading_algo.forex.train --no-train                 # evaluate only

This is what the GitHub Action runs in the cloud (runners have internet for real
Yahoo FX data); the report goes to the run summary and the models are uploaded as
artifacts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import functools
import os

import numpy as np

from . import fx_config as cfg
from . import promotion
from . import fx_data, ml_agent
from .fx_config import profile
from .fx_data import closes
from .ml_agent import ModelBundle
from .ml_backtest import (GRADED_EMBARGO, GRADED_EPOCHS, GRADED_LR,
                          GRADED_PATIENCE, GRADED_VAL_FRAC, _half_spreads,
                          _meta_factory, _sharpe_factory, format_report,
                          run_ml_backtest)
from .nn import StandardScaler
from .panel_index import build_panel_index
from .pairs import DEFAULT_UNIVERSE
from .walkforward import row_positions, validation_split

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def _load(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    return fx_data.load_panel(symbols, cfg.START, use_cache=True)


def _train_bundle(X, y, cols, task, factory, seeds, fit_kwargs, *,
                  panel_index=None, X_val=None, y_val=None,
                  val_panel_index=None) -> ModelBundle:
    """Fit a seed-ensembled bundle on the FIT rows (scaler fit on those rows).

    `panel_index` is the row bookkeeping the cost-aware objective needs. Every
    seed fits the SAME rows, so one index serves them all (`PanelIndex` is
    frozen). `MLP.fit` refuses by name if the objective needs one and it is
    missing, or if its row count disagrees with the rows being fit.

    `X_val`/`y_val` turn early stopping on. The scaler is fit on the FIT rows
    only and then applied to the validation rows, exactly as the walk-forward
    does it: the block early stopping trusts must be scored on statistics it did
    not help produce. The persisted scaler is the one the live model runs with,
    so it must be the one the model was fit under.
    """
    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    kwargs = dict(fit_kwargs)
    n_val = 0
    if X_val is not None and y_val is not None and len(X_val):
        n_val = int(len(X_val))
        kwargs["X_val"] = scaler.transform(np.asarray(X_val, dtype=float))
        kwargs["y_val"] = np.asarray(y_val, dtype=float).reshape(-1, 1)
        if val_panel_index is not None:
            kwargs["val_panel_index"] = val_panel_index
    models = []
    for s in range(seeds):
        m = factory(len(cols), s)()    # factory(n_feat, seed) -> (lambda -> MLP)
        if panel_index is not None:
            m.panel_index = panel_index
        m.fit(Xs, y, **kwargs)
        models.append(m)
    return ModelBundle(task=task, feature_cols=cols, models=models, scaler=scaler,
                       meta={"seeds": seeds, "n_samples": int(len(X)),
                             "n_held_out": n_val})


def train_models(panel, p, seeds=3, models_dir=MODELS_DIR) -> dict:
    os.makedirs(models_dir, exist_ok=True)
    out = {}

    # The DEPLOYED artefact must be the objective the grade describes. The
    # walk-forward (`ml_backtest.neural_oos_signal`) grades the cost-aware
    # `sharpe_net` model, `record_evaluation` stamps that grade onto THIS bundle
    # and `promotion.clears_floor` reads it to decide whether the model may
    # trade. Ship the legacy `sharpe` model here and the gate would let one
    # model trade on a different model's number.
    Xn, yn, tn, pn, cols_n, vn = ml_agent.pooled_dataset(panel, p, label="sharpe",
                                                        horizon=1)
    if len(Xn):
        # The DEPLOYED fit must follow the recipe the GRADE describes, not merely
        # carry the graded objective. `ml_backtest` grades this model on folds
        # that hold out a purged, contiguous, later validation slice and early-stop
        # against it; fitting the shipped bundle to convergence on every row would
        # hand `promotion.clears_floor` a grade earned under one procedure and an
        # artefact produced by a laxer one — a model MORE overfit than its own
        # number claims. Same split helper as the walk-forward, same knobs.
        px = closes(panel)
        row_pos = row_positions(tn)
        gap = 1 + GRADED_EMBARGO                     # label_horizon + embargo
        fit_mask, val_mask = validation_split(
            np.ones(len(Xn), dtype=bool), row_pos, GRADED_VAL_FRAC, gap)
        if val_mask is None:
            # Too little history to give a block up. Training blind here is the
            # exact failure this is guarding, and such a panel grades nothing
            # either (every fold would be skipped), so ship no model at all and
            # let the promotion floor refuse for want of a grade.
            print("  NeuralAgent bundle SKIPPED — too little history for a "
                  "purged validation block; an unvalidated fit is not shippable.")
        else:
            def index_for(rows):
                """Index ONE block, addressed to its own row positions.

                Same contract as the walk-forward's `index_factory`: `sharpe_net`
                must see its block in one batch, and `PanelIndex` addresses that
                batch positionally, so the fit rows and the validation rows each
                need their own.
                """
                return build_panel_index(tn[rows], pn[rows], vn[rows],
                                         _half_spreads(px, upto=px.index.max()))

            n_fit = int(fit_mask.sum())
            bundle = _train_bundle(
                Xn[fit_mask], yn[fit_mask], cols_n, "sharpe_net",
                functools.partial(_sharpe_factory, cost_aware=True), seeds,
                {"epochs": GRADED_EPOCHS, "batch_size": n_fit, "lr": GRADED_LR,
                 "patience": GRADED_PATIENCE},
                panel_index=index_for(fit_mask),
                X_val=Xn[val_mask], y_val=yn[val_mask],
                val_panel_index=index_for(val_mask))
            path = os.path.join(models_dir, "neural_sharpe.json")
            bundle.save(path)
            out["neural"] = path
            print(f"  trained NeuralAgent bundle ({seeds} seeds, {n_fit} samples, "
                  f"{int(val_mask.sum())} held out for early stopping) -> {path}")

    Xm, ym, _, _, cols_m, _ = ml_agent.pooled_dataset(panel, p, label="meta", horizon=1)
    if len(Xm):
        bundle = _train_bundle(Xm, ym, cols_m, "binary", _meta_factory, seeds,
                               {"epochs": 150, "batch_size": 64, "lr": 1e-3})
        path = os.path.join(models_dir, "meta_label.json")
        bundle.save(path)
        out["meta"] = path
        print(f"  trained MetaLabeler bundle ({seeds} seeds, {len(Xm)} samples) -> {path}")
    return out


def record_evaluation(path: str, metrics: dict | None, *,
                      synthetic: bool = False) -> None:
    """Stamp a saved bundle with the out-of-sample grade the promotion gate reads.

    Training saves the model and the walk-forward comparison grades it afterwards,
    so the score used to live only in stdout and a markdown report — nowhere the
    loader could see it. `forex.promotion.clears_floor` reads what this writes, so
    without this the gate would (safely but uselessly) refuse every model forever.

    A SYNTHETIC run records that it was synthetic and no Sharpe, which the floor
    treats as ungraded. Invariant #5: synthetic numbers are a pipeline test, never
    performance — and they must never be able to promote a model.
    """
    if not os.path.exists(path):
        return
    bundle = ml_agent.ModelBundle.load(path)
    if synthetic:
        evaluation = {"synthetic": True,
                      "note": "synthetic run — pipeline test only, never a grade"}
    else:
        m = metrics or {}
        if m.get("Sharpe") is None:
            # Nothing was graded this run (e.g. --no-ml). Writing an empty grade
            # would overwrite a passing one and demote a working model because of
            # a reporting flag, so leave whatever is there alone. A grade is only
            # ever replaced by another real grade.
            return
        evaluation = {"sharpe": m.get("Sharpe"), "dsr": m.get("DSR"),
                      "cagr": m.get("CAGR"), "max_dd": m.get("MaxDD")}
    evaluation["graded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundle.meta["evaluation"] = evaluation
    bundle.save(path)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Train + evaluate the FX deep-learning layer")
    ap.add_argument("--synthetic", action="store_true", help="offline synthetic data")
    ap.add_argument("--profile", default="balanced", choices=cfg.profile_names())
    ap.add_argument("--seeds", type=int, default=3, help="models per seed-ensemble")
    ap.add_argument("--out", default=None, help="write the report to this markdown file")
    ap.add_argument("--models-dir", default=MODELS_DIR)
    ap.add_argument("--no-train", action="store_true", help="skip training; evaluate only")
    ap.add_argument("--no-ml", action="store_true", help="skip ML strategies in the comparison")
    ap.add_argument("--folds", type=int, default=6)
    args = ap.parse_args(argv)

    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, not performance.")
    panel = _load(DEFAULT_UNIVERSE, args.synthetic)
    if not panel:
        raise SystemExit("No FX data (offline? try --synthetic).")
    p = profile(args.profile)

    if not args.no_train:
        print("Training models...")
        train_models(panel, p, seeds=args.seeds, models_dir=args.models_dir)

    print("Running walk-forward out-of-sample comparison...")
    res = run_ml_backtest(panel, p, include_ml=not args.no_ml, n_folds=args.folds)
    report = format_report(res)
    print(report)

    # Stamp the grade onto the model so the promotion floor can read it. Until
    # this existed the score lived only here, and `ml_pool` loaded whatever was
    # on disk — which is how a -0.62 Sharpe model traded the live books.
    #
    # `--no-ml` skips the ML strategies entirely, so there is NO grade this run.
    # Stamping an empty one would overwrite a previously passing grade with
    # nothing and silently demote a working model, so leave the model untouched.
    neural_path = os.path.join(args.models_dir, "neural_sharpe.json")
    if args.no_ml:
        print("\n--no-ml: the model was not graded this run; its existing grade stands.")
    else:
        record_evaluation(neural_path, res["metrics"].get("neural_oos"),
                          synthetic=args.synthetic)
        ok, reason = promotion.clears_floor(
            ml_agent.ModelBundle.load(neural_path).meta.get("evaluation")
            if os.path.exists(neural_path) else None)
        print(f"\nPromotion floor: {'PASS' if ok else 'REFUSED'} — {reason}")
        if not ok:
            print("  The live books will run the 5 technical agents only.")
    if args.out:
        with open(args.out, "w") as f:
            f.write("# FX deep-learning walk-forward report\n")
            f.write("\n```\n" + report + "\n```\n")
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
