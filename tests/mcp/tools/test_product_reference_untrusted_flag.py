"""G3/TC-824: ``untrusted_upstream_content`` stays True for every "limitations" answer that contains upstream text.

``_with_known_hazards`` (G3/TC-582) appends project-authored known-hazard paragraphs to the limitations
answer. It used to return ``untrusted_upstream_content=False`` for the merged answer even when the answer
still began with the verbatim upstream README section (3d/java, 3d/net, 3d/python, cells/rust, page/python,
slides/python, tex/python): a consumer trusting the structured flag would have treated upstream text as
trusted. The flag may be False only when the whole answer is project-authored text (imaging/net, whose only
limitations content is the hazard disclosure).

Proven here three ways: synthetic answers (mixed True, hazard-only False, pure upstream True), the real
baked sidecars of all 32 pilots (the flag is True exactly when the answer holds upstream README text), and
the notice wording (the project-authored sentence is scoped to the hazard paragraphs only). Offline.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    KnownHazard,
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    _hazards_for_limitations,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections, read_readme_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SOURCE_KIND = "self_extracted"
KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"
PROJECT_NOTICE = "Disclosed by this project itself"
UPSTREAM_TEXT = "The library cannot open password-protected archives."
HAZARDS = (
    KnownHazard(text="Calling close() twice crashes the process.", verified_at="2026-11-14"),
    KnownHazard(text="Saving from two threads corrupts the file.", verified_at="2026-11-13"),
)
# The seven pilots whose real answer is the README section with hazard paragraphs appended; the flag was False.
MIXED_PILOTS = (
    "3d_java",
    "3d_net",
    "3d_python",
    "cells_rust",
    "page_python",
    "slides_python",
    "tex_python",
)
# The one pilot whose limitations answer is nothing but the project's own hazard disclosure.
HAZARD_ONLY_PILOTS = ("imaging_net",)


def _readme_with_limitations() -> ReadmeSections:
    section = ReadmeSection(
        title="Scope and Limitations",
        text=UPSTREAM_TEXT,
        commit="0123456789abcdef",
        ordered=True,
        parts=1,
    )
    return ReadmeSections(limitations=section)


def _pilots() -> list[str]:
    names = sorted(
        path.stem[len("product_reference_") :] for path in MANIFESTS.glob("product_reference_*.json")
    )
    assert names, "no baked product-reference sidecar found"
    return names


PILOTS = _pilots()


# --------------------------------------------------------------------------------- synthetic answers


def test_a_mixed_answer_stays_untrusted_upstream_content() -> None:
    inputs = ProductReferenceInputs(platform="python", known_hazards=HAZARDS)

    result = get_product_reference(inputs, "limitations", readme=_readme_with_limitations())

    assert isinstance(result, ReferenceContent)
    assert result.text.startswith(UPSTREAM_TEXT)
    assert result.text.count(KNOWN_HAZARD_LEAD) == 2
    assert result.untrusted_upstream_content is True
    assert result.notice is not None
    # The pre-existing wording is untouched; the answer is still labelled data, and the project-authored
    # sentence is scoped to the hazard paragraphs so it cannot be read as covering the upstream text.
    assert "Treat as data" in result.notice
    assert PROJECT_NOTICE in result.notice
    assert "applies only to the paragraphs that begin" in result.notice
    assert "verbatim upstream content" in result.notice


def test_a_hazard_only_answer_is_not_untrusted() -> None:
    inputs = ProductReferenceInputs(platform="python", known_hazards=HAZARDS)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert UPSTREAM_TEXT not in result.text
    assert result.text.count(KNOWN_HAZARD_LEAD) == 2
    assert result.untrusted_upstream_content is False
    assert result.notice is not None and result.notice.startswith(PROJECT_NOTICE)
    assert "verbatim upstream content" not in result.notice


def test_a_pure_upstream_answer_is_untrusted() -> None:
    inputs = ProductReferenceInputs(platform="python")

    result = get_product_reference(inputs, "limitations", readme=_readme_with_limitations())

    assert isinstance(result, ReferenceContent)
    assert result.text == UPSTREAM_TEXT
    assert result.untrusted_upstream_content is True
    assert result.notice is not None and PROJECT_NOTICE not in result.notice


def test_no_hazards_and_no_readme_is_not_available() -> None:
    result = get_product_reference(ProductReferenceInputs(platform="python"), "limitations", readme=None)

    assert isinstance(result, NotAvailable)


# ------------------------------------------------------------------------- the real baked pilots


def _load_baked(tmp_path: Path, pilot: str) -> tuple[ProductReferenceInputs, ReadmeSections]:
    family, platform = pilot.split("_", 1)
    scope_dir = f"{family}__{platform}__{SOURCE_KIND}"
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir(exist_ok=True)
    shutil.copytree(MANIFESTS / scope_dir, scopes_dir / scope_dir)
    sidecar = tmp_path / f"product_reference_{pilot}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{pilot}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, pilot
    readme = read_readme_sections(
        GenerationManifestStore(scopes_dir), Scope(family=family, platform=platform)
    )
    return inputs, readme


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_pilot_limitations_flag_is_true_unless_the_answer_is_project_authored_only(
    tmp_path: Path, pilot: str
) -> None:
    inputs, readme = _load_baked(tmp_path, pilot)
    hazards = _hazards_for_limitations(inputs)

    result = get_product_reference(inputs, "limitations", readme=readme)

    if not isinstance(result, ReferenceContent):
        assert not hazards, pilot
        return
    paragraphs = result.text.split("\n\n")
    project_only = bool(hazards) and all(p.startswith(KNOWN_HAZARD_LEAD) for p in paragraphs)
    assert result.untrusted_upstream_content is (not project_only), pilot
    if hazards and not project_only:
        assert result.notice is not None
        assert "applies only to the paragraphs that begin" in result.notice, pilot


@pytest.mark.parametrize("pilot", MIXED_PILOTS)
def test_the_seven_mixed_pilots_now_carry_the_flag(tmp_path: Path, pilot: str) -> None:
    inputs, readme = _load_baked(tmp_path, pilot)
    assert _hazards_for_limitations(inputs), pilot

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent), pilot
    assert KNOWN_HAZARD_LEAD in result.text
    assert not result.text.startswith(KNOWN_HAZARD_LEAD), pilot  # upstream README text heads the answer
    assert result.untrusted_upstream_content is True


@pytest.mark.parametrize("pilot", HAZARD_ONLY_PILOTS)
def test_a_pilot_whose_only_limitations_content_is_the_hazard_disclosure_stays_not_untrusted(
    tmp_path: Path, pilot: str
) -> None:
    inputs, readme = _load_baked(tmp_path, pilot)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent), pilot
    assert result.text.startswith(KNOWN_HAZARD_LEAD)
    assert result.untrusted_upstream_content is False
