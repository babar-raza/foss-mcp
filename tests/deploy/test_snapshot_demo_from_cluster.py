"""G3/TC-349: scripts/release/snapshot_demo_from_cluster.py, offline.

subprocess.run is replaced by a fake cluster that answers `kubectl get pods` and `kubectl exec ... tar` with
in-memory data, so no kubectl ever runs and nothing reaches a real cluster. Every refusal and success path of
the snapshot tool is exercised here: pod health, real-content validation, sidecars, tar safety, per-pilot
atomicity, idempotency, selection, and the call site of the TC-346 compaction.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
TOOL = RELEASE_DIR / "snapshot_demo_from_cluster.py"
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"

sys.path.insert(0, str(RELEASE_DIR))
sys.path.insert(0, str(DEPLOY_DIR))

import compact_demo_manifests as compact  # noqa: E402
import pilots  # noqa: E402
import snapshot_demo_from_cluster as snap  # noqa: E402

from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER  # noqa: E402

NS = "foss-mcp-test"
VERSION = "20261010T000000000000Z-aaaa1111"
OLD_VERSION = "20260101T000000000000Z-bbbb2222"
SIDECAR_LABELS = ("product_reference", "recent_releases", "package_registry")

BY_KEY = {pilots.pilot_key(p): p for p in pilots.load_pilots()}
PDF_NET = BY_KEY["pdf_net"]
PDF_GO = BY_KEY["pdf_go"]
SLIDES_PY = BY_KEY["slides_python"]


# ---------------------------------------------------------------------------------------------------
# the fake cluster and the archives it serves
# ---------------------------------------------------------------------------------------------------


def scope_of(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


def release_of(pilot: dict[str, Any]) -> str:
    return f"foss-mcp-{pilot['family']}-{pilot['platform']}"


def pod_of(pilot: dict[str, Any]) -> str:
    return f"{release_of(pilot)}-6d5f7c9b8-abcde"


def tar_bytes(spec: list[tuple[str, str, Any]]) -> bytes:
    """An uncompressed tar from (kind, name, payload) triples; kind is dir, file, sym or hard."""
    buf = io.BytesIO()
    with tarfile.TarFile(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for kind, name, payload in spec:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            elif kind == "file":
                info.size, info.mode = len(payload), 0o644
                archive.addfile(info, io.BytesIO(payload))
            else:
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = payload
                archive.addfile(info)
    return buf.getvalue()


def generation_id_of(pilot: dict[str, Any], version: str = VERSION) -> str:
    return f"{pilot['family']}::{pilot['platform']}::{pilot['sourceKind']}::{version}"


def generation_doc(pilot: dict[str, Any], version: str = VERSION, chunks: int = 3) -> dict[str, Any]:
    gid = generation_id_of(pilot, version)
    points = [
        {"chunk_id": f"c{n}", "point_id": f"{gid}::p{n}", "text": text, "vector": [0.5 * n, -0.25, 1.0 / 3.0]}
        for n, text in enumerate(
            [
                "plain prose",
                f"{EXAMPLE_MARKER} run()",
                "reference text",
                f"intro\n{EXAMPLE_MARKER}\n  go()",
            ]
        )
    ]
    return {
        "family": pilot["family"],
        "generation_id": gid,
        "platform": pilot["platform"],
        "source_kind": pilot["sourceKind"],
        "version": version,
        "payload": {
            "lexical_index": {
                "doc_freq": {"alpha": chunks},
                "documents": {f"c{n}": {"tf": {"alpha": 1}, "length": 3} for n in range(chunks)},
                "generation_id": gid,
            },
            "vector_index": {"dimension": 3, "generation_id": gid, "points": points},
        },
    }


def pointer_bytes(pilot: dict[str, Any], version: str = VERSION) -> bytes:
    return json.dumps({"generation_id": generation_id_of(pilot, version)}, indent=2).encode("utf-8")


def sidecar_name_of(label: str, pilot: dict[str, Any]) -> str:
    return snap.sidecar_names(pilot["family"], pilot["platform"])[label]


def sidecar_content(label: str, pilot: dict[str, Any]) -> bytes:
    """Distinct per label and per pilot, so a sidecar landing under the wrong name is caught."""
    return json.dumps({"label": label, "pilot": pilots.pilot_key(pilot), "pad": label * 3}).encode("utf-8")


def pilot_tar(
    pilot: dict[str, Any],
    *,
    version: str = VERSION,
    chunks: int = 3,
    drop_sidecars: tuple[str, ...] = (),
    pointer: bytes | None = None,
    generation: bytes | None = None,
    sidecar_override: dict[str, bytes] | None = None,
    omit: tuple[str, ...] = (),
    extra: tuple[tuple[str, str, Any], ...] = (),
) -> bytes:
    """What `tar cf - -C /data/manifests .` would stream: the scope, a stale generation, lease state,
    this pilot's sidecars and another pilot's sidecar."""
    scope = scope_of(pilot)
    doc = generation_doc(pilot, version, chunks)
    stale = generation_doc(pilot, OLD_VERSION, 1)
    spec: list[tuple[str, str, Any]] = [
        ("dir", "./", None),
        ("dir", f"./{scope}/", None),
        (
            "file",
            f"./{scope}/active_pointer.json",
            pointer if pointer is not None else pointer_bytes(pilot, version),
        ),
        (
            "file",
            f"./{scope}/generation__{version}.json",
            generation if generation is not None else json.dumps(doc, indent=2).encode("utf-8"),
        ),
        ("file", f"./{scope}/generation__{OLD_VERSION}.json", json.dumps(stale).encode("utf-8")),
        ("file", f"./{scope}/lease_state.json", b'{"fencing_token": 4}'),
        ("file", "./product_reference_other_pilot.json", b'{"foreign": true}'),
    ]
    for label in SIDECAR_LABELS:
        if label in drop_sidecars:
            continue
        data = (sidecar_override or {}).get(label, sidecar_content(label, pilot))
        spec.append(("file", f"./{sidecar_name_of(label, pilot)}", data))
    spec.extend(extra)
    spec = [entry for entry in spec if entry[1] not in omit]
    return tar_bytes(spec)


class FakeCluster:
    """Stands in for subprocess.run. Records every command; answers get pods and exec from its tables."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.pods: dict[str, dict[str, Any]] = {}
        self.tars: dict[str, bytes] = {}
        self.exec_result: dict[str, tuple[int, bytes]] = {}
        self.missing_kubectl = False

    def add(
        self,
        pilot: dict[str, Any],
        tar: bytes | None = None,
        *,
        phase: str = "Running",
        ready: bool = True,
        terminating: bool = False,
    ) -> None:
        name = pod_of(pilot)
        meta: dict[str, Any] = {"name": name}
        if terminating:
            meta["deletionTimestamp"] = "2026-10-11T00:00:00Z"
        self.pods[release_of(pilot)] = {
            "metadata": meta,
            "status": {
                "phase": phase,
                "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            },
        }
        self.tars[name] = tar if tar is not None else pilot_tar(pilot)

    def remove(self, pilot: dict[str, Any]) -> None:
        self.pods.pop(release_of(pilot), None)

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(cmd))
        if self.missing_kubectl:
            raise FileNotFoundError(2, "kubectl")
        assert kwargs.get("capture_output") is True
        assert not kwargs.get("text") and not kwargs.get("universal_newlines"), "archives must stay bytes"
        if cmd[:3] == ["kubectl", "get", "pods"]:
            selector = cmd[cmd.index("-l") + 1]
            release = selector.split(",")[0].split("=", 1)[1]
            pod = self.pods.get(release)
            body = json.dumps({"items": [pod] if pod else []}).encode("utf-8")
            return subprocess.CompletedProcess(cmd, 0, body, b"")
        if cmd[:2] == ["kubectl", "exec"]:
            pod_name = cmd[4]
            if pod_name in self.exec_result:
                rc, err = self.exec_result[pod_name]
                return subprocess.CompletedProcess(cmd, rc, b"", err)
            return subprocess.CompletedProcess(cmd, 0, self.tars[pod_name], b"")
        raise AssertionError(f"unexpected command {cmd}")

    def exec_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["kubectl", "exec"]]

    def get_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["kubectl", "get", "pods"]]


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> FakeCluster:
    fake = FakeCluster()
    monkeypatch.setattr(snap.subprocess, "run", fake)
    return fake


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "demo-manifests"


def run_tool(capsys: pytest.CaptureFixture[str], root: Path, *args: str) -> tuple[int, str, str]:
    rc = snap.main(["--namespace", NS, "--root", str(root), *args])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def stat_tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def pilot_files(root: Path, pilot: dict[str, Any]) -> dict[str, bytes]:
    """Every file under root that belongs to the pilot: its scope directory and its sidecars."""
    names = set(snap.sidecar_names(pilot["family"], pilot["platform"]).values())
    return {
        rel: data
        for rel, data in tree(root).items()
        if rel.replace("\\", "/").startswith(scope_of(pilot) + "/") or rel in names
    }


def assert_nothing_written(root: Path) -> None:
    assert not root.exists() or list(root.iterdir()) == [], list(root.iterdir())


def assert_only_allowed_commands(calls: list[list[str]]) -> None:
    for cmd in calls:
        is_get = cmd[:3] == ["kubectl", "get", "pods"]
        is_export = (
            cmd[:2] == ["kubectl", "exec"]
            and cmd[5:8] == ["-c", "serving", "--"]
            and cmd[-6:] == ["tar", "cf", "-", "-C", "/data/manifests", "."]
        )
        assert is_get or is_export, cmd


# ---------------------------------------------------------------------------------------------------
# (a) a good pilot
# ---------------------------------------------------------------------------------------------------


def test_good_pilot_is_written_with_only_pointer_and_active_generation(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)

    rc, out, err = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 0, (out, err)
    scope_dir = root / scope_of(PDF_NET)
    assert sorted(p.name for p in scope_dir.iterdir()) == [
        "active_pointer.json",
        f"generation__{VERSION}.json",
    ]
    assert (scope_dir / "active_pointer.json").read_bytes() == pointer_bytes(PDF_NET)
    # the stale generation, the lease state and another pilot's sidecar never land
    assert not (scope_dir / f"generation__{OLD_VERSION}.json").exists()
    assert not (scope_dir / "lease_state.json").exists()
    assert not (root / "product_reference_other_pilot.json").exists()
    # exactly the scope directory and the pilot's three sidecars
    assert sorted(p.name for p in root.iterdir()) == sorted(
        [scope_of(PDF_NET), *(sidecar_name_of(label, PDF_NET) for label in SIDECAR_LABELS)]
    )
    assert "pdf_net: ok [written]" in out
    assert "chunks=3" in out and "vector_points_kept=2" in out
    for label in SIDECAR_LABELS:
        assert f"{label}={len(sidecar_content(label, PDF_NET))}B" in out
    assert re.search(r"total: 1 of 1 pilot\(s\) ok, 0 failed; tree \d+ bytes in 5 file\(s\)", out)


def test_sidecars_land_at_the_root_with_the_right_names_and_content(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)
    cluster.add(SLIDES_PY)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--only", "slides_python")

    assert rc == 0, out
    assert (root / "product_reference_pdf_net.json").read_bytes() == sidecar_content(
        "product_reference", PDF_NET
    )
    assert (root / "recent_releases_pdf_net.json").read_bytes() == sidecar_content("recent_releases", PDF_NET)
    assert (root / "package_registry_pdf_net.json").read_bytes() == sidecar_content(
        "package_registry", PDF_NET
    )
    assert (root / "product_reference_slides_python.json").read_bytes() == sidecar_content(
        "product_reference", SLIDES_PY
    )


def test_compaction_ran_on_what_was_written(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 0, out
    path = root / scope_of(PDF_NET) / f"generation__{VERSION}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    points = doc["payload"]["vector_index"]["points"]
    assert len(points) == 2 and all(EXAMPLE_MARKER in p["text"] for p in points)
    assert set(doc["payload"]["lexical_index"]["documents"]) == {"c0", "c1", "c2"}
    raw_bytes = len(json.dumps(generation_doc(PDF_NET), indent=2).encode("utf-8"))
    assert path.stat().st_size < raw_bytes
    assert path.read_bytes() == compact.render(doc)  # canonical compact serialization
    assert compact.main(["--check", "--root", str(root)]) == 0


def test_only_get_pods_and_exec_tar_ever_run(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 0, out
    assert cluster.calls == [
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            NS,
            "-l",
            "app.kubernetes.io/instance=foss-mcp-pdf-net,app.kubernetes.io/component=serving",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "exec",
            "-n",
            NS,
            pod_of(PDF_NET),
            "-c",
            "serving",
            "--",
            "tar",
            "cf",
            "-",
            "-C",
            "/data/manifests",
            ".",
        ],
    ]
    mount_path = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))["manifests"]["mountPath"]
    assert snap.MANIFESTS_MOUNT == mount_path


def test_the_command_guard_refuses_any_other_kubectl_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("subprocess.run must not be reached")

    monkeypatch.setattr(snap.subprocess, "run", explode)
    for cmd in (
        ["kubectl", "delete", "pod", "x"],
        ["kubectl", "get", "secrets", "-n", NS],
        ["kubectl", "exec", "-n", NS, "p", "-c", "serving", "--", "cat", "/etc/passwd"],
        ["helm", "list"],
    ):
        with pytest.raises(snap.SnapshotError):
            snap._run(cmd, 1)


# ---------------------------------------------------------------------------------------------------
# (b) pod health
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "ready", "terminating"),
    [
        ("Pending", False, False),
        ("Running", False, False),
        ("Failed", False, False),
        ("Succeeded", True, False),
        ("Running", True, True),
    ],
)
def test_a_pod_that_is_not_running_and_ready_fails_and_writes_nothing(
    cluster: FakeCluster,
    root: Path,
    capsys: pytest.CaptureFixture[str],
    phase: str,
    ready: bool,
    terminating: bool,
) -> None:
    cluster.add(PDF_NET, phase=phase, ready=ready, terminating=terminating)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "pdf_net: FAILED no serving pod is Running and Ready" in out
    assert cluster.exec_calls() == []  # never even read from an unhealthy pod
    assert_nothing_written(root)


def test_a_pod_missing_from_the_selector_fails(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)
    cluster.remove(PDF_NET)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "pdf_net: FAILED no serving pod matches app.kubernetes.io/instance=foss-mcp-pdf-net" in out
    assert cluster.exec_calls() == []
    assert_nothing_written(root)


def test_a_ready_pod_is_chosen_over_an_unready_one(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster.add(PDF_NET)
    good = cluster.pods[release_of(PDF_NET)]
    bad = {
        "metadata": {"name": "foss-mcp-pdf-net-0-aaaaa"},
        "status": {"phase": "Pending", "conditions": [{"type": "Ready", "status": "False"}]},
    }

    def two_pods(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if cmd[:3] == ["kubectl", "get", "pods"]:
            cluster.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"items": [bad, good]}).encode(), b"")
        return cluster(cmd, **kwargs)

    monkeypatch.setattr(snap.subprocess, "run", two_pods)
    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 0, out
    assert [c[4] for c in cluster.exec_calls()] == [pod_of(PDF_NET)]


@pytest.mark.parametrize("mode", ["exec-fails", "kubectl-missing", "garbage-tar", "empty-stream"])
def test_a_failing_kubectl_or_unreadable_stream_fails_and_writes_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    cluster.add(PDF_NET)
    if mode == "exec-fails":
        cluster.exec_result[pod_of(PDF_NET)] = (1, b"error: unable to upgrade connection")
    elif mode == "kubectl-missing":
        cluster.missing_kubectl = True
    elif mode == "garbage-tar":
        cluster.tars[pod_of(PDF_NET)] = b"this is not a tar archive" * 100
    else:
        cluster.tars[pod_of(PDF_NET)] = b""

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "pdf_net: FAILED" in out
    assert_nothing_written(root)


# ---------------------------------------------------------------------------------------------------
# (c) real content
# ---------------------------------------------------------------------------------------------------


def _generation_with(mutate: Any) -> bytes:
    doc = generation_doc(PDF_NET)
    mutate(doc)
    return json.dumps(doc, indent=2).encode("utf-8")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["payload"]["lexical_index"].update(documents={}),
        lambda d: d["payload"]["lexical_index"].pop("documents"),
        lambda d: d["payload"]["lexical_index"].update(documents=["c0"]),
        lambda d: d["payload"].pop("lexical_index"),
        lambda d: d["payload"].update(lexical_index=None),
    ],
    ids=["empty-mapping", "no-documents-key", "list-not-mapping", "no-lexical-index", "null-lexical-index"],
)
def test_a_well_formed_but_empty_lexical_index_fails(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], mutate: Any
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, generation=_generation_with(mutate)))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "lexical index is empty" in out
    assert_nothing_written(root)


@pytest.mark.parametrize(
    ("what", "kwargs"),
    [
        ("no pointer", {"omit": (f"./{scope_of(PDF_NET)}/active_pointer.json",)}),
        (
            "pointer names a generation that was not shipped",
            {"omit": (f"./{scope_of(PDF_NET)}/generation__{VERSION}.json",)},
        ),
        ("pointer is not json", {"pointer": b"not json"}),
        ("pointer has no generation_id", {"pointer": b"{}"}),
        (
            "pointer names another scope",
            {"pointer": json.dumps({"generation_id": generation_id_of(PDF_GO)}).encode("utf-8")},
        ),
        (
            "pointer version is not a safe file name",
            {
                "pointer": json.dumps({"generation_id": "pdf::net::self_extracted::../../evil"}).encode(
                    "utf-8"
                )
            },
        ),
        ("generation is not json", {"generation": b"{{{"}),
        ("generation has no payload", {"generation": b'{"generation_id": "x"}'}),
    ],
)
def test_an_unusable_pointer_or_generation_fails_and_writes_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], what: str, kwargs: dict[str, Any]
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, **kwargs))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1, what
    assert "pdf_net: FAILED" in out, what
    assert_nothing_written(root)


# ---------------------------------------------------------------------------------------------------
# (d) sidecars
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("label", SIDECAR_LABELS)
def test_each_missing_sidecar_fails_the_pilot_and_is_named(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, drop_sidecars=(label,)))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert f"pdf_net: FAILED {scope_of(PDF_NET)}: missing sidecar(s) {sidecar_name_of(label, PDF_NET)}" in out
    assert_nothing_written(root)


@pytest.mark.parametrize("label", SIDECAR_LABELS)
def test_each_missing_sidecar_passes_with_allow_missing_sidecars_and_is_reported(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, drop_sidecars=(label,)))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--allow-missing-sidecars")

    assert rc == 0, out
    assert (root / scope_of(PDF_NET) / f"generation__{VERSION}.json").is_file()
    for other in SIDECAR_LABELS:
        path = root / sidecar_name_of(other, PDF_NET)
        assert path.exists() is (other != label)
    assert f"{label}=MISSING" in out


def test_all_three_missing_sidecars_are_named_together(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, drop_sidecars=SIDECAR_LABELS))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    for label in SIDECAR_LABELS:
        assert sidecar_name_of(label, PDF_NET) in out
    assert_nothing_written(root)


@pytest.mark.parametrize("bad", [b"", b"not json", b"\xff\xfe"])
def test_an_empty_or_unparseable_sidecar_fails(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], bad: bytes
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, sidecar_override={"recent_releases": bad}))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert f"sidecar {sidecar_name_of('recent_releases', PDF_NET)} is not valid JSON" in out
    assert_nothing_written(root)


def _load_infra_function(filename: str, function: str) -> Any:
    path = REPO_ROOT / "infra" / filename
    spec = importlib.util.spec_from_file_location(f"_tc349_test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function)


def test_sidecar_names_equal_what_the_repos_own_helpers_return_for_every_pilot() -> None:
    product_reference = _load_infra_function("fetch_product_reference.py", "sidecar_name")
    recent_releases = _load_infra_function("fetch_recent_releases.py", "recent_releases_sidecar_name")
    package_registry = _load_infra_function(
        "verify_package_registry.py", "verify_package_registry_sidecar_name"
    )
    chart_pilots = pilots.load_pilots()
    assert len(chart_pilots) == pilots.PILOT_COUNT
    for pilot in chart_pilots:
        family, platform = pilot["family"], pilot["platform"]
        assert snap.sidecar_names(family, platform) == {
            "product_reference": product_reference(family, platform),
            "recent_releases": recent_releases(family, platform),
            "package_registry": package_registry(family, platform),
        }


def test_the_tool_never_retypes_a_sidecar_name_pattern() -> None:
    tree_ = ast.parse(TOOL.read_text(encoding="utf-8"))
    pattern = re.compile(r"^(product_reference|recent_releases|package_registry)_.*(\.json|[{<%])")
    literals = [
        node.value
        for node in ast.walk(tree_)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert not [text for text in literals if pattern.match(text)]


# ---------------------------------------------------------------------------------------------------
# (e) archive safety
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("member", "reason"),
    [
        (("file", "/etc/cron.d/evil", b"x"), "absolute path"),
        (("file", "C:/Windows/evil.txt", b"x"), "absolute path"),
        (("file", "C:evil.txt", b"x"), "absolute path"),
        (("file", "../escaped.txt", b"x"), "'..' path component"),
        (("file", "./pdf__net__self_extracted/../../escaped.txt", b"x"), "'..' path component"),
        (("file", "pdf__net__self_extracted\\evil.json", b"x"), "backslash"),
        (("sym", "./pdf__net__self_extracted/link", "/etc/passwd"), "link member"),
        (("sym", "./link-inside", "pdf__net__self_extracted"), "link member"),
        (("hard", "./hard", "pdf__net__self_extracted/active_pointer.json"), "link member"),
    ],
    ids=[
        "absolute",
        "drive-absolute",
        "drive-relative",
        "dotdot",
        "nested-dotdot",
        "backslash",
        "symlink-outside",
        "symlink-inside",
        "hardlink",
    ],
)
def test_an_unsafe_archive_is_refused_and_writes_nothing(
    cluster: FakeCluster,
    root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    member: tuple[str, str, Any],
    reason: str,
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, extra=(member,)))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "pdf_net: FAILED unsafe archive member" in out and reason in out
    assert_nothing_written(root)
    assert not (tmp_path / "escaped.txt").exists()
    assert not (root.parent / "escaped.txt").exists()


def test_a_non_regular_member_is_refused(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    buf = io.BytesIO(pilot_tar(PDF_NET))
    out_buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="r:") as source, tarfile.TarFile(fileobj=out_buf, mode="w") as target:
        for member in source.getmembers():
            target.addfile(member, source.extractfile(member) if member.isreg() else None)
        fifo = tarfile.TarInfo("./a-fifo")
        fifo.type = tarfile.FIFOTYPE
        target.addfile(fifo)
    cluster.add(PDF_NET, out_buf.getvalue())

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "not a regular file or directory" in out
    assert_nothing_written(root)


def test_the_tool_unpacks_in_memory_only() -> None:
    """Nothing is ever extracted to disk: extract/extractall are not called anywhere in the tool."""
    source = TOOL.read_text(encoding="utf-8")
    assert not re.search(r"\.extract(all)?\(", source)
    assert "tarfile.data_filter" in source


# ---------------------------------------------------------------------------------------------------
# (f) per-pilot atomicity, idempotency, selection
# ---------------------------------------------------------------------------------------------------


def test_a_failed_pilot_keeps_its_previous_files_while_the_others_still_succeed(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)
    cluster.add(PDF_GO)
    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--only", "pdf_go")
    assert rc == 0, out
    net_before = pilot_files(root, PDF_NET)
    go_before = pilot_files(root, PDF_GO)
    assert net_before and go_before

    # pdf_net now fails its health check; pdf_go has a newer generation and different chunk count
    new_version = "20261011T000000000000Z-cccc3333"
    cluster.add(PDF_NET, phase="Pending", ready=False)
    cluster.add(PDF_GO, pilot_tar(PDF_GO, version=new_version, chunks=5))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--only", "pdf_go")

    assert rc == 1
    assert "pdf_net: FAILED" in out and "pdf_go: ok [written]" in out and "chunks=5" in out
    assert pilot_files(root, PDF_NET) == net_before  # byte-identical
    go_after = pilot_files(root, PDF_GO)
    assert go_after != go_before
    assert f"{scope_of(PDF_GO)}/generation__{new_version}.json" in {k.replace("\\", "/") for k in go_after}
    assert f"{scope_of(PDF_GO)}/generation__{VERSION}.json" not in {k.replace("\\", "/") for k in go_after}


def test_a_pilot_that_fails_validation_after_a_good_run_leaves_every_file_byte_identical(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)
    assert run_tool(capsys, root, "--only", "pdf_net")[0] == 0
    before = stat_tree(root), tree(root)

    for broken in (
        pilot_tar(PDF_NET, version="20261012T000000000000Z-dddd4444", drop_sidecars=("package_registry",)),
        pilot_tar(PDF_NET, version="20261012T000000000000Z-dddd4444", extra=(("sym", "./l", "/etc"),)),
        pilot_tar(
            PDF_NET,
            version="20261012T000000000000Z-dddd4444",
            generation=_generation_with(lambda d: d["payload"]["lexical_index"].update(documents={})),
            pointer=pointer_bytes(PDF_NET, "20261012T000000000000Z-dddd4444"),
        ),
    ):
        cluster.add(PDF_NET, broken)
        rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")
        assert rc == 1 and "pdf_net: FAILED" in out
        assert (stat_tree(root), tree(root)) == before


def test_a_second_run_changes_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)
    cluster.add(SLIDES_PY)
    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--only", "slides_python")
    assert rc == 0, out
    assert out.count("[written]") == 2
    first = stat_tree(root), tree(root)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--only", "slides_python")

    assert rc == 0, out
    assert out.count("[unchanged]") == 2 and "[written]" not in out
    assert (stat_tree(root), tree(root)) == first  # bytes and modification times alike
    assert [p.name for p in root.iterdir() if p.name.startswith(".")] == []  # no stage directory left behind


def test_only_selects_the_named_pilots(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for pilot in (PDF_NET, PDF_GO, SLIDES_PY):
        cluster.add(pilot)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_go", "--only", "slides_python")

    assert rc == 0, out
    assert {c[cluster_selector_index(c)] for c in cluster.get_calls()} == {
        "app.kubernetes.io/instance=foss-mcp-pdf-go,app.kubernetes.io/component=serving",
        "app.kubernetes.io/instance=foss-mcp-slides-python,app.kubernetes.io/component=serving",
    }
    assert sorted(c[4] for c in cluster.exec_calls()) == sorted([pod_of(PDF_GO), pod_of(SLIDES_PY)])
    assert not (root / scope_of(PDF_NET)).exists()
    assert (root / scope_of(PDF_GO)).is_dir() and (root / scope_of(SLIDES_PY)).is_dir()


def cluster_selector_index(cmd: list[str]) -> int:
    return cmd.index("-l") + 1


def test_an_unknown_only_name_exits_2_and_runs_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run_tool(capsys, root, "--only", "pdf_net", "--only", "not_a_pilot")

    assert rc == 2
    assert "unknown pilot(s): not_a_pilot" in err
    assert cluster.calls == []
    assert not root.exists()


@pytest.mark.parametrize("namespace", ["", "Foss", "a b", "x;y", "bad-", "a" * 3 + "_b"])
def test_an_invalid_namespace_exits_2_and_runs_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], namespace: str
) -> None:
    rc, _, err = run_tool_namespace(capsys, root, namespace)

    assert rc == 2
    assert "not a valid Kubernetes namespace" in err
    assert cluster.calls == []
    assert not root.exists()


def run_tool_namespace(
    capsys: pytest.CaptureFixture[str], root: Path, namespace: str
) -> tuple[int, str, str]:
    rc = snap.main(["--namespace", namespace, "--root", str(root)])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_dry_run_reads_and_validates_but_writes_nothing(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--dry-run")

    assert rc == 0, out
    assert "pdf_net: ok [dry-run]" in out and "(dry run, nothing written)" in out
    assert not root.exists()
    assert len(cluster.exec_calls()) == 1

    # and a dry run never touches an existing tree, even when the cluster has moved on
    assert run_tool(capsys, root, "--only", "pdf_net")[0] == 0
    before = stat_tree(root), tree(root)
    cluster.add(PDF_NET, pilot_tar(PDF_NET, version="20261013T000000000000Z-eeee5555", chunks=9))
    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--dry-run")
    assert rc == 0 and "chunks=9" in out
    assert (stat_tree(root), tree(root)) == before


def test_a_dry_run_still_fails_an_invalid_pilot(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cluster.add(PDF_NET, pilot_tar(PDF_NET, drop_sidecars=("recent_releases",)))

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net", "--dry-run")

    assert rc == 1 and sidecar_name_of("recent_releases", PDF_NET) in out


# ---------------------------------------------------------------------------------------------------
# (g) the TC-346 compaction runs over --root, and every pilot of the chart is covered
# ---------------------------------------------------------------------------------------------------


def _write_uncompacted_scope(root: Path, name: str) -> Path:
    family, platform, source_kind = name.split("__")
    pilot = {"family": family, "platform": platform, "sourceKind": source_kind}
    scope_dir = root / name
    scope_dir.mkdir(parents=True)
    (scope_dir / "active_pointer.json").write_bytes(pointer_bytes(pilot))
    (scope_dir / f"generation__{VERSION}.json").write_bytes(
        json.dumps(generation_doc(pilot), indent=2).encode("utf-8")
    )
    return scope_dir


def test_the_final_compaction_runs_over_the_whole_root(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    foreign = _write_uncompacted_scope(root, "zzz__lang__self_extracted")
    cluster.add(PDF_NET)

    rc, out, _ = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 0, out
    doc = json.loads((foreign / f"generation__{VERSION}.json").read_text(encoding="utf-8"))
    assert len(doc["payload"]["vector_index"]["points"]) == 2
    assert compact.main(["--check", "--root", str(root)]) == 0


def test_a_broken_scope_in_the_root_fails_the_run_after_the_pilots_were_written(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / "broken__scope__self_extracted").mkdir(parents=True)  # no pointer
    cluster.add(PDF_NET)

    rc, out, err = run_tool(capsys, root, "--only", "pdf_net")

    assert rc == 1
    assert "compaction failed" in err and "broken__scope__self_extracted" in err
    assert "pdf_net: ok [written]" in out


def test_every_pilot_in_the_chart_is_snapshotted(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chart_pilots = pilots.load_pilots()
    count = len(chart_pilots)
    assert count == pilots.PILOT_COUNT
    for pilot in chart_pilots:
        cluster.add(pilot)

    rc, out, err = run_tool(capsys, root)

    assert rc == 0, (out, err)
    scope_dirs = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert scope_dirs == sorted(scope_of(p) for p in chart_pilots)
    for pilot in chart_pilots:
        scope_dir = root / scope_of(pilot)
        assert sorted(p.name for p in scope_dir.iterdir()) == [
            "active_pointer.json",
            f"generation__{VERSION}.json",
        ]
        for label in SIDECAR_LABELS:
            assert (root / sidecar_name_of(label, pilot)).read_bytes() == sidecar_content(label, pilot)
    assert len([p for p in root.iterdir() if p.is_file()]) == 3 * count
    assert out.count(": ok [written]") == count
    assert f"total: {count} of {count} pilot(s) ok, 0 failed" in out
    assert len(cluster.exec_calls()) == len(cluster.get_calls()) == count
    assert_only_allowed_commands(cluster.calls)


# ---------------------------------------------------------------------------------------------------
# (h) no environment value is ever printed; the tool is wired and runs as a script
# ---------------------------------------------------------------------------------------------------


def test_no_environment_value_appears_in_any_output(
    cluster: FakeCluster, root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = {
        "KUBECONFIG": "tc349-sentinel-kubeconfig-path",
        "GITHUB_TOKEN": "tc349-sentinel-github-token",
        "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY": "tc349-sentinel-api-key",
        "KUBE_TOKEN": "tc349-sentinel-kube-token",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    cluster.add(PDF_NET)
    cluster.add(PDF_GO, phase="Pending", ready=False)
    cluster.add(SLIDES_PY, pilot_tar(SLIDES_PY, extra=(("sym", "./l", "/etc"),)))

    rc, out, err = run_tool(capsys, root, "--only", "pdf_net", "--only", "pdf_go", "--only", "slides_python")
    rc2, out2, err2 = run_tool(capsys, root, "--only", "pdf_net", "--dry-run")

    assert rc == 1 and rc2 == 0
    combined = out + err + out2 + err2
    for value in secrets.values():
        assert value not in combined
    source = TOOL.read_text(encoding="utf-8")
    assert not re.search(r"\bos\.environ\b|\bgetenv\b|\benviron\b", source)


def test_the_tool_is_wired_to_the_real_compaction_and_pilot_helpers() -> None:
    """A grep over committed source (AGENTS.md: a green unit test never proves a call site exists)."""
    source = TOOL.read_text(encoding="utf-8")
    assert "import compact_demo_manifests as compact" in source
    assert "compact.compact_scope(" in source
    assert "compact.scope_dirs(" in source
    assert "pilots.select_pilots(" in source and "pilots.load_pilots(" in source
    assert "install_pilots.INSTANCE_LABEL" in source and "install_pilots.COMPONENT_LABEL" in source
    assert 'if __name__ == "__main__":' in source and "sys.exit(main())" in source


def test_the_script_runs_standalone_and_rejects_an_unknown_pilot(tmp_path: Path) -> None:
    """A real process (nothing mocked): imports resolve as a plain script, and exit 2 comes before any kubectl."""
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--namespace",
            "foss-mcp",
            "--only",
            "not_a_pilot",
            "--root",
            str(tmp_path / "r"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    assert "unknown pilot(s): not_a_pilot" in proc.stderr
    assert not (tmp_path / "r").exists()
