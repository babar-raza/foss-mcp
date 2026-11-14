"""G3/TC-812 (REQ-G3-013, 3D-JAVA-005): a "must NOT exist" claim in served ``agent_guidance`` that the
live symbol index contradicts is DISCLOSED with the real FQNs - never stripped.

REPRODUCED on the real corpus (infra/demo-manifests, 2026-11-14): the served 3d/java agent_guidance
(14,101 chars) lists ``License``, ``Metered`` and ``LicenseManager`` under "The following APIs must NOT
exist in the FOSS version", yet the active 3d/java generation indexes ``com.aspose.threed.License`` and
``com.aspose.threed.Metered`` (``LicenseManager`` is genuinely absent, so it is never named in the note).

MEASURED over all 32 pilots' real agent_guidance against each pilot's own real index: exactly ONE pilot
(3d/java) has a contradicted negative claim, and the contradicted names are exactly ``License`` and
``Metered``. A looser rule (any line with a cue and any backticked indexed name) was rejected for
flagging pilots that make no such claim; the strict, block-scoped rule is what the tests below pin.

Wiring (AGENTS.md "Integration and liveness"): ``server._live_get_product_reference`` must actually pass
``known_symbols`` to ``get_product_reference`` - asserted on the committed source AND through the real
``tools/call`` handler of ``create_server`` over a copy of the committed 3d/java generation.
"""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams

import infra.fetch_product_reference as fetch_product_reference
from foss_mcp.extraction.repo_native_reader import DocumentPresent
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import create_server
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    _contradicted_negative_claims,
    _negative_blocks,
    get_product_reference,
    read_known_symbols,
    symbol_fqns_by_short_name,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
GUARD_SECONDS = 120

NOTE_MARKER = "[Correction note from this server's live symbol index"


def _pilots() -> list[tuple[str, str]]:
    return sorted(
        (path.name.split("__")[0], path.name.split("__")[1])
        for path in DEMO_MANIFESTS.glob("*__self_extracted")
        if path.is_dir()
    )


PILOTS = _pilots()


def _scope(family: str, platform: str) -> Scope:
    return Scope(family=family, platform=platform, source_kind="self_extracted")


def _inputs(family: str, platform: str) -> ProductReferenceInputs:
    path = DEMO_MANIFESTS / fetch_product_reference.sidecar_name(family, platform)
    assert path.is_file(), path
    inputs = fetch_product_reference.load_manifest_sidecar(path)
    assert inputs is not None
    return inputs


def _document(content: str, *, path: str = "AGENTS.md") -> DocumentPresent:
    return DocumentPresent(path=path, sha="0" * 40, size=len(content), content=content)


def _served_text(result: Any) -> str:
    assert isinstance(result, ReferenceContent), result
    return result.text


JAVA_SYMBOLS = {
    "License": ("com.aspose.threed.License",),
    "Metered": ("com.aspose.threed.Metered",),
    "Camera": ("com.aspose.threed.Camera",),
}


# ---- REPRODUCE FIRST: the claims, on the real corpus -----------------------------------------------


def test_reproduced_the_real_3d_java_index_holds_license_and_metered_but_not_licensemanager() -> None:
    store = GenerationManifestStore(DEMO_MANIFESTS)
    known = read_known_symbols(store, _scope("3d", "java"))
    assert known["License"] == ("com.aspose.threed.License",)
    assert known["Metered"] == ("com.aspose.threed.Metered",)
    assert "LicenseManager" not in known
    for entity in ("Camera", "Light", "Deformer", "Material"):
        assert known[entity] == (f"com.aspose.threed.{entity}",), entity


def test_reproduced_the_real_3d_java_guidance_still_carries_the_stale_claim() -> None:
    document = _inputs("3d", "java").agent_guidance
    assert isinstance(document, DocumentPresent)
    assert "must NOT exist in the FOSS version" in document.content
    assert "`License`, `Metered`, `LicenseManager`" in document.content


# ---- the real 3d/java answer ------------------------------------------------------------------------


def test_real_3d_java_answer_discloses_the_contradiction_with_real_fqns_and_removes_nothing() -> None:
    inputs = _inputs("3d", "java")
    known = read_known_symbols(GenerationManifestStore(DEMO_MANIFESTS), _scope("3d", "java"))
    before = _served_text(get_product_reference(inputs, "agent_guidance"))
    after = _served_text(get_product_reference(inputs, "agent_guidance", known_symbols=known))

    # DISCLOSE, do not strip: every original character is kept, the note is purely appended.
    assert after.startswith(before)
    note = after[len(before) :]
    assert note.startswith("\n\n" + NOTE_MARKER)
    assert "`License` is indexed as `com.aspose.threed.License`" in note
    assert "`Metered` is indexed as `com.aspose.threed.Metered`" in note
    # LicenseManager is genuinely absent from the index, so the note must not claim otherwise.
    assert "LicenseManager" not in note
    assert "SetLicense" not in note


def test_a_server_without_known_symbols_answers_exactly_as_before() -> None:
    inputs = _inputs("3d", "java")
    default = get_product_reference(inputs, "agent_guidance")
    assert get_product_reference(inputs, "agent_guidance", known_symbols=None) == default
    assert get_product_reference(inputs, "agent_guidance", known_symbols={}) == default
    assert NOTE_MARKER not in _served_text(default)


# ---- the portfolio: exactly one pilot, exactly two names --------------------------------------------


def test_the_roster_is_the_32_committed_pilots() -> None:
    assert len(PILOTS) >= 32


def test_over_every_pilots_real_guidance_and_real_index_only_3d_java_flags_exactly_license_and_metered() -> (
    None
):
    store = GenerationManifestStore(DEMO_MANIFESTS)
    flagged: dict[str, list[str]] = {}
    for family, platform in PILOTS:
        document = _inputs(family, platform).agent_guidance
        if not isinstance(document, DocumentPresent):
            continue
        known = read_known_symbols(store, _scope(family, platform))
        assert known, (family, platform, "real index yields no symbols")
        contradicted = _contradicted_negative_claims(document.content, known)
        if contradicted:
            flagged[f"{family}/{platform}"] = sorted(contradicted)
    assert flagged == {"3d/java": ["License", "Metered"]}


def test_over_every_pilot_the_note_appears_in_the_served_answer_only_for_3d_java() -> None:
    store = GenerationManifestStore(DEMO_MANIFESTS)
    with_note = []
    for family, platform in PILOTS:
        inputs = _inputs(family, platform)
        known = read_known_symbols(store, _scope(family, platform))
        before = get_product_reference(inputs, "agent_guidance")
        after = get_product_reference(inputs, "agent_guidance", known_symbols=known)
        if isinstance(before, ReferenceContent):
            assert isinstance(after, ReferenceContent)
            assert after.text.startswith(before.text)
            if NOTE_MARKER in after.text:
                with_note.append(f"{family}/{platform}")
            else:
                assert after == before, (family, platform)
        else:
            assert after == before, (family, platform)
    assert with_note == ["3d/java"]


# ---- the block-scoped rule ---------------------------------------------------------------------------

KNOWN = {
    "License": ("com.example.License",),
    "Camera": ("com.example.scene.Camera", "com.example.other.Camera"),
    "Widget": ("com.example.Widget",),
}


def test_a_paragraph_opened_by_a_must_not_exist_line_is_one_negative_block_with_its_list() -> None:
    text = "Intro.\n\nThe following APIs must NOT exist:\n- `License`, `Ghost`\n- `Widget()`\n\nOther prose `Camera`."
    assert _negative_blocks(text) == [
        "The following APIs must NOT exist:\n- `License`, `Ghost`\n- `Widget()`"
    ]
    assert _contradicted_negative_claims(text, KNOWN) == {
        "License": ("com.example.License",),
        "Widget": ("com.example.Widget",),
    }


def test_a_cue_that_is_not_on_the_first_line_of_its_paragraph_is_not_a_negative_block() -> None:
    text = "The API surface:\n- `License` is the licence class\n- `Camera` was excluded earlier"
    assert _negative_blocks(text) == []
    assert _contradicted_negative_claims(text, KNOWN) == {}


def test_backticked_indexed_names_in_a_block_without_a_cue_are_never_flagged() -> None:
    text = "Use `License` and `Camera` like this.\n\nThey are stable."
    assert _contradicted_negative_claims(text, KNOWN) == {}


def test_fenced_code_is_never_part_of_a_negative_block() -> None:
    text = "Do not implement these:\n\n```\n`License`\n```\n\n- `Widget`"
    # The cue paragraph holds no identifier; the fence is skipped; the following list has no cue.
    assert _contradicted_negative_claims(text, KNOWN) == {}


def test_matching_is_case_sensitive_and_requires_a_real_indexed_name() -> None:
    text = "Excluded (Do Not Implement):\n- `license`, `LICENSE`, `Nonexistent`, `LicenseManager`"
    assert _contradicted_negative_claims(text, KNOWN) == {}


def test_a_qualified_identifier_counts_only_when_it_is_a_real_fqn() -> None:
    real = "These must not exist: `com.example.scene.Camera`"
    fake = "These must not exist: `org.nowhere.Camera`"
    assert _contradicted_negative_claims(real, KNOWN) == {
        "com.example.scene.Camera": ("com.example.scene.Camera",)
    }
    assert _contradicted_negative_claims(fake, KNOWN) == {}


def test_the_note_names_every_real_fqn_of_an_ambiguous_short_name() -> None:
    known = {"Camera": KNOWN["Camera"]}
    result = get_product_reference(
        ProductReferenceInputs(agent_guidance=_document("Intentionally omitted:\n- `Camera`")),
        "agent_guidance",
        known_symbols=known,
    )
    text = _served_text(result)
    assert "`com.example.scene.Camera`" in text and "`com.example.other.Camera`" in text
    assert text.startswith("Intentionally omitted:\n- `Camera`")


def test_the_note_also_rides_the_contributor_labelled_branch_and_keeps_its_label() -> None:
    content = "# CLAUDE.md\n\nThe following APIs must NOT exist:\n- `License`\n"
    inputs = ProductReferenceInputs(agent_guidance=_document(content, path="CLAUDE.md"))
    before = get_product_reference(inputs, "agent_guidance")
    after = get_product_reference(inputs, "agent_guidance", known_symbols=KNOWN)
    assert isinstance(before, ReferenceContent) and isinstance(after, ReferenceContent)
    assert after.untrusted_upstream_content is True
    assert after.notice == before.notice
    assert after.text.startswith(before.text)
    assert "`License` is indexed as `com.example.License`" in after.text


def test_an_absent_guidance_document_stays_an_explicit_absence() -> None:
    result = get_product_reference(ProductReferenceInputs(), "agent_guidance", known_symbols=KNOWN)
    assert not isinstance(result, ReferenceContent)


def test_other_sections_ignore_known_symbols() -> None:
    inputs = _inputs("3d", "java")
    for section in ("contributing", "limitations", "license"):
        assert get_product_reference(inputs, section) == get_product_reference(
            inputs, section, known_symbols=JAVA_SYMBOLS
        )


def test_symbol_fqns_by_short_name_groups_real_symbol_chunks_only() -> None:
    documents = {
        "a": {"text": "FQN: com.example.License\nKind: class\n"},
        "b": {"text": "FQN: com.example.Other.License\nKind: class\n"},
        "c": {"text": "no fqn line at all"},
    }
    grouped = symbol_fqns_by_short_name(documents)
    assert grouped.get("License") == ("com.example.License", "com.example.Other.License")
    assert "no" not in grouped


def test_read_known_symbols_never_raises() -> None:
    class Broken:
        def read_active(self, key: str) -> str:
            raise RuntimeError("store down")

    assert read_known_symbols(Broken(), _scope("3d", "java")) == {}
    assert read_known_symbols(GenerationManifestStore(DEMO_MANIFESTS), _scope("zz", "none")) == {}


# ---- WIRING: the real server path -------------------------------------------------------------------


def test_the_server_source_passes_known_symbols_to_the_product_reference_call() -> None:
    source = inspect.getsource(server_module._live_get_product_reference)
    assert "known_symbols=read_known_symbols(store, scope)" in source
    module_source = Path(server_module.__file__).read_text(encoding="utf-8")
    assert "read_known_symbols," in module_source


class _RealToolsClient:
    """A client whose ``invoke`` runs the real ``tools/call`` handler of ``create_server``."""

    def __init__(self, server: Any) -> None:
        entry = server.get_request_handler("tools/call")
        assert entry is not None
        self._handler = entry.handler

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        async def run() -> Any:
            with anyio.fail_after(GUARD_SECONDS):
                return await self._handler(None, CallToolRequestParams(name=name, arguments=arguments))

        result = anyio.run(run)
        assert not result.is_error, result.content
        return result.structured_content["result"]


def _real_client(tmp_path: Path, family: str, platform: str) -> _RealToolsClient:
    directory = f"{family}__{platform}__self_extracted"
    shutil.copytree(DEMO_MANIFESTS / directory, tmp_path / directory)
    server = create_server(
        DeploymentConfig(family=family, platform=platform),
        GenerationManifestStore(tmp_path),
        product_reference_inputs=_inputs(family, platform),
    )
    return _RealToolsClient(server)


def test_the_real_served_3d_java_agent_guidance_carries_the_correction_note(tmp_path: Path) -> None:
    client = _real_client(tmp_path, "3d", "java")
    served = client.invoke("get_product_reference", {"section": "agent_guidance"})
    text = served["text"]
    original = _inputs("3d", "java").agent_guidance
    assert isinstance(original, DocumentPresent)
    assert "must NOT exist in the FOSS version" in text
    assert NOTE_MARKER in text
    assert "`License` is indexed as `com.aspose.threed.License`" in text
    assert "`Metered` is indexed as `com.aspose.threed.Metered`" in text
    # The disclosure never removes upstream text.
    assert "`License`, `Metered`, `LicenseManager`" in text


def test_the_real_served_3d_net_agent_guidance_has_no_note(tmp_path: Path) -> None:
    client = _real_client(tmp_path, "3d", "net")
    served = client.invoke("get_product_reference", {"section": "agent_guidance"})
    assert NOTE_MARKER not in served["text"]


@pytest.mark.parametrize("section", ["license", "limitations"])
def test_the_real_server_does_not_read_symbols_for_other_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    calls: list[str] = []

    def spy(store: Any, scope: Any) -> dict[str, tuple[str, ...]]:
        calls.append(section)
        return {}

    monkeypatch.setattr(server_module, "read_known_symbols", spy)
    client = _real_client(tmp_path, "3d", "java")
    client.invoke("get_product_reference", {"section": section})
    assert calls == []
    client.invoke("get_product_reference", {"section": "agent_guidance"})
    assert calls == [section]
