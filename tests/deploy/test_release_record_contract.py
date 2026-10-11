"""G3/TC-348: the release-image record is a public, CI-run contract (REQ-G3-004).

`gatectl release-record` writes evidence/release/*.json and the state machine derives `release_images` from
them. The mechanism has its own canary tests under ops/tests; this module pins the SHAPE of the record from the
product side, importing nothing from ops/: the schema must stay a valid schema, a realistic record must validate,
and each way a record could silently name the wrong image must be rejected. It also checks the real records in
this repository, and that docs/RELEASE.md says what an operator needs to know.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "schemas"
RELEASE_DIR = REPO_ROOT / "evidence" / "release"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"
GATE_REPORT_SUFFIX = ".gate-report.json"

IMAGE_ID = "sha256:" + "b" * 64
DIGEST = "sha256:" + "d" * 64
COMMIT = "a" * 40
REFERENCE = "foss-mcp-serving:demo-aaaaaaa"
STATE_ROW_FIELDS = {
    "image",
    "image_id",
    "source_commit",
    "size_mb",
    "status",
    "gate_report",
    "registry_reference",
    "registry_digest",
    "record",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return _load("release-image.schema.json")


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


def _errors(validator: Draft202012Validator, doc: Any) -> list[str]:
    return [error.message for error in validator.iter_errors(doc)]


def local_only_record() -> dict[str, Any]:
    """A realistic record for an image that is built and gate-tested but not yet pushed."""
    return {
        "schema_version": 1,
        "generated_by": "gatectl",
        "recorded_at": "2026-10-11T10:00:00Z",
        "status": "LOCAL_ONLY",
        "image": {
            "reference": REFERENCE,
            "image_id": IMAGE_ID,
            "size_bytes": 52428800,
            "source_commit": COMMIT,
            "created": "2026-10-11T09:00:00Z",
        },
        "gate_report": {
            "file": f"evidence/release/{REFERENCE.replace(':', '__')}--0a1b2c3d{GATE_REPORT_SUFFIX}",
            "sha256": "e" * 64,
            "verdict": "PASS",
            "pilots_total": 40,
            "pilots_passed": 40,
            "image_id": IMAGE_ID,
            "peak_memory_mb": 120.25,
            "memory_budget": "256m",
        },
        "registry": None,
    }


def pushed_record() -> dict[str, Any]:
    """The same image after `push_image.py` pushed it to an internal registry (placeholder host)."""
    doc = local_only_record()
    doc["status"] = "PUSHED"
    doc["registry"] = {
        "reference": f"registry.example.internal:5000/foss-mcp/{REFERENCE}",
        "digest": DIGEST,
        "pushed_at": "2026-10-11T10:05:00Z",
    }
    return doc


def _set(path: tuple[str, ...], value: Any):
    def mutate(doc: dict[str, Any]) -> None:
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _drop(path: tuple[str, ...]):
    def mutate(doc: dict[str, Any]) -> None:
        node = doc
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


# (1) the schema itself ------------------------------------------------------------------------------------------


def test_the_schema_is_a_valid_draft_2020_12_schema(schema):
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


# (2) realistic records validate ---------------------------------------------------------------------------------


def test_a_realistic_local_only_record_with_a_null_registry_validates(validator):
    doc = local_only_record()
    assert doc["registry"] is None
    assert _errors(validator, doc) == []


def test_a_realistic_pushed_record_validates(validator):
    assert _errors(validator, pushed_record()) == []


# (3) each way a record could name the wrong image is rejected --------------------------------------------------

MUTATIONS = {
    "latest tag": (pushed_record, _set(("image", "reference"), "foss-mcp-serving:latest")),
    "image id is not sha256:<64 hex>": (pushed_record, _set(("image", "image_id"), "sha256:" + "b" * 63)),
    "image id has no sha256 prefix": (pushed_record, _set(("image", "image_id"), "b" * 64)),
    "39-character source commit": (pushed_record, _set(("image", "source_commit"), "a" * 39)),
    "status other than LOCAL_ONLY or PUSHED": (pushed_record, _set(("status",), "RELEASED")),
    "registry object without a digest": (pushed_record, _drop(("registry", "digest"))),
    "registry digest that is not sha256": (pushed_record, _set(("registry", "digest"), "md5:" + "d" * 32)),
    "extra unknown top-level key": (pushed_record, _set(("approved_by",), "a model")),
    "missing gate_report": (pushed_record, _drop(("gate_report",))),
    "negative pilots_passed": (pushed_record, _set(("gate_report", "pilots_passed"), -1)),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_a_record_with_this_defect_is_rejected(validator, name):
    build, mutate = MUTATIONS[name]
    good = build()
    assert _errors(validator, good) == [], "the unmutated record must validate, or this test proves nothing"
    bad = copy.deepcopy(good)
    mutate(bad)
    assert bad != good, "the mutation changed nothing"
    assert _errors(validator, bad), f"a record with {name} was accepted by the schema"


def test_a_local_only_record_with_a_missing_registry_key_is_rejected(validator):
    doc = local_only_record()
    del doc["registry"]
    assert _errors(validator, doc), "`registry` is required; LOCAL_ONLY carries an explicit null"


# (4) the derived state section ----------------------------------------------------------------------------------


def test_state_schema_requires_release_images_and_every_row_field():
    state = _load("state.schema.json")
    assert "release_images" in state["required"]
    Draft202012Validator.check_schema(state)
    rows = state["properties"]["release_images"]
    assert rows["type"] == "array"
    row = rows["items"]
    assert row.get("additionalProperties") is False
    assert set(row["required"]) == STATE_ROW_FIELDS
    assert set(row["properties"]) == STATE_ROW_FIELDS


def test_a_row_derived_from_each_realistic_record_fits_the_state_row_schema():
    rows_schema = _load("state.schema.json")["properties"]["release_images"]
    for doc, record_name in ((local_only_record(), "local"), (pushed_record(), "pushed")):
        reg = doc["registry"]
        row = {
            "image": doc["image"]["reference"],
            "image_id": doc["image"]["image_id"],
            "source_commit": doc["image"]["source_commit"],
            "size_mb": round(doc["image"]["size_bytes"] / 1048576, 1),
            "status": doc["status"],
            "gate_report": f"{doc['gate_report']['verdict']} {doc['gate_report']['pilots_passed']}/"
            f"{doc['gate_report']['pilots_total']}",
            "registry_reference": reg["reference"] if reg else None,
            "registry_digest": reg["digest"] if reg else None,
            "record": f"evidence/release/{record_name}.json",
        }
        assert list(Draft202012Validator(rows_schema).iter_errors([row])) == []


# (5) the real records in this repository ------------------------------------------------------------------------


def _real_records() -> list[Path]:
    if not RELEASE_DIR.is_dir():
        return []
    return sorted(p for p in RELEASE_DIR.glob("*.json") if not p.name.endswith(GATE_REPORT_SUFFIX))


def test_every_real_release_record_validates_and_has_a_matching_gate_report(validator):
    """Vacuously true until the first image is recorded; from then on it guards every committed record."""
    for path in _real_records():
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert _errors(validator, doc) == [], f"{path.name} does not match the release-image schema"
        report = REPO_ROOT / doc["gate_report"]["file"]
        assert report.is_file(), (
            f"{path.name}: the copied gate report {doc['gate_report']['file']} is missing"
        )
        assert hashlib.sha256(report.read_bytes()).hexdigest() == doc["gate_report"]["sha256"], (
            f"{path.name}: the copied gate report's sha256 does not match the record"
        )
        assert doc["gate_report"]["image_id"] == doc["image"]["image_id"], (
            f"{path.name}: the gate report is for a different image than the record"
        )
        assert (doc["status"] == "PUSHED") == (doc["registry"] is not None), (
            f"{path.name}: status and registry disagree"
        )


# (6) the operator's page ----------------------------------------------------------------------------------------


def _doc_text() -> str:
    assert RELEASE_DOC.is_file(), "docs/RELEASE.md does not exist"
    return RELEASE_DOC.read_text(encoding="utf-8")


def test_the_operator_page_names_every_tool_and_place_it_depends_on():
    text = _doc_text()
    for needle in (
        "gatectl release-record",
        "image_workflow_gate.py",
        "push_image.py",
        "compact_demo_manifests.py",
        "evidence/release",
        "OWNER-13",
    ):
        assert needle in text, f"docs/RELEASE.md does not name {needle}"


def test_the_operator_page_states_that_an_ungated_image_is_not_hosted():
    flat = " ".join(_doc_text().split()).lower()
    assert "an image without a passing gate report for that exact image id is not hosted" in flat


def test_the_operator_page_carries_no_credential_and_only_placeholder_hosts():
    text = _doc_text()
    credential_shapes = (
        r"--password",
        r"docker login[^\n]*\s-p\b",
        r"password\s*[:=]",
        r"token\s*[:=]\s*\S",
        r"://[^/\s]*:[^/@\s]+@",
    )
    for shape in credential_shapes:
        assert not re.search(shape, text, re.I), f"docs/RELEASE.md matches the credential shape {shape!r}"
    for host in re.findall(r"docker (?:login|pull) (?:--\S+ )*([A-Za-z0-9.:-]+)", text):
        assert host.endswith(".example.internal:5000") or host.startswith(("<", "registry.example")), (
            f"docs/RELEASE.md names a host that is not a placeholder: {host}"
        )
