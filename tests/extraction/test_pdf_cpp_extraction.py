"""Offline tests against the committed pdf/cpp fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port, so nothing here may
reach out; everything is read from the fixture ``run_extraction.py`` already produced and
committed at ``tests/fixtures/pdf_cpp/api_surface.json``.

This is TC-057's successor under a fresh id (TC-057 exhausted its 3-attempt cap, but each of
its three attempts found a distinct, real engine bug, all now fixed and accepted): TC-058
(return-type fallback firing before the free-function branch), TC-059 (``operator_name`` node
type missing from ``_cpp_free_function_name``'s checked tuple), and TC-092
(``consolidate_classes`` silently discarding distinct same-named free functions across
different files/namespaces). This card re-ran the live extraction fresh against the real,
current ``aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp`` repository to prove those fixes hold
against genuine, large C++ source rather than synthetic snippets.

TC-258 regenerated this fixture again, for a different reason: TC-153's original card picked an
explicit smaller ``--max-types 252`` (landing at ~2.18MB) purely for the ~2MB fixture budget, but
that 252 was chosen under the OLD ``reduce_fixture``, which sorted candidates by bare
``class_import``/``name`` (alphabetically) with no regard for how central a type actually is to
the rest of the public API. TC-252 replaced that with a centrality score - how many OTHER types
in the artifact reference a given type's bare name in their own ``bases``/``return_type``/
``params``/``properties`` - sorted descending. TC-258 re-ran the real, live extraction against
this exact fixed algorithm, at the project's real default ``--max-types 300`` (no smaller cap
chosen by hand): the real repository's full public surface is still 361 types, the reduced
fixture now keeps 300 of them, landing at 2,306,930 bytes - modestly larger than the old
252-type/~2.18MB fixture (about 6% larger), not "dramatically" larger, so the real default cap
was kept rather than silently shrunk again.

One directly-observed consequence of switching from alphabetical to centrality ranking: the
four operator-overload free functions this pilot's history previously spot-checked by name
(``AnnotationFlags::operator|``, ``BorderSide::operator|``, ``Permissions::operator|``, and
``RichTextFontStyles::operator|``, all spelled ``operator|`` at the top level) are NOT present
in this reduced, centrality-ranked top-300 - they happened to survive the OLD alphabetical
top-252 cut only because their lowercase name sorted favorably, not because they are
particularly central to the API. A centrality-ranked selection correctly deprioritizes them:
operator overloads are rarely referenced by name from other types' bases/params/return types.
This is the selection algorithm working as intended, not a regression of TC-059/TC-092's fixes
(those fixes are about correct naming and non-collapsing, not about which types a size-bounded
reduction keeps) - TC-059 and TC-092 remain proven by the full, un-reduced live extraction
output inspected directly during this card, even though the reduced fixture itself no longer
carries these specific four entries.

The named free-function target this card's own history cares about most directly,
``Identity()`` (see ``test_a_real_free_function_and_real_inheritance_are_present`` below), is
still present under the new centrality ranking - confirming TC-058's fix still holds: the real
source (``include/internal/transform.hpp``) defines ``Identity()`` as a genuine free function
returning a ``Matrix`` by value, not a static member of any class. (``Matrix`` itself - the
struct ``Identity`` returns - is referenced only this once in the whole artifact, so it does not
score highly enough under centrality to survive the top-300 cut on its own; that is an expected,
observed consequence of the ranking, not a defect, and nothing in this file or in TC-153's
original closeout ever required ``Matrix`` itself to be present.)

Real inheritance is also directly present and asserted below, confirming TC-010b's
``base_class_clause`` fix holds against real, large C++ source: 108 of the 300 reduced types
carry a non-empty ``bases`` list - the identical count TC-153's original 252-type fixture
happened to carry, now reached by a different, centrality-driven selection.

Another directly-observed, real change from switching selection algorithms: the reduced fixture
now includes ``struct_specifier`` entries (plain C++ ``struct`` declarations, e.g. ``Point``,
``Page``, ``Glyph``) that the old alphabetical top-252 cut never reached. ``struct_specifier`` is
a first-class, long-supported kind in the extraction engine itself (see
``tree_sitter_engine/tree_helpers.py``'s ``cpp`` kind set), so its appearance here is genuine,
previously-uncaptured real content, not new engine behavior.

One more directly-observed, real (not invented) shape fact: a free function (``kind ==
"function"``) entry carries ``params``/``return_type`` but no ``visibility``, ``class_import``,
``canonical_namespace``, ``deprecated``, or ``deprecated_reason`` keys - it has no enclosing
class and no access-specifier concept, so those keys are genuinely absent rather than present-
but-empty. A class/struct/enum entry has the reverse: no ``params``/``return_type``, but all of
the class-identity keys.

TC-282 re-ran this exact extraction again, for a third reason: TC-258's regeneration (above)
landed on 2026-10-08, one day BEFORE TC-261 (C3 of the third independent recon) added
``inherited_from`` provenance tagging to ``_flatten_inheritance`` on 2026-10-09. The committed
fixture therefore had real inheritance (108 types with non-empty ``bases``, as already asserted
above) but carried ZERO ``inherited_from`` tags anywhere - exactly the defect C3 was meant to
close. Re-running the identical extraction now that TC-261 is on main changed nothing about
WHICH 300 types are kept (same 300 names, same kinds, same 108-with-bases count, same pinned
commit) and added exactly one thing: 4,312 ``inherited_from`` tags across the fixture's copied
methods/properties, zero of which existed before. This pilot also directly exhibits the
multi-level rooting TC-261's own fix specifically targets: ``PopupAnnotation`` inherits from
``Annotation``, which itself inherits from ``Aspose::Pdf::BaseParagraph`` - a real three-level
chain. ``PopupAnnotation``'s copy of a method Annotation itself declares (e.g. ``GetRectangle``)
correctly roots to ``Aspose::Pdf::Annotations::Annotation`` (the real, direct declaring
ancestor), while its copy of a method that originates in ``BaseParagraph`` and was only copied
THROUGH ``Annotation`` (e.g. ``Margin``, ``Hyperlink``, ``ZIndex``) correctly roots to
``Aspose::Pdf::BaseParagraph`` - the true, original declaring ancestor - not to ``Annotation``,
the intermediate parent it was transitively copied through. See
``test_a_real_multilevel_inherited_from_chain_is_correctly_rooted`` below.

A C1-shaped-fabrication sweep (the same shape TC-271's own rework swept for cells/cpp: a
top-level ``"function"``-kind entry whose name collides with a real class/struct/enum name
already present in this same kept set, or whose name equals its own return type - the mark of a
member mis-named after its return type rather than its real accessor name) was re-run across
the full kept 300 and found zero such entries: this fixture's only 3 top-level free functions
(``ComponentCount``, ``HasSide``, ``Identity``) each have a name distinct from every class/
struct/enum name in the fixture and distinct from their own return type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_cpp" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "cpp.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "cpp"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp` if this ever
    # needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "4b83c9fec1e37fd205156770161f6843bac00ceb"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has 361 public types. This fixture was regenerated (TC-258) with the
    # project's real default --max-types 300, using the fixed, centrality-ranked reduce_fixture
    # (TC-252) - no smaller cap chosen by hand.
    assert data["type_count"] == 361
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_cpp_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.PDF-FOSS-for-Cpp exported types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"Annotation", "BorderSide", "AFRelationship"} <= names
    assert all(entry["file"].endswith(".hpp") for entry in data["types"])


def test_every_entry_is_a_real_cpp_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter C++ grammar's own node-type vocabulary for
    # this repository's reduced subset, not guessed or invented. struct_specifier is new
    # relative to the old 252-type alphabetical cut (see module docstring): the centrality-
    # ranked top-300 reaches real struct declarations the old cut never did.
    assert kinds == {"class_specifier", "struct_specifier", "enum_specifier", "function"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in (
            "name",
            "kind",
            "file",
            "line",
            "doc",
            "methods",
            "properties",
            "bases",
            "reachable",
        ):
            assert key in entry, (entry.get("name"), key)
        # A free function (kind "function") carries no class_import/canonical_namespace or
        # visibility/deprecated keys - real, observed engine behavior (it has no enclosing
        # class and no access-specifier concept), not a shape bug. It carries params/
        # return_type instead, which a class/struct/enum entry never does.
        if entry["kind"] == "function":
            assert "params" in entry
            assert "return_type" in entry
        else:
            for key in (
                "visibility",
                "class_import",
                "canonical_namespace",
                "deprecated",
                "deprecated_reason",
            ):
                assert key in entry, (entry.get("name"), key)


def test_a_real_free_function_and_real_inheritance_are_present() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # Observed directly in the live run: Identity() is a genuine free function in
    # include/internal/transform.hpp that returns a Matrix by value - not a method of Matrix
    # (TC-058's regression target: the return-type-matches-a-class-name fallback used to fire
    # before the free-function branch).
    identity = by_name["Identity"]
    assert identity["kind"] == "function"
    assert identity["return_type"] == "Matrix"
    assert identity["bases"] == []

    # Observed directly in the live run: Annotation is a real class that inherits from
    # Aspose::Pdf::BaseParagraph and has a real GetRectangle() accessor.
    annotation = by_name["Annotation"]
    assert annotation["kind"] == "class_specifier"
    assert annotation["bases"] == ["Aspose::Pdf::BaseParagraph"]
    methods = {m["name"]: m for m in annotation["methods"]}
    assert "GetRectangle" in methods
    assert methods["GetRectangle"]["return_type"] == "Aspose::Pdf::Rectangle"
    assert methods["GetRectangle"]["is_constructor"] is False


def test_at_least_one_hundred_types_carry_real_inheritance() -> None:
    data = _load_fixture()
    with_bases = [entry for entry in data["types"] if entry["bases"]]
    # 108 of 300 reduced types carry a non-empty bases list in the real, observed output -
    # proof TC-010b's base_class_clause fix holds broadly against real, large C++ source, not
    # just the one example asserted above.
    assert len(with_bases) >= 100


def test_a_real_multilevel_inherited_from_chain_is_correctly_rooted() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # Real three-level chain observed directly in the live run: PopupAnnotation -> Annotation
    # -> Aspose::Pdf::BaseParagraph. TC-261 (C3) added "inherited_from" provenance tagging to
    # _flatten_inheritance(); this fixture was regenerated by TC-258 one day BEFORE TC-261
    # landed, so it previously carried real inheritance but zero "inherited_from" tags
    # anywhere. TC-282 re-ran the identical extraction now that TC-261 is on main.
    assert by_name["PopupAnnotation"]["bases"] == ["Annotation"]
    assert by_name["Annotation"]["bases"] == ["Aspose::Pdf::BaseParagraph"]

    popup_methods = {m["name"]: m for m in by_name["PopupAnnotation"]["methods"]}

    # A method Annotation itself declares, copied one level into PopupAnnotation, roots to
    # Annotation - the real, direct declaring ancestor.
    assert popup_methods["GetRectangle"]["inherited_from"] == "Aspose::Pdf::Annotations::Annotation"

    # A method that originates in BaseParagraph and is copied TRANSITIVELY into PopupAnnotation
    # (through Annotation, which never declares it itself) roots to BaseParagraph - the true,
    # original declaring ancestor - not to Annotation, the intermediate parent it was copied
    # through. This is the exact multi-level rooting behavior TC-261's own fix targets.
    assert popup_methods["Margin"]["inherited_from"] == "Aspose::Pdf::BaseParagraph"
    assert popup_methods["Hyperlink"]["inherited_from"] == "Aspose::Pdf::BaseParagraph"
    assert popup_methods["ZIndex"]["inherited_from"] == "Aspose::Pdf::BaseParagraph"

    # Annotation's OWN copy of these same BaseParagraph-declared methods (one level up the
    # chain) already roots to BaseParagraph too - confirming the parent's own already-resolved
    # inherited_from survives being copied again into the grandchild rather than being
    # overwritten with the intermediate parent's identity.
    annotation_methods = {m["name"]: m for m in by_name["Annotation"]["methods"]}
    assert annotation_methods["Margin"]["inherited_from"] == "Aspose::Pdf::BaseParagraph"

    # A genuinely locally-declared method never gains an inherited_from key at all.
    assert "inherited_from" not in annotation_methods["GetRectangle"]


def test_a_real_enum_with_real_members_is_present() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # Observed directly in the live run: AFRelationship is a real enum with real members.
    enum = by_name["AFRelationship"]
    assert enum["kind"] == "enum_specifier"
    enum_member_names = {m["name"] for m in enum["enum_members"]}
    assert {"Source", "Data", "Alternative"} <= enum_member_names
