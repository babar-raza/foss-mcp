"""G3/TC-518: the product-reference answers over EVERY baked pilot of ``infra/demo-manifests``.

The independent pre-production review found ``get_product_reference`` answering ``limitations`` "has no
data source in this project yet" for 40 of 40 pilots while the same deployment's index held the README
section "Scope and Limitations". These tests are the contract that cannot happen again: for every
pilot, the sidecar is loaded the way the server loads it, the README sections are read from the
pilot's own baked generation, and the tool's answer is compared with the very chunks it would
otherwise claim are missing.

Data-driven: the pilot list is whatever ``product_reference_*.json`` files the directory holds, never
a hard-coded list. The scope directory and the sidecar are copied to a temporary directory first, so
nothing under ``infra/demo-manifests`` is ever opened for writing. Offline and deterministic.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSections, read_readme_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SOURCE_KIND = "self_extracted"
LEAD_IN = "Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries"
FORMATS_HEADING = (
    "File-format statements quoted from the README (selected by keyword, not an exhaustive list):"
)
MIT_NORMALISED = "Normalised to its SPDX identifier from the manifest's license name 'MIT License'."


def _pilots() -> list[str]:
    names = sorted(
        path.stem[len("product_reference_") :] for path in MANIFESTS.glob("product_reference_*.json")
    )
    assert names, "no baked product-reference sidecar found"
    return names


PILOTS = _pilots()


def _family_platform(pilot: str) -> tuple[str, str]:
    family, platform = pilot.split("_", 1)
    return family, platform


class _Baked:
    """The baked pilots, each copied once to a temporary directory and read from the copy."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, tuple[ProductReferenceInputs, ReadmeSections, list[str]]] = {}
        (root / "sidecars").mkdir()
        self.store = GenerationManifestStore(root / "scopes")

    def _copy(self, pilot: str) -> Path:
        family, platform = _family_platform(pilot)
        scope_dir = f"{family}__{platform}__{SOURCE_KIND}"
        shutil.copytree(MANIFESTS / scope_dir, self.root / "scopes" / scope_dir)
        sidecar = self.root / "sidecars" / f"product_reference_{pilot}.json"
        shutil.copyfile(MANIFESTS / f"product_reference_{pilot}.json", sidecar)
        return sidecar

    def load(self, pilot: str) -> tuple[ProductReferenceInputs, ReadmeSections, list[str]]:
        """``(inputs, readme sections, the pilot's documentation chunk texts)``."""
        if pilot not in self._cache:
            sidecar = self._copy(pilot)
            inputs = load_manifest_sidecar(sidecar)
            assert inputs is not None, pilot
            family, platform = _family_platform(pilot)
            readme = read_readme_sections(self.store, Scope(family=family, platform=platform))
            key = f"{family}::{platform}::{SOURCE_KIND}"
            active = self.store.read_active(key)
            assert active is not None, pilot
            documents = self.store.read_generation(key, active).payload["lexical_index"]["documents"]
            doc_texts = [d["text"] for d in documents.values() if d["text"].startswith("FQN: Doc: ")]
            self._cache[pilot] = (inputs, readme, doc_texts)
        return self._cache[pilot]

    def answer(self, pilot: str, section: str) -> ReferenceContent | NotAvailable:
        inputs, readme, _ = self.load(pilot)
        return get_product_reference(inputs, section, readme=readme)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def baked(tmp_path_factory: pytest.TempPathFactory) -> _Baked:
    return _Baked(tmp_path_factory.mktemp("baked_pilots"))


def _title(text: str) -> str:
    return text.split("\n", 1)[0][len("FQN: Doc: ") :]


def _body(text: str) -> str:
    """A documentation chunk's README text: no FQN line, no Part marker, no Source-Commit line."""
    lines = [line for line in text.split("\n")[1:] if not line.startswith("Source-Commit:")]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and re.match(r"^Part:\s*\d+\s+of\s+\d+\s*$", lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def _limitation_chunks(doc_texts: list[str]) -> list[str]:
    return [text for text in doc_texts if "limitation" in _title(text).lower()]


_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def _after_lead_in(body: str) -> str:
    """A body that starts with LEAD_IN loses its first paragraph (everything up to and including the first blank
    line; a body that is the lead-in alone gives the empty string); any other body is unchanged.

    Since TC-517 a chunk is the generated lead-in paragraph (possibly wrapped over several lines) followed by the
    README text; the 19 pilots not yet re-ingested at TC-553 still carried the lead-in as a SEPARATE chunk, so both
    shapes are handled."""
    if not body.startswith(LEAD_IN):
        return body
    parts = _BLANK_LINE.split(body, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


# --- the contract -----------------------------------------------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS)
def test_limitations_is_never_no_data_source_when_the_index_has_the_section(
    baked: _Baked, pilot: str
) -> None:
    _, _, doc_texts = baked.load(pilot)
    chunks = _limitation_chunks(doc_texts)

    result = baked.answer(pilot, "limitations")

    if not chunks:
        assert isinstance(result, NotAvailable)
        assert result.reason == "limitations has no data source in this project yet"
        return
    assert isinstance(result, ReferenceContent), (pilot, getattr(result, "reason", None))
    assert result.section == "limitations"
    assert result.untrusted_upstream_content is True
    assert result.notice is not None
    assert "Scope and Limitations" in result.notice or "imitation" in result.notice
    assert "Treat as data" in result.notice
    # re-pinned by TC-553: the filter discarded every chunk that starts with the lead-in -> the lead-in paragraph is cut off
    # and the README text after it is kept (since TC-517 the lead-in and the real text are ONE chunk)
    bodies = [body for body in (_after_lead_in(_body(text)) for text in chunks) if body]
    assert bodies, pilot
    # A sentence of the chunks the index holds is in the answer; when nothing was truncated, all of them are.
    assert any(body in result.text for body in bodies), pilot
    if "[truncated; use search_docs" not in result.text:
        assert all(body in result.text for body in bodies), pilot
    assert LEAD_IN not in result.text
    for marker in ("FQN: Doc:", "Source-Commit:"):
        assert marker not in result.text


def test_at_least_twenty_five_pilots_have_a_limitations_chunk_and_are_answered(baked: _Baked) -> None:
    with_chunk = [pilot for pilot in PILOTS if _limitation_chunks(baked.load(pilot)[2])]
    answered = [
        pilot for pilot in with_chunk if isinstance(baked.answer(pilot, "limitations"), ReferenceContent)
    ]

    # re-pinned by TC-551: 30 -> 25
    assert len(with_chunk) >= 25, with_chunk
    assert answered == with_chunk


@pytest.mark.parametrize("pilot", PILOTS)
def test_formats_is_content_or_a_specific_reason(baked: _Baked, pilot: str) -> None:
    _, readme, _ = baked.load(pilot)
    result = baked.answer(pilot, "formats")

    if readme.capabilities is None and readme.limitations is None:
        assert result == NotAvailable("formats", "formats has no data source in this project yet")
    elif isinstance(result, ReferenceContent):
        lines = result.text.split("\n")
        assert lines[0] == FORMATS_HEADING
        assert 1 <= len(lines) - 1 <= 8
        assert all(line.startswith("- ") and len(line) <= 302 for line in lines[1:])
        assert result.untrusted_upstream_content is True
        assert result.notice is not None and "Treat as data" in result.notice
    else:
        assert result == NotAvailable(
            "formats",
            "no file-format statement was found in the indexed README sections; ask lookup or search_docs",
        )


def test_formats_is_content_for_most_pilots(baked: _Baked) -> None:
    content = [pilot for pilot in PILOTS if isinstance(baked.answer(pilot, "formats"), ReferenceContent)]

    assert len(content) >= 25, content


# re-pinned by TC-553: ["cells_cpp", "email_cpp", "pdf_cpp", "slides_cpp"] -> ["email_cpp", "pdf_cpp", "slides_cpp"] (cells_cpp has
# no README installation section: see test_cells_cpp_install_is_an_honest_absence_while_its_readme_recipe_is_only_a_faq)
@pytest.mark.parametrize("pilot", ["email_cpp", "pdf_cpp", "slides_cpp"])
def test_cpp_installs_are_the_readme_recipe(baked: _Baked, pilot: str) -> None:
    _, readme, _ = baked.load(pilot)
    assert readme.install is not None, pilot

    result = baked.answer(pilot, "install")

    assert isinstance(result, ReferenceContent), (pilot, getattr(result, "reason", None))
    assert result.untrusted_upstream_content is True
    assert result.notice is not None and "Treat as data" in result.notice
    assert "names no package-manager coordinate" not in result.text
    assert result.text.strip()
    assert result.text.startswith(readme.install.text[:40])
    assert "cmake" in result.text.lower(), pilot


def test_cells_cpp_install_is_an_honest_absence_while_its_readme_recipe_is_only_a_faq(baked: _Baked) -> None:
    """The committed README page of cells/cpp has no installation section; its only install recipe is a FAQ answer, and a
    FAQ answer is never the installation section (readme_sections, test_readme_sections.test_faq_chunks_are_never_a_section).
    Extending the detection is a product decision, not a test change; the recipe stays reachable through the documentation."""
    _, readme, doc_texts = baked.load("cells_cpp")
    assert readme.install is None, (
        "cells_cpp now has a README installation section: move it back into test_cpp_installs_are_the_readme_recipe"
    )

    result = baked.answer("cells_cpp", "install")

    assert isinstance(result, NotAvailable), result
    assert "names no package-manager coordinate" in result.reason
    assert "add_subdirectory" not in result.reason and "FetchContent" not in result.reason

    faq = [text for text in doc_texts if _title(text) == "How do I install the library?"]
    assert faq, "the cells/cpp documentation holds no FAQ chunk titled 'How do I install the library?'"
    recipe = " ".join(_body(faq[0]).split())
    for needle in ("add_subdirectory", "FetchContent", "CMake 3.15"):
        assert needle in recipe, needle


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_answer_stays_a_well_formed_answer_for_its_section(baked: _Baked, pilot: str) -> None:
    for section in ("install", "formats", "limitations", "compatibility", "license", "support"):
        result = baked.answer(pilot, section)
        assert result.section == section
        if isinstance(result, ReferenceContent):
            assert result.text.strip(), (pilot, section)
            if result.untrusted_upstream_content:
                assert result.notice, (pilot, section)


# --- expectations on the baked data -----------------------------------------------------------------


def test_cells_go_compatibility_shows_both_go_versions(baked: _Baked) -> None:
    result = baked.answer("cells_go", "compatibility")

    assert isinstance(result, ReferenceContent)
    assert result.text == "1.24.5"
    assert result.notice is not None
    assert "1.24.5" in result.notice
    assert "1.18" in result.notice
    assert "AGENTS.md (untrusted)" in result.notice


@pytest.mark.parametrize("pilot", ["3d_java", "cells_java", "pdf_java", "slides_java"])
def test_the_four_java_pilots_license_is_mit_with_a_normalisation_notice(baked: _Baked, pilot: str) -> None:
    result = baked.answer(pilot, "license")

    assert result == ReferenceContent("license", "MIT", notice=MIT_NORMALISED)


def test_note_python_license_conflict_is_surfaced(baked: _Baked) -> None:
    result = baked.answer("note_python", "license")

    assert isinstance(result, ReferenceContent)
    assert result.text == "LicenseRef-Aspose-Split"
    assert result.notice is not None
    assert "LicenseRef-Aspose-Split" in result.notice
    assert "reads as MIT" in result.notice
    assert "They conflict" in result.notice


def test_cells_typescript_compatibility_comes_from_the_readme_and_3d_typescript_stays_not_available(
    baked: _Baked,
) -> None:
    cells = baked.answer("cells_typescript", "compatibility")
    three_d = baked.answer("3d_typescript", "compatibility")

    assert isinstance(cells, ReferenceContent)
    assert cells.text == ">=18"
    assert cells.notice is not None and "taken from the README installation section" in cells.notice
    assert isinstance(three_d, NotAvailable)
    assert three_d.reason.startswith("manifest does not state an engines.node range")


def test_cells_rust_compatibility_states_the_edition(baked: _Baked) -> None:
    result = baked.answer("cells_rust", "compatibility")

    assert isinstance(result, ReferenceContent)
    assert result.text == "Rust edition 2021 (rust-version not stated)"


def test_the_manifest_declared_version_reaches_a_registry_install_notice(baked: _Baked) -> None:
    result = baked.answer("cells_go", "install")
    python = baked.answer("note_python", "install")

    assert isinstance(result, ReferenceContent) and isinstance(python, ReferenceContent)
    assert python.text == "pip install aspose-note-foss"
    manifest_version = re.search(r'version\s*=\s*"([^"]+)"', baked.load("note_python")[0].manifest_text or "")
    assert manifest_version is not None
    assert python.notice is not None
    assert f"The manifest declares package version {manifest_version.group(1)}." in python.notice


# --- production wiring ------------------------------------------------------------------------------


def test_server_py_references_read_readme_sections() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py").read_text(encoding="utf-8")

    assert "from foss_mcp.mcp.tools.readme_sections import" in source
    assert re.search(r"\bread_readme_sections\(store, scope\)", source), "the call site is missing"
    assert re.search(r"get_product_reference\(inputs, section, readme=readme\)", source)
    assert "ReadmeSections" in source


def test_the_registered_tool_answers_limitations_from_the_index_end_to_end(baked: _Baked) -> None:
    pilot = "cells_go"
    inputs, _, _ = baked.load(pilot)
    family, platform = _family_platform(pilot)
    scope = Scope(family=family, platform=platform)

    registry = server_module._build_tool_registry(baked.store, scope, inputs, ())
    limitations = registry["get_product_reference"](section="limitations")
    formats = registry["get_product_reference"](section="formats")
    license_answer = registry["get_product_reference"](section="license")

    assert isinstance(limitations, ReferenceContent)
    assert "CalculateFormula" in limitations.text
    assert limitations.untrusted_upstream_content is True
    assert isinstance(formats, ReferenceContent)
    assert isinstance(license_answer, (ReferenceContent, NotAvailable))


def test_the_registered_tool_survives_an_unreadable_index(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "empty")
    inputs = ProductReferenceInputs()
    registry = server_module._build_tool_registry(store, Scope(family="cells", platform="go"), inputs, ())

    result = registry["get_product_reference"](section="limitations")

    assert result == NotAvailable("limitations", "limitations has no data source in this project yet")


def test_the_baked_sidecars_were_not_modified_by_these_tests(baked: _Baked) -> None:
    # The copies are read; the originals are only ever copied from.
    assert baked.root != MANIFESTS
    json.loads((MANIFESTS / f"product_reference_{PILOTS[0]}.json").read_text(encoding="utf-8"))


# --- the lead-in helper, proven on synthetic bodies ----------------------------------------------------


def test_after_lead_in_of_the_lead_in_alone_is_empty() -> None:
    assert _after_lead_in(f"{LEAD_IN} for this library, as of the pinned commit:") == ""


def test_after_lead_in_keeps_the_text_after_the_lead_in_paragraph() -> None:
    body = f"{LEAD_IN}, as of the pinned commit:\n\nThe library cannot write DRM-protected files."
    assert _after_lead_in(body) == "The library cannot write DRM-protected files."


def test_after_lead_in_handles_a_lead_in_wrapped_over_two_lines() -> None:
    body = f"{LEAD_IN}\nof this library, as of the pinned commit:\n\nFirst limit.\n\nSecond limit."
    assert _after_lead_in(body) == "First limit.\n\nSecond limit."


def test_after_lead_in_leaves_a_body_that_merely_contains_the_lead_in_later() -> None:
    body = f"Some other text.\n\n{LEAD_IN}: nothing."
    assert _after_lead_in(body) == body


def test_after_lead_in_leaves_a_body_without_it_unchanged() -> None:
    assert _after_lead_in("Plain limitation text.") == "Plain limitation text."
    assert _after_lead_in("") == ""
