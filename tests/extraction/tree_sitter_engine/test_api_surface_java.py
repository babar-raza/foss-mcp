"""``api_surface.extract_api_surface`` reads Java records as their own types (TC-168).

``_CLASS_TYPES["java"]`` previously omitted ``record_declaration``, so a Java 16+ record was
invisible as a type and its constructor/methods leaked out as top-level ``function`` entries.
The synthetic tests below pin the fixed behaviour and confirm ordinary Java types are unchanged.
The real-repository regression is in ``test_java_record_live_regression`` and needs network access
and an explicit opt-in (``FOSS_MCP_NETWORK_TESTS=1``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

RECORD = """
package p;

public record Point(int x, int y) {
    public int sum() { return x + y; }
}
"""

ORDINARY = """
package p;

public class Box {
    public int size() { return 1; }
}

public interface Shape {
    double area();
}

public enum Color { RED, GREEN }
"""

LIVE_REPO = "https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Java.git"
LIVE_COMMIT = "620a2614418854b4a18966a361e6907ddc88c7cb"


def _package(root: Path, name: str, source: str) -> Path:
    package = root / "src" / "p"
    package.mkdir(parents=True)
    (package / name).write_text(source, encoding="utf-8")
    return package


def _extract(package: Path, root: Path, family: str = "widget") -> list[dict]:
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, root, family)
    return types


def test_a_java_record_is_its_own_top_level_type(tmp_path: Path) -> None:
    package = _package(tmp_path, "Point.java", RECORD)
    types = _extract(package, tmp_path)
    point = next(t for t in types if t["name"] == "Point")
    assert point["kind"] == "record_declaration"


def test_a_record_method_is_scoped_to_the_record_not_a_stray_function(tmp_path: Path) -> None:
    package = _package(tmp_path, "Point.java", RECORD)
    types = _extract(package, tmp_path)
    point = next(t for t in types if t["name"] == "Point")
    assert [m["name"] for m in point["methods"]] == ["sum"]
    stray = [t for t in types if t.get("kind") == "function" and t["name"] == "sum"]
    assert stray == []


def test_ordinary_java_class_interface_and_enum_are_unaffected(tmp_path: Path) -> None:
    package = _package(tmp_path, "Ordinary.java", ORDINARY)
    types = _extract(package, tmp_path)
    by_name = {t["name"]: t for t in types}
    assert by_name["Box"]["kind"] == "class_declaration"
    assert [m["name"] for m in by_name["Box"]["methods"]] == ["size"]
    assert by_name["Shape"]["kind"] == "interface_declaration"
    assert by_name["Color"]["kind"] == "enum_declaration"
    assert not [t for t in types if t.get("kind") == "function"]


@pytest.mark.skipif(
    os.environ.get("FOSS_MCP_NETWORK_TESTS") != "1" or shutil.which("git") is None,
    reason="real-repository regression needs network; set FOSS_MCP_NETWORK_TESTS=1",
)
def test_java_record_live_regression(tmp_path: Path) -> None:
    clone = tmp_path / "slides-java"
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", LIVE_REPO, str(clone)],
        check=True,
    )
    subprocess.run(["git", "-C", str(clone), "checkout", LIVE_COMMIT], check=True)
    package = clone / "src" / "main" / "java"
    # Relationship lives in org.aspose.slides.foss.internal.opc, which the production default
    # excludes ("internal" segment). Disable that filter here so the record-scoping defect is
    # observable on Relationship as well as FontData.
    types, *_ = api_surface.extract_api_surface(
        get_parser("java"), "java", package, clone, "slides", excluded_package_segments=frozenset()
    )
    names = {t["name"]: t for t in types}
    assert names["Relationship"]["kind"] == "record_declaration"
    assert names["FontData"]["kind"] == "record_declaration"
    assert [m["name"] for m in names["FontData"]["methods"]] == ["getFontName", "getFontName"]
    stray = {t["name"] for t in types if t.get("kind") == "function"}
    assert "getFontName" not in stray
    assert "Relationship" not in stray
