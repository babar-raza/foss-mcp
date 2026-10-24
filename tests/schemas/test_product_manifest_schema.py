"""G3/TC-582: schema coverage for ``product-manifest.schema.json``'s new OPTIONAL ``known_hazards``
field - a project-authored, project-verified hazard disclosure (e.g. cells/go's reproduced
concurrency crash), never upstream content.

``tests/schemas/test_product_schemas.py`` already covers this schema's general shape (required
fields, repository pattern, schedule) with its own negative controls; this file is scoped to
``known_hazards`` alone, per this card's own write_paths. It proves: the field is OPTIONAL (every
one of the other 31 real pilot manifests, with no such field, still validates), the field accepts
``config/products/cells/go.yaml`` exactly as this card leaves it, and a malformed entry (missing
the required ``text`` field) is rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _errors(instance: dict) -> list:
    return list(jsonschema.Draft202012Validator(_schema()).iter_errors(instance))


def _well_formed_manifest(**over):
    manifest = {
        "family": "aspose-words",
        "platform": "python",
        "repository": "aspose-words/Aspose.Words-for-Python-via-NET",
        "schedule": {"cron": "0 3 * * *", "timezone": "UTC"},
        "storage_topology": "single-index",
    }
    manifest.update(over)
    return manifest


def test_known_hazards_is_not_in_the_schemas_required_list():
    """The field must stay OPTIONAL: every other pilot's manifest validates unchanged with no field
    added. If ``known_hazards`` is ever added to ``required``, this fails loudly."""
    assert "known_hazards" not in _schema()["required"]


def test_schema_accepts_a_manifest_with_no_known_hazards_field():
    assert _errors(_well_formed_manifest()) == []


def test_every_other_real_pilot_manifest_still_validates_with_no_known_hazards_field():
    """Every one of the other 31 real pilot manifests (today, every pilot but cells/go) has no
    ``known_hazards`` field and still validates - no regression from adding the OPTIONAL field."""
    paths = sorted(CONFIG_PRODUCTS.glob("*/*.yaml"))
    others = [path for path in paths if not (path.parent.name == "cells" and path.stem == "go")]
    assert len(others) == 31, others
    for path in others:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "known_hazards" not in manifest, path
        assert _errors(manifest) == [], (path, _errors(manifest))


def test_schema_accepts_config_products_cells_go_yaml_exactly_as_this_card_leaves_it():
    manifest = yaml.safe_load((CONFIG_PRODUCTS / "cells" / "go.yaml").read_text(encoding="utf-8"))
    assert "known_hazards" in manifest
    assert len(manifest["known_hazards"]) >= 1
    assert _errors(manifest) == []


def test_schema_accepts_a_manifest_with_a_well_formed_known_hazards_entry():
    manifest = _well_formed_manifest(
        known_hazards=[{"text": "a plainly stated hazard", "verified_at": "2026-10-24"}]
    )
    assert _errors(manifest) == []


def test_schema_accepts_multiple_known_hazards_entries():
    manifest = _well_formed_manifest(
        known_hazards=[
            {"text": "first hazard", "verified_at": "2026-10-20"},
            {"text": "second hazard", "verified_at": "2026-10-21"},
        ]
    )
    assert _errors(manifest) == []


def test_schema_rejects_a_known_hazards_entry_missing_text():
    """The named negative control: an entry missing the required ``text`` field must be rejected."""
    manifest = _well_formed_manifest(known_hazards=[{"verified_at": "2026-10-24"}])
    assert _errors(manifest), "a known_hazards entry missing text must be rejected"


def test_schema_rejects_a_known_hazards_entry_missing_verified_at():
    manifest = _well_formed_manifest(known_hazards=[{"text": "a hazard with no date"}])
    assert _errors(manifest), "a known_hazards entry missing verified_at must be rejected"


def test_schema_rejects_a_known_hazards_entry_with_an_unknown_key():
    manifest = _well_formed_manifest(
        known_hazards=[{"text": "a hazard", "verified_at": "2026-10-24", "severity": "high"}]
    )
    assert _errors(manifest), "an unknown key in a known_hazards entry must be rejected"


def test_schema_rejects_a_known_hazards_entry_with_a_malformed_date():
    manifest = _well_formed_manifest(known_hazards=[{"text": "a hazard", "verified_at": "Oct 24 2026"}])
    assert _errors(manifest), "a non YYYY-MM-DD verified_at must be rejected"


def test_schema_rejects_known_hazards_as_a_bare_object_not_an_array():
    manifest = _well_formed_manifest(known_hazards={"text": "a hazard", "verified_at": "2026-10-24"})
    assert _errors(manifest), "known_hazards must be an array, not a bare object"
