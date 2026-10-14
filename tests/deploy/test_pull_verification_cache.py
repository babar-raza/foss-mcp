"""G3/TC-441: scripts/release/pull_verification_cache.py, offline.

subprocess.run is replaced by a fake cluster that answers `kubectl get pods` and the single-file
`kubectl exec ... tar` with in-memory data, so no kubectl ever runs and nothing reaches a real
cluster. pull_verification_cache.py and scripts/release/snapshot_demo_from_cluster.py (whose
ready_serving_pod it reuses) both import the plain ``subprocess`` module, so patching
``subprocess.run`` on either module's own ``subprocess`` attribute intercepts both call sites -
they are the same module object.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
sys.path.insert(0, str(RELEASE_DIR))
sys.path.insert(0, str(DEPLOY_DIR))

import pilots  # noqa: E402
import pull_verification_cache as pull  # noqa: E402

import infra.build_chunks as build_chunks  # noqa: E402

NS = "foss-mcp-test"
BY_KEY = {pilots.pilot_key(p): p for p in pilots.load_pilots()}
PDF_NET = BY_KEY["pdf_net"]
PDF_GO = BY_KEY["pdf_go"]


def _entry(
    *,
    provenance: str = "readme",
    title: str = "An example",
    code_sha256: str = "a" * 64,
    library_commit: str = "1" * 40,
    platform: str = "net",
    toolchain: str = "dotnet 9.0.100",
    verified: bool = True,
    output: str = "ok",
) -> dict:
    return {
        "provenance": provenance,
        "title": title,
        "code_sha256": code_sha256,
        "library_commit": library_commit,
        "platform": platform,
        "toolchain": toolchain,
        "verified": verified,
        "output": output,
    }


def _cache_bytes(entries: list[dict]) -> bytes:
    return build_chunks.verification_cache_document(entries).encode("utf-8")


def _tar_single_file(filename: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.TarFile(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as archive:
        info = tarfile.TarInfo(filename)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _empty_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.TarFile(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT):
        pass
    return buf.getvalue()


class FakeCluster:
    """Stands in for subprocess.run; answers `get pods` and the single-file `exec ... tar`."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.pods: dict[str, str] = {}  # release name -> pod name
        self.exports: dict[str, tuple[int, bytes, bytes]] = {}  # pod name -> (rc, stdout, stderr)

    def add_pod(self, pilot: dict[str, Any], pod_name: str | None = None) -> str:
        release = pilots.release_name(pilot)
        name = pod_name or f"{release}-6d5f7c9b8-abcde"
        self.pods[release] = name
        self.exports.setdefault(name, (0, _empty_tar(), b""))
        return name

    def set_export(self, pod_name: str, *, rc: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.exports[pod_name] = (rc, stdout, stderr)

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(cmd))
        assert kwargs.get("capture_output") is True
        if cmd[:3] == ["kubectl", "get", "pods"]:
            selector = cmd[cmd.index("-l") + 1]
            release = selector.split(",")[0].split("=", 1)[1]
            pod = self.pods.get(release)
            items = []
            if pod:
                items = [
                    {
                        "metadata": {"name": pod},
                        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
                    }
                ]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"items": items}).encode("utf-8"), b"")
        if cmd[:2] == ["kubectl", "exec"]:
            pod_name = cmd[4]
            rc, stdout, stderr = self.exports.get(pod_name, (0, b"", b"not found"))
            return subprocess.CompletedProcess(cmd, rc, stdout, stderr)
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> FakeCluster:
    fake = FakeCluster()
    monkeypatch.setattr(pull.subprocess, "run", fake)
    return fake


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "verified_examples"


def _run(capsys: pytest.CaptureFixture[str], dest: Path, *extra: str) -> tuple[int, list[str]]:
    rc = pull.main(["--namespace", NS, "--dest", str(dest), *extra])
    return rc, capsys.readouterr().out.splitlines()


def test_a_new_pilot_cache_is_written_from_the_cluster(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pod = cluster.add_pod(PDF_NET)
    cluster.set_export(
        pod, stdout=_tar_single_file("verification_cache_pdf_net.json", _cache_bytes([_entry()]))
    )

    rc, lines = _run(capsys, dest, "--only", "pdf_net")

    assert rc == 0
    assert lines == ["pdf_net: written"]
    written = json.loads((dest / "pdf_net.json").read_text(encoding="utf-8"))
    assert len(written["entries"]) == 1


def test_merge_keeps_other_keys_and_cluster_wins_on_an_identical_key(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest.mkdir(parents=True)
    other_key_entry = _entry(title="Kept", code_sha256="b" * 64, output="existing-untouched")
    same_key_entry = _entry(title="Old verdict", output="stale")
    (dest / "pdf_net.json").write_text(
        build_chunks.verification_cache_document([other_key_entry, same_key_entry]), encoding="utf-8"
    )
    cluster_entry = _entry(title="New verdict", output="fresh-from-cluster")  # identical key, new output
    pod = cluster.add_pod(PDF_NET)
    cluster.set_export(
        pod, stdout=_tar_single_file("verification_cache_pdf_net.json", _cache_bytes([cluster_entry]))
    )

    rc, lines = _run(capsys, dest, "--only", "pdf_net")

    assert rc == 0
    assert lines == ["pdf_net: written"]
    written = json.loads((dest / "pdf_net.json").read_text(encoding="utf-8"))
    by_title = {e["title"]: e for e in written["entries"]}
    assert len(written["entries"]) == 2
    assert by_title["Kept"]["output"] == "existing-untouched"
    assert by_title["New verdict"]["output"] == "fresh-from-cluster"
    assert "Old verdict" not in by_title


def test_a_second_pull_with_the_same_data_reports_unchanged_and_does_not_rewrite(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pod = cluster.add_pod(PDF_NET)
    cluster.set_export(
        pod, stdout=_tar_single_file("verification_cache_pdf_net.json", _cache_bytes([_entry()]))
    )

    rc1, lines1 = _run(capsys, dest, "--only", "pdf_net")
    first_bytes = (dest / "pdf_net.json").read_bytes()
    rc2, lines2 = _run(capsys, dest, "--only", "pdf_net")
    second_bytes = (dest / "pdf_net.json").read_bytes()

    assert (rc1, lines1) == (0, ["pdf_net: written"])
    assert (rc2, lines2) == (0, ["pdf_net: unchanged"])
    assert first_bytes == second_bytes


def test_absent_when_the_archive_lacks_the_file(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pod = cluster.add_pod(PDF_NET)
    cluster.set_export(pod, stdout=_empty_tar())

    rc, lines = _run(capsys, dest, "--only", "pdf_net")

    assert rc == 0
    assert lines == ["pdf_net: absent"]
    assert not (dest / "pdf_net.json").exists()


def test_a_malformed_cluster_cache_is_failed_and_the_existing_file_is_untouched(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest.mkdir(parents=True)
    existing = build_chunks.verification_cache_document([_entry(title="Safe")])
    (dest / "pdf_net.json").write_text(existing, encoding="utf-8")
    pod = cluster.add_pod(PDF_NET)
    malformed = json.dumps({"schema_version": 2, "entries": []}).encode("utf-8")
    cluster.set_export(pod, stdout=_tar_single_file("verification_cache_pdf_net.json", malformed))

    rc, lines = _run(capsys, dest, "--only", "pdf_net")

    assert rc == 1
    assert len(lines) == 1
    assert lines[0].startswith("pdf_net: failed:")
    assert (dest / "pdf_net.json").read_text(encoding="utf-8") == existing


def test_dry_run_writes_nothing(cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pod = cluster.add_pod(PDF_NET)
    cluster.set_export(
        pod, stdout=_tar_single_file("verification_cache_pdf_net.json", _cache_bytes([_entry()]))
    )

    rc, lines = _run(capsys, dest, "--only", "pdf_net", "--dry-run")

    assert rc == 0
    assert lines == ["pdf_net: written"]
    assert not dest.exists() or not (dest / "pdf_net.json").exists()


def test_a_command_other_than_get_pods_or_exec_tar_is_refused() -> None:
    with pytest.raises(pull.PullError):
        pull._assert_allowed(["kubectl", "delete", "pods", "foo"])
    # Both allowed shapes pass.
    pull._assert_allowed(["kubectl", "get", "pods", "-n", NS, "-l", "x=y", "-o", "json"])
    pull._assert_allowed(pull.export_command(NS, "pod-1", "verification_cache_pdf_net.json"))


def test_an_unknown_only_name_exits_2(dest: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _lines = _run(capsys, dest, "--only", "not_a_real_pilot")
    assert rc == 2


def test_multiple_pilots_are_each_reported_on_their_own_line(
    cluster: FakeCluster, dest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pod_net = cluster.add_pod(PDF_NET)
    cluster.set_export(
        pod_net, stdout=_tar_single_file("verification_cache_pdf_net.json", _cache_bytes([_entry()]))
    )
    pod_go = cluster.add_pod(PDF_GO)
    cluster.set_export(pod_go, stdout=_empty_tar())

    rc, lines = _run(capsys, dest, "--only", "pdf_net", "--only", "pdf_go")

    assert rc == 0
    assert set(lines) == {"pdf_net: written", "pdf_go: absent"}
