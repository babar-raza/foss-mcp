"""KD4 (G3/TC-641, workstream J, REQ-G3-015): wiring TC-636's known-defects registry into
``get_product_reference``'s "limitations" section - the surface that carries the round-2 audit's
two headline upstream-bug examples (pdf/cpp's undecryptable AES-256 output, imaging/net's 3 DoS
bugs fixed 5 commits later upstream), since those are LIBRARY-level facts (``subject.kind``
"package" or "commit"), not single-symbol ones (which is KD2's ``get_symbol``/``list_members``
path, G3/TC-639, already wired, and is NOT this card's concern).

``get_product_reference.py`` already had a simpler, pre-existing disclosure mechanism for exactly
this section - ``KnownHazard``/``known_hazards_from_manifest``/``_with_known_hazards`` (G3/TC-582),
reading a pilot's own product manifest ``known_hazards:`` array. This card does not replace,
rename or remove that mechanism: it extends the SAME merge point so it also appends any matching
TC-636 ``known_defects`` entry, rendered through the identical ``KnownHazard`` paragraph /
provenance-notice presentation (``_known_defect_as_hazard`` feeds the unmodified
``_with_known_hazards``), never marked ``untrusted_upstream_content``.

No real defect data exists yet (KD6-KD9's job) - every test here proves the WIRING itself is
correct against a real, synthetic, ``tmp_path``-scoped registry file (never a real committed
``config/known_defects/*.yaml`` file): ``known_defects.REGISTRY_DIR`` is monkeypatched to a
``tmp_path`` directory for the duration of each test, the exact mechanism
``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) already established.

This file also records this card's ``lookup.py`` investigation finding as a regression test:
``lookup``'s own composition (``_limitation_docs``, ``answer_symbols``/``named_symbols``) never
surfaced a get_symbol-sourced ``SymbolSignature.known_defects`` through its own
``symbols: tuple[SymbolMatch, ...]`` field, because ``SymbolMatch`` (search_symbols.py's own
result shape) is structurally disconnected from ``SymbolSignature`` - and this card found no safe
way to wire that without either touching ``SymbolMatch`` itself (search_symbols.py, a different
card's write_paths) or widening ``tests/mcp/tools/test_lookup_forgiveness.py``'s own pinned
``TaskAnswer`` field-order assertion (also outside this card's write_paths) - see lookup.py's own
module docstring, "TC-641 (KD4 investigation)", for the full reasoning. ``test_lookup_disconnect``
below proves the disconnect is real and that this card left ``lookup`` unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.known_defects import registry_path_for
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.get_symbol import SymbolSignature, get_symbol
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"
FAMILY = "pdf"
PLATFORM = "cpp"
COMMIT = "a" * 40


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``tmp_path``-scoped stand-in for the real, committed ``config/known_defects/`` directory -
    never the real one. ``REGISTRY_DIR`` is read fresh on every ``load_known_defects`` call, so
    monkeypatching the module attribute is enough; no file exists here unless a test writes one."""
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _defect(
    defect_id: str,
    *,
    kind: str,
    ref: str,
    indexed_commit: str | None = None,
    package_versions: list[str] | None = None,
    summary: str = "",
    user_action: str = "",
    repro: str = "",
    verified_date: str = "2026-07-01",
) -> dict[str, Any]:
    applies_to: dict[str, Any] = {}
    if indexed_commit is not None:
        applies_to["indexed_commit"] = indexed_commit
    if package_versions is not None:
        applies_to["package_versions"] = package_versions
    return {
        "id": defect_id,
        "subject": {"kind": kind, "ref": ref},
        "applies_to": applies_to,
        "summary": summary or f"Summary describing {defect_id}.",
        "user_action": user_action or f"Recommended action for {defect_id}.",
        "evidence": {
            "repro": repro or f"Repro steps for {defect_id}.",
            "verified_on": {"package": "aspose.pdf.cpp", "version": "26.7.0", "date": verified_date},
        },
        "status": "open",
    }


def _write_registry(
    registry_dir: Path, defects: list[dict[str, Any]], *, family: str = FAMILY, platform: str = PLATFORM
) -> None:
    document = {"schema_version": 1, "pilot": f"{family}_{platform}", "defects": defects}
    path = registry_path_for(family, platform, base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------------------------
# a package/commit-scoped known-defect appends its own clearly-labelled paragraph
# --------------------------------------------------------------------------------------------


def test_package_scoped_defect_appends_its_own_paragraph_to_limitations(registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-001",
                kind="package",
                ref="aspose.pdf.cpp",
                package_versions=["26.7.0"],
                summary="AES-256 encrypted output cannot be decrypted by any reader.",
                user_action="Use AES-128 or RC4 until this is fixed upstream.",
                repro="Encrypt with AES256, reopen with Adobe Reader; the file is reported corrupt.",
            )
        ],
    )
    inputs = ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert result.section == "limitations"
    assert "AES-256 encrypted output cannot be decrypted" in result.text
    assert "Use AES-128 or RC4 until this is fixed upstream." in result.text
    assert "Encrypt with AES256, reopen with Adobe Reader" in result.text
    assert "aspose.pdf.cpp" in result.text
    # Own, clearly-labelled paragraph - never a claim about upstream's own documentation.
    assert result.untrusted_upstream_content is False


def test_commit_scoped_defect_appends_its_own_paragraph_to_limitations(registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-002",
                kind="commit",
                ref=COMMIT,
                indexed_commit=COMMIT,
                summary="3 DoS bugs present at this indexed commit, fixed 5 commits later upstream.",
                user_action="Update past this commit to pick up the upstream fix.",
                repro="Feed the malformed input fixture; the process hangs.",
            )
        ],
    )
    inputs = ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "3 DoS bugs present at this indexed commit" in result.text
    assert "Update past this commit to pick up the upstream fix." in result.text
    assert result.untrusted_upstream_content is False


# --------------------------------------------------------------------------------------------
# both known_hazards (manifest) and known_defects (registry) show, neither overwrites the other
# --------------------------------------------------------------------------------------------


def test_known_hazards_and_known_defects_both_appear_neither_overwrites_the_other(
    registry_dir: Path,
) -> None:
    from foss_mcp.mcp.tools.get_product_reference import KnownHazard

    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-003",
                kind="package",
                ref="aspose.pdf.cpp",
                package_versions=["26.7.0"],
                summary="A registry-sourced known defect.",
                user_action="Registry user action.",
                repro="Registry repro.",
            )
        ],
    )
    hazard = KnownHazard(text="A manifest-sourced known hazard.", verified_at="2026-10-20")
    inputs = ProductReferenceInputs(
        platform=PLATFORM, family=FAMILY, readme_document=None, known_hazards=(hazard,)
    )

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "A manifest-sourced known hazard." in result.text
    assert "A registry-sourced known defect." in result.text
    assert result.untrusted_upstream_content is False
    # The pre-existing known_hazards mechanism is not overwritten - it still appears, and first.
    hazard_index = result.text.index("A manifest-sourced known hazard.")
    defect_index = result.text.index("A registry-sourced known defect.")
    assert hazard_index < defect_index


# --------------------------------------------------------------------------------------------
# a pilot with neither known_hazards nor known_defects is byte-identical to before this card
# --------------------------------------------------------------------------------------------


def test_no_family_no_known_defects_is_byte_identical_to_before_this_card() -> None:
    """``family=None`` (the field's default, and every input built before this card) answers
    "limitations" exactly as it always did - proving the pre-existing known_hazards-ONLY mechanism
    is completely unaffected."""
    before = get_product_reference(
        ProductReferenceInputs(platform=PLATFORM, readme_document=None), "limitations", readme=None
    )
    after = get_product_reference(
        ProductReferenceInputs(platform=PLATFORM, family=None, readme_document=None),
        "limitations",
        readme=None,
    )

    assert after == before
    assert after == NotAvailable("limitations", "limitations has no data source in this project yet")


def test_family_set_but_no_registry_file_is_also_byte_identical(registry_dir: Path) -> None:
    """A pilot whose family IS known but has no registry file recorded yet (most pilots, today) -
    ``load_known_defects``'s own, already-established "absent file is valid, not an error"
    behaviour - answers exactly as it did before ``family`` existed. ``registry_dir`` exists but no
    file for this family/platform is written into it."""
    without_family = get_product_reference(
        ProductReferenceInputs(platform=PLATFORM, readme_document=None), "limitations", readme=None
    )
    with_family_no_file = get_product_reference(
        ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None),
        "limitations",
        readme=None,
    )

    assert with_family_no_file == without_family


def test_known_hazards_only_mechanism_unaffected_when_family_is_none() -> None:
    """The pre-existing known_hazards-only behaviour (G3/TC-582), proven again here with this
    card's code: it is unchanged when ``family`` stays unset."""
    from foss_mcp.mcp.tools.get_product_reference import KnownHazard

    hazard = KnownHazard(text="A manifest-sourced known hazard.", verified_at="2026-10-20")
    inputs = ProductReferenceInputs(platform=PLATFORM, readme_document=None, known_hazards=(hazard,))

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert KNOWN_HAZARD_LEAD in result.text
    assert "A manifest-sourced known hazard." in result.text
    assert result.untrusted_upstream_content is False


# --------------------------------------------------------------------------------------------
# a method/class-scoped defect does NOT appear here - that is KD2's get_symbol path
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["method", "class"])
def test_method_or_class_scoped_defect_does_not_surface_in_limitations(registry_dir: Path, kind: str) -> None:
    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-004",
                kind=kind,
                ref="Aspose.Pdf.Document.Save",
                package_versions=["26.7.0"],
                summary="A method/class-scoped defect that belongs to get_symbol, not here.",
            )
        ],
    )
    inputs = ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    # No registered defect at all applies here (it is library-scope-only), so the answer is the
    # same explicit absence as when no registry data exists - no stray paragraph is invented.
    assert result == NotAvailable("limitations", "limitations has no data source in this project yet")


def test_method_scoped_defect_alongside_a_package_scoped_one_only_the_package_one_shows(
    registry_dir: Path,
) -> None:
    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-005",
                kind="method",
                ref="Aspose.Pdf.Document.Save",
                package_versions=["26.7.0"],
                summary="Method-scoped defect; must not appear in limitations.",
            ),
            _defect(
                "PDF-CPP-006",
                kind="package",
                ref="aspose.pdf.cpp",
                package_versions=["26.7.0"],
                summary="Package-scoped defect; must appear in limitations.",
            ),
        ],
    )
    inputs = ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "Package-scoped defect; must appear in limitations." in result.text
    assert "Method-scoped defect; must not appear in limitations." not in result.text


# --------------------------------------------------------------------------------------------
# the other two "limitations" call sites (readme present but no limitations section; no readme
# document at all) also pick up known_defects - all three section=="limitations" branches
# --------------------------------------------------------------------------------------------


def test_readme_with_no_limitations_section_still_surfaces_known_defects(registry_dir: Path) -> None:
    from foss_mcp.mcp.tools.get_product_reference import DocumentPresent
    from foss_mcp.mcp.tools.readme_sections import ReadmeSections

    _write_registry(
        registry_dir,
        [_defect("PDF-CPP-007", kind="package", ref="aspose.pdf.cpp", package_versions=["26.7.0"])],
    )
    inputs = ProductReferenceInputs(
        platform=PLATFORM,
        family=FAMILY,
        readme_document=DocumentPresent(
            path="README.md", content="# pdf/cpp\n\nNo limitations section.", sha="deadbeef", size=30
        ),
    )
    readme = ReadmeSections()

    result = get_product_reference(inputs, "limitations", readme=readme)

    # _with_known_hazards replaces a NotAvailable entirely with the hazard/defect text (the
    # pre-existing G3/TC-582 behaviour, unchanged by this card) - the "no section on limitations"
    # reason is not itself part of the merged answer; only the disclosure paragraph is.
    assert isinstance(result, ReferenceContent)
    assert "Summary describing PDF-CPP-007." in result.text
    assert result.untrusted_upstream_content is False


def test_no_readme_document_at_all_still_surfaces_known_defects(registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [_defect("PDF-CPP-008", kind="commit", ref=COMMIT, indexed_commit=COMMIT)],
    )
    inputs = ProductReferenceInputs(platform=PLATFORM, family=FAMILY, readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    # Same replacement behaviour as the readme-present case above: the NotAvailable ("no data
    # source") is replaced entirely by the disclosure text, not appended alongside its reason.
    assert isinstance(result, ReferenceContent)
    assert "Summary describing PDF-CPP-008." in result.text
    assert result.untrusted_upstream_content is False


# --------------------------------------------------------------------------------------------
# lookup.py investigation finding: SymbolMatch is structurally disconnected from
# SymbolSignature.known_defects, and this card left lookup.py's behaviour unchanged (see
# lookup.py's own module docstring, "TC-641 (KD4 investigation)", for why forcing a fix was
# unsafe: it breaks tests/mcp/tools/test_lookup_forgiveness.py's own pinned TaskAnswer field list,
# a required check of this very card that is NOT in this card's write_paths).
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    same pattern ``test_known_defects_in_get_symbol.py`` (KD2's own tests) already established."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        from types import SimpleNamespace

        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-Cpp", commit="tc641"),
                evidence_refs=(),
                title="pdf/cpp API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc641")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc641"

    def read_generation(self, scope: str, generation_id: str) -> Any:
        return self._manifest


def _symbol_body(fqn: str, *, methods: tuple[str, ...] = (), commit: str | None = None) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class_declaration"]
    if methods:
        lines += ["", "Methods:", *(f"  - {item}" for item in methods)]
    if commit is not None:
        lines += ["", f"Source-Commit: {commit}"]
    return "\n".join(lines) + "\n"


DOCUMENT_FQN = "Aspose.Pdf.Document"
SYMBOL_SCOPE = Scope(family=FAMILY, platform=PLATFORM)


def test_lookup_disconnect_symbol_match_never_carries_known_defects(registry_dir: Path) -> None:
    """The investigation finding, as a regression test: a method-scoped known-defect that DOES
    surface through ``get_symbol`` (KD2, proven first) never surfaces through ``lookup``'s own
    ``symbols`` - because ``SymbolMatch`` has no ``known_defects`` field at all, unlike
    ``SymbolSignature``. This pins the disconnect so a future card that wires it (by touching
    ``search_symbols.SymbolMatch`` and ``test_lookup_forgiveness.py`` together, both outside this
    card's write_paths) updates this test deliberately rather than by accident."""
    _write_registry(
        registry_dir,
        [
            _defect(
                "PDF-CPP-009",
                kind="method",
                ref=DOCUMENT_FQN,
                package_versions=["26.7.0"],
                summary="A method-scoped defect that get_symbol discloses.",
            )
        ],
    )
    store = _InMemoryStore((_symbol_body(DOCUMENT_FQN, methods=("Save(path: string) -> void",)),))

    # First prove get_symbol (KD2, G3/TC-639) DOES disclose it - the baseline this investigates.
    signature = get_symbol(store, SYMBOL_SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]
    assert isinstance(signature, SymbolSignature)
    assert len(signature.known_defects) == 1
    assert "A method-scoped defect that get_symbol discloses." in signature.known_defects[0]

    # Now prove lookup's own symbol path carries no such field at all for the identical fqn/scope -
    # SymbolMatch (the type lookup's `symbols` field holds) simply has no known_defects attribute.
    result = lookup(store, SYMBOL_SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]
    if isinstance(result, TaskAnswer):
        symbols = result.symbols
    elif isinstance(result, list):
        symbols = tuple(result)
    else:
        symbols = ()
    assert symbols, "the query names an indexed symbol; lookup must return it in some shape"
    for match in symbols:
        assert not hasattr(match, "known_defects")
    assert not hasattr(TaskAnswer(scope=SYMBOL_SCOPE), "known_defects")
