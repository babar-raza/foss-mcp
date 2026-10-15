"""TC-520 (REQ-G3-005, REQ-G3-008): the extraction records per-file parse failures in the fixture.

``python_surface`` skips modules with a leading underscore before it parses them, so a package
whose files do not parse used to look like a small library. ``python_parse_health`` parses every
file of the package with ``ast`` and ``tree_sitter_parse_health`` reads the tree-sitter scout
report; ``extract_with_health`` returns the result next to the types. Offline: synthetic trees in
``tmp_path``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction

BROKEN = "def broken():\nreturn 1\n"  # IndentationError at line 2


def _package(root: Path, files: dict[str, str]) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    package = root / "src" / "pkg"
    package.mkdir(parents=True)
    for relative, text in files.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _manifest(platform: str = "python", family: str = "pkg") -> dict[str, Any]:
    return {"platform": platform, "family": family, "repository": "acme/pkg"}


def test_python_health_counts_broken_files(tmp_path: Path) -> None:
    _package(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": "class A:\n    pass\n",
            "e.py": BROKEN,
            "c.py": BROKEN,
            "d.py": BROKEN,
        },
    )
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["engine"], health["failed"], health["total"]) == ("ast", 3, 5)
    assert len(health["examples"]) == 3
    assert health["examples"] == sorted(health["examples"])
    for example in health["examples"]:
        assert re.fullmatch(r"src/pkg/[cde]\.py:2: .+", example), example


def test_python_health_counts_private_modules(tmp_path: Path) -> None:
    """python_surface never parses a leading-underscore module; the health check must."""
    _package(tmp_path, {"__init__.py": "", "_input/__init__.py": "", "_input/reader.py": BROKEN})
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["failed"], health["total"]) == (1, 3)
    assert health["examples"][0].startswith("src/pkg/_input/reader.py:2: ")


def test_python_health_excludes_tests_and_docs_dirs(tmp_path: Path) -> None:
    _package(
        tmp_path,
        {
            "__init__.py": "",
            "core.py": "x = 1\n",
            "tests/test_core.py": BROKEN,
            "docs/conf.py": BROKEN,
            "examples/demo.py": BROKEN,
        },
    )
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["failed"], health["total"]) == (0, 2)


def test_clean_package_has_no_failures(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "from .core import A\n", "core.py": "class A:\n    pass\n"})
    health = run_extraction.python_parse_health(tmp_path)
    assert health == {"engine": "ast", "failed": 0, "total": 2, "examples": []}


def test_python_health_keeps_at_most_five_examples(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "", **{f"m{index}.py": BROKEN for index in range(8)}})
    health = run_extraction.python_parse_health(tmp_path)
    assert (health["failed"], health["total"]) == (8, 9)
    assert len(health["examples"]) == 5
    assert health["examples"][0].startswith("src/pkg/m0.py:")


def test_python_health_without_a_package_dir_counts_nothing(tmp_path: Path) -> None:
    assert run_extraction.python_parse_health(tmp_path) == {
        "engine": "ast",
        "failed": 0,
        "total": 0,
        "examples": [],
    }


def test_extract_with_health_reports_python_failures(tmp_path: Path) -> None:
    _package(
        tmp_path,
        {
            "__init__.py": "from .core import Gadget\n",
            "core.py": "class Gadget:\n    pass\n",
            "_x.py": BROKEN,
        },
    )
    types, unresolved, health = run_extraction.extract_with_health(tmp_path, _manifest())
    assert any(entry["name"] == "Gadget" for entry in types)
    assert unresolved == []
    assert health is not None
    assert (health["engine"], health["failed"], health["total"]) == ("ast", 1, 3)


def test_extract_from_clone_keeps_its_pair_contract(tmp_path: Path) -> None:
    _package(tmp_path, {"__init__.py": "from .core import Gadget\n", "core.py": "class Gadget:\n    pass\n"})
    result = run_extraction.extract_from_clone(tmp_path, _manifest())
    assert isinstance(result, tuple) and len(result) == 2
    types, unresolved = result
    full_types, full_unresolved, _health = run_extraction.extract_with_health(tmp_path, _manifest())
    assert (types, unresolved) == (full_types, full_unresolved)


def test_tree_sitter_health_from_a_broken_js_tree(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "jspkg", "main": "index.js"}\n', encoding="utf-8")
    (tmp_path / "index.js").write_text(
        "class Ok {\n  run() { return 1; }\n}\nmodule.exports = { Ok };\n", encoding="utf-8"
    )
    (tmp_path / "broken.js").write_text("function ( {{{ class = ;\n", encoding="utf-8")
    _types, unresolved, health = run_extraction.extract_with_health(tmp_path, _manifest("nodejs", "jspkg"))
    assert unresolved == []
    assert health is not None
    assert health["engine"] == "tree-sitter"
    assert health["failed"] >= 1
    assert health["total"] >= 2
    assert any("broken.js" in example for example in health["examples"])


def test_tree_sitter_health_from_a_scout_report() -> None:
    report = {"files_attempted": 8, "parse_errors": ["z.js", "a.js", "m.js"], "parse_error_rate": 0.375}
    health = run_extraction.tree_sitter_parse_health(report)
    assert (health["engine"], health["failed"], health["total"]) == ("tree-sitter", 3, 8)
    assert [example.split(":")[0] for example in health["examples"]] == ["a.js", "m.js", "z.js"]
    assert run_extraction.tree_sitter_parse_health({})["total"] == 0


def test_parse_failures_survive_reduce_fixture() -> None:
    health = {"engine": "ast", "failed": 35, "total": 45, "examples": ["src/p/a.py:2: bad"]}
    artifact = {
        "source_repository": "acme/pkg",
        "source_commit": "c" * 40,
        "language": "python",
        "type_count": 1,
        "types": [{"name": "A", "kind": "class", "file": "src/p/a.py", "class_import": "p.A"}],
        "extractor_version": 2,
        "symbol_counts": {"types": 1, "functions": 0},
        "parse_failures": health,
    }
    reduced = run_extraction.reduce_fixture(artifact)
    assert reduced["parse_failures"] == health
    assert reduced["symbol_counts"] == {"types": 1, "functions": 0}
    assert reduced["extractor_version"] == 2
