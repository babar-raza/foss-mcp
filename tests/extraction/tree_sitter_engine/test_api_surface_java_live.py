"""Opt-in real-repository regression for Java records (TC-168).

Needs network access and an explicit opt-in (``FOSS_MCP_NETWORK_TESTS=1``). It is not part of
the offline gate; the offline synthetic tests live in test_api_surface_java.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

LIVE_REPO = "https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Java.git"
LIVE_COMMIT = "620a2614418854b4a18966a361e6907ddc88c7cb"


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
