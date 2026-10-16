"""TC-522: the Python reader keeps how an API is CALLED - defaults, ``*`` and ``/`` markers, decorators.

The independent pre-production review (RST-05) ran an ``ast`` ground truth over the upstream sources and
found every parameter default, every keyword-only marker and every ``@staticmethod`` / ``@classmethod``
lost on the way to the served signature, and eight pdf/python facade classes served without their own
methods because the re-export shell and its definition were two unrelated entries.

Everything here is offline: the sample package is written from the string constants below into
``tmp_path`` (never committed as ``.py`` under tests/fixtures), read by ``python_surface``, adapted by
``run_extraction`` and rendered by ``chunk_builder``. The oracle test compares every rendered signature
with one computed independently from ``ast.parse`` of the same source.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.extraction.tree_sitter_engine.python_surface import PublicSymbol, inspect_public_surface
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

PACKAGE = "samplepkg"
LONG_DEFAULT = "a" * 130

INIT_PY = """\
from ._impl import convert
from .facades import Facade
from .models import Account, Mode
from .util import load
"""

MODELS_PY = f'''\
import enum


def _trace(function):
    return function


class Mode(enum.Enum):
    """A mode."""

    READ = "r"
    WRITE = "w"


class Account:
    """An account."""

    def __init__(self, id, password=None, *, retries=3):
        """Create an account."""

    @classmethod
    def open(cls, path, /, mode="r", *args, strict, quiet=False, **extra):
        """Open one."""

    @staticmethod
    def make(a, /, b=1):
        """Make one."""

    async def sync(self, *, force=False):
        """Synchronise."""

    @_trace
    @staticmethod
    def traced(x=2):
        """A static method with another decorator stacked on it."""

    def close(self, force: bool = False, *, timeout: float | None = None) -> None:
        """Close."""

    def options(self, spec={{"k": [1, 2]}}, text="{LONG_DEFAULT}"):
        """Defaults that are containers and a very long one."""

    def plain(self, a, b):
        """No annotations, no defaults."""

    @property
    def size(self) -> int:
        """Writable."""

    @size.setter
    def size(self, value):
        pass

    @property
    def label(self) -> str:
        """Read-only."""
'''

UTIL_PY = """\
def load(path, *, encoding="utf-8"):
    \"\"\"Load.\"\"\"


async def fetch(url, retries=3):
    \"\"\"Fetch.\"\"\"


def combine(items=(), sep=", "):
    \"\"\"Combine.\"\"\"
"""

IMPL_PY = """\
async def convert(src, dst=None, *, fast=False) -> bytes:
    \"\"\"Convert; defined in a private module and re-exported.\"\"\"
"""

FACADES_PY = """\
class _Base:
    def bind(self, path=None, *, overwrite=False):
        \"\"\"Bind.\"\"\"

    def release(self):
        \"\"\"Release.\"\"\"


class Facade(_Base):
    \"\"\"A facade built on a private base.\"\"\"

    def encrypt(self, user, owner, *, permissions=0, keysize=128):
        \"\"\"Encrypt.\"\"\"
"""

SOURCES = {
    "__init__.py": INIT_PY,
    "models.py": MODELS_PY,
    "util.py": UTIL_PY,
    "_impl.py": IMPL_PY,
    "facades.py": FACADES_PY,
}


def _write_sample(root: Path) -> None:
    for relative, text in SOURCES.items():
        path = root / PACKAGE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _surface(root: Path) -> python_surface.PublicSurface:
    return inspect_public_surface(root, [PACKAGE])


def _symbols(root: Path) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in _surface(root).symbols}


def _types(root: Path) -> dict[str, dict[str, Any]]:
    return {
        entry["class_import"]: entry for entry in run_extraction._python_types_from_surface(_surface(root))
    }


def _method(entry: dict[str, Any], name: str) -> dict[str, Any]:
    return next(method for method in entry["methods"] if method["name"] == name)


def _param(method: dict[str, Any], name: str) -> dict[str, Any]:
    return next(param for param in method["params"] if param["name"] == name)


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("python_fidelity")
    _write_sample(root)
    return root


@pytest.fixture(scope="module")
def symbols(sample: Path) -> dict[str, PublicSymbol]:
    return _symbols(sample)


@pytest.fixture(scope="module")
def types(sample: Path) -> dict[str, dict[str, Any]]:
    return _types(sample)


# --------------------------------------------------------------------------- the extraction


def test_defaults_are_captured_as_source_text(types: dict[str, dict[str, Any]]) -> None:
    close = _method(types["samplepkg.models.Account"], "close")
    assert _param(close, "force")["default"] == "False"
    assert _param(close, "timeout")["default"] == "None"
    assert "default" not in _param(_method(types["samplepkg.models.Account"], "make"), "a")
    options = _method(types["samplepkg.models.Account"], "options")
    assert _param(options, "spec")["default"] == "{'k': [1, 2]}"


def test_long_default_is_cut_at_the_limit_with_an_ellipsis(types: dict[str, dict[str, Any]]) -> None:
    assert python_surface._DEFAULT_TEXT_LIMIT == 120
    text = _param(_method(types["samplepkg.models.Account"], "options"), "text")["default"]
    assert text == repr(LONG_DEFAULT)[:120] + "..."
    assert len(text) == 123


def test_default_exactly_at_the_limit_is_not_cut(tmp_path: Path) -> None:
    exact = "b" * (python_surface._DEFAULT_TEXT_LIMIT - 2)  # the quotes make 120 characters
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "mod.py").write_text(f"def f(x='{exact}'):\n    pass\n", encoding="utf-8")
    default = _symbols_of(tmp_path, "pkg")["pkg.mod.f"].param_details[0][2]
    assert default == repr(exact) and len(default) == 120


def _symbols_of(root: Path, package: str) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, [package]).symbols}


def test_keyword_only_params_carry_a_kind(types: dict[str, dict[str, Any]]) -> None:
    init = _method(types["samplepkg.models.Account"], "__init__")
    assert _param(init, "retries")["kind"] == "keyword_only"
    assert "kind" not in _param(init, "password")
    open_ = _method(types["samplepkg.models.Account"], "open")
    assert _param(open_, "strict")["kind"] == "keyword_only"
    assert "default" not in _param(open_, "strict")
    assert _param(open_, "quiet")["kind"] == "keyword_only"


def test_positional_only_params_carry_a_kind(types: dict[str, dict[str, Any]]) -> None:
    make = _method(types["samplepkg.models.Account"], "make")
    assert _param(make, "a")["kind"] == "positional_only"
    assert "kind" not in _param(make, "b")
    assert _param(_method(types["samplepkg.models.Account"], "open"), "path")["kind"] == "positional_only"


def test_star_args_keep_their_stars_and_no_kind(
    types: dict[str, dict[str, Any]], symbols: dict[str, PublicSymbol]
) -> None:
    open_ = _method(types["samplepkg.models.Account"], "open")
    assert [param["name"] for param in open_["params"]] == [
        "path",
        "mode",
        "*args",
        "strict",
        "quiet",
        "**extra",
    ]
    assert "kind" not in _param(open_, "*args")
    assert "kind" not in _param(open_, "**extra")
    details = {name: kind for name, kind, _ in symbols["samplepkg.models.Account.open"].param_details}
    assert details["*args"] == "var_positional"
    assert details["**extra"] == "var_keyword"


def test_param_details_align_with_params(symbols: dict[str, PublicSymbol]) -> None:
    checked = 0
    for symbol in symbols.values():
        assert [name for name, _, _ in symbol.param_details] == [name for name, _ in symbol.params]
        checked += bool(symbol.params)
    assert checked >= 10
    assert symbols["samplepkg.models.Account"].param_details == ()
    assert symbols["samplepkg.models.Account"].modifiers == ()


def test_staticmethod_is_a_modifier(types: dict[str, dict[str, Any]]) -> None:
    assert _method(types["samplepkg.models.Account"], "make")["modifiers"] == ["static"]


def test_classmethod_is_a_modifier(types: dict[str, dict[str, Any]]) -> None:
    assert _method(types["samplepkg.models.Account"], "open")["modifiers"] == ["classmethod"]


def test_async_is_a_modifier_on_methods_and_functions(types: dict[str, dict[str, Any]]) -> None:
    assert _method(types["samplepkg.models.Account"], "sync")["modifiers"] == ["async"]
    assert types["samplepkg.util.fetch"]["modifiers"] == ["async"]
    assert "modifiers" not in types["samplepkg.util.load"]


def test_stacked_decorators_keep_the_static_modifier(types: dict[str, dict[str, Any]]) -> None:
    assert _method(types["samplepkg.models.Account"], "traced")["modifiers"] == ["static"]


def test_methods_without_decorators_have_no_modifiers_key(types: dict[str, dict[str, Any]]) -> None:
    account = types["samplepkg.models.Account"]
    assert "modifiers" not in _method(account, "plain")
    assert "modifiers" not in _method(account, "close")
    assert "modifiers" not in _method(account, "__init__")


def test_property_with_setter_is_writable_and_getter_only_is_read_only(
    types: dict[str, dict[str, Any]], symbols: dict[str, PublicSymbol]
) -> None:
    properties = {prop["name"]: prop for prop in types["samplepkg.models.Account"]["properties"]}
    assert properties["size"]["writable"] is True
    assert properties["label"]["writable"] is False
    assert symbols["samplepkg.models.Account.size"].modifiers == ()
    assert symbols["samplepkg.models.Account.label"].modifiers == ()


def test_constructor_defaults_are_captured(types: dict[str, dict[str, Any]]) -> None:
    init = _method(types["samplepkg.models.Account"], "__init__")
    assert init["is_constructor"] is True
    assert _param(init, "password")["default"] == "None"
    assert _param(init, "retries")["default"] == "3"
    assert "default" not in _param(init, "id")


def test_module_function_defaults_and_kwonly(types: dict[str, dict[str, Any]]) -> None:
    load = types["samplepkg.util.load"]
    assert load["kind"] == "function"
    assert _param(load, "encoding") == {
        "name": "encoding",
        "type": "",
        "default": "'utf-8'",
        "kind": "keyword_only",
    }
    assert _param(load, "path") == {"name": "path", "type": ""}
    assert _param(types["samplepkg.util.combine"], "sep")["default"] == "', '"
    assert _param(types["samplepkg.util.combine"], "items")["default"] == "()"


def test_reexported_function_keeps_defaults_and_modifiers(types: dict[str, dict[str, Any]]) -> None:
    convert = types["samplepkg.convert"]
    assert convert["kind"] == "function"
    assert _param(convert, "dst")["default"] == "None"
    assert _param(convert, "fast") == {"name": "fast", "type": "", "default": "False", "kind": "keyword_only"}
    assert convert["modifiers"] == ["async"]
    assert convert["return_type"] == "bytes"
    load = types["samplepkg.load"]
    assert _param(load, "encoding")["default"] == "'utf-8'"
    assert "reexport_of" not in convert and "reexport_of" not in load


def test_inherited_private_base_methods_keep_their_defaults(types: dict[str, dict[str, Any]]) -> None:
    for fqn in ("samplepkg.facades.Facade", "samplepkg.Facade"):
        bind = _method(types[fqn], "bind")
        assert _param(bind, "path")["default"] == "None"
        assert _param(bind, "overwrite") == {
            "name": "overwrite",
            "type": "",
            "default": "False",
            "kind": "keyword_only",
        }


def test_reexport_shell_is_flagged_with_its_origin(types: dict[str, dict[str, Any]]) -> None:
    assert types["samplepkg.Facade"]["reexport_of"] == "samplepkg.facades.Facade"
    assert types["samplepkg.Account"]["reexport_of"] == "samplepkg.models.Account"
    assert types["samplepkg.Mode"]["reexport_of"] == "samplepkg.models.Mode"
    assert types["samplepkg.Mode"]["kind"] == "enum"
    assert "reexport_of" not in types["samplepkg.facades.Facade"]
    assert "reexport_of" not in types["samplepkg.models.Account"]


def test_unannotated_param_still_has_an_empty_type(types: dict[str, dict[str, Any]]) -> None:
    plain = _method(types["samplepkg.models.Account"], "plain")
    assert plain["params"] == [{"name": "a", "type": ""}, {"name": "b", "type": ""}]
    close = _method(types["samplepkg.models.Account"], "close")
    assert _param(close, "force")["type"] == "bool"
    assert _param(close, "timeout")["type"] == "float | None"


def test_extraction_is_deterministic(sample: Path) -> None:
    first = json.dumps(run_extraction._python_types_from_surface(_surface(sample)), sort_keys=True)
    second = json.dumps(run_extraction._python_types_from_surface(_surface(sample)), sort_keys=True)
    assert first == second


def test_extractor_version_is_at_least_four() -> None:
    assert run_extraction.EXTRACTOR_VERSION >= 4


# --------------------------------------------------------------------------- grouping and selection


def _artifact(types: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "source_repository": "acme/sample",
        "source_commit": "a" * 40,
        "language": "python",
        "type_count": len(types),
        "types": list(types.values()),
    }


def test_shell_with_inherited_members_stays_grouped_with_its_definition(
    types: dict[str, dict[str, Any]],
) -> None:
    shell = types["samplepkg.Facade"]
    assert shell["methods"], "the shell carries the methods TC-429 gave it from the private base"
    reduced = run_extraction.reduce_fixture(_artifact(types), max_types=1, must_keep={"Facade"})
    kept = {entry["class_import"] for entry in reduced["types"]}
    assert kept == {"samplepkg.Facade", "samplepkg.facades.Facade"}


def test_the_definition_is_pulled_in_when_the_shell_ranks_first() -> None:
    def entry(name: str, class_import: str, file: str, **extra: Any) -> dict[str, Any]:
        return {
            "name": name,
            "kind": "class",
            "file": file,
            "class_import": class_import,
            "bases": [],
            "methods": [{"name": "m", "params": [], "return_type": ""}],
            "properties": [],
            **extra,
        }

    shell = entry("Facade", "pkg.Facade", "pkg/__init__.py", reexport_of="pkg.facades.Facade")
    other = entry("Other", "pkg.other.Other", "pkg/other.py")
    other["methods"] = [{"name": "make", "params": [], "return_type": "Facade"}]
    definition = entry("Facade", "pkg.facades.Facade", "pkg/facades.py")
    artifact = {"source_repository": "a/b", "source_commit": "c" * 40, "types": [shell, other, definition]}
    reduced = run_extraction.reduce_fixture(artifact, max_types=1)
    assert [e["class_import"] for e in reduced["types"]] == ["pkg.facades.Facade", "pkg.Facade"]


def test_alias_groups_follow_a_chain_and_ignore_a_loop() -> None:
    def entry(class_import: str, reexport_of: str | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "name": "X",
            "kind": "class",
            "file": "pkg/x.py",
            "class_import": class_import,
            "methods": [{"name": "m", "params": [], "return_type": ""}],
            "properties": [],
        }
        if reexport_of:
            item["reexport_of"] = reexport_of
        return item

    chain = [entry("a.X", "b.X"), entry("b.X", "c.X"), entry("c.X")]
    assert run_extraction._alias_groups(chain) == [[2, 0, 1]]
    loop = [entry("a.X", "b.X"), entry("b.X", "a.X")]
    assert run_extraction._alias_groups(loop) == [[0], [1]]
    own = [entry("a.X", "a.X")]
    assert run_extraction._alias_groups(own) == [[0]]


# --------------------------------------------------------------------------- the ast oracle


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """*text* split at *separator* outside brackets and quotes."""
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            buffer.append(char)
            if char == "\\":
                index += 1
                buffer.append(text[index])
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            buffer.append(char)
        elif char in "([{":
            depth += 1
            buffer.append(char)
        elif char in ")]}":
            depth -= 1
            buffer.append(char)
        elif char == separator and depth == 0:
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _expected_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The parameter list the renderer must print for *node*, from ``ast.unparse(node.args)`` alone."""
    pieces = _split_top_level(ast.unparse(node.args))
    if pieces and pieces[0] in ("self", "cls"):
        pieces = pieces[1:]
    rendered: list[str] = []
    for piece in pieces:
        name, equals, default = (
            piece.partition("=") if len(_split_top_level(piece, "=")) == 2 else (piece, "", "")
        )
        if equals:
            default = default.strip()
            if len(default) > 120:
                default = default[:120] + "..."
            piece = f"{name.strip()} = {default}"
        rendered.append(piece)
    return ", ".join(rendered)


def _expected_suffix(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    names = {ast.unparse(decorator) for decorator in node.decorator_list}
    found = []
    if "staticmethod" in names:
        found.append("static")
    if "classmethod" in names:
        found.append("classmethod")
    if isinstance(node, ast.AsyncFunctionDef):
        found.append("async")
    return f" [{', '.join(found)}]" if found else ""


def _functions(source: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_methods(source: str, class_name: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name: item
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                and not any(
                    ast.unparse(d) == "property" or ast.unparse(d).endswith(".setter")
                    for d in item.decorator_list
                )
            }
    raise AssertionError(class_name)


def _oracle_cases() -> list[Any]:
    cases: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    account = _class_methods(MODELS_PY, "Account")
    for name in ("__init__", "open", "make", "sync", "traced", "close", "options", "plain"):
        cases.append(("samplepkg.models.Account", account[name]))
    base = _class_methods(FACADES_PY, "_Base")
    facade = _class_methods(FACADES_PY, "Facade")
    for fqn in ("samplepkg.facades.Facade", "samplepkg.Facade"):
        cases.append((fqn, facade["encrypt"]))
        cases.append((fqn, base["bind"]))
    util = _functions(UTIL_PY)
    for name in ("load", "fetch", "combine"):
        cases.append((f"samplepkg.util.{name}", util[name]))
    cases.append(("samplepkg.convert", _functions(IMPL_PY)["convert"]))
    cases.append(("samplepkg.load", util["load"]))
    return [pytest.param(fqn, node, id=f"{fqn}.{node.name}") for fqn, node in cases]


ORACLE_CASES = _oracle_cases()


@pytest.fixture(scope="module")
def chunk_texts(types: dict[str, dict[str, Any]]) -> dict[str, str]:
    chunks = build_chunks_from_api_surface(_artifact(types), title="sample", max_types=1000)
    texts: dict[str, str] = {}
    for chunk in chunks:
        match = re.search(r"^FQN: (.+)$", chunk.text, re.MULTILINE)
        assert match is not None
        texts[match.group(1)] = chunk.text
    return texts


def _rendered_line(text: str, name: str) -> tuple[str, str]:
    """``(parameter list, modifier suffix)`` of the ``Methods:`` line that defines *name*."""
    block = text.split("Methods:\n", 1)[1]
    for line in block.splitlines():
        match = re.fullmatch(rf"  - {re.escape(name)}\((.*)\)(?: -> [^\[]*?)?( \[[a-z, ]+\])?", line)
        if match is not None:
            return match.group(1), match.group(2) or ""
    raise AssertionError(f"no Methods line for {name} in:\n{text}")


def _mismatch(
    chunk_texts: dict[str, str], fqn: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> str | None:
    params, suffix = _rendered_line(chunk_texts[fqn], node.name)
    expected = (_expected_params(node), _expected_suffix(node))
    return (
        None
        if (params, suffix) == expected
        else f"{fqn}.{node.name}: rendered {(params, suffix)} expected {expected}"
    )


@pytest.mark.parametrize(("fqn", "node"), ORACLE_CASES)
def test_rendered_signatures_match_the_ast_oracle(
    chunk_texts: dict[str, str], fqn: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    assert _mismatch(chunk_texts, fqn, node) is None


def test_the_oracle_checks_something(chunk_texts: dict[str, str]) -> None:
    assert len(ORACLE_CASES) >= 12
    params: list[str] = []
    suffixes: list[str] = []
    mismatches: list[str] = []
    for case in ORACLE_CASES:
        fqn, node = case.values
        rendered_params, suffix = _rendered_line(chunk_texts[fqn], node.name)
        params.append(rendered_params)
        suffixes.append(suffix)
        problem = _mismatch(chunk_texts, fqn, node)
        if problem:
            mismatches.append(problem)
    assert mismatches == []
    assert any(" = " in text for text in params)
    assert any(re.search(r"(^|, )\*(,|$)", text) for text in params)
    assert any(re.search(r"(^|, )/(,|$)", text) for text in params)
    assert " [static]" in suffixes
    assert " [classmethod]" in suffixes
    assert " [async]" in suffixes


def test_the_paired_facade_shell_chunk_lists_its_definitions_own_methods(chunk_texts: dict[str, str]) -> None:
    shell = chunk_texts["samplepkg.Facade"]
    assert "Defined-In: samplepkg.facades.Facade" in shell
    assert "  - encrypt(user, owner, *, permissions = 0, keysize = 128)" in shell
    assert "  - bind(path = None, *, overwrite = False)" in shell
