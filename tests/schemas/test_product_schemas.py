"""Schema tests for the PRODUCT schemas, with negative controls.

Mirrors the discipline in ops/tests/test_schemas.py: a suite that only proves
well-formed documents pass proves almost nothing, so every positive case here
is paired with cases that MUST fail.

Plan 19.2 names one negative control explicitly for this card: a manifest
missing `schedule` must be rejected. That is asserted directly below by
deleting `schedule` from a well-formed manifest and requiring an error - if
`schedule` is ever dropped from product-manifest.schema.json's `required`
list, this test (and the "every required field" sweep) must fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas" / "product"

DISPOSITIONS = [
    "REUSE_AS_IS",
    "REUSE_WITH_CONFIGURATION",
    "WRAP_WITH_ADAPTER",
    "EXTRACT_TO_SHARED_CORE",
    "MODIFY_WITH_COMPATIBILITY",
    "REPLACE_WITH_PROVEN_ALTERNATIVE",
    "DO_NOT_REUSE",
    "INSUFFICIENT_EVIDENCE",
]


def load(name):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def errors(schema, instance):
    return list(jsonschema.Draft202012Validator(schema).iter_errors(instance))


def test_both_schemas_are_themselves_valid_draft_2020_12():
    for name in ("product-manifest.schema.json", "reuse-manifest.schema.json"):
        jsonschema.Draft202012Validator.check_schema(load(name))


# --------------------------------------------------------------- product manifest


def _manifest(**over):
    m = {
        "family": "aspose-words",
        "platform": "python",
        "repository": "aspose-words/Aspose.Words-for-Python-via-NET",
        "schedule": {"cron": "0 3 * * *", "timezone": "UTC"},
        "storage_topology": "single-index",
    }
    m.update(over)
    return m


def test_product_manifest_accepts_a_well_formed_document():
    assert errors(load("product-manifest.schema.json"), _manifest()) == []


def test_product_manifest_rejects_a_document_missing_schedule():
    """The named negative control: a manifest missing `schedule` must be
    rejected. If dropping `schedule` from the schema's required list does
    not break this test, the test is asserting nothing."""
    schema = load("product-manifest.schema.json")
    m = _manifest()
    del m["schedule"]
    assert errors(schema, m), "a manifest missing schedule must be rejected"


def test_product_manifest_requires_every_required_field():
    schema = load("product-manifest.schema.json")
    for field in schema["required"]:
        m = _manifest()
        del m[field]
        assert errors(schema, m), f"a manifest missing {field} must be rejected"


def test_product_manifest_rejects_unknown_top_level_key():
    assert errors(load("product-manifest.schema.json"), _manifest(extra_field="nope"))


def test_product_manifest_rejects_malformed_repository_full_name():
    schema = load("product-manifest.schema.json")
    for bad in ("no-slash-here", "/leading-slash", "trailing-slash/", "too/many/slashes", ""):
        assert errors(schema, _manifest(repository=bad)), f"{bad!r} must be rejected"


def test_product_manifest_accepts_schedule_without_optional_timezone():
    m = _manifest(schedule={"cron": "0 3 * * *"})
    assert errors(load("product-manifest.schema.json"), m) == []


def test_product_manifest_rejects_schedule_missing_cron():
    schema = load("product-manifest.schema.json")
    m = _manifest(schedule={"timezone": "UTC"})
    assert errors(schema, m), "a schedule without cron must be rejected"


# ----------------------------------------------------------------- reuse manifest


def _reuse(**over):
    r = {
        "component": "some-upstream-lib",
        "disposition": "REUSE_AS_IS",
        "rationale": "battle-tested, license-compatible, no modification needed",
    }
    r.update(over)
    return r


def test_reuse_manifest_accepts_a_well_formed_document():
    assert errors(load("reuse-manifest.schema.json"), _reuse()) == []


@pytest.mark.parametrize("disposition", DISPOSITIONS)
def test_reuse_manifest_accepts_every_named_disposition(disposition):
    assert errors(load("reuse-manifest.schema.json"), _reuse(disposition=disposition)) == []


def test_reuse_manifest_rejects_a_disposition_outside_the_eight_values():
    schema = load("reuse-manifest.schema.json")
    for bad in ("REUSE", "reuse_as_is", "NOT_A_REAL_DISPOSITION", "", "REUSE_AS_IS "):
        assert errors(schema, _reuse(disposition=bad)), f"{bad!r} must be rejected"


def test_reuse_manifest_rejects_unknown_top_level_key():
    assert errors(load("reuse-manifest.schema.json"), _reuse(extra_field="nope"))


def test_reuse_manifest_requires_every_required_field():
    schema = load("reuse-manifest.schema.json")
    for field in schema["required"]:
        r = _reuse()
        del r[field]
        assert errors(schema, r), f"a reuse manifest missing {field} must be rejected"
