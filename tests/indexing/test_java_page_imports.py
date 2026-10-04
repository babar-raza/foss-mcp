"""Offline checks that the pdf/java verifier honours the furnished page's own import lines (REQ-G2-048, TC-194).

A candidate's code is a bare statement block, so the wrapper it is compiled in must carry the
imports the page itself uses. Without them, classes from subpackages such as
``org.aspose.pdf.annotations`` do not resolve, and the candidate fails for a wrapper reason, not a
content reason. These tests never run javac or Maven: the one subprocess call is replaced by a
recorder, so they need no network, JDK or toolchain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import foss_mcp.indexing.example_verifier as example_verifier
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import _java_wrapper_source, verify_java_example
from infra.build_chunks import _page_java_imports

WIDGET_IMPORT = "import org.aspose.pdf.annotations.WidgetAnnotation;"

_SAMPLE_PAGE = """\
---
title: Sample
---

Overview: import statements are described in the next section, and nothing here is code.

```java
import org.aspose.pdf.Document;
import org.aspose.pdf.annotations.WidgetAnnotation;
```

Content continues here with more prose about the widget.
"""


def test_java_wrapper_source_puts_a_page_import_before_the_candidate_class() -> None:
    source = _java_wrapper_source("WidgetAnnotation widget = null;", "Candidate", (WIDGET_IMPORT,))

    assert source.startswith("import org.aspose.pdf.*;\n" + WIDGET_IMPORT + "\n")
    assert source.index(WIDGET_IMPORT) < source.index("public class Candidate")
    assert "public static void main(String[] args) throws Exception {" in source
    assert "        WidgetAnnotation widget = null;" in source


def test_page_java_imports_returns_the_import_lines_and_not_the_prose(tmp_path: Path) -> None:
    page = tmp_path / "_index.md"
    page.write_text(_SAMPLE_PAGE, encoding="utf-8")

    imports = _page_java_imports(page)

    assert imports == (
        "import org.aspose.pdf.Document;",
        WIDGET_IMPORT,
    )
    assert all(not line.startswith("Overview") for line in imports)
    assert _page_java_imports(None) == ()


def test_verify_java_example_writes_a_candidate_java_that_contains_the_page_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(example_verifier, "_run", _fake_run)
    candidate = CandidateExample(
        title="Widget",
        description="",
        language="java",
        code="WidgetAnnotation widget = null;",
    )

    result = verify_java_example(
        candidate,
        library_jar=tmp_path / "reference.jar",
        workdir=tmp_path,
        page_imports=(WIDGET_IMPORT,),
    )

    source_path = tmp_path / "candidate_project" / "Candidate.java"
    assert source_path.exists()
    assert WIDGET_IMPORT in source_path.read_text(encoding="utf-8")
    assert result.verified is True
    assert calls and calls[0][-1] == "Candidate.java"
