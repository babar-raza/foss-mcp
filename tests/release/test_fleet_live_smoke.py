"""G3/TC-566: scripts/release/fleet_live_smoke.py, offline.

subprocess.run, the HTTP readiness probe, and the MCP client are all replaced by fakes (the same
``gate.Deps`` injection ``scripts/release/image_workflow_gate.py``'s own tests use), so no docker
command ever runs and no container is ever started. Every fact plan is built from a hand-written
fixture dict the test controls directly -- never from a committed fixture, and never from a running
container -- so these tests prove the PLAN-BUILDING and FACT-CHECKING logic, exactly as
``--dry-run`` does for a real run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SOURCE = RELEASE_DIR / "fleet_live_smoke.py"

sys.path.insert(0, str(RELEASE_DIR))

import fleet_live_smoke as smoke  # noqa: E402
import image_workflow_gate as gate  # noqa: E402

IMAGE = "foss-mcp-serving:rev-abc1234"
IMAGE_ID = "sha256:" + "ab12" * 16
RUN_ID = "smoketest1"

ENTRIES = smoke.chart.load_pilots()
KEYS = [smoke.chart.pilot_key(entry) for entry in ENTRIES]
FIRST = KEYS[0]


def entry_of(key: str) -> dict[str, Any]:
    return next(entry for entry in ENTRIES if smoke.chart.pilot_key(entry) == key)


def _type(
    name: str,
    kind: str = "class_declaration",
    *,
    methods: list[dict] | None = None,
    properties: list[dict] | None = None,
    enum_members: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "class_import": name,
        "methods": methods or [],
        "properties": properties or [],
        "enum_members": enum_members or [],
    }


_FULL_FIXTURE = {
    "types": [
        _type(
            "Workbook",
            methods=[
                {"name": "__init__", "is_constructor": True, "params": []},
                {"name": "Save", "params": [{"name": "path"}]},
                {"name": "Create", "params": [], "modifiers": ["static"]},
            ],
        ),
        _type(
            "SaveFormat",
            "enum_declaration",
            enum_members=[{"name": "Xlsx", "value": "0"}, {"name": "Csv", "value": "1"}],
        ),
    ]
}


# --- build_plan: deriving the fact plan from a fixture, never fabricating ---------------------------


def test_build_plan_derives_all_four_facts_from_a_rich_fixture():
    plan = smoke.build_plan("cells_net", _FULL_FIXTURE)
    assert plan.reasons == []
    ids = [fact.id for fact in plan.facts]
    assert ids == ["F1", "F2", "F3", "F4"]
    f1 = plan.facts[0]
    assert (
        f1.tool == "get_symbol" and f1.arguments == {"fqn": "Workbook"} and f1.expect_contains == "Workbook"
    )
    f2 = plan.facts[1]
    assert f2.tool == "search_symbols" and f2.arguments == {"query": "__init__"}
    f3 = plan.facts[2]
    assert f3.expect_contains in {"Xlsx", "Csv"}
    f4 = plan.facts[3]
    assert f4.expect_contains == "Create"


def test_build_plan_picks_the_type_with_the_most_members_as_central():
    fixture = {
        "types": [
            _type("Small", methods=[{"name": "Small", "is_constructor": True, "params": []}]),
            _type(
                "Big",
                methods=[
                    {"name": "Big", "is_constructor": True, "params": []},
                    {"name": "A", "params": []},
                    {"name": "B", "params": []},
                ],
            ),
        ]
    }
    plan = smoke.build_plan("x", fixture)
    assert plan.facts[0].arguments == {"fqn": "Big"}


def test_build_plan_records_a_reason_when_no_class_like_type_exists():
    plan = smoke.build_plan("cells_go", {"types": [{"name": "frob", "kind": "function", "methods": []}]})
    assert any(reason.startswith("F1") for reason in plan.reasons)
    assert not plan.facts


def test_build_plan_records_a_reason_when_the_central_class_has_no_constructor():
    fixture = {"types": [_type("Widget", methods=[{"name": "Build", "params": []}])]}
    plan = smoke.build_plan("x", fixture)
    assert any(reason.startswith("F2") for reason in plan.reasons)
    assert [fact.id for fact in plan.facts] == ["F1"]


def test_build_plan_records_a_reason_when_no_enum_exists():
    fixture = {"types": [_type("Widget", methods=[{"name": "Widget", "is_constructor": True, "params": []}])]}
    plan = smoke.build_plan("x", fixture)
    assert any(reason.startswith("F3") for reason in plan.reasons)


def test_build_plan_records_a_reason_when_no_static_member_exists():
    fixture = {
        "types": [
            _type("Widget", methods=[{"name": "Widget", "is_constructor": True, "params": []}]),
            _type("Kind", "enum_declaration", enum_members=[{"name": "A", "value": "0"}]),
        ]
    }
    plan = smoke.build_plan("x", fixture)
    assert any(reason.startswith("F4") for reason in plan.reasons)
    assert [fact.id for fact in plan.facts] == ["F1", "F2", "F3"]


def test_build_plan_finds_a_static_property_too():
    fixture = {
        "types": [
            _type("Widget", properties=[{"name": "Default", "modifiers": ["static"]}]),
        ]
    }
    plan = smoke.build_plan("x", fixture)
    f4 = next(fact for fact in plan.facts if fact.id == "F4")
    assert f4.expect_contains == "Default"


def test_build_plan_an_empty_fixture_has_every_fact_not_applicable():
    plan = smoke.build_plan("x", {"types": []})
    assert plan.facts == []
    assert len(plan.reasons) == 4


# --- fact_result: substring matching, and the Miss-echo trap -----------------------------------------


def test_fact_result_passes_when_the_tool_result_names_the_expectation():
    fact = smoke.Fact("F1", "name", "get_symbol", {"fqn": "Workbook"}, "Workbook")

    class Client:
        def invoke(self, name, arguments):
            return {"raw_text": "FQN: Workbook\nMethods:\n  - Save() -> void"}

    outcome = smoke.fact_result(Client(), fact)
    assert outcome["status"] == "pass"


def test_fact_result_never_treats_a_miss_reason_as_a_match():
    """A Miss's own reason text often echoes the query (e.g. "no symbol matches 'Workbook'"); that
    must never be read as a real hit.
    """
    fact = smoke.Fact("F2", "name", "search_symbols", {"query": "Workbook"}, "Workbook")

    class Client:
        def invoke(self, name, arguments):
            return {"query": "Workbook", "reason": "no symbol matches 'Workbook'", "suggestions": []}

    outcome = smoke.fact_result(Client(), fact)
    assert outcome["status"] == "fail"


def test_fact_result_fails_on_an_empty_list():
    fact = smoke.Fact("F3", "name", "search_symbols", {"query": "Xlsx"}, "Xlsx")

    class Client:
        def invoke(self, name, arguments):
            return []

    assert smoke.fact_result(Client(), fact)["status"] == "fail"


def test_fact_result_reads_text_from_a_list_of_matches():
    fact = smoke.Fact("F3", "name", "search_symbols", {"query": "Xlsx"}, "Xlsx")

    class Client:
        def invoke(self, name, arguments):
            return [{"text": "FQN: SaveFormat.Xlsx\nKind: enum_item"}]

    assert smoke.fact_result(Client(), fact)["status"] == "pass"


def test_fact_result_fails_cleanly_when_invoke_raises():
    fact = smoke.Fact("F1", "name", "get_symbol", {"fqn": "Workbook"}, "Workbook")

    class Client:
        def invoke(self, name, arguments):
            raise RuntimeError("boom")

    outcome = smoke.fact_result(Client(), fact)
    assert outcome["status"] == "fail" and "boom" in outcome["reason"]


# --- a fake docker/HTTP/MCP world, mirroring tests/deploy/test_image_workflow_gate.py's World -------


class World:
    def __init__(self) -> None:
        self.running: set[str] = set()
        self.port_of: dict[str, int] = {}
        self.container_at_port: dict[int, str] = {}
        self.next_port = 41000
        self.removed: list[str] = []
        self.calls: list[list[str]] = []
        self.client: Any = None
        self.now = 0.0  # a virtual clock: sleep() advances it so a bounded wait actually terminates

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert cmd[0] == "docker", cmd
        verb = cmd[1]
        if verb == "image":
            return subprocess.CompletedProcess(cmd, 0, (IMAGE_ID if "{{.Id}}" in cmd else "123") + "\n", "")
        if verb == "run":
            name = cmd[cmd.index("--name") + 1]
            self.next_port += 1
            self.port_of[name] = self.next_port
            self.container_at_port[self.next_port] = name
            self.running.add(name)
            return subprocess.CompletedProcess(cmd, 0, "0123456789abcdef\n", "")
        name = cmd[-1] if verb != "port" else cmd[2]
        if verb == "port":
            if name not in self.running:
                return subprocess.CompletedProcess(cmd, 1, "", "no such container")
            return subprocess.CompletedProcess(cmd, 0, f"127.0.0.1:{self.port_of[name]}\n", "")
        if verb == "inspect":
            return subprocess.CompletedProcess(cmd, 0, "true false\n", "")
        if verb == "logs":
            return subprocess.CompletedProcess(cmd, 0, "serving started\n", "")
        if verb == "rm":
            self.removed.append(name)
            self.running.discard(name)
            return subprocess.CompletedProcess(cmd, 0, name + "\n", "")
        raise AssertionError(f"unexpected docker command {cmd}")

    def http_get(self, url: str, timeout: float) -> int | None:
        import re

        match = re.fullmatch(r"http://127\.0\.0\.1:(\d+)(/.*)", url)
        assert match is not None, url
        name = self.container_at_port.get(int(match.group(1)))
        if name is None or name not in self.running:
            return None
        return 200 if match.group(2) == "/readyz" else 404

    def open_client(self, url: str):
        return self.client() if callable(self.client) else self.client

    def deps(self, **overrides: Any) -> smoke.Deps:
        values: dict[str, Any] = {
            "run": self.run,
            "http_get": self.http_get,
            "client_factory": self.open_client,
            "sleep": self.sleep,
            "clock": self.clock,
            "environ": {},
            "run_id": RUN_ID,
        }
        values.update(overrides)
        return smoke.Deps(**values)


@pytest.fixture
def world() -> World:
    return World()


class ScriptedClient:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        key = arguments.get("fqn") or arguments.get("query")
        return self.answers.get(key, {"query": key, "reason": f"no symbol matches {key!r}"})


def _write_fixture(fixtures_root: Path, key: str, document: dict[str, Any]) -> None:
    directory = fixtures_root / key
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "api_surface.json").write_text(json.dumps(document), encoding="utf-8")


# --- the dry run --------------------------------------------------------------------------------------


def test_dry_run_builds_the_plan_and_starts_nothing(world, tmp_path, capsys):
    fixtures = tmp_path / "fixtures"
    for key in KEYS:
        _write_fixture(fixtures, key, _FULL_FIXTURE)
    code = smoke.main(["--image", IMAGE, "--fixtures", str(fixtures), "--dry-run"], world.deps())
    assert code == 0
    assert world.calls == [] and world.running == set()
    out = capsys.readouterr().out
    assert "F1" in out and "fact(s) planned" in out


def test_dry_run_reports_not_applicable_reasons_without_starting_docker(world, tmp_path, capsys):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, {"types": []})
    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST, "--dry-run"], world.deps()
    )
    assert code == 0
    assert world.calls == []
    assert "not_applicable" in capsys.readouterr().out


# --- the live run (fully faked docker/http/mcp) -------------------------------------------------------


def test_a_live_run_passes_when_every_fact_matches(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, _FULL_FIXTURE)
    world.client = lambda: ScriptedClient(
        {
            "Workbook": {"raw_text": "FQN: Workbook\nMethods:\n  - Save(path) -> void"},
            "__init__": [{"text": "FQN: Workbook.__init__"}],
            "Xlsx": [{"text": "FQN: SaveFormat.Xlsx"}],
            "Create": [{"text": "FQN: Workbook.Create"}],
        }
    )
    report_dir = tmp_path / "report"
    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST, "--report-dir", str(report_dir)],
        world.deps(),
    )
    assert code == 0, (report_dir / smoke.REPORT_NAME).read_text()
    document = json.loads((report_dir / smoke.REPORT_NAME).read_text(encoding="utf-8"))
    assert document["verdict"] == "PASS"
    assert document["pilots"][0]["key"] == FIRST
    assert all(fact["status"] == "pass" for fact in document["pilots"][0]["facts"])
    assert sorted(world.removed) == [gate.container_name(entry_of(FIRST), RUN_ID)]
    assert not world.running


def test_a_live_run_fails_when_a_fact_never_matches(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, _FULL_FIXTURE)
    world.client = lambda: ScriptedClient({"Workbook": {"raw_text": "FQN: Workbook"}})  # F2/F3/F4 miss
    report_dir = tmp_path / "report"
    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST, "--report-dir", str(report_dir)],
        world.deps(),
    )
    assert code == smoke.EXIT_FAILED
    document = json.loads((report_dir / smoke.REPORT_NAME).read_text(encoding="utf-8"))
    pilot = document["pilots"][0]
    assert pilot["status"] == "fail"
    assert any("F2 failed" in reason for reason in pilot["reasons"])
    assert not world.running, "the container is still removed even when a fact fails"


def test_a_live_run_fails_when_no_fact_could_be_built_at_all(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, {"types": []})
    world.client = lambda: ScriptedClient({})
    code = smoke.main(["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST], world.deps())
    assert code == smoke.EXIT_FAILED


def test_a_readiness_timeout_fails_the_pilot_and_still_removes_the_container(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, _FULL_FIXTURE)

    def never_ready(url, timeout):
        return 503

    deps = world.deps(http_get=never_ready)
    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST, "--ready-timeout", "0.1"], deps
    )
    assert code == smoke.EXIT_FAILED
    assert sorted(world.removed) == [gate.container_name(entry_of(FIRST), RUN_ID)]


def test_a_docker_run_failure_fails_the_pilot(world, tmp_path):
    fixtures = tmp_path / "fixtures"
    _write_fixture(fixtures, FIRST, _FULL_FIXTURE)

    def failing_run(cmd, **kwargs):
        if cmd[1] == "run":
            return subprocess.CompletedProcess(cmd, 125, "", "Conflict")
        return world.run(cmd, **kwargs)

    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST], world.deps(run=failing_run)
    )
    assert code == smoke.EXIT_FAILED


def test_every_selected_pilot_is_run_and_the_container_removed(world, tmp_path):
    second = KEYS[1]
    fixtures = tmp_path / "fixtures"
    for key in (FIRST, second):
        _write_fixture(fixtures, key, _FULL_FIXTURE)
    world.client = lambda: ScriptedClient(
        {
            "Workbook": {"raw_text": "FQN: Workbook"},
            "__init__": [{"text": "__init__"}],
            "Xlsx": [{"text": "Xlsx"}],
            "Create": [{"text": "Create"}],
        }
    )
    code = smoke.main(
        ["--image", IMAGE, "--fixtures", str(fixtures), "--only", FIRST, "--only", second], world.deps()
    )
    assert code == 0
    assert sorted(world.removed) == sorted(
        gate.container_name(entry_of(key), RUN_ID) for key in (FIRST, second)
    )
    assert not world.running


# --- refusals ------------------------------------------------------------------------------------------


def test_an_unknown_only_name_is_refused_before_anything_runs(world, tmp_path, capsys):
    code = smoke.main(["--image", IMAGE, "--only", "no_such_pilot"], world.deps())
    assert code == smoke.EXIT_REFUSED
    assert world.calls == []
    assert "no_such_pilot" in capsys.readouterr().err


def test_an_untagged_image_is_refused(world, capsys):
    code = smoke.main(["--image", "foss-mcp-serving:latest"], world.deps())
    assert code == smoke.EXIT_REFUSED
    assert world.calls == []


def test_a_missing_image_fails_before_any_container_starts(world, tmp_path):
    def missing_image(cmd, **kwargs):
        if cmd[1] == "image":
            return subprocess.CompletedProcess(cmd, 1, "", "no such image")
        return world.run(cmd, **kwargs)

    code = smoke.main(["--image", IMAGE, "--only", FIRST], world.deps(run=missing_image))
    assert code == smoke.EXIT_FAILED
    assert world.running == set()


# --- source hygiene -------------------------------------------------------------------------------------


def test_the_script_reuses_image_workflow_gate_rather_than_copying_it():
    source = SOURCE.read_text(encoding="utf-8")
    assert "import image_workflow_gate as gate" in source
    for reused in ("run_docker", "run_command", "wait_until_ready", "container_name", "host_port"):
        assert f"gate.{reused}" in source, f"fleet_live_smoke does not reuse gate.{reused}"
