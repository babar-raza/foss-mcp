"""G3/TC-350: infra/demo_entrypoint.py, the demo image's pilot-selecting entrypoint. Offline: no docker.

os.execv is replaced by a recorder, so selecting a pilot never starts a server. Part (a) pins pilot
spelling and selection, (b) the ``--list`` output, (c) every refusal (no pilot, unknown pilot, no
readable active generation) and that nothing is exec'd and no identity is invented, (d) the environment
set for the exec and the exec target, (e) that no other environment value is ever read or printed, and
(f) the real committed infra/demo-manifests tree against the chart's pilot list.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "infra"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
ENTRYPOINT = INFRA_DIR / "demo_entrypoint.py"

sys.path.insert(0, str(INFRA_DIR))
sys.path.insert(0, str(DEPLOY_DIR))

import demo_entrypoint as entry  # noqa: E402
import fetch_product_reference  # noqa: E402
import fetch_recent_releases  # noqa: E402
import pilots as chart_pilots  # noqa: E402
import verify_package_registry  # noqa: E402

CANARY = "canary-secret-7f3a91"
EXEC_TARGET = "/app/infra/serve_http.py"


# --------------------------------------------------------------------------------------------- helpers


def make_scope(
    root: Path,
    family: str,
    platform: str,
    *,
    kind: str = "self_extracted",
    documents: int = 3,
    version: str = "20261010T000000Z-abc123",
) -> Path:
    scope = root / f"{family}__{platform}__{kind}"
    scope.mkdir(parents=True)
    generation_id = f"{family}::{platform}::{kind}::{version}"
    (scope / "active_pointer.json").write_text(json.dumps({"generation_id": generation_id}), encoding="utf-8")
    generation = {
        "family": family,
        "platform": platform,
        "source_kind": kind,
        "generation_id": generation_id,
        "version": version,
        "payload": {
            "lexical_index": {
                "doc_freq": {"alpha": documents},
                "documents": {f"chunk{i}": {"tf": {"alpha": 1}, "length": 1} for i in range(documents)},
                "generation_id": generation_id,
            },
            "vector_index": {"points": []},
        },
    }
    (scope / f"generation__{version}.json").write_text(json.dumps(generation), encoding="utf-8")
    return scope


def add_sidecar(root: Path, prefix: str, family: str, platform: str) -> None:
    (root / f"{prefix}_{family}_{platform}.json").write_text("{}", encoding="utf-8")


class ExecRecorder:
    """Stands in for os.execv: records the call and returns, so main() falls through."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, path: str, args: list[str]) -> None:
        self.calls.append((path, list(args)))


class AuditedEnv(dict[str, str]):
    """A dict that records every key READ (and flags whole-environment enumeration as '*')."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read: set[str] = set()

    def get(self, key: str, default: Any = None) -> Any:
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.read.add(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self.read.add(str(key))
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        self.read.add("*")
        return super().__iter__()

    def items(self) -> Any:
        self.read.add("*")
        return super().items()

    def keys(self) -> Any:
        self.read.add("*")
        return super().keys()

    def values(self) -> Any:
        self.read.add("*")
        return super().values()

    def copy(self) -> Any:
        self.read.add("*")
        return super().copy()


@pytest.fixture
def execv(monkeypatch: pytest.MonkeyPatch) -> ExecRecorder:
    recorder = ExecRecorder()
    monkeypatch.setattr(entry.os, "execv", recorder)
    return recorder


@pytest.fixture
def baked(tmp_path: Path) -> Path:
    """A small baked tree: pdf/go (3 documents, one sidecar), pdf/net (5), jmap/nodejs (1)."""
    root = tmp_path / "manifests"
    root.mkdir()
    make_scope(root, "pdf", "go", documents=3)
    make_scope(root, "pdf", "net", documents=5)
    make_scope(root, "jmap", "nodejs", documents=1)
    add_sidecar(root, "product_reference", "pdf", "go")
    return root


def env_for(root: Path, **extra: str) -> dict[str, str]:
    return {
        entry.MANIFESTS_DIR_ENV: str(root),
        "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY": CANARY,
        "FOSS_MCP_GITHUB_TOKEN": CANARY,
        "HOME": CANARY,
        **extra,
    }


# ---------------------------------------------------------------------------- (a) spelling & selection


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pdf/go", ("pdf", "go")),
        ("pdf_go", ("pdf", "go")),
        ("  cells/nodejs  ", ("cells", "nodejs")),
        ("3d_java", ("3d", "java")),
        ("3d/typescript", ("3d", "typescript")),
    ],
)
def test_both_spellings_parse_to_family_and_platform(value: str, expected: tuple[str, str]) -> None:
    assert entry.parse_pilot(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "pdf",
        "pdf/",
        "/go",
        "pdf/go/extra",
        "pdf_go_extra",
        "PDF/Go",
        "pdf-go",
        "pdf/go net",
        "../pdf/go",
        "pdf/..",
    ],
)
def test_malformed_pilot_spellings_are_refused(value: str) -> None:
    with pytest.raises(entry.EntrypointError):
        entry.parse_pilot(value)


@pytest.mark.parametrize("spelling", ["pdf/go", "pdf_go"])
def test_selection_by_env_accepts_both_spellings(
    spelling: str, baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    env = env_for(baked, **{entry.PILOT_ENV: spelling})
    assert entry.main([], environ=env) == 0
    assert len(execv.calls) == 1
    assert env[entry.FAMILY_ENV] == "pdf"
    assert env[entry.PLATFORM_ENV] == "go"
    assert env[entry.SOURCE_KIND_ENV] == "self_extracted"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("spelling", ["jmap/nodejs", "jmap_nodejs"])
def test_selection_by_flag_accepts_both_spellings(spelling: str, baked: Path, execv: ExecRecorder) -> None:
    env = env_for(baked)
    assert entry.main(["--pilot", spelling], environ=env) == 0
    assert len(execv.calls) == 1
    assert (env[entry.FAMILY_ENV], env[entry.PLATFORM_ENV]) == ("jmap", "nodejs")


def test_the_flag_wins_over_the_environment_variable(baked: Path, execv: ExecRecorder) -> None:
    env = env_for(baked, **{entry.PILOT_ENV: "pdf/net"})
    assert entry.main(["--pilot", "pdf/go"], environ=env) == 0
    assert (env[entry.FAMILY_ENV], env[entry.PLATFORM_ENV]) == ("pdf", "go")


def test_the_source_kind_comes_from_the_matched_scope_directory(tmp_path: Path, execv: ExecRecorder) -> None:
    root = tmp_path / "m"
    root.mkdir()
    make_scope(root, "pdf", "go", kind="vendor_supplied")
    env = env_for(root, **{entry.PILOT_ENV: "pdf/go"})
    assert entry.main([], environ=env) == 0
    assert env[entry.SOURCE_KIND_ENV] == "vendor_supplied"


# ------------------------------------------------------------------------------------- (b) --list


def parse_list_line(line: str) -> tuple[str, dict[str, str]]:
    name, *fields = line.split()
    return name, dict(field.split("=", 1) for field in fields)


def test_list_prints_one_line_per_baked_pilot_and_exits_zero(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--list"], environ=env_for(baked)) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert [parse_list_line(line)[0] for line in lines] == ["jmap/nodejs", "pdf/go", "pdf/net"]
    by_name = dict(parse_list_line(line) for line in lines)
    assert by_name["pdf/go"] == {
        "source_kind": "self_extracted",
        "documents": "3",
        "product_reference": "yes",
        "recent_releases": "no",
        "package_registry": "no",
    }
    assert by_name["pdf/net"]["documents"] == "5"
    assert by_name["jmap/nodejs"]["documents"] == "1"
    assert execv.calls == []


def test_list_reports_each_of_the_three_sidecars_independently(tmp_path: Path, execv: ExecRecorder) -> None:
    root = tmp_path / "m"
    root.mkdir()
    make_scope(root, "pdf", "go")
    add_sidecar(root, "recent_releases", "pdf", "go")
    add_sidecar(root, "package_registry", "pdf", "go")
    add_sidecar(root, "product_reference", "pdf", "net")  # another pilot's sidecar must not count
    line = entry.format_pilot_line(root, entry.discover_pilots(root)[0])
    assert parse_list_line(line)[1] == {
        "source_kind": "self_extracted",
        "documents": "3",
        "product_reference": "no",
        "recent_releases": "yes",
        "package_registry": "yes",
    }


def test_list_marks_an_unreadable_scope_instead_of_hiding_it(
    baked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (baked / "pdf__net__self_extracted" / "active_pointer.json").unlink()
    assert entry.main(["--list"], environ=env_for(baked)) == 0
    by_name = dict(parse_list_line(line) for line in capsys.readouterr().out.splitlines())
    assert by_name["pdf/net"]["documents"] == "unreadable"
    assert by_name["pdf/go"]["documents"] == "3"


def test_list_ignores_root_files_and_directories_that_are_not_scopes(
    baked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (baked / "stray_dir").mkdir()
    (baked / "a__b").mkdir()
    (baked / "UPPER__CASE__self_extracted").mkdir()
    (baked / "notes.txt").write_text("x", encoding="utf-8")
    assert entry.main(["--list"], environ=env_for(baked)) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_list_of_an_empty_or_missing_manifests_directory_exits_zero_with_no_pilot_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--list"], environ=env_for(tmp_path / "does-not-exist")) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no pilots are baked" in captured.err


# --------------------------------------------------------------------------------- (c) refusals


def test_no_pilot_prints_usage_and_the_list_to_stderr_and_exits_two(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    env = env_for(baked)
    assert entry.main([], environ=env) == entry.EXIT_USAGE == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert entry.PILOT_ENV in captured.err
    for name in ("pdf/go", "pdf/net", "jmap/nodejs"):
        assert name in captured.err
    assert "documents=3" in captured.err
    assert execv.calls == []
    assert entry.FAMILY_ENV not in env
    assert entry.PLATFORM_ENV not in env
    assert entry.SOURCE_KIND_ENV not in env


def test_a_blank_pilot_is_the_same_as_no_pilot(baked: Path, execv: ExecRecorder) -> None:
    assert entry.main([], environ=env_for(baked, **{entry.PILOT_ENV: "   "})) == 2
    assert entry.main(["--pilot", ""], environ=env_for(baked)) == 2
    assert execv.calls == []


def test_no_pilot_never_adopts_a_pre_set_identity(baked: Path, execv: ExecRecorder) -> None:
    """A caller-supplied FOSS_MCP_FAMILY/PLATFORM without a pilot is not a selection: the entrypoint
    refuses rather than guess, exactly as it does for a bare run."""
    env = env_for(baked, **{entry.FAMILY_ENV: "pdf", entry.PLATFORM_ENV: "net"})
    assert entry.main([], environ=env) == 2
    assert execv.calls == []


def test_no_pilot_with_nothing_baked_still_exits_two(
    tmp_path: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main([], environ=env_for(tmp_path / "empty")) == 2
    assert "No pilots are baked" in capsys.readouterr().err
    assert execv.calls == []


def test_an_unknown_pilot_exits_two_and_names_the_baked_ones(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    env = env_for(baked, **{entry.PILOT_ENV: "pdf/rust"})
    assert entry.main([], environ=env) == 2
    err = capsys.readouterr().err
    assert "unknown pilot" in err
    assert "pdf/rust" in err
    for name in ("pdf/go", "pdf/net", "jmap/nodejs"):
        assert name in err
    assert execv.calls == []
    assert entry.FAMILY_ENV not in env


def test_a_malformed_pilot_exits_two_and_execs_nothing(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--pilot", "pdf/go/extra"], environ=env_for(baked)) == 2
    assert "invalid pilot" in capsys.readouterr().err
    assert execv.calls == []


def test_a_pilot_baked_under_two_source_kinds_is_refused_not_picked(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(baked, "pdf", "go", kind="vendor_supplied")
    assert entry.main(["--pilot", "pdf/go"], environ=env_for(baked)) == 2
    assert "ambiguous" in capsys.readouterr().err
    assert execv.calls == []


def break_missing_pointer(scope: Path) -> None:
    (scope / "active_pointer.json").unlink()


def break_malformed_pointer(scope: Path) -> None:
    (scope / "active_pointer.json").write_text("{not json", encoding="utf-8")


def break_pointer_without_id(scope: Path) -> None:
    (scope / "active_pointer.json").write_text(json.dumps({"generation_id": ""}), encoding="utf-8")


def break_pointer_not_an_object(scope: Path) -> None:
    (scope / "active_pointer.json").write_text("[]", encoding="utf-8")


def break_missing_generation_file(scope: Path) -> None:
    next(scope.glob("generation__*.json")).unlink()


def break_malformed_generation(scope: Path) -> None:
    next(scope.glob("generation__*.json")).write_text("{truncated", encoding="utf-8")


def break_generation_without_lexical_index(scope: Path) -> None:
    generation = next(scope.glob("generation__*.json"))
    generation.write_text(json.dumps({"payload": {"vector_index": {}}}), encoding="utf-8")


def break_pointer_escaping_the_scope(scope: Path) -> None:
    (scope / "active_pointer.json").write_text(
        json.dumps({"generation_id": "pdf::go::self_extracted::../../elsewhere"}), encoding="utf-8"
    )


def break_scope_has_no_files(scope: Path) -> None:
    for child in scope.iterdir():
        child.unlink()


@pytest.mark.parametrize(
    "breaker",
    [
        break_missing_pointer,
        break_malformed_pointer,
        break_pointer_without_id,
        break_pointer_not_an_object,
        break_missing_generation_file,
        break_malformed_generation,
        break_generation_without_lexical_index,
        break_pointer_escaping_the_scope,
        break_scope_has_no_files,
    ],
)
def test_a_scope_without_a_readable_active_generation_exits_two(
    breaker: Any, baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    breaker(baked / "pdf__go__self_extracted")
    env = env_for(baked, **{entry.PILOT_ENV: "pdf/go"})
    assert entry.main([], environ=env) == 2
    err = capsys.readouterr().err
    assert "pdf__go__self_extracted" in err
    assert execv.calls == []
    assert entry.FAMILY_ENV not in env


def test_extra_command_line_arguments_are_refused_with_exit_two(baked: Path, execv: ExecRecorder) -> None:
    """The image no longer starts an arbitrary command: a stray argument is a usage error."""
    with pytest.raises(SystemExit) as raised:
        entry.main(["python", EXEC_TARGET], environ=env_for(baked, **{entry.PILOT_ENV: "pdf/go"}))
    assert raised.value.code == 2
    assert execv.calls == []


def test_failure_to_exec_exits_one_without_a_traceback(
    baked: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_execv(path: str, args: list[str]) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(entry.os, "execv", broken_execv)
    assert entry.main(["--pilot", "pdf/go"], environ=env_for(baked)) == 1
    assert "cannot start the server" in capsys.readouterr().err


# ----------------------------------------------------------------------- (d) env set & exec target


def test_the_exec_replaces_this_process_with_the_http_server(baked: Path, execv: ExecRecorder) -> None:
    assert entry.main(["--pilot", "pdf/net"], environ=env_for(baked)) == 0
    assert execv.calls == [(sys.executable, [sys.executable, EXEC_TARGET])]
    assert entry.SERVER_SCRIPT == EXEC_TARGET


def test_the_exec_target_is_the_http_server_the_image_copies_to_that_path() -> None:
    assert (INFRA_DIR / Path(EXEC_TARGET).name).is_file()
    dockerfile = (REPO_ROOT / "Dockerfile.serving").read_text(encoding="utf-8")
    assert f"COPY infra/{Path(EXEC_TARGET).name} ./infra/{Path(EXEC_TARGET).name}" in dockerfile


def test_the_environment_for_the_exec_carries_exactly_the_matched_identity(
    baked: Path, execv: ExecRecorder
) -> None:
    env = env_for(baked, **{entry.PILOT_ENV: "pdf_net"})
    before = dict(env)
    assert entry.main([], environ=env) == 0
    changed = {key for key in env if env[key] != before.get(key)}
    assert changed == {entry.FAMILY_ENV, entry.PLATFORM_ENV, entry.SOURCE_KIND_ENV}
    assert {key: env[key] for key in changed} == {
        entry.FAMILY_ENV: "pdf",
        entry.PLATFORM_ENV: "net",
        entry.SOURCE_KIND_ENV: "self_extracted",
    }
    assert {key: env[key] for key in before} == before  # nothing the caller set was altered or removed


def test_a_pre_set_identity_is_overridden_by_the_selected_pilot(baked: Path, execv: ExecRecorder) -> None:
    env = env_for(
        baked,
        **{
            entry.PILOT_ENV: "pdf/go",
            entry.FAMILY_ENV: "cells",
            entry.PLATFORM_ENV: "rust",
            entry.SOURCE_KIND_ENV: "elsewhere",
        },
    )
    assert entry.main([], environ=env) == 0
    assert (env[entry.FAMILY_ENV], env[entry.PLATFORM_ENV], env[entry.SOURCE_KIND_ENV]) == (
        "pdf",
        "go",
        "self_extracted",
    )


def test_the_real_process_environment_is_what_the_exec_inherits(
    baked: Path, monkeypatch: pytest.MonkeyPatch, execv: ExecRecorder
) -> None:
    for name in (entry.FAMILY_ENV, entry.PLATFORM_ENV, entry.SOURCE_KIND_ENV):
        monkeypatch.setenv(name, "placeholder")  # registers the undo, so nothing leaks past this test
    monkeypatch.setenv(entry.MANIFESTS_DIR_ENV, str(baked))
    monkeypatch.setenv(entry.PILOT_ENV, "pdf/go")
    assert entry.main([]) == 0
    assert len(execv.calls) == 1
    assert os.environ[entry.FAMILY_ENV] == "pdf"
    assert os.environ[entry.PLATFORM_ENV] == "go"
    assert os.environ[entry.SOURCE_KIND_ENV] == "self_extracted"


# ------------------------------------------------------------------------ (e) no other env value


def run_every_path(baked: Path) -> list[AuditedEnv]:
    envs: list[AuditedEnv] = []
    for argv, extra in [
        (["--list"], {}),
        ([], {}),
        ([], {entry.PILOT_ENV: "nope/nope"}),
        ([], {entry.PILOT_ENV: "not a pilot"}),
        ([], {entry.PILOT_ENV: "pdf/go"}),
        (["--pilot", "pdf_net"], {}),
    ]:
        env = AuditedEnv(env_for(baked, **extra))
        entry.main(argv, environ=env)
        envs.append(env)
    return envs


def test_no_other_environment_value_appears_in_any_output(
    baked: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    run_every_path(baked)
    captured = capsys.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err
    assert captured.err  # the refusals really did print something to check


def test_only_the_two_permitted_environment_values_are_ever_read(baked: Path, execv: ExecRecorder) -> None:
    for env in run_every_path(baked):
        assert env.read <= {entry.PILOT_ENV, entry.MANIFESTS_DIR_ENV}, env.read
        assert "*" not in env.read


def test_a_real_process_prints_no_other_environment_value(baked: Path) -> None:
    """The script run as a program, with a canary in its real environment, on every non-serving path."""
    process_env = {**os.environ, **env_for(baked)}
    process_env.pop(entry.PILOT_ENV, None)
    for args, extra in [
        (["--list"], {}),
        ([], {}),
        ([], {entry.PILOT_ENV: "nope/nope"}),
        (["--pilot", "pdf/go/extra"], {}),
    ]:
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**process_env, **extra},
            timeout=60,
        )
        assert CANARY not in result.stdout + result.stderr
        assert result.returncode == (0 if args == ["--list"] else 2), (args, result.stderr)


def test_a_real_process_without_a_pilot_lists_the_pilots_on_stderr(baked: Path) -> None:
    process_env = {**os.environ, entry.MANIFESTS_DIR_ENV: str(baked)}
    process_env.pop(entry.PILOT_ENV, None)
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=process_env,
        timeout=60,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    for name in ("pdf/go", "pdf/net", "jmap/nodejs"):
        assert name in result.stderr


# ------------------------------------------------------------------------ module-level discipline


def test_the_entrypoint_imports_only_the_standard_library() -> None:
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, sorted(
        imported - set(sys.stdlib_module_names)
    )


def test_the_sidecar_names_agree_with_the_modules_that_write_them() -> None:
    expected = {
        "product_reference": fetch_product_reference.sidecar_name("pdf", "go"),
        "recent_releases": fetch_recent_releases.recent_releases_sidecar_name("pdf", "go"),
        "package_registry": verify_package_registry.verify_package_registry_sidecar_name("pdf", "go"),
    }
    assert set(entry.SIDECAR_PREFIXES) == set(expected)
    for prefix in entry.SIDECAR_PREFIXES:
        assert expected[prefix] == f"{prefix}_pdf_go.json"


# ----------------------------------------------------------------- (f) the real committed demo tree


def chart_pilot_names() -> list[str]:
    return [f"{pilot['family']}/{pilot['platform']}" for pilot in chart_pilots.load_pilots()]


def test_the_committed_demo_tree_bakes_exactly_the_charts_pilots(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--list"], environ={entry.MANIFESTS_DIR_ENV: str(DEMO_MANIFESTS)}) == 0
    parsed = [parse_list_line(line) for line in capsys.readouterr().out.splitlines()]
    names = [name for name, _ in parsed]
    assert len(names) == len(set(names)) == len(chart_pilot_names())
    assert set(names) == set(chart_pilot_names())
    for name, fields in parsed:
        assert fields["documents"].isdigit() and int(fields["documents"]) > 0, name


def test_every_chart_pilot_is_selectable_from_the_committed_demo_tree(execv: ExecRecorder) -> None:
    for name in chart_pilot_names():
        env = {entry.MANIFESTS_DIR_ENV: str(DEMO_MANIFESTS), entry.PILOT_ENV: name}
        assert entry.main([], environ=env) == 0, name
        assert f"{env[entry.FAMILY_ENV]}/{env[entry.PLATFORM_ENV]}" == name
    assert len(execv.calls) == len(chart_pilot_names())
