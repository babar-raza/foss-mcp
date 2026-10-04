"""G2/TC-172: pdf/cpp's ingest-pdf-cpp -> serving-pdf-cpp wiring, asserted offline.

Parses docker-compose.yml with PyYAML (yaml.safe_load resolves anchors such as <<: *serving-base
itself) and asserts the shared-volume contract between the one-shot ingest service and the
serving service, the unchanged host port, and that infra/build_chunks.py really runs against
pdf/cpp's committed API-surface fixture with no library flags.

Offline only: no docker, no network. The build_chunks run is a real subprocess, never a mock.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
BUILD_CHUNKS = REPO_ROOT / "infra" / "build_chunks.py"
CPP_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pdf_cpp" / "api_surface.json"
VOLUME = "manifests-pdf-cpp"


@pytest.fixture(scope="module")
def compose() -> dict:
    with COMPOSE_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_ingest_pdf_cpp_service_publishes_pdf_cpp_held_by_its_own_ingestion(compose: dict) -> None:
    service = compose["services"]["ingest-pdf-cpp"]
    command = " ".join(service["command"])
    assert "--family pdf" in command, command
    assert "--platform cpp" in command, command
    assert "--held-by ingestion-pdf-cpp" in command, command
    assert "--library-platform" not in command, command
    assert "--furnished-page" not in command, command


def test_ingest_and_serving_share_the_same_declared_named_volume(compose: dict) -> None:
    ingest_mounts = compose["services"]["ingest-pdf-cpp"]["volumes"]
    serving_mounts = compose["services"]["serving-pdf-cpp"]["volumes"]
    assert f"{VOLUME}:/data/manifests" in ingest_mounts, ingest_mounts
    assert f"{VOLUME}:/data/manifests" in serving_mounts, serving_mounts
    assert VOLUME in compose["volumes"], sorted(compose["volumes"])


def test_serving_pdf_cpp_keeps_host_port_8082_to_container_8080(compose: dict) -> None:
    ports = compose["services"]["serving-pdf-cpp"]["ports"]
    assert "8082:8080" in ports, ports


def test_build_chunks_really_runs_against_the_pdf_cpp_fixture_without_library_flags(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "pdf_cpp_chunks.json"
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_CHUNKS),
            "--api-surface",
            str(CPP_FIXTURE),
            "--title",
            "pdf/cpp API surface",
            "--max-types",
            "20",
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(payload.get("chunks"), list), payload.keys()
    assert len(payload["chunks"]) >= 1, "build_chunks produced no chunks from the pdf/cpp fixture"
