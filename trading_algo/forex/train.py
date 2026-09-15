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
import os

import numpy as np

from . import fx_config as cfg
from . import promotion
from . import fx_data, ml_agent
from .fx_config import profile
from .ml_agent import ModelBundle
from .ml_backtest import _meta_factory, _sharpe_factory, format_report, run_ml_backtest
from .nn import StandardScaler
from .pairs import DEFAULT_UNIVERSE

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def _load(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    return fx_data.load_panel(symbols, cfg.START, use_cache=True)


def _train_bundle(X, y, cols, task, factory, seeds, fit_kwargs) -> ModelBundle:
    """Fit a seed-ensembled bundle on ALL rows (scaler fit once on the same rows)."""
    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    models = []
    for s in range(seeds):
        m = factory(len(cols), s)()    # factory(n_feat, seed) -> (lambda -> MLP)
        m.fit(Xs, y, **fit_kwargs)
        models.append(m)
    return ModelBundle(task=task, feature_cols=cols, models=models, scaler=scaler,
                       meta={"seeds": seeds, "n_samples": int(len(X))})


def train_models(panel, p, seeds=3, models_dir=MODELS_DIR) -> dict:
    os.makedirs(models_dir, exist_ok=True)
    out = {}

    Xn, yn, _, _, cols_n, _ = ml_agent.pooled_dataset(panel, p, label="sharpe", horizon=1)
    if len(Xn):
        bundle = _train_bundle(Xn, yn, cols_n, "sharpe", _sharpe_factory, seeds,
                               {"epochs": 200, "batch_size": 100000, "lr": 1e-2})
        path = os.path.join(models_dir, "neural_sharpe.json")
        bundle.save(path)
        out["neural"] = path
        print(f"  trained NeuralAgent bundle ({seeds} seeds, {len(Xn)} samples) -> {path}")

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
