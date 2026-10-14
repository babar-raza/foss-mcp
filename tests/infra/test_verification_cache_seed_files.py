"""TC-498: every pilot has a committed, valid, empty seed verification-cache document.

docker-compose.yml and the Helm chart both pass ``--verification-cache /app/fixtures/verified_examples/<family>_<platform>.json``
to build_chunks.py, and the ingestion image bakes tests/fixtures/ in at /app/fixtures/. The file each pilot names must exist
in the repository (the persistent starting point scripts/release/pull_verification_cache.py later merges real verdicts into)
and must be accepted by the project's own loader. The pilot list is derived from docker-compose.yml, never retyped; the
chart side is rendered with the real helm binary (a missing helm binary FAILS, it never skips).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import infra.build_chunks as build_chunks

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
SEED_DIR = REPO_ROOT / "tests" / "fixtures" / "verified_examples"
IMAGE_DIR = "/app/fixtures/verified_examples/"
EXPECTED_PILOT_COUNT = 40


def _flag_value(tokens: list[str], flag: str) -> str | None:
    if flag not in tokens:
        return None
    return tokens[tokens.index(flag) + 1]


def _compose_cache_paths() -> dict[str, str]:
    """{service name: the --verification-cache path} for every compose ingest service that passes it."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    found: dict[str, str] = {}
    for name, service in services.items():
        if not name.startswith("ingest-"):
            continue
        path = _flag_value(shlex.split(service["command"][0]), "--verification-cache")
        if path is not None:
            found[name] = path
    return found


def _seed_name(path: str) -> str:
    assert path.startswith(IMAGE_DIR), f"{path} is not under {IMAGE_DIR}"
    return path[len(IMAGE_DIR) :]


def _pilot_cases() -> list[Any]:
    return [
        pytest.param(name, path, id=name.removeprefix("ingest-"))
        for name, path in sorted(_compose_cache_paths().items())
    ]


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. Source scripts/toolchain/activate.ps1, which adds the helm directory to PATH."
        )
    return helm


def _chart_cache_paths(tmp_path: Path) -> set[str]:
    """Every --verification-cache path the rendered chart passes, across all of values.yaml's pilots."""
    pilots = yaml.safe_load(VALUES.read_text(encoding="utf-8"))["ingestion"]["pilots"]
    values = tmp_path / "pilots.yaml"
    values.write_text(yaml.safe_dump({"ingestion": {"pilots": pilots}}), encoding="utf-8")
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", "image.tag=tc498", "-f", str(values)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    paths: set[str] = set()
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc.get("kind") != "Job":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for arg in container.get("args", []):
                path = _flag_value(shlex.split(arg), "--verification-cache")
                if path is not None:
                    paths.add(path)
    return paths


def test_there_is_one_compose_cache_path_per_pilot() -> None:
    paths = _compose_cache_paths()
    assert len(paths) == EXPECTED_PILOT_COUNT, sorted(paths)
    assert len(set(paths.values())) == EXPECTED_PILOT_COUNT


@pytest.mark.parametrize(("service", "path"), _pilot_cases())
def test_seed_file_exists_and_parses_with_the_project_loader(service: str, path: str) -> None:
    seed = SEED_DIR / _seed_name(path)
    assert seed.is_file(), f"{seed} does not exist for {service}"
    entries = build_chunks.load_verification_cache(seed)
    assert entries == [], f"{seed} is a seed file and must hold zero entries"


@pytest.mark.parametrize(("service", "path"), _pilot_cases())
def test_seed_file_is_the_canonical_deterministic_empty_document(service: str, path: str) -> None:
    seed = SEED_DIR / _seed_name(path)
    raw = seed.read_bytes()
    assert b"\r" not in raw, f"{seed} must use LF line endings"
    assert raw == build_chunks.verification_cache_document([]).encode("utf-8"), (
        f"{seed} is not byte-identical to build_chunks.verification_cache_document([])"
    )


def test_seed_files_equal_the_cache_paths_named_in_docker_compose() -> None:
    on_disk = {p.name for p in SEED_DIR.glob("*.json")}
    named = {_seed_name(path) for path in _compose_cache_paths().values()}
    assert on_disk == named, {"unreferenced": sorted(on_disk - named), "missing": sorted(named - on_disk)}


def test_seed_files_equal_the_cache_paths_the_rendered_chart_names(tmp_path: Path) -> None:
    on_disk = {p.name for p in SEED_DIR.glob("*.json")}
    named = {_seed_name(path) for path in _chart_cache_paths(tmp_path)}
    assert on_disk == named, {"unreferenced": sorted(on_disk - named), "missing": sorted(named - on_disk)}


def test_compose_and_rendered_chart_name_the_same_cache_paths(tmp_path: Path) -> None:
    assert set(_compose_cache_paths().values()) == _chart_cache_paths(tmp_path)
