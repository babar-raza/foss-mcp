"""G3/TC-345: scripts/release/image_workflow_gate.py, offline.

subprocess.run, the HTTP readiness probe, the MCP client and the clock are all replaced by fakes, so no docker
command ever runs and no container is ever started. Every test that could reach docker either fakes it or proves
it is never called. The pilot count is always derived from the chart, never written down here.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
GATE_SOURCE = RELEASE_DIR / "image_workflow_gate.py"
RUNBOOK = REPO_ROOT / "docs" / "DEPLOYMENT.md"

sys.path.insert(0, str(RELEASE_DIR))

import image_workflow_gate as gate  # noqa: E402

chart = gate.chart

IMAGE = "foss-mcp-serving:rev-abc1234"
IMAGE_ID = "sha256:" + "ab12" * 16
IMAGE_SIZE = 123456789
RUN_ID = "testrun1"
PLANTED_ENDPOINT = "https://gateway-planted.example.test/v1/9f3b"
PLANTED_KEY = "hunter2-planted-api-key-value-7c1d"
SYMBOL = "AutoShapeType"
TOOLS = (
    "lookup",
    "search_docs",
    "search_symbols",
    "get_symbol",
    "list_members",
    "find_examples",
    "get_product_reference",
    "list_recent_changes",
    "report_index_freshness",
)
TOOL_ARGUMENTS = {
    "lookup": ("query", "content_type", "top_k"),
    "search_docs": ("query", "content_type", "top_k"),
    "search_symbols": ("query", "top_k"),
    "get_symbol": ("fqn",),
    "list_members": ("fqn",),
    "find_examples": ("query", "top_k"),
    "get_product_reference": ("section",),
    "list_recent_changes": ("limit",),
    "report_index_freshness": ("source_kind", "current_source_commit"),
}
EXAMPLE_TITLE = "Create a New PDF from Scratch"
EXAMPLE_FQN = f"Example: {EXAMPLE_TITLE}"
UTC_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
SUMMARY_KEYS = {
    "schema_version",
    "image",
    "image_id",
    "image_size_bytes",
    "budget",
    "generated_at",
    "verdict",
    "pilots_total",
    "pilots_passed",
    "pilots",
}
PILOT_KEYS = {"key", "status", "checks", "peak_memory_mb", "startup_seconds", "reasons"}

ENTRIES = chart.load_pilots()
KEYS = [chart.pilot_key(entry) for entry in ENTRIES]
FIRST, SECOND = KEYS[0], KEYS[1]


def entry_of(key: str) -> dict[str, Any]:
    return next(entry for entry in ENTRIES if chart.pilot_key(entry) == key)


def _write_fixture(fixtures_root: Path, key: str, **extra: Any) -> None:
    """A minimal valid ``api_surface.json`` for ``key`` under ``fixtures_root``, carrying a current
    ``extractor_version`` (TC-566: REQUIRE_EXTRACTOR_VERSION) so a test that points ``--fixtures`` at
    a throwaway directory is not refused by the mandatory fixture-version check before anything else
    in the test runs.
    """
    directory = fixtures_root / key
    directory.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {"extractor_version": gate.EXTRACTOR_VERSION, "types": []}
    document.update(extra)
    (directory / "api_surface.json").write_text(json.dumps(document), encoding="utf-8")


def name_of(key: str) -> str:
    return gate.container_name(entry_of(key), RUN_ID)


_NAME = re.compile(r"foss-mcp-gate-(?P<family>[a-z0-9]+)-(?P<platform>[a-z0-9]+)-" + RUN_ID)


def key_of_container(name: str) -> str:
    match = _NAME.fullmatch(name)
    assert match is not None, name
    return f"{match.group('family')}_{match.group('platform')}"


# --- fakes -----------------------------------------------------------------------------------------------------


def make_checks(*, fail=(), na=(), example=True) -> list[dict[str, Any]]:
    """Eleven proof check records, in the shape pilot_workflow_proof.run_checks returns."""
    checks = []
    for number in range(1, 12):
        status = "fail" if number in fail else "not_applicable" if number in na else "pass"
        evidence: dict[str, Any] = {}
        if number == 6 and status == "pass":
            evidence = (
                {"answer": "verified example", "example_fqn": EXAMPLE_FQN}
                if example
                else {"answer": "honest no-verified-example miss"}
            )
        reason = {"fail": "boom", "not_applicable": "not ingested"}.get(status)
        checks.append(
            {
                "id": number,
                "name": f"check {number}",
                "status": status,
                "evidence": evidence,
                "reason": reason,
            }
        )
    return checks


def symbol_matches(symbol: str = SYMBOL) -> list[dict[str, Any]]:
    return [
        {"doc_id": "d1", "chunk_id": "c1", "text": f"FQN: Lib.{symbol}\nKind: enum_item\nMembers:\n  - A"}
    ]


def task_answer(symbol: str | None = SYMBOL) -> dict[str, Any]:
    text = (
        f"FQN: Doc: Guide\nTo draw a shape use {symbol} from the shapes module."
        if symbol
        else "FQN: Doc: Intro\nHello."
    )
    return {
        "scope": {},
        "doc_matches": [{"doc_id": "d2", "chunk_id": "c2", "content_type": "faq", "text": text}],
        "example": None,
    }


def miss(query: str) -> dict[str, Any]:
    return {"scope": {}, "query": query, "reason": f"no symbol matches {query!r}", "suggestions": []}


def example_matches(snippet: str = "doc = Document()") -> list[dict[str, Any]]:
    return [{"fqn": EXAMPLE_FQN, "snippet": snippet, "coverage": 1.0, "source": "lexical"}]


class FakeClient:
    """A scripted MCP client for the workflow steps (the proof itself is replaced separately)."""

    def __init__(self, *, tools=TOOLS, lookup=None, find_examples=None) -> None:
        self.tools = tools
        self.lookup = lookup
        self.find_examples = find_examples
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handshake(self) -> dict[str, Any]:
        return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "foss-mcp"}}

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name, "inputSchema": {}} for name in self.tools]

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if name == "lookup":
            if self.lookup is not None:
                return self.lookup(arguments)
            return task_answer() if arguments["query"].startswith("How do I use") else symbol_matches()
        if name == "find_examples":
            if self.find_examples is not None:
                return self.find_examples(arguments)
            return example_matches()
        raise AssertionError(f"unexpected tool {name}")


class World:
    """A fake docker daemon, readiness endpoint, clock and MCP client, with a log of every command."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.http_urls: list[str] = []
        self.sleeps: list[float] = []
        self.opened: list[str] = []
        self.now = 0.0
        self.next_port = 40000
        self.port_of: dict[str, int] = {}
        self.container_at_port: dict[int, str] = {}
        self.running: set[str] = set()
        self.removed: list[str] = []
        self.ready_after: dict[str, int | None] = {}  # key -> 404s before the first 200; None = never ready
        self.polls: dict[str, int] = {}
        self.run_result: dict[str, tuple[int, str]] = {}  # key -> (returncode, stderr) for `docker run`
        self.stats: dict[str, list[str]] = {}  # key -> successive MemUsage outputs (the last one repeats)
        self.state: dict[str, tuple[int, str]] = {}  # key -> `docker inspect` (returncode, stdout) override
        self.checks: Any = lambda pilot: make_checks()
        self.client: Any = FakeClient
        self.image_inspect_rc = 0

    # subprocess.run
    def run(self, cmd, **kwargs):
        with self.lock:
            self.calls.append(list(cmd))
            self.kwargs.append(dict(kwargs))
            return self._docker(list(cmd))

    def _done(self, cmd, rc=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    def _docker(self, cmd):
        assert cmd[0] == "docker", cmd
        verb = cmd[1]
        if verb == "image":
            if self.image_inspect_rc:
                return self._done(cmd, self.image_inspect_rc, "", "No such image")
            return self._done(cmd, stdout=(IMAGE_ID if "{{.Id}}" in cmd else str(IMAGE_SIZE)) + "\n")
        if verb == "run":
            name = cmd[cmd.index("--name") + 1]
            key = key_of_container(name)
            if key in self.run_result:
                rc, stderr = self.run_result[key]
                if rc:
                    return self._done(cmd, rc, "", stderr)
            self.next_port += 1
            self.port_of[name] = self.next_port
            self.container_at_port[self.next_port] = name
            self.running.add(name)
            return self._done(cmd, stdout="0123456789abcdef\n")
        name = cmd[-1] if verb != "port" else cmd[2]
        key = key_of_container(name)
        if verb == "port":
            if name not in self.running:
                return self._done(cmd, 1, "", "no such container")
            return self._done(cmd, stdout=f"127.0.0.1:{self.port_of[name]}\n")
        if verb == "stats":
            if name not in self.running:
                return self._done(cmd, 1, "", "no such container")
            outputs = self.stats.get(key, ["40.5MiB / 256MiB"])
            return self._done(cmd, stdout=(outputs.pop(0) if len(outputs) > 1 else outputs[0]) + "\n")
        if verb == "inspect":
            if key in self.state:
                rc, out = self.state[key]
                return self._done(cmd, rc, out, "")
            if name not in self.running:
                return self._done(cmd, 1, "", "no such container")
            return self._done(cmd, stdout="true false\n")
        if verb == "logs":
            return self._done(cmd, stdout="serving started\nwaiting for generation\n")
        if verb == "rm":
            self.removed.append(name)
            self.running.discard(name)
            return self._done(cmd, stdout=name + "\n")
        raise AssertionError(f"unexpected docker command {cmd}")

    # the readiness endpoint
    def http_get(self, url: str, timeout: float) -> int | None:
        with self.lock:
            self.http_urls.append(url)
            match = re.fullmatch(r"http://127\.0\.0\.1:(\d+)(/.*)", url)
            assert match is not None, url
            name = self.container_at_port.get(int(match.group(1)))
            if name is None or name not in self.running:
                return None
            if match.group(2) != "/readyz":
                return 404
            key = key_of_container(name)
            limit = self.ready_after.get(key, 0)
            if limit is None:
                return 503
            self.polls[key] = self.polls.get(key, 0) + 1
            return 200 if self.polls[key] > limit else 503

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.sleeps.append(seconds)
            self.now += seconds

    def open_client(self, url: str):
        self.opened.append(url)
        return self.client() if callable(self.client) else self.client

    def run_proof(self, client, pilot):
        checks = self.checks(pilot) if callable(self.checks) else self.checks
        return checks

    def deps(self, **overrides) -> Any:
        values = {
            "run": self.run,
            "http_get": self.http_get,
            "client_factory": self.open_client,
            "proof_runner": self.run_proof,
            "pilot_loader": lambda key, fixtures, chart_values=None: SimpleNamespace(
                name=key, symbol=SYMBOL, probe_error=None
            ),
            "sleep": self.sleep,
            "clock": self.clock,
            "environ": {},
            "run_id": RUN_ID,
            # TC-539: the provenance probes need a committed generation, which the fake world does not have.
            "provenance_runner": lambda client, key, entry: [],
        }
        values.update(overrides)
        return gate.Deps(**values)

    # views
    def docker_run_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["docker", "run"]]

    def rm_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["docker", "rm", "-f"]]


@pytest.fixture
def world() -> World:
    return World()


def run_gate(
    world: World, tmp_path: Path, *extra: str, only=(FIRST,), image=IMAGE, parallel=1, **overrides
) -> int:
    argv = ["--image", image, "--report-dir", str(tmp_path / "report"), "--parallel", str(parallel)]
    for key in only:
        argv += ["--only", key]
    return gate.main([*argv, *extra], world.deps(**overrides))


def summary_of(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "report" / "summary.json").read_text(encoding="utf-8"))


def pilot_report(tmp_path: Path, key: str) -> dict[str, Any]:
    return json.loads((tmp_path / "report" / f"{key}.json").read_text(encoding="utf-8"))


# --- the exact docker run argv ---------------------------------------------------------------------------------


def test_the_docker_run_argv_is_exactly_the_budgeted_peer_start(world, tmp_path):
    assert run_gate(world, tmp_path) == 0
    entry = entry_of(FIRST)
    expected = [
        "docker", "run", "-d", "--rm", "--read-only", "--tmpfs", "/tmp",
        "--memory", "256m", "--cpus", "0.5", "--pids-limit", "256",
        "--publish", "127.0.0.1::8080",
        "--name", name_of(FIRST),
        "-e", f"FOSS_MCP_PILOT={entry['family']}/{entry['platform']}",
        IMAGE,
    ]  # fmt: skip
    assert world.docker_run_calls() == [expected]
    flat = " ".join(expected)
    for forbidden in ("-v ", "--volume", "--mount", "--privileged", "--network host"):
        assert forbidden not in flat
    assert expected[-1] == IMAGE, "a command after the image would override the entrypoint"
    assert [arg for index, arg in enumerate(expected) if expected[index - 1] == "-e"] == [
        f"FOSS_MCP_PILOT={entry['family']}/{entry['platform']}"
    ], "exactly one environment variable, the identity"


def test_memory_cpus_and_the_other_budget_flags_reach_docker(world, tmp_path):
    assert run_gate(world, tmp_path, "--memory", "192m", "--cpus", "0.25", "--ready-timeout", "30") == 0
    (command,) = world.docker_run_calls()
    assert command[command.index("--memory") + 1] == "192m"
    assert command[command.index("--cpus") + 1] == "0.25"
    assert summary_of(tmp_path)["budget"] == {"memory": "192m", "cpus": 0.25}


def test_the_publish_is_loopback_only_and_the_port_comes_from_docker_port(world, tmp_path):
    assert run_gate(world, tmp_path) == 0
    (command,) = world.docker_run_calls()
    assert command[command.index("--publish") + 1] == "127.0.0.1::8080"
    assert ["docker", "port", name_of(FIRST), "8080/tcp"] in world.calls
    port = world.port_of[name_of(FIRST)]
    assert world.http_urls and all(url.startswith(f"http://127.0.0.1:{port}/") for url in world.http_urls)
    assert world.opened == [f"http://127.0.0.1:{port}"]


def test_container_names_are_unique_per_pilot_and_per_run(world, tmp_path):
    assert run_gate(world, tmp_path, only=(FIRST, SECOND)) == 0
    names = [call[call.index("--name") + 1] for call in world.docker_run_calls()]
    assert len(names) == len(set(names)) == 2
    assert gate.container_name(entry_of(FIRST), "run-a") != gate.container_name(entry_of(FIRST), "run-b")
    assert all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", name) for name in names)


# --- readiness ----------------------------------------------------------------------------------------------------


def test_readiness_is_polled_at_the_ready_path_until_it_answers_200(world, tmp_path):
    key = FIRST
    world.ready_after[key] = 2
    assert run_gate(world, tmp_path) == 0
    port = world.port_of[name_of(key)]
    polled = [url for url in world.http_urls]
    assert polled == [f"http://127.0.0.1:{port}/readyz"] * 3
    assert world.sleeps == [gate.POLL_INTERVAL_S] * 2
    assert pilot_report(tmp_path, key)["startup_seconds"] == 2 * gate.POLL_INTERVAL_S
    assert world.opened, "the proof starts only after the container is ready"


def test_a_readiness_timeout_fails_the_pilot_and_still_removes_the_container(world, tmp_path, capsys):
    world.ready_after[FIRST] = None
    assert run_gate(world, tmp_path, "--ready-timeout", "5") == 1
    assert world.opened == [], "no proof runs against a container that never became ready"
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls
    assert name_of(FIRST) not in world.running
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("never became ready" in reason and "5 s" in reason for reason in pilot["reasons"])
    assert any("serving started" in reason for reason in pilot["reasons"]), (
        "the log tail explains the timeout"
    )
    assert pilot["checks"] == {"pass": 0, "fail": 0, "not_applicable": 0}
    assert "FAIL" in capsys.readouterr().out


def test_a_docker_run_failure_fails_the_pilot_and_is_cleaned_up(world, tmp_path):
    world.run_result[FIRST] = (
        125,
        "docker: Error response from daemon: Conflict. The container name is in use",
    )
    assert run_gate(world, tmp_path) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("docker run failed (exit 125)" in reason for reason in pilot["reasons"])
    assert world.opened == [] and world.http_urls == []
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls


# --- the proof, the workflow and the counts ------------------------------------------------------------------


def test_a_failing_proof_check_fails_the_pilot(world, tmp_path):
    world.checks = make_checks(fail={3})
    assert run_gate(world, tmp_path) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert pilot["checks"]["fail"] == 1
    assert any(reason.startswith("check 3 failed: boom") for reason in pilot["reasons"])
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls


def test_not_applicable_is_never_counted_as_a_pass(world, tmp_path):
    world.checks = make_checks(na={5, 6}, example=False)
    assert run_gate(world, tmp_path) == 0
    counts = summary_of(tmp_path)["pilots"][0]["checks"]
    # 9 passing proof checks plus W1 and W2; W3 is not applicable because check 6 found no example.
    assert counts == {"pass": 9 + 2, "fail": 0, "not_applicable": 2 + 1}


def test_the_pass_status_needs_at_least_one_real_pass():
    entry = entry_of(FIRST)
    ctx = SimpleNamespace(memory="256m", memory_mib=256.0, image=IMAGE, cpus=0.5, redact=gate.Redactor([]))
    run = gate.PilotRun(key=FIRST, container="c")
    run.checks = [dict(check, status="not_applicable") for check in make_checks()]
    run.workflow = [gate._entry("W3", "not_applicable", {}, "none")]
    run.samples_mib = [10.0]
    outcome = gate.finalize(entry, run, ctx)["summary"]
    assert outcome["status"] == "fail"
    assert outcome["checks"]["pass"] == 0 and outcome["checks"]["not_applicable"] == 12
    assert outcome["reasons"] == ["no applicable check or workflow entry passed"]


def test_the_workflow_entries_are_recorded_with_evidence_not_a_boolean(world, tmp_path):
    assert run_gate(world, tmp_path) == 0
    report = pilot_report(tmp_path, FIRST)
    entries = {entry["id"]: entry for entry in report["workflow"]}
    assert set(entries) == {"W1", "W2", "W3"}
    assert all(entry["status"] == "pass" for entry in entries.values())
    assert entries["W1"]["evidence"]["tools"] == sorted(TOOLS)
    assert SYMBOL in entries["W2"]["evidence"]["excerpt"]
    assert entries["W2"]["evidence"]["satisfied_by"] == "question"
    assert entries["W3"]["evidence"]["title"] == EXAMPLE_TITLE
    assert entries["W3"]["evidence"]["snippet_excerpt"] == "doc = Document()"
    assert len(report["proof_checks"]) == 11
    assert report["budget"] == {"memory": "256m", "cpus": 0.5}


def test_w1_fails_on_an_empty_or_incomplete_tool_registry():
    empty = gate.workflow_tools(FakeClient(tools=()))
    assert empty["status"] == "fail" and "empty" in empty["reason"]
    partial = gate.workflow_tools(FakeClient(tools=tuple(t for t in TOOLS if t != "list_members")))
    assert partial["status"] == "fail" and "list_members" in partial["reason"]


def test_w2_accepts_a_natural_question_answer_that_names_the_symbol():
    client = FakeClient()
    entry = gate.workflow_lookup_question(client, SimpleNamespace(symbol=SYMBOL, probe_error=None, name="p"))
    assert entry["status"] == "pass"
    assert client.calls == [("lookup", {"query": f"How do I use {SYMBOL}?"})]
    assert entry["evidence"]["satisfied_by"] == "question"


def test_w2_falls_back_to_the_bare_name_and_records_both_attempts():
    def lookup(arguments):
        return task_answer(None) if arguments["query"].startswith("How") else symbol_matches()

    client = FakeClient(lookup=lookup)
    entry = gate.workflow_lookup_question(client, SimpleNamespace(symbol=SYMBOL, probe_error=None, name="p"))
    assert entry["status"] == "pass"
    assert entry["evidence"]["satisfied_by"] == "identifier"
    attempts = entry["evidence"]["attempts"]
    assert [a["form"] for a in attempts] == ["question", "identifier"]
    assert attempts[0]["names_symbol"] is False and attempts[1]["names_symbol"] is True


def test_w2_never_counts_a_miss_that_merely_repeats_the_query():
    client = FakeClient(lookup=lambda arguments: miss(arguments["query"]))
    entry = gate.workflow_lookup_question(client, SimpleNamespace(symbol=SYMBOL, probe_error=None, name="p"))
    assert entry["status"] == "fail"
    assert [a["kind"] for a in entry["evidence"]["attempts"]] == ["miss", "miss"]
    assert "names" in entry["reason"]


def test_w2_requires_the_whole_identifier_not_a_longer_one():
    longer = [{"text": f"FQN: Lib.{SYMBOL}Extended"}]
    assert gate.describe_lookup(longer, SYMBOL)["names_symbol"] is False
    assert gate.describe_lookup([{"text": f"FQN: Lib.{SYMBOL}"}], SYMBOL)["names_symbol"] is True


def test_w2_fails_when_the_pilot_has_no_probe_symbol():
    entry = gate.workflow_lookup_question(
        FakeClient(), SimpleNamespace(symbol=None, probe_error="no usable probe name", name="p")
    )
    assert entry["status"] == "fail" and entry["reason"] == "no usable probe name"


def test_w3_asks_find_examples_for_the_proofs_example_title():
    client = FakeClient()
    entry = gate.workflow_find_examples(client, make_checks())
    assert entry["status"] == "pass"
    assert client.calls == [("find_examples", {"query": EXAMPLE_TITLE})]


def test_w3_is_not_applicable_with_a_reason_when_the_proof_found_no_example():
    client = FakeClient()
    entry = gate.workflow_find_examples(client, make_checks(example=False))
    assert entry["status"] == "not_applicable" and "no verified example" in entry["reason"]
    assert client.calls == []
    failed = gate.workflow_find_examples(client, make_checks(fail={6}))
    assert failed["status"] == "not_applicable" and "fail" in failed["reason"]


def test_w3_fails_on_an_empty_snippet_or_an_empty_answer():
    blank = gate.workflow_find_examples(
        FakeClient(find_examples=lambda a: example_matches("   ")), make_checks()
    )
    assert blank["status"] == "fail" and "empty snippet" in blank["reason"]
    none = gate.workflow_find_examples(
        FakeClient(find_examples=lambda a: {"query": a["query"], "scope": {}}), make_checks()
    )
    assert none["status"] == "fail"


def test_a_workflow_entry_that_raises_is_a_failure_with_its_reason(world, tmp_path):
    def boom(arguments):
        raise RuntimeError("lookup reported a tool error")

    world.client = lambda: FakeClient(lookup=boom)
    assert run_gate(world, tmp_path) == 1
    reasons = summary_of(tmp_path)["pilots"][0]["reasons"]
    assert any(
        "workflow W2 failed" in reason and "lookup reported a tool error" in reason for reason in reasons
    )


def test_a_session_that_cannot_open_fails_every_check_with_the_reason(world, tmp_path):
    def refuse(url):
        raise ConnectionError("connection refused")

    assert run_gate(world, tmp_path, client_factory=refuse, proof_runner=None) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["checks"]["fail"] == 11 + 3
    assert any("could not be opened" in reason for reason in pilot["reasons"])


# --- memory, OOM ----------------------------------------------------------------------------------------------


def test_memory_over_the_budget_fails_the_pilot(world, tmp_path):
    world.stats[FIRST] = ["40.0MiB / 256MiB", "300.5MiB / 256MiB"]
    assert run_gate(world, tmp_path) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail" and pilot["peak_memory_mb"] == 300.5
    assert any("exceeds the 256m budget" in reason for reason in pilot["reasons"])


def test_the_larger_of_the_before_and_after_samples_is_the_peak(world, tmp_path):
    world.stats[FIRST] = ["10MiB / 256MiB", "55.5MiB / 256MiB"]
    assert run_gate(world, tmp_path) == 0
    assert summary_of(tmp_path)["pilots"][0]["peak_memory_mb"] == 55.5
    assert pilot_report(tmp_path, FIRST)["memory_samples_mib"] == [10.0, 55.5]
    world2 = World()
    world2.stats[SECOND] = ["80MiB / 256MiB", "20MiB / 256MiB"]
    assert run_gate(world2, tmp_path / "again", only=(SECOND,)) == 0
    assert (
        json.loads((tmp_path / "again" / "report" / "summary.json").read_text())["pilots"][0][
            "peak_memory_mb"
        ]
        == 80.0
    )


def test_an_oom_killed_container_fails_the_pilot(world, tmp_path):
    world.state[FIRST] = (0, "false true\n")
    assert run_gate(world, tmp_path) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("OOM-killed" in reason for reason in pilot["reasons"])
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls


def test_a_container_that_vanished_during_the_checks_fails_the_pilot(world, tmp_path):
    world.state[FIRST] = (1, "")
    assert run_gate(world, tmp_path) == 1
    reasons = summary_of(tmp_path)["pilots"][0]["reasons"]
    assert any("container is gone" in reason for reason in reasons)


def test_a_pilot_whose_memory_cannot_be_measured_does_not_pass(world, tmp_path):
    world.stats[FIRST] = ["not a number"]
    assert run_gate(world, tmp_path) == 1
    assert any("could not be measured" in r for r in summary_of(tmp_path)["pilots"][0]["reasons"])


@pytest.mark.parametrize(
    ("text", "mib"),
    [
        ("45.3MiB / 256MiB", 45.3),
        ("1.5GiB / 4GiB", 1536.0),
        ("512kB / 1GiB", 512000 / 1024**2),
        ("2048KiB / 1GiB", 2.0),
        ("100MB / 1GiB", 100_000_000 / 1024**2),
        ("0B / 0B", 0.0),
    ],
)
def test_docker_stats_usage_is_parsed_to_mib(text, mib):
    assert gate.parse_stats_mib(text) == pytest.approx(mib)


def test_unreadable_stats_output_gives_none():
    for text in ("", "--", "12 parsecs / 1GiB", "abc"):
        assert gate.parse_stats_mib(text) is None


@pytest.mark.parametrize(
    ("text", "mib"), [("256m", 256.0), ("1g", 1024.0), ("512M", 512.0), ("1048576", 1.0), ("2048k", 2.0)]
)
def test_the_memory_budget_is_parsed_like_docker_does(text, mib):
    assert gate.parse_memory_limit_mib(text) == mib


def test_a_malformed_memory_budget_is_refused_by_the_parser():
    for text in ("", "0m", "-5m", "1.5g", "256mb", "m", "256 m"):
        with pytest.raises(SystemExit) as raised:
            gate.parse_args(["--image", IMAGE, "--memory", text])
        assert raised.value.code == 2


# --- selection and refusals ------------------------------------------------------------------------------------


def test_only_selects_the_named_pilots_in_chart_order(world, tmp_path):
    assert run_gate(world, tmp_path, only=(SECOND, FIRST)) == 0
    summary = summary_of(tmp_path)
    assert [pilot["key"] for pilot in summary["pilots"]] == [FIRST, SECOND]
    assert summary["pilots_total"] == 2


def test_an_unknown_only_name_exits_2_and_runs_nothing(world, tmp_path, capsys):
    assert run_gate(world, tmp_path, only=(FIRST, "no_such_pilot")) == 2
    assert world.calls == [] and world.http_urls == []
    assert not (tmp_path / "report").exists()
    err = capsys.readouterr().err
    assert "no_such_pilot" in err and "unknown pilot" in err


@pytest.mark.parametrize(
    "image",
    [
        "foss-mcp-serving:latest",
        "foss-mcp-serving:LATEST",
        "foss-mcp-serving",
        "registry.example.internal:5000/foss-mcp-serving",
        "Foss:1",
        "foss-mcp-serving:",
        "foss mcp:1",
        "",
    ],
)
def test_latest_untagged_and_malformed_images_are_refused_before_anything_runs(
    world, tmp_path, image, capsys
):
    assert run_gate(world, tmp_path, image=image) == 2
    assert world.calls == []
    assert "Nothing was run" in capsys.readouterr().err


def test_an_image_that_reads_as_a_docker_option_is_refused():
    for image in ("-rm:1", "--privileged:1", "-v/:/x:1"):
        with pytest.raises(gate.Refused):
            gate.validate_image(image)


@pytest.mark.parametrize(
    "image",
    [IMAGE, "registry.example.internal:5000/foss-mcp/foss-mcp-serving:rev-abc1234", "foss-mcp-serving:1.2.3"],
)
def test_explicit_tags_are_accepted(image):
    assert gate.validate_image(image) == image


def test_a_missing_image_stops_before_any_container_starts(world, tmp_path):
    world.image_inspect_rc = 1
    assert run_gate(world, tmp_path) == 1
    assert world.docker_run_calls() == []


def test_bad_numbers_are_refused_by_the_argument_parser():
    for flag, value in (("--parallel", "0"), ("--cpus", "0"), ("--cpus", "-1"), ("--ready-timeout", "0")):
        with pytest.raises(SystemExit) as raised:
            gate.parse_args(["--image", IMAGE, flag, value])
        assert raised.value.code == 2


# --- the dry run -------------------------------------------------------------------------------------------------


def test_the_dry_run_runs_nothing_and_writes_nothing(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError(f"subprocess must not run: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(gate.urllib.request, "urlopen", boom)
    code = gate.main(
        ["--image", IMAGE, "--report-dir", str(tmp_path / "report"), "--dry-run"],
        gate.Deps(environ={}, run_id=RUN_ID),
    )
    assert code == 0
    out = capsys.readouterr().out
    run_lines = [line for line in out.splitlines() if line.startswith("docker run ")]
    assert len(run_lines) == len(KEYS), "one docker run line per chart pilot"
    assert f"FOSS_MCP_PILOT={ENTRIES[0]['family']}/{ENTRIES[0]['platform']}" in run_lines[0]
    assert IMAGE in run_lines[0]
    assert not (tmp_path / "report").exists()


def test_the_dry_run_validates_like_a_real_run(world, tmp_path):
    assert run_gate(world, tmp_path, "--dry-run", image="foss-mcp-serving:latest") == 2
    assert run_gate(world, tmp_path, "--dry-run", only=("no_such_pilot",)) == 2
    assert world.calls == []


# --- secrets -----------------------------------------------------------------------------------------------------


GATEWAY_ENVIRON = {
    gate.GATEWAY_ENV_NAMES[0]: PLANTED_ENDPOINT,
    gate.GATEWAY_ENV_NAMES[1]: PLANTED_KEY,
}


def test_gateway_variable_names_are_passed_only_with_the_flag(world, tmp_path):
    assert run_gate(world, tmp_path, environ=dict(GATEWAY_ENVIRON)) == 0
    (without,) = world.docker_run_calls()
    assert not any(name in without for name in gate.GATEWAY_ENV_NAMES)

    flagged = World()
    assert (
        run_gate(flagged, tmp_path / "flagged", "--with-embedding-gateway", environ=dict(GATEWAY_ENVIRON))
        == 0
    )
    (with_flag,) = flagged.docker_run_calls()
    env_args = [with_flag[i + 1] for i, arg in enumerate(with_flag) if arg == "-e"]
    assert env_args[1:] == list(gate.GATEWAY_ENV_NAMES), "the two names, bare, after the identity variable"
    assert all("=" not in name for name in env_args[1:])


def test_gateway_values_never_appear_in_any_argv_report_or_output(world, tmp_path, capsys):
    world.run_result[SECOND] = (
        1,
        f"docker: error while contacting {PLANTED_ENDPOINT} with key {PLANTED_KEY}",
    )

    def leaky_checks(pilot):
        checks = make_checks(fail={2})
        checks[1]["reason"] = f"echoed {PLANTED_KEY}"
        checks[1]["evidence"] = {"url": PLANTED_ENDPOINT}
        return checks

    world.checks = leaky_checks
    code = run_gate(
        world,
        tmp_path,
        "--with-embedding-gateway",
        only=(FIRST, SECOND),
        environ=dict(GATEWAY_ENVIRON),
    )
    assert code == 1
    captured = capsys.readouterr()
    haystacks = [captured.out, captured.err, *[" ".join(call) for call in world.calls]]
    haystacks += [path.read_text(encoding="utf-8") for path in (tmp_path / "report").glob("*.json")]
    assert len(haystacks) > 6
    for text in haystacks:
        assert PLANTED_KEY not in text and PLANTED_ENDPOINT not in text
    assert "[redacted]" in (tmp_path / "report" / "summary.json").read_text(encoding="utf-8")


def test_the_flag_without_both_variables_is_refused_and_names_only_the_missing_ones(world, tmp_path, capsys):
    only_endpoint = {gate.GATEWAY_ENV_NAMES[0]: PLANTED_ENDPOINT}
    assert run_gate(world, tmp_path, "--with-embedding-gateway", environ=only_endpoint) == 2
    assert world.calls == []
    err = capsys.readouterr().err
    assert gate.GATEWAY_ENV_NAMES[1] in err and PLANTED_ENDPOINT not in err
    assert gate.GATEWAY_ENV_NAMES[0] not in err


# --- summary.json, the exit code, cleanup ------------------------------------------------------------------------


def test_summary_json_has_exactly_the_contract_fields(world, tmp_path):
    world.stats[FIRST] = ["20.25MiB / 256MiB", "33.5MiB / 256MiB"]
    assert run_gate(world, tmp_path, only=(FIRST, SECOND)) == 0
    summary = summary_of(tmp_path)
    assert set(summary) == SUMMARY_KEYS
    assert summary["schema_version"] == 1
    assert summary["image"] == IMAGE
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", summary["image_id"]) and summary["image_id"] == IMAGE_ID
    assert summary["image_size_bytes"] == IMAGE_SIZE and isinstance(summary["image_size_bytes"], int)
    assert summary["budget"] == {"memory": "256m", "cpus": 0.5}
    assert UTC_ISO.fullmatch(summary["generated_at"])
    assert summary["verdict"] == "PASS"
    assert summary["pilots_total"] == 2 and summary["pilots_passed"] == 2
    assert len(summary["pilots"]) == 2
    for pilot in summary["pilots"]:
        assert set(pilot) == PILOT_KEYS
        assert pilot["status"] == "pass" and pilot["reasons"] == []
        assert set(pilot["checks"]) == {"pass", "fail", "not_applicable"}
        assert all(isinstance(n, int) for n in pilot["checks"].values())
        assert isinstance(pilot["peak_memory_mb"], int | float)
        assert isinstance(pilot["startup_seconds"], int | float)
    assert summary["pilots"][0]["peak_memory_mb"] == 33.5
    assert (tmp_path / "report" / f"{FIRST}.json").is_file() and (
        tmp_path / "report" / f"{SECOND}.json"
    ).is_file()


def test_the_exit_code_is_zero_only_when_every_pilot_passes(world, tmp_path, capsys):
    calls = {"count": 0}

    def alternate(pilot):
        calls["count"] += 1
        return make_checks(fail={4}) if pilot.name == SECOND else make_checks()

    world.checks = alternate
    assert run_gate(world, tmp_path, only=(FIRST, SECOND)) == 1
    summary = summary_of(tmp_path)
    assert summary["verdict"] == "FAIL"
    assert summary["pilots_total"] == 2 and summary["pilots_passed"] == 1
    assert [p["status"] for p in summary["pilots"]] == ["pass", "fail"]
    out = capsys.readouterr().out
    assert "verdict: FAIL (1 of 2 pilots passed)" in out
    assert sum(1 for line in out.splitlines() if line.startswith(tuple(KEYS))) == 2, (
        "one table line per pilot"
    )
    world2 = World()
    assert run_gate(world2, tmp_path / "ok", only=(FIRST, SECOND)) == 0
    assert "verdict: PASS (2 of 2 pilots passed)" in capsys.readouterr().out


def test_every_chart_pilot_is_run_and_the_count_comes_from_the_chart(world, tmp_path):
    assert run_gate(world, tmp_path, only=(), parallel=2) == 0
    summary = summary_of(tmp_path)
    assert summary["pilots_total"] == summary["pilots_passed"] == len(chart.load_pilots())
    assert [p["key"] for p in summary["pilots"]] == KEYS
    assert len(world.docker_run_calls()) == len(KEYS)
    assert sorted(world.removed) == sorted(name_of(key) for key in KEYS)
    assert not world.running, "no container is left behind"


def test_the_container_is_removed_on_every_outcome(world, tmp_path):
    world.ready_after[SECOND] = None
    world.checks = make_checks(fail={1})
    assert run_gate(world, tmp_path, "--ready-timeout", "2", only=(FIRST, SECOND)) == 1
    assert sorted(world.removed) == sorted([name_of(FIRST), name_of(SECOND)])
    assert not world.running


def test_cleanup_runs_when_the_run_is_interrupted(world, tmp_path, capsys):
    def interrupt(client, pilot):
        raise KeyboardInterrupt

    code = run_gate(world, tmp_path, only=(FIRST, SECOND), proof_runner=interrupt)
    assert code == gate.EXIT_INTERRUPTED
    started = [call[call.index("--name") + 1] for call in world.docker_run_calls()]
    assert started, "at least the first container was started"
    assert set(started) <= set(world.removed), "every container this run started was removed"
    assert not world.running
    assert not (tmp_path / "report" / "summary.json").exists(), "an interrupted run claims no verdict"
    assert "interrupted" in capsys.readouterr().err


def test_run_pilot_removes_its_container_when_a_keyboard_interrupt_passes_through(world, tmp_path):
    _write_fixture(tmp_path, FIRST)
    ctx = gate.Context(
        image=IMAGE,
        memory="256m",
        memory_mib=256.0,
        cpus=0.5,
        ready_timeout=10.0,
        fixtures=tmp_path,
        report_dir=tmp_path / "report",
        with_gateway=False,
        run_id=RUN_ID,
        deps=world.deps(proof_runner=lambda client, pilot: (_ for _ in ()).throw(KeyboardInterrupt())),
        proof=gate._load_proof_module(),
        containers=gate.Containers(world.deps()),
        redact=gate.Redactor([]),
    )
    with pytest.raises(KeyboardInterrupt):
        gate.run_pilot(entry_of(FIRST), ctx)
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls
    assert not world.running


def test_a_second_session_never_overlaps_the_first(world, tmp_path):
    """tests/e2e's client keeps its base URL in a module global, so two live sessions would cross wires."""
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    def slow_proof(client, pilot):
        with guard:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.03)
        with guard:
            active["now"] -= 1
        return make_checks()

    assert run_gate(world, tmp_path, only=(FIRST, SECOND), parallel=2, proof_runner=slow_proof) == 0
    assert active["max"] == 1


# --- wiring: the real entry points are the ones called -------------------------------------------------------------


def test_the_proof_module_still_offers_what_the_gate_calls():
    proof = gate._load_proof_module()
    for name in ("load_pilot", "run_checks", "open_live_client", "unrunnable_checks", "choose_probe"):
        assert callable(getattr(proof, name)), f"scripts/poc/pilot_workflow_proof.py lost {name}"


def test_the_gate_source_calls_the_existing_pilot_and_proof_functions():
    tree = ast.parse(GATE_SOURCE.read_text(encoding="utf-8"))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for needed in ("select_pilots", "load_pilots", "pilot_key"):
        assert needed in called, f"the gate does not call pilots.{needed}"
    for needed in ("load_pilot", "run_checks", "open_live_client", "unrunnable_checks"):
        assert needed in attributes, f"the gate does not reach the proof's {needed}"


def test_the_default_wiring_calls_the_real_proof_entry_points(world, tmp_path, monkeypatch):
    proof = gate._load_proof_module()
    seen: dict[str, list[Any]] = {"load": [], "open": [], "checks": []}

    def fake_load(key, fixtures, chart_values=None):
        seen["load"].append((key, Path(fixtures), chart_values))
        return SimpleNamespace(name=key, symbol=SYMBOL, probe_error=None)

    def fake_open(url):
        seen["open"].append(url)
        return FakeClient()

    def fake_checks(client, pilot):
        seen["checks"].append(pilot.name)
        return make_checks()

    monkeypatch.setattr(proof, "load_pilot", fake_load)
    monkeypatch.setattr(proof, "open_live_client", fake_open)
    monkeypatch.setattr(proof, "run_checks", fake_checks)
    deps = world.deps(client_factory=None, proof_runner=None, pilot_loader=None)
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST)
    argv = [
        "--image",
        IMAGE,
        "--report-dir",
        str(tmp_path / "r"),
        "--only",
        FIRST,
        "--fixtures",
        str(fixtures),
    ]
    assert gate.main(argv, deps) == 0
    assert seen["load"] == [(FIRST, fixtures, chart.CHART_VALUES)]
    assert seen["open"] == [f"http://127.0.0.1:{world.port_of[name_of(FIRST)]}"]
    assert seen["checks"] == [FIRST]


def test_the_real_proof_runs_end_to_end_against_a_scripted_server(world, tmp_path):
    """The real load_pilot and the real eleven checks, over a client that answers like a healthy cells_rust."""
    pilot = "cells_rust"
    assert pilot in KEYS
    root = tmp_path / "fixtures"
    sha = "1a6004af47b1ef15385f9d36d381a8172428cc7e"
    types = {
        pilot: [
            {"name": SYMBOL, "kind": "enum_item", "enum_members": [{"name": "Rectangle"}], "methods": []},
        ],
        "pdf_net": [{"name": "PdfDocumentSignatureField", "kind": "struct_item", "methods": []}],
    }
    for fixture, entries in types.items():
        (root / fixture).mkdir(parents=True)
        (root / fixture / "api_surface.json").write_text(
            json.dumps({"source_commit": sha, "extractor_version": gate.EXTRACTOR_VERSION, "types": entries}),
            encoding="utf-8",
        )
    scope = {"family": "cells", "platform": "rust", "source_kind": "self_extracted"}
    generation = "cells::rust::self_extracted::gen-1"

    def hit(symbol):
        text = f"FQN: Lib.{symbol}\nKind: enum_item\nMembers:\n  - Rectangle\n"
        return [{"scope": scope, "generation_id": generation, "doc_id": "d1", "chunk_id": "c1", "text": text}]

    def answer(name, arguments):
        query = arguments.get("query", "")
        if name == "search_symbols":
            return hit(SYMBOL) if query == SYMBOL else miss(query)
        if name == "lookup":
            if query == SYMBOL:
                return hit(SYMBOL)
            return task_answer() if query.startswith("How do I use") else miss(query)
        if name == "get_symbol":
            return {
                "fqn": arguments["fqn"],
                "kind": "enum_item",
                "methods": [],
                "properties": [],
                "members": ["Rectangle"],
            }
        if name == "list_members":
            return ["Rectangle"]
        if name == "find_examples":
            return example_matches()
        if name == "get_product_reference":
            return {"section": arguments["section"], "text": "cargo add cells"}
        if name == "report_index_freshness":
            return {"scope": scope, "indexed_generation_id": generation, "stale": False, "reason": "same"}
        if name == "list_recent_changes":
            return []
        raise AssertionError(name)

    class Server(FakeClient):
        def list_tools(self):
            return [
                {
                    "name": name,
                    "inputSchema": {
                        "type": "object",
                        "properties": {a: {"type": "string"} for a in TOOL_ARGUMENTS[name]},
                    },
                }
                for name in TOOLS
            ]

        def invoke(self, name, arguments):
            return answer(name, arguments)

    code = run_gate(
        world,
        tmp_path,
        "--fixtures",
        str(root),
        only=(pilot,),
        client_factory=lambda url: Server(),
        proof_runner=None,
        pilot_loader=None,
    )
    report = pilot_report(tmp_path, pilot)
    assert code == 0, report["reasons"]
    assert [c["status"] for c in report["proof_checks"]].count("pass") == 10
    assert next(c for c in report["proof_checks"] if c["id"] == 5)["status"] == "not_applicable"
    assert {e["id"]: e["status"] for e in report["workflow"]} == {"W1": "pass", "W2": "pass", "W3": "pass"}
    assert next(e for e in report["workflow"] if e["id"] == "W3")["evidence"]["title"] == EXAMPLE_TITLE


# --- the runbook ---------------------------------------------------------------------------------------------------


def test_the_runbook_documents_the_gate_and_every_flag_it_accepts():
    text = RUNBOOK.read_text(encoding="utf-8")
    source = GATE_SOURCE.read_text(encoding="utf-8")
    assert "scripts/release/image_workflow_gate.py" in text
    flags = sorted(set(re.findall(r'add_argument\(\s*"(--[a-z][a-z-]*)"', source)))
    assert {
        "--image",
        "--only",
        "--fixtures",
        "--report-dir",
        "--memory",
        "--cpus",
        "--ready-timeout",
    } <= set(flags)
    assert {"--parallel", "--dry-run", "--with-embedding-gateway"} <= set(flags)
    for flag in flags:
        assert flag in text, f"the runbook does not document {flag}"
    # re-pinned by TC-551: 40; 40 -> 32; 32
    assert "summary.json" in text and "32-of-32" in text
    assert "--gate-report" in text


def test_every_flag_the_runbook_shows_with_the_gate_is_one_it_accepts():
    text = RUNBOOK.read_text(encoding="utf-8")
    source = GATE_SOURCE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "image_workflow_gate.py" in line and "python" in line]
    assert lines, "the runbook shows no command for image_workflow_gate.py"
    for line in lines:
        for flag in re.findall(r"--[a-z][a-z-]*", line):
            assert f'"{flag}"' in source, f"the runbook uses {flag} with the gate, which does not accept it"


# --- TC-539: the served-versus-committed provenance probes -------------------------------------------------------


def provenance_entries(p1: str = "pass", p2: str = "pass") -> list[dict[str, Any]]:
    return [
        gate._entry("P1", p1, {"served_generation_id": "g"}, "differs" if p1 == "fail" else None),
        gate._entry("P2", p2, {"sampled": 30}, "signature differs" if p2 == "fail" else None),
    ]


def test_the_gate_calls_the_provenance_runner_once_per_pilot_with_its_chart_entry(world, tmp_path):
    calls: list[tuple[Any, str, dict[str, Any]]] = []

    def runner(client, key, entry):
        calls.append((client, key, entry))
        return []

    assert run_gate(world, tmp_path, only=(FIRST, SECOND), provenance_runner=runner) == 0
    assert [key for _client, key, _entry in calls] == [FIRST, SECOND]
    for client, key, entry in calls:
        assert isinstance(client, FakeClient)
        assert entry == entry_of(key)
        assert "maxTypes" in entry


def test_provenance_entries_are_recorded_beside_w1_to_w3_in_the_pilot_report(world, tmp_path):
    assert run_gate(world, tmp_path, provenance_runner=lambda c, k, e: provenance_entries()) == 0
    report = pilot_report(tmp_path, FIRST)
    assert [entry["id"] for entry in report["workflow"]] == ["W1", "W2", "W3", "P1", "P2"]
    by_id = {entry["id"]: entry for entry in report["workflow"]}
    for number, title in gate.served_provenance.PROVENANCE_TITLES.items():
        assert by_id[number]["name"] == title
    assert by_id["P2"]["evidence"] == {"sampled": 30}
    assert set(summary_of(tmp_path)["pilots"][0]) == PILOT_KEYS, (
        "the summary keys gatectl reads do not change"
    )


def test_a_failing_provenance_entry_fails_the_pilot_with_its_reason(world, tmp_path):
    assert run_gate(world, tmp_path, provenance_runner=lambda c, k, e: provenance_entries(p1="fail")) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("workflow P1 failed" in reason and "differs" in reason for reason in pilot["reasons"])
    assert ["docker", "rm", "-f", name_of(FIRST)] in world.calls


def test_a_passing_provenance_entry_is_counted_as_a_pass(world, tmp_path):
    assert run_gate(world, tmp_path, provenance_runner=lambda c, k, e: provenance_entries()) == 0
    # 11 proof checks, W1 to W3, then P1 and P2.
    assert summary_of(tmp_path)["pilots"][0]["checks"] == {"pass": 11 + 3 + 2, "fail": 0, "not_applicable": 0}


def test_the_default_runner_is_served_provenance_run_probes(world, tmp_path, monkeypatch):
    seen: list[dict[str, Any]] = []

    def recorder(client, key, entry, *, fixtures, manifests_dir):
        seen.append({"key": key, "entry": entry, "fixtures": fixtures, "manifests_dir": manifests_dir})
        return provenance_entries()

    monkeypatch.setattr(gate.served_provenance, "run_probes", recorder)
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST)
    assert run_gate(world, tmp_path, "--fixtures", str(fixtures), provenance_runner=None) == 0
    assert seen == [
        {
            "key": FIRST,
            "entry": entry_of(FIRST),
            "fixtures": fixtures,
            "manifests_dir": gate.DEFAULT_MANIFESTS,
        }
    ]
    assert gate.DEFAULT_MANIFESTS == REPO_ROOT / "infra" / "demo-manifests"
    assert [e["id"] for e in pilot_report(tmp_path, FIRST)["workflow"]] == ["W1", "W2", "W3", "P1", "P2"]


def _calls_of(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_gate_source_imports_and_calls_the_provenance_module():
    gate_tree = ast.parse(GATE_SOURCE.read_text(encoding="utf-8"))
    imported = [
        alias.name for node in ast.walk(gate_tree) if isinstance(node, ast.Import) for alias in node.names
    ]
    assert "served_provenance" in imported
    attribute_calls = [
        (call.func.value.id, call.func.attr)
        for call in _calls_of(gate_tree)
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
    ]
    assert ("served_provenance", "run_probes") in attribute_calls

    probe_tree = ast.parse((RELEASE_DIR / "served_provenance.py").read_text(encoding="utf-8"))
    (run_probes,) = [
        node
        for node in ast.walk(probe_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_probes"
    ]
    called = {call.func.id for call in _calls_of(run_probes) if isinstance(call.func, ast.Name)}
    assert {"probe_generation", "probe_signatures"} <= called


# --- TC-566: the mandated REQUIRE_EXTRACTOR_VERSION switch (RD3-150/RD3-154) ------------------------


def test_require_extractor_version_defaults_to_true():
    assert gate.REQUIRE_EXTRACTOR_VERSION is True


def test_check_extractor_version_accepts_the_current_schema(tmp_path):
    _write_fixture(tmp_path, FIRST, extractor_version=gate.EXTRACTOR_VERSION)
    assert gate.check_extractor_version(entry_of(FIRST), tmp_path, FIRST) is None


def test_check_extractor_version_refuses_a_stale_fixture_and_names_the_pilot(tmp_path):
    _write_fixture(tmp_path, FIRST, extractor_version=gate.EXTRACTOR_VERSION - 1)
    reason = gate.check_extractor_version(entry_of(FIRST), tmp_path, FIRST)
    assert reason is not None
    assert FIRST in reason
    assert str(gate.EXTRACTOR_VERSION) in reason


def test_check_extractor_version_refuses_a_fixture_with_no_version_key(tmp_path):
    directory = tmp_path / FIRST
    directory.mkdir(parents=True)
    (directory / "api_surface.json").write_text(json.dumps({"types": []}), encoding="utf-8")
    reason = gate.check_extractor_version(entry_of(FIRST), tmp_path, FIRST)
    assert reason is not None and FIRST in reason


def test_check_extractor_version_refuses_an_unreadable_fixture(tmp_path):
    reason = gate.check_extractor_version(entry_of(FIRST), tmp_path, FIRST)
    assert reason is not None and "api_surface.json" in reason


def test_a_stale_fixture_fails_the_pilot_before_any_container_starts(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, extractor_version=1)
    assert run_gate(world, tmp_path, "--fixtures", str(fixtures)) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("extractor_version" in reason and FIRST in reason for reason in pilot["reasons"])
    assert world.docker_run_calls() == [], "a stale fixture must never even start a container"


def test_a_missing_extractor_version_key_fails_the_pilot(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    directory = fixtures / FIRST
    directory.mkdir(parents=True)
    (directory / "api_surface.json").write_text(json.dumps({"types": []}), encoding="utf-8")
    assert run_gate(world, tmp_path, "--fixtures", str(fixtures)) == 1
    pilot = summary_of(tmp_path)["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("extractor_version" in reason for reason in pilot["reasons"])


def test_turning_the_switch_off_lets_a_stale_fixture_pass_again(world, tmp_path, monkeypatch):
    """The negative control's exact falsifier: flipping REQUIRE_EXTRACTOR_VERSION to False must make a
    stale-extractor fixture pass the gate again -- proving the switch genuinely gates this behavior.
    """
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, extractor_version=1)
    monkeypatch.setattr(gate, "REQUIRE_EXTRACTOR_VERSION", False)
    assert run_gate(world, tmp_path, "--fixtures", str(fixtures)) == 0
    assert summary_of(tmp_path)["pilots"][0]["status"] == "pass"
    assert world.docker_run_calls(), "with the switch off, the pilot's container is started as before"


def test_the_extractor_version_is_imported_never_retyped():
    source = GATE_SOURCE.read_text(encoding="utf-8")
    assert "from foss_mcp.extraction.run_extraction import EXTRACTOR_VERSION" in source
    assert "REQUIRE_EXTRACTOR_VERSION = True" in source


def test_the_gate_adds_no_command_line_flag():
    source = GATE_SOURCE.read_text(encoding="utf-8")
    flags = set(re.findall(r'add_argument\(\s*"(--[a-z][a-z-]*)"', source))
    assert flags == {
        "--image",
        "--only",
        "--fixtures",
        "--report-dir",
        "--memory",
        "--cpus",
        "--ready-timeout",
        "--parallel",
        "--with-embedding-gateway",
        "--dry-run",
    }
