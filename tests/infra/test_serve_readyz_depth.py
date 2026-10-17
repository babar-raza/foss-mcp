"""G3/TC-506 (REQ-G3-008): ``/readyz`` proves scope, shape and composition, and says why it is not ready.

Offline and deterministic: ``serve_http.build_app`` over a COPY of the committed ``pdf/go`` baked scope under
``tmp_path`` (the pointer and the one generation file), driven through Starlette's in-process ``TestClient``.
The wrong-scope scenarios (S20a to S20d) are the independent reviewer's doctored states, rebuilt here from the
committed ``cells/go`` and ``pdf/java`` scopes. Nothing opens a socket or starts a container.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.tools.relevance import chunk_kind

INFRA_DIR = Path(__file__).parents[2] / "infra"
sys.path.insert(0, str(INFRA_DIR))
import serve_http  # noqa: E402

REPO_ROOT = Path(__file__).parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SERVE_HTTP_SOURCE = INFRA_DIR / "serve_http.py"
SCOPE_DIR_NAME = "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
FAILED = "readiness failed"
RESTORED = "readiness restored"
OK = "readiness ok"
STARTUP_FAILED = "readiness failed at startup"


class Scope:
    """A copy of the baked pdf/go scope under ``tmp_path`` and helpers that doctor it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "manifests"
        self.dir = self.root / SCOPE_DIR_NAME
        shutil.copytree(DEMO_MANIFESTS / SCOPE_DIR_NAME, self.dir)

    @property
    def pointer(self) -> Path:
        return self.dir / "active_pointer.json"

    @property
    def generation(self) -> Path:
        return next(self.dir.glob("generation__*.json"))

    def load(self) -> dict[str, Any]:
        return json.loads(self.generation.read_text(encoding="utf-8"))

    def save(self, generation: dict[str, Any]) -> None:
        self.generation.write_text(json.dumps(generation), encoding="utf-8")

    def client(self) -> TestClient:
        store = GenerationManifestStore(self.root)
        return TestClient(serve_http.build_app(CONFIG, manifest_store=store, allowed_origins=[]))


@pytest.fixture
def scope(tmp_path: Path) -> Scope:
    return Scope(tmp_path)


def _records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == message]


def _foreign_generation(name: str) -> tuple[Path, dict[str, Any]]:
    path = next((DEMO_MANIFESTS / name).glob("generation__*.json"))
    return path, json.loads(path.read_text(encoding="utf-8"))


# --- the scenario builders (the reviewer's scen.py S20a to S20d) ---------------------------------------


def _s20a(scope: Scope) -> None:
    """cells/go pointer and generation copied into this deployment's scope directory."""
    for child in scope.dir.iterdir():
        child.unlink()
    # re-pinned by TC-551: <removed pilot> -> cells__go__self_extracted
    for child in (DEMO_MANIFESTS / "cells__go__self_extracted").iterdir():
        shutil.copy(child, scope.dir / child.name)


def _s20b(scope: Scope) -> None:
    """The cells/go generation file renamed to the version this deployment's pointer names."""
    # re-pinned by TC-551: <removed pilot> -> cells__go__self_extracted
    _, foreign = _foreign_generation("cells__go__self_extracted")
    version = json.loads(scope.pointer.read_text(encoding="utf-8"))["generation_id"].rsplit("::", 1)[-1]
    scope.generation.unlink()
    (scope.dir / f"generation__{version}.json").write_text(json.dumps(foreign), encoding="utf-8")


def _s20c(scope: Scope) -> None:
    """pdf/java content relabelled with this deployment's generation id, family, platform, version."""
    _, foreign = _foreign_generation("pdf__java__self_extracted")
    own = scope.load()
    for field in ("generation_id", "family", "platform", "version"):
        foreign[field] = own[field]
    scope.save(foreign)


def _s20d(scope: Scope) -> None:
    """A pdf/java generation with its own ids, the pointer rewritten to name it, in this directory."""
    path, foreign = _foreign_generation("pdf__java__self_extracted")
    scope.generation.unlink()
    (scope.dir / path.name).write_text(json.dumps(foreign), encoding="utf-8")
    scope.pointer.write_text(json.dumps({"generation_id": foreign["generation_id"]}), encoding="utf-8")


# --- a ready generation ---------------------------------------------------------------------------------


def test_ready_generation_gives_200_and_a_json_body_with_the_five_keys(scope: Scope) -> None:
    with scope.client() as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"ready", "reasons", "composition", "generation_id", "warnings"}
    assert body["ready"] is True
    assert body["reasons"] == []
    assert set(body["composition"]) == {"symbol", "doc", "example"}
    assert body["composition"]["symbol"] >= 3
    assert body["composition"]["doc"] >= 1
    assert body["generation_id"].startswith("pdf::go::self_extracted::")


def test_the_example_search_header_is_kept_on_200_and_503(scope: Scope, tmp_path: Path) -> None:
    with scope.client() as client:
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.headers[serve_http.EXAMPLE_SEARCH_HEADER]
    scope.pointer.write_text("not json", encoding="utf-8")
    with scope.client() as client:
        not_ready = client.get("/readyz")
    assert not_ready.status_code == 503
    assert not_ready.headers[serve_http.EXAMPLE_SEARCH_HEADER] == "lexical-only"
    empty_store = GenerationManifestStore(tmp_path / "nothing")
    with TestClient(serve_http.build_app(CONFIG, manifest_store=empty_store, allowed_origins=[])) as client:
        none = client.get("/readyz")
    assert none.status_code == 503
    assert none.headers[serve_http.EXAMPLE_SEARCH_HEADER] == "lexical-only"
    assert none.json()["reasons"] == ["no active generation"]


@pytest.mark.parametrize(
    ("build", "reason_part"),
    [
        (_s20a, "another scope"),
        (_s20b, "generation file is unreadable (UnknownGenerationError)"),
        (_s20c, "different generation"),
        (_s20d, "another scope"),
    ],
    ids=["S20a", "S20b", "S20c", "S20d"],
)
def test_s20a_s20b_s20c_s20d_return_503_and_log_a_warning(
    scope: Scope, caplog: pytest.LogCaptureFixture, build: Any, reason_part: str
) -> None:
    build(scope)
    with caplog.at_level(logging.INFO):
        with scope.client() as client:
            response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert any(reason_part in reason for reason in body["reasons"])
    records = _records(caplog, FAILED)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert list(records[0].reasons) == body["reasons"]
    assert records[0].generation_id == body["generation_id"]
    assert records[0].ready is False


@pytest.mark.parametrize("damage", ["tokens", "text", "doc_freq"])
def test_partial_documents_return_503(scope: Scope, damage: str) -> None:
    generation = scope.load()
    index = generation["payload"]["lexical_index"]
    if damage == "doc_freq":
        index.pop("doc_freq")
    else:
        for document in index["documents"].values():
            document.pop(damage)
    scope.save(generation)
    with scope.client() as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["reasons"]


@pytest.mark.parametrize("symbols", [0, 1])
def test_zero_and_one_symbol_return_503(scope: Scope, symbols: int) -> None:
    generation = scope.load()
    index = generation["payload"]["lexical_index"]
    kept: dict[str, Any] = {}
    seen = 0
    for doc_id, document in index["documents"].items():
        if chunk_kind(document["text"]) == "symbol":
            if seen >= symbols:
                continue
            seen += 1
        kept[doc_id] = document
    index["documents"] = kept
    scope.save(generation)
    with scope.client() as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert f"symbols {symbols} below the floor 3" in response.json()["reasons"]
    assert response.json()["composition"]["symbol"] == symbols


def test_a_corrupt_pointer_returns_503_not_500_and_logs_no_traceback(
    scope: Scope, caplog: pytest.LogCaptureFixture
) -> None:
    scope.pointer.write_text("", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        with scope.client() as client:
            response = client.get("/readyz")
            again = client.get("/readyz")
    assert response.status_code == 503
    assert again.status_code == 503
    assert response.json()["reasons"] == ["active pointer is unreadable (JSONDecodeError)"]
    assert all(record.exc_info is None for record in caplog.records)
    assert all(record.levelno < logging.ERROR for record in caplog.records)
    assert "Traceback" not in caplog.text


def test_a_bom_pointer_returns_200(scope: Scope) -> None:
    scope.pointer.write_bytes(b"\xef\xbb\xbf" + scope.pointer.read_bytes())
    with scope.client() as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ready"] is True


# --- logging: transitions only -----------------------------------------------------------------------


def test_repeated_failing_probes_log_one_warning(scope: Scope, caplog: pytest.LogCaptureFixture) -> None:
    scope.pointer.write_text("not json", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        with scope.client() as client:
            statuses = [client.get("/readyz").status_code for _ in range(3)]
    assert statuses == [503, 503, 503]
    assert len(_records(caplog, FAILED)) == 1


def test_a_changed_reason_logs_a_new_warning(scope: Scope, caplog: pytest.LogCaptureFixture) -> None:
    scope.pointer.write_text("null", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        with scope.client() as client:
            client.get("/readyz")
            client.get("/readyz")
            assert len(_records(caplog, FAILED)) == 1
            scope.pointer.write_text("not json", encoding="utf-8")
            client.get("/readyz")
            client.get("/readyz")
    records = _records(caplog, FAILED)
    assert len(records) == 2
    assert list(records[0].reasons) == ["no active generation"]
    assert list(records[1].reasons) == ["active pointer is unreadable (JSONDecodeError)"]


def test_recovery_logs_one_info_record(scope: Scope, caplog: pytest.LogCaptureFixture) -> None:
    good_pointer = scope.pointer.read_text(encoding="utf-8")
    scope.pointer.write_text("not json", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        with scope.client() as client:
            assert client.get("/readyz").status_code == 503
            scope.pointer.write_text(good_pointer, encoding="utf-8")
            assert client.get("/readyz").status_code == 200
            assert client.get("/readyz").status_code == 200
    restored = _records(caplog, RESTORED)
    assert len(restored) == 1
    assert restored[0].levelno == logging.INFO
    assert restored[0].ready is True
    assert len(_records(caplog, FAILED)) == 1
    assert _records(caplog, OK) == []


def test_a_first_ready_probe_logs_one_info_record(scope: Scope, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        with scope.client() as client:
            client.get("/readyz")
            client.get("/readyz")
    ok = _records(caplog, OK)
    assert len(ok) == 1
    assert ok[0].levelno == logging.INFO
    assert _records(caplog, FAILED) == []


def test_startup_logs_a_warning_when_not_ready_and_nothing_when_ready(
    scope: Scope, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        scope.client()
    assert _records(caplog, STARTUP_FAILED) == []
    caplog.clear()
    scope.pointer.write_text("null", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        scope.client()
    records = _records(caplog, STARTUP_FAILED)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert list(records[0].reasons) == ["no active generation"]
    assert records[0].family == "pdf"
    assert records[0].platform == "go"


def test_log_records_contain_no_filesystem_path(scope: Scope, caplog: pytest.LogCaptureFixture) -> None:
    scope.pointer.write_text(json.dumps({"generation_id": str(scope.tmp_path)}), encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        with scope.client() as client:
            response = client.get("/readyz")
            scope.pointer.write_text(f"not json {scope.tmp_path}", encoding="utf-8")
            client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["generation_id"] is None  # a pointer holding a path is never echoed back
    assert caplog.records
    for record in caplog.records:
        rendered = json.dumps(record.__dict__, default=str)
        assert str(scope.tmp_path) not in rendered
        assert scope.tmp_path.name not in rendered


# --- the wiring proof ------------------------------------------------------------------------------------


def test_serve_http_source_calls_readiness_report_and_round_trip_check() -> None:
    source = SERVE_HTTP_SOURCE.read_text(encoding="utf-8")
    assert "readiness_report(generation_store)" in source
    assert "round_trip_check(generation_store)" in source
    assert "_log_startup_readiness(" in source
    build_app_source = source[
        source.index("def build_app(") : source.index("def deployment_config_from_env(")
    ]
    assert "_log_startup_readiness(" in build_app_source
    route_source = source[source.index("def _readyz_route(") : source.index("def _log_readiness_transition(")]
    assert "readiness_report(generation_store)" in route_source
