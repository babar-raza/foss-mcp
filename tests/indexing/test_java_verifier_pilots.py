"""Offline checks of the opt-in pilot mode of the Java verifier (REQ-G3-005, TC-360).

pdf/java compiles a bare statement block inside a wrapper that imports ``org.aspose.pdf``. The
slides, cells, sample and 3d pilots have other packages, README code blocks that start with their own
``import`` lines, and (sample) a compile-scope dependency. These tests pin both modes without running
javac or Maven: the one subprocess seam, ``_run``, and the Maven lookup are replaced by recorders,
and the pom.xml and jar files are synthetic files written into ``tmp_path``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

import foss_mcp.indexing.example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from infra import build_chunks

REPOSITORY = "owner/pilot-java"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

SLIDES_PACKAGE = "org.aspose.slides.foss"
SAMPLE_PACKAGE = "com.aspose.sample"
THREED_PACKAGE = "com.aspose.threed"

SLIDES_README_BLOCK = """\
import org.aspose.slides.foss.Presentation;
import org.aspose.slides.foss.SaveFormat;

try (Presentation presentation = new Presentation()) {
    presentation.save("out.pptx", SaveFormat.Pptx);
}
"""

SAMPLE_README_BLOCK = """\
import com.aspose.sample.Mapper;

Mapper mapper = new Mapper();
System.out.println(mapper);
"""

THREED_README_BLOCK = """\
// scene setup
import com.aspose.threed.Scene;
import static java.lang.Math.PI;

Scene scene = new Scene();
scene.save("out.stl");
"""

CELLS_WHOLE_CLASS = """\
import org.aspose.cells_foss.Workbook;

public class HelloCells {
    public static void main(String[] args) throws Exception {
        Workbook workbook = new Workbook();
    }
}
"""

PLAIN_POM = """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <dependencies>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>javax.servlet</groupId>
      <artifactId>servlet-api</artifactId>
      <scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>org.example</groupId>
      <artifactId>nice-to-have</artifactId>
      <optional>true</optional>
    </dependency>
  </dependencies>
</project>
"""

COMPILE_POM = """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>2.17.0</version>
    </dependency>
  </dependencies>
</project>
"""

MANAGEMENT_ONLY_POM = """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
        <version>2.17.0</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
"""

NO_NAMESPACE_RUNTIME_POM = """\
<project>
  <dependencies>
    <dependency>
      <groupId>org.example</groupId>
      <artifactId>runtime-only</artifactId>
      <scope>runtime</scope>
    </dependency>
  </dependencies>
</project>
"""


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="Example", description="", language="java", code=code)


class _FakeToolchain:
    """Stands in for ``_run``: no process is started. Every argv that starts with the version
    control program succeeds (and leaves the synthetic pom.xml in its working directory); the Maven
    package step leaves the jars; the dependency step leaves dependency jars; javac is recorded.
    """

    def __init__(
        self,
        pom_text: str | None = None,
        dependency_jars: tuple[str, ...] = ("zeta-1.0.jar", "alpha-2.0.jar"),
        copy_returncode: int = 0,
        package_returncode: int = 0,
    ) -> None:
        self.pom_text = pom_text
        self.dependency_jars = dependency_jars
        self.copy_returncode = copy_returncode
        self.package_returncode = package_returncode
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[0] == "git":
            if self.pom_text is not None:
                (cwd / "pom.xml").write_text(self.pom_text, encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[0] == "fake-mvn" and args[1] == "package":
            if self.package_returncode == 0:
                target = cwd / "target"
                target.mkdir(parents=True, exist_ok=True)
                (target / "pilot-1.0.jar").write_bytes(b"jar")
                (target / "pilot-1.0-sources.jar").write_bytes(b"jar")
            return subprocess.CompletedProcess(args, self.package_returncode, stdout="", stderr="build log")
        if args[0] == "fake-mvn" and args[1] == "dependency:copy-dependencies":
            if self.copy_returncode == 0:
                deps = cwd / "target" / "foss-mcp-deps"
                deps.mkdir(parents=True, exist_ok=True)
                for name in self.dependency_jars:
                    (deps / name).write_bytes(b"jar")
            return subprocess.CompletedProcess(args, self.copy_returncode, stdout="", stderr="copy log")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    @property
    def maven_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "fake-mvn"]

    @property
    def javac_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "javac"]


@pytest.fixture
def toolchain(monkeypatch: pytest.MonkeyPatch) -> _FakeToolchain:
    fake = _FakeToolchain()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))
    return fake


def _written_source(workdir: Path, class_name: str = "Candidate") -> str:
    return (workdir / "candidate_project" / f"{class_name}.java").read_text(encoding="utf-8")


# --- _hoist_java_imports ---------------------------------------------------------------------


def test_hoist_returns_the_leading_imports_and_the_remaining_body() -> None:
    imports, body = ev._hoist_java_imports(SLIDES_README_BLOCK)

    assert imports == (
        "import org.aspose.slides.foss.Presentation;",
        "import org.aspose.slides.foss.SaveFormat;",
    )
    assert body.startswith("try (Presentation presentation = new Presentation()) {")
    assert "import" not in body


def test_hoist_allows_blank_lines_comments_and_import_static_between_imports() -> None:
    code = (
        "\n"
        "// scene setup\n"
        "import com.aspose.threed.Scene;\n"
        "\n"
        "/* block\n"
        "   comment import fake.Thing; */\n"
        "import static java.lang.Math.PI;\n"
        "\n"
        "Scene scene = new Scene();\n"
    )

    imports, body = ev._hoist_java_imports(code)

    assert imports == ("import com.aspose.threed.Scene;", "import static java.lang.Math.PI;")
    assert "fake.Thing" in body  # the comment text is not an import and stays in the body
    assert body.rstrip().endswith("Scene scene = new Scene();")
    assert "import com.aspose" not in body
    assert "import static" not in body


def test_hoist_leaves_an_import_word_after_the_first_statement_alone() -> None:
    code = "int x = 1;\nimport late.Thing;\n"

    imports, body = ev._hoist_java_imports(code)

    assert imports == ()
    assert body == code.rstrip("\n")


def test_hoist_of_code_without_imports_returns_the_code_unchanged() -> None:
    imports, body = ev._hoist_java_imports("Foo foo = new Foo();\nfoo.run();")

    assert imports == ()
    assert body == "Foo foo = new Foo();\nfoo.run();"


# --- _java_wrapper_source --------------------------------------------------------------------


def test_three_argument_wrapper_is_identical_to_the_text_it_produced_before_pilot_mode() -> None:
    literal = (
        "import org.aspose.pdf.*;\n"
        "import org.aspose.pdf.forms.Form;\n"
        "\n"
        "public class Candidate {\n"
        "    public static void main(String[] args) throws Exception {\n"
        "        Document doc = new Document();\n"
        "\n"
        '        doc.save("a.pdf");\n'
        "    }\n"
        "}\n"
    )

    source = ev._java_wrapper_source(
        'Document doc = new Document();\n\ndoc.save("a.pdf");',
        "Candidate",
        ("import org.aspose.pdf.forms.Form;",),
    )

    assert source == literal


def test_three_argument_wrapper_with_no_page_imports_starts_with_the_pdf_wildcard() -> None:
    source = ev._java_wrapper_source("int x = 1;", "Candidate", ())

    assert source.startswith("import org.aspose.pdf.*;\n\npublic class Candidate {")


def test_slides_shape_gets_the_package_wildcard_java_imports_and_hoisted_imports_above_the_class() -> None:
    source = ev._java_wrapper_source(SLIDES_README_BLOCK, "Candidate", (), base_packages=(SLIDES_PACKAGE,))

    assert source.splitlines()[:5] == [
        "import org.aspose.slides.foss.*;",
        "import java.io.*;",
        "import java.util.*;",
        "import org.aspose.slides.foss.Presentation;",
        "import org.aspose.slides.foss.SaveFormat;",
    ]
    main_body = source[source.index("public static void main") :]
    assert "import " not in main_body
    assert "        try (Presentation presentation = new Presentation()) {" in main_body
    assert source.index("public class Candidate") > source.index("import org.aspose.slides.foss.SaveFormat;")


def test_sample_shape_is_wrapped_with_the_sample_package() -> None:
    source = ev._java_wrapper_source(SAMPLE_README_BLOCK, "Candidate", (), base_packages=(SAMPLE_PACKAGE,))

    assert source.splitlines()[:4] == [
        "import com.aspose.sample.*;",
        "import java.io.*;",
        "import java.util.*;",
        "import com.aspose.sample.Mapper;",
    ]
    assert "        Mapper mapper = new Mapper();" in source
    assert "import " not in source[source.index("public static void main") :]


def test_threed_shape_keeps_import_static_and_drops_the_comment_above_the_imports_from_the_header() -> None:
    source = ev._java_wrapper_source(THREED_README_BLOCK, "Candidate", (), base_packages=(THREED_PACKAGE,))

    assert source.splitlines()[:5] == [
        "import com.aspose.threed.*;",
        "import java.io.*;",
        "import java.util.*;",
        "import com.aspose.threed.Scene;",
        "import static java.lang.Math.PI;",
    ]
    assert "        Scene scene = new Scene();" in source
    assert "import " not in source[source.index("public static void main") :]


def test_pilot_mode_ignores_page_imports_and_deduplicates_hoisted_imports() -> None:
    code = "import java.util.*;\nimport com.aspose.sample.Mapper;\nimport com.aspose.sample.Mapper;\nint x = 1;\n"

    source = ev._java_wrapper_source(
        code,
        "Candidate",
        ("import other.pkg.Mapper;",),
        base_packages=(SAMPLE_PACKAGE, "com.aspose.extra"),
    )

    assert "other.pkg" not in source
    assert source.count("import com.aspose.sample.Mapper;") == 1
    assert source.count("import java.util.*;") == 1
    assert source.splitlines()[:5] == [
        "import com.aspose.sample.*;",
        "import com.aspose.extra.*;",
        "import java.io.*;",
        "import java.util.*;",
        "import com.aspose.sample.Mapper;",
    ]


# --- verify_java_example ---------------------------------------------------------------------


def test_pilot_mode_verify_writes_the_wrapped_source_and_calls_javac(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    jar = tmp_path / "lib.jar"

    result = ev.verify_java_example(
        _candidate(SLIDES_README_BLOCK),
        library_jar=jar,
        workdir=tmp_path,
        page_imports=("import wrong.page.Import;",),
        java_packages=(SLIDES_PACKAGE,),
    )

    source = _written_source(tmp_path)
    assert source.startswith("import org.aspose.slides.foss.*;\nimport java.io.*;\nimport java.util.*;\n")
    assert "wrong.page" not in source
    assert result.verified is True
    assert toolchain.javac_calls == [["javac", "-cp", str(jar), "Candidate.java"]]


def test_a_whole_class_block_is_compiled_as_is_under_its_own_name_in_pilot_mode(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    ev.verify_java_example(
        _candidate(CELLS_WHOLE_CLASS),
        library_jar=tmp_path / "lib.jar",
        workdir=tmp_path,
        java_packages=("org.aspose.cells_foss",),
    )

    assert _written_source(tmp_path, "HelloCells") == CELLS_WHOLE_CLASS
    assert not (tmp_path / "candidate_project" / "Candidate.java").exists()
    assert toolchain.javac_calls[0][-1] == "HelloCells.java"


def test_a_whole_class_block_is_compiled_as_is_without_pilot_mode_too(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    ev.verify_java_example(_candidate(CELLS_WHOLE_CLASS), library_jar=tmp_path / "lib.jar", workdir=tmp_path)

    assert _written_source(tmp_path, "HelloCells") == CELLS_WHOLE_CLASS


def test_a_failing_compile_is_reported_as_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="error: cannot find symbol")

    monkeypatch.setattr(ev, "_run", failing_run)

    result = ev.verify_java_example(
        _candidate(SAMPLE_README_BLOCK),
        library_jar=tmp_path / "lib.jar",
        workdir=tmp_path,
        java_packages=(SAMPLE_PACKAGE,),
    )

    assert result.verified is False
    assert "cannot find symbol" in result.output


def test_default_mode_javac_argv_and_source_are_unchanged_for_pdf_java(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    jar = tmp_path / "reference.jar"

    ev.verify_java_example(
        _candidate("Document doc = new Document();"),
        library_jar=jar,
        workdir=tmp_path,
        page_imports=("import org.aspose.pdf.forms.Form;",),
    )

    assert toolchain.javac_calls == [["javac", "-cp", str(jar), "Candidate.java"]]
    assert _written_source(tmp_path) == ev._java_wrapper_source(
        "Document doc = new Document();", "Candidate", ("import org.aspose.pdf.forms.Form;",)
    )


def test_classpath_is_the_jar_alone_when_there_is_no_dependency_directory(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    jar = target / "lib.jar"

    ev.verify_java_example(_candidate("int x = 1;"), library_jar=jar, workdir=tmp_path)

    assert toolchain.javac_calls[0][2] == str(jar)


def test_classpath_is_the_jar_then_every_dependency_jar_in_sorted_order(
    toolchain: _FakeToolchain, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    deps = target / "foss-mcp-deps"
    deps.mkdir(parents=True)
    for name in ("zeta-1.0.jar", "alpha-2.0.jar", "mid-3.0.jar"):
        (deps / name).write_bytes(b"jar")
    (deps / "notes.txt").write_text("not a jar", encoding="utf-8")
    jar = target / "lib.jar"

    ev.verify_java_example(_candidate("int x = 1;"), library_jar=jar, workdir=tmp_path)

    assert toolchain.javac_calls[0][2] == os.pathsep.join(
        [str(jar), str(deps / "alpha-2.0.jar"), str(deps / "mid-3.0.jar"), str(deps / "zeta-1.0.jar")]
    )


# --- prepare_java_library --------------------------------------------------------------------


def test_pom_with_only_test_provided_and_optional_dependencies_issues_no_dependency_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=PLAIN_POM)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    jar = ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert fake.maven_calls == [["fake-mvn", "package", "-Dmaven.test.skip=true"]]
    assert jar == tmp_path / "work" / "repo" / "target" / "pilot-1.0.jar"
    assert not (jar.parent / "foss-mcp-deps").exists()
    assert all(call[0] in ("git", "fake-mvn") for call in fake.calls)


def test_pom_with_a_compile_scope_dependency_issues_the_copy_step_after_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=COMPILE_POM)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    jar = ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert fake.maven_calls == [
        ["fake-mvn", "package", "-Dmaven.test.skip=true"],
        [
            "fake-mvn",
            "dependency:copy-dependencies",
            "-DincludeScope=runtime",
            "-DoutputDirectory=target/foss-mcp-deps",
        ],
    ]
    assert (jar.parent / "foss-mcp-deps" / "alpha-2.0.jar").exists()


def test_javac_gets_the_jar_plus_the_copied_dependency_jars_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=COMPILE_POM)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))
    jar = ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    ev.verify_java_example(
        _candidate(SAMPLE_README_BLOCK),
        library_jar=jar,
        workdir=tmp_path / "candidate",
        java_packages=(SAMPLE_PACKAGE,),
    )

    deps = jar.parent / "foss-mcp-deps"
    assert fake.javac_calls == [
        [
            "javac",
            "-cp",
            os.pathsep.join([str(jar), str(deps / "alpha-2.0.jar"), str(deps / "zeta-1.0.jar")]),
            "Candidate.java",
        ]
    ]


@pytest.mark.parametrize(
    ("pom_text", "expects_copy_step"),
    [
        (MANAGEMENT_ONLY_POM, False),
        (NO_NAMESPACE_RUNTIME_POM, True),
        (PLAIN_POM, False),
        (COMPILE_POM, True),
    ],
)
def test_dependency_step_depends_only_on_project_level_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pom_text: str, expects_copy_step: bool
) -> None:
    fake = _FakeToolchain(pom_text=pom_text)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    issued = any(call[1] == "dependency:copy-dependencies" for call in fake.maven_calls)
    assert issued is expects_copy_step


def test_a_failing_dependency_copy_step_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=COMPILE_POM, copy_returncode=1)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    with pytest.raises(RuntimeError, match="could not be copied"):
        ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_a_failing_package_build_still_raises_runtime_error_and_issues_no_dependency_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=COMPILE_POM, package_returncode=1)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    with pytest.raises(RuntimeError, match="failed to build cleanly"):
        ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert len(fake.maven_calls) == 1


def test_a_repository_without_a_root_pom_raises_runtime_error_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeToolchain(pom_text=None)
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_or_download_maven", lambda workdir: Path("fake-mvn"))

    with pytest.raises(RuntimeError, match="only root Maven projects are supported") as excinfo:
        ev.prepare_java_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert REPOSITORY in str(excinfo.value)
    assert fake.maven_calls == []


# --- infra/build_chunks.py ---------------------------------------------------------------------


def test_java_verify_kwargs_without_the_flag_is_exactly_today_s_dict() -> None:
    prepared = Path("workdir") / "reference.jar"

    kwargs = build_chunks._PLATFORM_DISPATCH["java"]["verify_kwargs"](prepared, argparse.Namespace())

    assert kwargs == {"library_jar": prepared, "page_imports": ()}
    assert "java_packages" not in kwargs


def test_java_verify_kwargs_with_the_flag_adds_java_packages_as_a_tuple() -> None:
    prepared = Path("workdir") / "reference.jar"
    args = argparse.Namespace(library_java_packages=(SLIDES_PACKAGE, "org.aspose.extra"))

    kwargs = build_chunks._PLATFORM_DISPATCH["java"]["verify_kwargs"](prepared, args)

    assert kwargs == {
        "library_jar": prepared,
        "page_imports": (),
        "java_packages": (SLIDES_PACKAGE, "org.aspose.extra"),
    }


def test_java_verify_kwargs_with_an_unset_flag_adds_nothing() -> None:
    args = argparse.Namespace(library_java_packages=None)

    kwargs = build_chunks._PLATFORM_DISPATCH["java"]["verify_kwargs"](Path("x.jar"), args)

    assert "java_packages" not in kwargs


def test_the_flag_is_not_a_required_platform_flag() -> None:
    assert "java" not in build_chunks._PLATFORM_REQUIRED_FLAGS
    assert all(attr != "library_java_packages" for attr, _ in build_chunks._PLATFORM_REQUIRED_FLAGS.values())


def test_java_packages_argument_splits_a_comma_separated_list_in_order() -> None:
    assert build_chunks._java_packages_argument("org.aspose.slides.foss, com.aspose.sample") == (
        "org.aspose.slides.foss",
        "com.aspose.sample",
    )


@pytest.mark.parametrize(
    "bad", ["org..aspose", "1org.aspose", "org.aspose.", "org aspose", "org-aspose", ",", ""]
)
def test_java_packages_argument_rejects_an_invalid_name(bad: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        build_chunks._java_packages_argument(bad)


def test_an_invalid_package_name_on_the_command_line_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "chunks.json"
    old_argv = sys.argv
    sys.argv = [
        "build_chunks.py",
        "--api-surface",
        "tests/fixtures/pdf_net/api_surface.json",
        "--title",
        "Pilot",
        "--out",
        str(out_path),
        "--library-java-packages",
        "org.aspose..bad",
    ]
    try:
        with pytest.raises(SystemExit) as excinfo:
            build_chunks.main()
    finally:
        sys.argv = old_argv

    assert excinfo.value.code == 2
    assert "org.aspose..bad" in capsys.readouterr().err
    assert not out_path.exists()
