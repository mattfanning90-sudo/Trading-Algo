"""The product backlog is a governed artifact — validate it in CI.

Keeps product/backlog/backlog.json well-formed and fully specified: unique ids,
all four role reviews present, evidence + acceptance criteria, correct RICE
arithmetic, sound dependencies, and complete rollout plans. A malformed or
under-specified backlog item fails the build here, the same way the trading
invariants are enforced by tests/test_consistency.py.
"""
import json
from pathlib import Path

import pytest

from product.validate import ROLES, validate

ROOT = Path(__file__).resolve().parent.parent
BACKLOG = ROOT / "product" / "backlog" / "backlog.json"


@pytest.fixture(scope="module")
def backlog():
    with BACKLOG.open() as fh:
        return json.load(fh)


def test_backlog_passes_validator(backlog):
    errors = validate(backlog)
    assert not errors, "Backlog validation failed:\n" + "\n".join(errors)


def test_ids_unique_and_wellformed(backlog):
    ids = [it["id"] for it in backlog["items"]]
    assert len(ids) == len(set(ids)), "duplicate backlog ids"
    for iid in ids:
        assert iid.startswith("F") and iid[1:].isdigit(), iid


def test_every_item_has_all_four_role_reviews(backlog):
    for it in backlog["items"]:
        reviews = it["role_reviews"]
        assert set(ROLES) <= set(reviews), f"{it['id']} missing a role review"


def test_measurable_criteria_have_metrics(backlog):
    for it in backlog["items"]:
        for ac in it["acceptance_criteria"]:
            if ac.get("measurable"):
                assert ac.get("metric"), f"{it['id']}/{ac['id']} measurable without a metric"


def test_rice_score_matches_inputs(backlog):
    for it in backlog["items"]:
        s = it["scoring"]
        expect = s["reach"] * s["impact"] * s["confidence"] / s["effort"]
        assert abs(expect - s["score"]) <= 0.01, f"{it['id']} RICE score drifted"


def test_dependencies_resolve(backlog):
    ids = {it["id"] for it in backlog["items"]}
    for it in backlog["items"]:
        for dep in it.get("dependencies", []):
            assert dep in ids, f"{it['id']} depends on missing {dep}"
            assert dep != it["id"], f"{it['id']} depends on itself"


def test_schemas_are_valid_json(backlog):
    schema_dir = ROOT / "product" / "schema"
    for name in ("backlog_item.schema.json", "rollout.schema.json"):
        with (schema_dir / name).open() as fh:
            doc = json.load(fh)
        assert doc.get("$schema", "").startswith("https://json-schema.org"), name
