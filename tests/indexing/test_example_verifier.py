"""Tests for src/foss_mcp/indexing/example_verifier.py (REQ-G2-048).

Real network, real git clone, real toolchain builds/execution - nothing
here is mocked: the whole point of this card is that a candidate example's
verdict comes from a real compiler or interpreter, not from a guess about
what it should do.

This is genuinely slow (shallow clones + a pip install into a fresh venv,
a cargo build pulling ~90 crates, two go builds, a real Maven build - which
may itself first download and unzip a Maven distribution - and an npm
install + tsc build) and requires network access plus a Python 3, a
Rust/cargo, a Go, a JDK/javac, and a Node/npm toolchain.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    VerificationResult,
    prepare_go_library,
    prepare_java_library,
    prepare_python_library,
    prepare_rust_library,
    prepare_typescript_library,
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
