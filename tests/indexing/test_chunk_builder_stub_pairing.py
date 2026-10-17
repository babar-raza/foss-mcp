"""TC-541 (G3/REQ-G3-008): an empty Python class or enum alias is rendered with its one definition.

A Python library re-exports its classes from several modules and the extractor lists every spelling as
its own entry. TC-522 paired a shell with its definition only inside the shell's own package or by a
``reexport_of``; ``_reexport_pairs`` now also pairs a member-less ``class``/``enum`` of a ``.py`` file with
the ONE member-bearing entry of the same name and kind anywhere in the fixture. Synthetic fixtures built
by hand, plus the 12 committed Python fixtures. Fully offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing import chunk_builder
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
PYTHON_FIXTURES = sorted(
    directory.name
    for directory in FIXTURES_DIR.iterdir()
    if directory.is_dir()
    and directory.name.endswith("_python")
    and (directory / "api_surface.json").is_file()
)

_FQN_LINE = re.compile(r"^FQN: (.+)$", re.MULTILINE)
_KIND_LINE = re.compile(r"^Kind: (.+)$", re.MULTILINE)
_QUALIFIER_SUFFIX = re.compile(r"[@#]\d+$")
_FINAL_SEGMENT = re.compile(r"\.|::")


def _method(name: str = "m") -> dict[str, Any]:
    return {"name": name, "params": [], "return_type": ""}


def _entry(
    name: str,
    class_import: str,
    file: str,
    *,
    kind: str = "class",
    members: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "file": file,
        "class_import": class_import,
        "bases": [],
        "methods": [],
        "properties": [],
        **extra,
    }
    if members:
        if kind == "enum":
            entry["enum_members"] = [{"name": "ALPHA", "value": "1"}]
        else:
            entry["methods"] = [_method("save")]
    return entry


def _fixture(types: list[dict[str, Any]]) -> dict[str, Any]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}


def _texts(types: list[dict[str, Any]], max_types: int = 1000) -> list[str]:
    return [
        chunk.text for chunk in build_chunks_from_api_surface(_fixture(types), title="t", max_types=max_types)
    ]


def _fqn(text: str) -> str:
    match = _FQN_LINE.search(text)
    assert match is not None
    return match.group(1)


def _by_fqn(texts: list[str], fqn: str) -> str:
    return next(text for text in texts if _fqn(text) == fqn)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _lists_members(text: str) -> bool:
    lines = _lines(text)
    return any(header in lines for header in ("Methods:", "Properties:", "Members:"))


def _shell_and_definition(*, shell_file: str, shell_import: str) -> list[dict[str, Any]]:
    return [
        _entry("InkAnnotation", shell_import, shell_file, members=False),
        _entry("InkAnnotation", "pkg.InkAnnotation", "pkg/ink.py"),
    ]


# ------------------------------------------------------------------------------ the new rule


def test_a_stub_in_another_package_pairs_with_the_one_definition() -> None:
    texts = _texts(
        _shell_and_definition(
            shell_file="pkg/annotations/__init__.py", shell_import="pkg.annotations.InkAnnotation"
        )
    )
    shell = _by_fqn(texts, "pkg.annotations.InkAnnotation")
    definition = _by_fqn(texts, "pkg.InkAnnotation")
    assert "Defined-In: pkg.InkAnnotation" in _lines(shell)
    assert "Methods:" in _lines(shell)
    assert any("save(" in line for line in _lines(shell))
    assert "Exported-As: pkg.annotations.InkAnnotation" in _lines(definition)


def test_a_stub_module_class_pairs_with_the_definition() -> None:
    types = [
        _entry("Document", "pkg.generated.document.Document", "pkg/generated/document.py", members=False),
        _entry("Document", "pkg.document.Document", "pkg/document.py"),
    ]
    texts = _texts(types)
    shell = _by_fqn(texts, "pkg.generated.document.Document")
    assert "Defined-In: pkg.document.Document" in _lines(shell)
    assert any("save(" in line for line in _lines(shell))
    assert "Exported-As: pkg.generated.document.Document" in _lines(_by_fqn(texts, "pkg.document.Document"))


def test_two_definitions_of_the_same_name_leave_the_shell_unpaired() -> None:
    types = [
        _entry("Shape", "pkg.sub.Shape", "pkg/sub/shape.py", members=False),
        _entry("Shape", "pkg.a.Shape", "pkg/a.py"),
        _entry("Shape", "pkg.b.Shape", "pkg/b.py"),
    ]
    assert chunk_builder._reexport_pairs(types) == {}
    shell = _by_fqn(_texts(types), "pkg.sub.Shape")
    assert not any(line.startswith("Defined-In:") for line in _lines(shell))
    assert not _lists_members(shell)


def test_a_definition_of_another_kind_does_not_pair() -> None:
    types = [
        _entry("Mode", "pkg.sub.Mode", "pkg/sub/mode.py", members=False),
        _entry("Mode", "pkg.Mode", "pkg/mode.py", kind="enum"),
    ]
    assert chunk_builder._reexport_pairs(types) == {}


def test_a_member_less_function_never_pairs() -> None:
    types = [
        _entry("run", "pkg.sub.run", "pkg/sub/run.py", kind="function", members=False),
        _entry("run", "pkg.run", "pkg/run.py", kind="function"),
    ]
    assert chunk_builder._reexport_pairs(types) == {}


def test_non_python_entries_never_pair() -> None:
    types = [
        _entry("Widget", "Lib.Sub.Widget", "src/Sub/Widget.cs", members=False),
        _entry("Widget", "Lib.Widget", "src/Widget.cs"),
    ]
    assert chunk_builder._reexport_pairs(types) == {}
    shell = _by_fqn(_texts(types), "Lib.Sub.Widget")
    assert not _lists_members(shell)


def test_a_python_shell_is_not_paired_with_a_non_python_definition() -> None:
    types = [
        _entry("Widget", "pkg.Widget", "pkg/widget.py", members=False),
        _entry("Widget", "Lib.Widget", "src/Widget.cs"),
    ]
    assert chunk_builder._reexport_pairs(types) == {}


def test_an_explicit_reexport_of_pair_wins_over_the_new_rule() -> None:
    types = [
        _entry("Thing", "pkg.api.Thing", "pkg/api.py", members=False, reexport_of="pkg.real.Thing"),
        _entry("Thing", "pkg.other.Thing", "pkg/other.py"),
        _entry("Thing", "pkg.real.Thing", "pkg/real.py", methods=[_method("only_here")]),
    ]
    pairs = chunk_builder._reexport_pairs(types)
    assert pairs[0] == 2


def test_the_legacy_package_rule_still_wins_when_it_applies() -> None:
    types = [
        _entry("Cell", "pkg.charts.Cell", "pkg/charts/__init__.py", members=False),
        _entry("Cell", "pkg.charts.cell.Cell", "pkg/charts/cell.py"),
    ]
    assert chunk_builder._reexport_pairs(types) == {0: 1}


def test_a_shell_is_never_used_as_a_definition() -> None:
    types = [
        _entry("Gadget", "pkg.a.Gadget", "pkg/a.py", members=False),
        _entry("Gadget", "pkg.b.Gadget", "pkg/b.py", members=False),
    ]
    assert chunk_builder._reexport_pairs(types) == {}


def test_a_member_bearing_entry_that_is_an_explicit_shell_is_not_a_definition() -> None:
    types = [
        _entry("Gadget", "pkg.a.Gadget", "pkg/a.py", members=False),
        _entry("Gadget", "pkg.b.Gadget", "pkg/b.py", reexport_of="pkg.c.Gadget"),
        _entry("Gadget", "pkg.c.Gadget", "pkg/c.py"),
    ]
    pairs = chunk_builder._reexport_pairs(types)
    assert pairs == {1: 2, 0: 2}


def test_two_shells_pair_with_the_same_definition() -> None:
    types = [
        _entry("Gadget", "pkg.a.Gadget", "pkg/a.py", members=False),
        _entry("Gadget", "pkg.b.Gadget", "pkg/b.py", members=False),
        _entry("Gadget", "pkg.Gadget", "pkg/gadget.py"),
    ]
    assert chunk_builder._reexport_pairs(types) == {0: 2, 1: 2}
    texts = _texts(types)
    exported = [line for line in _lines(_by_fqn(texts, "pkg.Gadget")) if line.startswith("Exported-As:")]
    assert sorted(exported) == ["Exported-As: pkg.a.Gadget", "Exported-As: pkg.b.Gadget"]


def test_the_pair_is_one_slot_of_the_cap() -> None:
    types = [
        _entry("Ink", "pkg.annotations.Ink", "pkg/annotations/__init__.py", members=False),
        _entry("Other", "pkg.Other", "pkg/other.py"),
        _entry("Third", "pkg.Third", "pkg/third.py"),
        _entry("Ink", "pkg.Ink", "pkg/ink.py"),
    ]
    texts = _texts(types, max_types=2)
    assert sorted(_fqn(text) for text in texts) == ["pkg.Ink", "pkg.Other", "pkg.annotations.Ink"]
    assert "Defined-In: pkg.Ink" in _lines(_by_fqn(texts, "pkg.annotations.Ink"))
    assert "Exported-As: pkg.annotations.Ink" in _lines(_by_fqn(texts, "pkg.Ink"))


def test_an_enum_shell_pairs_with_an_enum_definition_and_renders_its_members() -> None:
    types = [
        _entry("Mode", "pkg.generated.mode.Mode", "pkg/generated/mode.py", kind="enum", members=False),
        _entry("Mode", "pkg.Mode", "pkg/mode.py", kind="enum"),
    ]
    texts = _texts(types)
    shell = _by_fqn(texts, "pkg.generated.mode.Mode")
    assert "Defined-In: pkg.Mode" in _lines(shell)
    assert "Members:" in _lines(shell)
    assert any("ALPHA" in line for line in _lines(shell))
    assert "Exported-As: pkg.generated.mode.Mode" in _lines(_by_fqn(texts, "pkg.Mode"))


def test_the_deciding_line_appears_once_in_the_module() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "indexing" / "chunk_builder.py").read_text(encoding="utf-8")
    assert source.count("if len(found) == 1 and found[0] != index:") == 1


def test_the_ingestion_entrypoint_calls_the_builder() -> None:
    source = (REPO_ROOT / "infra" / "build_chunks.py").read_text(encoding="utf-8")
    assert "build_chunks_from_api_surface(" in source


# --------------------------------------------------------------- the 12 committed Python fixtures


def test_the_eleven_python_fixtures_are_discovered() -> None:
    # re-pinned by TC-551: 12 -> 11
    assert len(PYTHON_FIXTURES) >= 11


def _final_segment(fqn: str) -> str:
    return _FINAL_SEGMENT.split(_QUALIFIER_SUFFIX.sub("", fqn))[-1]


@pytest.mark.parametrize("fixture_name", PYTHON_FIXTURES)
def test_no_member_less_class_or_enum_chunk_has_a_unique_member_bearing_twin_after_the_build(
    fixture_name: str,
) -> None:
    fixture = json.loads((FIXTURES_DIR / fixture_name / "api_surface.json").read_text(encoding="utf-8"))
    types = fixture["types"]
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=len(types))
    texts = [chunk.text for chunk in chunks]
    with_members: dict[str, int] = {}
    for text in texts:
        if _lists_members(text):
            segment = _final_segment(_fqn(text))
            with_members[segment] = with_members.get(segment, 0) + 1
    residual = []
    for text in texts:
        kind = _KIND_LINE.search(text)
        if kind is None or kind.group(1).strip() not in ("class", "enum"):
            continue
        if _lists_members(text) or any(line.startswith("Defined-In:") for line in _lines(text)):
            continue
        fqn = _fqn(text)
        if with_members.get(_final_segment(fqn)) == 1:
            residual.append(fqn)
    assert residual == []
