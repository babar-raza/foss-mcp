"""TC-498: every pilot has a committed, valid, empty seed verification-cache document.

docker-compose.yml and the Helm chart both pass ``--verification-cache /app/fixtures/verified_examples/<family>_<platform>.json``
to build_chunks.py, and the ingestion image bakes tests/fixtures/ in at /app/fixtures/. The file each pilot names must exist
in the repository (the persistent starting point scripts/release/pull_verification_cache.py later merges real verdicts into)
and must be accepted by the project's own loader. The pilot list is derived from docker-compose.yml, never retyped; the
chart side is rendered with the real helm binary (a missing helm binary FAILS, it never skips).
"""

from __future__ import annotations

import re
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
# re-pinned by TC-551: 40 -> 32
EXPECTED_PILOT_COUNT = 32
# TC-548: the platform in the FILE name (<family>_<platform>.json, the compose/chart service name) versus the platform
# value the verifiers write into a cache entry (infra/build_chunks.py keys its toolchains by "dotnet"). Only the .NET
# pilots differ; every other file-name platform is written as itself.
CACHE_PLATFORM_FOR_FILE_PLATFORM = {"net": "dotnet"}


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


_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@pytest.mark.parametrize(("service", "path"), _pilot_cases())
def test_seed_file_exists_and_parses_with_the_project_loader(service: str, path: str) -> None:
    seed = SEED_DIR / _seed_name(path)
    assert seed.is_file(), f"{seed} does not exist for {service}"
    entries = build_chunks.load_verification_cache(seed)
    # TC-547: the file used to be forced to hold zero entries (the TC-499 empty-seed guard), which also forbade the
    # verdicts the seed exists to receive from the cluster run. A committed cache may hold entries; what it must
    # always satisfy is the structure below. With an empty file this passes vacuously after the loader check.
    family, _, platform = seed.name.removesuffix(".json").partition("_")
    assert family and platform and "_" not in platform, f"{seed.name} is not <family>_<platform>.json"
    keys = []
    for entry in entries:
        expected_platform = CACHE_PLATFORM_FOR_FILE_PLATFORM.get(platform, platform)
        assert entry["platform"] == expected_platform, (
            f"{seed.name}: entry platform {entry['platform']!r} != {expected_platform!r}"
        )
        assert isinstance(entry["library_commit"], str) and _HEX40.fullmatch(entry["library_commit"]), (
            f"{seed.name}: library_commit is not 40 lowercase hex"
        )
        assert isinstance(entry["code_sha256"], str) and _HEX64.fullmatch(entry["code_sha256"]), (
            f"{seed.name}: code_sha256 is not 64 lowercase hex"
        )
        assert isinstance(entry["verified"], bool), f"{seed.name}: verified is not a boolean"
        assert isinstance(entry["toolchain"], str) and entry["toolchain"].strip(), (
            f"{seed.name}: toolchain is not a non-empty string"
        )
        keys.append(
            (
                entry["provenance"],
                entry["code_sha256"],
                entry["library_commit"],
                entry["platform"],
                entry["toolchain"],
            )
        )
    assert len(keys) == len(set(keys)), f"{seed.name}: entries are not unique by the five-field cache key"
    assert entries == build_chunks.merge_verification_cache([], entries), (
        f"{seed.name}: entries are not sorted the way merge_verification_cache sorts them"
    )


def test_the_platform_map_covers_every_cache_platform_present() -> None:
    file_platforms = {
        Path(path).name.removesuffix(".json").partition("_")[2] for path in _compose_cache_paths().values()
    }
    allowed = set(CACHE_PLATFORM_FOR_FILE_PLATFORM.values()) | (
        file_platforms - set(CACHE_PLATFORM_FOR_FILE_PLATFORM)
    )
    present = {
        entry["platform"]
        for seed in SEED_DIR.glob("*.json")
        for entry in build_chunks.load_verification_cache(seed)
    }
    assert present <= allowed, sorted(present - allowed)


@pytest.mark.parametrize(("service", "path"), _pilot_cases())
def test_seed_file_is_the_canonical_deterministic_empty_document(service: str, path: str) -> None:
    # TC-547: the name is kept (a rename would hide the history), but the test now pins the canonical form of the
    # pilot's OWN entries, empty or populated: byte-identical to verification_cache_document(entries), so a
    # pull from the cluster is deterministic and a second pull writes nothing.
    seed = SEED_DIR / _seed_name(path)
    raw = seed.read_bytes()
    assert b"\r" not in raw, f"{seed} must use LF line endings"
    entries = build_chunks.load_verification_cache(seed)
    assert raw == build_chunks.verification_cache_document(entries).encode("utf-8"), (
        f"{seed} is not byte-identical to build_chunks.verification_cache_document(its own entries)"
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
