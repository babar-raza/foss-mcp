"""TC-555 (REQ-G3-005, REQ-G3-008): enum members and the parse health read a collapsed file through the repair.

``python_surface`` recovers the declarations of a file whose indentation was collapsed upstream (every
indented line one space, a ``def`` followed by its docstring at the same column). Two readers in
``run_extraction`` parsed such a file with plain ``ast.parse`` and so disagreed with the surface:
``fill_python_enum_members`` left ``OutputFormat`` of tex/python without members, and
``python_parse_health`` counted every repairable file as a parse failure (a number served to peers in the
API-Coverage line). Both now go through the same repair; a file is a failure only if it still does not parse
after it. Offline: synthetic trees in ``tmp_path`` and the committed tex/python fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tex_python" / "api_surface.json"
MANIFEST = {"platform": "python", "family": "pkg", "repository": "acme/pkg"}

# Collapsed: one space per level, the docstring at the def's column.
COLLAPSED = 'def collapsed():\n"""Doc."""\nreturn 1\n'
# A real syntax error: not an indentation collapse, so no repair can read it.
BROKEN = "def broken(:\n    return 1\n"
COLLAPSED_ENUM = (
    "import enum\n"
    "\n"
    "\n"
    "class Color(enum.Enum):\n"
    ' """Colors."""\n'
    "\n"
    " RED = 'r'\n"
    " BLUE = 'b'\n"
    "\n"
    " def label(self) -> str:\n"
    ' """Label."""\n'
    " return self.value\n"
)


def _package(root: Path, files: dict[str, str]) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    package = root / "src" / "pkg"
    package.mkdir(parents=True)
    for relative, text in files.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _members(entry: dict[str, Any]) -> list[tuple[str, str]]:
    return [(member["name"], member["value"]) for member in entry["enum_members"]]


def test_the_members_of_an_enum_in_a_collapsed_file_are_filled(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "from .colors import Color\n", "colors.py": COLLAPSED_ENUM})
    types, _ = run_extraction.extract_from_clone(tmp_path, MANIFEST)
    by_import = {entry["class_import"]: entry for entry in types}
    assert by_import["pkg.colors.Color"]["kind"] == "enum"
    # TC-532: [] -> the members; python_surface reads them with the class (through the same repair)
    assert _members(by_import["pkg.colors.Color"]) == [("RED", "'r'"), ("BLUE", "'b'")]
    run_extraction.fill_python_enum_members(types, tmp_path)
    assert _members(by_import["pkg.colors.Color"]) == [("RED", "'r'"), ("BLUE", "'b'")]


def test_the_members_of_an_enum_in_a_file_that_parses_are_unchanged(tmp_path: Path) -> None:
    _package(
        tmp_path,
        {
            "__init__.py": "",
            "colors.py": "import enum\n\n\nclass Color(enum.Enum):\n    RED = 1\n    BLUE = 2\n",
        },
    )
    types, _ = run_extraction.extract_from_clone(tmp_path, MANIFEST)
    run_extraction.fill_python_enum_members(types, tmp_path)
    (enum,) = [entry for entry in types if entry["kind"] == "enum"]
    assert _members(enum) == [("RED", "1"), ("BLUE", "2")]


def test_an_enum_in_a_file_that_cannot_be_repaired_stays_empty_without_raising(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "", "colors.py": BROKEN})
    types = [{"kind": "enum", "name": "Color", "file": "src/pkg/colors.py", "line": 1, "enum_members": []}]
    run_extraction.fill_python_enum_members(types, tmp_path)
    assert types[0]["enum_members"] == []


def test_a_collapsed_file_counts_no_failure_in_the_health(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "", "a.py": COLLAPSED, "b.py": COLLAPSED_ENUM})
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["engine"], health["failed"], health["total"]) == ("ast", 0, 3)
    assert health["examples"] == []


def test_a_real_syntax_error_counts_one_failure_in_the_health(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "", "a.py": BROKEN})
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["failed"], health["total"]) == (1, 2)
    assert re.fullmatch(r"src/pkg/a\.py:1: .+", health["examples"][0]), health["examples"]


def test_the_health_of_a_mixed_tree_counts_only_the_files_that_still_fail(tmp_path: Path) -> None:
    _package(
        tmp_path,
        {
            "__init__.py": "",
            "ok.py": "class A:\n    pass\n",
            "collapsed.py": COLLAPSED,
            "_private/__init__.py": "",
            "_private/inner.py": COLLAPSED_ENUM,
            "broken_one.py": BROKEN,
            "_private/broken_two.py": BROKEN,
        },
    )
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["failed"], health["total"]) == (2, 7)
    assert health["examples"] == sorted(health["examples"])
    assert [example.split(":")[0] for example in health["examples"]] == [
        "src/pkg/_private/broken_two.py",
        "src/pkg/broken_one.py",
    ]


def test_a_header_with_a_trailing_comment_is_not_repaired_and_still_counts(tmp_path: Path) -> None:
    # The repair closes only a header that ends in a colon (the card's rule); this is measured, not hidden.
    _package(tmp_path, {"__init__.py": "", "a.py": "def f():  # comment\nreturn 1\n"})
    assert run_extraction.python_parse_health(tmp_path)["failed"] == 1


def test_extract_with_health_reports_a_repaired_file_as_read(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "from .colors import Color\n", "colors.py": COLLAPSED_ENUM})
    types, unresolved, health = run_extraction.extract_with_health(tmp_path, MANIFEST)
    assert health is not None and (health["failed"], health["total"]) == (0, 2)
    assert any(entry["name"] == "Color" for entry in types)
    assert [entry for entry in unresolved if ":repaired-syntax:" in entry] == [
        "pkg.colors:10:repaired-syntax:src/pkg/colors.py:1 header lines closed with pass"
    ]


def test_the_two_readers_go_through_the_one_repair() -> None:
    source = Path(run_extraction.__file__).read_text(encoding="utf-8")
    assert source.count("python_surface._parse_source_file(path)") == 2
    assert "ast.parse(" not in source


def test_the_committed_output_format_has_its_real_members() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    enums = {entry["class_import"]: entry for entry in fixture["types"] if entry["kind"] == "enum"}
    definition = enums["aspose_tex.presentation.OutputFormat"]
    # Read from `class OutputFormat(Enum)` in src/aspose_tex/presentation/__init__.py at 181015d2ee.
    assert _members(definition) == [("DVI", "'dvi'"), ("PDF", "'pdf'"), ("SVG", "'svg'")]
    assert len(definition["enum_members"]) >= 2
    # the re-export stub in aspose_tex/__init__.py has no class body of its own and stays as it was
    assert enums["aspose_tex.OutputFormat"]["enum_members"] == []


def test_the_committed_tex_health_counts_only_the_unrepairable_files() -> None:
    health = json.loads(FIXTURE.read_text(encoding="utf-8"))["parse_failures"]
    assert (health["engine"], health["total"]) == ("ast", 45)
    # TC-555: 35 of 45 (every collapsed file) -> 11 of 45 (the files the bounded repair cannot read).
    assert health["failed"] == 11
    assert len(health["examples"]) == 5
