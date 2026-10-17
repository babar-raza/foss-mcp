"""Offline tests for TC-451: the Go verifier assembles README blocks the way a reader would.

A README Go block is rarely a complete file. The verifier builds ``main.go`` from the block, the
page's own import lines and the library's real package names, never wraps a block that already has
``func main``, rejects a block that is only a package clause and imports, runs ``go mod tidy`` only
when the build asks for it, and blanks unused variables only for a wrapped fragment whose every
compile error is an unused variable. Every assembly step is the first line of the output.

Nothing here runs a real toolchain: ``_run`` is replaced by a scripted fake with the real
``(args, *, cwd)`` signature that answers per argv in order, records each argv and snapshots
``main.go`` at each build. The cells pages are the real committed furnished page; the page with an UNALIASED own
import (which no committed page has any more, re-pinned by TC-551) is a small synthetic page with the same block shapes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample, extract_candidate_examples

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests/fixtures/furnished"

CELLS_MODULE = "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26"
CELLS_IMPORT = f"{CELLS_MODULE}/aspose/cells_foss"
SAMPLE_MODULE = "example.org/sample-go"

# A synthetic furnished page with the block shapes the removed real page had: a quick start whose own import is
# UNALIASED (``import "<module>"``, which the verifier must remove and re-emit from the package name), a fragment
# with no import (taken from the page), and a fragment that uses undefined names of the page's package.
SAMPLE_PAGE: dict[str, object] = {
    "single": {
        "block": [
            {
                "title": "Quick Start",
                "content": (
                    "```go\n"
                    'import "example.org/sample-go"\n\n'
                    "options := sample.ClientOptions{\n"
                    '    SessionURL: "https://sample.example.test/.well-known/session",\n'
                    '    Username:   "user@example.test",\n'
                    '    Password:   "secret",\n'
                    "}\n"
                    "client := sample.NewClient(options)\n"
                    "session, err := client.Connect()\n"
                    "```\n"
                ),
            },
            {
                "title": "Bearer token authentication",
                "content": (
                    "Authenticate with a bearer token by setting `BearerToken`:\n\n"
                    "```go\n"
                    "options := sample.ClientOptions{\n"
                    '    SessionURL:  "https://sample.example.test/.well-known/session",\n'
                    '    BearerToken: "access-token",\n'
                    "}\n"
                    "client := sample.NewClient(options)\n"
                    "session, err := client.Connect()\n"
                    "```\n"
                ),
            },
            {
                "title": "Bearer token authentication (2)",
                "content": (
                    "Several calls can be batched into one round trip:\n\n"
                    "```go\n"
                    'query := sample.Invocation{Name: "Item/query", Arguments: map[string]interface{}{"accountId": accountId}}\n'
                    "get := sample.Invocation{\n"
                    '    Name: "Item/get",\n'
                    "    Arguments: map[string]interface{}{\n"
                    '        "accountId": accountId,\n'
                    '        "#ids":      sample.ResultReference{ResultOf: "c1", Name: "Item/query", Path: "/ids"},\n'
                    "    },\n"
                    "}\n"
                    'resp, err := client.SendRequest([]sample.Invocation{query, get}, []string{"urn:example:items"})\n'
                    "```\n"
                ),
            },
        ]
    }
}

BUILD = ["go", "build", "./..."]
VET = ["go", "vet", "./..."]
TIDY = ["go", "mod", "tidy"]


def _candidates(pilot: str) -> list[CandidateExample]:
    page = build_chunks._load_furnished_page(FURNISHED / pilot / "pages" / "_index.md")
    return extract_candidate_examples(page)


def _by_title(candidates: list[CandidateExample], title: str) -> CandidateExample:
    matches = [candidate for candidate in candidates if candidate.title == title]
    assert len(matches) == 1, f"expected exactly one candidate titled {title!r}"
    return matches[0]


def _cells(title: str) -> CandidateExample:
    return _by_title(_candidates("cells_go"), title)


def _sample(title: str) -> CandidateExample:
    return _by_title(extract_candidate_examples(SAMPLE_PAGE), title)


def _completed(
    args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class ScriptedRun:
    """A replacement for ``example_verifier._run`` answering each argv from an ordered script."""

    def __init__(self, script: dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]]) -> None:
        self.script = {key: list(results) for key, results in script.items()}
        self.calls: list[list[str]] = []
        self.main_go_at_build: list[str] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args == BUILD:
            self.main_go_at_build.append((cwd / "main.go").read_text(encoding="utf-8"))
        queue = self.script.get(tuple(args))
        if not queue:
            raise AssertionError(f"unscripted _run call: {args!r}")
        return queue.pop(0)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    script: dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]] | None = None,
) -> ScriptedRun:
    if script is None:
        script = {tuple(BUILD): [_completed(BUILD)], tuple(VET): [_completed(VET)]}
    fake = ScriptedRun(script)
    monkeypatch.setattr(ev, "_run", fake)
    return fake


def _library(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _cells_library(tmp_path: Path) -> Path:
    return _library(tmp_path, {"aspose/cells_foss/x.go": "package cells_foss\n\nfunc NewWorkbook() {}\n"})


def _sample_library(tmp_path: Path) -> Path:
    return _library(tmp_path, {"sample.go": "package sample\n\ntype ClientOptions struct{}\n"})


def _verify(
    candidate: CandidateExample, module_path: str, library_dir: Path, tmp_path: Path
) -> ev.VerificationResult:
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    return ev.verify_go_example(candidate, module_path=module_path, library_dir=library_dir, workdir=workdir)


def _assembly_line(result: ev.VerificationResult) -> str:
    return result.output.split("\n", 1)[0]


# --- rejection and complete files -------------------------------------------------------------


def test_import_only_block_is_rejected_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, {})
    candidate = _cells("Installation")

    result = _verify(candidate, CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert result.output == ev._NO_STATEMENTS_MESSAGE.format(kind="package and import declarations")
    assert fake.calls == []
    assert result.candidate is candidate


def test_package_and_import_only_file_is_rejected_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, {})
    candidate = CandidateExample(
        title="Empty", description="", language="go", code='package main\n\nimport (\n\t"fmt"\n)\n'
    )

    result = _verify(candidate, CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert "no statements" in result.output
    assert fake.calls == []


def test_complete_file_is_written_byte_for_byte_as_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch)
    candidate = _cells("Quick Start")

    result = _verify(candidate, CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    assert fake.main_go_at_build == [candidate.code]
    assert fake.calls == [BUILD, VET]
    assert _assembly_line(result) == (
        "[foss-mcp] assembly: go mode=as-given; imports=none; blank-uses=none; go-mod-tidy=not-run"
    )
    assert result.candidate is candidate


# --- fragments assembled from the page's imports ----------------------------------------------


def test_fragment_gets_the_page_import_and_stdlib_and_one_func_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch)
    candidate = _cells("Quick Start (2)")
    assert "import" not in candidate.code

    result = _verify(candidate, CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    (main_go,) = fake.main_go_at_build
    assert main_go.startswith("package main\n\nimport (\n")
    assert '\t"fmt"\n' in main_go
    assert f'\tcells_foss "{CELLS_IMPORT}"\n' in main_go
    assert main_go.count("func main(") == 1
    assert "\twb, _ := cells_foss.LoadWorkbook" in main_go
    assert _assembly_line(result) == (
        f"[foss-mcp] assembly: go mode=wrapped-fragment; imports=fmt=fmt,cells_foss={CELLS_IMPORT}; "
        "blank-uses=none; go-mod-tidy=not-run"
    )


def test_block_that_starts_with_func_main_is_not_wrapped_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch)
    candidate = _cells("Formulas and the Calculation Engine")
    assert candidate.code.startswith("func main() {")

    result = _verify(candidate, CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    (main_go,) = fake.main_go_at_build
    assert main_go.startswith("package main\n\nimport (\n")
    assert '\t"fmt"\n' in main_go
    assert f'\tcells_foss "{CELLS_IMPORT}"\n' in main_go
    assert main_go.count("func main(") == 1
    assert "\n\twb := cells_foss.NewWorkbook()" in main_go
    assert "mode=file-with-main" in _assembly_line(result)


def test_every_main_block_of_the_cells_page_yields_exactly_one_func_main() -> None:
    blocks = [c for c in _candidates("cells_go") if c.code.startswith("func main()")]
    assert len(blocks) >= 7
    for candidate in blocks:
        source = ev._wrap_go_fragment_if_needed(
            candidate.code,
            CELLS_MODULE,
            page_code=candidate.page_code,
            library_dir=None,
        )
        assert source.count("func main(") == 1, candidate.title
        assert source.startswith("package main\n"), candidate.title


def test_unaliased_own_import_is_removed_and_re_emitted_from_the_package_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch)
    candidate = _sample("Quick Start")
    assert candidate.code.startswith(f'import "{SAMPLE_MODULE}"')

    result = _verify(candidate, SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    (main_go,) = fake.main_go_at_build
    assert f'import "{SAMPLE_MODULE}"' not in main_go
    assert f'\tsample "{SAMPLE_MODULE}"\n' in main_go
    assert main_go.count("import") == 1
    assert main_go.count("func main(") == 1


def test_fragment_without_an_import_takes_it_from_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch)
    candidate = _sample("Bearer token authentication")
    assert "import" not in candidate.code

    result = _verify(candidate, SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    (main_go,) = fake.main_go_at_build
    assert f'\tsample "{SAMPLE_MODULE}"\n' in main_go
    assert "sample=" + SAMPLE_MODULE in _assembly_line(result)


def test_page_import_the_block_does_not_use_is_not_imported() -> None:
    page_code = (
        f'import cells_foss "{CELLS_IMPORT}"\n\nfunc main() {{}}',
        "x := 1\n_ = x",
    )

    source = ev._wrap_go_fragment_if_needed(
        "x := 1\n_ = x\n", CELLS_MODULE, page_code=page_code, library_dir=None
    )

    assert "cells_foss" not in source
    assert "import" not in source


def test_stdlib_os_and_strings_are_imported_when_used() -> None:
    source = ev._wrap_go_fragment_if_needed("fmt.Println(strings.ToUpper(os.Args[0]))\n", CELLS_MODULE)

    assert '\t"fmt"\n' in source
    assert '\t"os"\n' in source
    assert '\t"strings"\n' in source


def test_stdlib_table_maps_the_qualifiers_that_differ_from_their_path() -> None:
    assert ev._GO_STDLIB_QUALIFIERS["filepath"] == "path/filepath"
    assert ev._GO_STDLIB_QUALIFIERS["json"] == "encoding/json"
    assert ev._GO_STDLIB_QUALIFIERS["http"] == "net/http"
    source = ev._wrap_go_fragment_if_needed('_ = filepath.Join("a")\n', CELLS_MODULE)
    assert '\t"path/filepath"\n' in source


def test_legacy_pdf_fragment_still_gets_its_alias_against_an_empty_library(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    module = "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"

    source = ev._wrap_go_fragment_if_needed(
        'doc, _ := pdf.Open("a.pdf")\n_ = doc\n', module, library_dir=empty
    )

    assert f'\tpdf "{module}"\n' in source


def test_wrap_helper_still_works_with_two_positional_arguments() -> None:
    source = ev._wrap_go_fragment_if_needed('pdf.Open("a")\n', CELLS_MODULE)

    assert f'\tpdf "{CELLS_MODULE}"\n' in source
    assert source.startswith("package main\n\n")


def test_candidates_own_blank_and_dot_imports_are_kept() -> None:
    code = 'import _ "embed"\nimport . "strings"\nx := ToUpper("a")\n_ = x\n'

    source = ev._wrap_go_fragment_if_needed(code, CELLS_MODULE)

    assert '\t_ "embed"\n' in source
    assert '\t. "strings"\n' in source


def test_import_parsing_reads_single_and_grouped_declarations() -> None:
    code = (
        'package main\n\nimport "fmt"\nimport str "strings"\n\n'
        'import (\n\t"os"\n\tcf "example.com/lib/cells" // alias\n\t_ "embed"\n)\n\nfunc main() {}\n'
    )

    assert ev._go_import_specs(code) == [
        (None, "fmt"),
        ("str", "strings"),
        (None, "os"),
        ("cf", "example.com/lib/cells"),
        ("_", "embed"),
    ]
    stripped = ev._strip_go_preamble(code)
    assert stripped == "func main() {}"


def test_package_name_comes_from_the_first_sorted_non_test_file(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        {
            "sub/a_test.go": "package sub_test\n",
            "sub/b.go": "package realname\n",
            "sub/c.go": "package other\n",
        },
    )

    assert ev._go_package_name(library, "example.com/lib", "example.com/lib/sub") == "realname"
    assert ev._go_package_name(library, "example.com/lib", "example.com/elsewhere") is None
    assert ev._go_package_name(library, "example.com/lib", "example.com/lib/missing") is None


# --- unused-variable retries ------------------------------------------------------------------


def test_unused_variables_of_a_wrapped_fragment_are_blanked_and_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Quick Start")
    failing = _completed(
        BUILD,
        1,
        stderr=(
            "# candidate\n"
            "./main.go:14:1: declared and not used: session\n"
            "./main.go:14:10: declared and not used: err\n"
        ),
    )
    fake = _install(
        monkeypatch,
        {tuple(BUILD): [failing, _completed(BUILD)], tuple(VET): [_completed(VET)]},
    )

    result = _verify(candidate, SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    assert fake.calls == [BUILD, BUILD, VET]
    assert "_ = session" not in fake.main_go_at_build[0]
    final = fake.main_go_at_build[1]
    assert final.index("\t_ = session") < final.rindex("}")
    assert "\t_ = err" in final
    assert final.count("func main(") == 1
    assert "blank-uses=session,err" in _assembly_line(result)
    assert result.candidate is candidate


def test_older_unused_variable_wording_is_also_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = _completed(BUILD, 1, stderr="./main.go:3:2: client declared and not used\n")
    fake = _install(
        monkeypatch,
        {tuple(BUILD): [failing, _completed(BUILD)], tuple(VET): [_completed(VET)]},
    )

    result = _verify(_sample("Quick Start"), SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    assert "\t_ = client" in fake.main_go_at_build[1]


def test_retry_stops_after_the_maximum_number_of_rebuilds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert ev._GO_BLANK_USE_MAX_RETRIES == 2
    builds = [_completed(BUILD, 1, stderr=f"./main.go:5:1: declared and not used: v{i}\n") for i in range(5)]
    fake = _install(monkeypatch, {tuple(BUILD): builds})

    result = _verify(_sample("Quick Start"), SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD, BUILD, BUILD]
    assert "blank-uses=v0,v1" in _assembly_line(result)


def test_real_error_mixed_with_an_unused_variable_is_never_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Bearer token authentication (2)")
    failing = _completed(
        BUILD,
        1,
        stderr=(
            "# candidate\n./main.go:8:67: undefined: accountId\n./main.go:19:2: declared and not used: resp\n"
        ),
    )
    fake = _install(monkeypatch, {tuple(BUILD): [failing]})

    result = _verify(candidate, SAMPLE_MODULE, _sample_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD]
    assert "undefined: accountId" in result.output
    assert "blank-uses=none" in _assembly_line(result)
    assert result.candidate is candidate


def test_file_with_its_own_func_main_is_not_retried_for_unused_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = _completed(BUILD, 1, stderr="./main.go:9:2: declared and not used: ws\n")
    fake = _install(monkeypatch, {tuple(BUILD): [failing]})

    result = _verify(
        _cells("Formulas and the Calculation Engine"), CELLS_MODULE, _cells_library(tmp_path), tmp_path
    )

    assert result.verified is False
    assert fake.calls == [BUILD]
    assert "declared and not used: ws" in result.output


def test_complete_file_with_an_unused_variable_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = _completed(BUILD, 1, stderr="./main.go:9:2: declared and not used: wb\n")
    fake = _install(monkeypatch, {tuple(BUILD): [failing]})

    result = _verify(_cells("Quick Start"), CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD]


def test_failed_build_without_hints_runs_only_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = _completed(BUILD, 1, stderr="./main.go:4:5: undefined: generateSmallPNG\n")
    fake = _install(monkeypatch, {tuple(BUILD): [failing]})

    result = _verify(_cells("Picture Embedding"), CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD]
    assert "undefined: generateSmallPNG" in result.output


# --- go mod tidy ------------------------------------------------------------------------------


def test_build_asking_for_go_mod_tidy_runs_tidy_once_and_builds_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hint = _completed(BUILD, 1, stderr="go: updates to go.mod needed; to update it:\n\tgo mod tidy\n")
    fake = _install(
        monkeypatch,
        {
            tuple(BUILD): [hint, _completed(BUILD)],
            tuple(TIDY): [_completed(TIDY, 0, stderr="go: downloading something\n")],
            tuple(VET): [_completed(VET)],
        },
    )

    result = _verify(_cells("Quick Start"), CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is True, result.output
    assert fake.calls == [BUILD, TIDY, BUILD, VET]
    assert "go-mod-tidy=rc=0" in _assembly_line(result)
    assert "go: downloading something" in result.output


def test_failed_tidy_ends_the_verification_with_the_tidy_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hint = _completed(BUILD, 1, stderr="go: missing go.sum entry for module providing package x\n")
    fake = _install(
        monkeypatch,
        {
            tuple(BUILD): [hint],
            tuple(TIDY): [_completed(TIDY, 1, stderr="go: proxy.golang.org: connection refused\n")],
        },
    )

    result = _verify(_cells("Quick Start"), CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD, TIDY]
    assert "proxy.golang.org: connection refused" in result.output
    assert "go-mod-tidy=rc=1" in _assembly_line(result)


def test_vet_failure_is_not_verified_and_vet_output_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(
        monkeypatch,
        {
            tuple(BUILD): [_completed(BUILD, 0, stdout="build ok\n")],
            tuple(VET): [_completed(VET, 1, stderr="./main.go:5:2: printf format %d has 0 args\n")],
        },
    )

    result = _verify(_cells("Quick Start (2)"), CELLS_MODULE, _cells_library(tmp_path), tmp_path)

    assert result.verified is False
    assert fake.calls == [BUILD, VET]
    assert "printf format %d has 0 args" in result.output


# --- wiring -----------------------------------------------------------------------------------


def test_go_platform_dispatch_points_at_verify_go_example() -> None:
    assert build_chunks._PLATFORM_DISPATCH["go"]["verify"] is ev.verify_go_example


def test_verify_go_example_passes_the_page_context_to_the_assembler() -> None:
    source = Path(ev.__file__).read_text(encoding="utf-8")
    start = source.index("def verify_go_example(")
    end = source.index("\n# --- pdf/java", start)
    assert "page_code=candidate.page_code" in source[start:end]
