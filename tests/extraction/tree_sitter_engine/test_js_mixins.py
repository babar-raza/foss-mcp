"""TC-521: JavaScript object-literal mixins, aliased bases, destructured parameters and class docs.

The jmap/nodejs pilot composes its client from ``export const MailClientMixin = { async listMailboxes()
{...} }`` objects applied with ``Object.assign(JmapClient.prototype, MailClientMixin)`` onto ``class
JmapClient extends CoreClient {}`` (``CoreClient`` is the import alias of the core client). Every test
runs ``run_extraction.extract_from_clone`` on a COPY of the committed sample tree (or on a small tree
written here), offline.
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
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "js_mixin_sample"
MANIFEST = {"platform": "nodejs", "family": "jmap", "repository": "x/y"}

MAIL_METHODS = ["listMailboxes", "getMailbox", "createMailbox", "deleteMailbox"]
SUBMISSION_METHODS = ["send", "cancelSend"]


def _extract(root: Path, manifest: dict[str, str] | None = None) -> list[dict[str, Any]]:
    types, unresolved = run_extraction.extract_from_clone(root, manifest or MANIFEST)
    assert unresolved == []
    return types


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    (root / "package.json").write_text('{"name": "t", "type": "module"}\n', encoding="utf-8")
    for name, text in files.items():
        target = root / "src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def sample_types(tmp_path: Path) -> list[dict[str, Any]]:
    root = tmp_path / "sample"
    shutil.copytree(SAMPLE, root)
    return _extract(root)


def _by_file(types: list[dict[str, Any]], name: str, file: str) -> dict[str, Any]:
    matches = [t for t in types if t["name"] == name and t["file"] == file]
    assert len(matches) == 1, [(t["name"], t["file"]) for t in types]
    return matches[0]


def _methods(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in entry["methods"]}


def _wrapper(types: list[dict[str, Any]]) -> dict[str, Any]:
    return _by_file(types, "JmapClient", "src/index.js")


def test_wrapper_class_gets_the_mixin_methods(sample_types: list[dict[str, Any]]) -> None:
    methods = _methods(_wrapper(sample_types))
    for name in MAIL_METHODS + SUBMISSION_METHODS:
        assert name in methods, name


def test_mixin_method_keeps_the_mixin_file_and_name(sample_types: list[dict[str, Any]]) -> None:
    methods = _methods(_wrapper(sample_types))
    assert methods["listMailboxes"]["mixin"] == "MailClientMixin"
    assert methods["listMailboxes"]["file"] == "src/client-mail.js"
    assert methods["listMailboxes"]["line"] == 12
    assert methods["send"]["mixin"] == "SubmissionClientMixin"
    assert methods["send"]["file"] == "src/client-submission.js"
    assert methods["listMailboxes"]["is_constructor"] is False
    assert methods["listMailboxes"]["doc"] == "List the mailboxes of an account."
    for key in ("name", "params", "return_type", "doc", "line", "file", "is_constructor", "deprecated"):
        assert key in methods["getMailbox"], key


def test_mixin_async_is_a_modifier(sample_types: list[dict[str, Any]]) -> None:
    methods = _methods(_wrapper(sample_types))
    assert methods["listMailboxes"]["modifiers"] == ["async"]
    assert methods["cancelSend"]["modifiers"] == ["async"]
    # a plain (non-async) member has no modifiers key at all
    assert "modifiers" not in methods["countMailboxes"]


def test_mixin_defaults_and_params_are_extracted(sample_types: list[dict[str, Any]]) -> None:
    methods = _methods(_wrapper(sample_types))
    params = methods["listMailboxes"]["params"]
    assert [p["name"] for p in params] == ["accountId", "ids", "properties"]
    assert "default" not in params[0]
    assert params[1]["default"] == "null"
    assert params[2]["default"] == "null"
    delete_params = methods["deleteMailbox"]["params"]
    assert [p["name"] for p in delete_params] == ["accountId", "id", "...rest"]
    assert methods["countMailboxes"]["params"] == [{"name": "accountId", "type": ""}]


def test_js_rest_and_default_params_outside_mixins(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "scratch.js": (
                "export class Scratch {\n"
                "  async f(a, b = null, ...rest) {}\n"
                '  g(x = "a long default", y = {}) {}\n'
                "}\n"
            )
        },
    )
    scratch = _methods(_by_file(_extract(root), "Scratch", "src/scratch.js"))
    assert scratch["f"]["params"] == [
        {"name": "a", "type": ""},
        {"name": "b", "type": "", "default": "null"},
        {"name": "...rest", "type": ""},
    ]
    assert scratch["g"]["params"][0] == {"name": "x", "type": "", "default": '"a long default"'}
    assert scratch["g"]["params"][1]["default"] == "{}"


def test_private_and_unapplied_mixin_members_are_not_published(sample_types: list[dict[str, Any]]) -> None:
    published = {m["name"] for t in sample_types for m in t["methods"]}
    assert "_private" not in published
    assert "neverApplied" not in published
    assert "_internal" not in published


def test_mixin_const_is_not_a_type(sample_types: list[dict[str, Any]]) -> None:
    names = {t["name"] for t in sample_types}
    assert not names & {"MailClientMixin", "SubmissionClientMixin", "UnusedMixin", "AMixin", "BMixin"}


def test_object_literal_methods_are_not_top_level_functions(sample_types: list[dict[str, Any]]) -> None:
    assert [t["name"] for t in sample_types if t.get("kind") == "function"] == []
    top_level_names = {t["name"] for t in sample_types}
    assert not top_level_names & {"listMailboxes", "send", "alpha", "ping", "neverApplied"}


def test_multi_argument_object_assign(sample_types: list[dict[str, Any]]) -> None:
    hub = _by_file(sample_types, "Hub", "src/multi.js")
    names = [m["name"] for m in hub["methods"]]
    # declared method first, then mixin argument order (A then B), then the inline literal
    assert names == ["start", "alpha", "beta", "ping"]
    assert _methods(hub)["alpha"]["mixin"] == "AMixin"
    assert _methods(hub)["beta"]["mixin"] == "BMixin"
    assert _methods(hub)["beta"]["modifiers"] == ["async"]


def test_inline_object_literal_assign(sample_types: list[dict[str, Any]]) -> None:
    ping = _methods(_by_file(sample_types, "Hub", "src/multi.js"))["ping"]
    assert ping["mixin"] == "<inline>"
    assert ping["file"] == "src/multi.js"
    assert ping["doc"] == "Ping the hub."


def test_unresolvable_mixin_is_skipped_silently(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "a.js": (
                "export class Widget { real() {} }\n"
                "Object.assign(Widget.prototype, MissingMixin, mixins.Other, ...spread);\n"
                "Object.assign(Nowhere.prototype, { lost() {} });\n"
                "Object.assign(somethingElse, { nope() {} });\n"
            )
        },
    )
    types = _extract(root)
    assert [m["name"] for m in _by_file(types, "Widget", "src/a.js")["methods"]] == ["real"]
    assert [t["name"] for t in types] == ["Widget"]


def test_application_in_another_file_than_the_mixin(sample_types: list[dict[str, Any]]) -> None:
    # src/client-mail.js defines MailClientMixin, src/index.js applies it: the member keeps the
    # defining file and the class keeps its own file
    wrapper = _wrapper(sample_types)
    assert wrapper["file"] == "src/index.js"
    assert {m["file"] for m in wrapper["methods"] if m.get("mixin")} == {
        "src/client-mail.js",
        "src/client-submission.js",
    }


def test_application_resolves_through_an_aliased_import(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "mixins/one.js": "export const Shared = { shared() {} };\n",
            "mixins/two.js": "export const Shared = { other() {} };\n",
            "main.js": (
                'import { Shared as Chosen } from "./mixins/two.js";\n'
                "export class Host {}\n"
                "Object.assign(Host.prototype, Chosen);\n"
            ),
        },
    )
    host = _by_file(_extract(root), "Host", "src/main.js")
    assert [m["name"] for m in host["methods"]] == ["other"]
    assert host["methods"][0]["file"] == "src/mixins/two.js"


def test_alias_base_is_resolved_to_the_core_class(sample_types: list[dict[str, Any]]) -> None:
    wrapper = _wrapper(sample_types)
    core = _by_file(sample_types, "JmapClient", "src/client-core.js")
    assert core["class_import"] == "client-core.JmapClient"
    # the base stays as written (TC-315 pins it); the alias is resolved beside it
    assert wrapper["bases"] == ["CoreClient"]
    assert wrapper["base_imports"] == {"CoreClient": "client-core.JmapClient"}
    assert "base_imports" not in core


def test_wrapper_inherits_core_methods_with_inherited_from(sample_types: list[dict[str, Any]]) -> None:
    wrapper = _wrapper(sample_types)
    inherited = {m["name"]: m for m in wrapper["methods"] if m.get("inherited_from")}
    assert set(inherited) == {"connect", "close", "sendRequest"}
    assert all(m["inherited_from"] == "client-core.JmapClient" for m in inherited.values())
    # constructors are never inherited, and the private member never reached the core entry
    assert "constructor" not in _methods(wrapper)
    assert len(wrapper["methods"]) == 4 + 1 + 2 + 3  # mail + arrow member + submission + inherited


def test_unresolved_alias_base_stays_as_written(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "core.js": "export class Core { run() {} }\n",
            "wrapper.js": (
                'import { Core as Base } from "./core.js";\n'
                'import { Missing as Other } from "./nowhere.js";\n'
                "export class Good extends Base {}\n"
                "export class Bad extends Other {}\n"
            ),
        },
    )
    types = _extract(root)
    good = _by_file(types, "Good", "src/wrapper.js")
    bad = _by_file(types, "Bad", "src/wrapper.js")
    assert good["base_imports"] == {"Base": "core.Core"}
    assert [m["name"] for m in good["methods"]] == ["run"]
    assert bad["bases"] == ["Other"]
    assert "base_imports" not in bad
    assert bad["methods"] == []


def test_destructured_constructor_is_one_object_param(sample_types: list[dict[str, Any]]) -> None:
    constructor = _methods(_by_file(sample_types, "Mailbox", "src/models/Mailbox.js"))["constructor"]
    assert len(constructor["params"]) == 1
    param = constructor["params"][0]
    assert param["name"] == "params"
    assert param["type"] == "{ id?: string; name: string; parentId?: string | null; sortOrder?: number }"
    assert param["default"] == "{}"


def test_destructured_keys_carry_jsdoc_types_and_defaults(sample_types: list[dict[str, Any]]) -> None:
    constructor = _methods(_by_file(sample_types, "Mailbox", "src/models/Mailbox.js"))["constructor"]
    keys = {k["name"]: k for k in constructor["params"][0]["destructured"]}
    assert list(keys) == ["id", "name", "parentId", "sortOrder"]
    assert keys["name"] == {
        "name": "name",
        "type": "string",
        "default": "",
        "optional": False,
        "doc": "Human readable name (required).",
    }
    assert keys["id"]["optional"] is True
    assert keys["parentId"]["type"] == "string | null"
    assert keys["parentId"]["default"] == "null"
    assert keys["sortOrder"]["default"] == "0"
    assert keys["sortOrder"]["type"] == "number"


def test_undocumented_destructuring_uses_options_and_any(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "o.js": (
                "export class Opt {\n"
                "  constructor({ a, b = 2, c: renamed = 'x', ...others }) {}\n"
                "  run({ k } = { k: 1 }) {}\n"
                "}\n"
            )
        },
    )
    methods = _methods(_by_file(_extract(root), "Opt", "src/o.js"))
    constructor = methods["constructor"]["params"][0]
    assert constructor["name"] == "options"
    assert constructor["type"] == "{ a: any; b?: any; c?: any }"
    assert "default" not in constructor
    assert [k["default"] for k in constructor["destructured"]] == ["", "2", "'x'"]
    assert methods["run"]["params"][0]["default"] == "{ k: 1 }"


def test_destructured_constructor_adds_properties(sample_types: list[dict[str, Any]]) -> None:
    mailbox = _by_file(sample_types, "Mailbox", "src/models/Mailbox.js")
    properties = {p["name"]: p for p in mailbox["properties"]}
    assert list(properties) == ["id", "name", "parentId", "sortOrder"]
    assert properties["parentId"]["type"] == "string | null"
    assert properties["name"]["writable"] is True
    assert properties["name"]["doc"] == "Human readable name (required)."


def test_destructured_constructor_does_not_duplicate_a_declared_property(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {"p.js": "export class P {\n  name = 'x';\n  constructor({ name, other }) {}\n}\n"},
    )
    properties = _by_file(_extract(root), "P", "src/p.js")["properties"]
    assert sorted(p["name"] for p in properties) == ["name", "other"]


def test_positional_params_are_unchanged(sample_types: list[dict[str, Any]]) -> None:
    describe = _methods(_by_file(sample_types, "Mailbox", "src/models/Mailbox.js"))["describe"]
    assert describe["params"] == [{"name": "prefix", "type": ""}]
    core = _methods(_by_file(sample_types, "JmapClient", "src/client-core.js"))
    assert core["constructor"]["params"] == [{"name": "options", "type": ""}]
    assert core["sendRequest"]["params"] == [{"name": "request", "type": ""}]
    assert "destructured" not in json.dumps(core)


def test_exported_js_class_keeps_its_first_sentence(sample_types: list[dict[str, Any]]) -> None:
    assert (
        _by_file(sample_types, "Mailbox", "src/models/Mailbox.js")["doc"]
        == "Represents a named set of Emails."
    )
    assert _by_file(sample_types, "JmapClient", "src/client-core.js")["doc"].startswith(
        "Core client that opens"
    )
    assert _by_file(sample_types, "Hub", "src/multi.js")["doc"] == "A hub composed from several mixins."
    # a declaration with no JSDoc stays empty
    assert _wrapper(sample_types)["doc"] == ""


def test_exported_js_function_keeps_its_first_sentence(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"f.js": "/** Make a thing. Longer text. */\nexport function make(a) {}\n"})
    assert _by_file(_extract(root), "make", "src/f.js")["doc"] == "Make a thing."


def test_existing_member_name_is_never_replaced(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "a.js": (
                "const M = { run() {}, extra() {} };\n"
                "export class A {\n  /** Own. */\n  run() {}\n}\n"
                "Object.assign(A.prototype, M);\n"
            )
        },
    )
    methods = _by_file(_extract(root), "A", "src/a.js")["methods"]
    assert [m["name"] for m in methods] == ["run", "extra"]
    assert "mixin" not in methods[0] and methods[0]["doc"] == "Own."
    assert methods[1]["mixin"] == "M"


def test_getters_setters_and_computed_keys_are_not_methods(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "g.js": (
                "const M = {\n  get size() { return 1; },\n  set size(v) {},\n  ['dyn' + 1]() {},\n"
                "  value: 5,\n  #hidden() {},\n  ok() {},\n  constructor() {},\n};\n"
                "export class G {}\nObject.assign(G.prototype, M);\n"
            )
        },
    )
    assert [m["name"] for m in _by_file(_extract(root), "G", "src/g.js")["methods"]] == ["ok"]


def test_function_expression_pair_and_single_arrow_param(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "h.js": (
                "export const M = {\n  viaFn: function (a, b = 1) {},\n  viaArrow: x => x,\n"
                "  later: async (y) => y,\n};\n"
                "export class H {}\nObject.assign(H.prototype, M);\n"
            )
        },
    )
    methods = _methods(_by_file(_extract(root), "H", "src/h.js"))
    assert methods["viaFn"]["params"] == [
        {"name": "a", "type": ""},
        {"name": "b", "type": "", "default": "1"},
    ]
    assert methods["viaArrow"]["params"] == [{"name": "x", "type": ""}]
    assert methods["later"]["modifiers"] == ["async"]


def test_mixin_methods_are_copies_per_class(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "c.js": (
                "const M = { shared() {} };\nexport class One {}\nexport class Two {}\n"
                "Object.assign(One.prototype, M);\nObject.assign(Two.prototype, M);\n"
            )
        },
    )
    types = _extract(root)
    one, two = _by_file(types, "One", "src/c.js"), _by_file(types, "Two", "src/c.js")
    assert one["methods"] == two["methods"]
    assert one["methods"][0] is not two["methods"][0]


def test_extraction_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    shutil.copytree(SAMPLE, first)
    shutil.copytree(SAMPLE, second)
    assert json.dumps(_extract(first), sort_keys=True) == json.dumps(_extract(second), sort_keys=True)


def test_typescript_language_is_unaffected(tmp_path: Path) -> None:
    root = tmp_path / "ts"
    root.mkdir()
    (root / "package.json").write_text('{"name": "ts-sample"}\n', encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "widget.ts").write_text(
        "export class Widget {\n"
        "  run(a: string, b?: number, c = 3, ...rest: string[]): void {}\n"
        "  open({ x, y }: Opts = {}, z = 1): void {}\n"
        "}\n"
        "export const Mixin = { hello() {}, async later(q = 1) {} };\n"
        "export function build({ a, b }: Opts, c = 1) {}\n"
        "Object.assign(Widget.prototype, Mixin);\n",
        encoding="utf-8",
    )
    types = _extract(root, {"platform": "typescript", "family": "x", "repository": "x/y"})
    widget = next(t for t in types if t["name"] == "Widget")
    assert [m["name"] for m in widget["methods"]] == ["run", "open"]
    # the parameter output is byte-identical to the output before TC-521
    assert widget["methods"][0]["params"] == [
        {"name": "a", "type": "string"},
        {"name": "b", "type": "number"},
        {"name": "c", "type": ""},
    ]
    assert widget["methods"][1]["params"] == [{"name": "z", "type": ""}]
    assert next(t for t in types if t["name"] == "build")["params"] == [{"name": "c", "type": ""}]
    serialized = json.dumps(types)
    assert '"mixin"' not in serialized
    assert '"destructured"' not in serialized
    assert '"base_imports"' not in serialized


def test_extractor_version_is_at_least_three() -> None:
    assert run_extraction.EXTRACTOR_VERSION >= 3


def test_mixin_assign_callee_is_wired() -> None:
    source = Path(api_surface.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"^_JS_MIXIN_ASSIGN_CALLEE = ", source, re.M)) == 1
    assert 'Object.assign"' in source
    extract_source = inspect.getsource(api_surface.extract_api_surface)
    attach_source = inspect.getsource(api_surface._attach_js_mixins)
    assert "_JS_MIXIN_ASSIGN_CALLEE" in extract_source or "_JS_MIXIN_ASSIGN_CALLEE" in attach_source
    # AGENTS.md integration rule: the entrypoint itself calls the wired pass and the alias pass
    assert "_attach_js_mixins(" in extract_source
    assert "_resolve_js_alias_bases(" in extract_source
    attach_at = extract_source.index("_attach_js_mixins(")
    assert attach_at < extract_source.index("classes = consolidate_classes(classes, language)")
    assert attach_at < extract_source.index("_flatten_inheritance(classes)")


def test_mixin_claims_are_emitted(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    shutil.copytree(SAMPLE, root)
    from tree_sitter_language_pack import get_parser

    result = api_surface.extract_api_surface(
        get_parser("javascript"), "javascript", root / "src", root, "jmap"
    )
    texts = [claim["text"] for claim in result[1]]
    assert any(text.startswith("JmapClient.listMailboxes(accountId, ids, properties)") for text in texts)
    assert any(text == "Mailbox.parentId property of type string | null" for text in texts)


def test_list_members_of_the_owner_is_not_empty(sample_types: list[dict[str, Any]]) -> None:
    """The exact failure of the committed jmap_nodejs fixture is gone for the sample: the wrapper's
    ``Methods:`` block lists the mixin methods and the inherited core methods."""
    fixture = {
        "source_repository": "x/y",
        "source_commit": "0" * 40,
        "type_count": len(sample_types),
        "types": sample_types,
    }
    chunks = build_chunks_from_api_surface(fixture, title="sample", max_types=10_000)
    owner = next(chunk for chunk in chunks if chunk.text.splitlines()[0] == "FQN: JmapClient")
    lines = owner.text.splitlines()
    methods_block: list[str] = []
    for line in lines[lines.index("Methods:") + 1 :]:
        if not line.startswith("  - "):
            break
        methods_block.append(line[len("  - ") :].strip())
    names = {re.split(r"\(|:", item)[0].strip() for item in methods_block}
    assert {"listMailboxes", "createMailbox", "connect"} <= names
    assert len(methods_block) > 6
    listed = next(item for item in methods_block if item.startswith("listMailboxes"))
    assert listed.endswith("[async]  (mixin MailClientMixin, src/client-mail.js)")
    assert "API-Coverage: " in owner.text
