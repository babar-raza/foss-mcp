"""Offline tests for the Go example verifier's major-version rule
(src/foss_mcp/indexing/example_verifier.py, ``_go_required_version``; TC-376, REQ-G3-005).

Go refuses to ``require`` a module whose path ends in a major-version suffix at a version of a
different major, so the verifier's throwaway go.mod must require ``.../v26`` at ``v26.0.0``.
Every other module path keeps ``v0.0.0``. ``_run`` is replaced with a fake, so these tests need
no network and no Go toolchain; one extra test runs the real ``go build`` and is skipped, with a
stated reason, when ``go`` is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample

V26_PATH = "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26"
PLAIN_PATH = "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"


@pytest.mark.parametrize(
    ("module_path", "expected"),
    [
        (V26_PATH, "v26.0.0"),
        ("example.com/lib/v2", "v2.0.0"),
        ("example.com/lib/v10", "v10.0.0"),
        ("example.com/lib/v26", "v26.0.0"),
        ("example.com/lib/v26/", "v26.0.0"),
        ("gopkg.in/yaml.v2", "v2.0.0"),
        ("gopkg.in/yaml.v3", "v3.0.0"),
        ("example.com/lib/v1", "v0.0.0"),
        ("example.com/lib/v0", "v0.0.0"),
        ("example.com/lib/version", "v0.0.0"),
        ("example.com/lib/v26x", "v0.0.0"),
        ("example.com/lib/vendor", "v0.0.0"),
        ("example.com/lib/V26", "v0.0.0"),
        ("example.com/lib/v02", "v0.0.0"),
        ("example.com/v2/lib", "v0.0.0"),
        ("gopkg.in/yaml.v1", "v0.0.0"),
        (PLAIN_PATH, "v0.0.0"),
        ("example.com/lib", "v0.0.0"),
        ("", "v0.0.0"),
    ],
)
def test_go_required_version_matches_the_module_path_major(module_path: str, expected: str) -> None:
    assert ev._go_required_version(module_path) == expected


def _candidate(code: str = 'fmt.Println("hello")\n') -> CandidateExample:
    return CandidateExample(title="Major version fixture", description="", language="go", code=code)


def _verify_capturing_go_mod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module_path: str, code: str
) -> tuple[str, str]:
    """Run ``verify_go_example`` with ``_run`` faked; return the go.mod and main.go it wrote."""

    def fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ev, "_run", fake_run)
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = ev.verify_go_example(
        _candidate(code), module_path=module_path, library_dir=library_dir, workdir=workdir
    )
    assert result.verified is True, result.output
    project_dir = workdir / "candidate_project"
    return (
        (project_dir / "go.mod").read_text(encoding="utf-8"),
        (project_dir / "main.go").read_text(encoding="utf-8"),
    )


def test_go_mod_for_a_major_suffixed_module_requires_the_matching_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    go_mod, _ = _verify_capturing_go_mod(monkeypatch, tmp_path, V26_PATH, 'fmt.Println("x")\n')

    assert f"require {V26_PATH} v26.0.0\n" in go_mod
    assert f"replace {V26_PATH} => " in go_mod
    assert (tmp_path / "library").resolve().as_posix() in go_mod
    assert "v0.0.0" not in go_mod


def test_go_mod_for_a_plain_module_still_requires_v0_0_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    go_mod, _ = _verify_capturing_go_mod(monkeypatch, tmp_path, PLAIN_PATH, 'fmt.Println("x")\n')

    assert f"require {PLAIN_PATH} v0.0.0\n" in go_mod
    assert f"replace {PLAIN_PATH} => " in go_mod


def test_wrapper_still_imports_the_full_module_path_including_the_major_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, main_go = _verify_capturing_go_mod(
        monkeypatch, tmp_path, V26_PATH, 'doc, _ := pdf.Open("input.pdf")\n_ = doc\n'
    )

    assert f'\tpdf "{V26_PATH}"\n' in main_go


def test_wrap_helper_import_path_is_unchanged_by_the_version_rule() -> None:
    wrapped = ev._wrap_go_fragment_if_needed('pdf.Open("a")\n', V26_PATH)

    assert f'\tpdf "{V26_PATH}"\n' in wrapped


@pytest.mark.skipif(
    shutil.which("go") is None,
    reason="the real-toolchain check needs `go` on PATH; it is absent, so only the offline tests ran",
)
def test_real_go_builds_a_consumer_of_a_v3_module_at_the_returned_version(tmp_path: Path) -> None:
    module_path = "example.invalid/lib/v3"
    version = ev._go_required_version(module_path)
    assert version == "v3.0.0"

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "go.mod").write_text(f"module {module_path}\n\ngo 1.20\n", encoding="utf-8")
    (lib_dir / "lib.go").write_text('package lib\n\nfunc Hello() string { return "hi" }\n', encoding="utf-8")

    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    (consumer_dir / "go.mod").write_text(
        f"module consumer\n\ngo 1.20\n\nrequire {module_path} {version}\n\n"
        f"replace {module_path} => {lib_dir.resolve().as_posix()}\n",
        encoding="utf-8",
    )
    (consumer_dir / "main.go").write_text(
        f'package main\n\nimport lib "{module_path}"\n\nfunc main() {{ _ = lib.Hello() }}\n',
        encoding="utf-8",
    )

    env = {**os.environ, "GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod", "GOPROXY": "off"}
    completed = subprocess.run(
        ["go", "build", "./..."], cwd=consumer_dir, capture_output=True, text=True, env=env
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
