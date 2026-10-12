"""G3/TC-391 (REQ-G3-006): the server says which build it is and where to start.

``initialize`` used to report ``serverInfo.version`` "0.0.0" and no ``instructions``. The release
build stamps the exact commit into ``src/foss_mcp/BUILD_REVISION`` of the exported tree (an image
label cannot be read from inside a container), ``server_version`` turns it into a version, and
``create_server`` reports it with fixed instructions. Offline: no docker, no network; the
repository used for the export is a throw-away one created in ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import SERVER_INSTRUCTIONS, server_version

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

HEX40 = "0123456789abcdef0123456789abcdef01234567"
HEX64 = "fedcba9876543210" * 4

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def _load_build_images():
    spec = importlib.util.spec_from_file_location(
        "build_images_under_test", REPO_ROOT / "scripts" / "release" / "build_images.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- server_version ---------------------------------------------------------------------------


def test_server_version_without_a_file_is_unknown(tmp_path: Path) -> None:
    assert server_version(tmp_path / "BUILD_REVISION") == "0.1.0+unknown"


def test_server_version_with_a_40_hex_revision(tmp_path: Path) -> None:
    path = tmp_path / "BUILD_REVISION"
    path.write_text(HEX40 + "\n", encoding="ascii")
    assert server_version(path) == "0.1.0+" + HEX40[:12]


def test_server_version_with_a_64_hex_revision(tmp_path: Path) -> None:
    path = tmp_path / "BUILD_REVISION"
    path.write_text(HEX64 + "\n", encoding="ascii")
    assert server_version(path) == "0.1.0+" + HEX64[:12]


@pytest.mark.parametrize(
    "content",
    ["", "garbage\n", HEX40[:-1], HEX40 + "0", "z" * 40, HEX40 + "\n" + HEX40, "<script>" + HEX40],
)
def test_server_version_with_garbage_is_unknown(tmp_path: Path, content: str) -> None:
    path = tmp_path / "BUILD_REVISION"
    path.write_text(content, encoding="ascii")
    assert server_version(path) == "0.1.0+unknown"


def test_server_version_with_undecodable_bytes_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "BUILD_REVISION"
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert server_version(path) == "0.1.0+unknown"


def test_default_revision_file_sits_next_to_the_package() -> None:
    package_dir = Path(server_module.__file__).resolve().parents[1]
    assert package_dir.name == "foss_mcp"
    assert server_module.BUILD_REVISION_FILENAME == "BUILD_REVISION"
    # The working tree never carries the stamp: only the exported release tree does.
    assert not (package_dir / "BUILD_REVISION").exists()
    assert server_version() == "0.1.0+unknown"


# --- initialize -------------------------------------------------------------------------------


def _initialize(tmp_path: Path) -> dict:
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=[],
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers=HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        body = data[-1]
    return json.loads(body)["result"]


def test_initialize_reports_the_stamped_version_and_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / "BUILD_REVISION"
    stamp.write_text(HEX40 + "\n", encoding="ascii")
    real = server_module.server_version
    monkeypatch.setattr(server_module, "server_version", lambda: real(stamp))
    result = _initialize(tmp_path)
    assert result["serverInfo"]["name"] == "foss-mcp"
    assert result["serverInfo"]["version"] == "0.1.0+" + HEX40[:12]
    assert result["instructions"] == SERVER_INSTRUCTIONS


def test_initialize_without_a_stamp_reports_unknown_and_never_0_0_0(tmp_path: Path) -> None:
    result = _initialize(tmp_path)
    assert result["serverInfo"]["version"] == "0.1.0+unknown"
    assert "0.0.0" not in result["serverInfo"]["version"]
    assert result["instructions"]


def test_instructions_name_the_starting_tools_and_stay_short() -> None:
    assert len(SERVER_INSTRUCTIONS) <= 800
    for name in ("lookup", "search_symbols", "get_symbol", "find_examples", "report_index_freshness"):
        assert name in SERVER_INSTRUCTIONS, name
    assert "fixed by the deployment" in SERVER_INSTRUCTIONS
    assert "honest answer" in SERVER_INSTRUCTIONS
    assert "\n" not in SERVER_INSTRUCTIONS


# --- build_images writes BUILD_REVISION -------------------------------------------------------


def _vcs(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        check=True,
        text=True,
    )
    return proc.stdout.strip()


def test_build_images_stamps_the_export_directory_and_not_the_working_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert shutil.which("git") is not None, "git is a declared requirement of this card"
    repo = tmp_path / "repo"
    (repo / "src" / "foss_mcp").mkdir(parents=True)
    (repo / "src" / "foss_mcp" / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _vcs(repo, "add", "-A")
    _vcs(repo, "commit", "-q", "-m", "init")
    sha = _vcs(repo, "rev-parse", "HEAD")
    assert len(sha) in (40, 64)

    build_images = _load_build_images()
    monkeypatch.setattr(build_images, "REPO_ROOT", repo)
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    build_images.export_head(sha, export_dir)
    assert not (export_dir / "src" / "foss_mcp" / "BUILD_REVISION").exists()

    build_images.write_build_revision(sha, export_dir)

    stamp = export_dir / "src" / "foss_mcp" / "BUILD_REVISION"
    assert stamp.read_bytes() == sha.encode("ascii") + b"\n"
    assert server_version(stamp) == "0.1.0+" + sha[:12]
    assert not (repo / "src" / "foss_mcp" / "BUILD_REVISION").exists()


def test_main_stamps_the_revision_after_export_and_before_the_image_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_images = _load_build_images()
    calls: list[str] = []
    seen_stamp: list[bytes] = []
    monkeypatch.setattr(build_images, "head_sha", lambda: HEX40)
    monkeypatch.setattr(build_images, "refuse_if_dirty", lambda: None)
    monkeypatch.setattr(build_images.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(build_images.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(build_images.shutil, "rmtree", lambda *a, **k: None)

    def fake_export(sha: str, dest: Path) -> None:
        calls.append("export")
        (dest / "src" / "foss_mcp").mkdir(parents=True)

    def fake_build(name, dockerfile, context, sha, tag, target) -> None:
        calls.append("build")
        seen_stamp.append((context / "src" / "foss_mcp" / "BUILD_REVISION").read_bytes())

    monkeypatch.setattr(build_images, "export_head", fake_export)
    monkeypatch.setattr(build_images, "build_image", fake_build)
    assert build_images.main(["--tag", "t"]) == 0
    assert calls == ["export", "build", "build"]
    assert seen_stamp == [HEX40.encode("ascii") + b"\n"] * 2
