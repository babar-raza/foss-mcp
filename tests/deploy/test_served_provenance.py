"""G3/TC-539 (REQ-G3-008, REQ-G3-001): scripts/release/served_provenance.py, offline.

P1 (the served generation id equals the committed active pointer) and P2 (30 sampled signatures served by
get_symbol and list_members equal the committed fixture rendered by the production chunk builder) are pinned
twice. Unit tests drive the pure probe functions with a scripted client. The differential tests drive the REAL
``tools/call`` handler (``create_server``) over a COPY of every pilot's committed scope directory, so the probes
are proven against the data that ships and against a generation that was changed after the fact. Nothing here
starts docker, opens a socket, sleeps or reads the clock.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import create_server
from foss_mcp.mcp.tools.get_symbol import extract_fqn

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
GUARD_SECONDS = 120

for _directory in (RELEASE_DIR, DEPLOY_DIR):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import pilots as chart  # noqa: E402
import served_provenance as sp  # noqa: E402

CHART = {chart.pilot_key(entry): entry for entry in chart.load_pilots()}


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


# --- a small fixture and a scripted client -----------------------------------------------------------------------


def method(name: str, params: tuple[tuple[str, str], ...] = (), ret: str | None = "void") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "params": [{"name": n, "type": t} for n, t in params]}
    if ret is not None:
        entry["return_type"] = ret
    return entry


def small_fixture(type_count: int = 12, methods_per_type: int = 3) -> dict[str, Any]:
    types = []
    for index in range(type_count):
        methods = [
            method(
                f"Op{index}_{number}",
                tuple((f"p{n}", "int") for n in range(number)),
                ret=f"Result{number}",
            )
            for number in range(methods_per_type)
        ]
        types.append(
            {
                "name": f"T{index}",
                "class_import": f"Lib.T{index}",
                "kind": "class_declaration",
                "methods": methods,
            }
        )
    return {
        "source_repository": "example/lib",
        "source_commit": "0" * 40,
        "type_count": type_count,
        "types": types,
    }


def methods_block(text: str) -> list[str]:
    lines = text.splitlines()
    if "Methods:" not in lines:
        return []
    block: list[str] = []
    for line in lines[lines.index("Methods:") + 1 :]:
        if not line.startswith("  - "):
            break
        block.append(line[4:].strip())
    return block


class ServingClient:
    """Serves get_symbol, list_members and report_index_freshness from the rendered fixture, like the real tools."""

    def __init__(self, fixture: dict[str, Any], *, generation_id: str = "g::1", max_types: int = 300) -> None:
        self.generation_id = generation_id
        self.methods: dict[str, list[str]] = {}
        for chunk in build_chunks_from_api_surface(fixture, title="probe", max_types=max_types):
            fqn = extract_fqn(chunk.text)
            assert fqn is not None
            self.methods[fqn] = methods_block(chunk.text)
        self.members = {fqn: list(lines) for fqn, lines in self.methods.items()}
        self.missing: set[str] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if name == "report_index_freshness":
            return {"indexed_generation_id": self.generation_id}
        fqn = arguments["fqn"]
        if fqn in self.missing or fqn not in self.methods:
            return {"scope": {}, "fqn": fqn, "suggestions": [], "found": False, "reason": "no symbol"}
        if name == "get_symbol":
            return {
                "found": True,
                "fqn": fqn,
                "kind": "class_declaration",
                "methods": list(self.methods[fqn]),
            }
        if name == "list_members":
            return list(self.members[fqn])
        raise AssertionError(f"unexpected tool {name}")


class RaisingClient:
    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("boom")


def write_pointer(
    manifests: Path, family: str, platform: str, generation_id: str, *, bom: bool = False
) -> None:
    directory = manifests / f"{family}__{platform}__self_extracted"
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"generation_id": generation_id}, indent=2) + "\n"
    (directory / "active_pointer.json").write_bytes(
        (b"\xef\xbb\xbf" if bom else b"") + payload.encode("utf-8")
    )


def write_fixture(fixtures: Path, key: str, fixture: dict[str, Any]) -> None:
    (fixtures / key).mkdir(parents=True, exist_ok=True)
    (fixtures / key / "api_surface.json").write_text(json.dumps(fixture), encoding="utf-8")


ENTRY = {"family": "pdf", "platform": "net", "maxTypes": 300}
KEY = "pdf_net"


def sampled_pairs(fixture: dict[str, Any]) -> list[tuple[str, str]]:
    return sp.sample_method_pairs(fixture, max_types=300)


# --- the committed pointer ---------------------------------------------------------------------------------------


def test_committed_generation_id_reads_the_pointer_and_tolerates_a_bom(tmp_path):
    write_pointer(tmp_path, "pdf", "net", "pdf::net::self_extracted::A")
    assert sp.committed_generation_id(tmp_path, "pdf", "net") == "pdf::net::self_extracted::A"
    write_pointer(tmp_path, "cells", "java", "cells::java::self_extracted::B", bom=True)
    assert sp.committed_generation_id(tmp_path, "cells", "java") == "cells::java::self_extracted::B"


def test_committed_generation_id_names_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="pdf__net__self_extracted"):
        sp.committed_generation_id(tmp_path, "pdf", "net")
    with pytest.raises(FileNotFoundError, match="active_pointer.json"):
        sp.committed_generation_id(tmp_path, "pdf", "net")


@pytest.mark.parametrize("content", ["{not json", "[]", '{"generation": "x"}', '{"generation_id": ""}'])
def test_committed_generation_id_rejects_a_file_that_is_not_a_pointer(tmp_path, content):
    directory = tmp_path / "pdf__net__self_extracted"
    directory.mkdir()
    (directory / "active_pointer.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="active_pointer.json"):
        sp.committed_generation_id(tmp_path, "pdf", "net")


# --- P1 -----------------------------------------------------------------------------------------------------------


def test_p1_passes_when_the_served_id_equals_the_committed_pointer(tmp_path):
    write_pointer(tmp_path, "pdf", "net", "g::1")
    client = ServingClient(small_fixture(), generation_id="g::1")
    entry = sp.probe_generation(client, "pdf", "net", tmp_path)
    assert entry["id"] == "P1" and entry["status"] == "pass" and entry["reason"] is None
    assert entry["name"] == sp.PROVENANCE_TITLES["P1"]
    assert entry["evidence"] == {"served_generation_id": "g::1", "committed_generation_id": "g::1"}
    assert client.calls == [("report_index_freshness", {})]


def test_p1_fails_naming_both_ids_when_they_differ(tmp_path):
    write_pointer(tmp_path, "pdf", "net", "g::committed")
    entry = sp.probe_generation(
        ServingClient(small_fixture(), generation_id="g::older"), "pdf", "net", tmp_path
    )
    assert entry["status"] == "fail"
    assert "g::older" in entry["reason"] and "g::committed" in entry["reason"]
    assert entry["evidence"]["served_generation_id"] == "g::older"


@pytest.mark.parametrize(
    "report", [{}, {"indexed_generation_id": None}, {"indexed_generation_id": ""}, "x", None]
)
def test_p1_fails_when_the_report_carries_no_generation_id(tmp_path, report):
    write_pointer(tmp_path, "pdf", "net", "g::1")
    client = SimpleNamespace(invoke=lambda name, arguments: report)
    entry = sp.probe_generation(client, "pdf", "net", tmp_path)
    assert entry["status"] == "fail"
    assert "no indexed_generation_id" in entry["reason"] and "g::1" in entry["reason"]


def test_p1_fails_when_the_client_raises(tmp_path):
    write_pointer(tmp_path, "pdf", "net", "g::1")
    (entry,) = [
        e
        for e in sp.run_probes(RaisingClient(), KEY, ENTRY, fixtures=tmp_path, manifests_dir=tmp_path)
        if e["id"] == "P1"
    ]
    assert entry["status"] == "fail"
    assert entry["reason"] == "could not run: RuntimeError: boom"


def test_p1_fails_when_the_committed_pointer_is_missing(tmp_path):
    entry = sp.probe_generation(ServingClient(small_fixture()), "pdf", "net", tmp_path)
    assert entry["status"] == "fail"
    assert "active_pointer.json" in entry["reason"]


# --- describe_line ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "name", "arity", "return_type"),
    [
        ("  - Close() -> void", "Close", 0, "void"),
        ("Open(path: string) -> bool", "Open", 1, "bool"),
        ("Run(retries: int = 3, name?: str) -> None", "Run", 2, "None"),
        ("Get(m: Map<string, List<int>>, k: string) -> List<string>", "Get", 2, "List<string>"),
        ("Put(m: std::map<K, V>, k: K) -> std::map<K, V>", "Put", 2, "std::map<K, V>"),
        ("Scan(rows: []*Row, n: int) -> []*Row", "Scan", 2, "[]*Row"),
        ("read(self, *, size: int = -1, /) -> bytes", "read", 2, "bytes"),
        ("Create() -> Page [static]", "Create", 0, "Page"),
        ("Accept(visitor: Visitor)  (inherited from Base)", "Accept", 1, ""),
        ("Accept(visitor: Visitor) -> void  (inherited from Base)", "Accept", 1, "void"),
        ("Accept(visitor: ImagePlacementAbsorber)", "Accept", 1, ""),
        ("Call(cb: () => void, x: int) -> int", "Call", 2, "int"),
        ("Greet(name: &'a str, other: &'a str) -> &'a str", "Greet", 2, "&'a str"),
        ("Join(sep: str = ', ', parts: list) -> str", "Join", 2, "str"),
        ("Ok(r: Result<(), Error>) -> Result<(), Error> [static, async]", "Ok", 1, "Result<(), Error>"),
    ],
)
def test_describe_line(line, name, arity, return_type):
    assert sp.describe_line(line) == {"name": name, "arity": arity, "return_type": return_type}


# --- sampling -----------------------------------------------------------------------------------------------------


def test_sampling_is_deterministic_and_spread():
    fixture = small_fixture(type_count=40, methods_per_type=3)
    everything = sp.sample_method_pairs(fixture, max_types=300, count=10**6)
    assert len(everything) == 120
    first = sp.sample_method_pairs(fixture, max_types=300)
    assert first == sp.sample_method_pairs(copy.deepcopy(fixture), max_types=300)
    assert len(first) == sp.SAMPLE_SIZE == 30
    assert first[0] == everything[0]
    indexes = [everything.index(pair) for pair in first]
    assert indexes == sorted(indexes) and len(set(indexes)) == 30
    assert indexes[-1] >= 0.7 * len(everything), "the sample reaches the last 30 percent of the pairs"


def test_sampling_returns_everything_when_there_are_at_most_30():
    small = small_fixture(type_count=4, methods_per_type=3)
    everything = sp.sample_method_pairs(small, max_types=300, count=10**6)
    assert len(everything) == 12 and sp.sample_method_pairs(small, max_types=300) == everything
    exact = small_fixture(type_count=10, methods_per_type=3)
    assert len(sp.sample_method_pairs(exact, max_types=300)) == 30


def test_sampling_honours_the_chart_max_types():
    fixture = small_fixture(type_count=40, methods_per_type=3)
    assert len(sp.sample_method_pairs(fixture, max_types=2, count=10**6)) == 6


def test_sampling_skips_example_and_doc_chunks(monkeypatch):
    texts = [
        "FQN: Example: Create a page\nKind: example\nMethods:\n  - Hidden() -> void",
        "FQN: Doc: Guide\nKind: doc\nMethods:\n  - AlsoHidden() -> void",
        "FQN: Lib.Real\nKind: class_declaration\nMethods:\n  - Shown(a: int) -> bool\n  - Other() -> void\n\nProperties:\n  - X: int",
    ]
    monkeypatch.setattr(
        "foss_mcp.indexing.chunk_builder.build_chunks_from_api_surface",
        lambda fixture, *, title, max_types: [SimpleNamespace(text=text) for text in texts],
    )
    assert sp.sample_method_pairs({}, max_types=300) == [
        ("Lib.Real", "Shown(a: int) -> bool"),
        ("Lib.Real", "Other() -> void"),
    ]


# --- P2 -----------------------------------------------------------------------------------------------------------


def p2(tmp_path: Path, client: Any, fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    write_fixture(tmp_path, KEY, fixture if fixture is not None else small_fixture())
    return sp.probe_signatures(client, KEY, ENTRY, tmp_path)


def test_p2_passes_when_the_client_serves_the_rendered_lines(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    entry = p2(tmp_path, client, fixture)
    assert entry["id"] == "P2" and entry["status"] == "pass" and entry["reason"] is None
    assert entry["name"] == sp.PROVENANCE_TITLES["P2"]
    assert entry["evidence"] == {"sampled": 30, "failed": 0, "failures": []}
    names = [name for name, _arguments in client.calls]
    distinct = {arguments["fqn"] for _name, arguments in client.calls}
    assert names.count("get_symbol") == names.count("list_members") == len(distinct), "once per distinct FQN"


def first_pair_with_a_parameter(fixture: dict[str, Any]) -> tuple[str, str]:
    return next((fqn, line) for fqn, line in sampled_pairs(fixture) if sp.describe_line(line)["arity"] >= 1)


def test_p2_fails_when_a_served_line_has_one_parameter_less_and_reports_both_arities(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    fqn, line = first_pair_with_a_parameter(fixture)
    expected_arity = sp.describe_line(line)["arity"]
    shorter = (
        line.replace(", p" + str(expected_arity - 1) + ": int", "")
        if expected_arity > 1
        else line.replace("p0: int", "")
    )
    assert sp.describe_line(shorter)["arity"] == expected_arity - 1
    client.methods[fqn][client.methods[fqn].index(line)] = shorter
    client.members[fqn] = list(client.methods[fqn])
    entry = p2(tmp_path, client, fixture)
    assert entry["status"] == "fail"
    failure = next(item for item in entry["evidence"]["failures"] if item["expected"] == line)
    assert failure["fqn"] == fqn
    assert failure["expected_arity"] == expected_arity and failure["served_arity"] == expected_arity - 1
    assert entry["evidence"]["failed"] >= 1 and entry["evidence"]["sampled"] == 30
    assert fqn in entry["reason"]


def test_p2_fails_when_a_served_return_type_differs_and_reports_both(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    fqn, line = sampled_pairs(fixture)[0]
    changed = line.split(" -> ")[0] + " -> Different"
    client.methods[fqn][client.methods[fqn].index(line)] = changed
    client.members[fqn] = list(client.methods[fqn])
    entry = p2(tmp_path, client, fixture)
    assert entry["status"] == "fail"
    failure = entry["evidence"]["failures"][0]
    expected_return = sp.describe_line(line)["return_type"]
    assert failure["expected_return_type"] == expected_return and failure["served_return_type"] == "Different"
    assert failure["expected_arity"] == failure["served_arity"]
    assert set(failure) == {
        "fqn",
        "expected",
        "expected_arity",
        "served_arity",
        "expected_return_type",
        "served_return_type",
        "problem",
    }


def test_p2_fails_when_get_symbol_says_not_found(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    fqn = sampled_pairs(fixture)[0][0]
    client.missing.add(fqn)
    entry = p2(tmp_path, client, fixture)
    assert entry["status"] == "fail"
    failure = entry["evidence"]["failures"][0]
    assert failure["fqn"] == fqn and "not served" in failure["problem"]
    assert failure["served_arity"] is None and failure["served_return_type"] is None


def test_p2_fails_when_list_members_lacks_a_line_that_get_symbol_has(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    fqn, line = sampled_pairs(fixture)[0]
    client.members[fqn] = [item for item in client.members[fqn] if item != line]
    entry = p2(tmp_path, client, fixture)
    assert entry["status"] == "fail"
    failure = entry["evidence"]["failures"][0]
    assert failure["fqn"] == fqn and failure["expected"] == line and "list_members" in failure["problem"]


def test_p2_reports_at_most_five_failures_but_counts_them_all(tmp_path):
    fixture = small_fixture()
    client = ServingClient(fixture)
    client.missing.update(fqn for fqn, _line in sampled_pairs(fixture))
    entry = p2(tmp_path, client, fixture)
    assert entry["status"] == "fail"
    assert entry["evidence"]["failed"] == 30 and len(entry["evidence"]["failures"]) == 5


def test_p2_is_not_applicable_for_a_fixture_without_methods_and_never_a_pass(tmp_path):
    fixture = small_fixture()
    for item in fixture["types"]:
        item["methods"] = []
        item["enum_members"] = [{"name": "A"}]
    entry = p2(tmp_path, ServingClient(fixture), fixture)
    assert entry["status"] == "not_applicable"
    assert entry["status"] != "pass" and entry["reason"]
    assert entry["evidence"] == {}


# --- run_probes ----------------------------------------------------------------------------------------------------


def test_run_probes_returns_p1_then_p2(tmp_path):
    fixture = small_fixture()
    write_fixture(tmp_path, KEY, fixture)
    write_pointer(tmp_path, "pdf", "net", "g::1")
    entries = sp.run_probes(ServingClient(fixture), KEY, ENTRY, fixtures=tmp_path, manifests_dir=tmp_path)
    assert [(e["id"], e["status"]) for e in entries] == [("P1", "pass"), ("P2", "pass")]
    assert all(set(e) == {"id", "name", "status", "evidence", "reason"} for e in entries)


def test_run_probes_turns_an_exception_into_a_fail_entry(tmp_path):
    write_pointer(tmp_path, "pdf", "net", "g::1")
    entries = sp.run_probes(RaisingClient(), KEY, ENTRY, fixtures=tmp_path, manifests_dir=tmp_path)
    assert [e["id"] for e in entries] == ["P1", "P2"]
    assert all(e["status"] == "fail" for e in entries)
    assert entries[0]["reason"] == "could not run: RuntimeError: boom"
    assert entries[1]["reason"].startswith("could not run: FileNotFoundError:"), (
        "no fixture is committed here"
    )


def test_make_entry_has_the_shape_of_the_gate_entry():
    entry = sp.make_entry("P1", "fail", {"a": 1}, "why")
    assert entry == {
        "id": "P1",
        "name": sp.PROVENANCE_TITLES["P1"],
        "status": "fail",
        "evidence": {"a": 1},
        "reason": "why",
    }
    assert sp.make_entry("P2", "pass")["evidence"] == {}


# --- the command line ----------------------------------------------------------------------------------------------


def run_main(tmp_path: Path, client: Any, *extra: str) -> int:
    argv = [
        "--url",
        "http://127.0.0.1:1",
        "--pilot",
        KEY,
        "--fixtures",
        str(tmp_path / "fixtures"),
        "--manifests",
        str(tmp_path / "manifests"),
        *extra,
    ]
    return sp.main(argv, client_factory=lambda url: client)


def test_main_exits_0_on_pass_1_on_fail(tmp_path, capsys):
    fixture = small_fixture()
    write_fixture(tmp_path / "fixtures", KEY, fixture)
    write_pointer(tmp_path / "manifests", "pdf", "net", "g::1")
    report = tmp_path / "out" / "report.json"
    assert run_main(tmp_path, ServingClient(fixture, generation_id="g::1"), "--report", str(report)) == 0
    out = capsys.readouterr().out
    assert "P1 PASS" in out and "P2 PASS" in out
    document = json.loads(report.read_text(encoding="utf-8"))
    assert [e["status"] for e in document["entries"]] == ["pass", "pass"] and document["pilot"] == KEY

    assert run_main(tmp_path, ServingClient(fixture, generation_id="g::other")) == 1
    out = capsys.readouterr().out
    assert "P1 FAIL" in out and "g::other" in out and "P2 PASS" in out


def test_main_exits_1_when_the_client_cannot_be_opened(tmp_path, capsys):
    def refuse(url: str) -> Any:
        raise ConnectionError("refused")

    argv = [
        "--url",
        "http://127.0.0.1:1",
        "--pilot",
        KEY,
        "--fixtures",
        str(tmp_path),
        "--manifests",
        str(tmp_path),
    ]
    assert sp.main(argv, client_factory=refuse) == 1
    assert "could not run: ConnectionError" in capsys.readouterr().out


def test_main_refuses_an_unknown_pilot(tmp_path, capsys):
    argv = ["--url", "http://127.0.0.1:1", "--pilot", "no_such_pilot"]
    assert sp.main(argv, client_factory=lambda url: ServingClient(small_fixture())) == 2
    assert "unknown pilot" in capsys.readouterr().err


# --- differential tests over the REAL tool path --------------------------------------------------------------------


class RealToolsClient:
    """A client whose ``invoke`` runs the real ``tools/call`` handler of ``create_server``."""

    def __init__(self, server: Any) -> None:
        entry = server.get_request_handler("tools/call")
        assert entry is not None
        self._handler = entry.handler

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        async def run() -> Any:
            with anyio.fail_after(GUARD_SECONDS):
                return await self._handler(None, CallToolRequestParams(name=name, arguments=arguments))

        result = anyio.run(run)
        if result.is_error:
            raise RuntimeError(f"{name} reported a tool error: {result.content}")
        return result.structured_content["result"]


def real_client(scopes: Path, family: str, platform: str) -> RealToolsClient:
    """A real server over a COPY of the committed scope directory (the committed data is never touched)."""
    directory = f"{family}__{platform}__self_extracted"
    shutil.copytree(DEMO_MANIFESTS / directory, scopes / directory)
    server = create_server(
        DeploymentConfig(family=family, platform=platform), GenerationManifestStore(scopes)
    )
    return RealToolsClient(server)


def committed_pilots() -> list[tuple[str, str]]:
    found = sorted(
        tuple(path.name.split("__")[:2])
        for path in DEMO_MANIFESTS.iterdir()
        if path.is_dir() and path.name.endswith("__self_extracted")
    )
    return [(family, platform) for family, platform in found]


PILOTS = committed_pilots()


def test_the_committed_data_covers_every_pilot_of_the_chart():
    # re-pinned by TC-553: 40 -> 32 (the eight removed pilots left the chart and the committed data; TC-551 missed this pin)
    assert len(PILOTS) >= 32
    assert {f"{family}_{platform}" for family, platform in PILOTS} == set(CHART)


@pytest.mark.parametrize(("family", "platform"), PILOTS, ids=[f"{f}_{p}" for f, p in PILOTS])
def test_p1_and_p2_over_the_committed_data_of_every_pilot(tmp_path, family, platform):
    key = f"{family}_{platform}"
    client = real_client(tmp_path / "scopes", family, platform)
    entries = sp.run_probes(client, key, CHART[key], fixtures=FIXTURES, manifests_dir=DEMO_MANIFESTS)
    p1, p2_entry = entries
    assert (p1["id"], p2_entry["id"]) == ("P1", "P2")
    assert p1["status"] == "pass", (key, p1["reason"])
    if platform != "python":
        assert p2_entry["status"] == "pass", (key, p2_entry["reason"], p2_entry["evidence"])
    else:
        # A committed Python generation may pre-date the TC-522 slot cap and re-export pairing until the final
        # re-ingest. The probe is right to flag it; the pilot is named here, never skipped.
        assert p2_entry["status"] in ("pass", "fail", "not_applicable"), (key, p2_entry["reason"])
    assert (p2_entry["status"] == "not_applicable") == (key == "tex_python"), (key, p2_entry["status"])


def test_p1_fails_against_a_container_serving_another_generation(tmp_path):
    committed = sp.committed_generation_id(DEMO_MANIFESTS, "pdf", "net")
    other = committed.rsplit("-", 1)[0] + "-00000000"
    assert other != committed
    write_pointer(tmp_path / "manifests", "pdf", "net", other)
    client = real_client(tmp_path / "scopes", "pdf", "net")
    entry = sp.probe_generation(client, "pdf", "net", tmp_path / "manifests")
    assert entry["status"] == "fail"
    assert committed in entry["reason"] and other in entry["reason"]
    assert sp.probe_generation(client, "pdf", "net", DEMO_MANIFESTS)["status"] == "pass"


def drop_last_parameter(line: str) -> str:
    """The same method line without its last top-level parameter."""
    start = line.index("(")
    depth = 0
    last_comma = None
    for index in range(start, len(line)):
        char = line[index]
        if char in "([<{":
            depth += 1
        elif char in ")]>}":
            depth -= 1
            if depth == 0:
                end = index
                break
        elif char == "," and depth == 1:
            last_comma = index
    opening = last_comma if last_comma is not None else start + 1
    return line[:opening] + line[end:]


def test_p2_fails_when_a_served_generation_was_edited_after_the_fact(tmp_path):
    key, family, platform = "cells_net", "cells", "net"
    entry = CHART[key]
    fixture = json.loads((FIXTURES / key / "api_surface.json").read_text(encoding="utf-8-sig"))
    fqn, line = sp.sample_method_pairs(fixture, max_types=int(entry["maxTypes"]))[0]
    shorter = drop_last_parameter(line)
    assert sp.describe_line(shorter)["arity"] == sp.describe_line(line)["arity"] - 1

    scopes = tmp_path / "scopes"
    client = real_client(scopes, family, platform)
    (generation_file,) = (scopes / f"{family}__{platform}__self_extracted").glob("generation__*.json")
    document = json.loads(generation_file.read_text(encoding="utf-8-sig"))
    documents = document["payload"]["lexical_index"]["documents"]
    (doc_id,) = [name for name, item in documents.items() if extract_fqn(item["text"]) == fqn]
    text = documents[doc_id]["text"]
    assert f"  - {line}" in text
    documents[doc_id]["text"] = text.replace(f"  - {line}", f"  - {shorter}", 1)
    generation_file.write_text(json.dumps(document), encoding="utf-8")

    entries = sp.run_probes(client, key, entry, fixtures=FIXTURES, manifests_dir=DEMO_MANIFESTS)
    p1, p2_entry = entries
    assert p1["status"] == "pass", "the pointer is unchanged, so the generation id still matches"
    assert p2_entry["status"] == "fail"
    assert fqn in p2_entry["reason"]
    failure = next(item for item in p2_entry["evidence"]["failures"] if item["fqn"] == fqn)
    assert failure["expected"] == line
    assert failure["served_arity"] == failure["expected_arity"] - 1
