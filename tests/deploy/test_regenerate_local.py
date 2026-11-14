"""G3/TC-829: scripts/release/regenerate_local.py, offline.

No docker, helm, network or embedding gateway is ever reached: `main` takes an
injected runner, so helm, `docker image inspect`, `docker run` and `docker rm` are answered by an in-memory fake that
also plays the part of the ingestion container (it writes a generation, sidecars and a verification cache into the
bind-mounted work directory). The repository's real demo data is never written: every test points --root,
--cache-dest and --work-dir at tmp_path.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
TOOL = RELEASE_DIR / "regenerate_local.py"

sys.path.insert(0, str(RELEASE_DIR))
sys.path.insert(0, str(DEPLOY_DIR))

import compact_demo_manifests as compact  # noqa: E402
import pilots  # noqa: E402
import regenerate_local as regen  # noqa: E402

import infra.build_chunks as build_chunks  # noqa: E402
from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER  # noqa: E402

BY_KEY = {pilots.pilot_key(p): p for p in pilots.load_pilots()}
PDF_NET = BY_KEY["pdf_net"]
SECRET_ENDPOINT = "https://gateway.invalid/very-private-endpoint"
SECRET_KEY = "sk-test-0123456789-never-print-me"
SECRET_GH = "ghp_testtokenvalue_never_print_me"
GATEWAY_ENV = {"GPT_OSS_ENDPOINT": SECRET_ENDPOINT, "GPT_OSS_API_KEY": SECRET_KEY}
NEW_VERSION = "20261114T000000000000Z-deadbeef"
OLD_VERSION = "20261023T000000000000Z-0ld0ld00"
DEFAULT_PATH_LIKE = "/fake/"


def scope_of(pilot: dict[str, Any]) -> str:
    return "__".join((pilot["family"], pilot["platform"], pilot["sourceKind"]))


def gen_doc(
    pilot: dict[str, Any], version: str, *, dim: int = 4096, vector_len: int | None = None
) -> dict[str, Any]:
    gid = "::".join((pilot["family"], pilot["platform"], pilot["sourceKind"], version))
    length = dim if vector_len is None else vector_len
    texts = ["plain prose", f"{EXAMPLE_MARKER} run()", "reference", f"intro\n{EXAMPLE_MARKER}\n go()"]
    points = [
        {"chunk_id": f"c{n}", "point_id": f"{gid}::p{n}", "text": text, "vector": [0.25] * length}
        for n, text in enumerate(texts)
    ]
    return {
        "generation_id": gid,
        "family": pilot["family"],
        "platform": pilot["platform"],
        "source_kind": pilot["sourceKind"],
        "version": version,
        "payload": {
            "lexical_index": {
                "documents": {f"c{n}": {"tf": {"a": 1}, "length": 3} for n in range(3)},
                "generation_id": gid,
                "doc_freq": {"a": 3},
            },
            "vector_index": {"dimension": dim, "generation_id": gid, "points": points},
        },
    }


def write_scope(root: Path, pilot: dict[str, Any], version: str, *, dim: int = 4096) -> Path:
    scope_dir = root / scope_of(pilot)
    scope_dir.mkdir(parents=True, exist_ok=True)
    doc = gen_doc(pilot, version, dim=dim)
    (scope_dir / f"generation__{version}.json").write_bytes(compact.render(doc))
    (scope_dir / "active_pointer.json").write_text(
        json.dumps({"generation_id": doc["generation_id"]}), encoding="utf-8"
    )
    return scope_dir


def cache_entry(title: str, *, verified: bool, commit: str = "1" * 40) -> dict[str, Any]:
    return {
        "provenance": "readme",
        "title": title,
        "code_sha256": ("a" if verified else "b") * 64,
        "library_commit": commit,
        "platform": "net",
        "toolchain": "dotnet 10.0.401",
        "verified": verified,
        "output": "ok" if verified else "boom",
    }


def job_yaml(pilot: dict[str, Any]) -> str:
    key = pilots.pilot_key(pilot)
    fam, plat, kind = pilot["family"], pilot["platform"], pilot["sourceKind"]
    args = (
        f"python /app/infra/build_chunks.py --api-surface {pilot['apiSurface']} "
        f'--title "{key} API surface" --max-types 10 '
        f"--verification-cache /app/fixtures/verified_examples/{key}.json "
        f"--verification-cache-out /data/manifests/verification_cache_{key}.json --out /tmp/{key}_chunks.json "
        f"&& python /app/infra/ingest.py --family {fam} --platform {plat} --source-kind {kind} "
        f"--chunks /tmp/{key}_chunks.json --manifest-store /data/manifests "
        f"--embedding-provider {regen.GATEWAY_PROVIDER} --vector-scope examples --vector-decimals 5 "
        f"--api-surface {pilot['apiSurface']} --held-by ingestion-{fam}-{plat} "
        f"&& {{ python /app/infra/fetch_product_reference.py --repository o/r --platform {plat} "
        f'--manifest-path m --ref abc --output /data/manifests/product_reference_{key}.json || echo "WARNING (exit $?)"; }} '
        f"&& {{ python /app/infra/verify_product_reference_install.py --family {fam} --platform {plat} "
        f"--manifests-dir /data/manifests || true; }} "
        f"&& {{ python /app/infra/fetch_recent_releases.py --repository o/r "
        f"--output /data/manifests/recent_releases_{key}.json || true; }}"
    )
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"x-ingest-{fam}-{plat}"},
        "spec": {
            "template": {
                "spec": {"containers": [{"name": "ingest", "command": ["sh", "-c"], "args": [args]}]}
            }
        },
    }
    return "---\n# Source: foss-mcp/templates/ingestion-job.yaml\n" + yaml.safe_dump(job)


def completed(cmd: list[str], rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, rc, out, err)


class FakeRun:
    """helm, `docker image inspect`, `docker run` (the ingestion container) and `docker rm` in memory."""

    def __init__(
        self,
        *,
        dim: int = 4096,
        vector_len: int | None = None,
        docker_rc: int = 0,
        docker_stderr: str = "",
        timeout: bool = False,
        cache_entries: list[dict[str, Any]] | None = None,
        image_present: bool = True,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.dim, self.vector_len = dim, vector_len
        self.docker_rc, self.docker_stderr = docker_rc, docker_stderr
        self.timeout = timeout
        self.cache_entries = cache_entries
        self.image_present = image_present
        self.delay = delay
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        with self.lock:
            self.calls.append((list(cmd), dict(kwargs)))
        if cmd[0] == "helm":
            release = cmd[2]
            pilot = next(p for p in BY_KEY.values() if pilots.release_name(p) == release)
            return completed(cmd, 0, job_yaml(pilot))
        if cmd[:3] == ["docker", "image", "inspect"]:
            return completed(
                cmd, 0 if self.image_present else 1, "", "" if self.image_present else "no such image"
            )
        if cmd[:3] == ["docker", "rm", "-f"]:
            return completed(cmd)
        if cmd[:2] == ["docker", "run"]:
            return self._docker_run(cmd, kwargs)
        raise AssertionError(f"unexpected command {cmd}")

    def _docker_run(self, cmd: list[str], kwargs: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.timeout:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))
            if self.docker_rc != 0:
                return completed(cmd, self.docker_rc, "", self.docker_stderr)
            mount = cmd[cmd.index("-v") + 1].removesuffix(":" + regen.CONTAINER_MANIFESTS)
            host = Path(mount)
            chain = cmd[-1]
            assert "--content-derived-version" in chain
            key = chain.split("--family ")[1].split()[0] + "_" + chain.split("--platform ")[1].split()[0]
            pilot = BY_KEY[key]
            scope_dir = host / scope_of(pilot)
            doc = gen_doc(pilot, NEW_VERSION, dim=self.dim, vector_len=self.vector_len)
            (scope_dir / f"generation__{NEW_VERSION}.json").write_text(json.dumps(doc), encoding="utf-8")
            (scope_dir / "active_pointer.json").write_text(
                json.dumps({"generation_id": doc["generation_id"]}), encoding="utf-8"
            )
            (scope_dir / "lease_state.json").write_text('{"fencing_token": 1}', encoding="utf-8")
            # what the (dropped) sidecar tail would have written, and what must never be copied back
            (host / f"product_reference_{key}.json").write_text(
                '{"sidecar": "product_reference"}', encoding="utf-8"
            )
            (host / f"recent_releases_{key}.json").write_text(
                '{"sidecar": "recent_releases"}', encoding="utf-8"
            )
            entries = (
                self.cache_entries if self.cache_entries is not None else [cache_entry("new", verified=True)]
            )
            (host / f"verification_cache_{key}.json").write_text(
                build_chunks.verification_cache_document(entries), encoding="utf-8"
            )
            return completed(cmd, 0, "ingested", "")
        finally:
            with self.lock:
                self.running -= 1

    def of(self, program: str, sub: str | None = None) -> list[list[str]]:
        return [c for c, _ in self.calls if c[0] == program and (sub is None or c[1] == sub)]


def which_all(name: str) -> str | None:
    return DEFAULT_PATH_LIKE + name


def which_none(name: str) -> str | None:
    return None


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "demo-manifests"
    root.mkdir()
    cache = tmp_path / "verified_examples"
    cache.mkdir()
    return {"root": root, "cache": cache, "work": tmp_path / "work", "tmp": tmp_path}


def argv(layout: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--root",
        str(layout["root"]),
        "--cache-dest",
        str(layout["cache"]),
        "--work-dir",
        str(layout["work"]),
        *extra,
    ]


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def run_main(
    layout: dict[str, Path],
    fake: FakeRun,
    *extra: str,
    environ: dict[str, str] | None = None,
    which: Any = which_all,
) -> int:
    return regen.main(
        argv(layout, *extra), run=fake, environ=GATEWAY_ENV if environ is None else environ, which=which
    )


def seeded(layout: dict[str, Path], pilot: dict[str, Any] = PDF_NET) -> Path:
    scope_dir = write_scope(layout["root"], pilot, OLD_VERSION)
    (layout["root"] / f"product_reference_{pilots.pilot_key(pilot)}.json").write_text(
        '{"committed": 1}', encoding="utf-8"
    )
    return scope_dir


# ---------------------------------------------------------------------------------------------------
# cohorts and selection
# ---------------------------------------------------------------------------------------------------


def test_cohort_table_covers_all_32_pilots_exactly_once() -> None:
    members = [key for cohort in regen.COHORTS.values() for key in cohort]
    assert len(members) == len(set(members)) == pilots.PILOT_COUNT == 32
    assert set(members) == set(BY_KEY)


@pytest.mark.parametrize(
    ("cohort", "size"), [("C0", 1), ("C1", 11), ("C2", 6), ("C3", 4), ("C4", 3), ("C5", 7)]
)
def test_cohort_sizes(cohort: str, size: int) -> None:
    assert len(regen.COHORTS[cohort]) == size


def test_cohorts_group_by_platform() -> None:
    assert regen.COHORTS["C0"] == ("imaging_net",)
    assert all(k.endswith("_python") for k in regen.COHORTS["C1"])
    assert all(k.endswith("_net") for k in regen.COHORTS["C2"])
    assert all(k.endswith("_java") for k in regen.COHORTS["C3"])
    assert all(k.endswith("_typescript") for k in regen.COHORTS["C4"])
    assert {k.rsplit("_", 1)[1] for k in regen.COHORTS["C5"]} == {"go", "rust", "cpp"}


def test_select_by_cohort_only_and_union() -> None:
    assert [pilots.pilot_key(p) for p in regen.select([], ["C0"])] == ["imaging_net"]
    assert {pilots.pilot_key(p) for p in regen.select(["pdf_net,pdf_go"], ["C0"])} == {
        "pdf_net",
        "pdf_go",
        "imaging_net",
    }
    assert len(regen.select([], ["C1,C2"])) == 17


@pytest.mark.parametrize("bad", [(["nope_net"], []), ([], ["C9"]), ([], []), (["pdf_net", "nope"], [])])
def test_select_rejects_typos_and_empty_selection(bad: tuple[list[str], list[str]]) -> None:
    with pytest.raises(ValueError):
        regen.select(*bad)


def test_main_exits_2_on_a_bad_selection(layout: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeRun()
    assert run_main(layout, fake, "--only", "nope_net") == 2
    assert run_main(layout, fake) == 2
    assert fake.calls == []
    assert "unknown pilot" in capsys.readouterr().err


def test_help_names_the_cohorts_and_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        regen.parse_args(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--cohort", "--only", "--image", "--work-dir", "--parallel", "--dry-run", "--no-copy-back"):
        assert flag in out
    assert "C5" in out


# ---------------------------------------------------------------------------------------------------
# memory caps
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "cap"),
    [
        ("words_net", "8g"),
        ("html_python", "8g"),
        ("page_python", "8g"),
        ("pdf_cpp", "10g"),
        ("slides_cpp", "10g"),
        ("imaging_net", "4g"),
        ("pdf_net", "4g"),
        ("cells_typescript", "4g"),
    ],
)
def test_memory_caps_for_named_pilots(key: str, cap: str) -> None:
    assert regen.memory_cap(BY_KEY[key]) == cap


def test_every_pilot_cap_follows_values_yaml() -> None:
    default = regen.memory_to_docker(regen.default_memory())
    assert default == "4g"
    for key, pilot in BY_KEY.items():
        expected = (
            regen.memory_to_docker(pilot["ingestionMemoryLimit"])
            if "ingestionMemoryLimit" in pilot
            else default
        )
        assert regen.memory_cap(pilot) == expected, key


@pytest.mark.parametrize(
    ("quantity", "docker"), [("4Gi", "4g"), ("512Mi", "512m"), ("10Gi", "10g"), ("2G", "2g")]
)
def test_memory_quantity_conversion(quantity: str, docker: str) -> None:
    assert regen.memory_to_docker(quantity) == docker


def test_memory_quantity_garbage_is_an_error() -> None:
    with pytest.raises(regen.RegenError):
        regen.memory_to_docker("lots")


# ---------------------------------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------------------------------


def test_extract_job_args_finds_the_single_job() -> None:
    args = regen.extract_job_args(job_yaml(PDF_NET))
    assert args.startswith("python /app/infra/build_chunks.py")


def test_extract_job_args_rejects_zero_or_many_jobs_and_odd_commands() -> None:
    with pytest.raises(regen.RegenError):
        regen.extract_job_args("---\nkind: ConfigMap\n")
    with pytest.raises(regen.RegenError):
        regen.extract_job_args(job_yaml(PDF_NET) + job_yaml(BY_KEY["pdf_go"]))
    odd = yaml.safe_load(job_yaml(PDF_NET))
    odd["spec"]["template"]["spec"]["containers"][0]["command"] = ["python"]
    with pytest.raises(regen.RegenError):
        regen.extract_job_args(yaml.safe_dump(odd))


def test_chain_keeps_build_chunks_and_ingest_and_drops_the_sidecar_tail() -> None:
    chain, dropped = regen.build_chain(regen.extract_job_args(job_yaml(PDF_NET)), epoch=1234)
    assert "build_chunks.py" in chain and "infra/ingest.py" in chain
    assert "--content-derived-version" in chain
    assert "--source-date-epoch 1234" in chain
    assert "--vector-scope examples" in chain and "--vector-decimals 5" in chain
    for name in regen.SIDECAR_STEPS:
        assert name not in chain
    assert dropped == list(regen.SIDECAR_STEPS)
    assert "||" not in chain and "{" not in chain
    assert chain.startswith("mkdir -p /tmp/h && python /app/infra/build_chunks.py")


def test_chain_forces_examples_scope_and_decimals_when_the_chart_changes() -> None:
    args = regen.extract_job_args(job_yaml(PDF_NET)).replace("--vector-scope examples", "--vector-scope all")
    chain, _ = regen.build_chain(args.replace("--vector-decimals 5", "--vector-decimals 9"), epoch=1)
    assert "--vector-scope examples" in chain and "--vector-decimals 5" in chain
    assert "--vector-scope all" not in chain and "--vector-decimals 9" not in chain


def test_chain_refuses_a_hashing_provider() -> None:
    args = regen.extract_job_args(job_yaml(PDF_NET)).replace(
        regen.GATEWAY_PROVIDER, "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider"
    )
    with pytest.raises(regen.RegenError, match="gateway embedding provider"):
        regen.build_chain(args, epoch=1)


def test_chain_refuses_a_job_that_is_not_build_then_ingest() -> None:
    with pytest.raises(regen.RegenError):
        regen.build_chain("python /app/infra/ingest.py --family x && python /app/infra/other.py", epoch=1)
    with pytest.raises(regen.RegenError):
        regen.build_chain("python /app/infra/build_chunks.py --out x && python /app/infra/other.py", epoch=1)


def test_chain_accept_regression_needs_a_reason_and_is_passed_to_ingest() -> None:
    args = regen.extract_job_args(job_yaml(PDF_NET))
    with pytest.raises(regen.RegenError, match="--reason"):
        regen.build_chain(args, epoch=1, accept_regressions=["examples:10->8"])
    chain, _ = regen.build_chain(
        args, epoch=1, accept_regressions=["examples:10->8"], reason="TC-800 one chunk"
    )
    assert "--accept-regression 'examples:10->8'" in chain and "--reason 'TC-800 one chunk'" in chain


def test_dry_run_through_a_rendered_job_keeps_the_cache_flags_and_drops_the_tail(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The runner stands in for `helm template` and returns a realistic rendered Job (see job_yaml)."""
    fake = FakeRun()
    rc = run_main(layout, fake, "--only", "words_net", "--dry-run")
    out = capsys.readouterr().out
    assert rc == 0
    assert [c[:2] for c, _ in fake.calls] == [["helm", "template"]]
    assert "--verification-cache-out /data/manifests/verification_cache_words_net.json" in out
    assert "--content-derived-version" in out and "-m 8g" in out
    assert "fetch_recent_releases.py --repository" not in out


# ---------------------------------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------------------------------


def test_dry_run_prints_the_plan_runs_only_helm_and_writes_nothing(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(layout)
    before = tree(layout["root"])
    fake = FakeRun()

    rc = run_main(layout, fake, "--only", "pdf_net", "--dry-run")

    out = capsys.readouterr().out
    assert rc == 0
    assert [c[0] for c, _ in fake.calls] == ["helm"]
    assert "docker run" in out and "build_chunks.py" in out and "infra/ingest.py" in out
    assert (
        "--content-derived-version" in out
        and "--vector-scope examples" in out
        and "--vector-decimals 5" in out
    )
    for name in regen.SIDECAR_STEPS:
        assert name not in out.split("sidecar steps dropped")[0]
    assert "--entrypoint sh" in out and "--cpus 2" in out and "-m 4g" in out
    assert "compact_demo_manifests.py" in out and "--check" in out
    assert tree(layout["root"]) == before
    assert not layout["work"].exists()
    assert list(layout["cache"].iterdir()) == []


def test_dry_run_without_helm_still_prints_a_plan(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun()
    rc = run_main(layout, fake, "--cohort", "C0", "--dry-run", which=which_none)
    out = capsys.readouterr().out
    assert rc == 0 and fake.calls == []
    assert "NOT on PATH" in out and "docker run" in out


def test_dry_run_without_gateway_variables_notes_it_and_succeeds(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun()
    rc = run_main(layout, fake, "--cohort", "C0", "--dry-run", environ={})
    out = capsys.readouterr().out
    assert rc == 0
    assert "MISSING" in out


def test_dry_run_never_prints_a_credential_value(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {**GATEWAY_ENV, "GITHUB_TOKEN": SECRET_GH}
    rc = run_main(layout, FakeRun(), "--cohort", "C0", "--dry-run", environ=environ)
    captured = capsys.readouterr()
    assert rc == 0
    for secret in (SECRET_ENDPOINT, SECRET_KEY, SECRET_GH):
        assert secret not in captured.out + captured.err
    assert "GPT_OSS_ENDPOINT" in captured.out and "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY" in captured.out


# ---------------------------------------------------------------------------------------------------
# the gateway: refusal, name mapping, redaction
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"GPT_OSS_ENDPOINT": SECRET_ENDPOINT},
        {"GPT_OSS_API_KEY": SECRET_KEY},
        {"FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT": SECRET_ENDPOINT},
        {"GPT_OSS_ENDPOINT": SECRET_ENDPOINT, "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY": SECRET_KEY},
        {"GPT_OSS_ENDPOINT": "", "GPT_OSS_API_KEY": ""},
        {"GITHUB_TOKEN": SECRET_GH},
    ],
)
def test_real_run_refuses_without_a_complete_gateway_pair(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], environ: dict[str, str]
) -> None:
    seeded(layout)
    before = tree(layout["root"])
    fake = FakeRun()

    rc = run_main(layout, fake, "--only", "pdf_net", environ=environ)

    err = capsys.readouterr().err
    assert rc == regen.EXIT_REFUSED == 3
    assert fake.calls == []  # not even helm or docker image inspect ran
    assert "GPT_OSS_ENDPOINT" in err and "GPT_OSS_API_KEY" in err and "64-dim" in err
    for secret in (SECRET_ENDPOINT, SECRET_KEY, SECRET_GH):
        assert secret not in err
    assert tree(layout["root"]) == before
    assert not layout["work"].exists()


def test_gpt_oss_variables_are_mapped_to_the_container_names() -> None:
    credentials = regen.gateway_credentials({**GATEWAY_ENV, "GH_TOKEN": SECRET_GH})
    env = credentials.child_env
    assert env["FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"] == SECRET_ENDPOINT
    assert env["FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"] == SECRET_KEY
    assert env["GITHUB_TOKEN"] == SECRET_GH
    assert (credentials.endpoint_source, credentials.api_key_source) == (
        "GPT_OSS_ENDPOINT",
        "GPT_OSS_API_KEY",
    )
    assert SECRET_KEY not in repr(credentials)


def test_the_container_pair_is_preferred_and_the_dimension_variable_never_survives() -> None:
    environ = {
        "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT": "https://direct.invalid",
        "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY": "direct-key-value",
        **GATEWAY_ENV,
        "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION": "64",
    }
    credentials = regen.gateway_credentials(environ)
    assert credentials.child_env["FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"] == "https://direct.invalid"
    assert "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION" not in credentials.child_env


def test_docker_command_carries_names_only_and_never_the_dimension(layout: dict[str, Path]) -> None:
    seeded(layout)
    fake = FakeRun()
    environ = {**GATEWAY_ENV, "GITHUB_TOKEN": SECRET_GH, "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION": "64"}

    rc = run_main(layout, fake, "--only", "pdf_net", environ=environ)

    assert rc == 0
    (docker_cmd,) = fake.of("docker", "run")
    joined = " ".join(docker_cmd)
    for secret in (SECRET_ENDPOINT, SECRET_KEY, SECRET_GH):
        assert secret not in joined
    pairs = [(docker_cmd[i], docker_cmd[i + 1]) for i in range(len(docker_cmd) - 1) if docker_cmd[i] == "-e"]
    assert ("-e", "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT") in pairs
    assert ("-e", "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY") in pairs
    assert ("-e", "GITHUB_TOKEN") in pairs
    assert ("-e", "HOME=/tmp/h") in pairs
    assert "DIMENSION" not in joined
    (_, docker_kwargs) = next(c for c in fake.calls if c[0][:2] == ["docker", "run"])
    env = docker_kwargs["env"]
    assert env["FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"] == SECRET_ENDPOINT
    assert env["FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"] == SECRET_KEY
    assert env["GITHUB_TOKEN"] == SECRET_GH
    assert "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION" not in env


def test_credential_values_are_redacted_from_everything_printed_and_logged(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(layout)
    leak = f"auth failed for {SECRET_KEY} at {SECRET_ENDPOINT} using {SECRET_GH}"
    fake = FakeRun(docker_rc=2, docker_stderr=leak)

    rc = run_main(layout, fake, "--only", "pdf_net", environ={**GATEWAY_ENV, "GITHUB_TOKEN": SECRET_GH})

    captured = capsys.readouterr()
    assert rc == 1
    text = captured.out + captured.err
    for secret in (SECRET_ENDPOINT, SECRET_KEY, SECRET_GH):
        assert secret not in text
    assert "<FOSS_MCP_EMBEDDING_GATEWAY_API_KEY>" in text or "<GPT_OSS_API_KEY>" in text
    for path in layout["work"].rglob("*"):
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace")
            assert SECRET_KEY not in body and SECRET_ENDPOINT not in body


@pytest.mark.parametrize(
    ("text", "secrets", "expected"),
    [
        ("a SECRETVALUE b", {"NAME": "SECRETVALUE"}, "a <NAME> b"),
        ("tiny x", {"NAME": "x"}, "tiny x"),
        ("one two", {"A": "one", "B": "two2"}, "one two"),
    ],
)
def test_redact(text: str, secrets: dict[str, str], expected: str) -> None:
    assert regen.redact(text, secrets) == expected


# ---------------------------------------------------------------------------------------------------
# safety refusals
# ---------------------------------------------------------------------------------------------------


def test_helm_missing_on_a_real_run_names_the_activation_script(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun()
    assert run_main(layout, fake, "--only", "pdf_net", which=which_none) == 3
    assert "scripts/toolchain/activate.ps1" in capsys.readouterr().err
    assert fake.calls == []


def test_docker_missing_is_refused(layout: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeRun()
    rc = run_main(layout, fake, "--only", "pdf_net", which=lambda n: None if n == "docker" else "/x/" + n)
    assert rc == 3 and "docker is not on PATH" in capsys.readouterr().err


def test_missing_image_is_refused_with_the_build_command(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun(image_present=False)
    assert run_main(layout, fake, "--only", "pdf_net") == 3
    err = capsys.readouterr().err
    assert "docker build -f Dockerfile.ingestion -t foss-mcp-ingestion:regen" in err
    assert fake.of("docker", "run") == []


@pytest.mark.parametrize(
    "image", ["foss-mcp-ingestion", "foss-mcp-ingestion:latest", "foss-mcp-ingestion@sha256:abc", "x:"]
)
def test_unpinned_image_is_refused(layout: dict[str, Path], image: str) -> None:
    fake = FakeRun()
    assert run_main(layout, fake, "--only", "pdf_net", "--image", image) == 3
    assert fake.calls == []


def test_image_tag_is_what_the_chart_is_rendered_with(layout: dict[str, Path]) -> None:
    seeded(layout)
    fake = FakeRun()
    assert (
        run_main(
            layout, fake, "--only", "pdf_net", "--image", "reg.local:5000/foss-mcp-ingestion:demo-abc1234"
        )
        == 0
    )
    (helm_cmd,) = fake.of("helm")
    assert helm_cmd[helm_cmd.index("--set") + 1] == "image.tag=demo-abc1234"
    assert "reg.local:5000/foss-mcp-ingestion:demo-abc1234" in fake.of("docker", "run")[0]


def test_work_dir_inside_the_repository_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun()
    rc = regen.main(
        ["--only", "pdf_net", "--work-dir", str(REPO_ROOT / "scratch-work"), "--root", str(layout["root"])],
        run=fake,
        environ=GATEWAY_ENV,
        which=which_all,
    )
    assert rc == 3 and "inside the repository" in capsys.readouterr().err
    assert fake.calls == []


def test_work_dir_under_onedrive_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun()
    rc = regen.main(
        [
            "--only",
            "pdf_net",
            "--work-dir",
            str(layout["tmp"] / "OneDrive" / "w"),
            "--root",
            str(layout["root"]),
        ],
        run=fake,
        environ=GATEWAY_ENV,
        which=which_all,
    )
    assert rc == 3 and "OneDrive" in capsys.readouterr().err
    assert fake.calls == []


def test_default_work_dir_is_outside_the_repository() -> None:
    assert regen.check_work_dir(regen.default_work_dir())


def test_accept_regression_arguments_are_validated() -> None:
    with pytest.raises(SystemExit):
        regen.parse_args(["--only", "pdf_net", "--accept-regression", "examples:10->8"])
    with pytest.raises(SystemExit):
        regen.parse_args(["--only", "pdf_net", "--accept-regression", "bogus", "--reason", "x"])
    args = regen.parse_args(["--only", "pdf_net", "--accept-regression", "examples:10->8", "--reason", "x"])
    assert args.accept_regression == ["examples:10->8"]


# ---------------------------------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------------------------------


def test_seeding_copies_the_committed_scope_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    committed = write_scope(root, PDF_NET, OLD_VERSION)
    work = tmp_path / "work" / "pdf_net"

    target = regen.seed_work_dir(PDF_NET, root, work)

    assert target == work / scope_of(PDF_NET)
    assert tree(target) == tree(committed)
    assert target != committed


def test_seeding_replaces_a_stale_work_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_scope(root, PDF_NET, OLD_VERSION)
    work = tmp_path / "work" / "pdf_net"
    work.mkdir(parents=True)
    (work / "leftover.json").write_text("{}", encoding="utf-8")

    regen.seed_work_dir(PDF_NET, root, work)

    assert not (work / "leftover.json").exists()


def test_seeding_without_a_committed_baseline_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(regen.RegenError, match="baseline"):
        regen.seed_work_dir(PDF_NET, tmp_path / "empty-root", tmp_path / "work" / "pdf_net")


def test_the_container_sees_the_seeded_baseline(layout: dict[str, Path]) -> None:
    seeded(layout)
    seen: dict[str, list[str]] = {}

    class Spy(FakeRun):
        def _docker_run(self, cmd: list[str], kwargs: dict[str, Any]) -> subprocess.CompletedProcess[str]:
            host = Path(cmd[cmd.index("-v") + 1].removesuffix(":" + regen.CONTAINER_MANIFESTS))
            seen["files"] = sorted(p.name for p in (host / scope_of(PDF_NET)).iterdir())
            return super()._docker_run(cmd, kwargs)

    assert run_main(layout, Spy(), "--only", "pdf_net") == 0
    assert seen["files"] == ["active_pointer.json", f"generation__{OLD_VERSION}.json"]


# ---------------------------------------------------------------------------------------------------
# vector dimension
# ---------------------------------------------------------------------------------------------------


def test_check_vector_dimension_accepts_4096_and_counts_points() -> None:
    assert regen.check_vector_dimension(gen_doc(PDF_NET, NEW_VERSION)) == 4


def test_a_64_dim_index_is_rejected() -> None:
    with pytest.raises(regen.RegenError, match="4096"):
        regen.check_vector_dimension(gen_doc(PDF_NET, NEW_VERSION, dim=64))


def test_a_point_of_the_wrong_length_is_rejected_even_under_a_4096_header() -> None:
    with pytest.raises(regen.RegenError, match="length"):
        regen.check_vector_dimension(gen_doc(PDF_NET, NEW_VERSION, vector_len=64))


def test_a_missing_vector_index_is_rejected() -> None:
    doc = gen_doc(PDF_NET, NEW_VERSION)
    doc["payload"]["vector_index"] = None
    with pytest.raises(regen.RegenError, match="no vector index"):
        regen.check_vector_dimension(doc)
    with pytest.raises(regen.RegenError):
        regen.check_vector_dimension({})


def test_64_dim_work_output_is_never_copied_back(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(layout)
    before = tree(layout["root"])

    rc = run_main(layout, FakeRun(dim=64), "--only", "pdf_net")

    out = capsys.readouterr().out
    assert rc == 1
    assert "FAILED" in out and "not 4096" in out
    assert tree(layout["root"]) == before
    assert list(layout["cache"].iterdir()) == []
    assert (layout["work"] / "pdf_net").is_dir()  # kept for diagnosis


# ---------------------------------------------------------------------------------------------------
# end to end through main, with the fake container
# ---------------------------------------------------------------------------------------------------


def test_success_copies_only_pointer_and_active_generation_never_sidecars(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    scope_dir = seeded(layout)
    committed_sidecar = (layout["root"] / "product_reference_pdf_net.json").read_bytes()

    rc = run_main(layout, FakeRun(), "--only", "pdf_net")

    out = capsys.readouterr().out
    assert rc == 0, out
    assert sorted(p.name for p in scope_dir.iterdir()) == [
        "active_pointer.json",
        f"generation__{NEW_VERSION}.json",
    ]
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    assert pointer["generation_id"].endswith(NEW_VERSION)
    # compacted: only the two example points remain
    doc = json.loads((scope_dir / f"generation__{NEW_VERSION}.json").read_text(encoding="utf-8"))
    assert len(doc["payload"]["vector_index"]["points"]) == 2
    assert doc["payload"]["vector_index"]["dimension"] == 4096
    # sidecars: the committed one is untouched, the container's are not copied, no lease state
    assert (layout["root"] / "product_reference_pdf_net.json").read_bytes() == committed_sidecar
    assert not (layout["root"] / "recent_releases_pdf_net.json").exists()
    assert not (scope_dir / "lease_state.json").exists()
    assert sorted(p.name for p in layout["root"].iterdir()) == [
        scope_of(PDF_NET),
        "product_reference_pdf_net.json",
    ]
    assert "pdf_net: ok" in out and "compaction check rc 0" in out
    assert not (layout["work"] / "pdf_net").exists()  # cleaned after a successful copy-back


def test_keep_work_keeps_the_work_directory(layout: dict[str, Path]) -> None:
    seeded(layout)
    assert run_main(layout, FakeRun(), "--only", "pdf_net", "--keep-work") == 0
    assert (layout["work"] / "pdf_net").is_dir()


def test_verification_cache_is_merged_and_failing_entries_are_preserved(layout: dict[str, Path]) -> None:
    seeded(layout)
    failing = cache_entry("known failing", verified=False)
    old_other_commit = cache_entry("old commit", verified=True, commit="2" * 40)
    dest = layout["cache"] / "pdf_net.json"
    dest.write_text(build_chunks.verification_cache_document([failing, old_other_commit]), encoding="utf-8")
    fresh = cache_entry("new", verified=True)

    rc = run_main(layout, FakeRun(cache_entries=[fresh]), "--only", "pdf_net")

    assert rc == 0
    merged = build_chunks.parse_verification_cache(dest.read_text(encoding="utf-8"))
    titles = {e["title"]: e["verified"] for e in merged}
    assert titles == {"known failing": False, "old commit": True, "new": True}


def test_no_copy_back_leaves_the_repository_untouched(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(layout)
    before = tree(layout["root"])

    rc = run_main(layout, FakeRun(), "--only", "pdf_net", "--no-copy-back")

    assert rc == 0
    assert tree(layout["root"]) == before
    assert list(layout["cache"].iterdir()) == []
    assert "not copied back" in capsys.readouterr().out


def test_a_failing_container_fails_the_pilot_and_copies_nothing(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    seeded(layout)
    before = tree(layout["root"])

    rc = run_main(layout, FakeRun(docker_rc=137, docker_stderr="Killed"), "--only", "pdf_net")

    out = capsys.readouterr().out
    assert rc == 1
    assert "exited 137" in out and "Killed" in out and "pdf_net.log" in out
    assert tree(layout["root"]) == before


def test_a_timeout_removes_the_container(layout: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    seeded(layout)
    fake = FakeRun(timeout=True)

    rc = run_main(layout, fake, "--only", "pdf_net", "--pilot-timeout-minutes", "1")

    assert rc == 1
    (remove,) = fake.of("docker", "rm")
    assert remove[:3] == ["docker", "rm", "-f"] and remove[3].startswith("foss-mcp-regen-pdf_net-")
    assert "did not finish" in capsys.readouterr().out


def test_one_failed_pilot_does_not_block_the_others(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_go = BY_KEY["pdf_go"]
    seeded(layout)
    write_scope(layout["root"], pdf_go, OLD_VERSION)

    class Flaky(FakeRun):
        def _docker_run(self, cmd: list[str], kwargs: dict[str, Any]) -> subprocess.CompletedProcess[str]:
            if "pdf_net" in cmd[cmd.index("--name") + 1]:
                return completed(cmd, 1, "", "network down")
            return super()._docker_run(cmd, kwargs)

    rc = run_main(layout, Flaky(), "--only", "pdf_net,pdf_go")

    out = capsys.readouterr().out
    assert rc == 1
    assert "pdf_net: FAILED" in out and "pdf_go: ok" in out
    assert "1 of 2 pilot(s) ok" in out
    assert (layout["root"] / scope_of(pdf_go) / f"generation__{NEW_VERSION}.json").is_file()
    assert (layout["root"] / scope_of(PDF_NET) / f"generation__{OLD_VERSION}.json").is_file()


def test_parallelism_is_bounded_by_the_flag(layout: dict[str, Path]) -> None:
    keys = ["pdf_net", "pdf_go", "cells_go", "cells_rust"]
    for key in keys:
        write_scope(layout["root"], BY_KEY[key], OLD_VERSION)
    fake = FakeRun(delay=0.15)

    assert run_main(layout, fake, "--only", ",".join(keys), "--parallel", "2") == 0
    assert 1 <= fake.max_running <= 2

    fake_serial = FakeRun(delay=0.02)
    assert run_main(layout, fake_serial, "--only", ",".join(keys), "--parallel", "1") == 0
    assert fake_serial.max_running == 1


def test_a_helm_failure_stops_before_any_container(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    class BadHelm(FakeRun):
        def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "helm":
                self.calls.append((list(cmd), kwargs))
                return completed(cmd, 1, "", "chart broken")
            return super().__call__(cmd, **kwargs)

    fake = BadHelm()
    assert run_main(layout, fake, "--only", "pdf_net") == 1
    assert "helm template failed" in capsys.readouterr().err
    assert fake.of("docker", "run") == []


# ---------------------------------------------------------------------------------------------------
# liveness: main really reaches the snapshot / pull / compaction code
# ---------------------------------------------------------------------------------------------------


def _reachable_calls(source: str, entry: str = "main") -> set[str]:
    tree_ = ast.parse(source)
    functions = {n.name: n for n in tree_.body if isinstance(n, ast.FunctionDef)}
    seen: set[str] = set()
    calls: set[str] = set()
    queue = [entry]
    while queue:
        name = queue.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    calls.add(target.id)
                    queue.append(target.id)
                elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    calls.add(f"{target.value.id}.{target.attr}")
    return calls


def test_main_reaches_the_snapshot_pull_and_compaction_helpers() -> None:
    source = TOOL.read_text(encoding="utf-8")
    calls = _reachable_calls(source)
    for dotted in ("snap.snapshot_pilot", "snap.compact_root", "compact.run", "pull.pull_pilot"):
        assert dotted in calls, f"main() never reaches {dotted}"
    assert {"regenerate", "copy_back", "run_compaction", "build_plan"} <= calls
    assert "pool.submit(execute_pilot, plan, ctx)" in source
    assert "import snapshot_demo_from_cluster as snap" in source
    assert "import pull_verification_cache as pull" in source
    assert "import compact_demo_manifests as compact" in source


def test_the_driver_uses_the_two_scripts_from_dir_paths() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "from_dir=plan.work_pilot_dir" in source
    assert "include_sidecars=False" in source


def test_the_driver_never_invokes_kubectl_or_a_docker_build() -> None:
    tree_ = ast.parse(TOOL.read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree_) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "kubectl" not in literals
    assert "build" not in literals and "push" not in literals


def test_the_driver_runs_as_a_script_with_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    assert "--cohort" in proc.stdout and "--dry-run" in proc.stdout
