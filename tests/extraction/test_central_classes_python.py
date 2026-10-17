"""Offline guard: the Python pilots TC-401 re-extracted carry their pinned commit, their own
central class, every example-used class the full extraction actually defines, and the
constructor/property facts TC-393/TC-473 exist to recover - all read from committed files only,
no network, no clone.

TC-401 re-extracted exactly these 11 Python fixtures at their pinned library commits with the
corrected extractor and selection (TC-393's constructor/parameter/property fixes, TC-394's
must-keep selection, TC-426/TC-429/TC-473's further Python-surface recoveries). This test closes
the same hole ``test_central_classes_indexed.py`` closes for slides/python and words/net: the
extraction proof harnesses pick their probe from the already-cut type list, so a cut that
silently drops a pilot's central class is invisible to them without a fixture-level guard.

``barcode_python`` and ``page_python`` were also re-extracted by this card (their write_paths
and checks are identical) but name no central class in the card's own instructions, so they are
covered here only by the pinned-commit and no-test-file-entry/example-coverage checks, not by
``_CENTRAL``.

``tex_python``'s real public surface is almost entirely unresolved at the pinned commit (a
genuine upstream Python syntax error in ``presentation/__init__.py`` - see
``test_tex_python_extraction.py``'s own module docstring): ``TeXJob``/``TeXOptions`` never
resolve to a kept type here, so per this card's own instructions ("if the upstream extraction at
the pinned commit genuinely has no such type, remove it from the expected set and report it -
never invent one") neither name is asserted here. Reported: this is a genuine upstream defect at
the pinned commit, not an extractor or selection defect this card can fix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction.run_extraction import _is_test_entry, names_used_in_examples

PILOTS = [
    "3d_python",
    "barcode_python",
    "cells_python",
    "email_python",
    "html_python",
    "note_python",
    "page_python",
    "pdf_python",
    "tex_python",
    "words_python",
]

_ROOT = Path(__file__).parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures"
_VALUES = _ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# The central class(es) every example of the pilot opens with, asserted by name - not only via
# the page scan. barcode_python and page_python name none in this card's own instructions, so
# they are intentionally absent here. tex_python's TeXJob/TeXOptions are intentionally absent
# too (see the module docstring): this card's real, upstream-genuine extraction never resolves
# either name to a kept type at the pinned commit.
_CENTRAL: dict[str, tuple[str, ...]] = {
    "3d_python": ("Scene",),
    "cells_python": ("Workbook",),
    "email_python": ("MapiMessage",),
    "html_python": ("Document",),
    "note_python": ("Document",),
    "pdf_python": ("Document",),
    "words_python": ("Document",),
}

_CENTRAL_PAIRS = [(pilot, name) for pilot, names in _CENTRAL.items() for name in names]


def _fixture(pilot: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _furnished_page(pilot: str) -> Path:
    return _FIXTURES / "furnished" / pilot / "pages" / "_index.md"


def _pinned_commit(pilot: str) -> str:
    values = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    wanted = f"/app/fixtures/{pilot}/api_surface.json"
    for entry in values["ingestion"]["pilots"]:
        if entry.get("apiSurface") == wanted:
            return str(entry["library"]["commit"])
    raise AssertionError(f"no pilot in values.yaml has apiSurface {wanted}")


def _has_members(entry: dict[str, Any]) -> bool:
    return bool(entry.get("methods") or entry.get("properties") or entry.get("enum_members"))


# Python stdlib/typing names that routinely appear bare inside a real annotation string
# (e.g. "str | Path | bytes | BinaryIO | None") without ever being a type THIS library
# defines - hand-confirmed for "Path" against pdf_python's and note_python's own real
# "str | Path | ..." parameter annotations (pathlib.Path, never a library class). Excluded
# so a stdlib/typing token embedded in a real annotation is never mistaken for a real,
# silently-cut library type.
_NEVER_LIBRARY_DEFINED = frozenset(
    {
        "Path",
        "Optional",
        "Union",
        "List",
        "Dict",
        "Set",
        "Tuple",
        "Any",
        "Callable",
        "Type",
        "BinaryIO",
        "TextIO",
        "Iterable",
        "Iterator",
        "Sequence",
        "Mapping",
        # re-extracted by TC-550: pdf_python now keeps InvalidOperationException(RuntimeError), so the builtin
        # base is referenced by a kept entry; it is a Python builtin, never defined by the library.
        "RuntimeError",
    }
)


def _defined_names(pilot: str) -> set[str]:
    """Names used in the pilot's examples that some kept fixture entry references in a base, a
    method return/parameter type or a property type (so they are real types of the library,
    whether or not they themselves survived the selection cut) - the same inference
    ``test_central_classes_indexed.py`` already uses, generalized to this card's own fixtures.
    """
    data = _fixture(pilot)
    referenced: set[str] = set()
    for entry in data["types"]:
        referenced.update(str(base) for base in entry.get("bases") or [])
        for method in entry.get("methods") or []:
            referenced.add(str(method.get("return_type") or ""))
            referenced.update(str(p.get("type") or "") for p in method.get("params") or [])
            referenced.update(str(t) for t in method.get("param_types") or [])
        for prop in entry.get("properties") or []:
            referenced.add(str(prop.get("type") or ""))
    words: set[str] = set()
    for text in referenced:
        # A bare name only: a namespace segment (``Drawing`` in ``Drawing.Shape``) is not a type.
        words.update(re.findall(r"(?<![\w.])[A-Za-z_]\w*(?![\w.])", text))
    words -= _NEVER_LIBRARY_DEFINED
    return words & set(names_used_in_examples(_furnished_page(pilot)))


def test_the_pilot_list_covers_the_eleven_refreshed_pilots() -> None:
    assert set(PILOTS) == {
        "3d_python",
        "barcode_python",
        "cells_python",
        "email_python",
        "html_python",
        # re-pinned by TC-551: <removed pilot> -> note_python
        "note_python",
        "page_python",
        "pdf_python",
        "tex_python",
        "words_python",
    }


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_is_extracted_at_the_pinned_commit(pilot: str) -> None:
    pinned = _pinned_commit(pilot)
    assert re.fullmatch(r"[0-9a-f]{40}", pinned), pinned
    assert _fixture(pilot)["source_commit"] == pinned


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_entry_comes_from_a_test_file(pilot: str) -> None:
    offenders = [entry["class_import"] for entry in _fixture(pilot)["types"] if _is_test_entry(entry)]
    assert not offenders, f"{pilot}: entries read from test sources: {offenders[:5]}"


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_class_the_examples_use_is_in_the_fixture(pilot: str) -> None:
    data = _fixture(pilot)
    present = {entry["name"] for entry in data["types"]}
    used = names_used_in_examples(_furnished_page(pilot))
    assert used, f"{pilot}: the furnished page names no classes - the scan is broken"
    missing = sorted(name for name in used if name in _defined_names(pilot) and name not in present)
    assert not missing, f"{pilot}: example classes cut from the fixture: {missing}"


@pytest.mark.parametrize("pilot,central", _CENTRAL_PAIRS)
def test_the_central_class_is_present_and_has_members(pilot: str, central: str) -> None:
    # The card's own requirement (2): present among the fixture's type names.
    entries = [entry for entry in _fixture(pilot)["types"] if entry["name"] == central]
    assert entries, f"{pilot}: {central} is not in the committed fixture"
    assert any(_has_members(entry) for entry in entries), f"{pilot}: {central} carries no members"


def test_cells_python_workbook_has_a_constructor_with_parameters() -> None:
    # TC-393: __init__ is the constructor, every declared parameter kept. Workbook's own,
    # member-bearing entry (not its member-less package-level re-export shell) must carry it.
    data = _fixture("cells_python")
    entries = [e for e in data["types"] if e["name"] == "Workbook" and _has_members(e)]
    assert entries, "cells_python: no member-bearing Workbook entry in the fixture"
    ctor = next(
        (m for e in entries for m in e["methods"] if m["name"] == "__init__" and m.get("is_constructor")),
        None,
    )
    assert ctor is not None, "cells_python: Workbook carries no __init__ constructor"
    assert ctor["params"], "cells_python: Workbook's __init__ carries no parameters"


def test_words_python_document_has_a_constructor_with_parameters() -> None:
    # Same TC-393 guarantee for words/python's own central class.
    data = _fixture("words_python")
    entries = [e for e in data["types"] if e["name"] == "Document" and _has_members(e)]
    assert entries, "words_python: no member-bearing Document entry in the fixture"
    ctor = next(
        (m for e in entries for m in e["methods"] if m["name"] == "__init__" and m.get("is_constructor")),
        None,
    )
    assert ctor is not None, "words_python: Document carries no __init__ constructor"
    assert ctor["params"], "words_python: Document's __init__ carries no parameters"


def test_email_python_has_at_least_one_property() -> None:
    # TC-393/TC-473: an annotated @property getter (or a self.<name> assignment in __init__)
    # is recovered as a property - email_python's MapiMessage alone carries dozens.
    data = _fixture("email_python")
    total_properties = sum(len(entry.get("properties") or []) for entry in data["types"])
    assert total_properties > 0, "email_python: no entry carries a single property"
