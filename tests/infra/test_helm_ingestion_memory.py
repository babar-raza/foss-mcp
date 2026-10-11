"""TC-457: a pilot-level `ingestionMemoryLimit` replaces only the ingestion Job's memory limit.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.

Four pilots need more than the 4Gi default to finish their example verification: html/python,
page/python and words/net at 8Gi (measured by the 2026-10-11 diagnosis Jobs) and slides/cpp at 10Gi
(measured by its cluster run). The key lives at the PILOT level, never inside `library`, whose keys
are all forwarded to build_chunks.py as flags.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
JOB_TEMPLATE = CHART / "templates" / "ingestion-job.yaml"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
TAG = "tc457-test-tag"

sys.path.insert(0, str(DEPLOY_DIR))

import pilots as deploy_pilots  # noqa: E402

EXPECTED_MEMORY_LIMITS: dict[tuple[str, str], str] = {
    ("html", "python"): "8Gi",
    ("page", "python"): "8Gi",
    ("words", "net"): "8Gi",
    ("slides", "cpp"): "10Gi",
}

DEFAULT_LIMITS: dict[str, Any] = {
    "requests": {"cpu": "500m", "memory": "1Gi"},
    "limits": {"cpu": "2", "memory": "4Gi"},
}

KEY = "ingestionMemoryLimit"
MEMORY_PATTERN = re.compile(r"^[1-9][0-9]*Gi$")


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-457 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _render(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _all_pilots() -> list[dict[str, Any]]:
    return _values()["ingestion"]["pilots"]


def _key(pilot: dict[str, Any]) -> tuple[str, str]:
    return (pilot["family"], pilot["platform"])


def _jobs_by_pilot(docs: list[dict[str, Any]], pilot_list: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    jobs = [doc for doc in docs if doc.get("kind") == "Job"]
    by_pilot: dict[tuple[str, str], dict[str, Any]] = {}
    for pilot in pilot_list:
        suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
        matches = [job for job in jobs if job["metadata"]["name"].endswith(suffix)]
        assert len(matches) == 1, f"expected one Job for {pilot['family']}/{pilot['platform']}"
        by_pilot[_key(pilot)] = matches[0]
    assert len(by_pilot) == len(jobs), "the render has a Job that no pilot accounts for"
    return by_pilot


def _resources(job: dict[str, Any]) -> dict[str, Any]:
    return job["spec"]["template"]["spec"]["containers"][0]["resources"]


def _write_override(path: Path, pilot_list: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": pilot_list}}, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def shipped_jobs() -> dict[tuple[str, str], dict[str, Any]]:
    return _jobs_by_pilot(_render(), _all_pilots())


# (1) values.yaml


def test_exactly_the_four_pilots_carry_the_key_with_the_expected_values() -> None:
    carriers = {_key(p): p[KEY] for p in _all_pilots() if KEY in p}
    assert carriers == EXPECTED_MEMORY_LIMITS


@pytest.mark.parametrize("pilot_key", sorted(EXPECTED_MEMORY_LIMITS))
def test_each_value_is_a_whole_gibibyte_quantity(pilot_key: tuple[str, str]) -> None:
    pilot = next(p for p in _all_pilots() if _key(p) == pilot_key)
    assert MEMORY_PATTERN.match(pilot[KEY]), pilot[KEY]


def test_the_key_is_never_inside_library() -> None:
    offenders = [_key(p) for p in _all_pilots() if KEY in (p.get("library") or {})]
    assert offenders == []


# (2) the global block


def test_the_global_ingestion_resources_block_is_unchanged() -> None:
    assert _values()["ingestion"]["resources"] == DEFAULT_LIMITS


# (3) each carrier's Job


@pytest.mark.parametrize("pilot_key", sorted(EXPECTED_MEMORY_LIMITS))
def test_each_carrier_job_renders_its_own_memory_limit(
    shipped_jobs: dict[tuple[str, str], dict[str, Any]], pilot_key: tuple[str, str]
) -> None:
    assert _resources(shipped_jobs[pilot_key])["limits"]["memory"] == EXPECTED_MEMORY_LIMITS[pilot_key]


# (4) every other Job


def test_every_other_job_renders_the_default_memory_limit(
    shipped_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    others = {k: _resources(j)["limits"]["memory"] for k, j in shipped_jobs.items() if k not in EXPECTED_MEMORY_LIMITS}
    assert others, "no non-carrier pilots rendered"
    assert {k: v for k, v in others.items() if v != "4Gi"} == {}


# (5) requests and cpu stay global


def test_every_job_keeps_the_global_requests_and_cpu_limit(
    shipped_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    for key, job in shipped_jobs.items():
        res = _resources(job)
        assert res["requests"] == DEFAULT_LIMITS["requests"], key
        assert res["limits"]["cpu"] == "2", key
        assert set(res["limits"]) == {"cpu", "memory"}, key


# (6) the real install path


def test_the_per_release_values_file_for_slides_cpp_renders_10gi(tmp_path: Path) -> None:
    pilot = next(p for p in deploy_pilots.load_pilots() if _key(p) == ("slides", "cpp"))
    (path,) = deploy_pilots.write_values([pilot], tmp_path)
    docs = _render("-f", str(path))
    jobs = [doc for doc in docs if doc.get("kind") == "Job"]
    assert len(jobs) == 1
    res = _resources(jobs[0])
    assert res["limits"]["memory"] == "10Gi"
    assert res["limits"]["cpu"] == "2"
    assert res["requests"] == DEFAULT_LIMITS["requests"]


# (7) deleting the key falls back to the default


def test_deleting_the_key_from_slides_cpp_falls_back_to_the_default(tmp_path: Path) -> None:
    pilot_list = copy.deepcopy(_all_pilots())
    for pilot in pilot_list:
        if _key(pilot) == ("slides", "cpp"):
            del pilot[KEY]
    path = _write_override(tmp_path / "override_deleted.yaml", pilot_list)
    jobs = _jobs_by_pilot(_render("-f", str(path)), pilot_list)
    assert _resources(jobs[("slides", "cpp")])["limits"]["memory"] == "4Gi"
    assert _resources(jobs[("words", "net")])["limits"]["memory"] == "8Gi"


# (8) an override on another pilot does not leak


def test_an_override_on_pdf_net_applies_to_pdf_net_only(tmp_path: Path) -> None:
    pilot_list = copy.deepcopy(_all_pilots())
    for pilot in pilot_list:
        if _key(pilot) == ("pdf", "net"):
            pilot[KEY] = "6Gi"
    path = _write_override(tmp_path / "override_pdf_net.yaml", pilot_list)
    jobs = _jobs_by_pilot(_render("-f", str(path)), pilot_list)
    memory = {key: _resources(job)["limits"]["memory"] for key, job in jobs.items()}
    expected = {_key(p): EXPECTED_MEMORY_LIMITS.get(_key(p), "4Gi") for p in pilot_list}
    expected[("pdf", "net")] = "6Gi"
    assert memory == expected


def test_an_override_does_not_leak_into_the_jobs_rendered_after_it(tmp_path: Path) -> None:
    pilot_list = copy.deepcopy(_all_pilots())
    first = pilot_list[0]
    first[KEY] = "7Gi"
    path = _write_override(tmp_path / "override_first.yaml", pilot_list)
    jobs = _jobs_by_pilot(_render("-f", str(path)), pilot_list)
    for pilot in pilot_list[1:]:
        want = EXPECTED_MEMORY_LIMITS.get(_key(pilot), "4Gi") if KEY not in pilot else pilot[KEY]
        assert _resources(jobs[_key(pilot)])["limits"]["memory"] == want, _key(pilot)
    assert _resources(jobs[_key(first)])["limits"]["memory"] == "7Gi"


# (9) wiring on committed source


def test_the_template_reads_the_pilot_key_and_copies_the_global_block() -> None:
    text = JOB_TEMPLATE.read_text(encoding="utf-8")
    assert "$p.ingestionMemoryLimit" in text
    assert "deepCopy $.Values.ingestion.resources" in text
    assert "toYaml $.Values.ingestion.resources" not in text
