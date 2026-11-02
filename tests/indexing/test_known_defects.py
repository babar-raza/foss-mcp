"""Offline tests for KD1 (REQ-G3-015): the known-defects registry's schema and loader.

This module is dormant - not wired into any live tool yet (KD2-KD9's job) - so every test here
exercises the loader and its helpers directly, against a synthetic fixture file written under
``tmp_path``, never the real (currently empty) ``config/known_defects/`` directory. No network, no
toolchain, no real interpreter.

``jsonschema.Draft202012Validator`` is used here, in test code only, to confirm
``schemas/product/known_defects.schema.json`` itself correctly accepts a valid document and rejects
a structurally invalid one - matching the established pattern in
``tests/config/test_cells_cpp_manifest.py``. The production loader
(``src/foss_mcp/indexing/known_defects.py``) never imports ``jsonschema``; it hand-validates the
same structure, plus the rules a schema cannot express, in plain Python.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.known_defects import (
    AppliesTo,
    Evidence,
    KnownDefect,
    KnownDefectsError,
    Subject,
    VerifiedOn,
    defect_is_stale,
    load_known_defects,
    registry_path_for,
    validate_known_defects,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_document(pilot: str = "imaging_net") -> dict:
    return {
        "schema_version": 1,
        "pilot": pilot,
        "defects": [
            {
                "id": "IMAGING-NET-004",
                "subject": {"kind": "method", "refs": ["Aspose.Imaging.Image.Save"]},
                "applies_to": {"package_versions": ["26.7.0"]},
                "summary": "Image.Save raises on a malformed TIFF header the reader otherwise accepts.",
                "user_action": "Validate the TIFF header before calling Save, or catch the exception.",
                "evidence": {
                    "repro": "Called Save on a crafted TIFF fixture with a truncated IFD; raised ArgumentException.",
                    "verified_on": {
                        "package": "Aspose.Imaging",
                        "version": "26.7.0",
                        "date": "2026-10-15",
                    },
                },
                "status": "open",
            }
        ],
    }


def _write_registry(
    tmp_path: Path, document: dict, *, family: str = "imaging", platform: str = "net"
) -> Path:
    path = registry_path_for(family, platform, base_dir=tmp_path)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


# --- loader: absent file, valid fixture -----------------------------------------------------


def test_load_known_defects_returns_empty_list_when_no_file_exists(tmp_path):
    assert load_known_defects("imaging", "net", base_dir=tmp_path) == []


def test_registry_path_for_uses_double_underscore_separator(tmp_path):
    path = registry_path_for("imaging", "net", base_dir=tmp_path)
    assert path == tmp_path / "imaging__net.yaml"


def test_load_known_defects_loads_a_valid_fixture_with_every_field_intact(tmp_path):
    _write_registry(tmp_path, _valid_document())
    defects = load_known_defects("imaging", "net", base_dir=tmp_path)

    assert len(defects) == 1
    defect = defects[0]
    assert isinstance(defect, KnownDefect)
    assert defect.id == "IMAGING-NET-004"
    assert defect.subject == Subject(kind="method", refs=("Aspose.Imaging.Image.Save",))
    assert defect.applies_to == AppliesTo(indexed_commit=None, package_versions=("26.7.0",))
    assert defect.summary.startswith("Image.Save raises")
    assert defect.user_action.startswith("Validate the TIFF header")
    assert defect.evidence == Evidence(
        repro=("Called Save on a crafted TIFF fixture with a truncated IFD; raised ArgumentException."),
        verified_on=VerifiedOn(package="Aspose.Imaging", version="26.7.0", date="2026-10-15"),
    )
    assert defect.status == "open"


def test_load_known_defects_raises_for_wrong_pilot(tmp_path):
    _write_registry(tmp_path, _valid_document(pilot="imaging_java"))
    with pytest.raises(KnownDefectsError, match="expected 'imaging_net'"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


# --- jsonschema: the schema document itself, matching tests/config/test_cells_cpp_manifest.py ----


def test_schema_accepts_the_valid_fixture_document():
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(_valid_document()))
    assert errors == [], f"valid fixture failed schema validation: {errors}"


def test_schema_rejects_a_document_with_an_unexpected_top_level_property():
    """Guards the top-level ``additionalProperties: false`` specifically: an unexpected/typo'd
    top-level key (for example a stray ``note``) must be rejected, not silently ignored."""
    schema = _load_schema()
    document = copy.deepcopy(_valid_document())
    document["note"] = "not a real field"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted an unexpected top-level property"


def test_schema_rejects_a_document_missing_a_required_field():
    schema = _load_schema()
    document = copy.deepcopy(_valid_document())
    del document["defects"][0]["status"]
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted a defect missing its required 'status' field"


def test_schema_rejects_an_out_of_range_summary_length():
    schema = _load_schema()
    document = copy.deepcopy(_valid_document())
    document["defects"][0]["summary"] = "x" * 241
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted a summary longer than 240 characters"


def test_schema_rejects_an_invalid_status_enum_value():
    schema = _load_schema()
    document = copy.deepcopy(_valid_document())
    document["defects"][0]["status"] = "bogus"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted an invalid status enum value"


def test_schema_rejects_an_invalid_subject_kind_enum_value():
    schema = _load_schema()
    document = copy.deepcopy(_valid_document())
    document["defects"][0]["subject"]["kind"] = "bogus"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted an invalid subject.kind enum value"


# --- loader: rules a schema cannot express --------------------------------------------------


def test_loader_rejects_duplicate_id_within_one_pilot_file(tmp_path):
    document = _valid_document()
    duplicate = copy.deepcopy(document["defects"][0])
    document["defects"].append(duplicate)
    _write_registry(tmp_path, document)

    with pytest.raises(KnownDefectsError, match="not unique"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


def test_validate_known_defects_rejects_duplicate_id_directly():
    document = _valid_document()
    document["defects"].append(copy.deepcopy(document["defects"][0]))
    problems = validate_known_defects(document, pilot="imaging_net")
    assert any("not unique" in problem for problem in problems)


def test_loader_rejects_an_out_of_range_calendar_date(tmp_path):
    """The schema's own date pattern only checks the YYYY-MM-DD *shape*; month 13 day 40 still
    matches that shape, so only the loader's real datetime.date parsing can reject it."""
    document = _valid_document()
    document["defects"][0]["evidence"]["verified_on"]["date"] = "2026-13-40"
    _write_registry(tmp_path, document)

    with pytest.raises(KnownDefectsError, match="real calendar date"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


def test_loader_rejects_fixed_upstream_scoped_only_to_an_indexed_commit(tmp_path):
    document = _valid_document()
    document["defects"][0]["status"] = "fixed_upstream"
    document["defects"][0]["applies_to"] = {"indexed_commit": "a" * 40}
    _write_registry(tmp_path, document)

    with pytest.raises(KnownDefectsError, match="fixed_upstream"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


def test_loader_accepts_fixed_upstream_with_package_versions(tmp_path):
    document = _valid_document()
    document["defects"][0]["status"] = "fixed_upstream"
    document["defects"][0]["applies_to"] = {
        "indexed_commit": "a" * 40,
        "package_versions": ["26.7.0"],
    }
    _write_registry(tmp_path, document)

    defects = load_known_defects("imaging", "net", base_dir=tmp_path)
    assert defects[0].status == "fixed_upstream"


def test_loader_rejects_an_applies_to_with_neither_scope(tmp_path):
    document = _valid_document()
    document["defects"][0]["applies_to"] = {}
    _write_registry(tmp_path, document)

    with pytest.raises(KnownDefectsError, match="at least one of"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


def test_loader_rejects_a_file_that_is_not_valid_yaml(tmp_path):
    path = registry_path_for("imaging", "net", base_dir=tmp_path)
    path.write_text("not: [valid: yaml: at: all", encoding="utf-8")

    with pytest.raises(KnownDefectsError, match="not valid YAML"):
        load_known_defects("imaging", "net", base_dir=tmp_path)


def test_validate_known_defects_accepts_a_file_with_zero_defects():
    document = {"schema_version": 1, "pilot": "cells_rust", "defects": []}
    assert validate_known_defects(document, pilot="cells_rust") == []


# --- defect_is_stale: the Decision C disclosure-expiry comparison helper --------------------


def _defect_verified_on(version: str) -> KnownDefect:
    return KnownDefect(
        id="X-1",
        subject=Subject(kind="method", refs=("X.Y",)),
        applies_to=AppliesTo(indexed_commit=None, package_versions=(version,)),
        summary="s",
        user_action="a",
        evidence=Evidence(repro="r", verified_on=VerifiedOn(package="X", version=version, date="2026-10-15")),
        status="open",
    )


def test_defect_is_stale_is_false_for_an_equal_version_at_different_precision_indexed_shorter():
    """The exact TC-629 bug class: (26, 7) and (26, 7, 0) are the same version."""
    defect = _defect_verified_on("26.7")
    assert defect_is_stale(defect, served_version=(26, 7, 0)) is False


def test_defect_is_stale_is_false_for_an_equal_version_at_different_precision_indexed_longer():
    defect = _defect_verified_on("26.7.0")
    assert defect_is_stale(defect, served_version=(26, 7)) is False


def test_defect_is_stale_is_true_for_a_genuinely_newer_served_version():
    defect = _defect_verified_on("26.7")
    assert defect_is_stale(defect, served_version=(26, 8, 0)) is True


def test_defect_is_stale_is_false_for_a_genuinely_older_served_version():
    defect = _defect_verified_on("26.7.0")
    assert defect_is_stale(defect, served_version=(26, 6)) is False


def test_defect_is_stale_raises_for_an_unparseable_verified_on_version():
    defect = _defect_verified_on("unknown")
    with pytest.raises(KnownDefectsError, match="no parseable digit run"):
        defect_is_stale(defect, served_version=(26, 7, 0))
