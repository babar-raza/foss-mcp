"""TC-329: committed, content-verified demo manifests for the self-contained demo image.

Offline. No docker, no network. Reads infra/demo-manifests/ and
infra/helm/foss-mcp/values.yaml as committed files only. The expected set of pilots is derived
from values.yaml's ``ingestion.pilots`` list — never hardcoded here — so this test tracks the
chart's own pilot roster rather than drifting from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"


def _expected_scope_dirs() -> list[str]:
    """Every pilot in values.yaml's ingestion.pilots, as its GenerationManifestStore
    ``_scope_dir()`` name: ``<family>__<platform>__<source_kind>``."""
    data: dict[str, Any] = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    pilots = data["ingestion"]["pilots"]
    return sorted(f"{p['family']}__{p['platform']}__{p['sourceKind']}" for p in pilots)


def test_one_scope_directory_per_pilot_named_from_values_yaml() -> None:
    expected = _expected_scope_dirs()
    found = sorted(p.name for p in DEMO_MANIFESTS.iterdir() if p.is_dir())
    assert found == expected, (found, expected)


def test_each_scope_directory_has_exactly_active_pointer_and_one_generation_file() -> None:
    for scope in _expected_scope_dirs():
        scope_dir = DEMO_MANIFESTS / scope
        entries = sorted(p.name for p in scope_dir.iterdir())
        generation_files = [
            name for name in entries if name.startswith("generation__") and name.endswith(".json")
        ]
        assert "active_pointer.json" in entries, (scope, entries)
        assert "lease_state.json" not in entries, (scope, entries)
        assert len(generation_files) == 1, (scope, entries)
        assert entries == sorted(["active_pointer.json", generation_files[0]]), (scope, entries)


def test_each_scope_directorys_active_pointer_matches_its_generation_file() -> None:
    for scope in _expected_scope_dirs():
        scope_dir = DEMO_MANIFESTS / scope
        active_pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
        generation_files = sorted(scope_dir.glob("generation__*.json"))
        assert len(generation_files) == 1, (scope, generation_files)
        generation = json.loads(generation_files[0].read_text(encoding="utf-8"))
        assert active_pointer["generation_id"] == generation["generation_id"], (
            scope,
            active_pointer["generation_id"],
            generation["generation_id"],
        )


def test_each_scope_directorys_generation_carries_non_empty_lexical_documents() -> None:
    for scope in _expected_scope_dirs():
        scope_dir = DEMO_MANIFESTS / scope
        generation_files = sorted(scope_dir.glob("generation__*.json"))
        assert len(generation_files) == 1, (scope, generation_files)
        generation = json.loads(generation_files[0].read_text(encoding="utf-8"))
        documents = generation["payload"]["lexical_index"]["documents"]
        assert isinstance(documents, dict), (scope, type(documents))
        assert len(documents) > 0, (scope, "documents is empty - no queryable content")
