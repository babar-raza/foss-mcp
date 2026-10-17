"""TC-555: the declarations of a Python file whose indentation is collapsed are recovered, never dropped.

``aspose-tex-foss/Aspose.TeX-FOSS-for-Python`` ships two modules (``_input/reader.py`` and
``presentation/__init__.py``) that lost their indentation: every indented line has exactly one
leading space, a ``def`` is followed by its docstring at the same column, an ``if`` by its body at
the same column. ``ast.parse`` refuses them, and the extractor used to drop the whole module (and
with it ``TeXJob``, ``TeXOptions`` and the devices). The declarations themselves are intact.

``_parse_repairing_collapsed_indentation`` closes every compound-statement header whose body is
missing with `` pass`` and parses again. Offline: every source here is written inline, and the two
real shapes are pinned as excerpts of the upstream text. The repair is disclosed, never silent: a
``repaired-syntax`` entry in ``unresolved`` for each repaired file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.extraction.tree_sitter_engine.python_surface import (
    _parse_repairing_collapsed_indentation,
    inspect_public_surface,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _declared(tree: ast.Module) -> list[tuple[str, int]]:
    found = [
        (node.name, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    return sorted(found, key=lambda item: item[1])


def _repaired(entries: tuple[str, ...]) -> list[str]:
    return [entry for entry in entries if ":repaired-syntax:" in entry]


# --- the helper ------------------------------------------------------------------------------


def test_a_class_with_a_method_and_a_docstring_at_the_defs_column_is_recovered() -> None:
    source = (
        "class Reader:\n"
        ' """A reader."""\n'
        "\n"
        " def read(self, size: int) -> bytes:\n"
        ' """Read *size* bytes."""\n'
        " return b''\n"
    )
    tree, repairs = _parse_repairing_collapsed_indentation(source, "reader.py")
    assert repairs == 1
    assert _declared(tree) == [("Reader", 1), ("read", 4)]


def test_a_function_with_if_else_bodies_is_recovered() -> None:
    source = (
        "def pick(flag: bool) -> int:\nif flag:\nreturn 1\nelif flag is None:\nreturn 2\nelse:\nreturn 3\n"
    )
    tree, repairs = _parse_repairing_collapsed_indentation(source, "pick.py")
    assert repairs >= 1
    assert _declared(tree) == [("pick", 1)]


def test_a_try_except_finally_ladder_is_recovered() -> None:
    source = (
        "class Box:\n"
        " def open(self) -> None:\n"
        " try:\n"
        " self.fd = 1\n"
        " except OSError as error:\n"
        " raise error\n"
        " except (ValueError, KeyError):\n"
        " pass\n"
        " else:\n"
        " self.ok = True\n"
        " finally:\n"
        " self.done = True\n"
        "\n"
        " def close(self) -> None:\n"
        " pass\n"
    )
    tree, repairs = _parse_repairing_collapsed_indentation(source, "box.py")
    assert repairs >= 2
    assert _declared(tree) == [("Box", 1), ("open", 2), ("close", 14)]


def test_for_while_and_with_headers_are_closed() -> None:
    source = (
        "def run(items):\n"
        "for item in items:\n"
        "print(item)\n"
        "while items:\n"
        "items.pop()\n"
        "with open('x') as handle:\n"
        "handle.read()\n"
        "\n"
        "def after():\n"
        "pass\n"
    )
    tree, repairs = _parse_repairing_collapsed_indentation(source, "run.py")
    assert repairs >= 3
    assert _declared(tree) == [("run", 1), ("after", 9)]


def test_a_decorator_stack_is_kept() -> None:
    source = (
        "class Job:\n"
        " @property\n"
        " @abc.abstractmethod\n"
        " def name(self) -> str:\n"
        ' """The name."""\n'
        " return ''\n"
    )
    tree, _ = _parse_repairing_collapsed_indentation(source, "job.py")
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    assert [ast.unparse(decorator) for decorator in method.decorator_list] == [
        "property",
        "abc.abstractmethod",
    ]
    assert method.returns is not None and ast.unparse(method.returns) == "str"


def test_a_multi_line_signature_is_closed_on_its_last_line() -> None:
    source = (
        "def f(\n"
        " a,\n"
        " b,\n"
        ") -> int:\n"
        '"""Add."""\n'
        "return a + b\n"
        "\n"
        "def g(x: int,\n"
        " y: str = 'z') -> int:\n"
        "return x\n"
    )
    tree, repairs = _parse_repairing_collapsed_indentation(source, "sig.py")
    assert repairs == 2
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert [arg.arg for arg in functions["f"].args.args] == ["a", "b"]
    assert [arg.arg for arg in functions["g"].args.args] == ["x", "y"]
    assert functions["g"].lineno == 8


def test_annotated_class_fields_are_kept() -> None:
    source = (
        "@dataclass\n"
        "class Options:\n"
        ' """Options."""\n'
        "\n"
        " load_format: bool = True\n"
        " name: str = 'tex'\n"
        "\n"
        " def validate(self) -> None:\n"
        ' """Check."""\n'
        " if self.load_format:\n"
        " return\n"
    )
    tree, _ = _parse_repairing_collapsed_indentation(source, "options.py")
    options = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    fields = [
        item.target.id
        for item in options.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    ]
    assert fields == ["load_format", "name"]
    assert [ast.unparse(decorator) for decorator in options.decorator_list] == ["dataclass"]


def test_a_file_that_parses_as_written_is_returned_untouched_with_zero_repairs() -> None:
    source = "class A:\n    def m(self):\n        try:\n            pass\n        except OSError:\n            pass\n"
    tree, repairs = _parse_repairing_collapsed_indentation(source, "ok.py")
    assert repairs == 0
    assert ast.dump(tree) == ast.dump(ast.parse(source, filename="ok.py"))


def test_a_real_unrelated_syntax_error_still_raises_the_original_error() -> None:
    with pytest.raises(SyntaxError) as raised:
        _parse_repairing_collapsed_indentation("def f(:\n pass\n", "bad.py")
    assert raised.value.lineno == 1
    assert "expected an indented block" not in (raised.value.msg or "")


def test_a_header_that_does_not_end_in_a_colon_raises_the_original_error() -> None:
    with pytest.raises(SyntaxError, match="expected an indented block") as raised:
        _parse_repairing_collapsed_indentation("def f():  # the header carries a comment\nreturn 1\n", "c.py")
    assert raised.value.lineno == 2


def test_an_inconsistent_dedent_is_not_a_collapsed_indentation() -> None:
    with pytest.raises(SyntaxError, match="unindent"):
        _parse_repairing_collapsed_indentation("if x:\n    y = 1\n   z = 2\n", "dedent.py")


def test_the_repair_count_is_reported_and_each_header_is_one_repair() -> None:
    source = "class A:\n def a(self):\n pass\n def b(self):\n pass\n def c(self):\n pass\n"
    lines: list[int] = []
    tree, repairs = _parse_repairing_collapsed_indentation(source, "count.py", repaired_lines=lines)
    assert repairs == 3
    assert lines == [2, 4, 6]
    assert [name for name, _ in _declared(tree)] == ["A", "a", "b", "c"]


def test_line_numbers_of_declarations_are_the_real_lines() -> None:
    source = (
        "# header\n"  # 1
        "\n"  # 2
        "class A:\n"  # 3
        ' """Doc."""\n'  # 4
        "\n"  # 5
        " def one(self):\n"  # 6
        ' """One."""\n'  # 7
        "\n"  # 8
        " # a comment between the blocks\n"  # 9
        " def two(self):\n"  # 10
        " if self:\n"  # 11
        " return 1\n"  # 12
        " else:\n"  # 13
        " return 2\n"  # 14
        "\n"  # 15
        "def three():\n"  # 16
        "pass\n"  # 17
    )
    tree, _ = _parse_repairing_collapsed_indentation(source, "lines.py")
    assert _declared(tree) == [("A", 3), ("one", 6), ("two", 10), ("three", 16)]


def test_a_comment_between_a_header_and_its_missing_body_does_not_hide_the_header() -> None:
    source = "def f():\n# explain\nreturn 1\n"
    tree, repairs = _parse_repairing_collapsed_indentation(source, "comment.py")
    assert repairs == 1
    assert _declared(tree) == [("f", 1)]


def test_the_repair_is_bounded() -> None:
    assert python_surface._MAX_REPAIRS == 2000
    # a 2100-method class needs 2100 repairs: the original error is raised, not an endless loop
    source = "class Big:\n" + "".join(
        f" def m{i}(self):\n pass\n" for i in range(python_surface._MAX_REPAIRS + 100)
    )
    with pytest.raises(SyntaxError, match="expected an indented block"):
        _parse_repairing_collapsed_indentation(source, "big.py")


def test_the_try_replacement_is_a_neutral_statement() -> None:
    assert python_surface._REPAIR_TRY == "if True: pass"
    assert python_surface._REPAIR_INERT == "if False: pass"


# --- the two real shapes of the upstream files --------------------------------------------------

# reader.py `_expand_caret_caret` (excerpt, indentation as published: one space per level).
_READER_EXCERPT = (
    "def _expand_caret_caret(line: str) -> list[tuple[str, int]]:\n"
    ' """Decode all ``^^`` escape sequences in *line*.\n'
    "\n"
    " Returns a list of ``(char, 1-based-column)`` pairs.\n"
    ' """\n'
    " result: list[tuple[str, int]] = []\n"
    " i = 0\n"
    " n = len(line)\n"
    " while i < n:\n"
    " col = i + 1 # 1-based\n"
    " if (\n"
    ' line[i] == "^"\n'
    " and i + 1 < n\n"
    " ):\n"
    " x = line[i + 2]\n"
    " try:\n"
    " code = int(x, 16)\n"
    " except ValueError:\n"
    " code = 0\n"
    " if code < 64:\n"
    " result.append((chr(code + 64), col))\n"
    " else:\n"
    " result.append((chr(code - 64), col))\n"
    " i += 3\n"
    " else:\n"
    " result.append((line[i], col))\n"
    " i += 1\n"
    " return result\n"
    "\n"
    "\n"
    "class InputReader:\n"
    ' """Manages a stack of sources."""\n'
    "\n"
    " def __init__(self, source) -> None:\n"
    ' """Create a reader."""\n'
    " self._stack = [source]\n"
)

# presentation/__init__.py `OutputDevice.__init__` (excerpt): the docstring sits at the def's column.
_PRESENTATION_EXCERPT = (
    "import abc\n"
    "import io\n"
    "from pathlib import Path\n"
    "\n"
    "\n"
    "class OutputDevice(abc.ABC):\n"
    ' """Abstract base class for all output devices.\n'
    "\n"
    " Example::\n"
    "\n"
    ' device = DviDevice(Path("output.dvi"))\n'
    ' """\n'
    "\n"
    " def __init__(self, destination: Path | io.BytesIO | None = None) -> None:\n"
    ' """\n'
    " Args:\n"
    " destination: ``Path`` for file output.\n"
    ' """\n'
    " if destination is None:\n"
    " destination = io.BytesIO()\n"
    " self._destination = destination\n"
    "\n"
    " @property\n"
    " def destination(self) -> Path | io.BytesIO:\n"
    ' """The output destination (read-only after construction)."""\n'
    " return self._destination\n"
    "\n"
    "\n"
    "def create_input_source(text: str) -> None:\n"
    ' """Make a source."""\n'
    " return None\n"
)


def test_the_reader_excerpt_recovers_the_caret_ladder_and_both_declarations() -> None:
    tree, repairs = _parse_repairing_collapsed_indentation(_READER_EXCERPT, "reader.py")
    assert repairs > 5
    assert [
        (node.name, node.lineno) for node in tree.body if isinstance(node, ast.ClassDef | ast.FunctionDef)
    ] == [
        ("_expand_caret_caret", 1),
        ("InputReader", _READER_EXCERPT.splitlines().index("class InputReader:") + 1),
    ]
    reader = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert [item.name for item in reader.body if isinstance(item, ast.FunctionDef)] == ["__init__"]


def test_the_presentation_excerpt_recovers_the_device_and_the_factory() -> None:
    tree, repairs = _parse_repairing_collapsed_indentation(_PRESENTATION_EXCERPT, "presentation.py")
    assert repairs >= 3
    device = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert device.name == "OutputDevice"
    assert [ast.unparse(base) for base in device.bases] == ["abc.ABC"]
    assert [item.name for item in device.body if isinstance(item, ast.FunctionDef)] == [
        "__init__",
        "destination",
    ]
    assert any(isinstance(node, ast.FunctionDef) and node.name == "create_input_source" for node in tree.body)


# --- the extractor ---------------------------------------------------------------------------------


def test_a_collapsed_module_is_scanned_and_disclosed_with_one_entry(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from .widget import Widget\n")
    _write(
        tmp_path,
        "src/pkg/widget.py",
        "class Widget:\n"
        ' """A widget."""\n'
        "\n"
        " def render(self, scale: float = 1.0) -> str:\n"
        ' """Render it."""\n'
        " return ''\n",
    )
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    names = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert names["pkg.widget.Widget"].kind == "class"
    assert names["pkg.widget.Widget"].line == 1
    assert names["pkg.widget.Widget.render"].kind == "method"
    assert names["pkg.widget.Widget.render"].line == 4
    assert names["pkg.widget.Widget.render"].signature == "def render(self, scale: float=1.0) -> str"
    assert _repaired(surface.unresolved) == [
        "pkg.widget:4:repaired-syntax:src/pkg/widget.py:1 header lines closed with pass"
    ]
    assert not any(":syntax-error:" in entry for entry in surface.unresolved)


def test_a_collapsed_init_re_exporting_a_collapsed_private_module_resolves_the_reexport(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/pkg/__init__.py",
        "from ._impl import Job\n"
        "from .api import Options\n"
        "\n"
        "def create(text: str) -> Job:\n"
        '"""Make a job."""\n'
        "return Job(text)\n",
    )
    _write(
        tmp_path,
        "src/pkg/_impl.py",
        "class Job:\n"
        ' """A job."""\n'
        "\n"
        " def run(self) -> None:\n"
        ' """Run."""\n'
        " try:\n"
        " self.go()\n"
        " except OSError:\n"
        " pass\n",
    )
    _write(
        tmp_path,
        "src/pkg/api.py",
        "class Options:\n ' '\n\n def validate(self) -> None:\n pass\n",
    )
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    names = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert names["pkg.Job"].kind == "class"
    assert names["pkg.Job.run"].kind == "method"
    assert names["pkg.create"].kind == "function"
    assert names["pkg.Options"].kind == "class"
    assert not any(":unresolved-reexport:" in entry for entry in surface.unresolved)
    assert not any(":syntax-error:" in entry for entry in surface.unresolved)
    repaired = _repaired(surface.unresolved)
    assert len(repaired) == 3
    assert {entry.split(":repaired-syntax:")[1].split(":")[0] for entry in repaired} == {
        "src/pkg/__init__.py",
        "src/pkg/_impl.py",
        "src/pkg/api.py",
    }
    assert "pkg._impl:4:repaired-syntax:src/pkg/_impl.py:2 header lines closed with pass" in repaired


def test_one_repaired_entry_per_file_however_many_times_it_is_read(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._impl import A, B, C\n")
    _write(
        tmp_path,
        "src/pkg/_impl.py",
        "class A:\n def a(self):\n pass\n\nclass B(A):\n pass\n\nclass C(B):\n pass\n",
    )
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert len(_repaired(surface.unresolved)) == 1


def test_a_file_that_parses_is_never_repaired_nor_disclosed(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from .ok import Fine\n")
    _write(tmp_path, "src/pkg/ok.py", "class Fine:\n    def m(self):\n        pass\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert surface.unresolved == ()


def test_a_collapsed_file_with_a_real_syntax_error_keeps_the_syntax_error_entry(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from .bad import Bad\n")
    _write(tmp_path, "src/pkg/bad.py", "class Bad:\n def m(self:\n pass\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert not any(symbol.name == "Bad" and symbol.kind == "class" for symbol in surface.symbols)
    assert any(
        entry.startswith("pkg.bad:") and ":syntax-error:src/pkg/bad.py:" in entry
        for entry in surface.unresolved
    )
    assert _repaired(surface.unresolved) == []


def test_imports_of_other_libraries_in_a_package_init_are_not_unresolved_reexports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/pkg/__init__.py",
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "\n"
        "@dataclass\n"
        "class Options:\n"
        "    root: Path\n",
    )
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert surface.unresolved == ()
    assert next(symbol for symbol in surface.symbols if symbol.name == "Options").kind == "class"


def test_a_reexport_of_a_missing_origin_in_the_tree_is_still_unresolved(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from pkg.gone import Ghost\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert surface.unresolved == ("pkg:1:unresolved-reexport:pkg.gone.Ghost",)


def test_the_repair_is_wired_into_the_extractor_not_only_defined() -> None:
    source = Path(python_surface.__file__).read_text(encoding="utf-8")
    assert "def _parse_repairing_collapsed_indentation(" in source
    assert source.count("_parse_source_file(") >= 5  # the definition and four call sites
    assert source.count("ast.parse(") == 1  # only the helper parses; every caller goes through it
    assert "tree = _parse_source_file(path, repairs)" in source
