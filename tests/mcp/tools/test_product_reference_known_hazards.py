"""G3/TC-582: a project-authored, project-verified hazard disclosure in ``get_product_reference``'s
"limitations" answer.

A 2026-10-24 independent audit live-reproduced a real, serious hazard (5 out of 5 runs): two
goroutines mutating different cells of ONE SHARED cells/go ``Workbook`` crash the process with an
unrecoverable Go runtime fatal error, not a catchable panic. The real upstream repository documents
no thread-safety guidance anywhere - a genuine upstream gap - but this project's own served
``limitations`` answer carried no warning either. ``config/products/cells/go.yaml`` now carries one
``known_hazards`` entry describing it (schemas/product/product-manifest.schema.json's new OPTIONAL
field); ``known_hazards_from_manifest`` parses it and ``get_product_reference`` merges it into the
"limitations" answer, distinctly framed as THIS PROJECT's own verified finding and never marked
``untrusted_upstream_content``.

These tests prove: (1) cells/go's LIVE answer (the real baked sidecar, the real README, the real
config/products/cells/go.yaml) discloses the hazard, distinctly framed, never marked
``untrusted_upstream_content``. (2) Every one of the other 31 real pilot manifests carries no
``known_hazards`` field, and feeding that absence through the real merge path changes nothing - no
regression. (3) A synthetic manifest with two ``known_hazards`` entries produces both, in a stable,
deterministic order.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
    known_hazards_from_manifest,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSections, read_readme_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"
SOURCE_KIND = "self_extracted"
KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"

ALL_PRODUCT_MANIFEST_PATHS = sorted(CONFIG_PRODUCTS.glob("*/*.yaml"))
OTHER_PILOT_MANIFEST_PATHS = [
    path for path in ALL_PRODUCT_MANIFEST_PATHS if not (path.parent.name == "cells" and path.stem == "go")
]
# 32 is every real pilot manifest under config/products/; 31 is that count minus cells/go.
# If a pilot is ever added or removed, re-pin both counts here to match the new total.
assert len(ALL_PRODUCT_MANIFEST_PATHS) == 32, ALL_PRODUCT_MANIFEST_PATHS
assert len(OTHER_PILOT_MANIFEST_PATHS) == 31, OTHER_PILOT_MANIFEST_PATHS


def _real_manifest(family: str, platform: str) -> dict[str, Any]:
    path = CONFIG_PRODUCTS / family / f"{platform}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_baked(tmp_path: Path, family: str, platform: str) -> tuple[ProductReferenceInputs, ReadmeSections]:
    """``(inputs, readme)`` for *family*/*platform*'s REAL baked pilot (infra/demo-manifests) - the exact
    way the live server loads it (``load_manifest_sidecar`` + ``read_readme_sections``), copied to a
    temporary directory first so nothing under infra/demo-manifests is ever opened for writing."""
    scope_dir = f"{family}__{platform}__{SOURCE_KIND}"
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir(exist_ok=True)
    shutil.copytree(MANIFESTS / scope_dir, scopes_dir / scope_dir)
    sidecar = tmp_path / f"product_reference_{family}_{platform}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{family}_{platform}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, (family, platform)
    store = GenerationManifestStore(scopes_dir)
    readme = read_readme_sections(store, Scope(family=family, platform=platform))
    return inputs, readme


# ----------------------------------------------------------------------------------------- (1) cells/go


def test_cells_go_live_limitations_discloses_the_reproduced_concurrency_hazard(tmp_path: Path) -> None:
    """cells/go's LIVE limitations answer - the real baked sidecar, the real README, and the real
    config/products/cells/go.yaml's own known_hazards entry - discloses the reproduced
    concurrent-mutation crash, distinctly framed as this project's own verified finding, and the
    merged answer is NEVER marked untrusted_upstream_content."""
    inputs, readme = _load_baked(tmp_path, "cells", "go")
    manifest = _real_manifest("cells", "go")
    hazards = known_hazards_from_manifest(manifest)
    assert hazards, "config/products/cells/go.yaml must carry at least one known_hazards entry"
    inputs = dataclasses.replace(inputs, known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent)
    assert result.section == "limitations"
    # Distinctly framed as THIS PROJECT's own verified finding.
    assert KNOWN_HAZARD_LEAD in result.text
    assert "goroutine" in result.text.lower()
    assert "fatal error" in result.text.lower() or "unrecoverable" in result.text.lower()
    assert "workbook" in result.text.lower()
    # Never a claim about what the upstream repository's own documentation says.
    assert result.untrusted_upstream_content is False
    # The README-sourced limitations text is still there too - never replaced, only added alongside.
    assert "CalculateFormula" in result.text
    # Hazard paragraph is a distinct block, not merged into the README prose.
    hazard_index = result.text.index(KNOWN_HAZARD_LEAD)
    readme_index = result.text.index("CalculateFormula")
    assert readme_index < hazard_index, "the hazard paragraph must come after the README text, not inside it"


def test_cells_go_manifest_known_hazard_states_failure_mode_trigger_and_safe_pattern() -> None:
    """config/products/cells/go.yaml's own entry, read as plainly as an agent would: the real failure
    mode (unrecoverable, not a catchable panic), the real trigger (two goroutines, one shared
    Workbook), and the safe pattern (one Workbook per goroutine, or serialize access)."""
    manifest = _real_manifest("cells", "go")
    hazards = known_hazards_from_manifest(manifest)

    assert len(hazards) == 1
    hazard = hazards[0]
    text = hazard.text.lower()
    assert "not a catchable panic" in text or "unrecoverable" in text
    assert "goroutine" in text and "workbook" in text
    assert "serialize" in text or "one workbook" in text or "mutex" in text
    assert hazard.verified_at == "2026-10-24"


# -------------------------------------------------------------------------- (2) no regression, 31 others


@pytest.mark.parametrize("path", OTHER_PILOT_MANIFEST_PATHS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_other_pilots_manifest_carries_no_known_hazards(path: Path) -> None:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert known_hazards_from_manifest(manifest) == ()


@pytest.mark.parametrize(
    "family,platform",
    [("pdf", "net"), ("words", "python"), ("slides", "cpp"), ("3d", "typescript")],
)
def test_other_pilot_limitations_answer_is_unchanged_by_the_known_hazards_field(
    tmp_path: Path, family: str, platform: str
) -> None:
    """A synthetic manifest with NO known_hazards field (every real pilot but cells/go, today)
    produces a limitations answer byte-identical to before this card - proven against the REAL
    product manifest and the REAL baked sidecar, not a mock of ``get_product_reference``."""
    inputs, readme = _load_baked(tmp_path, family, platform)
    manifest = _real_manifest(family, platform)
    hazards_from_manifest = known_hazards_from_manifest(manifest)
    assert hazards_from_manifest == ()

    before = get_product_reference(inputs, "limitations", readme=readme)
    after = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards_from_manifest), "limitations", readme=readme
    )

    assert after == before
    if isinstance(before, ReferenceContent):
        assert KNOWN_HAZARD_LEAD not in before.text


def test_limitations_answer_unchanged_when_known_hazards_defaults_to_empty() -> None:
    """``known_hazards=()`` (the field's default, and what every manifest but cells/go's parses to)
    merges to nothing: the "limitations" answer is returned completely unchanged, whatever shape it
    already had (``ReferenceContent`` or ``NotAvailable``)."""
    result = get_product_reference(
        ProductReferenceInputs(platform="python", readme_document=None), "limitations", readme=None
    )
    assert result == NotAvailable("limitations", "limitations has no data source in this project yet")


# ------------------------------------------------------------------- (3) two known_hazards, synthetic


def test_two_known_hazards_entries_both_appear_in_a_stable_deterministic_order() -> None:
    """A synthetic manifest with two known_hazards entries produces both, in manifest order, every
    time - never reordered, never deduplicated into one."""
    synthetic_manifest: dict[str, Any] = {
        "known_hazards": [
            {"text": "First hazard: do not do the first unsafe thing.", "verified_at": "2026-10-20"},
            {"text": "Second hazard: do not do the second unsafe thing.", "verified_at": "2026-10-21"},
        ]
    }
    hazards = known_hazards_from_manifest(synthetic_manifest)
    assert len(hazards) == 2

    inputs = ProductReferenceInputs(platform="go", known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert result.untrusted_upstream_content is False
    first_index = result.text.index("First hazard: do not do the first unsafe thing.")
    second_index = result.text.index("Second hazard: do not do the second unsafe thing.")
    assert first_index < second_index
    assert result.text.count(KNOWN_HAZARD_LEAD) == 2

    # Re-running with the same inputs produces the exact same answer - deterministic, not reordered.
    again = get_product_reference(inputs, "limitations", readme=None)
    assert again == result


def test_known_hazards_from_manifest_skips_an_entry_missing_text() -> None:
    """A malformed entry (the schema's own negative control - a missing ``text`` field) is skipped
    rather than guessed at; a well-formed sibling entry is still returned."""
    manifest: dict[str, Any] = {
        "known_hazards": [
            {"verified_at": "2026-10-24"},  # missing text - skipped
            {"text": "a real hazard", "verified_at": "2026-10-24"},
        ]
    }
    hazards = known_hazards_from_manifest(manifest)
    assert len(hazards) == 1
    assert hazards[0].text == "a real hazard"


def test_known_hazards_from_manifest_absent_field_is_empty() -> None:
    assert known_hazards_from_manifest({}) == ()
    assert known_hazards_from_manifest({"known_hazards": None}) == ()
