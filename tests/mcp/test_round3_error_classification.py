"""G3/TC-564 (REQ-G3-009, RD3-120, RD3-123, RD3-124): storage faults are classified, and a missing sidecar is not "no data".

Round 3 found that a corrupt pointer or generation made ``list_recent_changes`` and ``report_index_freshness``
answer ``invalid_argument`` or ``internal`` with raw exception text, that a pointer file that vanished gave a
non-error ``found: false``, and that an absent releases sidecar was answered as an empty, "complete" list.

Every test here drives the real ``tools/call`` handler (``server.get_request_handler("tools/call")``, the one the
transport uses) over a COPY of the committed ``pdf/go`` baked scope. The 25 corruption cases damage the files on
disk AFTER the server was built (a live fault, which is how production sees one) and are written as raw bytes,
never through the store under test. The expected generation id and source commit come from reading that same
file as plain JSON and text, not from the extractor's or the server's own output.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestError, GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp import sidecar_state
from foss_mcp.mcp.health import GENERATION_UNAVAILABLE_MESSAGE
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import SidecarSnapshot, create_server
from foss_mcp.mcp.sidecar_state import RELEASES_NOT_LOADED_MESSAGE
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

BAKED_ROOT = REPO_ROOT / "infra" / "demo-manifests"
BAKED_SCOPE = BAKED_ROOT / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
SCOPE_KEY = "pdf::go::self_extracted"
GUARD_SECONDS = 60

# Every tool that reads the generation, with arguments that pass validation.
GENERATION_READING_TOOLS: dict[str, dict[str, Any]] = {
    "lookup": {"query": "Document"},
    "search_docs": {"query": "install"},
    "search_symbols": {"query": "Document"},
    "get_symbol": {"fqn": "Document"},
    "list_members": {"fqn": "Document"},
    "find_examples": {"query": "Document"},
    "list_recent_changes": {},
    "report_index_freshness": {},
}


def call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def baked_copy(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the baked pdf/go scope; returns (manifests root, scope directory)."""
    root = tmp_path / "manifests"
    scope_dir = root / "pdf__go__self_extracted"
    shutil.copytree(BAKED_SCOPE, scope_dir)
    return root, scope_dir


def make_server(root: Path, **kwargs: Any) -> Any:
    return create_server(CONFIG, GenerationManifestStore(root), **kwargs)


def generation_path(scope_dir: Path) -> Path:
    return next(scope_dir.glob("generation__*.json"))


def edit_generation(scope_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    path = generation_path(scope_dir)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def write_pointer(content: bytes) -> Callable[[Path], None]:
    return lambda scope_dir: (scope_dir / "active_pointer.json").write_bytes(content)


def write_generation_bytes(content: bytes) -> Callable[[Path], None]:
    return lambda scope_dir: generation_path(scope_dir).write_bytes(content)


def truncate_generation(scope_dir: Path) -> None:
    path = generation_path(scope_dir)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def pointer_to(generation_id: str) -> Callable[[Path], None]:
    return write_pointer(json.dumps({"generation_id": generation_id}).encode("utf-8"))


def _drop_payload(document: dict[str, Any]) -> None:
    del document["payload"]


def _payload_number(document: dict[str, Any]) -> None:
    document["payload"] = 5


def _no_lexical_index(document: dict[str, Any]) -> None:
    del document["payload"]["lexical_index"]


def _documents_list(document: dict[str, Any]) -> None:
    document["payload"]["lexical_index"]["documents"] = [1, 2, 3]


def _document_not_object(document: dict[str, Any]) -> None:
    document["payload"]["lexical_index"]["documents"] = {"x": 5}


def _document_without_text(document: dict[str, Any]) -> None:
    documents = document["payload"]["lexical_index"]["documents"]
    first = next(iter(documents))
    documents[first] = {"chunk_id": "c", "tokens": ["a"]}


def _doc_freq_list(document: dict[str, Any]) -> None:
    document["payload"]["lexical_index"]["doc_freq"] = []


def _generation_edit(mutate: Callable[[dict[str, Any]], None]) -> Callable[[Path], None]:
    return lambda scope_dir: edit_generation(scope_dir, mutate)


# 25 damages to the files a running server reads. Each is applied to a scope directory.
CORRUPTIONS: dict[str, Callable[[Path], None]] = {
    "pointer-deleted": lambda scope_dir: (scope_dir / "active_pointer.json").unlink(),
    "pointer-empty-file": write_pointer(b""),
    "pointer-truncated-json": write_pointer(b'{"generation_id": "pdf::go::self_ex'),
    "pointer-invalid-utf8": write_pointer(b"\xff\xfe\x00{"),
    "pointer-empty-array": write_pointer(b"[]"),
    "pointer-null": write_pointer(b"null"),
    "pointer-empty-object": write_pointer(b"{}"),
    "pointer-numeric-id": write_pointer(b'{"generation_id": 5}'),
    "pointer-empty-id": write_pointer(b'{"generation_id": ""}'),
    "pointer-list-id": write_pointer(b'{"generation_id": ["x"]}'),
    "pointer-names-missing-generation": pointer_to("pdf::go::self_extracted::20990101T000000Z-deadbeef"),
    "pointer-names-another-scope": pointer_to("email::go::self_extracted::20261017T043309353548Z-a9467e67"),
    "pointer-path-traversal": pointer_to("pdf::go::self_extracted::../../etc/passwd"),
    "pointer-oversize": write_pointer(b'{"generation_id": "' + b"a" * 70_000 + b'"}'),
    "generation-deleted": lambda scope_dir: generation_path(scope_dir).unlink(),
    "generation-empty-file": write_generation_bytes(b""),
    "generation-truncated": truncate_generation,
    "generation-invalid-utf8": write_generation_bytes(b"\x00\xff\xfe not json at all {{{"),
    "generation-json-array": write_generation_bytes(b"[]"),
    "generation-without-payload": _generation_edit(_drop_payload),
    "generation-payload-is-a-number": _generation_edit(_payload_number),
    "generation-without-lexical-index": _generation_edit(_no_lexical_index),
    "generation-documents-is-a-list": _generation_edit(_documents_list),
    "generation-document-is-not-an-object": _generation_edit(_document_not_object),
    "generation-document-without-text": _generation_edit(_document_without_text),
    "generation-doc-freq-is-a-list": _generation_edit(_doc_freq_list),
}


def assert_unavailable(result: CallToolResult, tmp_path: Path, context: str) -> None:
    assert result.is_error is True, context
    structured = result.structured_content
    assert structured["code"] == "unavailable", (context, structured)
    assert structured["message"] == GENERATION_UNAVAILABLE_MESSAGE, context
    assert structured["correlation_id"], context
    text = result.content[0].text
    assert text == GENERATION_UNAVAILABLE_MESSAGE, context
    serialised = text + json.dumps(structured)
    for forbidden in (
        str(tmp_path),
        "Traceback",
        "JSONDecodeError",
        "KeyError",
        "passwd",
        "UnicodeDecodeError",
    ):
        assert forbidden not in serialised, (context, forbidden)


# --- 25 corruption cases x every tool that reads the generation -------------------------------------------


def test_the_corruption_table_has_at_least_21_cases() -> None:
    assert len(CORRUPTIONS) >= 21


@pytest.mark.parametrize("case", sorted(CORRUPTIONS))
def test_a_corrupt_store_is_the_sanitised_unavailable_on_every_tool(case: str, tmp_path: Path) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    for tool, arguments in GENERATION_READING_TOOLS.items():
        before = call(server, tool, arguments)
        assert before.is_error is False, (case, tool, "the baked copy must answer before it is damaged")

    CORRUPTIONS[case](scope_dir)

    for tool, arguments in GENERATION_READING_TOOLS.items():
        assert_unavailable(call(server, tool, arguments), tmp_path, f"{case}/{tool}")
    # A tool that does not read the generation is unaffected by the damage.
    answer = call(server, "get_product_reference", {"section": "license"})
    assert answer.is_error is False, case


def test_a_vanished_pointer_is_an_error_not_a_miss_and_the_log_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    assert (
        call(server, "get_symbol", {"fqn": "NoSuchType"}).is_error is False
    )  # an honest miss while it exists
    (scope_dir / "active_pointer.json").unlink()

    with caplog.at_level(logging.WARNING, logger="foss_mcp.mcp.server"):
        result = call(server, "get_symbol", {"fqn": "NoSuchType"})
        freshness = call(server, "report_index_freshness", {})

    assert_unavailable(result, tmp_path, "get_symbol")
    assert_unavailable(freshness, tmp_path, "report_index_freshness")
    assert any("active pointer has vanished" in record.getMessage() for record in caplog.records)


def test_a_store_that_never_published_is_still_an_honest_miss_not_an_error(tmp_path: Path) -> None:
    server = make_server(tmp_path / "empty")
    freshness = call(server, "report_index_freshness", {})
    assert freshness.is_error is False
    assert freshness.structured_content["result"]["reason"] == "never published"
    for tool, arguments in GENERATION_READING_TOOLS.items():
        assert call(server, tool, arguments).is_error is False, tool


def test_a_pointer_that_comes_back_makes_the_tools_answer_again(tmp_path: Path) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    pointer = (scope_dir / "active_pointer.json").read_bytes()
    (scope_dir / "active_pointer.json").unlink()
    assert call(server, "search_symbols", {"query": "Document"}).is_error is True

    (scope_dir / "active_pointer.json").write_bytes(pointer)

    assert call(server, "search_symbols", {"query": "Document"}).is_error is False


# --- exceptions raised inside a handler -------------------------------------------------------------------


def _server_with_handler(
    monkeypatch: pytest.MonkeyPatch, root: Path, tool: str, failure: BaseException
) -> Any:
    """A server whose ``get_product_reference`` handler raises *failure* (the wrapper's own signature, so
    the advertised schema still matches)."""
    assert tool == "get_product_reference"
    original = server_module._build_tool_registry

    def failing(*, section: str) -> Any:
        raise failure

    def patched(*args: Any, **kwargs: Any) -> dict[str, Any]:
        registry = original(*args, **kwargs)
        registry[tool] = failing
        return registry

    monkeypatch.setattr(server_module, "_build_tool_registry", patched)
    return make_server(root)


@pytest.mark.parametrize(
    "failure",
    [
        json.JSONDecodeError("Expecting value", "<doc>", 0),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        FileNotFoundError(2, "No such file", "/data/manifests/secret-path.json"),
        PermissionError(13, "denied", "/data/manifests/secret-path.json"),
        GenerationManifestError("could not take the publish lock for scope 'pdf::go::self_extracted'"),
    ],
    ids=["json-decode", "utf8-decode", "file-not-found", "permission", "manifest-error"],
)
def test_a_storage_exception_inside_a_handler_is_unavailable_never_invalid_argument(
    failure: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = baked_copy(tmp_path)
    server = _server_with_handler(monkeypatch, root, "get_product_reference", failure)

    with caplog.at_level(logging.WARNING, logger="foss_mcp.mcp.server"):
        result = call(server, "get_product_reference", {"section": "license"})

    assert_unavailable(result, tmp_path, type(failure).__name__)
    assert "secret-path" not in result.content[0].text
    assert any(type(failure).__name__ in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    "failure",
    [
        KeyError("secret_field"),
        IndexError("list index out of range"),
        AttributeError("'NoneType' object has no attribute 'text'"),
        TypeError("unsupported operand type(s) for +: 'int' and 'str'"),
    ],
    ids=["key-error", "index-error", "attribute-error", "type-error"],
)
def test_a_lookup_or_attribute_fault_never_echoes_its_text(
    failure: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = baked_copy(tmp_path)
    server = _server_with_handler(monkeypatch, root, "get_product_reference", failure)

    result = call(server, "get_product_reference", {"section": "license"})

    assert result.is_error is True
    assert result.structured_content["code"] == "internal"
    assert result.structured_content["message"] == server_module._GENERIC_ERROR_FALLBACK
    assert str(failure).strip("'") not in result.content[0].text


def test_a_plain_safe_runtime_message_is_still_shown_and_a_value_error_is_still_invalid_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = baked_copy(tmp_path)
    runtime = call(
        _server_with_handler(monkeypatch, root, "get_product_reference", RuntimeError("the gateway is down")),
        "get_product_reference",
        {"section": "license"},
    )
    assert (runtime.structured_content["code"], runtime.content[0].text) == (
        "internal",
        "the gateway is down",
    )
    value = call(
        _server_with_handler(monkeypatch, root, "get_product_reference", ValueError("section is unknown")),
        "get_product_reference",
        {"section": "license"},
    )
    assert (value.structured_content["code"], value.content[0].text) == (
        "invalid_argument",
        "section is unknown",
    )


def test_an_invalid_argument_is_still_invalid_argument_over_a_corrupt_store(tmp_path: Path) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    CORRUPTIONS["generation-truncated"](scope_dir)

    result = call(server, "search_symbols", {"query": "Document", "top_k": 0})

    assert result.structured_content["code"] == "invalid_argument"


# --- a missing releases sidecar is not "no releases" ----------------------------------------------------------


def test_a_missing_releases_sidecar_is_an_unavailable_error_that_says_it_is_not_loaded(
    tmp_path: Path,
) -> None:
    root, _ = baked_copy(tmp_path)
    server = make_server(root, recent_releases_state=sidecar_state.SIDECAR_NOT_PRESENT)

    result = call(server, "list_recent_changes", {})

    assert result.is_error is True
    assert result.structured_content["code"] == "unavailable"
    assert result.structured_content["message"] == RELEASES_NOT_LOADED_MESSAGE
    assert "complete answer" not in result.content[0].text
    assert "not a statement that the product has no releases" in result.content[0].text


def test_with_the_switch_off_a_missing_releases_sidecar_is_the_old_empty_complete_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_state, "SIDECAR_STATE_WARNINGS", False)
    root, _ = baked_copy(tmp_path)
    server = make_server(root, recent_releases_state=sidecar_state.SIDECAR_NOT_PRESENT)

    result = call(server, "list_recent_changes", {})

    assert result.is_error is False
    assert result.structured_content["result"] == []
    assert "An empty list is a complete answer" in result.structured_content["note"]


def test_a_present_empty_releases_sidecar_is_still_a_complete_answer(tmp_path: Path) -> None:
    root, _ = baked_copy(tmp_path)
    server = make_server(root, recent_releases=(), recent_releases_state=sidecar_state.SIDECAR_PRESENT)

    result = call(server, "list_recent_changes", {})

    assert result.is_error is False
    assert result.structured_content["result"] == []
    assert "An empty list is a complete answer" in result.structured_content["note"]


def test_the_mandated_switch_is_a_literal_true_line_in_the_source() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "sidecar_state.py").read_text(encoding="utf-8")
    assert source.count("SIDECAR_STATE_WARNINGS = True") == 1
    assert sidecar_state.SIDECAR_STATE_WARNINGS is True


# --- observability: log fields and the success correlation id ---------------------------------------------------


def test_call_log_lines_carry_the_generation_id_and_source_commit_and_the_result_the_correlation_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    oracle_generation = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))[
        "generation_id"
    ]
    generation_text = generation_path(scope_dir).read_text(encoding="utf-8")
    oracle_commits = set(re.findall(r"Source-Commit: ([0-9a-f]{40})", generation_text))
    assert oracle_commits

    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        result = call(server, "search_symbols", {"query": "Document"})

    records = [r for r in caplog.records if getattr(r, "tool", None) == "search_symbols"]
    assert len(records) == 1
    record = records[0]
    assert record.generation_id == oracle_generation  # type: ignore[attr-defined]
    assert record.source_commit in oracle_commits  # type: ignore[attr-defined]
    meta = result.meta
    assert meta is not None and meta["correlation_id"] == record.correlation_id  # type: ignore[attr-defined]
    assert re.fullmatch(r"[0-9a-f]{32}", meta["correlation_id"])


def test_an_unreadable_store_logs_no_generation_id_rather_than_inventing_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, scope_dir = baked_copy(tmp_path)
    server = make_server(root)
    (scope_dir / "active_pointer.json").unlink()

    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        call(server, "search_symbols", {"query": "Document"})

    record = next(r for r in caplog.records if getattr(r, "tool", None) == "search_symbols")
    assert record.generation_id is None  # type: ignore[attr-defined]
    assert record.source_commit is None  # type: ignore[attr-defined]


def test_the_server_version_comes_from_the_build_revision_environment_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.delenv("FOSS_MCP_BUILD_REVISION", raising=False)
    without = server_module.server_version()
    monkeypatch.setenv("FOSS_MCP_BUILD_REVISION", revision)

    with_env = server_module.server_version()

    assert with_env == f"0.1.0+{revision[:12]}"
    assert without != with_env
    # A malformed value is "unknown", an explicit file is read alone, and a file beats the variable.
    monkeypatch.setenv("FOSS_MCP_BUILD_REVISION", "not-a-revision")
    assert server_module.server_version() == without or server_module.server_version().endswith("+unknown")
    monkeypatch.setenv("FOSS_MCP_BUILD_REVISION", revision)
    assert server_module.server_version(tmp_path / "missing") == "0.1.0+unknown"
    file_revision = "f" * 40
    (tmp_path / "BUILD_REVISION").write_text(file_revision, encoding="ascii")
    assert server_module.server_version(tmp_path / "BUILD_REVISION") == f"0.1.0+{file_revision[:12]}"


# --- the install notice follows the ACTIVE generation's commit ------------------------------------------------


def _install_text(server: Any) -> str:
    result = call(server, "get_product_reference", {"section": "install"})
    assert result.is_error is False
    return json.dumps(result.structured_content["result"])


def test_after_a_rollback_the_install_notice_names_the_active_generations_commit_not_the_sidecars(
    tmp_path: Path,
) -> None:
    root, scope_dir = baked_copy(tmp_path)
    generation_commit = "cdf43df10c8c565ecaa978428b1fe66ad6685f8d"
    assert generation_commit in generation_path(scope_dir).read_text(encoding="utf-8")  # the oracle: the file
    newer = "f" * 40
    inputs = dataclasses.replace(
        load_manifest_sidecar(BAKED_ROOT / "product_reference_pdf_go.json"),
        source_ref=newer,
        install_verified=False,
        install_verified_coordinate="github.com/aspose-pdf-foss/aspose-pdf-foss-for-go",
        install_verified_checked_at="2026-10-17T00:00:00+00:00",
    )
    served = make_server(root, sidecars=lambda: SidecarSnapshot(inputs))
    frozen = make_server(root, product_reference_inputs=inputs)

    live_text = _install_text(served)
    frozen_text = _install_text(frozen)

    assert generation_commit in live_text
    assert newer not in live_text
    assert newer in frozen_text, "a server built with fixed inputs serves them as given"


def test_a_sidecar_on_one_of_the_generations_own_commits_is_left_alone(tmp_path: Path) -> None:
    root, _ = baked_copy(tmp_path)
    sidecar = load_manifest_sidecar(BAKED_ROOT / "product_reference_pdf_go.json")
    assert isinstance(sidecar, ProductReferenceInputs)
    inputs = dataclasses.replace(
        sidecar,
        source_ref=sidecar.source_ref[:12],  # an abbreviation of the generation's commit
        install_verified=False,
        install_verified_coordinate="github.com/aspose-pdf-foss/aspose-pdf-foss-for-go",
        install_verified_checked_at="2026-10-17T00:00:00+00:00",
    )
    served = make_server(root, sidecars=lambda: SidecarSnapshot(inputs))

    assert inputs.source_ref in _install_text(served)
