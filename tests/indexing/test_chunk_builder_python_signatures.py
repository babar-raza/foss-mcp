"""TC-522: how ``chunk_builder`` renders a Python signature and pairs a re-export with its definition.

Synthetic fixture dicts only (plus the committed pdf_net fixture, to pin that nothing else moved).
Fully offline.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from foss_mcp.indexing import chunk_builder
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _method(name: str, params: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"name": name, "params": params, "return_type": "", **extra}


def _entry(
    name: str,
    class_import: str,
    *,
    file: str = "pkg/mod.py",
    methods: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "class",
        "file": file,
        "class_import": class_import,
        "bases": [],
        "methods": [_method("m", [])] if methods is None else methods,
        "properties": [],
        **extra,
    }


def _fixture(types: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types, **extra}


def _texts(types: list[dict[str, Any]], max_types: int = 1000, **extra: Any) -> list[str]:
    chunks = build_chunks_from_api_surface(_fixture(types, **extra), title="t", max_types=max_types)
    return [chunk.text for chunk in chunks]


def _fqns(texts: list[str]) -> list[str]:
    found = [re.search(r"^FQN: (.+)$", text, re.MULTILINE) for text in texts]
    return [match.group(1) for match in found if match is not None]


def _line(params: list[dict[str, Any]], **extra: Any) -> str:
    return chunk_builder._method_line(_method("save", params, **extra))


def _param(name: str, kind: str | None = None, default: str | None = None, type_: str = "") -> dict[str, Any]:
    param: dict[str, Any] = {"name": name, "type": type_}
    if kind is not None:
        param["kind"] = kind
    if default is not None:
        param["default"] = default
    return param


# ------------------------------------------------------------------------- parameter markers


def test_star_marker_goes_before_the_first_keyword_only_parameter() -> None:
    params = [_param("path", default="None", type_="str"), _param("overwrite", "keyword_only", "False")]
    assert _line(params) == "  - save(path: str = None, *, overwrite = False)"


def test_no_star_marker_after_a_var_positional_parameter() -> None:
    params = [_param("a"), _param("*args"), _param("flag", "keyword_only", "False")]
    assert _line(params) == "  - save(a, *args, flag = False)"
    typed = [_param("*args", "var_positional"), _param("flag", "keyword_only")]
    assert _line(typed) == "  - save(*args, flag)"


def test_one_star_marker_for_several_keyword_only_parameters() -> None:
    params = [_param("a"), _param("b", "keyword_only"), _param("c", "keyword_only", "1")]
    assert _line(params) == "  - save(a, *, b, c = 1)"


def test_slash_marker_follows_the_last_positional_only_parameter() -> None:
    params = [_param("a", "positional_only"), _param("b", "positional_only", "1"), _param("c", default="2")]
    assert _line(params) == "  - save(a, b = 1, /, c = 2)"


def test_slash_and_star_markers_together() -> None:
    params = [
        _param("a", "positional_only"),
        _param("b", default="1"),
        _param("c", "keyword_only"),
        _param("d", "keyword_only", "None"),
        _param("**kw", "var_keyword"),
    ]
    assert _line(params) == "  - save(a, /, b = 1, *, c, d = None, **kw)"


def test_var_keyword_alone_gets_no_marker() -> None:
    assert _line([_param("a"), _param("**kw")]) == "  - save(a, **kw)"


def test_entries_without_kind_render_byte_for_byte_as_before() -> None:
    params = [_param("a", type_="int"), _param("b"), {"name": "c", "type": "str", "default": "'x'"}]
    assert _line(params) == "  - save(a: int, b, c: str = 'x')"
    assert (
        chunk_builder._method_line({"name": "run", "params": [{"name": "x", "type": "T"}]}) == "  - run(x: T)"
    )
    assert chunk_builder._method_line({"name": "run"}) == "  - run()"


def test_defaults_and_modifiers_render() -> None:
    method = _method("open", [_param("path", "positional_only"), _param("mode", default="'r'")])
    method["modifiers"] = ["classmethod"]
    method["return_type"] = "Doc"
    assert chunk_builder._method_line(method) == "  - open(path, /, mode = 'r') -> Doc [classmethod]"
    assert _line([], modifiers=["static"]) == "  - save() [static]"
    assert _line([], modifiers=["async"]) == "  - save() [async]"
    assert _line([], modifiers=["static", "async"]) == "  - save() [static, async]"


def test_a_module_function_entry_renders_its_markers_and_modifiers() -> None:
    function = {
        "name": "load",
        "kind": "function",
        "file": "pkg/util.py",
        "class_import": "pkg.util.load",
        "methods": [],
        "properties": [],
        "params": [_param("path"), _param("encoding", "keyword_only", "'utf-8'")],
        "return_type": "str",
        "modifiers": ["async"],
    }
    (text,) = _texts([function])
    assert "  - load(path, *, encoding = 'utf-8') -> str [async]" in text


# ------------------------------------------------------------------------- pairing and the cap


def _pair(index: int, *, with_members: bool = False) -> list[dict[str, Any]]:
    """A shell and its definition, named by *index*; the shell comes first in the fixture."""
    name = f"C{index}"
    shell = _entry(
        name,
        f"pkg.{name}",
        file="pkg/__init__.py",
        methods=[_method("inherited", [])] if with_members else [],
        reexport_of=f"pkg.mod{index}.{name}",
    )
    definition = _entry(name, f"pkg.mod{index}.{name}", file=f"pkg/mod{index}.py")
    return [shell, definition]


def test_shell_and_definition_cost_one_slot() -> None:
    texts = _texts([*_pair(1), _entry("Other", "pkg.other.Other")], max_types=1)
    assert _fqns(texts) == ["pkg.C1", "pkg.mod1.C1"]


def test_the_cap_never_splits_a_pair() -> None:
    types = [entry for index in (1, 2, 3) for entry in _pair(index)]
    texts = _texts(types, max_types=2)
    assert _fqns(texts) == ["pkg.C1", "pkg.mod1.C1", "pkg.C2", "pkg.mod2.C2"]


def test_a_shell_ranking_first_pulls_its_definition() -> None:
    shell, definition = _pair(1)
    types = [shell, _entry("Middle", "pkg.mid.Middle"), _entry("Tail", "pkg.tail.Tail"), definition]
    assert _fqns(_texts(types, max_types=1)) == ["pkg.C1", "pkg.mod1.C1"]
    # The definition sits in the slot of its earliest member, so the second slot is Middle.
    assert _fqns(_texts(types, max_types=2)) == ["pkg.C1", "pkg.mid.Middle", "pkg.mod1.C1"]


def test_output_keeps_fixture_order() -> None:
    shell, definition = _pair(1)
    types = [_entry("A", "pkg.a.A"), definition, _entry("B", "pkg.b.B"), shell]
    assert _fqns(_texts(types, max_types=2)) == ["pkg.a.A", "pkg.mod1.C1", "pkg.C1"]


def test_unpaired_shell_is_an_ordinary_slot() -> None:
    lonely = _entry("Gone", "pkg.Gone", file="pkg/__init__.py", reexport_of="pkg._private.Gone")
    types = [lonely, _entry("Next", "pkg.n.Next"), _entry("Last", "pkg.l.Last")]
    assert chunk_builder._reexport_pairs(types) == {}
    assert _fqns(_texts(types, max_types=2)) == ["pkg.Gone", "pkg.n.Next"]


def test_explicit_pairing_wins_for_a_shell_with_members() -> None:
    shell, definition = _pair(1, with_members=True)
    assert chunk_builder._reexport_pairs([shell, definition]) == {0: 1}
    shell_text, definition_text = _texts([shell, definition])
    assert "Defined-In: pkg.mod1.C1" in shell_text
    assert "Exported-As: pkg.C1" in definition_text


def test_explicit_pairing_beats_the_member_less_heuristic() -> None:
    explicit = _entry("Z", "pkg.Z", file="pkg/__init__.py", methods=[], reexport_of="pkg.sub.Z")
    decoy = _entry("Z", "pkg.zz.Z")
    definition = _entry("Z", "pkg.sub.Z", file="pkg/sub.py", methods=[_method("real", [])])
    assert chunk_builder._reexport_pairs([explicit, decoy, definition]) == {0: 2}


def test_legacy_heuristic_pairing_still_works() -> None:
    stub = _entry("Doc", "pkg.Doc", file="pkg/__init__.py", methods=[])
    definition = _entry("Doc", "pkg.doc.Doc", file="pkg/doc.py", methods=[_method("save", [])])
    assert chunk_builder._reexport_pairs([stub, definition]) == {0: 1}
    stub_text, _ = _texts([stub, definition])
    assert "Defined-In: pkg.doc.Doc" in stub_text and "  - save()" in stub_text


def test_a_chain_of_reexports_ends_at_the_definition_and_a_loop_pairs_nothing() -> None:
    a = _entry("X", "a.X", file="a/__init__.py", reexport_of="b.X")
    b = _entry("X", "b.X", file="b/__init__.py", reexport_of="c.X")
    c = _entry("X", "c.X", file="c/x.py")
    assert chunk_builder._reexport_pairs([a, b, c]) == {0: 2, 1: 2}
    loop_a = _entry("Y", "a.Y", file="a/__init__.py", reexport_of="b.Y")
    loop_b = _entry("Y", "b.Y", file="b/__init__.py", reexport_of="a.Y")
    assert chunk_builder._reexport_pairs([loop_a, loop_b]) == {}
    selfish = _entry("S", "a.S", file="a/__init__.py", reexport_of="a.S")
    assert chunk_builder._reexport_pairs([selfish]) == {}


def test_rendered_shell_shows_the_definitions_own_methods() -> None:
    shell = _entry(
        "PdfFileSecurity",
        "aspose_pdf.PdfFileSecurity",
        file="aspose_pdf/__init__.py",
        methods=[_method("bind_pdf", [_param("path")])],
        reexport_of="aspose_pdf.facades.PdfFileSecurity",
    )
    definition = _entry(
        "PdfFileSecurity",
        "aspose_pdf.facades.PdfFileSecurity",
        file="aspose_pdf/facades.py",
        methods=[
            _method("bind_pdf", [_param("path")]),
            _method(
                "encrypt_file", [_param("user"), _param("owner"), _param("permissions", "keyword_only", "0")]
            ),
        ],
    )
    shell_text, _ = _texts([shell, definition])
    assert "Defined-In: aspose_pdf.facades.PdfFileSecurity" in shell_text
    assert "  - encrypt_file(user, owner, *, permissions = 0)" in shell_text


def test_coverage_line_counts_entries_so_it_can_exceed_the_cap() -> None:
    types = [entry for index in (1, 2) for entry in _pair(index)]
    texts = _texts(types, max_types=2, type_count=10)
    assert len(texts) == 4
    assert all(text.splitlines()[-1] == "API-Coverage: 4 of 10 types" for text in texts)


def test_a_fixture_without_shells_is_cut_exactly_as_before() -> None:
    types = [_entry(f"T{i}", f"pkg.t{i}.T{i}") for i in range(6)]
    assert _fqns(_texts(types, max_types=4)) == [f"pkg.t{i}.T{i}" for i in range(4)]


def test_test_sources_are_dropped_before_the_cap() -> None:
    types = [
        _entry("T0", "pkg.t0.T0", file="pkg/tests/t0.py"),
        _entry("T1", "pkg.t1.T1"),
        _entry("T2", "pkg.t2.T2"),
    ]
    assert _fqns(_texts(types, max_types=1)) == ["pkg.t1.T1"]


# ------------------------------------------------------------------------- nothing else moved


def test_non_python_fixture_is_unchanged() -> None:
    fixture: Mapping[str, object] = json.loads(
        (FIXTURES_DIR / "pdf_net" / "api_surface.json").read_text(encoding="utf-8")
    )
    chunks = build_chunks_from_api_surface(fixture, title="pdf/net")
    assert len(chunks) == 300
    assert all(chunk.text.splitlines()[-1] == "API-Coverage: 300 of 901 types" for chunk in chunks)


def test_chunk_builder_does_not_import_run_extraction() -> None:
    tree = ast.parse(Path(chunk_builder.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
    assert not any("run_extraction" in name for name in imported)
