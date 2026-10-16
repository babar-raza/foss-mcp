"""TC-382 (REQ-G3-005): the symbol chunks state only what the extraction stated.

One focused test per rendering rule on small synthetic fixtures, plus tests against the real
committed fixtures (pdf_net, pdf_go, pdf_typescript, pdf_cpp, pdf_java, jmap_go, jmap_nodejs,
slides_python). Fully offline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from foss_mcp.indexing.chunk_builder import _is_test_source, build_chunks_from_api_surface

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(types: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types, **extra}


def _texts(fixture: Mapping[str, object]) -> list[str]:
    return [chunk.text for chunk in build_chunks_from_api_surface(fixture, title="t", max_types=10_000)]


def _text(fixture: Mapping[str, object]) -> str:
    return "\n".join(_texts(fixture))


def _real(pilot: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _real_texts(pilot: str) -> list[str]:
    return _texts(_real(pilot))


def _fqn(text: str) -> str:
    match = re.search(r"^FQN: (.+)$", text, re.MULTILINE)
    assert match is not None
    return match.group(1)


# ---------------------------------------------------------------- rule 1: no invented return type


def test_missing_return_type_renders_without_an_arrow() -> None:
    fixture = _fixture(
        [
            {
                "name": "Doc",
                "kind": "class",
                "methods": [
                    {"name": "Open", "params": [{"name": "path", "type": "string"}]},
                    {"name": "Close", "params": [], "return_type": ""},
                    {"name": "Nothing", "params": [], "return_type": None},
                ],
            }
        ]
    )
    lines = _text(fixture).splitlines()
    assert "  - Open(path: string)" in lines
    assert "  - Close()" in lines
    assert "  - Nothing()" in lines
    assert "->" not in _text(fixture)


def test_a_stated_return_type_including_void_renders_as_before() -> None:
    fixture = _fixture(
        [
            {
                "name": "Doc",
                "kind": "class",
                "methods": [
                    {"name": "Stop", "params": [], "return_type": "void"},
                    {"name": "Count", "params": [], "return_type": "int"},
                ],
            }
        ]
    )
    lines = _text(fixture).splitlines()
    assert "  - Stop() -> void" in lines
    assert "  - Count() -> int" in lines


# ------------------------------------------------------- rule 2: an untyped parameter or property


def test_untyped_parameter_and_property_render_as_bare_names() -> None:
    fixture = _fixture(
        [
            {
                "name": "Client",
                "kind": "class",
                "methods": [{"name": "Connect", "params": [{"name": "options", "type": ""}, {"name": "n"}]}],
                "properties": [{"name": "timeout", "type": ""}, {"name": "retries", "type": None}],
            }
        ]
    )
    text = _text(fixture)
    assert "  - Connect(options, n)" in text.splitlines()
    assert "  - timeout" in text.splitlines()
    assert "  - retries" in text.splitlines()
    assert "options: " not in text


# ------------------------------------------------------------ rule 3: the leading declaration colon


def test_leading_colon_is_removed_from_params_properties_and_return_types() -> None:
    fixture = _fixture(
        [
            {
                "name": "Node",
                "kind": "class",
                "methods": [
                    {
                        "name": "get",
                        "params": [{"name": "k", "type": " :  string"}],
                        "return_type": ": number",
                    }
                ],
                "properties": [{"name": "id", "type": ": string", "writable": True}],
            }
        ]
    )
    text = _text(fixture)
    assert "  - get(k: string) -> number" in text.splitlines()
    assert "  - id: string (writable)" in text.splitlines()
    assert ": :" not in text


# --------------------------------------------------------------------------- rule 4: duplicates


def test_a_repeated_property_name_is_rendered_once_first_wins() -> None:
    fixture = _fixture(
        [
            {
                "name": "T",
                "kind": "class",
                "properties": [
                    {"name": "a", "type": "int"},
                    {"name": "b", "type": "str"},
                    {"name": "a", "type": "float"},
                ],
            }
        ]
    )
    lines = _text(fixture).splitlines()
    assert lines.count("  - a: int") == 1
    assert "  - a: float" not in lines
    assert lines.index("  - a: int") < lines.index("  - b: str")


def test_an_identical_method_line_is_rendered_once_but_overloads_stay() -> None:
    method = {"name": "Save", "params": [{"name": "p", "type": "string"}], "return_type": "void"}
    overload = {"name": "Save", "params": [{"name": "s", "type": "Stream"}], "return_type": "void"}
    fixture = _fixture([{"name": "T", "kind": "class", "methods": [method, overload, dict(method)]}])
    lines = _text(fixture).splitlines()
    assert lines.count("  - Save(p: string) -> void") == 1
    assert lines.count("  - Save(s: Stream) -> void") == 1
    assert lines.index("  - Save(p: string) -> void") < lines.index("  - Save(s: Stream) -> void")


# ----------------------------------------------------- rule 5: inherited constructors/destructors


def test_inherited_constructors_and_destructors_are_not_rendered() -> None:
    fixture = _fixture(
        [
            {
                "name": "Derived",
                "kind": "class",
                "methods": [
                    {"name": "Derived", "params": [], "is_constructor": True},
                    {"name": "Run", "params": [], "return_type": "void"},
                    {"name": "Base", "params": [], "inherited_from": "ns::Base"},
                    {"name": "~Base", "params": [], "inherited_from": "ns::Base"},
                    {"name": "Other", "params": [], "inherited_from": "ns::Base", "is_constructor": True},
                    {"name": "constructor", "params": [], "inherited_from": "a.Base"},
                    {"name": "__init__", "params": [], "inherited_from": "a.Base"},
                    {"name": "Helper", "params": [], "return_type": "int", "inherited_from": "a.Base"},
                ],
            }
        ]
    )
    text = _text(fixture)
    lines = text.splitlines()
    assert "  - Derived()" in lines
    assert "  - Run() -> void" in lines
    assert "  - Helper() -> int  (inherited from a.Base)" in lines
    for gone in ("Base()", "~Base", "Other()", "constructor(", "__init__("):
        assert gone not in text


def test_an_own_destructor_or_constructor_named_like_a_base_stays() -> None:
    fixture = _fixture(
        [
            {
                "name": "D",
                "kind": "class",
                "methods": [{"name": "~D", "params": []}, {"name": "D", "params": []}],
            }
        ]
    )
    lines = _text(fixture).splitlines()
    assert "  - ~D()" in lines
    assert "  - D()" in lines


# ------------------------------------------------------------------------ rule 6: deprecation


def test_deprecated_method_property_and_type_are_marked() -> None:
    fixture = _fixture(
        [
            {
                "name": "Old",
                "kind": "class",
                "deprecated": True,
                "deprecated_reason": "use New instead",
                "methods": [
                    {"name": "A", "params": [], "return_type": "int", "deprecated": True},
                    {
                        "name": "B",
                        "params": [],
                        "return_type": "int",
                        "deprecated": True,
                        "deprecated_reason": "x" * 500,
                    },
                    {"name": "C", "params": [], "return_type": "int", "deprecated": False},
                ],
                "properties": [{"name": "p", "type": "int", "deprecated": True, "deprecated_reason": "gone"}],
            },
            {"name": "Fresh", "kind": "class", "deprecated": False, "methods": []},
        ]
    )
    texts = _texts(fixture)
    old = next(t for t in texts if _fqn(t) == "Old")
    lines = old.splitlines()
    assert lines[1] == "Kind: class"
    assert lines[2] == "Deprecated: use New instead"
    assert "  - A() -> int (deprecated)" in lines
    assert "  - B() -> int (deprecated: " + "x" * 200 + ")" in lines
    assert "  - C() -> int" in lines
    assert "  - p: int (deprecated: gone)" in lines
    fresh = next(t for t in texts if _fqn(t) == "Fresh")
    assert "eprecated" not in fresh


def test_a_deprecated_type_without_a_reason_says_yes() -> None:
    text = _text(_fixture([{"name": "Old", "kind": "class", "deprecated": True}]))
    assert text.splitlines()[2] == "Deprecated: yes"


# --------------------------------------------------------------------- rule 7: test-file symbols


def test_is_test_source_accepts_only_test_files() -> None:
    for path in (
        "a/b_test.go",
        "x_test.py",
        "src/a.test.ts",
        "src/a.spec.ts",
        "a.test.js",
        "a.spec.js",
        "pkg/test_widget.py",
        "pkg/test/helper.go",
        "src/tests/helper.ts",
        "src/__tests__/a.ts",
        "pkg\\tests\\a.py",
    ):
        assert _is_test_source(path), path
    for path in ("a/b.go", "src/contest.ts", "test.go", "pkg/testing/a.py", "src/latest/a.ts", "", None):
        assert not _is_test_source(path), path


def test_test_file_symbols_are_not_published() -> None:
    fixture = _fixture(
        [
            {"name": "Real", "kind": "function", "file": "real.go", "line": 1},
            {"name": "Helper", "kind": "function", "file": "real_test.go", "line": 2},
            {"name": "NoFile", "kind": "function"},
        ],
        type_count=3,
    )
    texts = _texts(fixture)
    assert sorted(_fqn(t) for t in texts) == ["NoFile", "Real"]
    assert all(t.splitlines()[-1] == "API-Coverage: 2 of 3 types" for t in texts)


# ---------------------------------------------------------------- rule 8: Python re-export stubs


def _reexport_fixture() -> dict[str, object]:
    return _fixture(
        [
            {
                "name": "Cell",
                "class_import": "pkg.charts.Cell",
                "kind": "class",
                "file": "pkg/charts/__init__.py",
                "bases": [],
                "methods": [],
            },
            {
                "name": "Cell",
                "class_import": "pkg.charts.Cell.Cell",
                "kind": "class",
                "file": "pkg/charts/Cell.py",
                "doc": "A cell.",
                "bases": ["ABC"],
                "methods": [{"name": "get", "params": [], "return_type": "int"}],
                "properties": [{"name": "row", "type": "int"}],
            },
            {
                "name": "Lonely",
                "class_import": "pkg.charts.Lonely",
                "kind": "class",
                "file": "pkg/charts/__init__.py",
                "methods": [],
            },
        ]
    )


def test_a_reexport_stub_is_rendered_with_its_definitions_members() -> None:
    texts = _texts(_reexport_fixture())
    stub = next(t for t in texts if _fqn(t) == "pkg.charts.Cell")
    lines = stub.splitlines()
    assert lines[2] == "Defined-In: pkg.charts.Cell.Cell"
    assert "A cell." in lines
    assert "  - ABC" in lines
    assert "  - get() -> int" in lines
    assert "  - row: int" in lines


def test_the_definition_keeps_its_fqn_and_names_its_export() -> None:
    texts = _texts(_reexport_fixture())
    definition = next(t for t in texts if _fqn(t) == "pkg.charts.Cell.Cell")
    lines = definition.splitlines()
    assert lines[2] == "Exported-As: pkg.charts.Cell"
    assert "  - get() -> int" in lines


def test_a_stub_without_a_unique_definition_stays_empty() -> None:
    lonely = next(t for t in _texts(_reexport_fixture()) if _fqn(t) == "pkg.charts.Lonely")
    assert "Defined-In" not in lonely
    assert "Methods:" not in lonely


# -------------------------------------------------------------------- rule 9: colliding FQNs


def test_colliding_fqns_are_qualified_and_unique_while_others_are_untouched() -> None:
    fixture = _fixture(
        [
            {
                "name": "Color",
                "kind": "function",
                "receiver_type": "annotationBase",
                "file": "annotation.go",
                "line": 5,
            },
            {"name": "Color", "kind": "type_spec", "file": "color.go", "line": 6},
            {"name": "Unique", "kind": "type_spec", "file": "u.go", "line": 7},
        ]
    )
    fqns = [_fqn(t) for t in _texts(fixture)]
    assert fqns == ["annotationBase.Color", "color.Color", "Unique"]


def test_a_namespace_that_is_already_in_the_fqn_is_not_the_qualifier() -> None:
    fixture = _fixture(
        [
            {
                "name": "x",
                "class_import": "ns.x",
                "canonical_namespace": "ns",
                "kind": "function",
                "file": "src/a.ts",
                "line": 1,
            },
            {
                "name": "x",
                "class_import": "ns.x",
                "canonical_namespace": "other",
                "kind": "function",
                "file": "src/b.ts",
                "line": 2,
            },
        ]
    )
    assert [_fqn(t) for t in _texts(fixture)] == ["a.x", "other.x"]


def test_a_collision_that_survives_qualification_gets_the_line() -> None:
    fixture = _fixture(
        [
            {"name": "f", "kind": "function", "file": "src/a.ts", "line": 10},
            {"name": "f", "kind": "function", "file": "lib/a.ts", "line": 20},
        ]
    )
    assert [_fqn(t) for t in _texts(fixture)] == ["a.f@10", "a.f@20"]


# ------------------------------------------------------------------------ rule 10: coverage


def test_api_coverage_line_ends_every_section_when_type_count_is_recorded() -> None:
    fixture = _fixture(
        [
            {"name": "A", "kind": "class", "methods": [{"name": "m"}]},
            {"name": "B", "kind": "enum", "enum_members": [{"name": "X", "value": 1}]},
        ],
        type_count=50,
    )
    texts = _texts(fixture)
    assert len(texts) == 2
    assert all(t.splitlines()[-1] == "API-Coverage: 2 of 50 types" for t in texts)


def test_no_coverage_line_without_a_type_count() -> None:
    assert "API-Coverage" not in _text(_fixture([{"name": "A", "kind": "class"}]))
    assert "API-Coverage" not in _text(_fixture([{"name": "A", "kind": "class"}], type_count="many"))


# ----------------------------------------------------------------------- rule 11: enum values


def test_an_enum_member_without_a_value_renders_without_a_dangling_equals() -> None:
    fixture = _fixture(
        [
            {
                "name": "E",
                "kind": "enum",
                "enum_members": [
                    {"name": "A"},
                    {"name": "B", "value": None},
                    {"name": "C", "value": ""},
                    {"name": "D", "value": 0},
                    {"name": "E", "value": "x"},
                ],
            }
        ]
    )
    text = _text(fixture)
    lines = text.splitlines()
    assert lines[lines.index("Members:") + 1 : lines.index("Members:") + 6] == [
        "  - A",
        "  - B",
        "  - C",
        "  - D = 0",
        "  - E = x",
    ]
    assert " = None" not in text


# ----------------------------------------------------------------------------- real fixtures


def test_real_pdf_net_has_no_invented_void_and_keeps_the_genuine_ones() -> None:
    pdf_net = _real("pdf_net")
    expected_void = 0
    for entry in pdf_net["types"]:
        for method in entry.get("methods") or []:
            if method.get("return_type") == "void":
                expected_void += 1
    texts = _real_texts("pdf_net")
    all_lines = [line for text in texts for line in text.splitlines()]
    void_lines = [line for line in all_lines if " -> void" in line]
    # re-extracted by TC-530: 1034 -> 2408 (the fixture holds all 901 types, not the first 300)
    assert expected_void == 2408
    assert len(void_lines) == 2408
    page = next(t for t in texts if _fqn(t) == "Aspose.Pdf.Page")
    assert "  - SetRotation(degrees: int) -> void" in page.splitlines()
    # every rendered void line comes from a method whose fixture return_type really is "void"
    real_void_names = {
        method["name"]
        for entry in pdf_net["types"]
        for method in entry.get("methods") or []
        if method.get("return_type") == "void"
    }
    for line in void_lines:
        assert line.split("(")[0].strip("- ").strip() in real_void_names, line


def test_real_jmap_nodejs_has_no_untyped_parameter_remnants() -> None:
    text = "\n".join(_real_texts("jmap_nodejs"))
    assert re.search(r": [,)]", text) is None
    assert re.search(r": $", text, re.MULTILINE) is None


def test_real_pdf_typescript_has_no_double_colon_and_no_repeated_property() -> None:
    texts = _real_texts("pdf_typescript")
    assert ": : " not in "\n".join(texts)
    for text in texts:
        names = [line[4:].split(":")[0].split(" (")[0] for line in _block(text, "Properties:")]
        assert len(names) == len(set(names)), _fqn(text)


def _block(text: str, header: str) -> list[str]:
    lines = text.splitlines()
    if header not in lines:
        return []
    items: list[str] = []
    for line in lines[lines.index(header) + 1 :]:
        if not line.startswith("  - "):
            break
        items.append(line)
    return items


def test_real_pdf_cpp_does_not_list_a_base_destructor_as_inherited() -> None:
    texts = _real_texts("pdf_cpp")
    for text in texts:
        assert "~BaseParagraph()" not in "\n".join(_block(text, "Methods:")) or _fqn(text).endswith(
            "BaseParagraph"
        ), _fqn(text)
        for line in _block(text, "Methods:"):
            if "(inherited from" in line:
                assert not line.startswith("  - ~"), (_fqn(text), line)
    popup = next(t for t in texts if _fqn(t).endswith("PopupAnnotation"))
    assert "~BaseParagraph()" not in popup


def test_real_go_fixtures_publish_no_test_file_symbol() -> None:
    # re-extracted by TC-550: the regenerated pdf_go holds no _test.go entry by design (TC-392 removed them
    # from the engine), so for it every fixture entry is published; jmap_go keeps the original proof that
    # the filter is exercised on a real fixture.
    pdf_go = _real("pdf_go")
    assert [e for e in pdf_go["types"] if str(e.get("file", "")).endswith("_test.go")] == []
    assert len({_fqn(t) for t in _real_texts("pdf_go")}) == len(pdf_go["types"])
    for pilot in ("jmap_go",):
        fixture = _real(pilot)
        test_entries = [e for e in fixture["types"] if str(e.get("file", "")).endswith("_test.go")]
        assert test_entries, pilot
        published = {_fqn(t) for t in _real_texts(pilot)}
        assert len(published) == len(fixture["types"]) - len(test_entries), pilot


def test_real_pdf_go_and_pdf_typescript_fqns_are_unique() -> None:
    for pilot in ("pdf_go", "pdf_typescript"):
        fqns = [_fqn(t) for t in _real_texts(pilot)]
        assert len(fqns) == len(set(fqns)), pilot
    # re-extracted by TC-550: pdf_go now holds a single `Color` (the old second one, `color.Color`, was read
    # from a non-library directory), so no qualification is needed there; the same-bare-name guard now reads
    # pdf_typescript, whose `Field` of formfield.ts and structattr.ts must stay two distinct FQNs.
    go_fqns = {_fqn(t) for t in _real_texts("pdf_go")}
    assert "Color" in go_fqns
    assert "color.Color" not in go_fqns
    ts_fqns = {_fqn(t) for t in _real_texts("pdf_typescript")}
    assert {"formfield.Field", "structattr.Field"} <= ts_fqns
    assert "Field" not in ts_fqns


def test_real_slides_python_reexport_lists_the_definitions_methods() -> None:
    fixture = _real("slides_python")
    definition = next(
        e
        for e in fixture["types"]
        if e.get("class_import") == "aspose.slides_foss.charts.IChartDataCell.IChartDataCell"
    )
    texts = _real_texts("slides_python")
    stub = next(t for t in texts if _fqn(t) == "aspose.slides_foss.charts.IChartDataCell")
    assert "Defined-In: aspose.slides_foss.charts.IChartDataCell.IChartDataCell" in stub.splitlines()
    # The python reader now files accessors under ``properties`` (this class's four members are
    # properties), so the re-export must list the definition's methods and properties alike.
    member_lines = _block(stub, "Methods:") + _block(stub, "Properties:")
    assert len(definition["methods"]) + len(definition["properties"]) == 4
    assert len(member_lines) == 4
    defn = next(t for t in texts if _fqn(t) == "aspose.slides_foss.charts.IChartDataCell.IChartDataCell")
    assert "Exported-As: aspose.slides_foss.charts.IChartDataCell" in defn.splitlines()
    assert _block(defn, "Methods:") + _block(defn, "Properties:") == member_lines


def test_real_pdf_net_chunks_state_the_coverage() -> None:
    texts = _real_texts("pdf_net")
    # re-extracted by TC-530: 300 chunks of "300 of 901 types" -> 901 chunks of "901 of 901 types"
    assert len(texts) == 901
    assert all(t.splitlines()[-1] == "API-Coverage: 901 of 901 types" for t in texts)


def test_real_pdf_java_deprecated_method_is_marked() -> None:
    fixture = _real("pdf_java")
    deprecated = [
        method
        for entry in fixture["types"]
        for method in entry.get("methods") or []
        if method.get("deprecated") is True
    ]
    texts = _real_texts("pdf_java")
    marked = [line for text in texts for line in text.splitlines() if "(deprecated" in line]
    assert deprecated
    assert marked


def test_output_is_byte_identical_across_two_runs() -> None:
    for pilot in ("pdf_net", "pdf_go", "slides_python"):
        first = _real_texts(pilot)
        second = _real_texts(pilot)
        assert first == second, pilot
