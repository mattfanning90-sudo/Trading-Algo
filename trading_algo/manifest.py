"""Run manifests + experiment ledger (backlog F17 / foundation P0-G).

Every backtest / paper / sweep run should be reproducible and auditable: which
code, which parameters, which universe, over what dates, producing what metrics.
Today none of that is recorded — sweep results are printed and lost, so F2's
Deflated Sharpe has no honest trial count to deflate against.

This module (pure stdlib) provides:

  * ``build_manifest(...)``   -> a dict describing one run
  * ``params_fingerprint(p)`` -> a stable hash of a StrategyParams
  * ``validate_manifest(m)``  -> list[str]  (empty == valid)
  * ``write_manifest(m,path)`` and ``append_run(ledger, m)`` / ``trial_count``

The ledger is a JSONL file (one manifest per line) so trials accumulate
append-only across runs — that append-only count is exactly what a Deflated
Sharpe / PBO gate needs.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

MANIFEST_SCHEMA_VERSION = 1
KINDS = ("backtest", "portfolio", "paper", "sweep", "tune")


def _git_commit() -> str:
    """Best-effort current commit; falls back to CI env, then 'unknown'."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(__file__),
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("GITHUB_SHA", "unknown")


def params_to_dict(params) -> dict:
    """A plain dict of a StrategyParams (or any dataclass); pass-through a dict."""
    if dataclasses.is_dataclass(params) and not isinstance(params, type):
        return dataclasses.asdict(params)
    if isinstance(params, dict):
        return dict(params)
    return {"repr": repr(params)}


def params_fingerprint(params) -> str:
    """Stable short hash of the strategy parameters — same knobs, same id."""
    payload = json.dumps(params_to_dict(params), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def build_manifest(kind: str, *, params, regions: list[str], metrics: dict,
                   data_range: tuple | None = None, synthetic: bool = False,
                   point_in_time: bool = False, extra: dict | None = None,
                   created_utc: str | None = None) -> dict:
    """Assemble a manifest for one run. `created_utc` is injectable for tests."""
    if kind not in KINDS:
        raise ValueError(f"unknown manifest kind '{kind}' (known: {KINDS})")
    start = end = None
    if data_range is not None:
        start, end = (str(data_range[0]), str(data_range[1]))
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": kind,
        "git_commit": _git_commit(),
        "created_utc": created_utc or datetime.now(timezone.utc).isoformat(),
        "synthetic": bool(synthetic),
        "point_in_time": bool(point_in_time),
        "regions": list(regions),
        "params_fingerprint": params_fingerprint(params),
        "params": params_to_dict(params),
        "data_range": {"start": start, "end": end},
        "metrics": dict(metrics),
        "extra": dict(extra or {}),
    }


def validate_manifest(m) -> list[str]:
    errs: list[str] = []
    if not isinstance(m, dict):
        return ["manifest must be an object"]
    if m.get("kind") not in KINDS:
        errs.append(f"kind '{m.get('kind')}' not in {KINDS}")
    for key, typ in (("git_commit", str), ("created_utc", str),
                     ("params_fingerprint", str)):
        if not isinstance(m.get(key), typ):
            errs.append(f"missing/invalid '{key}'")
    if not isinstance(m.get("regions"), list) or not m.get("regions"):
        errs.append("'regions' must be a non-empty list")
    if not isinstance(m.get("metrics"), dict):
        errs.append("'metrics' must be an object")
    if not isinstance(m.get("params"), dict):
        errs.append("'params' must be an object")
    return errs


def write_manifest(m: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(m, f, indent=2)
    return path


def append_run(ledger_path: str, m: dict) -> None:
    """Append one manifest to the JSONL experiment ledger (append-only)."""
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
    with open(ledger_path, "a") as f:
        f.write(json.dumps(m) + "\n")


def read_ledger(ledger_path: str) -> list[dict]:
    if not os.path.exists(ledger_path):
        return []
    rows = []
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def trial_count(ledger_path: str, kind: str | None = None,
                params_fingerprint_filter: str | None = None) -> int:
    """How many runs are recorded — the honest n_trials for a Deflated Sharpe.

    Filter by `kind` and/or a specific params fingerprint to count only the
    relevant search (e.g. every sweep trial of one strategy variant)."""
    rows = read_ledger(ledger_path)
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    if params_fingerprint_filter is not None:
        rows = [r for r in rows if r.get("params_fingerprint") == params_fingerprint_filter]
    return len(rows)
