"""Tests for src/foss_mcp/indexing/example_verifier.py (REQ-G2-048).

Real network, real git clone, real toolchain builds/execution - nothing
here is mocked: the whole point of this card is that a candidate example's
verdict comes from a real compiler or interpreter, not from a guess about
what it should do.

This is genuinely slow (shallow clones + a pip install into a fresh venv,
a cargo build pulling ~90 crates, two go builds, a real Maven build - which
may itself first download and unzip a Maven distribution - an npm
install + tsc build, and a real MSVC cmake+ninja build of a ~190-target C++
static library) and requires network access plus a Python 3, a Rust/cargo,
a Go, a JDK/javac, a Node/npm toolchain, and (Windows only) the MSVC build
tools already installed at C:\\tools\\rp-toolchains per
C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

import foss_mcp.indexing.example_verifier as example_verifier
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    ExampleEnvironmentError,
    VerificationResult,
    prepare_cpp_library,
    prepare_go_library,
    prepare_java_library,
    prepare_python_library,
    prepare_rust_library,
    prepare_typescript_library,
    verify_cpp_example,
    verify_go_example,
    verify_java_example,
    verify_python_example,
    verify_rust_example,
    verify_typescript_example,
)

# --- slides/python -----------------------------------------------------
# Pinned exactly as indexed - matches tests/fixtures/slides_python/api_surface.json's
# own `source_commit`. Never "latest".
PYTHON_REPOSITORY = "aspose-slides-foss/Aspose.Slides-FOSS-for-Python"
PYTHON_COMMIT = "4e63447ba79d1c27a5192844847d9f872c5b92ad"

_PYTHON_WORKING_CODE = """\
import aspose.slides_foss as slides
from aspose.slides_foss.export import SaveFormat

with slides.Presentation() as prs:
    slide = prs.slides[0]
    assert len(prs.slides) == 1
    prs.save("output.pptx", SaveFormat.PPTX)
"""

_PYTHON_BROKEN_CODE = """\
import aspose.slides_foss as slides

with slides.Presentation() as prs:
    slide = prs.slides[0]
    undefined_function_call_that_does_not_exist()
"""

# --- cells/rust ----------------------------------------------------------
# Pinned exactly as indexed. This repo's default branch is `master`, not
# `main` - irrelevant once pinned to the exact commit below.
RUST_REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Rust"
RUST_COMMIT = "1a6004af47b1ef15385f9d36d381a8172428cc7e"
RUST_CRATE_NAME = "aspose-cells-foss-rust"

_RUST_WORKING_CODE = """\
use aspose_cells_foss_rust::Workbook;

fn main() {
    let mut workbook = Workbook::new();
    {
        let mut worksheets = workbook.get_worksheets_mut();
        let sheet = worksheets.get(0).expect("sheet 0 exists");
        let mut cells = sheet.get_cells_mut();
        cells
            .get("A1")
            .expect("A1 cell")
            .put_value_string("Hello")
            .expect("set value");
    }
    workbook.save("output.xlsx").expect("save workbook");
}
"""

_RUST_BROKEN_CODE = """\
use aspose_cells_foss_rust::Workbook;

fn main() {
    let mut workbook = Workbook::new();
    workbook.frobnicate_nonexistent_method_that_does_not_exist();
}
"""

# --- pdf/go ----------------------------------------------------------------
# Pinned exactly as indexed. Note the dash in the repo name, not a dot.
GO_REPOSITORY = "aspose-pdf-foss/Aspose-PDF-FOSS-for-Go"
GO_COMMIT = "286484d235196d65c9a458c5eff3d3d6539216dc"
GO_MODULE_PATH = "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"

_GO_WORKING_CODE = """\
package main

import (
\t"log"

\tpdf "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
)

func main() {
\tdoc := pdf.NewDocument(595, 842)
\tif err := doc.Save("output.pdf"); err != nil {
\t\tlog.Fatalf("save: %v", err)
\t}
}
"""

_GO_BROKEN_CODE = """\
package main

import (
\tpdf "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
)

func main() {
\tdoc := pdf.NewDocument(595, 842)
\tdoc.FrobnicateNonexistentMethodThatDoesNotExist()
}
"""


# --- pdf/java ------------------------------------------------------------
# Pinned exactly as indexed - matches tests/fixtures/pdf_java/api_surface.json's
# own `source_commit`.
JAVA_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-Java"
JAVA_COMMIT = "db2d3f0622f035825419c6d46727022064f39f15"

# Real furnished pdf/java content (tests/fixtures/furnished/pdf_java/pages/_index.md)
# is a bare, self-contained statement block already wrapped in a
# `try (Document doc = ...)` block - not a full compilable Java file with its
# own class declaration - so these deliberately omit a class declaration to
# exercise verify_java_example's real wrapping logic.
_JAVA_WORKING_CODE = """\
try (Document doc = new Document()) {
    Page page = doc.getPages().add();
    doc.save("output.pdf");
}
"""

_JAVA_BROKEN_CODE = """\
try (Document doc = new Document()) {
    Page page = doc.getPages().add();
    doc.frobnicateNonexistentMethodThatDoesNotExist();
    doc.save("output.pdf");
}
"""

# --- pdf/typescript --------------------------------------------------------
# Pinned exactly as indexed - matches tests/fixtures/pdf_typescript/api_surface.json's
# own `source_commit`.
TYPESCRIPT_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript"
TYPESCRIPT_COMMIT = "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"
TYPESCRIPT_PACKAGE_NAME = "@asposefoss/pdf"

# Document's constructor is private - real candidates must use the static
# factory Document.New(), confirmed by reading src/document.ts.
_TYPESCRIPT_WORKING_CODE = """\
import { Document, PageFormat } from '@asposefoss/pdf';

const doc = Document.New(PageFormat.A4);
doc.Pages[0].AddText('Hello', 72, 720, { fontSize: 14 });
doc.WriteTo('scratch.pdf');
"""

_TYPESCRIPT_BROKEN_CODE = """\
import { Document, PageFormat } from '@asposefoss/pdf';

const doc = Document.New(PageFormat.A4);
doc.frobnicateNonexistentMethodThatDoesNotExist();
"""


# --- cells/cpp -------------------------------------------------------------
# Pinned exactly as indexed - matches tests/fixtures/cells_cpp/api_surface.json's
# own `source_commit`.
CPP_REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp"
CPP_COMMIT = "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2"

# The real furnished cells/cpp content (tests/fixtures/furnished/cells_cpp/pages/_index.md)
# is a complete, self-contained snippet with its own #includes and int main() -
# unlike pdf/java's bare statement block. That real snippet is itself missing
# `#include "aspose/cells_foss/WorksheetCollection.h"` (Workbook.h only
# forward-declares WorksheetCollection, so operator[] is not visible from it
# alone) - a real gap this module's own real MSVC compile step is meant to
# catch, so it is added here deliberately for the KNOWN-WORKING candidate.
_CPP_WORKING_CODE = """\
#include "aspose/cells_foss/Workbook.h"
#include "aspose/cells_foss/WorksheetCollection.h"
#include "aspose/cells_foss/Worksheet.h"
#include "aspose/cells_foss/Cell.h"
#include "aspose/cells_foss/Style.h"
#include "aspose/cells_foss/Color.h"
#include "aspose/cells_foss/Font.h"
using namespace Aspose::Cells_FOSS;
int main() {
    Workbook workbook;
    Worksheet& sheet = workbook.GetWorksheets()[0];
    sheet.SetName("Products");
    sheet.GetCells()["A1"].PutValue("Product");
    sheet.GetCells()["B1"].PutValue("Price");
    sheet.GetCells()["A2"].PutValue("Apple");
    sheet.GetCells()["B2"].PutValue(2.99);
    sheet.GetCells()["B4"].SetFormula("=SUM(B2:B3)");
    Style headerStyle = sheet.GetCells()["A1"].GetStyle();
    Font font;
    font.SetBold(true);
    font.SetColor(Color::FromArgb(255, 255, 255, 255));
    headerStyle.SetFont(font);
    headerStyle.SetForegroundColor(Color::FromArgb(255, 34, 120, 212));
    sheet.GetCells()["A1"].SetStyle(headerStyle);
    workbook.Save("products.xlsx");
    return 0;
}
"""

_CPP_BROKEN_CODE = """\
#include "aspose/cells_foss/Workbook.h"
#include "aspose/cells_foss/WorksheetCollection.h"
#include "aspose/cells_foss/Worksheet.h"
#include "aspose/cells_foss/Cell.h"
using namespace Aspose::Cells_FOSS;
int main() {
    Workbook workbook;
    Worksheet& sheet = workbook.GetWorksheets()[0];
    sheet.GetCells()["A1"].PutValue("Hello");
    sheet.FrobnicateNonexistentMethodThatDoesNotExist();
    workbook.Save("output.xlsx");
    return 0;
}
"""


def _candidate(title: str, language: str, code: str) -> CandidateExample:
    return CandidateExample(title=title, description="", language=language, code=code)


# --- Python fixtures/tests -------------------------------------------------


@pytest.fixture(scope="module")
def python_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("slides_python_reference")
    return prepare_python_library(
        repository=PYTHON_REPOSITORY,
        commit=PYTHON_COMMIT,
        workdir=workdir,
    )


def test_prepare_python_library_installs_the_real_pinned_commit(python_venv: Path) -> None:
    assert python_venv.exists()
    assert python_venv.stem == "python"


def test_python_known_working_candidate_runs_clean(python_venv: Path, tmp_path: Path) -> None:
    candidate = _candidate("Create a presentation", "python", _PYTHON_WORKING_CODE)

    result = verify_python_example(candidate, venv_python=python_venv, workdir=tmp_path)

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_python_known_broken_candidate_fails_with_real_exception(python_venv: Path, tmp_path: Path) -> None:
    candidate = _candidate("Broken presentation", "python", _PYTHON_BROKEN_CODE)

    result = verify_python_example(candidate, venv_python=python_venv, workdir=tmp_path)

    assert result.verified is False
    assert "NameError" in result.output
    assert "undefined_function_call_that_does_not_exist" in result.output


# --- Rust fixtures/tests -----------------------------------------------


@pytest.fixture(scope="module")
def rust_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("cells_rust_reference")
    return prepare_rust_library(
        repository=RUST_REPOSITORY,
        commit=RUST_COMMIT,
        workdir=workdir,
    )


def test_prepare_rust_library_builds_the_real_pinned_commit(rust_library: Path) -> None:
    assert rust_library.exists()
    assert (rust_library / "Cargo.toml").exists()


def test_rust_known_working_candidate_builds_clean(rust_library: Path, tmp_path: Path) -> None:
    candidate = _candidate("Write a cell", "rust", _RUST_WORKING_CODE)

    result = verify_rust_example(
        candidate,
        library_crate_name=RUST_CRATE_NAME,
        library_dir=rust_library,
        workdir=tmp_path,
    )

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_rust_known_broken_candidate_fails_real_compile(rust_library: Path, tmp_path: Path) -> None:
    candidate = _candidate("Broken cell", "rust", _RUST_BROKEN_CODE)

    result = verify_rust_example(
        candidate,
        library_crate_name=RUST_CRATE_NAME,
        library_dir=rust_library,
        workdir=tmp_path,
    )

    assert result.verified is False
    assert "E0599" in result.output or "frobnicate_nonexistent_method_that_does_not_exist" in result.output


# --- Go fixtures/tests -------------------------------------------------


@pytest.fixture(scope="module")
def go_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("pdf_go_reference")
    return prepare_go_library(
        repository=GO_REPOSITORY,
        commit=GO_COMMIT,
        workdir=workdir,
    )


def test_prepare_go_library_builds_the_real_pinned_commit(go_library: Path) -> None:
    assert go_library.exists()
    assert (go_library / "go.mod").exists()


def test_go_known_working_candidate_builds_clean(go_library: Path, tmp_path: Path) -> None:
    candidate = _candidate("Create a blank document", "go", _GO_WORKING_CODE)

    result = verify_go_example(
        candidate,
        module_path=GO_MODULE_PATH,
        library_dir=go_library,
        workdir=tmp_path,
    )

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_go_known_broken_candidate_fails_real_compile(go_library: Path, tmp_path: Path) -> None:
    candidate = _candidate("Broken document", "go", _GO_BROKEN_CODE)

    result = verify_go_example(
        candidate,
        module_path=GO_MODULE_PATH,
        library_dir=go_library,
        workdir=tmp_path,
    )

    assert result.verified is False
    assert "FrobnicateNonexistentMethodThatDoesNotExist" in result.output


# Real, bare-fragment furnished content copied verbatim from the "Split and
# Merge PDFs" example in tests/fixtures/furnished/pdf_go/pages/_index.md -
# no package/import/func main of its own, the real shape TC-100's own live
# E2E run found every one of pdf/go's 4 real furnished candidates share.
# Before _wrap_go_fragment_if_needed existed, writing this verbatim as
# main.go failed identically with
# `main.go:1:1: expected 'package', found doc` - this is REQ-G2-048's own
# concrete proof the fix closes that real gap, not a synthetic unit test.
_GO_FRAGMENT_WORKING_CODE = """\
doc, _ := pdf.Open("input.pdf")
pages, _ := doc.Split()
for i, p := range pages {
    p.Save(fmt.Sprintf("page%03d.pdf", i+1))
}
doc2, _ := pdf.Open("file2.pdf")
doc.Append(doc2)
doc.Save("merged.pdf")
"""

_GO_FRAGMENT_BROKEN_CODE = """\
doc, _ := pdf.Open("input.pdf")
pages, _ := doc.Split()
for i, p := range pages {
    p.Save(fmt.Sprintf("page%03d.pdf", i+1))
}
doc.FrobnicateNonexistentMethodThatDoesNotExist()
"""


def test_go_real_furnished_fragment_candidate_verifies_true(go_library: Path, tmp_path: Path) -> None:
    """pdf/go's own real furnished-content candidate (a bare statement
    fragment with no package/import/func main of its own) now genuinely
    compiles once wrapped, against the real pinned reference module.
    """
    candidate = _candidate("Split and Merge PDFs", "go", _GO_FRAGMENT_WORKING_CODE)

    result = verify_go_example(
        candidate,
        module_path=GO_MODULE_PATH,
        library_dir=go_library,
        workdir=tmp_path,
    )

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_go_real_furnished_fragment_broken_variant_fails_real_compile(
    go_library: Path, tmp_path: Path
) -> None:
    candidate = _candidate("Split and Merge PDFs (broken)", "go", _GO_FRAGMENT_BROKEN_CODE)

    result = verify_go_example(
        candidate,
        module_path=GO_MODULE_PATH,
        library_dir=go_library,
        workdir=tmp_path,
    )

    assert result.verified is False
    assert "FrobnicateNonexistentMethodThatDoesNotExist" in result.output


# --- Java fixtures/tests ------------------------------------------------


@pytest.fixture(scope="module")
def java_jar(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("pdf_java_reference")
    return prepare_java_library(
        repository=JAVA_REPOSITORY,
        commit=JAVA_COMMIT,
        workdir=workdir,
    )


def test_prepare_java_library_builds_the_real_pinned_commit(java_jar: Path) -> None:
    assert java_jar.exists()
    assert java_jar.suffix == ".jar"
    assert not java_jar.name.endswith("-sources.jar")
    assert not java_jar.name.endswith("-javadoc.jar")


def test_java_known_working_candidate_compiles_clean(java_jar: Path, tmp_path: Path) -> None:
    candidate = _candidate("Create a document", "java", _JAVA_WORKING_CODE)

    result = verify_java_example(candidate, library_jar=java_jar, workdir=tmp_path)

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_java_known_broken_candidate_fails_real_compile(java_jar: Path, tmp_path: Path) -> None:
    candidate = _candidate("Broken document", "java", _JAVA_BROKEN_CODE)

    result = verify_java_example(candidate, library_jar=java_jar, workdir=tmp_path)

    assert result.verified is False
    assert "frobnicateNonexistentMethodThatDoesNotExist" in result.output


# --- TypeScript fixtures/tests -------------------------------------------


@pytest.fixture(scope="module")
def typescript_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("pdf_typescript_reference")
    return prepare_typescript_library(
        repository=TYPESCRIPT_REPOSITORY,
        commit=TYPESCRIPT_COMMIT,
        workdir=workdir,
    )


def test_prepare_typescript_library_builds_the_real_pinned_commit(typescript_library: Path) -> None:
    assert typescript_library.exists()
    assert (typescript_library / "package.json").exists()
    dist_dir = typescript_library / "dist"
    assert dist_dir.is_dir()
    assert any(dist_dir.iterdir())


def test_typescript_known_working_candidate_type_checks_clean(
    typescript_library: Path, tmp_path: Path
) -> None:
    candidate = _candidate("Create a new PDF from scratch", "typescript", _TYPESCRIPT_WORKING_CODE)

    result = verify_typescript_example(
        candidate,
        library_dir=typescript_library,
        package_name=TYPESCRIPT_PACKAGE_NAME,
        workdir=tmp_path,
    )

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_typescript_known_broken_candidate_fails_real_type_check(
    typescript_library: Path, tmp_path: Path
) -> None:
    candidate = _candidate("Broken PDF creation", "typescript", _TYPESCRIPT_BROKEN_CODE)

    result = verify_typescript_example(
        candidate,
        library_dir=typescript_library,
        package_name=TYPESCRIPT_PACKAGE_NAME,
        workdir=tmp_path,
    )

    assert result.verified is False
    assert "TS2339" in result.output or "frobnicateNonexistentMethodThatDoesNotExist" in result.output


# --- C++ fixtures/tests -----------------------------------------------


@pytest.fixture(scope="module")
def cpp_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("cells_cpp_reference")
    return prepare_cpp_library(
        repository=CPP_REPOSITORY,
        commit=CPP_COMMIT,
        workdir=workdir,
    )


@pytest.fixture(scope="module")
def cpp_include_dir(cpp_library: Path) -> Path:
    return cpp_library / "Aspose.Cells.Foss.Cpp" / "include"


def test_prepare_cpp_library_builds_the_real_pinned_commit(cpp_library: Path) -> None:
    assert cpp_library.exists()
    library_artifact = cpp_library / "Aspose.Cells.Foss.Cpp" / "_build" / "aspose_cells_foss.lib"
    assert library_artifact.exists()


def test_cpp_known_working_candidate_builds_clean(
    cpp_library: Path, cpp_include_dir: Path, tmp_path: Path
) -> None:
    candidate = _candidate("Create a styled workbook", "cpp", _CPP_WORKING_CODE)

    result = verify_cpp_example(
        candidate,
        library_dir=cpp_library,
        include_dir=cpp_include_dir,
        workdir=tmp_path,
    )

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is True, result.output


def test_cpp_known_broken_candidate_fails_real_compile(
    cpp_library: Path, cpp_include_dir: Path, tmp_path: Path
) -> None:
    candidate = _candidate("Broken worksheet", "cpp", _CPP_BROKEN_CODE)

    result = verify_cpp_example(
        candidate,
        library_dir=cpp_library,
        include_dir=cpp_include_dir,
        workdir=tmp_path,
    )

    assert result.verified is False
    assert "C2039" in result.output or "FrobnicateNonexistentMethodThatDoesNotExist" in result.output


def test_dataclasses_replace_still_works_on_candidate_example(python_venv: Path, tmp_path: Path) -> None:
    """Sanity check mirroring the pdf/net suite's own use of
    dataclasses.replace to build a corrected variant from a real
    candidate, kept here so a future change to CandidateExample's shape
    is caught by every language's suite, not just pdf/net's.
    """
    candidate = _candidate("Broken presentation", "python", _PYTHON_BROKEN_CODE)
    corrected_code = _PYTHON_WORKING_CODE
    corrected_candidate = dataclasses.replace(candidate, code=corrected_code)

    result = verify_python_example(corrected_candidate, venv_python=python_venv, workdir=tmp_path)

    assert result.verified is True, result.output


# --- cargo offline fallback (REQ-G2-043) -------------------------------------
#
# These four tests mock subprocess.run, so they need no network, no cargo and
# no real reference crate. They drive verify_rust_example, which runs every
# cargo command through _run_cargo, and record each argv that was started.

_NETWORK_FAILURE_OUTPUT = (
    "    Updating crates.io index\n"
    "warning: spurious network error (4 tries remaining): failed to get `serde`\n"
    "error: failed to get `serde` as a dependency\n"
)
_RUSTC_E0308_OUTPUT = (
    "   Compiling candidate v0.0.0\n"
    "error[E0308]: mismatched types\n"
    " --> src/main.rs:2:18\n"
    "error: could not compile `candidate` (bin \"candidate\") due to 1 previous error\n"
)
_RUST_CANDIDATE = _candidate("Cargo fallback", "rust", "fn main() {}\n")


def _script_cargo_runs(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[tuple[int, str]],
) -> list[list[str]]:
    """Replace subprocess.run with a script of (returncode, output) outcomes, one
    per call, and return the list that records each argv in call order.
    """
    calls: list[list[str]] = []
    remaining = list(outcomes)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        returncode, output = remaining.pop(0)
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=output)

    monkeypatch.setattr(example_verifier.subprocess, "run", fake_run)
    return calls


def test_cargo_network_failure_then_offline_success_returns_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_cargo_runs(monkeypatch, [(101, _NETWORK_FAILURE_OUTPUT), (0, "Finished")])

    result = verify_rust_example(
        _RUST_CANDIDATE,
        library_crate_name="reference",
        library_dir=tmp_path,
        workdir=tmp_path / "work",
    )

    assert result.verified is True
    assert len(calls) == 2
    assert calls[0] == ["cargo", "build"]
    assert calls[1] == ["cargo", "build", "--offline"]


def test_cargo_network_failure_twice_still_raises_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_cargo_runs(
        monkeypatch,
        [(101, _NETWORK_FAILURE_OUTPUT), (101, _NETWORK_FAILURE_OUTPUT)],
    )

    with pytest.raises(ExampleEnvironmentError) as excinfo:
        verify_rust_example(
            _RUST_CANDIDATE,
            library_crate_name="reference",
            library_dir=tmp_path,
            workdir=tmp_path / "work",
        )

    assert excinfo.value.tool == "cargo"
    assert excinfo.value.marker == "spurious network error"
    assert len(calls) == 2
    assert calls[1] == ["cargo", "build", "--offline"]


def test_cargo_compiler_diagnostic_is_never_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_cargo_runs(monkeypatch, [(101, _RUSTC_E0308_OUTPUT)])

    result = verify_rust_example(
        _RUST_CANDIDATE,
        library_crate_name="reference",
        library_dir=tmp_path,
        workdir=tmp_path / "work",
    )

    assert result.verified is False
    assert len(calls) == 1
    assert calls[0] == ["cargo", "build"]


def test_cargo_first_run_success_makes_exactly_one_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_cargo_runs(monkeypatch, [(0, "Finished")])

    result = verify_rust_example(
        _RUST_CANDIDATE,
        library_crate_name="reference",
        library_dir=tmp_path,
        workdir=tmp_path / "work",
    )

    assert result.verified is True
    assert calls == [["cargo", "build"]]
