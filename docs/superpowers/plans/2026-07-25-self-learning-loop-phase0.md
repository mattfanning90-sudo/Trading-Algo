# Self-Learning Loop — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the inert foundation of the self-learning loop — a causal trade-outcome ledger and an upgraded validation gate (Gate v2) proven to reject noise — with no live adaptation.

**Architecture:** A new `trading_algo/learning/` package logs every decision/outcome to an append-only, checksummed JSONL ledger, and wraps the existing DSR/PBO machinery (validation.py, walkforward.py, manifest.py) with cumulative-trial deflation, a single locked never-reused holdout, and sequential alpha-spending. Champion stays the static config; nothing adapts.

**Tech Stack:** Python 3.11+, numpy, pandas, stdlib (hashlib/json). Reuses trading_algo/{validation,manifest,walkforward,promotion,signals}.py.

## Global Constraints

- Ships INERT: champion = static default config; NO learning layers (L1-L4) in Phase 0.
- Preserve invariants: no-lookahead (data <= t); costs always on; ONE weight function (compute_targets untouched); backtest==paper.
- Deflate against the CUMULATIVE trial count (manifest.trial_count), never the per-batch grid size.
- Locked holdout is scored at most ONCE per candidate; regime features are CAUSAL (rolling/expanding only).
- New deps: none. Pure stdlib for ledger integrity.
- Exit criteria: noise-falsification test green; replay-determinism test green; manifest.trial_count wired into purged_cv_report.

---

## Task 1: Learning ledger — package init + checksummed append core

**Files:**
- Create: `trading_algo/learning/__init__.py`
- Create: `trading_algo/learning/ledger.py`
- Test: `tests/test_learning_ledger.py`

**Interfaces:**
- Produces: `ledger._checksum(rec: dict) -> str` (hashlib.sha1 over `json.dumps(rec, sort_keys=True)`)
- Produces: `ledger.record_decision(ledger_dir, account, *, decision_id, ts, book, champion_id, features: dict, regime: dict, action: dict, considered: list, rationale: dict) -> None`
- Produces: `ledger.record_outcome(ledger_dir, account, *, decision_id, exit_ts, holding_bars, gross_return, cost, carry, net_return, label, attribution: dict) -> None`
- On disk: `decision_log_{account}.jsonl`, `outcome_log_{account}.jsonl`; each line `{"_v":1,"_sha1":<hex>,"rec":{...}}`

Steps:

- [ ] **Step 1: Create the package `__init__.py`.** Marks Phase 0 as inert/log-only so the intent is documented at the package root. This is the canonical package init and it is created ONCE here; later Gate v2 tasks add `gate.py` alongside it without re-creating this file.

```python
# trading_algo/learning/__init__.py
"""Self-learning trade loop (Phase 0: INERT — log-only, static champion).

Phase 0 ships the plumbing WITHOUT any live adaptation. Every rebalance decision
and its realised outcome are recorded to a tamper-evident ledger, market state is
tagged with causal regime features, and the validation gate is upgraded to deflate
against the CUMULATIVE trial count. No learning layer (L1-L4) is active: the
champion is the static default config and nothing mutates it. The point of Phase 0
is to LOG faithfully and PROVE the gate rejects noise — not to learn.
"""
from __future__ import annotations

LEARNING_PHASE = 0
```

- [ ] **Step 2: Write the failing integrity tests (RED).** Assert the checksum is deterministic + sensitive, and that both record functions write one `{"_v","_sha1","rec"}` line whose stored `_sha1` matches `_checksum(rec)`.

```python
# tests/test_learning_ledger.py
"""Phase 0 self-learning ledger: checksummed append + read/join/quarantine."""
import json

from trading_algo.learning import ledger


def test_checksum_deterministic_and_sensitive():
    rec = {"decision_id": "d1", "book": "US", "features": {"vol_pctile": 0.4}}
    a = ledger._checksum(rec)
    b = ledger._checksum(dict(reversed(list(rec.items()))))  # key order must not matter
    assert a == b, "canonical (sort_keys) checksum must be order-independent"
    changed = {**rec, "book": "ASX"}
    assert ledger._checksum(changed) != a, "any field change must change the checksum"


def _read_lines(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_record_decision_writes_checksummed_line(tmp_path):
    d = str(tmp_path)
    ledger.record_decision(
        d, "full", decision_id="d1", ts="2026-07-24", book="US",
        champion_id="static-default", features={"vol_pctile": 0.4},
        regime={"trend_strength": 0.02}, action={"buys": ["AAPL"]},
        considered=[{"ticker": "AAPL", "score": 1.2}],
        rationale={"why": "top momentum"})
    lines = _read_lines(tmp_path / "decision_log_full.jsonl")
    assert len(lines) == 1
    line = lines[0]
    assert line["_v"] == 1
    assert line["rec"]["decision_id"] == "d1"
    assert line["rec"]["book"] == "US"
    assert line["_sha1"] == ledger._checksum(line["rec"]), "stored sha must match record"


def test_record_outcome_writes_checksummed_line(tmp_path):
    d = str(tmp_path)
    ledger.record_outcome(
        d, "full", decision_id="d1", exit_ts="2026-08-24", holding_bars=21,
        gross_return=0.031, cost=0.0008, carry=0.0, net_return=0.0302,
        label="win", attribution={"momentum": 0.031})
    lines = _read_lines(tmp_path / "outcome_log_full.jsonl")
    assert len(lines) == 1
    line = lines[0]
    assert line["_v"] == 1
    assert line["rec"]["net_return"] == 0.0302
    assert line["rec"]["holding_bars"] == 21
    assert line["_sha1"] == ledger._checksum(line["rec"])
```

Run: `pytest -q tests/test_learning_ledger.py`
Expected output: fails at import — `ModuleNotFoundError: No module named 'trading_algo.learning.ledger'` (RED).

- [ ] **Step 3: Implement the checksum + record core (GREEN).** Pure stdlib; append-only checksummed JSONL, one file per stream per account.

```python
# trading_algo/learning/ledger.py
"""Append-only, checksummed decision/outcome ledger for the self-learning loop.

Phase 0 is INERT: the champion stays the static config and nothing adapts live.
This module only *logs* every rebalance decision and its realised outcome so a
later learning layer (L1-L4) has an honest, tamper-evident record to learn from.

Each JSONL line is:  {"_v": 1, "_sha1": <hex>, "rec": {...}}
where "_sha1" is the SHA-1 of the canonical JSON of "rec" (json.dumps sort_keys).
On read (see read_decisions/read_outcomes) a line whose checksum does not match
its record — or whose decision_id was already seen — is skipped and copied
verbatim to quarantine_{account}.jsonl, so corruption/duplication can never
silently poison downstream learning. Pure stdlib.

Files: decision_log_{account}.jsonl, outcome_log_{account}.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os

LEDGER_SCHEMA_VERSION = 1


def _decision_path(ledger_dir: str, account: str) -> str:
    return os.path.join(ledger_dir, f"decision_log_{account}.jsonl")


def _outcome_path(ledger_dir: str, account: str) -> str:
    return os.path.join(ledger_dir, f"outcome_log_{account}.jsonl")


def _quarantine_path(ledger_dir: str, account: str) -> str:
    return os.path.join(ledger_dir, f"quarantine_{account}.jsonl")


def _checksum(rec: dict) -> str:
    """SHA-1 hex of the canonical JSON of `rec` (sort_keys=True) — order-stable."""
    payload = json.dumps(rec, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()


def _append_checksummed(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    line = {"_v": LEDGER_SCHEMA_VERSION, "_sha1": _checksum(rec), "rec": rec}
    with open(path, "a") as f:
        f.write(json.dumps(line) + "\n")


def record_decision(ledger_dir: str, account: str, *, decision_id, ts, book: str,
                    champion_id: str, features: dict, regime: dict, action: dict,
                    considered: list, rationale: dict) -> None:
    """Append one rebalance decision. INERT in Phase 0 — nothing acts on it."""
    rec = {
        "decision_id": str(decision_id),
        "ts": str(ts),
        "book": str(book),
        "champion_id": str(champion_id),
        "features": dict(features),
        "regime": dict(regime),
        "action": dict(action),
        "considered": list(considered),
        "rationale": dict(rationale),
    }
    _append_checksummed(_decision_path(ledger_dir, account), rec)


def record_outcome(ledger_dir: str, account: str, *, decision_id, exit_ts,
                   holding_bars, gross_return, cost, carry, net_return, label,
                   attribution: dict) -> None:
    """Append the realised outcome of a prior decision (joined by decision_id)."""
    rec = {
        "decision_id": str(decision_id),
        "exit_ts": str(exit_ts),
        "holding_bars": int(holding_bars),
        "gross_return": float(gross_return),
        "cost": float(cost),
        "carry": float(carry),
        "net_return": float(net_return),
        "label": label,
        "attribution": dict(attribution),
    }
    _append_checksummed(_outcome_path(ledger_dir, account), rec)
```

Run: `pytest -q tests/test_learning_ledger.py`
Expected output: `3 passed` (GREEN).

- [ ] **Step 4: Commit.**

```bash
git add trading_algo/learning/__init__.py trading_algo/learning/ledger.py tests/test_learning_ledger.py
git commit -m "learn(ledger): checksummed append-only decision/outcome ledger (Phase 0 inert)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Ledger read + inner join + quarantine of corrupt/duplicate lines

**Files:**
- Modify: `trading_algo/learning/ledger.py`
- Test: `tests/test_learning_ledger.py` (modify — add read/join/quarantine tests)

**Interfaces:**
- Produces: `ledger.read_decisions(ledger_dir, account) -> list[dict]`
- Produces: `ledger.read_outcomes(ledger_dir, account) -> list[dict]`
- Produces: `ledger.join(ledger_dir, account) -> list[dict]` (inner join on `decision_id`, flat `{**decision, **outcome}`)
- Behaviour: on read, sha mismatch OR duplicate `decision_id` → skip + append raw line to `quarantine_{account}.jsonl`
- Consumes: `ledger._checksum` (from Task 1)

Steps:

- [ ] **Step 1: Add failing read/join/quarantine tests (RED).** The quarantine test plants a tampered line (valid JSON, wrong `_sha1`) and a correctly-checksummed duplicate `decision_id`, then asserts read returns ONLY the first good record and both bad lines land verbatim in the quarantine file.

```python
# append to tests/test_learning_ledger.py

def test_read_roundtrips_valid_records(tmp_path):
    d = str(tmp_path)
    for i in range(3):
        ledger.record_decision(
            d, "full", decision_id=f"d{i}", ts="2026-07-24", book="US",
            champion_id="static-default", features={}, regime={},
            action={}, considered=[], rationale={})
    recs = ledger.read_decisions(d, "full")
    assert [r["decision_id"] for r in recs] == ["d0", "d1", "d2"]
    assert not (tmp_path / "quarantine_full.jsonl").exists(), "clean read -> no quarantine"


def test_join_inner_on_decision_id(tmp_path):
    d = str(tmp_path)
    ledger.record_decision(d, "full", decision_id="d1", ts="t", book="US",
                           champion_id="c", features={}, regime={},
                           action={"buys": ["AAPL"]}, considered=[], rationale={})
    ledger.record_decision(d, "full", decision_id="d2", ts="t", book="US",
                           champion_id="c", features={}, regime={},
                           action={"buys": ["MSFT"]}, considered=[], rationale={})
    # only d1 has an outcome -> inner join yields exactly one row
    ledger.record_outcome(d, "full", decision_id="d1", exit_ts="t2", holding_bars=21,
                          gross_return=0.03, cost=0.0, carry=0.0, net_return=0.03,
                          label="win", attribution={})
    rows = ledger.join(d, "full")
    assert len(rows) == 1
    row = rows[0]
    assert row["decision_id"] == "d1"
    assert row["action"] == {"buys": ["AAPL"]}   # from the decision side
    assert row["net_return"] == 0.03             # from the outcome side


def _write_raw(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def test_corrupt_and_duplicate_lines_are_quarantined_not_returned(tmp_path):
    d = str(tmp_path)
    path = str(tmp_path / "decision_log_full.jsonl")
    # 1) one genuinely valid record
    ledger.record_decision(d, "full", decision_id="ok1", ts="t", book="US",
                           champion_id="c", features={}, regime={},
                           action={}, considered=[], rationale={})
    # 2) a TAMPERED line: valid JSON but _sha1 does not match its rec
    tampered_rec = {"decision_id": "bad1", "book": "US"}
    _write_raw(path, {"_v": 1, "_sha1": "deadbeef", "rec": tampered_rec})
    # 3) a correctly-checksummed DUPLICATE of an already-seen decision_id
    dup_rec = {"decision_id": "ok1", "book": "ASX"}
    _write_raw(path, {"_v": 1, "_sha1": ledger._checksum(dup_rec), "rec": dup_rec})

    recs = ledger.read_decisions(d, "full")
    assert [r["decision_id"] for r in recs] == ["ok1"], "only the first valid id survives"
    assert recs[0]["book"] == "US", "the duplicate must not overwrite the original"

    q = tmp_path / "quarantine_full.jsonl"
    assert q.exists(), "corrupt/duplicate lines must be quarantined"
    with open(q) as f:
        quarantined = [json.loads(line) for line in f if line.strip()]
    ids = {qq["rec"]["decision_id"] for qq in quarantined}
    assert ids == {"bad1", "ok1"}, "both the tampered and the duplicate line are quarantined"
```

Run: `pytest -q tests/test_learning_ledger.py`
Expected output: fails — `AttributeError: module 'trading_algo.learning.ledger' has no attribute 'read_decisions'` (RED).

- [ ] **Step 2: Implement read + join + quarantine (GREEN).** A shared reader validates each line's checksum and first-seen `decision_id`; anything failing is copied verbatim to the quarantine file and excluded from the result.

```python
# append to trading_algo/learning/ledger.py

def _read_checksummed(path: str, quarantine_path: str) -> list[dict]:
    """Valid `rec`s in `path`; skip + quarantine corrupt or duplicate lines.

    A line is quarantined (its raw text appended to `quarantine_path`) when it is
    not parseable, its stored `_sha1` != `_checksum(rec)`, or its `decision_id`
    was already returned by an earlier (valid) line in this read.
    """
    if not os.path.exists(path):
        return []
    recs: list[dict] = []
    seen: set = set()
    quarantined: list[str] = []
    with open(path) as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                line = json.loads(stripped)
                rec = line["rec"]
                did = rec["decision_id"]
                good_sha = line.get("_sha1") == _checksum(rec)
            except (json.JSONDecodeError, KeyError, TypeError):
                quarantined.append(stripped)
                continue
            if not good_sha or did in seen:
                quarantined.append(stripped)
                continue
            seen.add(did)
            recs.append(rec)
    if quarantined:
        os.makedirs(os.path.dirname(os.path.abspath(quarantine_path)), exist_ok=True)
        with open(quarantine_path, "a") as q:
            for s in quarantined:
                q.write(s + "\n")
    return recs


def read_decisions(ledger_dir: str, account: str) -> list[dict]:
    """Valid decision records for `account`; corrupt/duplicate lines quarantined."""
    return _read_checksummed(_decision_path(ledger_dir, account),
                             _quarantine_path(ledger_dir, account))


def read_outcomes(ledger_dir: str, account: str) -> list[dict]:
    """Valid outcome records for `account`; corrupt/duplicate lines quarantined."""
    return _read_checksummed(_outcome_path(ledger_dir, account),
                             _quarantine_path(ledger_dir, account))


def join(ledger_dir: str, account: str) -> list[dict]:
    """Inner join decisions ⨝ outcomes on decision_id.

    One flat row `{**decision, **outcome}` per decision that has a matching
    outcome (both carry the same `decision_id`, so the merge never conflicts).
    Preserves decision order.
    """
    outcomes = {o["decision_id"]: o for o in read_outcomes(ledger_dir, account)}
    rows: list[dict] = []
    for dec in read_decisions(ledger_dir, account):
        out = outcomes.get(dec["decision_id"])
        if out is not None:
            rows.append({**dec, **out})
    return rows
```

Run: `pytest -q tests/test_learning_ledger.py`
Expected output: `6 passed` (GREEN).

- [ ] **Step 3: Commit.**

```bash
git add trading_algo/learning/ledger.py tests/test_learning_ledger.py
git commit -m "learn(ledger): read/join with checksum + duplicate-id quarantine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Causal regime feature tagger

**Files:**
- Create: `trading_algo/learning/regime.py`
- Test: `tests/test_learning_regime.py`

**Interfaces:**
- Produces: `regime.regime_features(prices: pd.DataFrame, index_px: pd.Series, asof, p) -> dict` with keys `{"vol_pctile","dispersion","trend_strength","avg_corr"}`, all `float`
- Consumes: `trading_algo.signals.realised_vol(prices, p)`; `StrategyParams.vol_lookback`, `StrategyParams.index_trend_ma`
- Guarantee: CAUSAL — uses only rows with index `<= asof`; expanding/rolling stats only, never a full-sample percentile

Steps:

- [ ] **Step 1: Write the no-lookahead tests (RED).** One test perturbs rows STRICTLY AFTER `asof` and asserts the feature dict is byte-identical (no lookahead); a companion test perturbs a row BEFORE `asof` and asserts the features DO change (proving the tagger actually reads the past, so the first test isn't trivially passing).

```python
# tests/test_learning_regime.py
"""Phase 0 regime tagger: continuous, causal features (no discrete buckets yet)."""
import numpy as np
import pandas as pd

from trading_algo.config import DEFAULT_PARAMS
from trading_algo.learning import regime


def _synth():
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(0)
    prices = pd.DataFrame(
        100 * np.cumprod(1 + 0.001 * rng.standard_normal((len(idx), 6)), axis=0),
        index=idx, columns=[f"S{i}" for i in range(6)])
    index_px = pd.Series(
        100 * np.cumprod(1 + 0.0008 * rng.standard_normal(len(idx))), index=idx)
    return prices, index_px, idx


def test_regime_features_keys_and_finite():
    prices, index_px, idx = _synth()
    feats = regime.regime_features(prices, index_px, idx[250], DEFAULT_PARAMS)
    assert set(feats) == {"vol_pctile", "dispersion", "trend_strength", "avg_corr"}
    assert all(isinstance(v, float) for v in feats.values())
    assert all(np.isfinite(v) for v in feats.values())
    assert 0.0 <= feats["vol_pctile"] <= 1.0, "expanding percentile is a fraction"


def test_regime_features_no_lookahead():
    prices, index_px, idx = _synth()
    asof = idx[250]
    base = regime.regime_features(prices, index_px, asof, DEFAULT_PARAMS)

    # Perturb EVERYTHING strictly after asof — an as-of feature must not move.
    future = prices.index > asof
    prices2 = prices.copy()
    prices2.loc[future] = prices2.loc[future] * 3.0 + 17.0
    index_px2 = index_px.copy()
    index_px2.loc[index_px2.index > asof] = index_px2.loc[index_px2.index > asof] * 0.1

    after = regime.regime_features(prices2, index_px2, asof, DEFAULT_PARAMS)
    assert base == after, "features must depend only on rows <= asof (no lookahead)"


def test_regime_features_actually_use_the_past():
    # Guards against the no-lookahead test passing because the function ignores data.
    prices, index_px, idx = _synth()
    asof = idx[250]
    base = regime.regime_features(prices, index_px, asof, DEFAULT_PARAMS)

    prices2 = prices.copy()
    prices2.iloc[240, 0] *= 1.5   # a bump BEFORE asof, inside the trailing window
    after = regime.regime_features(prices2, index_px, asof, DEFAULT_PARAMS)
    assert base != after, "past data inside the window must influence the features"
```

Run: `pytest -q tests/test_learning_regime.py`
Expected output: fails at import — `ModuleNotFoundError: No module named 'trading_algo.learning.regime'` (RED).

- [ ] **Step 2: Implement the causal regime tagger (GREEN).** Slice to `<= asof` FIRST so no future row is even present, then compute four continuous features with expanding/rolling stats only.

```python
# trading_algo/learning/regime.py
"""Causal regime features for the self-learning loop (Phase 0: LOG-ONLY).

`regime_features` summarises the market state *as of* a rebalance date using ONLY
data on or before that date. Every input is sliced to rows with index <= asof
before anything is computed, so a future price can never leak into an as-of
feature (no lookahead, invariant #1). Statistics are expanding/rolling — never a
full-sample percentile.

Phase 0 emits CONTINUOUS features only and logs them beside each decision; it does
NOT act on them. Discrete regime BUCKETS (e.g. risk-on / risk-off / high-dispersion)
are deliberately deferred to L1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..signals import realised_vol

_KEYS = ("vol_pctile", "dispersion", "trend_strength", "avg_corr")


def regime_features(prices: pd.DataFrame, index_px: pd.Series, asof, p) -> dict:
    """Continuous, causal regime features as of `asof` (rows <= asof only).

    Returns floats for: vol_pctile (expanding percentile of the cross-sectional
    average realised vol), dispersion (cross-sectional std of trailing returns),
    trend_strength (index distance above/below its trend MA), avg_corr (mean
    off-diagonal pairwise correlation over the trailing window). Missing inputs
    yield NaN for the affected feature rather than raising.
    """
    px = prices.loc[prices.index <= asof]
    idx = index_px.loc[index_px.index <= asof]
    if px.empty or idx.empty:
        return {k: float("nan") for k in _KEYS}

    # --- vol_pctile: expanding percentile of the cross-sectional average vol ---
    vol = realised_vol(px, p)                      # (T x N) annualised, causal
    avg_vol = vol.mean(axis=1).dropna()            # one number per date
    if len(avg_vol) >= 1:
        latest = avg_vol.iloc[-1]
        vol_pctile = float((avg_vol <= latest).mean())   # rank of latest vs its own past
    else:
        vol_pctile = float("nan")

    # --- dispersion: cross-sectional std of trailing total return at asof ---
    rets = px.pct_change(fill_method=None)
    window = rets.tail(p.vol_lookback)
    cum = (1.0 + window).prod() - 1.0
    cum = cum.replace([np.inf, -np.inf], np.nan).dropna()
    dispersion = float(cum.std(ddof=1)) if len(cum) > 1 else float("nan")

    # --- trend_strength: index vs its trend MA (rolling, causal) ---
    ma = idx.rolling(p.index_trend_ma).mean().iloc[-1]
    last_idx = idx.iloc[-1]
    if pd.notna(ma) and ma != 0:
        trend_strength = float(last_idx / ma - 1.0)
    else:
        trend_strength = float("nan")

    # --- avg_corr: mean off-diagonal pairwise correlation over trailing window ---
    win = rets.tail(p.vol_lookback).dropna(axis=1, how="all")
    if win.shape[1] >= 2 and len(win) >= 2:
        corr = win.corr().to_numpy()
        off = corr[~np.eye(corr.shape[0], dtype=bool)]
        off = off[np.isfinite(off)]
        avg_corr = float(off.mean()) if off.size else float("nan")
    else:
        avg_corr = float("nan")

    return {"vol_pctile": vol_pctile, "dispersion": dispersion,
            "trend_strength": trend_strength, "avg_corr": avg_corr}
```

Run: `pytest -q tests/test_learning_regime.py`
Expected output: `3 passed` (GREEN).

- [ ] **Step 3: Commit.**

```bash
git add trading_algo/learning/regime.py tests/test_learning_regime.py
git commit -m "learn(regime): causal continuous regime features (no-lookahead proven)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Gate v2 — cumulative trial count

**Files:**
- Create: `trading_algo/learning/gate.py`
- Note: `trading_algo/learning/__init__.py` already exists from Task 1 — do NOT re-create it.
- Test: `tests/test_learning_gate.py` (Create)

**Interfaces:**
- Consumes: `manifest.trial_count(ledger_path, kind=None, params_fingerprint_filter=None) -> int`, `manifest.append_run(ledger_path, m)`, `manifest.build_manifest(kind, *, params, regions, metrics, ...)`
- Produces: `cumulative_trials(manifest_ledger_path, *, kind, family=None, batch_configs=0) -> int` (= `manifest.trial_count(path, kind, family) + batch_configs`)

Steps:

- [ ] **Step 1: Write the failing test (red).** The import fails until `gate.py` exists, and the assertion pins the "ledger runs + this batch's configs" arithmetic and the `kind`/`family` filters.

```python
# tests/test_learning_gate.py
import numpy as np
import pytest

from trading_algo import manifest


def test_cumulative_trials_counts_ledger_plus_batch(tmp_path):
    from trading_algo.learning.gate import cumulative_trials

    ledger = str(tmp_path / "manifest.jsonl")
    for _ in range(3):
        manifest.append_run(ledger, manifest.build_manifest(
            "sweep", params={"top_n": 5}, regions=["US"], metrics={"sharpe": 1.0}))

    # 3 recorded sweep runs + 4 configs in the current batch = 7
    assert cumulative_trials(ledger, kind="sweep", batch_configs=4) == 7

    # kind filter isolates the relevant search
    manifest.append_run(ledger, manifest.build_manifest(
        "backtest", params={"top_n": 5}, regions=["US"], metrics={}))
    assert cumulative_trials(ledger, kind="sweep", batch_configs=0) == 3
    assert cumulative_trials(ledger, kind="backtest", batch_configs=0) == 1

    # family (params fingerprint) narrows further
    fp = manifest.params_fingerprint({"top_n": 5})
    assert cumulative_trials(ledger, kind="sweep", family=fp, batch_configs=0) == 3
    assert cumulative_trials(ledger, kind="sweep", family="deadbeef", batch_configs=0) == 0

    # missing ledger = zero recorded, only the batch counts
    assert cumulative_trials(str(tmp_path / "nope.jsonl"),
                             kind="sweep", batch_configs=2) == 2
```

Run: `pytest -q tests/test_learning_gate.py::test_cumulative_trials_counts_ledger_plus_batch -v`
Expected output: fails at collection/import — `ModuleNotFoundError: No module named 'trading_algo.learning.gate'`.

- [ ] **Step 2: Create `gate.py` (green).** The package `__init__.py` already exists from Task 1, so only `gate.py` is created here. Its module header imports everything Gate v2 will use across the following tasks (`config`/`validation`/`np` are used by `LockedHoldout`/`AlphaLedger`/`gate_v2` added in Tasks 5-7); `cumulative_trials` is the only function implemented now.

```python
# trading_algo/learning/gate.py
"""Gate v2 — the Phase-0 upgrade to the validation gate.

Three honesty upgrades over ``validation.overfitting_gate``, all INERT (they only
gate; they never adapt the live champion):

  * ``cumulative_trials`` — deflate against every trial ever recorded in the
    manifest ledger (plus the current batch), NOT just this batch's grid size.
    A continuous re-selection loop that keeps searching MUST pay for every look.
  * ``LockedHoldout`` — a write-once evaluation window; each candidate is scored
    on it exactly once (``HoldoutReuseError`` on reuse) so the loop can't quietly
    re-roll the holdout until something passes.
  * ``AlphaLedger`` — alpha-investing (LORD-style) thresholds: the DSR floor
    tightens as unsuccessful attempts spend the alpha wealth, and loosens only
    when a genuine discovery pays wealth back in.

``gate_v2`` composes the three. Phase 0 proves it rejects noise; no live wiring.
"""
from __future__ import annotations

import json
import os

import numpy as np

from trading_algo import config, manifest, validation


def cumulative_trials(manifest_ledger_path: str, *, kind: str,
                      family: str | None = None, batch_configs: int = 0) -> int:
    """Honest trial count for a Deflated Sharpe: every prior run of this `kind`
    (optionally narrowed to one params fingerprint via `family`) recorded in the
    append-only manifest ledger, PLUS the number of configs in the current batch.

    This is the whole Phase-0 point: a continuous loop deflates against the
    CUMULATIVE look count, not a single batch's grid size.
    """
    prior = manifest.trial_count(manifest_ledger_path, kind, family)
    return int(prior) + int(batch_configs)
```

Run: `pytest -q tests/test_learning_gate.py::test_cumulative_trials_counts_ledger_plus_batch -v`
Expected output: `1 passed`.

- [ ] **Step 3: Commit.**

Run: `git add trading_algo/learning/gate.py tests/test_learning_gate.py && git commit -m "$(printf 'feat(learning): gate_v2 cumulative_trials — deflate against every recorded look\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"`
Expected output: one commit created with 2 files changed.

---

## Task 5: Gate v2 — LockedHoldout (write-once evaluation window)

**Files:**
- Modify: `trading_algo/learning/gate.py`
- Test: `tests/test_learning_gate.py` (Modify)

**Interfaces:**
- Consumes: `validation.deflated_sharpe_ratio(returns, n_trials, sr_variance=None) -> float`
- Produces: `class HoldoutReuseError(Exception)`; `class LockedHoldout` with `__init__(path)`, classmethod `create(path, window: tuple) -> LockedHoldout`, `window() -> tuple`, `score_once(candidate_id: str, returns: np.ndarray, n_trials: int) -> dict` (persists `{candidate_id: {"dsr": ...}}`; raises `HoldoutReuseError` if `candidate_id` already scored)

Steps:

- [ ] **Step 1: Write the failing test (red).** Covers create/window, refusal of a second `create` on the same path, a first successful score, `HoldoutReuseError` on re-scoring the same candidate, a different candidate allowed, and persistence across reopen.

```python
# tests/test_learning_gate.py  (append)
def test_locked_holdout_create_window_and_score_once(tmp_path):
    from trading_algo.learning.gate import LockedHoldout, HoldoutReuseError

    path = str(tmp_path / "holdout.json")
    ho = LockedHoldout.create(path, ("2020-01-01", "2020-12-31"))
    assert ho.window() == ("2020-01-01", "2020-12-31")

    # write-once: a second create on the same path is refused
    with pytest.raises(HoldoutReuseError):
        LockedHoldout.create(path, ("2021-01-01", "2021-12-31"))

    rng = np.random.default_rng(0)
    rets = 0.01 + 0.001 * rng.standard_normal(300)  # strong positive signal
    res = ho.score_once("candA", rets, n_trials=5)
    assert "dsr" in res and 0.0 <= res["dsr"] <= 1.0

    # scoring the SAME candidate again is refused
    with pytest.raises(HoldoutReuseError):
        ho.score_once("candA", rets, n_trials=5)

    # a different candidate is allowed and the score persists on disk
    ho.score_once("candB", rets, n_trials=5)
    reopened = LockedHoldout(path)
    assert reopened.window() == ("2020-01-01", "2020-12-31")
    with pytest.raises(HoldoutReuseError):
        reopened.score_once("candA", rets, n_trials=5)
    reopened.score_once("candC", rets, n_trials=5)  # still room for new ids
```

Run: `pytest -q tests/test_learning_gate.py::test_locked_holdout_create_window_and_score_once -v`
Expected output: fails — `ImportError: cannot import name 'LockedHoldout'`.

- [ ] **Step 2: Implement `HoldoutReuseError` + `LockedHoldout` (green).** Append to `trading_algo/learning/gate.py`.

```python
# trading_algo/learning/gate.py  (append)
class HoldoutReuseError(Exception):
    """Raised when a candidate is scored on a locked holdout more than once (or a
    holdout is re-created over an existing lock). A sealed test set is only honest
    if it is used exactly once per candidate."""


class LockedHoldout:
    """A write-once out-of-sample evaluation window.

    The window is fixed at ``create`` time and every candidate may be scored on it
    exactly once. Re-scoring an already-seen ``candidate_id`` raises
    ``HoldoutReuseError`` — that is what stops a continuous loop from silently
    re-rolling the holdout until noise passes by luck. State (window + per-candidate
    DSR) persists to a JSON file so the lock survives across processes.
    """

    def __init__(self, path: str):
        self.path = path
        self._data = self._read(path)

    @staticmethod
    def _read(path: str) -> dict:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"window": None, "scores": {}}

    def _write(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)

    @classmethod
    def create(cls, path: str, window: tuple) -> "LockedHoldout":
        if os.path.exists(path):
            raise HoldoutReuseError(f"holdout already locked at {path}")
        inst = cls(path)
        inst._data = {"window": list(window), "scores": {}}
        inst._write()
        return inst

    def window(self) -> tuple | None:
        w = self._data.get("window")
        return tuple(w) if w is not None else None

    def score_once(self, candidate_id: str, returns: np.ndarray, n_trials: int) -> dict:
        if candidate_id in self._data["scores"]:
            raise HoldoutReuseError(
                f"candidate '{candidate_id}' already scored on this locked holdout")
        dsr = validation.deflated_sharpe_ratio(
            np.asarray(returns, dtype=float), int(n_trials))
        rec = {"dsr": round(float(dsr), 6), "n_trials": int(n_trials)}
        self._data["scores"][candidate_id] = rec
        self._write()
        return rec
```

Run: `pytest -q tests/test_learning_gate.py::test_locked_holdout_create_window_and_score_once -v`
Expected output: `1 passed`.

- [ ] **Step 3: Commit.**

Run: `git add trading_algo/learning/gate.py tests/test_learning_gate.py && git commit -m "$(printf 'feat(learning): LockedHoldout — write-once OOS window, HoldoutReuseError on reuse\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"`
Expected output: one commit created with 2 files changed.

---

## Task 6: Gate v2 — AlphaLedger (alpha-investing thresholds)

**Files:**
- Modify: `trading_algo/learning/gate.py`
- Test: `tests/test_learning_gate.py` (Modify)

**Interfaces:**
- Consumes: `config.PROMOTION_DSR_MIN` (= 0.95)
- Produces: `class AlphaLedger` with `__init__(path, alpha0=0.05, omega=0.05)`, `current_dsr_min() -> float` (= `1 - alpha_k`, clamped to `[PROMOTION_DSR_MIN, 0.999]`), `record(passed: bool) -> None`, property `alpha_wealth`; `alpha_k = wealth/2`, discovery adds `omega` back

Steps:

- [ ] **Step 1: Write the failing test (red).** Pins the exact wealth arithmetic, the monotonic tightening of `current_dsr_min` across failures, the loosening + payout on a discovery, the `[0.95, 1.0)` clamp, and persistence across reopen.

```python
# tests/test_learning_gate.py  (append)
def test_alpha_ledger_threshold_rises_after_failures(tmp_path):
    from trading_algo.learning.gate import AlphaLedger

    path = str(tmp_path / "alpha.json")
    al = AlphaLedger(path, alpha0=0.05, omega=0.05)

    t0 = al.current_dsr_min()
    assert al.alpha_wealth == pytest.approx(0.05)
    assert t0 == pytest.approx(0.975)          # 1 - 0.05/2

    al.record(False)
    t1 = al.current_dsr_min()
    assert al.alpha_wealth == pytest.approx(0.025)
    assert t1 == pytest.approx(0.9875)         # 1 - 0.025/2

    al.record(False)
    t2 = al.current_dsr_min()
    assert al.alpha_wealth == pytest.approx(0.0125)
    assert t2 == pytest.approx(0.99375)        # 1 - 0.0125/2

    # threshold tightens monotonically as wealth depletes, never >= 1.0, never < floor
    assert t0 < t1 < t2 < 1.0
    assert t2 >= 0.95

    # a discovery pays omega back into wealth and loosens the next threshold
    w_before = al.alpha_wealth
    al.record(True)
    assert al.alpha_wealth == pytest.approx(0.0125 - 0.00625 + 0.05)  # 0.05625
    assert al.alpha_wealth > w_before
    assert al.current_dsr_min() <= t2

    # depleting wealth cannot push the floor below the hard promotion minimum
    for _ in range(60):
        al.record(False)
    assert al.current_dsr_min() >= 0.95  # clamp holds even as wealth -> ~0

    # wealth persists across reopen
    al2 = AlphaLedger(path, alpha0=0.05, omega=0.05)
    assert al2.alpha_wealth == pytest.approx(al.alpha_wealth)
```

Run: `pytest -q tests/test_learning_gate.py::test_alpha_ledger_threshold_rises_after_failures -v`
Expected output: fails — `ImportError: cannot import name 'AlphaLedger'`.

- [ ] **Step 2: Implement `AlphaLedger` (green).** Append to `trading_algo/learning/gate.py`.

```python
# trading_algo/learning/gate.py  (append)
class AlphaLedger:
    """Simple alpha-investing (LORD-style) wealth ledger.

    The ledger starts with ``alpha0`` of alpha wealth. Each promotion attempt
    spends ``alpha_k = wealth / 2``; the current DSR floor is ``1 - alpha_k``, so a
    depleted wealth demands a STRICTER Deflated Sharpe. A genuine discovery (a
    ``record(True)``) pays ``omega`` back into the wealth, loosening the next
    threshold. The floor is clamped to ``[config.PROMOTION_DSR_MIN, 0.999]`` so it
    can never drop below the hard promotion minimum nor reach an unachievable 1.0.
    Wealth persists to a JSON file so the budget accrues across processes.
    """

    def __init__(self, path: str, alpha0: float = 0.05, omega: float = 0.05):
        self.path = path
        self.alpha0 = float(alpha0)
        self.omega = float(omega)
        self._wealth = self._load()

    def _load(self) -> float:
        if os.path.exists(self.path):
            with open(self.path) as f:
                return float(json.load(f).get("wealth", self.alpha0))
        return self.alpha0

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"wealth": self._wealth, "alpha0": self.alpha0,
                       "omega": self.omega}, f, indent=2)

    @property
    def alpha_wealth(self) -> float:
        return self._wealth

    def _alpha_k(self) -> float:
        return self._wealth / 2.0

    def current_dsr_min(self) -> float:
        floor = 1.0 - self._alpha_k()
        return float(min(max(floor, config.PROMOTION_DSR_MIN), 0.999))

    def record(self, passed: bool) -> None:
        self._wealth -= self._alpha_k()
        if passed:
            self._wealth += self.omega
        self._save()
```

Run: `pytest -q tests/test_learning_gate.py::test_alpha_ledger_threshold_rises_after_failures -v`
Expected output: `1 passed`.

- [ ] **Step 3: Commit.**

Run: `git add trading_algo/learning/gate.py tests/test_learning_gate.py && git commit -m "$(printf 'feat(learning): AlphaLedger — alpha-investing DSR floor tightens as wealth depletes\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"`
Expected output: one commit created with 2 files changed.

---

## Task 7: Gate v2 — compose cumulative_trials + overfitting_gate + holdout + alpha

**Files:**
- Modify: `trading_algo/learning/gate.py`
- Test: `tests/test_learning_gate.py` (Modify)

**Interfaces:**
- Consumes: `cumulative_trials(...)`, `validation.overfitting_gate(returns_matrix, n_trials, dsr_min, pbo_max, sr_variance) -> dict` (keys `dsr`, `pbo`, `best_config`, `passed`), `LockedHoldout.score_once(...)`, `AlphaLedger.current_dsr_min()` / `record(...)` / `alpha_wealth`
- Produces: `gate_v2(returns_matrix, *, manifest_ledger_path, kind, family, holdout, alpha, candidate_id, pbo_max=0.5, sr_variance=None) -> dict` with keys `passed`, `dsr`, `pbo`, `n_trials`, `dsr_min`, `alpha_wealth`, `reason`

Steps:

- [ ] **Step 1: Write the failing test (red).** A clean, strongly-ranked signal matrix must pass (DSR grid + holdout above the alpha-investing floor, PBO in-bounds, `n_trials` = seeded ledger runs + batch columns); a zero-mean noise matrix must fail. Separate ledger/holdout/alpha per sub-run keeps the two independent.

```python
# tests/test_learning_gate.py  (append)
def test_gate_v2_passes_clean_signal_and_rejects_noise(tmp_path):
    from trading_algo.learning.gate import gate_v2, LockedHoldout, AlphaLedger

    def _run(name, M):
        ledger = str(tmp_path / f"manifest_{name}.jsonl")
        for _ in range(2):  # 2 prior sweep looks already on the books
            manifest.append_run(ledger, manifest.build_manifest(
                "sweep", params={"top_n": 5}, regions=["US"], metrics={}))
        ho = LockedHoldout.create(str(tmp_path / f"ho_{name}.json"),
                                  ("2020-01-01", "2021-12-31"))
        al = AlphaLedger(str(tmp_path / f"alpha_{name}.json"))
        return gate_v2(M, manifest_ledger_path=ledger, kind="sweep", family=None,
                       holdout=ho, alpha=al, candidate_id=name)

    rng = np.random.default_rng(7)
    T, N = 520, 4
    # clean: every column drifts up, with a clear quality ordering (col 3 best)
    clean = rng.standard_normal((T, N)) * 0.003 + np.array([0.006, 0.008, 0.010, 0.012])
    res_clean = _run("clean", clean)
    assert res_clean["passed"] is True
    assert res_clean["dsr"] >= res_clean["dsr_min"]
    assert res_clean["pbo"] is not None and res_clean["pbo"] <= 0.5
    assert res_clean["n_trials"] == 2 + N          # 2 seeded looks + 4 batch configs
    assert res_clean["dsr_min"] == pytest.approx(0.975)  # fresh alpha wealth
    assert "PASS" in res_clean["reason"]

    # noise: zero-mean — no edge to find
    noise = rng.standard_normal((T, N)) * 0.01
    res_noise = _run("noise", noise)
    assert res_noise["passed"] is False
    assert res_noise["dsr"] < res_noise["dsr_min"]
    assert res_noise["n_trials"] == 2 + N
    assert "FAIL" in res_noise["reason"]
```

Run: `pytest -q tests/test_learning_gate.py::test_gate_v2_passes_clean_signal_and_rejects_noise -v`
Expected output: fails — `ImportError: cannot import name 'gate_v2'`.

- [ ] **Step 2: Implement `gate_v2` (green).** Append to `trading_algo/learning/gate.py`. Composition order matches the contract exactly: cumulative trials (deflation count) → alpha-investing floor → `overfitting_gate` → single write-once holdout score of the grid-best column → combined verdict → `alpha.record`.

```python
# trading_algo/learning/gate.py  (append)
def gate_v2(returns_matrix, *, manifest_ledger_path: str, kind: str,
            family: str | None, holdout: "LockedHoldout", alpha: "AlphaLedger",
            candidate_id: str, pbo_max: float = 0.5,
            sr_variance: float | None = None) -> dict:
    """Phase-0 gate: deflate against the CUMULATIVE look count, gate on DSR+PBO at
    an alpha-investing floor, then confirm the grid-best config on a write-once
    locked holdout. INERT — it only returns a verdict; it never mutates the
    champion. ``alpha.record`` is called so unsuccessful looks tighten the floor.
    """
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim == 1:
        M = M.reshape(-1, 1)

    n = cumulative_trials(manifest_ledger_path, kind=kind, family=family,
                          batch_configs=M.shape[1])
    dsr_min = alpha.current_dsr_min()
    g = validation.overfitting_gate(M, n, dsr_min, pbo_max, sr_variance)
    ho = holdout.score_once(candidate_id, M[:, g["best_config"]], n)

    passed = bool(g["passed"] and ho["dsr"] >= dsr_min)
    alpha.record(passed)

    verdict = "PASS" if passed else "FAIL"
    reason = (f"{verdict}: n_trials={n}, dsr={g['dsr']} (grid) / {ho['dsr']} "
              f"(holdout) vs dsr_min={round(dsr_min, 4)}, pbo={g['pbo']} "
              f"vs pbo_max={pbo_max}")
    return {
        "passed": passed,
        "dsr": g["dsr"],
        "pbo": g["pbo"],
        "n_trials": n,
        "dsr_min": dsr_min,
        "alpha_wealth": alpha.alpha_wealth,
        "reason": reason,
    }
```

Run: `pytest -q tests/test_learning_gate.py -v`
Expected output: `4 passed` (all Gate v2 tests green).

- [ ] **Step 3: Commit.**

Run: `git add trading_algo/learning/gate.py tests/test_learning_gate.py && git commit -m "$(printf 'feat(learning): gate_v2 — compose cumulative trials + DSR/PBO + locked holdout + alpha-investing\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"`
Expected output: one commit created with 2 files changed.

---

## Task 8: Noise-falsification test — the gate must reject pure-noise candidates (permanent CI)

**Files:**
- Create: `tests/test_learning_noise_falsification.py`

**Interfaces:**
- Consumes: `trading_algo.learning.gate.gate_v2(returns_matrix, *, manifest_ledger_path, kind, family, holdout: LockedHoldout, alpha: AlphaLedger, candidate_id, pbo_max=0.5, sr_variance=None) -> dict` (keys `passed, dsr, pbo, n_trials, dsr_min, alpha_wealth, reason`)
- Consumes: `trading_algo.learning.gate.AlphaLedger(path, alpha0=0.05, omega=0.05)` with property `alpha_wealth` and `current_dsr_min()`
- Consumes: `trading_algo.learning.gate.LockedHoldout.create(path, window: tuple) -> LockedHoldout`
- Consumes: `trading_algo.config.PROMOTION_DSR_MIN = 0.95`

Steps:

- [ ] **Step 1: Write the falsification test (deterministic, ~200 no-edge matrices).** This is the load-bearing safety proof for the whole self-learning premise: if a continuous re-selection loop can promote random strategies, everything downstream is unsafe. Uses a fixed `np.random.default_rng` seed (never nondeterministic), one shared `AlphaLedger`, a fresh `LockedHoldout` per candidate, and asserts promotions stay within a tiny stated FDR bound (0).

```python
# tests/test_learning_noise_falsification.py
"""Phase 0 falsification: the upgraded gate must reject pure-noise candidates.

A continuous re-selection loop is only safe if the validation gate refuses to
promote strategies that have no edge. We fire ~200 zero-mean Gaussian return
matrices (each column a candidate "strategy") through gate_v2, sharing one
alpha-investing ledger and giving each candidate its own locked holdout, then
assert essentially nothing is promoted. The seed is fixed so this is a permanent,
deterministic CI check — never nondeterministic.
"""
import numpy as np
import pytest

from trading_algo import config as cfg
from trading_algo.learning.gate import AlphaLedger, LockedHoldout, gate_v2

N_CANDIDATES = 200
T, N = 252, 8          # 1yr of daily obs, 8-config batch grid per candidate
FDR_BOUND = 0          # pure noise + fixed seed -> exactly zero false discoveries


def test_pure_noise_is_never_promoted(tmp_path):
    rng = np.random.default_rng(12345)
    alpha = AlphaLedger(str(tmp_path / "alpha.json"))
    promotions = 0
    dsr_mins = []
    for i in range(N_CANDIDATES):
        # zero-mean Gaussian -> no edge by construction (Sharpe ~ 0)
        matrix = rng.standard_normal((T, N))
        holdout = LockedHoldout.create(str(tmp_path / f"ho_{i}.json"), (0, T))
        res = gate_v2(
            matrix,
            manifest_ledger_path=str(tmp_path / "no_such_ledger.jsonl"),
            kind="sweep", family=None,
            holdout=holdout, alpha=alpha,
            candidate_id=f"cand_{i}",
        )
        dsr_mins.append(res["dsr_min"])
        if res["passed"]:
            promotions += 1

    # (1) the headline claim: noise does not get promoted
    assert promotions <= FDR_BOUND, f"{promotions} pure-noise candidates promoted"
    # (2) the alpha floor never loosens BELOW the promotion DSR minimum
    assert min(dsr_mins) >= cfg.PROMOTION_DSR_MIN
    # (3) wealth was spent on 200 non-discoveries and never replenished
    assert alpha.alpha_wealth < 0.05


def test_falsification_is_deterministic(tmp_path):
    """Same seed -> identical pass/fail sequence, so the CI gate can't flap."""
    def run(dir_):
        rng = np.random.default_rng(999)
        alpha = AlphaLedger(str(dir_ / "alpha.json"))
        out = []
        for i in range(25):
            m = rng.standard_normal((T, N))
            ho = LockedHoldout.create(str(dir_ / f"ho_{i}.json"), (0, T))
            out.append(gate_v2(m, manifest_ledger_path=str(dir_ / "led.jsonl"),
                               kind="sweep", family=None, holdout=ho, alpha=alpha,
                               candidate_id=f"c_{i}")["passed"])
        return out

    a = run(tmp_path / "a")
    b = run(tmp_path / "b")
    assert a == b
    assert not any(a)   # still zero promotions on this seed too
```

Run: `pytest -q tests/test_learning_noise_falsification.py -v`
Expected output: `2 passed` — `test_pure_noise_is_never_promoted PASSED`, `test_falsification_is_deterministic PASSED`.

- [ ] **Step 2: Commit.**

```bash
cd /Users/matthewfanning/Trading-Algo
git add tests/test_learning_noise_falsification.py
git commit -m "test(learning): permanent noise-falsification gate — 200 no-edge matrices, 0 promotions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Wire cumulative manifest trial count into `purged_cv_report`

**Files:**
- Modify: `trading_algo/walkforward.py`
- Test: `tests/test_walkforward_equity.py`

**Interfaces:**
- Consumes: `trading_algo.learning.gate.cumulative_trials(manifest_ledger_path, *, kind, family=None, batch_configs=0) -> int` (= `manifest.trial_count(path, kind, family) + batch_configs`)
- Consumes: `trading_algo.manifest.build_manifest(kind, *, params, regions, metrics, ...) -> dict`, `manifest.append_run(ledger_path, m) -> None`
- Produces: `walkforward.purged_cv_report(prices, index_px, region, top_ns, lookbacks, *, n_folds=6, embargo=21, membership=None, n_trials=None, manifest_ledger_path: str|None=None, kind: str="sweep", dsr_min=0.95, pbo_max=0.5) -> dict`

Steps:

- [ ] **Step 1 (RED): Add the two-branch test — default grid-size vs cumulative manifest count.** Add a `manifest` import and two tests to the existing walk-forward suite (which already defines the `synth_us` fixture and the `SMALL_TOP_NS` / `SMALL_LOOKBACKS` grids).

```python
# --- add to the imports block at the top of tests/test_walkforward_equity.py ---
from trading_algo import manifest


# --- append these two tests ---
def test_purged_cv_default_n_trials_is_grid_size(synth_us):
    """Backward-compatible default: n_trials is the per-batch grid size."""
    region, prices, index_px = synth_us
    rep = walkforward.purged_cv_report(prices, index_px, region,
                                       SMALL_TOP_NS, SMALL_LOOKBACKS)
    assert rep["n_trials"] == len(SMALL_TOP_NS) * len(SMALL_LOOKBACKS)


def test_purged_cv_deflates_against_cumulative_manifest_trials(synth_us, tmp_path):
    """The Phase-0 fix: a continuous loop must deflate against the CUMULATIVE
    trial count (every prior sweep in the ledger), not just this batch's grid."""
    region, prices, index_px = synth_us
    ledger = str(tmp_path / "experiments.jsonl")
    for _ in range(5):                       # 5 prior sweep trials already searched
        m = manifest.build_manifest("sweep", params=region.params,
                                    regions=[region.key], metrics={})
        manifest.append_run(ledger, m)
    grid = len(SMALL_TOP_NS) * len(SMALL_LOOKBACKS)

    rep = walkforward.purged_cv_report(prices, index_px, region,
                                       SMALL_TOP_NS, SMALL_LOOKBACKS,
                                       manifest_ledger_path=ledger, kind="sweep")
    # cumulative = 5 prior sweeps + this batch's grid size
    assert rep["n_trials"] == 5 + grid
    # prior sweeps of a DIFFERENT kind must NOT inflate the sweep count
    manifest.append_run(ledger, manifest.build_manifest(
        "backtest", params=region.params, regions=[region.key], metrics={}))
    rep2 = walkforward.purged_cv_report(prices, index_px, region,
                                        SMALL_TOP_NS, SMALL_LOOKBACKS,
                                        manifest_ledger_path=ledger, kind="sweep")
    assert rep2["n_trials"] == 5 + grid      # unchanged: backtest kind is excluded
```

Run: `pytest -q tests/test_walkforward_equity.py::test_purged_cv_deflates_against_cumulative_manifest_trials -v`
Expected output: FAIL — `TypeError: purged_cv_report() got an unexpected keyword argument 'manifest_ledger_path'`.

- [ ] **Step 2 (GREEN): Add the `manifest_ledger_path` / `kind` params and the cumulative branch.** Replace the `purged_cv_report` definition (currently at `trading_algo/walkforward.py:91`) with the version below so the manifest branch overrides the per-batch default while the old default stays fully backward-compatible. (`overfitting_gate` returns the `dsr`/`pbo`/`best_config`/`passed` dict; this call keeps its existing keyword usage.)

```python
# In trading_algo/walkforward.py, replace the purged_cv_report definition
# (currently starting at line 91) with:

def purged_cv_report(prices: pd.DataFrame, index_px: pd.Series, region: Region,
                     top_ns, lookbacks, *, n_folds: int = DEFAULT_N_FOLDS,
                     embargo: int = DEFAULT_EMBARGO, membership=None,
                     n_trials: int | None = None,
                     manifest_ledger_path: str | None = None,
                     kind: str = "sweep",
                     dsr_min: float = 0.95, pbo_max: float = 0.5) -> dict:
    """Run the purged/embargoed CV over the grid and apply the F2 overfitting gate.

    Trial count for the Deflated Sharpe:
      * default (`manifest_ledger_path=None`, `n_trials=None`): the per-batch grid
        size — the honest count of configurations searched in THIS call.
      * an explicit `n_trials` overrides that default.
      * `manifest_ledger_path` (Phase-0 self-learning loop): deflate against the
        CUMULATIVE trial count — every prior run of `kind` recorded in the ledger
        PLUS this batch's grid — so a continuous re-selection loop can't launder
        away its multiple-testing burden by resetting the count each batch.
    """
    cv = cv_returns_matrix(prices, index_px, region, top_ns, lookbacks,
                           n_folds=n_folds, embargo=embargo, membership=membership)
    if cv is None:
        return {"verdict": "no result", "n_configs": 0}
    if manifest_ledger_path is not None:
        from .learning.gate import cumulative_trials
        n_trials = cumulative_trials(manifest_ledger_path, kind=kind,
                                     batch_configs=cv["n_configs"])
    elif n_trials is None:
        n_trials = cv["n_configs"]
    gate = validation.overfitting_gate(cv["matrix"], n_trials=n_trials,
                                       dsr_min=dsr_min, pbo_max=pbo_max)
    gate.update({"n_obs": cv["n_obs"], "n_folds": cv["n_folds"],
                 "embargo": cv["embargo"], "grid_size": cv["n_configs"]})
    return gate
```

Run: `pytest -q tests/test_walkforward_equity.py -v`
Expected output: all pass, including `test_purged_cv_default_n_trials_is_grid_size PASSED`, `test_purged_cv_deflates_against_cumulative_manifest_trials PASSED`, and the pre-existing `test_purged_cv_report_runs_the_gate PASSED` (default branch unchanged).

- [ ] **Step 3: Commit.**

```bash
cd /Users/matthewfanning/Trading-Algo
git add trading_algo/walkforward.py tests/test_walkforward_equity.py
git commit -m "feat(walkforward): deflate purged CV against CUMULATIVE manifest trials

Optional manifest_ledger_path/kind route n_trials through
learning.gate.cumulative_trials so a continuous loop deflates against every
prior sweep, not just the per-batch grid. Default path unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: INERT ledger hook in `paper_trade.run_daily` + `LEARNING_LEDGER_ENABLED` config flag

**Files:**
- Modify: `trading_algo/config.py`
- Modify: `trading_algo/paper_trade.py`
- Test: `tests/test_learning_replay.py`

**Interfaces:**
- Consumes: `trading_algo.learning.ledger.record_decision(ledger_dir, account, *, decision_id, ts, book, champion_id, features: dict, regime: dict, action: dict, considered: list, rationale: dict) -> None`
- Consumes: `trading_algo.learning.regime.regime_features(prices, index_px, asof, p) -> dict`
- Consumes: `trading_algo.signals.momentum_score(prices, p)`, `realised_vol(prices, p)`, `index_risk_on(index_px, p)` (already imported in `paper_trade.py` as `sig`)
- Consumes: `trading_algo.manifest.params_fingerprint(params) -> str`
- Produces: `trading_algo.config.LEARNING_LEDGER_ENABLED: bool` (default `True`); a `record_decision` call inside `run_daily` writing `<STATE_DIR>/learning/decision_log_{account}.jsonl`

Steps:

- [ ] **Step 1: Add the config flag (INERT — logging only).** Append a new section after `VALIDATE_STATE_FILES` (currently `trading_algo/config.py:195`).

```python
# ---------------------------------------------------------------------------
# Self-learning trade loop — Phase 0 (INERT)
# ---------------------------------------------------------------------------
# Phase 0 ships the self-learning scaffolding switched OFF as an INFLUENCE: the
# champion is ALWAYS the static config above and nothing adapts live. This flag
# only turns on append-only LOGGING of each champion decision + causal features
# to trading_algo/learning/ledger.py, so a later learning layer (L1-L4) has an
# audited, byte-reproducible record to learn from. It NEVER changes sizing —
# weights still route solely through strategy.compute_targets (invariant #3).
LEARNING_LEDGER_ENABLED = True
```

- [ ] **Step 2: Add the imports and the `_record_learning_decision` helper + call site in `paper_trade.py`.** `paper_trade.py` already imports `os`, `pandas as pd`, `config as cfg`, and `signals as sig`, and defines the module global `STATE_DIR`. First add these imports after `from .regions import REGIONS, get_region` (line 42):

```python
from . import manifest
from .learning import ledger as learning_ledger
from .learning import regime as learning_regime
```

Then add the helper immediately above `def run_daily(` (currently line 449):

```python
def _record_learning_decision(account: str, region, params, prices: pd.DataFrame,
                              index_px: pd.Series, targets: pd.Series,
                              status: str, today: str) -> None:
    """Phase 0 (INERT): append the champion's decision + CAUSAL features to the
    learning ledger. Purely observational — the champion is the static config and
    the weights here already came from strategy.compute_targets (invariant #3), so
    this never influences sizing. All values are coerced to finite python floats so
    each JSONL line is byte-reproducible (the replay-determinism guarantee)."""
    if not cfg.LEARNING_LEDGER_ENABLED:
        return

    def _finite(x):
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return None
        return xf if xf == xf and xf not in (float("inf"), float("-inf")) else None

    ledger_dir = os.path.join(STATE_DIR, "learning")
    asof = prices.index[-1]
    mom_last = sig.momentum_score(prices, params).iloc[-1]
    vol_last = sig.realised_vol(prices, params).iloc[-1]
    risk_on = bool(sig.index_risk_on(index_px, params).iloc[-1])
    # Phase 0 champion is the STATIC default config; identify it by its params hash.
    champion_id = f"static:{manifest.params_fingerprint(params)}"

    features = {
        "mom_mean": _finite(mom_last.mean()),
        "vol_mean": _finite(vol_last.mean()),
        "n_targets": int(len(targets)),
        "index_risk_on": risk_on,
    }
    # regime_features returns CONTINUOUS causal stats; coerce to finite floats for
    # JSON reproducibility (discrete regime BUCKETS are deferred to L1).
    regime = {k: _finite(v)
              for k, v in learning_regime.regime_features(
                  prices, index_px, asof, params).items()}
    considered = [{"ticker": str(t), "mom": _finite(v)}
                  for t, v in mom_last.dropna().nlargest(10).items()]
    action = {"kind": "rebalance", "status": status,
              "targets": {str(t): _finite(w) for t, w in targets.items()}}
    rationale = {"status": status, "index_risk_on": risk_on,
                 "champion_id": champion_id}

    learning_ledger.record_decision(
        ledger_dir, account,
        decision_id=f"{account}:{region.key}:{today}",
        ts=today, book=region.key, champion_id=champion_id,
        features=features, regime=regime, action=action,
        considered=considered, rationale=rationale)
```

Then wire the call inside `run_daily`'s rebalance branch, right after the existing `sleeve["last_rebalance_date"] = today` (currently line 511) — replace that line with:

```python
                sleeve["last_rebalance_date"] = today
                # Phase 0 (INERT): log the champion's decision for later learning.
                _record_learning_decision(account, region, params, prices,
                                          index_px, targets, status, today)
```

- [ ] **Step 3: Add a smoke test proving the hook writes a well-formed decision log.** Create `tests/test_learning_replay.py` (the byte-identical determinism test is added in Task 11).

```python
# tests/test_learning_replay.py
"""Phase 0: the INERT decision-ledger hook in paper_trade.run_daily."""
from trading_algo import config as cfg
from trading_algo import paper_trade as pt
from trading_algo.learning import ledger as learning_ledger


def test_hook_writes_a_decision_record(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    assert cfg.LEARNING_LEDGER_ENABLED
    pt.init_account("hook", capital=300_000, synthetic=True)
    pt.run_daily("hook", synthetic=True)

    ledger_dir = str(tmp_path / "learning")
    decisions = learning_ledger.read_decisions(ledger_dir, "hook")
    assert decisions, "the rebalance branch should have logged >=1 decision"
    d = decisions[0]
    # champion is the STATIC default config (never mutated in Phase 0)
    assert d["champion_id"].startswith("static:")
    assert d["ts"] == d["decision_id"].split(":")[-1]        # ts is the data date
    assert set(d["regime"]) == {"vol_pctile", "dispersion",
                                "trend_strength", "avg_corr"}
    assert "targets" in d["action"] and "mom_mean" in d["features"]


def test_hook_is_inert_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "LEARNING_LEDGER_ENABLED", False)
    pt.init_account("off", capital=300_000, synthetic=True)
    pt.run_daily("off", synthetic=True)
    assert learning_ledger.read_decisions(str(tmp_path / "learning"), "off") == []
```

Run: `pytest -q tests/test_learning_replay.py tests/test_paper_trade.py -v`
Expected output: all pass — `test_hook_writes_a_decision_record PASSED`, `test_hook_is_inert_when_flag_off PASSED`, and the existing `test_paper_trade.py` suite still green (no sizing regression).

- [ ] **Step 4: Commit.**

```bash
cd /Users/matthewfanning/Trading-Algo
git add trading_algo/config.py trading_algo/paper_trade.py tests/test_learning_replay.py
git commit -m "feat(learning): INERT decision-ledger hook in run_daily + LEARNING_LEDGER_ENABLED

Logs the static champion's decision + causal features per rebalance; never
influences sizing (invariant #3). Guarded by config flag, default on.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Replay-determinism test — two identical synthetic passes write byte-identical decision logs

**Files:**
- Test: `tests/test_learning_replay.py` (Modify — add to the file created in Task 10)

**Interfaces:**
- Consumes: `trading_algo.paper_trade.run_daily(account, synthetic)` with the `_record_learning_decision` hook; module global `paper_trade.STATE_DIR`
- Consumes: the on-disk artefact `<STATE_DIR>/learning/decision_log_{account}.jsonl` (checksum-per-line JSONL)

Steps:

- [ ] **Step 1: Add the byte-identical determinism test.** Two fresh state dirs, same account name and same seeded synthetic data, must produce byte-identical decision logs — proving the ledger has no wall-clock or ordering nondeterminism (backtest==paper reproducibility), so a later learning layer replays exactly what happened. Append to `tests/test_learning_replay.py`.

```python
# tests/test_learning_replay.py  (append)
import hashlib


def _one_synthetic_pass(state_dir, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(state_dir))
    pt.init_account("replay", capital=300_000, synthetic=True)
    pt.run_daily("replay", synthetic=True)
    return state_dir / "learning" / "decision_log_replay.jsonl"


def test_two_synthetic_passes_are_byte_identical(tmp_path, monkeypatch):
    """Determinism proof: identical inputs -> byte-identical decision log. The ts
    is the DATA date (not wall clock) and decision_id is data-derived, so the
    checksum-per-line JSONL reproduces exactly across independent runs."""
    assert cfg.LEARNING_LEDGER_ENABLED
    log_a = _one_synthetic_pass(tmp_path / "a", monkeypatch)
    log_b = _one_synthetic_pass(tmp_path / "b", monkeypatch)

    assert log_a.exists() and log_b.exists()
    a, b = log_a.read_bytes(), log_b.read_bytes()
    assert len(a) > 0, "the pass must have logged at least one decision"
    # byte-for-byte, and via checksum (proves the embedded per-line _sha1 matches)
    assert a == b
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_decision_ids_are_stable_across_passes(tmp_path, monkeypatch):
    """The join key must be reproducible so outcomes attach to the same decision."""
    log_a = _one_synthetic_pass(tmp_path / "a", monkeypatch)
    decisions_a = learning_ledger.read_decisions(str(tmp_path / "a" / "learning"),
                                                 "replay")
    log_b = _one_synthetic_pass(tmp_path / "b", monkeypatch)
    decisions_b = learning_ledger.read_decisions(str(tmp_path / "b" / "learning"),
                                                 "replay")
    assert log_a.exists() and log_b.exists()
    assert [d["decision_id"] for d in decisions_a] == \
           [d["decision_id"] for d in decisions_b]
    assert decisions_a == decisions_b
```

Run: `pytest -q tests/test_learning_replay.py -v`
Expected output: all pass — `test_two_synthetic_passes_are_byte_identical PASSED`, `test_decision_ids_are_stable_across_passes PASSED` (plus the two hook tests from Task 10).

- [ ] **Step 2: Full-suite sanity — the Phase-0 learning slice is green end-to-end.**

Run: `pytest -q tests/test_learning_replay.py tests/test_learning_noise_falsification.py tests/test_walkforward_equity.py`
Expected output: `passed` with no failures (falsification, cumulative-trials wiring, hook, and replay determinism all green).

- [ ] **Step 3: Commit.**

```bash
cd /Users/matthewfanning/Trading-Algo
git add tests/test_learning_replay.py
git commit -m "test(learning): replay-determinism — two synthetic passes write byte-identical decision logs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
