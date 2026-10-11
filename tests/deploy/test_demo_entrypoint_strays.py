"""G3/TC-390: the demo entrypoint survives stray scope directories and tells the operator what to fix.

Offline, tmp manifests roots only. ``os.execv`` is replaced by a recorder (the way
tests/deploy/test_demo_entrypoint.py does it), so selecting a pilot never starts a server.

The defect: ``discover_pilots`` treated ANY ``<family>__<platform>__<kind>`` directory as a baked pilot,
so one stray empty directory (``pdf__net__oops``, which the report_index_freshness traversal once created)
next to the baked ``pdf__net__self_extracted`` made the next ``docker restart`` stop with exit 2
"pilot pdf/net is ambiguous". Part (a) pins that a stray is ignored, (b) that genuine ambiguity is still
refused, (c) the lowercase hint, (d) the usage text's mention of the browser-origin variable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "infra"

sys.path.insert(0, str(INFRA_DIR))

import demo_entrypoint as entry  # noqa: E402

VERSION = "20261010T000000Z-abc123"


def make_scope(
    root: Path, family: str, platform: str, *, kind: str = "self_extracted", pointer: bool = True
) -> Path:
    scope = root / f"{family}__{platform}__{kind}"
    scope.mkdir(parents=True)
    generation_id = f"{family}::{platform}::{kind}::{VERSION}"
    if pointer:
        (scope / "active_pointer.json").write_text(
            json.dumps({"generation_id": generation_id}), encoding="utf-8"
        )
    generation = {
        "generation_id": generation_id,
        "payload": {"lexical_index": {"documents": {"chunk0": {"tf": {"alpha": 1}, "length": 1}}}},
    }
    (scope / f"generation__{VERSION}.json").write_text(json.dumps(generation), encoding="utf-8")
    return scope


class ExecRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, path: str, args: list[str]) -> None:
        self.calls.append((path, list(args)))


@pytest.fixture
def execv(monkeypatch: pytest.MonkeyPatch) -> ExecRecorder:
    recorder = ExecRecorder()
    monkeypatch.setattr(entry.os, "execv", recorder)
    return recorder


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "manifests"
    path.mkdir()
    return path


def env_for(root: Path, **extra: str) -> dict[str, str]:
    return {entry.MANIFESTS_DIR_ENV: str(root), **extra}


# --------------------------------------------------------------------------------- (a) strays are ignored


def test_an_empty_stray_next_to_a_baked_scope_still_selects_the_baked_pilot(
    root: Path, execv: ExecRecorder
) -> None:
    make_scope(root, "pdf", "net")
    (root / "pdf__net__oops").mkdir()
    env = env_for(root, **{entry.PILOT_ENV: "pdf/net"})
    assert entry.main([], environ=env) == 0
    assert len(execv.calls) == 1
    assert env[entry.SOURCE_KIND_ENV] == "self_extracted"
    assert env[entry.FAMILY_ENV] == "pdf"
    assert env[entry.PLATFORM_ENV] == "net"


def test_the_qa_sequence_a_stray_appears_and_the_container_restarts(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net")
    first = env_for(root, **{entry.PILOT_ENV: "pdf/net"})
    assert entry.main([], environ=first) == 0
    # The traversal creates a directory next to the baked scope while the container runs ...
    (root / "pdf__net__oops").mkdir()
    # ... and `docker restart` runs the entrypoint again with a fresh environment.
    restarted = env_for(root, **{entry.PILOT_ENV: "pdf/net"})
    assert entry.main([], environ=restarted) == 0
    assert len(execv.calls) == 2
    assert restarted[entry.SOURCE_KIND_ENV] == "self_extracted"
    assert "ambiguous" not in capsys.readouterr().err


def test_a_stray_holding_only_unrelated_files_is_still_a_stray(root: Path, execv: ExecRecorder) -> None:
    make_scope(root, "pdf", "net")
    stray = root / "pdf__net__oops"
    stray.mkdir()
    (stray / "notes.txt").write_text("x", encoding="utf-8")
    (stray / "active_pointer.json").write_text("{}", encoding="utf-8")
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net"})) == 0
    assert len(execv.calls) == 1


def test_a_stray_is_not_listed(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_scope(root, "pdf", "net")
    (root / "pdf__net__oops").mkdir()
    (root / "pdf__go__self_extracted").mkdir()
    assert entry.main(["--list"], environ=env_for(root)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("pdf/net source_kind=self_extracted ")


def test_discover_pilots_ignores_a_directory_with_no_generation_file(root: Path) -> None:
    make_scope(root, "pdf", "net")
    (root / "pdf__net__oops").mkdir()
    assert [pilot.scope_dir.name for pilot in entry.discover_pilots(root)] == ["pdf__net__self_extracted"]


def test_the_baked_scope_test_is_the_presence_of_a_generation_file(tmp_path: Path) -> None:
    empty = tmp_path / "pdf__net__oops"
    empty.mkdir()
    assert entry._is_baked_scope(empty) is False
    (empty / "active_pointer.json").write_text("{}", encoding="utf-8")
    assert entry._is_baked_scope(empty) is False
    (empty / f"generation__{VERSION}.json").write_text("{}", encoding="utf-8")
    assert entry._is_baked_scope(empty) is True


def test_a_scope_that_lost_its_pointer_but_keeps_its_generation_is_still_baked(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net", pointer=False)
    assert entry.main(["--list"], environ=env_for(root)) == 0
    assert "documents=unreadable" in capsys.readouterr().out


def test_a_pilot_whose_only_directory_is_a_stray_is_unknown_and_the_stray_is_named(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "go")
    (root / "pdf__net__oops").mkdir()
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net"})) == 2
    err = capsys.readouterr().err
    assert "unknown pilot 'pdf/net'" in err
    assert "pdf__net__oops" in err
    assert execv.calls == []


# ------------------------------------------------------------------------ (b) real ambiguity still refused


def test_two_genuinely_baked_kinds_with_pointers_stay_ambiguous(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net", kind="self_extracted")
    make_scope(root, "pdf", "net", kind="vendor_supplied")
    (root / "pdf__net__oops").mkdir()
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net"})) == 2
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "self_extracted" in err
    assert "vendor_supplied" in err
    assert execv.calls == []


def test_of_two_candidates_only_the_one_with_a_pointer_counts(root: Path, execv: ExecRecorder) -> None:
    make_scope(root, "pdf", "net", kind="self_extracted")
    make_scope(root, "pdf", "net", kind="leftover", pointer=False)
    env = env_for(root, **{entry.PILOT_ENV: "pdf/net"})
    assert entry.main([], environ=env) == 0
    assert env[entry.SOURCE_KIND_ENV] == "self_extracted"
    assert len(execv.calls) == 1


def test_two_candidates_and_neither_has_a_pointer_is_ambiguous(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net", kind="self_extracted", pointer=False)
    make_scope(root, "pdf", "net", kind="leftover", pointer=False)
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net"})) == 2
    assert "ambiguous" in capsys.readouterr().err
    assert execv.calls == []


def test_a_single_candidate_without_a_pointer_is_still_refused_as_unreadable(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net", pointer=False)
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net"})) == 2
    assert "pdf__net__self_extracted" in capsys.readouterr().err
    assert execv.calls == []


# ------------------------------------------------------------------------------------ (c) lowercase hint


def test_an_upper_case_pilot_is_refused_with_the_lowercase_hint(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net")
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "PDF/NET"})) == 2
    err = capsys.readouterr().err
    assert "did you mean 'pdf/net'?" in err
    assert execv.calls == []


def test_case_is_not_normalised_even_when_the_lowercase_pilot_is_baked(
    root: Path, execv: ExecRecorder
) -> None:
    make_scope(root, "pdf", "net")
    env = env_for(root, **{entry.PILOT_ENV: "PDF/NET"})
    assert entry.main([], environ=env) == 2
    assert entry.FAMILY_ENV not in env
    assert execv.calls == []


def test_the_hint_follows_the_spelling_the_operator_used(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net")
    assert entry.main(["--pilot", "  Pdf_Net "], environ=env_for(root)) == 2
    assert "did you mean 'pdf_net'?" in capsys.readouterr().err


def test_a_malformed_pilot_gets_no_hint(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net")
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf/net/x"})) == 2
    err = capsys.readouterr().err
    assert "invalid pilot" in err
    assert "did you mean" not in err
    assert execv.calls == []


def test_an_upper_case_value_that_is_still_malformed_in_lowercase_gets_no_hint(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "PDF/NET/X"})) == 2
    assert "did you mean" not in capsys.readouterr().err


def test_a_lowercase_value_never_gets_a_hint(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main([], environ=env_for(root, **{entry.PILOT_ENV: "pdf-net"})) == 2
    assert "did you mean" not in capsys.readouterr().err


# ------------------------------------------------------------------------------- (d) browser-origin hint


def test_the_usage_text_names_the_browser_origin_variable(
    root: Path, execv: ExecRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(root, "pdf", "net")
    assert entry.main([], environ=env_for(root)) == 2
    err = capsys.readouterr().err
    assert "FOSS_MCP_ALLOWED_ORIGINS=<origin>[,<origin>...]" in err
    assert execv.calls == []


def test_the_usage_text_never_prints_an_origin_value_from_the_environment(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "https://canary-origin.invalid"
    make_scope(root, "pdf", "net")
    assert entry.main([], environ=env_for(root, FOSS_MCP_ALLOWED_ORIGINS=canary)) == 2
    assert canary not in capsys.readouterr().err
