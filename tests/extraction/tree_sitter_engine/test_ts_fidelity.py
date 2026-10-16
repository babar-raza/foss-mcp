"""TC-523: the TypeScript reader keeps how an API is declared - optional, readonly, static, async, defaults.

The independent pre-production review (RST-07) compared the served TypeScript pilots with their upstream
sources and found every interface property labelled read-only, type aliases without their right-hand side,
static and async methods served as plain instance methods, optional and defaulted parameters served as
required, and ``@internal`` members served as public API.

Every test runs ``run_extraction.extract_from_clone`` on a COPY of the committed sample tree
(``tests/fixtures/ts_sample``) or on a small tree written here, offline.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.tree_helpers import get_parser
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "ts_sample"
MANIFEST = {"platform": "typescript", "family": "x", "repository": "x/y"}


def _extract(root: Path, manifest: dict[str, str] | None = None) -> list[dict[str, Any]]:
    types, unresolved = run_extraction.extract_from_clone(root, manifest or MANIFEST)
    assert unresolved == []
    return types


def _write_tree(root: Path, source: str, name: str = "index.ts") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name": "t", "main": "src/index.ts"}\n', encoding="utf-8")
    target = root / "src" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return root


def _extract_source(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    return _extract(_write_tree(tmp_path / "tree", source))


@pytest.fixture
def sample_types(tmp_path: Path) -> list[dict[str, Any]]:
    root = tmp_path / "sample"
    shutil.copytree(SAMPLE, root)
    return _extract(root)


def _type(types: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [t for t in types if t["name"] == name]
    assert len(matches) == 1, [t["name"] for t in types]
    return matches[0]


def _props(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in entry["properties"]}


def _methods(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in entry["methods"]}


def _param(method: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in method["params"] if p["name"] == name)


def test_interface_optional_and_readonly(sample_types: list[dict[str, Any]]) -> None:
    props = _props(_type(sample_types, "A"))
    assert props["x"]["writable"] is True
    assert props["x"]["modifiers"] == ["optional"]
    assert props["y"]["writable"] is False
    assert "modifiers" not in props["y"]
    assert props["z"]["writable"] is True
    assert "modifiers" not in props["z"]


def test_type_alias_value_is_captured(sample_types: list[dict[str, Any]]) -> None:
    assert _type(sample_types, "T")["alias_of"] == "'a' | 'b'"


def test_static_async_method_with_default_param(sample_types: list[dict[str, Any]]) -> None:
    open_method = _methods(_type(sample_types, "Document"))["open"]
    assert open_method["modifiers"] == ["static", "async"]
    assert [p["name"] for p in open_method["params"]] == ["buf", "opts"]
    assert "default" not in _param(open_method, "buf")
    assert _param(open_method, "opts")["default"] == "{}"
    assert _param(open_method, "opts")["type"] == "OpenOptions"


def test_object_alias_keeps_properties_and_alias_of(sample_types: list[dict[str, Any]]) -> None:
    opts = _type(sample_types, "Opts")
    assert opts["alias_of"] == "{ depth: number; name?: string }"
    props = _props(opts)
    assert set(props) == {"depth", "name"}
    assert props["depth"]["writable"] is True
    assert props["name"]["modifiers"] == ["optional"]


def test_long_alias_is_cut_at_the_limit_with_an_ellipsis(sample_types: list[dict[str, Any]]) -> None:
    alias = _type(sample_types, "ArrowShapeType")["alias_of"]
    assert len(alias) == api_surface._TS_ALIAS_TEXT_LIMIT
    assert alias.endswith("...")
    assert alias.startswith("| 'arrow' | 'rightArrow'")


def test_alias_text_is_one_line(sample_types: list[dict[str, Any]]) -> None:
    for entry in sample_types:
        alias = entry.get("alias_of")
        if alias is not None:
            assert "\n" not in alias and "  " not in alias, entry["name"]


def test_alias_exactly_at_the_limit_is_not_cut(tmp_path: Path) -> None:
    value = "'" + "x" * (api_surface._TS_ALIAS_TEXT_LIMIT - 2) + "'"
    assert len(value) == api_surface._TS_ALIAS_TEXT_LIMIT
    types = _extract_source(tmp_path, f"export type K = {value};\n")
    assert _type(types, "K")["alias_of"] == value


def test_alias_of_is_absent_on_other_kinds(sample_types: list[dict[str, Any]]) -> None:
    for entry in sample_types:
        if entry["kind"] != "type_alias_declaration":
            assert "alias_of" not in entry, entry["name"]
        else:
            assert entry["alias_of"], entry["name"]


def test_interface_method_optional_is_a_modifier(sample_types: list[dict[str, Any]]) -> None:
    method = _methods(_type(sample_types, "A"))["m"]
    assert method["modifiers"] == ["optional"]
    assert method["params"] == [{"name": "a", "type": "number"}]


def test_class_field_optional_and_static_modifiers(sample_types: list[dict[str, Any]]) -> None:
    props = _props(_type(sample_types, "Document"))
    assert props["title"]["modifiers"] == ["optional"]
    assert props["title"]["writable"] is True
    assert props["VERSION"]["modifiers"] == ["static"]
    assert props["VERSION"]["is_static"] is True


def test_readonly_class_field_is_not_writable(sample_types: list[dict[str, Any]]) -> None:
    props = _props(_type(sample_types, "Document"))
    assert props["VERSION"]["writable"] is False
    assert props["title"]["writable"] is True


def test_plain_method_has_no_modifiers_key(sample_types: list[dict[str, Any]]) -> None:
    methods = _methods(_type(sample_types, "Document"))
    for name in ("save", "configure", "markModified", "constructor"):
        assert "modifiers" not in methods[name], name


def test_constructor_default_is_captured(sample_types: list[dict[str, Any]]) -> None:
    constructor = _methods(_type(sample_types, "Document"))["constructor"]
    assert constructor["is_constructor"] is True
    assert constructor["params"] == [{"name": "createDefault", "type": "boolean", "default": "true"}]


def test_optional_param_is_marked_not_defaulted(sample_types: list[dict[str, Any]]) -> None:
    save = _methods(_type(sample_types, "Document"))["save"]
    assert _param(save, "path") == {"name": "path", "type": "string", "optional": True}
    assert _param(save, "flag") == {"name": "flag", "type": "", "default": "true"}


def test_param_default_text_is_cut_at_120(tmp_path: Path) -> None:
    long_default = "'" + "d" * 200 + "'"
    types = _extract_source(
        tmp_path,
        f"export class C {{\n  run(text: string = {long_default}, short = 'ok'): void {{}}\n}}\n",
    )
    run = _methods(_type(types, "C"))["run"]
    default = _param(run, "text")["default"]
    assert len(default) == 120
    assert default.endswith("...")
    assert default.startswith("'ddd")
    assert _param(run, "short")["default"] == "'ok'"


def test_rest_param_keeps_the_ellipsis_name(sample_types: list[dict[str, Any]]) -> None:
    save = _methods(_type(sample_types, "Document"))["save"]
    assert [p["name"] for p in save["params"]] == ["path", "flag", "...rest"]
    assert _param(save, "...rest")["type"] == "string[]"


def test_destructured_ts_param_uses_the_shared_helper(sample_types: list[dict[str, Any]]) -> None:
    configure = _methods(_type(sample_types, "Document"))["configure"]
    assert len(configure["params"]) == 1
    param = configure["params"][0]
    assert param == {"name": "options", "type": "Opts", "default": "{ depth: 1 }"}


def test_destructured_ts_param_takes_its_name_from_the_jsdoc_object_tag(tmp_path: Path) -> None:
    types = _extract_source(
        tmp_path,
        "/**\n * Build it.\n * @param {object} config - The configuration.\n */\n"
        "export function build({ a }: Opts): void {}\n",
    )
    assert _type(types, "build")["params"][0]["name"] == "config"


def test_annotation_wins_over_synthesised_object_type(tmp_path: Path) -> None:
    types = _extract_source(
        tmp_path,
        "export function build({ a, b }: { a: number; b?: string }, { c }: Other = {}): void {}\n",
    )
    build = _type(types, "build")
    first, second = build["params"]
    assert first["type"] == "{ a: number; b?: string }"
    assert first["name"] == "options"
    assert second["type"] == "Other"
    assert second["name"] == "options2"
    assert second["default"] == "{}"
    assert "default" not in first


def test_destructured_param_without_annotation_synthesises_the_object_type(tmp_path: Path) -> None:
    types = _extract_source(tmp_path, "export function build({ a, b = 2 }): void {}\n")
    assert _type(types, "build")["params"][0]["type"] == "{ a: any; b?: any }"


def test_array_pattern_param_keeps_its_annotation(tmp_path: Path) -> None:
    types = _extract_source(tmp_path, "export function pair([x, y]: number[] = []): void {}\n")
    param = _type(types, "pair")["params"][0]
    assert param["type"] == "number[]"
    assert param["default"] == "[]"
    assert "destructured" not in param


def test_param_without_default_has_no_default_key(sample_types: list[dict[str, Any]]) -> None:
    for entry in sample_types:
        for method in entry["methods"]:
            for param in method["params"]:
                if param["name"] in ("buf", "size", "a"):
                    assert "default" not in param, (entry["name"], method["name"])
                    assert "optional" not in param, (entry["name"], method["name"])


def test_getter_setter_properties_are_unchanged(tmp_path: Path) -> None:
    types = _extract_source(
        tmp_path,
        "export class G {\n  get size(): number { return 1; }\n  set size(v: number) {}\n"
        "  get label(): string { return ''; }\n}\n",
    )
    props = _props(_type(types, "G"))
    assert props["size"] == {
        "name": "size",
        "type": "number",
        "kind": "property",
        "access_mode": "readwrite",
        "doc": "",
        "line": 2,
        "file": "src/index.ts",
    }
    assert props["label"]["access_mode"] == "readonly"
    assert "modifiers" not in props["label"]
    assert "writable" not in props["label"]


def test_internal_member_is_not_published(sample_types: list[dict[str, Any]]) -> None:
    document = _type(sample_types, "Document")
    assert "allocObject" not in _methods(document)
    assert "open" in _methods(document)


def test_hidden_interface_is_not_published(sample_types: list[dict[str, Any]]) -> None:
    assert "HiddenThing" not in {t["name"] for t in sample_types}
    assert {"A", "T", "ArrowShapeType", "Opts", "OpenOptions", "Document"} <= {
        t["name"] for t in sample_types
    }


def test_tag_word_inside_prose_does_not_hide_a_member(sample_types: list[dict[str, Any]]) -> None:
    mark = _methods(_type(sample_types, "Document"))["markModified"]
    assert mark["doc"] == "Not @internalized prose."


@pytest.mark.parametrize("tag", ["@internal", "@hidden", "@ignore"])
def test_each_hiding_tag_hides_members_and_types(tmp_path: Path, tag: str) -> None:
    source = (
        f"/** {tag} */\nexport interface Gone {{ q: number }}\n"
        f"/** {tag} */\nexport type GoneAlias = 'a' | 'b';\n"
        "export interface Kept {\n"
        f"  /** {tag} */\n  secret: string;\n"
        f"  /**\n   * Documented.\n   * {tag}\n   */\n  hiddenMethod(): void;\n"
        "  shown: number;\n}\n"
        "export class Box {\n"
        f"  /** {tag} */\n  helper(): void {{}}\n"
        "  /** Visible. */\n  open(): void {}\n}\n"
    )
    types = _extract_source(tmp_path, source)
    assert {t["name"] for t in types} == {"Kept", "Box"}
    kept = _type(types, "Kept")
    assert set(_props(kept)) == {"shown"}
    assert _methods(kept) == {}
    assert set(_methods(_type(types, "Box"))) == {"open"}


def test_hidden_members_leave_no_claim(tmp_path: Path) -> None:
    source = "export class Box {\n  /** @internal */\n  helper(): void {}\n  open(): void {}\n}\n"
    root = _write_tree(tmp_path / "tree", source)
    result = api_surface.extract_api_surface(get_parser("typescript"), "typescript", root, root, "x")
    texts = [claim["text"] for claim in result[1]]
    assert any("Box.open" in text for text in texts)
    assert not any("helper" in text for text in texts)


JS_SOURCE = """\
export class Client {
  static async open(path, options = {}, ...rest) {}

  async send({ to, cc = [] }, flag = true) {}

  plain(a, b) {}
}
"""


def _extract_js(tmp_path: Path, source: str = JS_SOURCE) -> list[dict[str, Any]]:
    root = tmp_path / "js"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name": "t", "type": "module"}\n', encoding="utf-8")
    (root / "src" / "index.js").write_text(source, encoding="utf-8")
    return _extract(root, {"platform": "nodejs", "family": "x", "repository": "x/y"})


def test_javascript_class_static_and_async_are_marked(tmp_path: Path) -> None:
    client = _type(_extract_js(tmp_path), "Client")
    methods = _methods(client)
    assert methods["open"]["modifiers"] == ["static", "async"]
    assert methods["send"]["modifiers"] == ["async"]
    assert "modifiers" not in methods["plain"]


def test_javascript_param_branch_is_unchanged_by_this_card(tmp_path: Path) -> None:
    """The parameter output equals what the extractor produced before this card (TC-521's)."""
    methods = _methods(_type(_extract_js(tmp_path), "Client"))
    assert methods["open"]["params"] == [
        {"name": "path", "type": ""},
        {"name": "options", "type": "", "default": "{}"},
        {"name": "...rest", "type": ""},
    ]
    assert methods["send"]["params"] == [
        {
            "name": "options",
            "type": "{ to: any; cc?: any }",
            "destructured": [
                {"name": "to", "type": "any", "default": "", "optional": False, "doc": ""},
                {"name": "cc", "type": "any", "default": "[]", "optional": True, "doc": ""},
            ],
        },
        {"name": "flag", "type": "", "default": "true"},
    ]
    assert methods["plain"]["params"] == [{"name": "a", "type": ""}, {"name": "b", "type": ""}]


def test_non_typescript_languages_are_unaffected(tmp_path: Path) -> None:
    java = tmp_path / "java"
    (java / "src" / "main" / "java" / "p").mkdir(parents=True)
    (java / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>p</groupId>"
        "<artifactId>a</artifactId><version>1</version></project>",
        encoding="utf-8",
    )
    (java / "src" / "main" / "java" / "p" / "Greeter.java").write_text(
        "package p;\npublic class Greeter {\n    public static int count(int a, String b) { return a; }\n}\n",
        encoding="utf-8",
    )
    types = _extract(java, {"platform": "java", "family": "x", "repository": "x/y"})
    greeter = _type(types, "Greeter")
    assert greeter["methods"] == [
        {
            "name": "count",
            "params": [{"name": "a", "type": "int"}, {"name": "b", "type": "String"}],
            "return_type": "int",
            "doc": "",
            "line": 3,
            "is_constructor": False,
            "deprecated": False,
            "deprecated_reason": "",
            "file": "src/main/java/p/Greeter.java",
        }
    ]
    assert "alias_of" not in greeter

    python = tmp_path / "py"
    (python / "pkg").mkdir(parents=True)
    (python / "pkg" / "__init__.py").write_text("from .m import F\n", encoding="utf-8")
    (python / "pkg" / "m.py").write_text(
        'class F:\n    """F."""\n\n    def run(self, a, b=1):\n        """Run."""\n',
        encoding="utf-8",
    )
    py_types = _extract(python, {"platform": "python", "family": "x", "repository": "x/y"})
    serialized = json.dumps(py_types)
    assert '"alias_of"' not in serialized
    assert '"optional"' not in serialized


def test_extraction_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    shutil.copytree(SAMPLE, first)
    shutil.copytree(SAMPLE, second)
    assert json.dumps(_extract(first), sort_keys=True) == json.dumps(_extract(second), sort_keys=True)


def test_extractor_version_is_at_least_five() -> None:
    assert run_extraction.EXTRACTOR_VERSION >= 5


def test_member_flags_helper_reads_the_tokens() -> None:
    source = b"interface A { readonly a?: number; m?(): void }\nclass C { static async f() {} }\n"
    tree = get_parser("typescript").parse(source)

    def first(node: Any, kind: str) -> Any:
        if node.type == kind:
            return node
        for child in node.children:
            found = first(child, kind)
            if found is not None:
                return found
        return None

    assert api_surface._ts_member_flags(first(tree.root_node, "property_signature")) == (
        True,
        True,
        False,
        False,
    )
    assert api_surface._ts_member_flags(first(tree.root_node, "method_signature")) == (
        False,
        True,
        False,
        False,
    )
    assert api_surface._ts_member_flags(first(tree.root_node, "method_definition")) == (
        False,
        False,
        True,
        True,
    )


def test_member_flags_helper_is_wired() -> None:
    source = Path(api_surface.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"^    return readonly, optional, is_static, is_async$", source, re.M)) == 1
    extract_source = inspect.getsource(api_surface.extract_api_surface)
    property_branch = extract_source.split('if mnode.type in ("property_declaration"', 1)[1].split(
        "params = _extract_method_params(mnode, language)", 1
    )[0]
    method_branch = extract_source.split("params = _extract_method_params(mnode, language)", 1)[1].split(
        "methods.append(method_entry)", 1
    )[0]
    assert "_ts_member_flags(" in property_branch
    assert "_ts_member_flags(" in method_branch
    # the parameter reader and the hidden-tag filter are called by the entrypoint too
    assert "_ts_param(" in inspect.getsource(api_surface._extract_method_params)
    assert extract_source.count("_ts_is_hidden(") == 2
    assert "_ts_alias_text(" in extract_source


def test_rendered_sample_shows_every_mark(sample_types: list[dict[str, Any]]) -> None:
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": sample_types}
    text = "\n".join(c.text for c in build_chunks_from_api_surface(fixture, title="t", max_types=10_000))
    lines = text.splitlines()
    assert "  - x: number [optional] (writable)" in lines
    assert "  - y: string (read-only)" in lines
    assert "  - open(buf: Uint8Array, opts: OpenOptions = {}) -> Promise<Document> [static, async]" in lines
    assert "  - save(path?: string, flag = true, ...rest: string[]) -> void" in lines
    assert "  - constructor(createDefault: boolean = true)" in lines
    assert "Alias-Of: 'a' | 'b'" in lines
    assert "allocObject" not in text
    assert "HiddenThing" not in text
