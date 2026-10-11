"""G3/TC-350: docker-compose.demo.yml, its generator, and the Dockerfile.serving stage structure. Offline.

Part (a) parses the COMMITTED docker-compose.demo.yml and pins one service per chart pilot, unique names
and host ports, and the memory/CPU limits and hardening every service carries. Part (b) exercises
scripts/release/generate_demo_compose.py, including that ``--check`` passes on the committed file and
fails on drift. Part (c) pins Dockerfile.serving: the ``demo`` stage runs the entrypoint, the ``runtime``
stage is byte-for-byte what it was, and the Dockerfile's last stage is still the bare ``FROM runtime``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
GENERATOR = RELEASE_DIR / "generate_demo_compose.py"
COMPOSE = REPO_ROOT / "docker-compose.demo.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile.serving"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
HELM_DEMO_DEPLOYMENT = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "templates" / "demo-deployment.yaml"

sys.path.insert(0, str(DEPLOY_DIR))
sys.path.insert(0, str(RELEASE_DIR))

import generate_demo_compose as generator  # noqa: E402
import pilots as chart_pilots  # noqa: E402

IMAGE_REFERENCE = "${FOSS_MCP_DEMO_IMAGE:?set FOSS_MCP_DEMO_IMAGE to the demo image reference}"
BASE_PORT = 8101
PORT_PATTERN = re.compile(r"^127\.0\.0\.1:\$\{(?P<var>[A-Z0-9_]+):-(?P<port>\d+)\}:8080$")


def chart_list() -> list[dict[str, Any]]:
    return chart_pilots.load_pilots()


def load_compose() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def services() -> dict[str, dict[str, Any]]:
    found = load_compose()["services"]
    assert isinstance(found, dict)
    return found


# ------------------------------------------------------------------- (a) the committed compose file


def test_the_committed_compose_file_is_plain_utf8_with_lf_newlines() -> None:
    raw = COMPOSE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    raw.decode("utf-8")


def test_the_compose_file_parses_and_names_its_project() -> None:
    compose = load_compose()
    assert compose["name"] == "foss-mcp-demo"
    assert set(compose) == {"name", "services"}


def test_there_is_exactly_one_service_per_chart_pilot_in_chart_order() -> None:
    expected = [f"{pilot['family']}-{pilot['platform']}" for pilot in chart_list()]
    assert len(expected) == len(set(expected)) == chart_pilots.PILOT_COUNT == 40
    assert list(services()) == expected


def test_every_service_selects_its_pilot_through_the_entrypoints_env_var() -> None:
    found = services()
    for pilot in chart_list():
        name = f"{pilot['family']}-{pilot['platform']}"
        assert found[name]["environment"] == {"FOSS_MCP_PILOT": f"{pilot['family']}/{pilot['platform']}"}, (
            name
        )


def test_every_service_runs_the_one_required_demo_image() -> None:
    for name, service in services().items():
        assert service["image"] == IMAGE_REFERENCE, name
        assert "build" not in service, name


def test_host_ports_are_unique_loopback_only_and_start_at_8101_in_chart_order() -> None:
    ports: list[int] = []
    variables: list[str] = []
    for name, service in services().items():
        assert len(service["ports"]) == 1, name
        match = PORT_PATTERN.match(service["ports"][0])
        assert match is not None, (name, service["ports"])
        ports.append(int(match["port"]))
        variables.append(match["var"])
    assert ports == list(range(BASE_PORT, BASE_PORT + len(ports)))
    assert len(set(ports)) == len(ports)
    assert len(set(variables)) == len(variables)
    for pilot, variable in zip(chart_list(), variables, strict=True):
        assert variable == f"FOSS_MCP_DEMO_PORT_{pilot['family']}_{pilot['platform']}".upper()


def test_every_service_carries_the_memory_and_cpu_limits() -> None:
    for name, service in services().items():
        assert service["mem_limit"] == "192m", name
        assert service["cpus"] == 0.25, name
        assert service["pids_limit"] == 256, name


def test_every_service_carries_the_hardening() -> None:
    for name, service in services().items():
        assert service["read_only"] is True, name
        assert service["tmpfs"] == ["/tmp"], name
        assert service["cap_drop"] == ["ALL"], name
        assert service["security_opt"] == ["no-new-privileges:true"], name
        assert service["restart"] == "unless-stopped", name
        assert "privileged" not in service and "cap_add" not in service and "volumes" not in service, name


def test_every_service_has_a_readyz_healthcheck() -> None:
    for name, service in services().items():
        check = service["healthcheck"]
        assert check["test"][:3] == ["CMD", "python", "-c"], name
        assert "http://localhost:8080/readyz" in check["test"][3], name
        assert {"interval", "timeout", "start_period", "retries"} <= set(check), name


def test_every_profile_set_is_the_family_plus_all() -> None:
    found = services()
    for pilot in chart_list():
        name = f"{pilot['family']}-{pilot['platform']}"
        profiles = found[name]["profiles"]
        assert pilot["family"] in profiles and "all" in profiles, name
        assert profiles == [pilot["family"], "all"], name
        for profile in profiles:
            assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", profile), (name, profile)


def test_every_service_has_the_same_keys() -> None:
    key_sets = {tuple(sorted(service)) for service in services().values()}
    assert len(key_sets) == 1, key_sets


def test_every_composed_pilot_is_baked_into_the_committed_demo_tree() -> None:
    for pilot in chart_list():
        scope = DEMO_MANIFESTS / f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"
        assert (scope / "active_pointer.json").is_file(), scope.name


# ------------------------------------------------------------------------------ (b) the generator


def test_the_generators_check_passes_on_the_committed_file() -> None:
    assert generator.main(["--check"]) == 0


def test_the_generators_check_passes_when_run_as_a_program(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generator_reproduces_the_committed_file_exactly(tmp_path: Path) -> None:
    target = tmp_path / "docker-compose.demo.yml"
    assert generator.main(["--output", str(target)]) == 0
    assert target.read_bytes() == COMPOSE.read_bytes()


def test_the_generators_check_fails_when_the_committed_file_drifts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "docker-compose.demo.yml"
    target.write_bytes(COMPOSE.read_bytes().replace(b"mem_limit: 192m", b"mem_limit: 512m", 1))
    assert generator.main(["--check", "--output", str(target)]) == 1
    assert "differs" in capsys.readouterr().err
    assert target.read_bytes().count(b"512m") == 1  # --check changed nothing


def test_the_generators_check_fails_when_the_file_is_missing(tmp_path: Path) -> None:
    assert generator.main(["--check", "--output", str(tmp_path / "absent.yml")]) == 1


def test_the_generators_check_passes_on_a_crlf_checkout(tmp_path: Path) -> None:
    target = tmp_path / "docker-compose.demo.yml"
    target.write_bytes(COMPOSE.read_bytes().replace(b"\n", b"\r\n"))
    assert generator.main(["--check", "--output", str(target)]) == 0


def write_values(path: Path, pilots: list[dict[str, str]]) -> Path:
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": pilots}}), encoding="utf-8")
    return path


def test_the_generator_follows_the_pilot_list_it_is_given(tmp_path: Path) -> None:
    values = write_values(
        tmp_path / "values.yaml",
        [
            {"family": "pdf", "platform": "go", "sourceKind": "self_extracted"},
            {"family": "cells", "platform": "rust", "sourceKind": "self_extracted"},
            {"family": "pdf", "platform": "net", "sourceKind": "self_extracted"},
        ],
    )
    target = tmp_path / "out.yml"
    assert generator.main(["--values", str(values), "--output", str(target)]) == 0
    compose = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert list(compose["services"]) == ["pdf-go", "cells-rust", "pdf-net"]
    host_ports = [PORT_PATTERN.match(svc["ports"][0])["port"] for svc in compose["services"].values()]
    assert host_ports == ["8101", "8102", "8103"]
    assert compose["services"]["cells-rust"]["profiles"] == ["cells", "all"]


def test_the_generator_refuses_a_duplicate_pilot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    values = write_values(
        tmp_path / "values.yaml",
        [
            {"family": "pdf", "platform": "go", "sourceKind": "self_extracted"},
            {"family": "pdf", "platform": "go", "sourceKind": "self_extracted"},
        ],
    )
    target = tmp_path / "out.yml"
    assert generator.main(["--values", str(values), "--output", str(target)]) == 2
    assert "duplicate" in capsys.readouterr().err
    assert not target.exists()


@pytest.mark.parametrize("bad", [{"family": "PDF", "platform": "go"}, {"family": "pdf", "platform": "g o"}])
def test_the_generator_refuses_an_identity_that_is_not_lowercase_letters_and_digits(
    bad: dict[str, str], tmp_path: Path
) -> None:
    values = write_values(tmp_path / "values.yaml", [{**bad, "sourceKind": "self_extracted"}])
    assert generator.main(["--values", str(values), "--output", str(tmp_path / "out.yml")]) == 2


def test_the_generator_output_is_deterministic() -> None:
    first = generator.render(chart_list())
    second = generator.render(chart_list())
    assert first == second
    assert first.encode("utf-8") == COMPOSE.read_bytes()


def test_the_generators_healthcheck_is_the_one_in_the_dockerfile() -> None:
    probe = generator.HEALTHCHECK_COMMAND[3]
    assert probe in DOCKERFILE.read_text(encoding="utf-8")
    assert json.dumps(probe) in COMPOSE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- (c) Dockerfile.serving


def dockerfile_stages() -> list[list[str]]:
    """Each stage as its logical instructions (comments and blanks dropped, continuations joined,
    whitespace normalised); a stage starts at its FROM."""
    stages: list[list[str]] = []
    buffer = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        instruction = " ".join((buffer + line).split())
        buffer = ""
        if instruction.startswith("FROM "):
            stages.append([])
        stages[-1].append(instruction)
    return stages


RUNTIME_STAGE = [
    "FROM python:3.13-slim@sha256:b92e6b9bb1ea9d826e9956fd8d30bf18bc384c130231d74fdf0ba3460c290b0b AS runtime",
    "WORKDIR /app",
    "COPY requirements-serving.lock ./requirements-serving.lock",
    "RUN pip install --no-cache-dir --require-hashes -r requirements-serving.lock",
    "COPY src/ ./src/",
    "COPY config/ ./config/",
    "COPY infra/serve_stdio.py ./infra/serve_stdio.py",
    "COPY infra/serve_http.py ./infra/serve_http.py",
    "COPY infra/fetch_product_reference.py ./infra/fetch_product_reference.py",
    "COPY infra/fetch_recent_releases.py ./infra/fetch_recent_releases.py",
    "COPY infra/verify_package_registry.py ./infra/verify_package_registry.py",
    "ENV PYTHONPATH=/app/src",
    "RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin foss-mcp && mkdir -p /data/manifests"
    " && chown -R foss-mcp:foss-mcp /data",
    "USER foss-mcp",
    "EXPOSE 8080",
    "HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD"
    ' ["python", "-c", "import urllib.request,sys; sys.exit(0 if'
    " urllib.request.urlopen('http://localhost:8080/readyz', timeout=3).status==200 else 1)\"]",
    'CMD ["python", "/app/infra/serve_stdio.py"]',
]


def test_dockerfile_serving_has_exactly_three_stages_runtime_demo_and_the_reanchor() -> None:
    stages = dockerfile_stages()
    assert [stage[0].split(" AS ")[-1] if " AS " in stage[0] else "(unnamed)" for stage in stages] == [
        "runtime",
        "demo",
        "(unnamed)",
    ]


def test_the_runtime_stage_is_unchanged() -> None:
    assert dockerfile_stages()[0] == RUNTIME_STAGE


def test_the_last_stage_is_still_the_bare_from_runtime() -> None:
    assert dockerfile_stages()[-1] == ["FROM runtime"]


def test_the_demo_stage_builds_on_runtime_and_keeps_its_baked_data() -> None:
    demo = dockerfile_stages()[1]
    assert demo[0] == "FROM runtime AS demo"
    assert "COPY --chown=foss-mcp:foss-mcp infra/demo-manifests/ /data/manifests" in demo


def test_the_demo_stage_runs_the_entrypoint_and_exposes_the_http_port() -> None:
    demo = dockerfile_stages()[1]
    assert 'ENTRYPOINT ["python", "/app/infra/demo_entrypoint.py"]' in demo
    assert "EXPOSE 8080" in demo
    assert demo[-1] == "CMD []", (
        "the demo stage's CMD must be empty so a bare `docker run IMAGE` prints the usage"
    )
    assert not any(instruction.startswith("USER ") for instruction in demo), (
        "the demo stage runs as runtime's user"
    )


def test_the_demo_stage_copies_the_entrypoint_with_the_ownership_of_the_other_infra_files() -> None:
    demo = dockerfile_stages()[1]
    assert "COPY infra/demo_entrypoint.py ./infra/demo_entrypoint.py" in demo
    other_infra = [i for i in RUNTIME_STAGE if i.startswith("COPY infra/")]
    assert other_infra and not any("--chown" in instruction for instruction in other_infra)


def test_the_entrypoint_file_the_demo_stage_copies_exists() -> None:
    assert (REPO_ROOT / "infra" / "demo_entrypoint.py").is_file()


def test_the_helm_demo_deployment_still_overrides_the_command_itself() -> None:
    """A Kubernetes `command` replaces ENTRYPOINT as well as CMD, so the chart is unaffected by the new
    ENTRYPOINT only while it keeps naming the server explicitly."""
    text = HELM_DEMO_DEPLOYMENT.read_text(encoding="utf-8")
    assert 'command: ["python", "/app/infra/serve_http.py"]' in text
