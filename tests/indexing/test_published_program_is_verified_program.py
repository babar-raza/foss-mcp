"""Offline tests for TC-803 (REQ-G3-014): a published example is the program the toolchain compiled.

Before this card a java or go README snippet was served as the bare fragment although the verifier had
added imports, a class and ``main`` (java) or a package clause, imports and ``func main()`` (go): pasting it
failed to compile (RD2-190, live: ``undefined: pdf``). A staged input file (``SimpleTable.one``) was named
by a generic sentence only, with no repository path or commit (RD2-163).

Nothing here touches git, a toolchain or the network. The REAL ``verify_java_example`` /
``verify_go_example`` / ``verify_dotnet_example`` / ``verify_python_example`` run, with only the subprocess
step (``example_verifier._run`` / ``_run_bounded``) replaced by a recorder that reads the file the toolchain
would have been handed. Each test then compares what ``build_chunks`` published with that recorded file.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_verifier import (
    RuntimeRecord,
    VerificationResult,
    java_program_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests/fixtures/furnished"
PDF_GO_PAGE = FURNISHED / "pdf_go/pages/_index.md"
CELLS_GO_PAGE = FURNISHED / "cells_go/pages/_index.md"
PDF_JAVA_PAGE = FURNISHED / "pdf_java/pages/_index.md"
CELLS_JAVA_PAGE = FURNISHED / "cells_java/pages/_index.md"
PDF_NET_PAGE = FURNISHED / "pdf_net/pages/_index.md"

REPOSITORY = "example-org/reference-library"
COMMIT = "3" * 40
TOOLCHAIN = "toolchain 1.0"
GO_MODULE = "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
CELLS_GO_MODULE = "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26"
CELLS_JAVA_PACKAGES = "org.aspose.cells_foss"

PREPARE_NAME = {
    "go": "prepare_go_library",
    "java": "prepare_java_library",
    "dotnet": "prepare_reference_library",
    "python": "prepare_python_library",
}


# --- helpers ---------------------------------------------------------------------------------------------


def _completed(args: list[str], returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, "", stderr)


def _argv(out_dir: Path, page: Path, platform: str, extra: list[str] | None = None) -> list[str]:
    return [
        "build_chunks.py",
        "--api-surface",
        str(REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"),
        "--title",
        "Published program fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page),
        "--library-repository",
        REPOSITORY,
        "--library-commit",
        COMMIT,
        "--library-platform",
        platform,
        *(extra or []),
    ]


def _prepare(monkeypatch: pytest.MonkeyPatch, platform: str, prepared: Path) -> None:
    if not prepared.suffix and not prepared.exists():
        prepared.mkdir(parents=True)
    monkeypatch.setattr(build_chunks, PREPARE_NAME[platform], lambda *a, **k: prepared)
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda name: TOOLCHAIN)


def _ran(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run step of a runnable platform (java, dotnet, go): a clean run that printed something."""
    monkeypatch.setattr(
        build_chunks,
        "run_compiled_candidate",
        lambda *a, **k: RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=1),
    )


def _build(
    monkeypatch: pytest.MonkeyPatch,
    out_dir: Path,
    page: Path,
    platform: str,
    extra: list[str] | None = None,
) -> list[str]:
    monkeypatch.setattr(sys, "argv", _argv(out_dir, page, platform, extra))
    build_chunks.main()
    chunks = json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    return [chunk["text"] for chunk in chunks if "Kind: verified_example" in chunk["text"]]


def _program(text: str) -> str:
    """The program of a published example chunk: everything after ``Example:`` up to ``Source-Commit:``."""
    body = text.split("\nExample:\n", 1)[1]
    return body.rsplit("\nSource-Commit:", 1)[0]


def _harness(text: str) -> str | None:
    lines = [line for line in text.splitlines() if line.startswith("Harness: ")]
    assert len(lines) <= 1
    return lines[0][len("Harness: ") :] if lines else None


def _is_authored(text: str) -> bool:
    return "Provenance: authored" in text


def _readme(texts: list[str]) -> list[str]:
    return [text for text in texts if not _is_authored(text)]


def _norm(program: str) -> str:
    return program.strip()


class _Recorder:
    """Stands in for ``example_verifier._run``: records the source file the toolchain would be handed."""

    def __init__(self, source_file: str, tool: str, fail_first_build: str = "") -> None:
        self.source_file = source_file
        self.tool = tool
        self.sources: list[str] = []
        self.calls: list[list[str]] = []
        self.fail_first_build = fail_first_build
        self.failed = False
        self.csprojs: list[str] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[0] != self.tool:
            return _completed(args)
        if self.tool == "javac":
            self.sources.append((cwd / args[-1]).read_text(encoding="utf-8"))
            return _completed(args)
        if self.tool == "dotnet":
            self.sources.append((cwd / self.source_file).read_text(encoding="utf-8"))
            self.csprojs.append((cwd / "candidate.csproj").read_text(encoding="utf-8"))
            return _completed(args)
        assert self.tool == "go"
        source = (cwd / self.source_file).read_text(encoding="utf-8")
        if args[1] == "build":
            self.sources.append(source)
            if self.fail_first_build and not self.failed:
                self.failed = True
                return _completed(args, 1, self.fail_first_build)
        return _completed(args)


def _forbid_toolchain(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Any toolchain invocation is a test failure: used to prove a cache hit verified nothing."""
    calls: list[list[str]] = []

    def refuse(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        raise AssertionError(f"a cache hit must not run the toolchain: {args}")

    monkeypatch.setattr(example_verifier, "_run", refuse)
    monkeypatch.setattr(example_verifier, "_run_bounded", lambda *a, **k: refuse(a[0], cwd=k["cwd"]))
    return calls


def _cache_flags(path: Path) -> list[str]:
    return ["--verification-cache", str(path), "--verification-cache-out", str(path)]


# --- java: the published program is the file javac was given ---------------------------------------------


@pytest.mark.parametrize(
    ("page", "extra", "has_fragments"),
    [
        (PDF_JAVA_PAGE, [], True),
        # cells/java's README blocks all declare their own class: published unchanged, no Harness part.
        (CELLS_JAVA_PAGE, ["--library-java-packages", CELLS_JAVA_PACKAGES], False),
    ],
    ids=["pdf-java-page-imports-mode", "cells-java-pilot-mode"],
)
def test_java_published_text_is_the_wrapper_source_javac_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Path, extra: list[str], has_fragments: bool
) -> None:
    recorder = _Recorder("Candidate.java", "javac")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "java", tmp_path / "library.jar")
    _ran(monkeypatch)

    texts = _build(monkeypatch, tmp_path, page, "java", extra)

    assert texts and recorder.sources
    readme = _readme(texts)
    assert readme, "the real page has README examples"
    # Every README program that was published is, byte for byte, a file javac was handed.
    handed = {_norm(source) for source in recorder.sources}
    for text in readme:
        assert _norm(_program(text)) in handed
    # ... and it is a complete program, not the README fragment.
    for text in readme:
        assert re.search(r"public\s+class\s+\w+", _program(text)), _program(text)
        assert "static void main(" in _program(text)
    # A block that had no class of its own says it was wrapped.
    wrapped = [text for text in readme if "public class Candidate" in _program(text)]
    assert bool(wrapped) is has_fragments
    for text in wrapped:
        harness = _harness(text)
        assert harness is not None and "public class Candidate" in harness
    for text in set(readme) - set(wrapped):
        assert _harness(text) is None


def test_java_pilot_mode_fragment_is_published_with_its_hoisted_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "import java.nio.file.*;" + chr(10) + 'System.out.println(Files.exists(Path.of("a")));'
    page = tmp_path / "_index.md"
    page.write_text(
        "---"
        + chr(10)
        + yaml.safe_dump(
            {
                "title": "Fragment",
                "single": {
                    "block": [
                        {
                            "title": "Frag",
                            "content": "Prose."
                            + chr(10) * 2
                            + f"```java{chr(10)}{code}{chr(10)}```{chr(10)}",
                        }
                    ]
                },
            }
        )
        + "---"
        + chr(10),
        encoding="utf-8",
    )
    recorder = _Recorder("Candidate.java", "javac")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "java", tmp_path / "library.jar")
    _ran(monkeypatch)

    (text,) = _build(monkeypatch, tmp_path, page, "java", ["--library-java-packages", "org.example"])

    program = _program(text)
    assert _norm(program) == _norm(recorder.sources[0])
    header = ["import org.example.*;", "import java.io.*;", "import java.util.*;", "import java.nio.file.*;"]
    assert program.splitlines()[:4] == header
    assert "public static void main(String[] args) throws Exception {" in program
    harness = _harness(text)
    assert harness is not None and "public class Candidate" in harness


def test_java_wrapper_is_a_pure_function_of_the_code_and_the_pilot_configuration() -> None:
    code = 'try (Document doc = new Document()) {\n    doc.save("out.pdf");\n}'
    class_name, source = java_program_source(code, ("import org.aspose.pdf.annotations.*;",), None)
    assert class_name == "Candidate"
    assert source == (
        "import org.aspose.pdf.*;\nimport org.aspose.pdf.annotations.*;\n\n"
        "public class Candidate {\n"
        "    public static void main(String[] args) throws Exception {\n"
        '        try (Document doc = new Document()) {\n            doc.save("out.pdf");\n        }\n'
        "    }\n}\n"
    )
    assert java_program_source(code, ("import org.aspose.pdf.annotations.*;",), None) == (class_name, source)
    own_class = "public class Demo {\n    public static void main(String[] a) {}\n}\n"
    assert java_program_source(own_class, (), ("org.x",)) == ("Demo", own_class)
    pilot = java_program_source("import java.nio.file.*;\nSystem.out.println(1);", (), ("org.x",))[1]
    assert pilot.startswith(
        "import org.x.*;\nimport java.io.*;\nimport java.util.*;\nimport java.nio.file.*;\n"
    )


def test_java_example_with_its_own_public_class_is_published_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "public class Demo {\n    public static void main(String[] args) {\n        System.out.println(1);\n    }\n}"
    page = tmp_path / "_index.md"
    page.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "title": "Own class",
                "single": {
                    "block": [{"title": "Own", "content": f"Prose for Own.\n\n```java\n{code}\n```\n"}]
                },
            }
        )
        + "---\n",
        encoding="utf-8",
    )
    recorder = _Recorder("Demo.java", "javac")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "java", tmp_path / "library.jar")
    _ran(monkeypatch)

    (text,) = _build(monkeypatch, tmp_path, page, "java")

    assert _program(text) == code
    assert _harness(text) is None
    assert recorder.sources == [code]


# --- go: the published program is the assembled main.go, and a cache hit republishes it ------------------


@pytest.mark.parametrize(
    ("page", "module"),
    [(PDF_GO_PAGE, GO_MODULE), (CELLS_GO_PAGE, CELLS_GO_MODULE)],
    ids=["pdf-go", "cells-go"],
)
def test_go_published_text_is_the_main_go_that_was_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Path, module: str
) -> None:
    recorder = _Recorder("main.go", "go")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "go", tmp_path / "lib")
    _ran(monkeypatch)  # TC-805: go is run too; a clean exit-0 run that printed something

    texts = _build(monkeypatch, tmp_path, page, "go", ["--library-module-path", module])

    readme = _readme(texts)
    assert readme and recorder.sources
    handed = {_norm(source) for source in recorder.sources}
    for text in readme:
        program = _program(text)
        assert _norm(program) in handed
        assert program.lstrip().startswith("package main"), program
    assert any(_harness(text) is not None for text in readme)


def test_go_blanked_unused_variables_are_published_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = 'doc, _ := pdf.Open("a.pdf")\npages := 3\ndoc.Save("b.pdf")'
    page = tmp_path / "_index.md"
    page.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "title": "Unused",
                "single": {"block": [{"title": "Unused", "content": f"Prose.\n\n```go\n{code}\n```\n"}]},
            }
        )
        + "---\n",
        encoding="utf-8",
    )
    recorder = _Recorder("main.go", "go", fail_first_build="./main.go:5:2: declared and not used: pages")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "go", tmp_path / "lib")
    _ran(monkeypatch)  # TC-805: go is run too; a clean exit-0 run that printed something

    (text,) = _build(monkeypatch, tmp_path, page, "go", ["--library-module-path", GO_MODULE])

    program = _program(text)
    assert _norm(program) == _norm(recorder.sources[-1])
    assert "\t_ = pages" in program
    harness = _harness(text)
    assert harness is not None
    assert "wrapped by the verifier in package main" in harness and "_ = name`: pages" in harness


def test_go_cache_hit_republishes_the_assembled_source_and_verifies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    recorder = _Recorder("main.go", "go")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "go", tmp_path / "lib")
    _ran(monkeypatch)  # TC-805: go is run too; a clean exit-0 run that printed something
    flags = ["--library-module-path", GO_MODULE, *_cache_flags(cache)]
    first = _build(monkeypatch, tmp_path, PDF_GO_PAGE, "go", flags)
    entries = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    assembled = [entry for entry in entries if entry.get("assembled_source")]
    assert assembled, "the verifier's assembled main.go is stored in the cache entry"
    assert all(entry["assembled_source"].lstrip().startswith("package main") for entry in assembled)

    calls = _forbid_toolchain(monkeypatch)
    second = _build(monkeypatch, tmp_path, PDF_GO_PAGE, "go", flags)

    assert calls == []
    assert second == first
    assert any(_harness(text) for text in _readme(second))


def test_go_legacy_cache_entry_without_assembled_source_republishes_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    recorder = _Recorder("main.go", "go")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "go", tmp_path / "lib")
    _ran(monkeypatch)  # TC-805: go is run too; a clean exit-0 run that printed something
    flags = ["--library-module-path", GO_MODULE, *_cache_flags(cache)]
    _build(monkeypatch, tmp_path, PDF_GO_PAGE, "go", flags)
    document = json.loads(cache.read_text(encoding="utf-8"))
    for entry in document["entries"]:
        # TC-805: go is a runnable platform, so an entry without ``verifier_version`` is a miss (it cannot say the
        # example ran); the entry kept here is the one written between TC-801 and TC-803: versioned, no source.
        entry.pop("assembled_source", None)
    cache.write_text(json.dumps(document), encoding="utf-8")

    _forbid_toolchain(monkeypatch)
    texts = _build(monkeypatch, tmp_path, PDF_GO_PAGE, "go", flags)

    readme = _readme(texts)
    assert readme
    assert any("package main" not in _program(text) for text in readme), "fragments stay fragments"
    assert all(_harness(text) is None for text in readme)


def test_authored_examples_are_unchanged_by_the_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = yaml.safe_load((FURNISHED / "pdf_go/authored_examples.yaml").read_text(encoding="utf-8"))
    recorder = _Recorder("main.go", "go")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "go", tmp_path / "lib")
    _ran(monkeypatch)  # TC-805: go is run too; a clean exit-0 run that printed something

    texts = _build(
        monkeypatch,
        tmp_path,
        PDF_GO_PAGE,
        "go",
        [
            "--library-module-path",
            GO_MODULE,
            "--library-repository",
            sidecar["library_repository"],
            "--library-commit",
            sidecar["library_commit"],
            "--api-surface",
            str(REPO_ROOT / "tests/fixtures/pdf_go/api_surface.json"),
        ],
    )

    authored = [text for text in texts if _is_authored(text)]
    assert len(authored) == len(sidecar["examples"]) > 0
    for text in authored:
        assert _harness(text) is None
        assert _program(text).startswith("// Authored example:")
    published = {_norm(_program(text)) for text in authored}
    assert published == {_norm(example["code"]) for example in sidecar["examples"]}


# --- dotnet: one Harness part names ImplicitUsings and the target framework -------------------------------


def test_dotnet_harness_part_names_implicit_usings_and_the_target_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "Library.csproj"
    library.write_text(
        "<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    recorder = _Recorder("Program.cs", "dotnet")
    monkeypatch.setattr(example_verifier, "_run", recorder)
    _prepare(monkeypatch, "dotnet", library)
    _ran(monkeypatch)
    cache = tmp_path / "cache.json"
    flags = ["--library-csproj", "src/Library.csproj", *_cache_flags(cache)]

    texts = _build(monkeypatch, tmp_path, PDF_NET_PAGE, "dotnet", flags)

    readme = _readme(texts)
    assert readme
    for text in readme:
        harness = _harness(text)
        assert harness is not None
        assert "ImplicitUsings=enable, net9.0" in harness
    assert all("<TargetFramework>net9.0</TargetFramework>" in csproj for csproj in recorder.csprojs)
    assert all("<ImplicitUsings>enable</ImplicitUsings>" in csproj for csproj in recorder.csprojs)
    entries = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    built = [entry for entry in entries if entry["verified"]]
    assert built and all(entry["target_framework"] == "net9.0" for entry in built)

    _forbid_toolchain(monkeypatch)
    assert _build(monkeypatch, tmp_path, PDF_NET_PAGE, "dotnet", flags) == texts


# --- staged input files: where each one came from ---------------------------------------------------------


STAGING_CODE = 'doc = Document("SimpleTable.one")\nreport = Document("report.docx")\nprint(doc, report)'


def _python_page(tmp_path: Path) -> Path:
    page = tmp_path / "_index.md"
    page.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "title": "Staging",
                "single": {
                    "block": [
                        {"title": "Reads", "content": f"Prose for Reads.\n\n```python\n{STAGING_CODE}\n```\n"}
                    ]
                },
            }
        )
        + "---\n",
        encoding="utf-8",
    )
    return page


def _python_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake venv beside a fake pinned clone that holds ``testfiles/SimpleTable.one``; returns the interpreter."""
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    sample = tmp_path / "repo" / "testfiles" / "SimpleTable.one"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"one-bytes")
    monkeypatch.setattr(
        example_verifier,
        "_run_bounded",
        lambda args, **kwargs: _completed(list(args)),
    )
    _prepare(monkeypatch, "python", interpreter)
    return interpreter


def test_staged_input_files_are_traced_to_their_repository_path_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _python_environment(tmp_path, monkeypatch)
    page = _python_page(tmp_path)

    (text,) = _build(monkeypatch, tmp_path, page, "python")

    harness = _harness(text)
    assert harness is not None
    assert f"testfiles/SimpleTable.one @ {COMMIT}" in harness
    assert f"SimpleTable.one from testfiles/SimpleTable.one @ {COMMIT}" in harness
    assert REPOSITORY in harness
    assert "report.docx generated by the verifier as a minimal docx file, not from the repository" in harness
    assert "when it can" not in harness, "the generic sentence is replaced once the provenance is known"
    assert _program(text) == STAGING_CODE


def test_staged_input_provenance_survives_a_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _python_environment(tmp_path, monkeypatch)
    page = _python_page(tmp_path)
    cache = tmp_path / "cache.json"
    first = _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache))
    entry = json.loads(cache.read_text(encoding="utf-8"))["entries"][0]
    assert {fixture["origin"] for fixture in entry["fixtures"]} == {"repository", "generated"}

    _forbid_toolchain(monkeypatch)
    second = _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache))

    assert second == first


def test_a_later_example_on_the_page_still_names_where_a_shared_staged_file_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page's python examples share one workdir: the second finds ``SimpleTable.one`` already there."""
    _python_environment(tmp_path, monkeypatch)
    second_code = 'doc = Document("SimpleTable.one")\nprint(len(str(doc)))'
    page = tmp_path / "_index.md"
    blocks = [("Reads", STAGING_CODE), ("Reads again", second_code)]
    page.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "title": "Staging",
                "single": {
                    "block": [
                        {"title": title, "content": f"Prose for {title}.\n\n```python\n{code}\n```\n"}
                        for title, code in blocks
                    ]
                },
            }
        )
        + "---\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache.json"

    first, again = _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache))
    entries = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    assert any(f["origin"] == "present" for entry in entries for f in entry["fixtures"])

    for text in (first, again):
        harness = _harness(text)
        assert harness is not None
        assert f"SimpleTable.one from testfiles/SimpleTable.one @ {COMMIT}" in harness
        assert "already in the working directory" not in harness
    # A whole-page cache hit publishes the same, in the same order.
    _forbid_toolchain(monkeypatch)
    assert _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache)) == [first, again]


def test_input_files_without_recorded_provenance_keep_the_generic_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _python_environment(tmp_path, monkeypatch)
    page = _python_page(tmp_path)
    cache = tmp_path / "cache.json"
    _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache))
    document = json.loads(cache.read_text(encoding="utf-8"))
    for entry in document["entries"]:
        entry.pop("fixtures")
    cache.write_text(json.dumps(document), encoding="utf-8")

    _forbid_toolchain(monkeypatch)
    (text,) = _build(monkeypatch, tmp_path, page, "python", _cache_flags(cache))

    harness = _harness(text)
    assert harness is not None
    assert "when it can" in harness and "SimpleTable.one, report.docx" in harness
    assert "@ " not in harness


def test_a_named_input_the_verifier_did_not_supply_is_said_to_be_missing() -> None:
    part = build_chunks._staged_inputs_part(
        [{"literal": "a.docx", "origin": "present", "source": ""}], ("a.docx", "b.one"), REPOSITORY, COMMIT
    )
    assert "a.docx already in the working directory" in part
    assert "b.one not supplied" in part


# --- stand-ins and wiring ----------------------------------------------------------------------------------


def test_a_stand_in_verifier_changes_nothing_about_the_published_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test double states no assembled source, framework or fixtures: the published program is the block."""
    _prepare(monkeypatch, "go", tmp_path / "lib")
    monkeypatch.setitem(
        build_chunks._PLATFORM_DISPATCH["go"],
        "verify",
        lambda candidate, **kwargs: VerificationResult(candidate=candidate, verified=True, output="ok"),
    )

    texts = _build(monkeypatch, tmp_path, PDF_GO_PAGE, "go", ["--library-module-path", GO_MODULE])

    readme = _readme(texts)
    assert readme
    assert any("package main" not in _program(text) for text in readme), "fragments stay fragments"
    assert all(_harness(text) is None for text in readme)


def test_the_assembly_is_wired_into_the_production_entrypoints() -> None:
    chunks_source = inspect.getsource(build_chunks._build_verified_example_chunks)
    assert "_published_assembly(" in chunks_source
    assert "published_program" in chunks_source and "published_harness" in chunks_source
    assert "_cache_assembly_fields(" in chunks_source
    assert "java_program_source(" in inspect.getsource(build_chunks._published_program)
    assert "java_program_source(" in inspect.getsource(example_verifier.verify_java_example)
    assert "assembled_source=" in inspect.getsource(example_verifier.verify_go_example)
    assert "target_framework=" in inspect.getsource(example_verifier.verify_dotnet_example)
    assert {"assembled_source", "target_framework", "fixtures"} <= build_chunks._CACHE_RUNTIME_FIELDS
