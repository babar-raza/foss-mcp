"""G3/TC-347: scripts/release/push_image.py, offline. subprocess.run is replaced by a fake.

No docker command ever runs and no registry is ever contacted. Every test that could reach subprocess either
fakes it or proves it is never called. The registry host in every test is a placeholder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
PUSH_SOURCE = RELEASE_DIR / "push_image.py"
RUNBOOK = REPO_ROOT / "docs" / "DEPLOYMENT.md"

sys.path.insert(0, str(RELEASE_DIR))

import push_image as push  # noqa: E402

REGISTRY = "registry.example.internal:5000/foss-mcp"
IMAGE = "foss-mcp-serving:rev-abc1234"
REFERENCE = f"{REGISTRY}/foss-mcp-serving:rev-abc1234"
IMAGE_ID = "sha256:" + "ab12" * 16
DIGEST = "sha256:" + "0123456789abcdef" * 4
PUSH_OUTPUT = (
    f"The push refers to repository [{REGISTRY}/foss-mcp-serving]\n"
    "5f70bf18a086: Preparing\n"
    "5f70bf18a086: Layer already exists\n"
    "8d3ac3489996: Pushed\n"
    f"rev-abc1234: digest: {DIGEST} size: 1783\n"
)
PLANTED = "hunter2-planted-credential-value-9f3b"
RESULT_KEYS = {"image", "pushed_reference", "digest", "local_image_id", "timestamp"}
HEX_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
UTC_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _done(args, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, rc, stdout, stderr)


def _default_handler(cmd):
    if cmd[:3] == ["docker", "image", "inspect"]:
        return _done(cmd, stdout=IMAGE_ID + "\n")
    if cmd[:2] == ["docker", "push"]:
        return _done(cmd, stdout=PUSH_OUTPUT)
    return _done(cmd)


class FakeRun:
    """Stands in for subprocess.run. Records every command and its keyword arguments."""

    def __init__(self, handler=None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.handler = handler or _default_handler

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs.append(dict(kwargs))
        return self.handler(list(cmd))


@pytest.fixture(autouse=True)
def _no_registry_in_env(monkeypatch):
    monkeypatch.delenv(push.REGISTRY_ENV, raising=False)


@pytest.fixture
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"subprocess must not run: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def _fake(monkeypatch, handler=None) -> FakeRun:
    fake = FakeRun(handler)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def run(argv: list[str]) -> int:
    """main(argv) as a process would exit: the return value, or the code of the SystemExit it raised."""
    try:
        return push.main(argv)
    except SystemExit as exc:
        assert isinstance(exc.code, int)
        return exc.code


def args(*extra: str, image: str = IMAGE, registry: str | None = REGISTRY) -> list[str]:
    argv = ["--image", image]
    if registry is not None:
        argv += ["--registry", registry]
    return [*argv, *extra]


# --- the exact commands -------------------------------------------------------------------------------------


def test_dry_run_prints_the_tag_and_push_commands_and_runs_nothing(capsys, no_subprocess):
    rc = run(args("--dry-run"))
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip().splitlines() == [
        f"docker tag {IMAGE} {REFERENCE}",
        f"docker push {REFERENCE}",
    ]
    assert "dry run" in captured.err


def test_dry_run_writes_no_record_file(tmp_path, capsys, no_subprocess):
    record = tmp_path / "out" / "push.json"
    assert run(args("--dry-run", "--record", str(record))) == 0
    assert not record.exists()
    assert not record.parent.exists()


def test_the_command_builders_produce_the_exact_argv():
    assert push.inspect_command(IMAGE) == ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]
    assert push.tag_command(IMAGE, REFERENCE) == ["docker", "tag", IMAGE, REFERENCE]
    assert push.push_command(REFERENCE) == ["docker", "push", REFERENCE]


def test_a_normal_run_executes_exactly_inspect_then_tag_then_push(monkeypatch, capsys):
    fake = _fake(monkeypatch)
    assert run(args()) == 0
    assert fake.calls == [
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
        ["docker", "tag", IMAGE, REFERENCE],
        ["docker", "push", REFERENCE],
    ]
    for kwargs in fake.kwargs:
        assert "env" not in kwargs, "the tool must not build or pass an environment"
        assert "input" not in kwargs and "stdin" not in kwargs


def test_the_pushed_reference_is_registry_slash_name_colon_tag(monkeypatch, capsys):
    _fake(monkeypatch)
    assert run(args(image="team/foss-mcp-serving:v1.2", registry="registry.example.internal")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pushed_reference"] == "registry.example.internal/team/foss-mcp-serving:v1.2"
    assert result["image"] == "team/foss-mcp-serving:v1.2"


# --- the recorded result ------------------------------------------------------------------------------------


def test_a_normal_run_prints_the_digest_parsed_from_a_realistic_push_output(monkeypatch, capsys):
    _fake(monkeypatch)
    assert run(args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["digest"] == DIGEST
    assert result["pushed_reference"] == REFERENCE
    assert result["local_image_id"] == IMAGE_ID
    assert result["image"] == IMAGE


def test_the_result_has_exactly_the_five_keys_release_record_reads(monkeypatch, capsys):
    _fake(monkeypatch)
    before = datetime.now(UTC).replace(microsecond=0)
    assert run(args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == RESULT_KEYS
    assert list(result) == ["digest", "image", "local_image_id", "pushed_reference", "timestamp"]
    assert all(isinstance(value, str) for value in result.values())
    assert HEX_DIGEST.fullmatch(result["digest"])
    assert HEX_DIGEST.fullmatch(result["local_image_id"])
    assert UTC_ISO.fullmatch(result["timestamp"])
    stamp = datetime.strptime(result["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert before <= stamp <= before + timedelta(minutes=5)


def test_record_writes_the_same_object_as_utf8_json_without_a_byte_order_mark(monkeypatch, capsys, tmp_path):
    _fake(monkeypatch)
    record = tmp_path / "nested" / "push-result.json"
    assert run(args("--record", str(record))) == 0
    raw = record.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    recorded = json.loads(raw.decode("utf-8"))
    assert recorded == json.loads(capsys.readouterr().out)
    assert set(recorded) == RESULT_KEYS
    assert recorded["digest"] == DIGEST


def test_the_last_digest_line_wins_when_the_output_has_several(monkeypatch, capsys):
    other = "sha256:" + "f" * 64
    output = f"first: digest: {other} size: 10\n{PUSH_OUTPUT}"
    _fake(monkeypatch, lambda cmd: _done(cmd, stdout=output) if cmd[1] == "push" else _default_handler(cmd))
    assert run(args()) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == DIGEST


def test_a_digest_on_stderr_is_found_too(monkeypatch, capsys):
    def handler(cmd):
        if cmd[1] == "push":
            return _done(cmd, stdout="Pushed\n", stderr=f"rev-abc1234: digest: {DIGEST} size: 1783\n")
        return _default_handler(cmd)

    _fake(monkeypatch, handler)
    assert run(args()) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == DIGEST


def test_parse_digest_needs_the_whole_sha256_and_a_size():
    assert push.parse_digest(PUSH_OUTPUT) == DIGEST
    assert push.parse_digest("digest: sha256:abc size: 1") is None
    assert push.parse_digest(f"digest: {DIGEST}") is None
    assert push.parse_digest("") is None
    assert push.parse_digest(f"digest: sha256:{'A' * 64} size: 5") is None


# --- registry: required, and strictly validated -------------------------------------------------------------


def test_no_registry_anywhere_exits_two_and_runs_nothing(capsys, no_subprocess):
    assert run(args(registry=None)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no registry" in captured.err and push.REGISTRY_ENV in captured.err


def test_an_empty_environment_registry_is_no_registry(monkeypatch, capsys, no_subprocess):
    monkeypatch.setenv(push.REGISTRY_ENV, "")
    assert run(args(registry=None)) == 2
    assert "no registry" in capsys.readouterr().err


def test_the_registry_falls_back_to_the_environment_variable(monkeypatch, capsys):
    monkeypatch.setenv(push.REGISTRY_ENV, "env-registry.example.internal:5000")
    fake = _fake(monkeypatch)
    assert run(args(registry=None)) == 0
    assert fake.calls[-1] == [
        "docker",
        "push",
        "env-registry.example.internal:5000/foss-mcp-serving:rev-abc1234",
    ]


def test_the_flag_wins_over_the_environment_variable(monkeypatch, capsys):
    monkeypatch.setenv(push.REGISTRY_ENV, "env-registry.example.internal")
    fake = _fake(monkeypatch)
    assert run(args(registry="flag-registry.example.internal")) == 0
    assert fake.calls[-1][2].startswith("flag-registry.example.internal/")


def test_a_bad_environment_registry_is_refused_too(monkeypatch, capsys, no_subprocess):
    monkeypatch.setenv(push.REGISTRY_ENV, "https://registry.example.internal")
    assert run(args(registry=None)) == 2
    assert "scheme" in capsys.readouterr().err


BAD_REGISTRIES = [
    "http://r",
    "https://registry.example.internal",
    "u:p@r",
    "user:pw@registry.example.internal:5000/foss-mcp",
    "Reg Istry",
    "r/..",
    "r/../x",
    "r/.",
    "",
    "REGISTRY.example.internal",
    "registry.example.internal/Foss",
    "registry.example.internal:",
    "registry.example.internal:0",
    "registry.example.internal:65536",
    "registry.example.internal:abc",
    "registry.example.internal:5000:6000",
    "registry.example.internal/",
    "registry.example.internal//x",
    "registry.example.internal/x/",
    "registry.example.internal/-x",
    "registry.example.internal/x-",
    "registry.example.internal/a b",
    "registry.example.internal\n",
    "\tregistry.example.internal",
    "-registry",
    ".registry",
    "registry..internal",
    "registry_name.example.internal",
    "registry.example.internal/x?y=1",
    "registry.example.internal/x#frag",
    "a" * 300,
]


@pytest.mark.parametrize("registry", BAD_REGISTRIES)
def test_each_bad_registry_exits_two_and_runs_nothing(registry, capsys, no_subprocess):
    assert run([f"--image={IMAGE}", f"--registry={registry}"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # An empty value is "no registry named"; every other bad value is a grammar refusal.
    expected = "no registry named" if registry == "" else "not a valid HOST[:PORT][/PATH]"
    assert expected in captured.err


@pytest.mark.parametrize("registry", BAD_REGISTRIES)
def test_each_bad_registry_is_refused_in_a_dry_run_as_well(registry, capsys, no_subprocess):
    assert run([f"--image={IMAGE}", f"--registry={registry}", "--dry-run"]) == 2
    assert capsys.readouterr().out == ""


def test_a_bad_registry_is_never_echoed_back(capsys, no_subprocess):
    assert run(args(registry="someuser:" + PLANTED + "@registry.example.internal")) == 2
    captured = capsys.readouterr()
    assert PLANTED not in captured.out + captured.err
    assert "credentials" in captured.err


GOOD_REGISTRIES = [
    "registry.example.internal",
    "registry.example.internal:5000",
    "registry.example.internal:5000/foss-mcp",
    "registry.example.internal/team/foss-mcp",
    "10.0.0.5:5000/a/b_c.d-e",
    "localhost",
    "r",
    "a-b.c-d.example.internal:65535/x1/y2",
]


@pytest.mark.parametrize("registry", GOOD_REGISTRIES)
def test_each_plain_registry_is_accepted(registry, capsys, no_subprocess):
    assert run(args("--dry-run", registry=registry)) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"docker push {registry}/foss-mcp-serving:rev-abc1234"


# --- image: a local NAME:TAG with an explicit tag, never latest ---------------------------------------------


@pytest.mark.parametrize(
    "image",
    [
        "foss-mcp-serving:latest",
        "foss-mcp-serving:LATEST",
        "foss-mcp-serving",
        "foss-mcp-serving:",
        ":rev-abc1234",
        "",
        "Foss-MCP:rev-1",
        "registry.example.internal:5000/foss-mcp-serving:rev-1",
        "foss-mcp-serving@sha256:" + "a" * 64,
        "foss-mcp-serving:rev 1",
        "foss-mcp-serving:-rev",
        "foss-mcp-serving:rev-1\n",
        "-foss-mcp-serving:rev-1",
        "foss-mcp-serving:rev-1:extra",
        "a/../b:rev-1",
        "foss-mcp-serving:" + "t" * 129,
    ],
)
def test_a_latest_untagged_or_non_local_image_is_refused_before_anything_runs(image, capsys, no_subprocess):
    assert run([f"--image={image}", f"--registry={REGISTRY}"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Nothing was tagged or pushed" in captured.err


def test_a_missing_image_argument_is_an_argparse_error(capsys, no_subprocess):
    with pytest.raises(SystemExit) as excinfo:
        push.parse_args(["--registry", REGISTRY])
    assert excinfo.value.code == 2


def test_defaults_are_not_dry_run_and_no_record():
    parsed = push.parse_args(["--image", IMAGE])
    assert parsed.dry_run is False
    assert parsed.record is None
    assert parsed.registry is None


# --- failures are failures ----------------------------------------------------------------------------------


def test_a_failed_push_exits_nonzero_prints_the_cause_and_records_nothing(monkeypatch, capsys, tmp_path):
    def handler(cmd):
        if cmd[1] == "push":
            return _done(cmd, rc=1, stderr="denied: requested access to the resource is denied")
        return _default_handler(cmd)

    _fake(monkeypatch, handler)
    record = tmp_path / "push-result.json"
    rc = run(args("--record", str(record)))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "denied" in captured.err
    assert not record.exists()


def test_push_output_with_no_digest_line_exits_nonzero_and_records_nothing(monkeypatch, capsys, tmp_path):
    def handler(cmd):
        if cmd[1] == "push":
            return _done(cmd, stdout="The push refers to repository [x]\n8d3ac3489996: Pushed\n")
        return _default_handler(cmd)

    _fake(monkeypatch, handler)
    record = tmp_path / "push-result.json"
    rc = run(args("--record", str(record)))
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert "no captured digest is not a success" in captured.err
    assert not record.exists()


def test_a_missing_local_image_stops_before_tag_and_push(monkeypatch, capsys):
    def handler(cmd):
        if cmd[:3] == ["docker", "image", "inspect"]:
            return _done(cmd, rc=1, stderr="Error: No such image")
        return _default_handler(cmd)

    fake = _fake(monkeypatch, handler)
    assert run(args()) == 1
    assert [c[1] for c in fake.calls] == ["image"]
    assert "was not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "inspect_out", ["", "\n", "not-an-id\n", "sha256:abc\n", "sha256:" + "A" * 64 + "\n"]
)
def test_an_inspect_without_a_sha256_image_id_stops_before_tag_and_push(monkeypatch, capsys, inspect_out):
    fake = _fake(
        monkeypatch,
        lambda cmd: _done(cmd, stdout=inspect_out) if cmd[1] == "image" else _default_handler(cmd),
    )
    assert run(args()) == 1
    assert [c[1] for c in fake.calls] == ["image"]
    assert "sha256:<64 hex> image id" in capsys.readouterr().err


def test_a_failed_tag_stops_before_the_push(monkeypatch, capsys):
    def handler(cmd):
        if cmd[1] == "tag":
            return _done(cmd, rc=1, stderr="tag refused")
        return _default_handler(cmd)

    fake = _fake(monkeypatch, handler)
    assert run(args()) == 1
    assert [c[1] for c in fake.calls] == ["image", "tag"]
    assert "tag refused" in capsys.readouterr().err


def test_a_missing_docker_binary_is_reported_not_raised(monkeypatch, capsys):
    def handler(cmd):
        raise FileNotFoundError("docker")

    _fake(monkeypatch, handler)
    assert run(args()) == 1
    err = capsys.readouterr().err
    assert "FileNotFoundError" in err and "docker" in err


# --- no credential is handled, read, or printed -------------------------------------------------------------


def test_the_source_never_logs_in_and_never_reads_a_docker_client_config():
    text = PUSH_SOURCE.read_text(encoding="utf-8")
    assert "docker login" not in text
    lowered = text.lower()
    assert "login" not in lowered
    assert "config.json" not in lowered
    assert ".docker" not in lowered
    assert "docker_config" not in lowered
    assert "expanduser" not in lowered and "home()" not in lowered
    assert "password" not in lowered and "--username" not in lowered and "stdin" not in lowered


def test_the_source_reads_exactly_one_environment_variable_and_never_dumps_the_environment():
    text = PUSH_SOURCE.read_text(encoding="utf-8")
    assert text.count("os.environ") == 1
    assert "os.environ.get(REGISTRY_ENV" in text
    assert "environ.items" not in text and "environ.copy" not in text and "dict(os.environ" not in text
    assert "getenv" not in text
    assert "env=" not in text


def _plant_credentials(monkeypatch) -> None:
    for name in ("DOCKER_PASSWORD", "REGISTRY_TOKEN", "FOSS_MCP_PLANTED_SECRET", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, PLANTED)


def test_a_credential_planted_in_the_environment_never_appears_in_any_output(monkeypatch, capsys, tmp_path):
    _plant_credentials(monkeypatch)
    fake = _fake(monkeypatch)
    seen: list[str] = []

    def collect() -> None:
        captured = capsys.readouterr()
        seen.append(captured.out + captured.err)

    # dry run, success with a record, refusals, and every failure path
    assert run(args("--dry-run")) == 0
    collect()
    record = tmp_path / "r.json"
    assert run(args("--record", str(record))) == 0
    collect()
    seen.append(record.read_text(encoding="utf-8"))
    assert run(args(registry=None)) == 2
    collect()
    assert run(args(registry="http://r")) == 2
    collect()
    assert run(args(image="x:latest")) == 2
    collect()
    failing_handlers = {
        "push": lambda cmd: _done(cmd, rc=1, stderr="denied") if cmd[1] == "push" else _default_handler(cmd),
        "nodigest": lambda cmd: _done(cmd, stdout="Pushed\n") if cmd[1] == "push" else _default_handler(cmd),
        "inspect": lambda cmd: _done(cmd, rc=1) if cmd[1] == "image" else _default_handler(cmd),
        "tag": lambda cmd: _done(cmd, rc=1) if cmd[1] == "tag" else _default_handler(cmd),
    }
    for name, handler in failing_handlers.items():
        fake.handler = handler
        assert run(args()) != 0, name
        collect()
    assert len(seen) >= 9
    assert all(PLANTED not in text for text in seen)


def test_a_credential_planted_in_the_registry_variable_is_refused_and_never_printed(monkeypatch, capsys):
    monkeypatch.setenv(push.REGISTRY_ENV, f"someuser:{PLANTED}@registry.example.internal")
    fake = _fake(monkeypatch)
    assert run(args(registry=None)) == 2
    captured = capsys.readouterr()
    assert PLANTED not in captured.out + captured.err
    assert fake.calls == []


def test_the_script_run_as_a_process_with_no_registry_exits_two_and_names_the_variable():
    env = {k: v for k, v in os.environ.items() if k != push.REGISTRY_ENV}
    proc = subprocess.run(
        [sys.executable, "-I", str(PUSH_SOURCE), "--image", IMAGE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "no registry named" in proc.stderr and push.REGISTRY_ENV in proc.stderr


# --- the runbook names the tool and every flag --------------------------------------------------------------


def test_the_runbook_names_the_tool_and_every_flag_it_accepts():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/release/push_image.py" in text
    for flag in ("--image", "--registry", "--dry-run", "--record"):
        assert f'"{flag}"' in PUSH_SOURCE.read_text(encoding="utf-8"), f"the tool does not accept {flag}"
        assert flag in text, f"the runbook does not document {flag}"
    assert push.REGISTRY_ENV in text
    assert "release-record" in text


def test_every_flag_the_runbook_shows_with_the_push_tool_is_one_it_accepts():
    text = RUNBOOK.read_text(encoding="utf-8")
    source = PUSH_SOURCE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "push_image.py" in line and "python" in line]
    assert lines, "the runbook shows no command for push_image.py"
    for line in lines:
        for flag in re.findall(r"--[a-z][a-z-]*", line):
            assert f'"{flag}"' in source, (
                f"the runbook uses {flag} with push_image.py, which does not accept it"
            )
