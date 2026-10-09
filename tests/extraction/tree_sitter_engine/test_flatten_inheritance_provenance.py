"""Tests for the TC-261 fix to ``_flatten_inheritance()`` (C3 of the third independent
recon, 2026-10-08; see docs/DECISION_LOG.md's "2026-10-08 - A third independent recon
claims C1-C8" entry, the "C3" heading).

ROOT CAUSE: ``_flatten_inheritance()`` walks a class's full transitive ``bases`` chain and
physically copies every ancestor's methods/properties into every descendant's own
``methods``/``properties`` list, with no marker distinguishing a copied (inherited) entry
from a genuinely locally-declared one. Confirmed live against pdf/cpp's real
``PopupAnnotation -> Annotation -> BaseParagraph`` chain: ``PopupAnnotation``'s real header
declares exactly 5 members, but the extracted fixture's ``PopupAnnotation`` entry also
carried every one of ``Annotation``'s ~30 methods and every one of ``BaseParagraph``'s ~15
methods - including ``BaseParagraph``'s own constructor and destructor, which
``PopupAnnotation`` obviously never declares. Each copied method's own file/line metadata
was individually accurate, but nothing in the data itself told a caller a copied member was
not the child's own.

The fix adds an ``"inherited_from"`` key, set only on the COPIED dict (never on the
original ancestor's own entry, and never on a genuinely locally-declared entry), rooted to
the real ORIGINAL declaring class even through a multi-level chain: a grandparent's member
copied transitively into a grandchild must record the grandparent's own identity, not the
intermediate parent it was copied through on the way.

The synthetic fixture below mirrors the real PopupAnnotation/Annotation/BaseParagraph shape
closely enough to be a faithful regression: a three-level chain, each level declaring its
own method and property, plus BaseParagraph's own constructor (name equals return_type, the
same shape the real audit flagged) to prove that entry is correctly rooted to BaseParagraph
and not to Annotation.
"""

from __future__ import annotations

from foss_mcp.extraction.tree_sitter_engine.api_surface import _flatten_inheritance

GRANDPARENT_IMPORT = "Aspose::Pdf::Annotations::BaseParagraph"
PARENT_IMPORT = "Aspose::Pdf::Annotations::Annotation"
CHILD_IMPORT = "Aspose::Pdf::Annotations::PopupAnnotation"


def _three_level_chain() -> list[dict]:
    """A fresh grandparent/parent/child triple each time - _flatten_inheritance mutates its
    input in place, so every test gets its own untouched fixture."""
    grandparent = {
        "name": "BaseParagraph",
        "class_import": GRANDPARENT_IMPORT,
        "kind": "class",
        "bases": [],
        "methods": [
            # A constructor, encoded with name == return_type == the class's own short name -
            # the exact shape the audit's own C1 scan separately flagged as a false positive
            # for this mechanism (see DECISION_LOG.md's correction paragraph). Real, genuinely
            # declared by BaseParagraph itself, so it must never carry "inherited_from" here.
            {"name": "BaseParagraph", "params": [], "return_type": "BaseParagraph"},
            {"name": "GetIsInLine", "params": [], "return_type": "bool"},
        ],
        "properties": [
            {"name": "IsInLine", "type": "bool", "writable": True},
        ],
    }
    parent = {
        "name": "Annotation",
        "class_import": PARENT_IMPORT,
        "kind": "class",
        "bases": ["BaseParagraph"],
        "methods": [
            {"name": "GetRectangle", "params": [], "return_type": "Rectangle"},
        ],
        "properties": [
            {"name": "Rect", "type": "Rectangle", "writable": True},
        ],
    }
    child = {
        "name": "PopupAnnotation",
        "class_import": CHILD_IMPORT,
        "kind": "class",
        "bases": ["Annotation"],
        "methods": [
            {"name": "Accept", "params": [], "return_type": "void"},
        ],
        "properties": [
            {"name": "Open", "type": "bool", "writable": True},
        ],
    }
    return [grandparent, parent, child]


def _by_name(entries: list[dict], name: str) -> dict:
    (match,) = (e for e in entries if e["name"] == name)
    return match


def test_a_genuinely_own_declared_method_never_carries_inherited_from() -> None:
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["name"] == "PopupAnnotation")

    accept = _by_name(child["methods"], "Accept")
    assert "inherited_from" not in accept


def test_a_method_copied_from_the_direct_parent_is_rooted_to_the_parent() -> None:
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["name"] == "PopupAnnotation")

    get_rectangle = _by_name(child["methods"], "GetRectangle")
    assert get_rectangle.get("inherited_from") == PARENT_IMPORT


def test_a_method_copied_transitively_from_the_grandparent_is_rooted_to_the_grandparent_not_the_parent() -> (
    None
):
    """The multi-level case TC-261's card requires verifying directly: GetIsInLine only
    exists on BaseParagraph (the grandparent). It reaches PopupAnnotation (the grandchild)
    by being copied into Annotation first, then copied again from Annotation into
    PopupAnnotation - its "inherited_from" must still read BaseParagraph, the class that
    actually declares it, never Annotation, the intermediate class it passed through."""
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["name"] == "PopupAnnotation")

    get_is_in_line = _by_name(child["methods"], "GetIsInLine")
    assert get_is_in_line.get("inherited_from") == GRANDPARENT_IMPORT
    assert get_is_in_line.get("inherited_from") != PARENT_IMPORT


def test_a_transitively_copied_constructor_is_rooted_to_the_grandparent() -> None:
    """BaseParagraph's own constructor (name == return_type == "BaseParagraph") must be
    rooted to BaseParagraph once it reaches the grandchild, exactly like any other
    transitively-copied member - this is the specific shape the audit's own correction
    paragraph called out as a real, genuinely-declared member, not a C1-style fabrication."""
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["name"] == "PopupAnnotation")

    ctor = _by_name(child["methods"], "BaseParagraph")
    assert ctor.get("inherited_from") == GRANDPARENT_IMPORT


def test_properties_behave_the_same_way_as_methods() -> None:
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["name"] == "PopupAnnotation")

    own_prop = _by_name(child["properties"], "Open")
    assert "inherited_from" not in own_prop

    direct_parent_prop = _by_name(child["properties"], "Rect")
    assert direct_parent_prop.get("inherited_from") == PARENT_IMPORT

    grandparent_prop = _by_name(child["properties"], "IsInLine")
    assert grandparent_prop.get("inherited_from") == GRANDPARENT_IMPORT


def test_the_ancestors_own_original_entries_never_gain_the_key() -> None:
    """ "inherited_from" is set on the COPIED dict only - the real ancestor's own original
    entry in its own methods/properties list must never be mutated to carry it, since that
    entry genuinely IS a local declaration from that class's own point of view."""
    classes = _three_level_chain()
    _flatten_inheritance(classes)
    grandparent = next(c for c in classes if c["name"] == "BaseParagraph")
    parent = next(c for c in classes if c["name"] == "Annotation")

    assert "inherited_from" not in _by_name(grandparent["methods"], "GetIsInLine")
    assert "inherited_from" not in _by_name(grandparent["methods"], "BaseParagraph")
    assert "inherited_from" not in _by_name(grandparent["properties"], "IsInLine")
    # Annotation's own genuinely-declared GetRectangle/Rect must also stay untagged - only
    # PopupAnnotation's COPY of them (asserted above) carries "inherited_from".
    assert "inherited_from" not in _by_name(parent["methods"], "GetRectangle")
    assert "inherited_from" not in _by_name(parent["properties"], "Rect")


def test_a_child_declared_override_takes_precedence_and_is_never_tagged() -> None:
    """When a child declares its own method/property with the same dedup key as a parent's,
    flattening must not add a duplicate copy, and the existing, genuinely-declared entry must
    never gain "inherited_from" - the existing dedup-by-(name, param-type-signature) behavior
    (ST-013 / Node.ToString) must remain completely unaffected by this card."""
    classes = _three_level_chain()
    child = next(c for c in classes if c["name"] == "PopupAnnotation")
    # PopupAnnotation declares its own GetRectangle override with the same signature as
    # Annotation's.
    child["methods"].append({"name": "GetRectangle", "params": [], "return_type": "Rectangle"})

    _flatten_inheritance(classes)

    matches = [m for m in child["methods"] if m["name"] == "GetRectangle"]
    assert len(matches) == 1
    assert "inherited_from" not in matches[0]


# ---------------------------------------------------------------------------
# TC-293: short-name collision between an empty re-export shell and a real
# definition (found while verifying TC-281/TC-288 against 3d/python's real
# extraction; see docs/DECISION_LOG.md's 2026-10-09 entry).
#
# ROOT CAUSE: _flatten_inheritance()'s by-name index resolves a `bases` entry
# written as a bare, unqualified name (the overwhelmingly common way source
# code references its own base class) through a short-name fallback built
# first-writer-wins across the full class list. A Python package following
# the common "class defined in its own submodule, re-exported at the package
# level" convention produces two entries sharing one bare name: an empty
# re-export shell (class_import e.g. "pkg.Base", methods=[]) and the real
# definition (class_import e.g. "pkg.Base.Base", real methods/properties).
# Because the shell's shorter class_import sorts first in the type list, it
# used to claim the short-name slot before the real definition was ever
# processed, so every subclass's unqualified `bases` reference resolved
# straight to the empty shell - live-confirmed against 3d/python's real
# extraction, where _flatten_inheritance() copied zero members anywhere in
# the entire 697-type pilot as a result.
# ---------------------------------------------------------------------------

SHELL_IMPORT = "pkg.Base"
REAL_DEFINITION_IMPORT = "pkg.Base.Base"
COLLISION_CHILD_IMPORT = "pkg.Child.Child"


def _shell_collision_fixture() -> list[dict]:
    """Mirrors TC-288's own isolated repro and the real 3d/python shape exactly:
    an empty re-export shell and the real definition share the bare name "Base",
    and a child references the base by its bare, unqualified name - the way
    Python source (and most other languages') actually writes a base-class
    reference."""
    shell = {
        "name": "Base",
        "class_import": SHELL_IMPORT,
        "kind": "class",
        "bases": [],
        "methods": [],
        "properties": [],
    }
    real_definition = {
        "name": "Base",
        "class_import": REAL_DEFINITION_IMPORT,
        "kind": "class",
        "bases": [],
        "methods": [{"name": "find_property", "params": [], "return_type": "object"}],
        "properties": [{"name": "name", "type": "str", "writable": True}],
    }
    child = {
        "name": "Child",
        "class_import": COLLISION_CHILD_IMPORT,
        "kind": "class",
        "bases": ["Base"],
        "methods": [],
        "properties": [],
    }
    # List order matters: the shell's shorter class_import sorts before the
    # real definition's, exactly as it does in the real, live 3d/python
    # extraction's own (alphabetically sorted) type list.
    return [shell, real_definition, child]


def test_a_short_name_collision_between_an_empty_shell_and_a_real_definition_resolves_to_the_real_definition() -> (
    None
):
    """The fix this card adds: on a short-name collision, prefer whichever
    candidate actually carries structured data over an empty shell, so a
    bare-name base reference resolves to the real definition's real members,
    correctly tagged "inherited_from" the real definition - never the empty
    shell, and never left uncopied the way it was before this fix."""
    classes = _shell_collision_fixture()
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["class_import"] == COLLISION_CHILD_IMPORT)

    method = _by_name(child["methods"], "find_property")
    assert method.get("inherited_from") == REAL_DEFINITION_IMPORT

    prop = _by_name(child["properties"], "name")
    assert prop.get("inherited_from") == REAL_DEFINITION_IMPORT


def test_the_empty_shells_own_entry_is_never_mutated_by_the_collision_fix() -> None:
    """The shell itself is never resolved as anyone's parent (nothing's `bases`
    names its own class_import), so it must stay exactly as empty as it
    started - the fix changes which entry wins the short-name slot, not the
    shell's own, independently-recorded data."""
    classes = _shell_collision_fixture()
    _flatten_inheritance(classes)
    shell = next(c for c in classes if c["class_import"] == SHELL_IMPORT)

    assert shell["methods"] == []
    assert shell["properties"] == []


def test_a_short_name_collision_between_two_genuinely_non_empty_candidates_keeps_first_writer_wins() -> None:
    """When BOTH same-named candidates carry real, structured data (a genuine
    ambiguity between two actually-different classes that merely share a bare
    name across namespaces - confirmed live as a real shape in pdf_cpp/
    pdf_java/pdf_typescript's own committed fixtures while checking this
    card's tree-sitter-language question), this card does not invent a new
    tie-break rule: the existing first-writer-wins behavior (whichever
    candidate is processed first) is left completely unchanged."""
    classes = [
        {
            "name": "Rect",
            "class_import": "ns_a.Rect",
            "kind": "class",
            "bases": [],
            "methods": [{"name": "area", "params": [], "return_type": "float"}],
            "properties": [],
        },
        {
            "name": "Rect",
            "class_import": "ns_b.Rect",
            "kind": "class",
            "bases": [],
            "methods": [{"name": "perimeter", "params": [], "return_type": "float"}],
            "properties": [],
        },
        {
            "name": "User",
            "class_import": "pkg.User",
            "kind": "class",
            "bases": ["Rect"],
            "methods": [],
            "properties": [],
        },
    ]
    _flatten_inheritance(classes)
    user = next(c for c in classes if c["class_import"] == "pkg.User")

    method_names = {m["name"] for m in user["methods"]}
    assert method_names == {"area"}, method_names
    assert "perimeter" not in method_names


def test_the_full_class_import_keyed_resolution_path_is_unaffected_by_a_short_name_collision() -> None:
    """A `bases` entry written as the FULL, exact class_import (not a bare
    short name) must still resolve through the untouched, unconditional
    `by_name[key] = c` qualified-key path - even when the very same short
    name is, elsewhere in the same class list, involved in the collision this
    card fixes. Explicitly asking for the shell by its own exact class_import
    must still yield the shell, proving the two resolution paths (qualified-
    key vs short-name-fallback) are independent and the qualified path was
    never touched by this card."""
    classes = [
        {
            "name": "Base",
            "class_import": SHELL_IMPORT,
            "kind": "class",
            "bases": [],
            "methods": [],
            "properties": [],
        },
        {
            "name": "Base",
            "class_import": REAL_DEFINITION_IMPORT,
            "kind": "class",
            "bases": [],
            "methods": [{"name": "find_property", "params": [], "return_type": "object"}],
            "properties": [],
        },
        {
            "name": "Child",
            "class_import": "pkg.Other.Child",
            "kind": "class",
            # References the shell by its OWN exact, full class_import, not the
            # bare short name "Base" that the collision fix affects.
            "bases": [SHELL_IMPORT],
            "methods": [],
            "properties": [],
        },
    ]
    _flatten_inheritance(classes)
    child = next(c for c in classes if c["class_import"] == "pkg.Other.Child")

    assert child["methods"] == []
