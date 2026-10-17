"""Offline tests for TC-458 (REQ-G3-007): build_chunks publishes the program that was compiled.

``_build_verified_example_chunks`` reads the widened candidate supply, hands each verifier the
``effective_candidate`` (a continuation gets the import lines of its chain where no verifier borrows
page imports), publishes ``_published_program`` - what the toolchain actually compiled - with a
``Harness:`` line naming what was added, never verifies an import-only block on cpp, java or nodejs,
and keys the verification cache (TC-441) on the published program.

Nothing here touches git, a toolchain or a network: every prepare function is stubbed and every verify
function is replaced inside ``_PLATFORM_DISPATCH`` itself (the table captures the function object at
import time), as tests/indexing/test_build_chunks_verification_report.py does. ``toolchain_id`` is
always monkeypatched. The pages are synthetic except where a test names a real committed pilot page.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_candidates
from foss_mcp.indexing.example_candidates import CandidateExample, extract_candidate_examples
from foss_mcp.indexing.example_fixtures import FixtureRecord
from foss_mcp.indexing.example_verifier import (
    VerificationResult,
    _assemble_dotnet_source,
    _assemble_rust_source,
    _wrap_cpp_fragment_if_needed,
)
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests/fixtures/furnished"
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"

REPOSITORY = "example-org/reference-library"
COMMIT = "3" * 40
TOOLCHAIN = "toolchain 1.0"

PREPARE_NAME = {
    "dotnet": "prepare_reference_library",
    "python": "prepare_python_library",
    "rust": "prepare_rust_library",
    "go": "prepare_go_library",
    "java": "prepare_java_library",
    "typescript": "prepare_typescript_library",
    "nodejs": "prepare_nodejs_library",
    "cpp": "prepare_cpp_library_linux",
}
PLATFORM_FLAGS = {
    "dotnet": ["--library-csproj", "src/Library.csproj"],
    "rust": ["--library-crate-name", "example_crate"],
    "go": ["--library-module-path", "example.org/module"],
    "typescript": ["--library-package-name", "example-package"],
    "nodejs": ["--library-package-name", "example-package"],
}
FENCE_TAG = {
    "dotnet": "csharp",
    "python": "python",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "typescript": "typescript",
    "nodejs": "javascript",
    "cpp": "cpp",
}

INPUT_FILES_PART = (
    "input files named in the code are placed in the working directory by the verifier when it can "
    "(kept if already there, otherwise copied by name from the pinned repository or generated as a "
    "minimal file): "
)


# --- helpers ---------------------------------------------------------------------------------------------


def _write_page(path: Path, platform: str, blocks: list[tuple[str, str]]) -> Path:
    """A furnished-style page with one example block per ``(title, code)``."""
    tag = FENCE_TAG[platform]
    front_matter = {
        "title": "Chain fixture",
        "single": {
            "block": [
                {"title": title, "content": f"Prose for {title}.\n\n```{tag}\n{code}\n```\n"}
                for title, code in blocks
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    return path


class Harness:
    """A stubbed prepare and verify for one platform; records every call and decides each verdict."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        *,
        verdict: Callable[[CandidateExample], bool] = lambda candidate: True,
        status: str = "",
        fixtures: tuple[FixtureRecord, ...] = (),
    ) -> None:
        self.platform = platform
        self.calls: list[CandidateExample] = []
        self.prepare_calls = 0

        def fake_prepare(*args: object, **kwargs: object) -> Path:
            self.prepare_calls += 1
            return Path("prepared") / "library"

        def fake_verify(candidate: CandidateExample, **kwargs: object) -> VerificationResult:
            self.calls.append(candidate)
            return VerificationResult(
                candidate=candidate,
                verified=verdict(candidate),
                output=f"real output for {candidate.title}",
                status=status,
                fixtures=fixtures,
            )

        monkeypatch.setattr(build_chunks, PREPARE_NAME[platform], fake_prepare)
        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH[platform], "verify", fake_verify)
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda name: TOOLCHAIN)

    @property
    def titles(self) -> list[str]:
        return [candidate.title for candidate in self.calls]

    def received(self, title: str) -> CandidateExample:
        (candidate,) = [c for c in self.calls if c.title == title]
        return candidate


def _argv(out_dir: Path, page_path: Path, platform: str, extra: list[str] | None = None) -> list[str]:
    return [
        "build_chunks.py",
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Chain fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page_path),
        "--library-repository",
        REPOSITORY,
        "--library-commit",
        COMMIT,
        "--library-platform",
        platform,
        *PLATFORM_FLAGS.get(platform, []),
        *(extra or []),
    ]


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    build_chunks.main()


def _build(
    monkeypatch: pytest.MonkeyPatch,
    out_dir: Path,
    page_path: Path,
    platform: str,
    extra: list[str] | None = None,
) -> None:
    _run(monkeypatch, _argv(out_dir, page_path, platform, extra))


def _chunks(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def _example_texts(out_dir: Path) -> list[str]:
    return [chunk["text"] for chunk in _chunks(out_dir) if "Kind: verified_example" in chunk["text"]]


def _example_text(out_dir: Path, title: str) -> str:
    (text,) = [text for text in _example_texts(out_dir) if f"FQN: Example: {title}\n" in text]
    return text


def _report(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.verification.json").read_text(encoding="utf-8"))


def _expected_text(
    page_path: Path, title: str, description: str, program: str, harness: str | None = None
) -> str:
    """The chunk text an example publishes, built through the same document, chunker and clean-up."""
    harness_line = f"Harness: {harness}\n" if harness else ""
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="example",
        provenance=Provenance(repository=REPOSITORY, commit=COMMIT, path=str(page_path)),
        evidence_refs=(f"{REPOSITORY}@{COMMIT}",),
        title=title,
        body=(
            f"# Example: {title}\n\nFQN: Example: {title}\nKind: verified_example\n"
            f"{harness_line}{description}\n\nExample:\n{program}"
        ),
    )
    (chunk,) = chunk_document(doc)
    return f"{build_chunks._sanitize_chunk_text(chunk.text)}\nSource-Commit: {COMMIT}"


def _harness_line(text: str) -> str | None:
    lines = [line for line in text.splitlines() if line.startswith("Harness: ")]
    assert len(lines) <= 1
    return lines[0] if lines else None


PY_FIRST = "import aspose.words as words\ndoc = words.Document()\nprint(doc)"
PY_SECOND = "other = words.Document()\nprint(other)"


# --- python (CHAIN_PREFIX_PLATFORMS): a continuation is verified and published with its chain imports -------


def test_a_python_continuation_is_verified_and_published_with_the_imports_of_its_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST), ("Second", PY_SECOND)])
    harness = Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    prefixed = "import aspose.words as words\n" + PY_SECOND
    assert harness.received("First").code == PY_FIRST
    assert harness.received("Second").code == prefixed
    assert harness.received("Second").code_sha256 == build_chunks.normalised_code_sha256(prefixed)
    assert harness.received("Second").import_prefix == ()

    second = _example_text(tmp_path, "Second")
    assert second == _expected_text(
        page_path,
        "Second",
        "Prose for Second.",
        prefixed,
        "import lines prepended from earlier examples on this page: import aspose.words as words",
    )
    assert _harness_line(second) == (
        "Harness: import lines prepended from earlier examples on this page: import aspose.words as words"
    )
    assert second.index("Kind: verified_example") < second.index("Harness: ") < second.index("Prose for")


def test_a_starter_with_nothing_added_keeps_todays_body_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST), ("Second", PY_SECOND)])
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    first = _example_text(tmp_path, "First")
    assert first == _expected_text(page_path, "First", "Prose for First.", PY_FIRST)
    assert "Harness:" not in first


def test_the_harness_line_never_carries_an_example_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(
        tmp_path / "_index.md",
        "python",
        [("First", "import os  # Example: usage\nprint(os)"), ("Second", "print(os.name)")],
    )
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    line = _harness_line(_example_text(tmp_path, "Second"))
    assert line is not None
    assert "# Example : usage" in line
    assert "Example:" not in line


def test_a_python_program_naming_an_input_file_gets_the_input_files_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = 'with open("input.ps") as handle:\n    print(handle.read())'
    page_path = _write_page(tmp_path / "_index.md", "python", [("Reads", code)])
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    text = _example_text(tmp_path, "Reads")
    assert _harness_line(text) == f"Harness: {INPUT_FILES_PART}input.ps"
    assert text == _expected_text(page_path, "Reads", "Prose for Reads.", code, f"{INPUT_FILES_PART}input.ps")


def test_a_python_continuation_that_also_names_an_input_file_lists_both_parts_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(
        tmp_path / "_index.md",
        "python",
        [("First", PY_FIRST), ("Second", 'data = open("a.csv").read()\nprint(data)')],
    )
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    assert _harness_line(_example_text(tmp_path, "Second")) == (
        "Harness: import lines prepended from earlier examples on this page: import aspose.words as words; "
        f"{INPUT_FILES_PART}a.csv"
    )


# --- dotnet and rust: the verifier's assembled source is what is published -----------------------------------


def test_the_real_slides_net_continuation_is_published_as_the_assembled_dotnet_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page_path = FURNISHED / "slides_net/pages/_index.md"
    harness = Harness(monkeypatch, "dotnet", verdict=lambda candidate: candidate.title != "Examples")

    _build(monkeypatch, tmp_path, page_path, "dotnet")

    candidates = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    target = next(c for c in candidates if c.title == "Text and formatting")
    # The verifier is handed the ORIGINAL candidate with the whole page; the import-only block too.
    assert harness.titles == [c.title for c in candidates]
    received = harness.received("Text and formatting")
    assert received.code == target.code
    assert len(received.page_code) == 9
    assert received.page_code == target.page_code

    source, added = _assemble_dotnet_source(target.code, target.page_code)
    assert added
    assert source != target.code
    expected_harness = "using directives from other blocks on this page added by the verifier: " + " | ".join(
        added
    )
    text = _example_text(tmp_path, "Text and formatting")
    assert text == _expected_text(
        page_path, "Text and formatting", target.description, source, expected_harness
    )
    assert _harness_line(text) == f"Harness: {expected_harness}"
    for directive in added:
        assert directive in text

    assert "verified 8/9 candidate examples" in capsys.readouterr().out


def test_a_dotnet_import_only_block_is_passed_to_the_verifier_which_rejects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(
        tmp_path / "_index.md",
        "dotnet",
        [("Usings", "using System;\nusing System.Text;"), ("Real", "Console.WriteLine(1);")],
    )
    harness = Harness(monkeypatch, "dotnet", verdict=lambda candidate: candidate.title == "Real")

    _build(monkeypatch, tmp_path, page_path, "dotnet")

    assert harness.titles == ["Usings", "Real"]
    report = _report(tmp_path)
    assert [(r["title"], r["kind"], r["verified"]) for r in report] == [
        ("Usings", "candidate", False),
        ("Real", "candidate", True),
    ]


def test_the_real_cells_rust_wrapped_fragment_is_published_as_the_assembled_rust_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # re-pinned by TC-551: <removed pilot> -> cells_rust
    page_path = FURNISHED / "cells_rust/pages/_index.md"
    harness = Harness(monkeypatch, "rust")

    _build(monkeypatch, tmp_path, page_path, "rust")

    candidates = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    target = candidates[1]
    source, mode, added = _assemble_rust_source(target.code, target.page_code)
    assert mode == "wrapped-fragment"
    assert added
    assert harness.received(target.title).code == target.code
    expected_harness = (
        "wrapped by the verifier in fn main() -> Result<(), Box<dyn std::error::Error>>; "
        "use statements from other blocks on this page added by the verifier: " + " | ".join(added)
    )
    text = _example_text(tmp_path, target.title)
    assert text == _expected_text(page_path, target.title, target.description, source, expected_harness)
    assert _harness_line(text) == f"Harness: {expected_harness}"


def test_a_rust_block_with_its_own_main_is_published_as_given_without_a_harness_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = 'fn main() {\n    println!("ok");\n}'
    page_path = _write_page(tmp_path / "_index.md", "rust", [("Whole", code)])
    Harness(monkeypatch, "rust")

    _build(monkeypatch, tmp_path, page_path, "rust")

    text = _example_text(tmp_path, "Whole")
    assert "Harness:" not in text
    assert text == _expected_text(page_path, "Whole", "Prose for Whole.", code)


# --- cpp: page includes and the main wrapper; import-only blocks are not examples ----------------------------


CPP_FLAGS = ["--library-cpp-target", "example_target"]


def test_a_cpp_fragment_is_published_wrapped_with_the_int_main_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "#include <a.hpp>\nauto value = 1;"
    page_path = _write_page(tmp_path / "_index.md", "cpp", [("Fragment", code)])
    harness = Harness(monkeypatch, "cpp")

    _build(monkeypatch, tmp_path, page_path, "cpp")

    wrapped = _wrap_cpp_fragment_if_needed(code)
    assert wrapped != code
    assert harness.received("Fragment").code == code
    text = _example_text(tmp_path, "Fragment")
    assert text == _expected_text(
        page_path, "Fragment", "Prose for Fragment.", wrapped, "wrapped by the verifier in int main()"
    )


def test_cpp_page_includes_are_published_in_front_only_when_the_compat_name_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = "#include <a.hpp>\nint main() { return 0; }"
    fragment = "auto value = make();"
    page_path = _write_page(tmp_path / "_index.md", "cpp", [("Whole", whole), ("Fragment", fragment)])
    Harness(monkeypatch, "cpp")

    with_compat = tmp_path / "with"
    with_compat.mkdir()
    _build(monkeypatch, with_compat, page_path, "cpp", [*CPP_FLAGS, "--library-cpp-compat", "page-includes"])
    source = "#include <a.hpp>\n" + fragment
    assert _example_text(with_compat, "Fragment") == _expected_text(
        page_path,
        "Fragment",
        "Prose for Fragment.",
        _wrap_cpp_fragment_if_needed(source),
        "#include lines from other blocks on this page added by the verifier: #include <a.hpp>; "
        "wrapped by the verifier in int main()",
    )
    assert "Harness:" not in _example_text(with_compat, "Whole")

    without = tmp_path / "without"
    without.mkdir()
    _build(monkeypatch, without, page_path, "cpp", CPP_FLAGS)
    assert (
        _harness_line(_example_text(without, "Fragment")) == "Harness: wrapped by the verifier in int main()"
    )


def test_a_cpp_run_names_input_files_but_a_compile_only_run_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = '#include <fstream>\nint main() {\n    std::ifstream file("input.ps");\n    return 0;\n}'
    page_path = _write_page(tmp_path / "_index.md", "cpp", [("Reads", code)])
    Harness(monkeypatch, "cpp")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _build(monkeypatch, run_dir, page_path, "cpp")
    assert _harness_line(_example_text(run_dir, "Reads")) == f"Harness: {INPUT_FILES_PART}input.ps"

    compile_dir = tmp_path / "compile"
    compile_dir.mkdir()
    _build(monkeypatch, compile_dir, page_path, "cpp", [*CPP_FLAGS, "--library-cpp-verify-mode", "compile"])
    assert "Harness:" not in _example_text(compile_dir, "Reads")


def test_a_cpp_import_only_block_is_never_verified_published_or_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page_path = _write_page(
        tmp_path / "_index.md",
        "cpp",
        [("Install", "#include <a.hpp>"), ("Real", "#include <a.hpp>\nint main() { return 0; }")],
    )
    harness = Harness(monkeypatch, "cpp")

    _build(monkeypatch, tmp_path, page_path, "cpp")

    assert harness.titles == ["Real"]
    assert [("FQN: Example: Real\n" in text) for text in _example_texts(tmp_path)] == [True]
    report = _report(tmp_path)
    skipped = report[0]
    assert skipped["title"] == "Install"
    assert skipped["kind"] == "imports_only"
    assert skipped["verified"] is False
    assert skipped["output"] == "import-only block: not an example by itself"
    assert skipped["cached"] is False
    assert skipped["index"] == 0
    assert skipped["harness"] == ""
    assert report[1]["title"] == "Real"
    assert report[1]["index"] == 1
    assert report[1]["kind"] == "candidate"
    out = capsys.readouterr().out
    assert "verified 1/1 candidate examples" in out
    assert "1 import-only blocks" in out


def test_an_import_only_block_is_never_a_cache_entry_and_does_not_force_a_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "cpp", [("Install", "#include <a.hpp>")])
    harness = Harness(monkeypatch, "cpp")

    cache_out = tmp_path / "cache.json"
    _build(monkeypatch, tmp_path, page_path, "cpp", ["--verification-cache-out", str(cache_out)])

    assert harness.calls == []
    assert harness.prepare_calls == 0
    assert json.loads(cache_out.read_text(encoding="utf-8")) == {"schema_version": 1, "entries": []}


def test_java_and_nodejs_import_only_blocks_are_skipped_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java_dir = tmp_path / "java"
    java_dir.mkdir()
    java_page = _write_page(
        java_dir / "_index.md",
        "java",
        [("Imports", "import java.util.List;"), ("Body", "System.out.println(1);")],
    )
    java = Harness(monkeypatch, "java")
    _build(monkeypatch, java_dir, java_page, "java")
    assert java.titles == ["Body"]

    node_dir = tmp_path / "node"
    node_dir.mkdir()
    node_page = _write_page(
        node_dir / "_index.md",
        "nodejs",
        [
            ("Imports", "import { a } from 'example-package';"),
            ("Body", "import { a } from 'example-package';\na();"),
        ],
    )
    node = Harness(monkeypatch, "nodejs")
    _build(monkeypatch, node_dir, node_page, "nodejs")
    assert node.titles == ["Body"]


# --- go and java: published unchanged ----------------------------------------------------------------------------


def test_a_go_candidate_is_published_unchanged_without_a_harness_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("a")\n}'
    page_path = _write_page(
        tmp_path / "_index.md", "go", [("Whole", first), ("Fragment", "value := 1\n_ = value")]
    )
    harness = Harness(monkeypatch, "go")

    _build(monkeypatch, tmp_path, page_path, "go")

    for title, code in (("Whole", first), ("Fragment", "value := 1\n_ = value")):
        assert harness.received(title).code == code
        text = _example_text(tmp_path, title)
        assert "Harness:" not in text
        assert text == _expected_text(page_path, title, f"Prose for {title}.", code)


def test_a_java_candidate_is_published_unchanged_without_a_harness_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "import java.util.List;\nSystem.out.println(List.of());"
    page_path = _write_page(tmp_path / "_index.md", "java", [("Body", code)])
    Harness(monkeypatch, "java")

    _build(monkeypatch, tmp_path, page_path, "java")

    text = _example_text(tmp_path, "Body")
    assert "Harness:" not in text
    assert text == _expected_text(page_path, "Body", "Prose for Body.", code)


# --- a README content block's prose holds markdown headings ------------------------------------------------------------


def test_a_readme_content_candidate_is_published_as_one_recognisable_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = FURNISHED / "pdf_net/pages/_index.md"
    Harness(monkeypatch, "dotnet")

    _build(monkeypatch, tmp_path, page_path, "dotnet")

    title = "Installation, Dependencies, and Quick Start"
    candidates = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    assert len(candidates) == 3
    example_chunks = [chunk for chunk in _chunks(tmp_path) if chunk["content_type"] == "example"]
    # One chunk per published candidate: the three example blocks and the Quick Start of the README.
    assert len(example_chunks) == 4
    (chunk,) = [chunk for chunk in example_chunks if f"FQN: Example: {title}\n" in chunk["text"]]
    text = chunk["text"]
    assert "Kind: verified_example\n" in text
    assert "\nExample:\nusing Aspose.Pdf;" in text
    assert not any(line.startswith("#") for line in text.splitlines())
    # The text of the README's headings is kept as plain prose.
    assert "\nRequired Package Dependencies\n" in text


# --- the supply line and the legacy fallback ------------------------------------------------------------------------


def test_the_candidate_supply_line_counts_every_kind_of_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    front_matter = {
        "single": {
            "block": [
                {"title": "One", "content": "```python\nprint(1)\n```\n\n```bash\nls\n```"},
                {"title": "Two", "content": "```python\nprint(1)\n```"},
            ]
        },
        "content": {
            "block": [{"title_left": "Quick start", "content_left": "```python\nprint(3)\n```"}],
        },
    }
    page_path = tmp_path / "_index.md"
    page_path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python")

    out = capsys.readouterr().out.splitlines()
    assert (
        "candidate supply: 2 candidates for python (1 from example blocks, 1 from content blocks; "
        "dropped 1 duplicates and 1 fences in other languages; 0 import-only blocks)"
    ) in out
    assert "verified 2/2 candidate examples" in out


def test_a_page_without_the_platform_language_keeps_the_legacy_candidates_in_legacy_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page_path = FURNISHED / "pdf_net/pages/_index.md"
    harness = Harness(monkeypatch, "rust")

    _build(monkeypatch, tmp_path, page_path, "rust")

    legacy = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    assert harness.titles == [candidate.title for candidate in legacy]
    assert [c.code for c in harness.calls] == [c.code for c in legacy]
    out = capsys.readouterr().out.splitlines()
    assert (
        "candidate supply: no rust code fences on the page; using the first fence of every example block "
        "unfiltered"
    ) in out
    assert f"verified {len(legacy)}/{len(legacy)} candidate examples" in out


# --- verification records ---------------------------------------------------------------------------------------------


def test_records_carry_source_start_harness_status_and_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = FixtureRecord(
        literal="input.ps", destination="input.ps", origin="generated", source="minimal-ps", sha256="ab" * 32
    )
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST), ("Second", PY_SECOND)])
    Harness(monkeypatch, "python", status="fixture_unavailable", fixtures=(fixture,))

    _build(monkeypatch, tmp_path, page_path, "python")

    first, second = _report(tmp_path)
    for record in (first, second):
        assert record["kind"] == "candidate"
        assert record["source"] == "single"
        assert record["status"] == "fixture_unavailable"
        assert record["fixtures"] == [fixture.as_dict()]
    assert (first["starts_example"], second["starts_example"]) == (True, False)
    assert first["harness"] == ""
    assert second["harness"] == (
        "import lines prepended from earlier examples on this page: import aspose.words as words"
    )


def test_a_result_without_status_or_fixtures_records_empty_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST)])
    Harness(monkeypatch, "python")
    monkeypatch.setitem(
        build_chunks._PLATFORM_DISPATCH["python"],
        "verify",
        lambda candidate, **kwargs: type("Bare", (), {"verified": True, "output": "ok"})(),
    )

    _build(monkeypatch, tmp_path, page_path, "python")

    (record,) = _report(tmp_path)
    assert record["status"] == ""
    assert record["fixtures"] == []


# --- the verification cache is keyed on the published program ------------------------------------------------------------


DOTNET_A = "using System;\nusing System.Text;\nConsole.WriteLine(1);"
DOTNET_B = "Console.WriteLine(2);"
DOTNET_C = "using System.Linq;\nConsole.WriteLine(3);"


def _cache_entries(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_the_cache_entry_of_a_dotnet_continuation_is_keyed_on_the_published_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(
        tmp_path / "_index.md", "dotnet", [("A", DOTNET_A), ("B", DOTNET_B), ("C", DOTNET_C)]
    )
    Harness(monkeypatch, "dotnet")
    cache = tmp_path / "cache.json"

    _build(monkeypatch, tmp_path, page_path, "dotnet", ["--verification-cache-out", str(cache)])

    candidates = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    program_b = _assemble_dotnet_source(DOTNET_B, candidates[1].page_code)[0]
    assert program_b != DOTNET_B
    by_title = {entry["title"]: entry for entry in _cache_entries(cache)}
    assert by_title["B"]["code_sha256"] == build_chunks.normalised_code_sha256(program_b)
    assert by_title["B"]["code_sha256"] != build_chunks.normalised_code_sha256(DOTNET_B)
    record_b = next(r for r in _report(tmp_path) if r["title"] == "B")
    assert record_b["code_sha256"] == by_title["B"]["code_sha256"]


def test_a_changed_using_directive_in_another_block_makes_the_next_run_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache.json"
    flags = ["--verification-cache", str(cache), "--verification-cache-out", str(cache)]
    page_path = _write_page(
        tmp_path / "_index.md", "dotnet", [("A", DOTNET_A), ("B", DOTNET_B), ("C", DOTNET_C)]
    )
    harness = Harness(monkeypatch, "dotnet")

    _build(monkeypatch, tmp_path, page_path, "dotnet", flags)
    assert harness.titles == ["A", "B", "C"]
    capsys.readouterr()

    harness.calls.clear()
    _build(monkeypatch, tmp_path, page_path, "dotnet", flags)
    assert harness.calls == []
    assert "verified 3/3 candidate examples (3 from cache)" in capsys.readouterr().out

    # Prose is not code: a changed description leaves every key alone.
    page = build_chunks._load_furnished_page(page_path)
    page["single"]["block"][1]["content"] = page["single"]["block"][1]["content"].replace(
        "Prose", "New prose"
    )
    page_path.write_text("---\n" + yaml.safe_dump(page, sort_keys=False) + "---\n", encoding="utf-8")
    _build(monkeypatch, tmp_path, page_path, "dotnet", flags)
    assert harness.calls == []

    # A changed using directive in block C changes the program published for block B.
    changed = _write_page(
        tmp_path / "_index.md",
        "dotnet",
        [("A", DOTNET_A), ("B", DOTNET_B), ("C", DOTNET_C.replace("System.Linq", "System.Collections"))],
    )
    _build(monkeypatch, tmp_path, changed, "dotnet", flags)
    assert "B" in harness.titles
    assert "C" in harness.titles


def test_the_go_cache_key_includes_the_import_units_of_the_whole_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    flags = ["--verification-cache", str(cache), "--verification-cache-out", str(cache)]
    whole = 'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println() }'
    fragment = "value := 1\n_ = value"
    page_path = _write_page(tmp_path / "_index.md", "go", [("Whole", whole), ("Fragment", fragment)])
    harness = Harness(monkeypatch, "go")

    _build(monkeypatch, tmp_path, page_path, "go", flags)
    entries = {entry["title"]: entry for entry in _cache_entries(cache)}
    assert entries["Fragment"]["code_sha256"] == build_chunks.normalised_code_sha256(
        fragment + '\nimport "fmt"'
    )
    assert entries["Whole"]["code_sha256"] == build_chunks.normalised_code_sha256(whole + '\nimport "fmt"')

    harness.calls.clear()
    _build(monkeypatch, tmp_path, page_path, "go", flags)
    assert harness.calls == []

    changed = _write_page(
        tmp_path / "_index.md",
        "go",
        [
            ("Whole", whole.replace('"fmt"', '"os"').replace("fmt.Println()", "os.Exit(0)")),
            ("Fragment", fragment),
        ],
    )
    _build(monkeypatch, tmp_path, changed, "go", flags)
    assert "Fragment" in harness.titles


def test_a_cached_and_a_fresh_verdict_publish_identical_chunk_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    flags = ["--verification-cache", str(cache), "--verification-cache-out", str(cache)]
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST), ("Second", PY_SECOND)])
    harness = Harness(monkeypatch, "python")

    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    _build(monkeypatch, fresh_dir, page_path, "python", flags)
    assert harness.titles == ["First", "Second"]

    harness.calls.clear()
    cached_dir = tmp_path / "cached"
    cached_dir.mkdir()
    _build(monkeypatch, cached_dir, page_path, "python", flags)
    assert harness.calls == []
    assert {r["cached"] for r in _report(cached_dir)} == {True}
    assert _example_texts(cached_dir) == _example_texts(fresh_dir)
    assert _harness_line(_example_text(cached_dir, "Second")) is not None


def test_the_cache_hit_check_uses_the_key_of_the_published_program_for_a_python_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    page_path = _write_page(tmp_path / "_index.md", "python", [("First", PY_FIRST), ("Second", PY_SECOND)])
    Harness(monkeypatch, "python")

    _build(monkeypatch, tmp_path, page_path, "python", ["--verification-cache-out", str(cache)])

    entries = {entry["title"]: entry for entry in _cache_entries(cache)}
    assert entries["First"]["code_sha256"] == build_chunks.normalised_code_sha256(PY_FIRST)
    assert entries["Second"]["code_sha256"] == build_chunks.normalised_code_sha256(
        "import aspose.words as words\n" + PY_SECOND
    )


# --- one hash rule, and the wiring ---------------------------------------------------------------------------------------


def test_build_chunks_uses_the_hash_rule_of_example_candidates() -> None:
    for code in ("a\nb", "\r\n a  \r\n\r\n", "", "x = 'é'"):
        assert build_chunks.normalised_code_sha256(code) == example_candidates.normalised_code_sha256(code)
    assert inspect.getsource(build_chunks.normalised_code_sha256).startswith("def normalised_code_sha256(")
    assert "hashlib" not in inspect.getsource(build_chunks.normalised_code_sha256)


def test_the_supply_effective_candidate_and_published_program_calls_exist_in_the_production_function() -> (
    None
):
    source = inspect.getsource(build_chunks._build_verified_example_chunks)
    assert "extract_candidate_supply(" in source
    assert "effective_candidate(" in source
    assert "_published_program(" in source
    # The legacy call text stays for the fallback (TC-450's wiring test reads it).
    assert "extract_candidate_examples(page)" in source
    committed = (REPO_ROOT / "infra/build_chunks.py").read_text(encoding="utf-8")
    assert "extract_candidate_examples(page)" in committed


def test_the_published_program_is_a_pure_function_of_the_candidate_the_page_and_the_arguments() -> None:
    page_path = FURNISHED / "slides_net/pages/_index.md"
    candidates = extract_candidate_examples(build_chunks._load_furnished_page(page_path))
    args = dataclasses.make_dataclass("Args", ["library_platform", "furnished_page"])("dotnet", page_path)
    first = build_chunks._published_program(candidates[2], args)
    second = build_chunks._published_program(candidates[2], args)
    assert first == second
    assert first[0] == _assemble_dotnet_source(candidates[2].code, candidates[2].page_code)[0]
