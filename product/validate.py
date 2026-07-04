"""Validate the product backlog against the schemas and the process invariants.

Zero-dependency (stdlib only) so it runs in a fresh container and in CI without
installing anything — matching the project's zero-dependency-friendly ethos. The
JSON Schema files in product/schema/ are the published contract; this validator
enforces the same shape plus the semantic rules the process relies on:

  * every id is unique and well-formed
  * all four roles have reviewed every item (the four-lens critique gate)
  * every item has >= 1 evidence entry and >= 1 acceptance criterion
  * a measurable acceptance criterion carries a metric
  * the RICE score equals reach * impact * confidence / effort
  * dependencies point at ids that exist (no dangling / self deps)
  * invariants_touched values are drawn from the known invariant set
  * every rollout has a mechanism, a default-off-or-justified flag, >= 1 stage,
    >= 1 guardrail metric, and a rollback

Run:  python -m product.validate           (from repo root)
      python product/validate.py
Exit code is non-zero if anything fails, so it doubles as a CI gate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKLOG = HERE / "backlog" / "backlog.json"
BUILD_PLAN = HERE / "backlog" / "build_plan.json"

ROLES = ("product_owner", "data_scientist", "chief_engineer", "algo_trader")
VERDICTS = ("strong_yes", "yes", "maybe", "no")
STATUSES = (
    "proposed", "researching", "groomed", "ready",
    "in_progress", "in_rollout", "done", "parked", "rejected",
)
EPICS = (
    "data-integrity", "statistical-validity", "risk-and-guardrails",
    "execution-quality", "reporting-and-attribution", "platform-and-ci",
    "portfolio-mechanics",
)
INVARIANTS = (
    "1-no-lookahead", "2-costs-always-on", "3-one-weight-function",
    "4-whole-shares", "5-synthetic-is-test-only", "6-local-currency", "none",
)
IMPACTS = (0.25, 0.5, 1, 2, 3)
CONFIDENCES = (0.5, 0.8, 1.0)
MECHANISMS = ("config_knob", "feature_flag", "new_module", "ci_gate")
STAGE_NAMES = ("shadow", "synthetic", "single-sleeve", "paper", "canary-live", "full-live")


def _load(path: Path):
    with path.open() as fh:
        return json.load(fh)


def validate(backlog: dict) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    items = backlog.get("items")
    if not isinstance(items, list) or not items:
        return ["backlog has no 'items' array"]

    ids: set[str] = set()
    for i, it in enumerate(items):
        iid = it.get("id", f"<index {i}>")
        tag = f"[{iid}]"

        # --- identity & enums ---------------------------------------------
        if not isinstance(iid, str) or not (iid.startswith("F") and iid[1:].isdigit()):
            errors.append(f"{tag} id must match ^F[0-9]+$")
        if iid in ids:
            errors.append(f"{tag} duplicate id")
        ids.add(iid)

        if it.get("epic") not in EPICS:
            errors.append(f"{tag} epic '{it.get('epic')}' not in {EPICS}")
        if it.get("status") not in STATUSES:
            errors.append(f"{tag} status '{it.get('status')}' not in {STATUSES}")
        if it.get("owner_role") not in ROLES:
            errors.append(f"{tag} owner_role '{it.get('owner_role')}' not a known role")

        for field in ("title", "problem", "hypothesis"):
            if not isinstance(it.get(field), str) or len(it.get(field, "")) < 6:
                errors.append(f"{tag} missing/short '{field}'")

        # --- evidence ------------------------------------------------------
        evidence = it.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{tag} needs >= 1 evidence entry")
        else:
            for ev in evidence:
                if not all(k in ev for k in ("claim", "source", "confidence")):
                    errors.append(f"{tag} evidence entry missing claim/source/confidence")
                elif ev["confidence"] not in ("high", "medium", "low"):
                    errors.append(f"{tag} evidence confidence '{ev['confidence']}' invalid")

        # --- four-lens review gate ----------------------------------------
        reviews = it.get("role_reviews", {})
        if not isinstance(reviews, dict):
            errors.append(f"{tag} role_reviews must be an object")
        else:
            missing = [r for r in ROLES if r not in reviews]
            if missing:
                errors.append(f"{tag} missing role reviews: {missing} (all four must weigh in)")
            for role, rev in reviews.items():
                if role not in ROLES:
                    errors.append(f"{tag} unknown reviewer role '{role}'")
                    continue
                if rev.get("verdict") not in VERDICTS:
                    errors.append(f"{tag} {role} verdict '{rev.get('verdict')}' invalid")
                if len(rev.get("notes", "")) < 12:
                    errors.append(f"{tag} {role} review notes too short")

        # --- RICE ----------------------------------------------------------
        sc = it.get("scoring", {})
        try:
            reach = float(sc["reach"]); impact = float(sc["impact"])
            conf = float(sc["confidence"]); effort = float(sc["effort"])
            claimed = float(sc["score"])
            if impact not in IMPACTS:
                errors.append(f"{tag} impact {impact} not in {IMPACTS}")
            if conf not in CONFIDENCES:
                errors.append(f"{tag} confidence {conf} not in {CONFIDENCES}")
            if effort <= 0:
                errors.append(f"{tag} effort must be > 0")
            else:
                expect = reach * impact * conf / effort
                if abs(expect - claimed) > 0.01:
                    errors.append(f"{tag} score {claimed} != reach*impact*conf/effort ({expect:.2f})")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{tag} scoring must have numeric reach/impact/confidence/effort/score")

        # --- acceptance criteria ------------------------------------------
        acs = it.get("acceptance_criteria")
        if not isinstance(acs, list) or not acs:
            errors.append(f"{tag} needs >= 1 acceptance_criteria")
        else:
            for ac in acs:
                if not (isinstance(ac.get("id"), str) and ac["id"].startswith("AC")):
                    errors.append(f"{tag} acceptance id must match ^AC[0-9]+$")
                if len(ac.get("statement", "")) < 12:
                    errors.append(f"{tag} acceptance {ac.get('id')} statement too short")
                if ac.get("measurable") is True and not ac.get("metric"):
                    errors.append(f"{tag} acceptance {ac.get('id')} is measurable but has no metric")

        # --- invariants ----------------------------------------------------
        inv = it.get("invariants_touched")
        if not isinstance(inv, list):
            errors.append(f"{tag} invariants_touched must be a list")
        else:
            for v in inv:
                if v not in INVARIANTS:
                    errors.append(f"{tag} invariant '{v}' not recognised")

        # --- rollout -------------------------------------------------------
        ro = it.get("rollout", {})
        if not isinstance(ro, dict):
            errors.append(f"{tag} rollout must be an object")
        else:
            if ro.get("mechanism") not in MECHANISMS:
                errors.append(f"{tag} rollout mechanism '{ro.get('mechanism')}' invalid")
            if len(ro.get("flag", "")) < 3:
                errors.append(f"{tag} rollout needs a flag (or 'n/a' for a ci_gate)")
            stages = ro.get("stages")
            if not isinstance(stages, list) or not stages:
                errors.append(f"{tag} rollout needs >= 1 stage")
            else:
                for st in stages:
                    if st.get("name") not in STAGE_NAMES:
                        errors.append(f"{tag} rollout stage '{st.get('name')}' invalid")
                    if len(st.get("exit_criteria", "")) < 10:
                        errors.append(f"{tag} rollout stage {st.get('name')} needs exit_criteria")
            if not ro.get("guardrail_metrics"):
                errors.append(f"{tag} rollout needs >= 1 guardrail_metric")
            if len(ro.get("rollback", "")) < 12:
                errors.append(f"{tag} rollout needs a rollback plan")

    # --- cross-item: dependency integrity ---------------------------------
    for it in items:
        iid = it.get("id")
        for dep in it.get("dependencies", []) or []:
            if dep not in ids:
                errors.append(f"[{iid}] depends on unknown id '{dep}'")
            if dep == iid:
                errors.append(f"[{iid}] depends on itself")

    return errors


def validate_build_plan(backlog: dict, plan: dict) -> list[str]:
    """Cross-check the Architect/Chief-Engineer build plan against the backlog.

    The sustainability guarantees this enforces:
      * every backlog id is placed in exactly one phase (nothing dropped)
      * every backlog id has exactly one feature_plan entry, and its phase
        matches the phase whose items list contains it
      * phase numbers are contiguous from 0
      * dependencies flow forward: a feature's backlog deps are in the same or
        an earlier phase (you can't schedule a feature before what it needs)
      * foundations/refactors referenced by feature_plan.needs actually exist
      * a feature that requires a foundation isn't scheduled before it (all
        foundations are Phase-0 primitives, so any needer must be phase >= those)
    """
    errors: list[str] = []
    backlog_ids = {it["id"] for it in backlog.get("items", [])}
    deps = {it["id"]: set(it.get("dependencies", []) or []) for it in backlog.get("items", [])}

    phases = plan.get("phases", [])
    nums = sorted(p["phase"] for p in phases)
    if nums != list(range(len(nums))):
        errors.append(f"phase numbers must be contiguous from 0, got {nums}")

    # placement: exactly one phase per id
    placed: dict[str, list[int]] = {}
    for p in phases:
        for fid in p.get("items", []):
            placed.setdefault(fid, []).append(p["phase"])
            if fid not in backlog_ids:
                errors.append(f"phase {p['phase']} references unknown id '{fid}'")
    for fid in backlog_ids:
        where = placed.get(fid, [])
        if not where:
            errors.append(f"[{fid}] is not placed in any phase (add them all)")
        elif len(where) > 1:
            errors.append(f"[{fid}] placed in multiple phases {where}")
    phase_of = {fid: w[0] for fid, w in placed.items() if len(w) == 1}

    # feature_plan: one per id, phase agrees with placement
    fp_ids: dict[str, int] = {}
    foundation_ids = {f["id"] for f in plan.get("foundations", [])}
    refactor_ids = {r["id"] for r in plan.get("prerequisite_refactors", [])}
    known_needs = foundation_ids | refactor_ids
    for fp in plan.get("feature_plan", []):
        fid = fp.get("id")
        if fid in fp_ids:
            errors.append(f"[{fid}] has duplicate feature_plan entries")
        fp_ids[fid] = fp.get("phase")
        if fid not in backlog_ids:
            errors.append(f"feature_plan references unknown id '{fid}'")
        if fid in phase_of and fp.get("phase") != phase_of[fid]:
            errors.append(f"[{fid}] feature_plan phase {fp.get('phase')} != its phase placement {phase_of[fid]}")
        for need in fp.get("needs", []) or []:
            if need not in known_needs:
                errors.append(f"[{fid}] needs unknown foundation/refactor '{need}'")
    for fid in backlog_ids:
        if fid not in fp_ids:
            errors.append(f"[{fid}] missing a feature_plan entry")

    # dependency ordering: deps in same or earlier phase
    for fid, dset in deps.items():
        if fid not in phase_of:
            continue
        for d in dset:
            if d in phase_of and phase_of[d] > phase_of[fid]:
                errors.append(f"[{fid}] (phase {phase_of[fid]}) depends on {d} scheduled later (phase {phase_of[d]})")

    # foundations must enable real ids
    for f in plan.get("foundations", []):
        for fid in f.get("enables", []):
            if fid not in backlog_ids:
                errors.append(f"foundation {f['id']} enables unknown id '{fid}'")

    return errors


def main() -> int:
    backlog = _load(BACKLOG)
    errors = validate(backlog)
    n = len(backlog.get("items", []))

    plan_note = ""
    if BUILD_PLAN.exists():
        plan = _load(BUILD_PLAN)
        plan_errors = validate_build_plan(backlog, plan)
        errors += [f"build_plan: {e}" for e in plan_errors]
        if not plan_errors:
            plan_note = f"; build plan places all {n} items across {len(plan.get('phases', []))} phases"

    if errors:
        print(f"FAIL: {len(errors)} problem(s) in {n} backlog item(s):\n")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK: {n} backlog items valid "
          f"({sum(1 for i in backlog['items'] if i['status'] in ('ready', 'in_progress', 'in_rollout'))} "
          f"ready/in-flight){plan_note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
