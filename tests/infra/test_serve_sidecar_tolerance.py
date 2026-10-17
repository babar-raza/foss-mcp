"""G3/TC-507: a bad optional sidecar degrades that ONE sidecar to ``fetch_failed``; it never crashes the
server and is never served as an absence or as another product's data.

Before this card ``_serving_product_reference_inputs`` and ``_serving_recent_releases`` raised on any
unusable sidecar (an empty file, a truncated write, ``{}``, a JSON list, an unknown document status, a
null release body), so the container exited 1 at start and the pod went into CrashLoopBackOff although the
generation was perfect. A sidecar that belonged to another product was served as this product's. These
tests build the real serving path over doctored copies of the baked ``pdf/go`` sidecars in ``tmp_path``.

Offline and deterministic. The doctored cases mirror the independent reviewer's.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.sidecar_state import SIDECAR_FETCH_FAILED, SIDECAR_NOT_PRESENT, SIDECAR_PRESENT

REPO_ROOT = Path(__file__).parents[2]
INFRA_DIR = REPO_ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))
import serve_http  # noqa: E402
import serve_stdio  # noqa: E402

DEMO = INFRA_DIR / "demo-manifests"
CONFIG = DeploymentConfig(family="pdf", platform="go")
PRODUCT_REFERENCE = "product_reference_pdf_go.json"
RELEASES = "recent_releases_pdf_go.json"
REGISTRY = "package_registry_pdf_go.json"
GOOD_FETCHED_AT = "2026-10-15T08:00:00Z"
MAX_BYTES = 8 * 1024 * 1024
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def _stage(tmp_path: Path, *names: str) -> Path:
    """A manifests directory holding copies of the named baked ``pdf/go`` sidecars."""
    directory = tmp_path / "manifests"
    directory.mkdir(exist_ok=True)
    for name in names:
        shutil.copy(DEMO / name, directory / name)
    return directory


def _good(name: str) -> bytes:
    return (DEMO / name).read_bytes()


def _good_product_reference() -> dict[str, Any]:
    return json.loads(_good(PRODUCT_REFERENCE))


def _dumps(data: Any) -> bytes:
    return json.dumps(data).encode("utf-8")


def _sidecar_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and hasattr(record, "sidecar")
    ]


def _assert_log_is_clean(caplog: pytest.LogCaptureFixture, tmp_path: Path, content: bytes) -> None:
    """No line of the log carries the sidecar's content or the temporary path."""
    assert str(tmp_path) not in caplog.text
    text = content.decode("utf-8", errors="replace").strip()
    if text:
        assert text not in caplog.text
        for line in text.splitlines():
            if len(line) > 20:
                assert line not in caplog.text


# --- the product-reference sidecar ----------------------------------------------------------------

WRONG_PLATFORM = {**_good_product_reference(), "platform": "net"}
WRONG_FAMILY_KEY = {**_good_product_reference(), "family": "cells"}
UNKNOWN_STATUS = {
    **_good_product_reference(),
    "contributing": {"status": "bogus-status", "path": "CONTRIBUTING.md"},
}

BAD_PRODUCT_REFERENCES: dict[str, bytes] = {
    "empty_file": b"",
    "truncated": _good(PRODUCT_REFERENCE)[: len(_good(PRODUCT_REFERENCE)) // 2],
    "empty_object": b"{}",
    "json_list": b"[]",
    "unknown_document_status": _dumps(UNKNOWN_STATUS),
    "non_utf8_bytes": b"\xff\xfe\x00{\x80",
    "oversized_over_8_mib": b" " * (MAX_BYTES + 1),
    "wrong_platform": _dumps(WRONG_PLATFORM),
    "wrong_family_key": _dumps(WRONG_FAMILY_KEY),
    # re-pinned by TC-551: <removed pilot> -> product_reference_cells_go
    "other_product_same_platform": _good("product_reference_cells_go.json"),
    "failure_placeholder_state_key": _dumps({"state": "fetch_failed", "fetched_at": GOOD_FETCHED_AT}),
}


@pytest.mark.parametrize("case", list(BAD_PRODUCT_REFERENCES))
def test_a_bad_product_reference_sidecar_degrades_to_fetch_failed_and_never_raises(
    case: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    content = BAD_PRODUCT_REFERENCES[case]
    (manifests / PRODUCT_REFERENCE).write_bytes(content)
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_FETCH_FAILED
    assert inputs.platform == "go"
    assert inputs.manifest_text is None
    (record,) = _sidecar_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.sidecar == "product_reference"  # type: ignore[attr-defined]
    assert record.family == "pdf"  # type: ignore[attr-defined]
    assert record.platform == "go"  # type: ignore[attr-defined]
    problem = record.problem  # type: ignore[attr-defined]
    assert isinstance(problem, str)
    assert problem
    assert record.file == PRODUCT_REFERENCE  # type: ignore[attr-defined]
    _assert_log_is_clean(caplog, tmp_path, content)


def test_the_placeholder_carries_its_fetched_at_into_the_inputs(tmp_path: Path) -> None:
    manifests = _stage(tmp_path)
    (manifests / PRODUCT_REFERENCE).write_bytes(BAD_PRODUCT_REFERENCES["failure_placeholder_state_key"])
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_fetched_at == GOOD_FETCHED_AT


def test_an_absent_product_reference_sidecar_is_still_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert serve_http._serving_product_reference_inputs(manifests, CONFIG) is None
    assert not _sidecar_records(caplog)


def test_a_failure_marker_without_a_sidecar_is_fetch_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    (manifests / (PRODUCT_REFERENCE + ".failed")).write_text(
        "HTTPError 2026-10-15T09:30:00Z\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_FETCH_FAILED
    assert inputs.sidecar_fetched_at == "2026-10-15T09:30:00Z"
    (record,) = _sidecar_records(caplog)
    assert record.sidecar == "product_reference"  # type: ignore[attr-defined]


def test_a_failure_marker_beside_a_good_sidecar_keeps_serving_it_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE)
    (manifests / (PRODUCT_REFERENCE + ".failed")).write_text(
        "HTTPError 2026-10-15T09:30:00Z\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_PRESENT
    assert inputs.manifest_text is not None
    (record,) = _sidecar_records(caplog)
    assert record.getMessage() == "refresh failed, serving the last good sidecar"


def test_a_good_sidecar_is_present_and_passes_fetched_at_through(tmp_path: Path) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE)
    data = _good_product_reference()
    data["fetched_at"] = GOOD_FETCHED_AT
    (manifests / PRODUCT_REFERENCE).write_bytes(_dumps(data))
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_PRESENT
    assert inputs.sidecar_fetched_at == GOOD_FETCHED_AT
    assert inputs.platform == "go"
    assert inputs.manifest_text is not None


def test_a_good_sidecar_without_fetched_at_is_present_with_none(tmp_path: Path) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE)
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_PRESENT
    assert inputs.sidecar_fetched_at is None


def test_a_wrongly_typed_manifest_text_degrades_to_fetch_failed(tmp_path: Path) -> None:
    manifests = _stage(tmp_path)
    data = _good_product_reference()
    data["manifest_text"] = 12345
    (manifests / PRODUCT_REFERENCE).write_bytes(_dumps(data))
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_FETCH_FAILED


# --- the package-registry sidecar -------------------------------------------------------------------

BAD_REGISTRIES: dict[str, bytes] = {
    "corrupt": b"{not json at all",
    "empty_object": b"{}",
    "verified_as_string": _dumps(
        {
            "ecosystem": "go",
            "coordinate": "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go",
            "verified": "false",
            "checked_at": "2026-10-14T13:17:41+00:00",
        }
    ),
    "truncated": _good(REGISTRY)[:40],
}


def test_a_good_registry_sidecar_is_merged_without_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE, REGISTRY)
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_PRESENT
    assert inputs.install_verified is True
    assert inputs.install_verified_coordinate
    assert not _sidecar_records(caplog)


@pytest.mark.parametrize("case", list(BAD_REGISTRIES))
def test_the_registry_sidecar_problems_are_ignored_with_a_warning(
    case: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE)
    (manifests / REGISTRY).write_bytes(BAD_REGISTRIES[case])
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.sidecar_state == SIDECAR_PRESENT
    assert inputs.install_verified is None
    assert inputs.install_verified_coordinate is None
    (record,) = _sidecar_records(caplog)
    assert record.sidecar == "package_registry"  # type: ignore[attr-defined]
    assert record.levelno == logging.WARNING
    _assert_log_is_clean(caplog, tmp_path, BAD_REGISTRIES[case])


# --- the recent-releases sidecar --------------------------------------------------------------------

GOOD_RELEASES: list[dict[str, Any]] = json.loads(_good(RELEASES))
BAD_RELEASES: dict[str, bytes] = {
    "empty_file": b"",
    "json_object": b'{"releases": []}',
    "list_of_integers": b"[1, 2, 3]",
    "missing_keys": _dumps([{"tag_name": "v1.0.0"}]),
    "null_body": _dumps([{**GOOD_RELEASES[0], "body": None}]),
    "oversized": b" " * (MAX_BYTES + 1),
    # re-pinned by TC-551: <removed pilot> -> recent_releases_cells_go
    "releases_of_another_product": _good("recent_releases_cells_go.json"),
    "repository_differs_from_the_product_reference": _dumps(
        [
            {
                **GOOD_RELEASES[0],
                "url": "https://github.com/aspose-pdf-foss/Some-Other-PDF-Repository/releases/tag/v0.11.0",
            }
        ]
    ),
}


@pytest.mark.parametrize("case", list(BAD_RELEASES))
def test_a_bad_releases_sidecar_degrades_to_fetch_failed(
    case: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE)
    content = BAD_RELEASES[case]
    (manifests / RELEASES).write_bytes(content)
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    assert inputs.source_repository
    with caplog.at_level(logging.WARNING):
        releases, state = serve_http._resolve_recent_releases(
            manifests, CONFIG, source_repository=inputs.source_repository
        )
    assert state == SIDECAR_FETCH_FAILED
    assert releases == ()
    (record,) = _sidecar_records(caplog)
    assert record.sidecar == "recent_releases"  # type: ignore[attr-defined]
    assert record.levelno == logging.WARNING
    assert record.file == RELEASES  # type: ignore[attr-defined]
    _assert_log_is_clean(caplog, tmp_path, content)


def test_a_release_url_that_is_not_a_repository_url_degrades_to_fetch_failed(tmp_path: Path) -> None:
    manifests = _stage(tmp_path)
    (manifests / RELEASES).write_bytes(_dumps([{**GOOD_RELEASES[0], "url": "not a url"}]))
    releases, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    assert (releases, state) == ((), SIDECAR_FETCH_FAILED)


def test_a_release_with_an_optional_field_of_the_wrong_type_degrades_to_fetch_failed(
    tmp_path: Path,
) -> None:
    manifests = _stage(tmp_path)
    (manifests / RELEASES).write_bytes(_dumps([{**GOOD_RELEASES[0], "published_at": 20261015}]))
    releases, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    assert (releases, state) == ((), SIDECAR_FETCH_FAILED)


def test_the_good_releases_sidecar_is_present(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    manifests = _stage(tmp_path, PRODUCT_REFERENCE, RELEASES)
    inputs = serve_http._serving_product_reference_inputs(manifests, CONFIG)
    assert inputs is not None
    with caplog.at_level(logging.WARNING):
        releases, state = serve_http._resolve_recent_releases(
            manifests, CONFIG, source_repository=inputs.source_repository
        )
    assert state == SIDECAR_PRESENT
    assert len(releases) == len(GOOD_RELEASES)
    assert all(isinstance(release, Release) for release in releases)
    assert not _sidecar_records(caplog)


def test_an_empty_list_with_a_failure_marker_is_fetch_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    (manifests / RELEASES).write_text("[]", encoding="utf-8")
    (manifests / (RELEASES + ".failed")).write_text("HTTPError 2026-10-15T09:30:00Z\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        releases, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    assert (releases, state) == ((), SIDECAR_FETCH_FAILED)
    assert len(_sidecar_records(caplog)) == 1


def test_an_empty_list_without_a_marker_is_present_and_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    (manifests / RELEASES).write_text("[]", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        releases, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    assert (releases, state) == ((), SIDECAR_PRESENT)
    assert not _sidecar_records(caplog)


def test_a_nonempty_list_with_a_marker_is_present_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path, RELEASES)
    (manifests / (RELEASES + ".failed")).write_text("HTTPError 2026-10-15T09:30:00Z\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        releases, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    assert state == SIDECAR_PRESENT
    assert len(releases) == len(GOOD_RELEASES)
    (record,) = _sidecar_records(caplog)
    assert record.getMessage() == "refresh failed, serving the last good sidecar"


def test_an_absent_releases_sidecar_is_not_present_and_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _stage(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert serve_http._resolve_recent_releases(manifests, CONFIG) == ((), SIDECAR_NOT_PRESENT)
    assert not _sidecar_records(caplog)


def test_a_releases_failure_marker_without_a_sidecar_is_fetch_failed(tmp_path: Path) -> None:
    manifests = _stage(tmp_path)
    (manifests / (RELEASES + ".failed")).write_text("HTTPError 2026-10-15T09:30:00Z\n", encoding="utf-8")
    assert serve_http._resolve_recent_releases(manifests, CONFIG) == ((), SIDECAR_FETCH_FAILED)


def test_serving_recent_releases_returns_the_releases_of_resolve(tmp_path: Path) -> None:
    manifests = _stage(tmp_path, RELEASES)
    resolved, state = serve_http._resolve_recent_releases(manifests, CONFIG)
    served = serve_http._serving_recent_releases(manifests, CONFIG)
    assert served == resolved
    assert served.state == state == SIDECAR_PRESENT
    (manifests / RELEASES).write_bytes(b"")
    failed = serve_http._serving_recent_releases(manifests, CONFIG)
    assert failed == ()
    assert failed.state == SIDECAR_FETCH_FAILED


# --- helpers ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HTTPError 2026-10-15T09:30:00Z\n", "2026-10-15T09:30:00Z"),
        ("OSError\n", None),
        ("", None),
    ],
)
def test_the_failure_marker_stamp_is_the_last_iso_token(
    tmp_path: Path, text: str, expected: str | None
) -> None:
    sidecar = tmp_path / "sidecar.json"
    (tmp_path / "sidecar.json.failed").write_text(text, encoding="utf-8")
    assert serve_http._failure_marker(sidecar) == (True, expected)


def test_no_marker_is_reported_as_none(tmp_path: Path) -> None:
    assert serve_http._failure_marker(tmp_path / "sidecar.json") == (False, None)


@pytest.mark.parametrize(
    ("family", "repository", "expected"),
    [
        ("pdf", "aspose-pdf-foss/Aspose-PDF-FOSS-for-Go", True),
        # re-pinned by TC-551: <removed pilot>; <removed pilot> -> email; Email
        ("email", "aspose-email-foss/Aspose.Email-FOSS-for-Node.js", True),
        ("3d", "aspose-3d-foss/Aspose.3D-FOSS-for-.NET", True),
        ("pdf", "https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET.git", True),
        ("pdf", "aspose-cells-foss/Aspose.Cells-FOSS-for-Go", False),
        ("pdf", "", False),
    ],
)
def test_family_in_repository(family: str, repository: str, expected: bool) -> None:
    assert serve_http._family_in_repository(family, repository) is expected


def test_read_sidecar_json_distinguishes_absent_unusable_and_good(tmp_path: Path) -> None:
    assert serve_http._read_sidecar_json(tmp_path / "none.json") == (SIDECAR_NOT_PRESENT, None)
    (tmp_path / "bad.json").write_bytes(b"{")
    assert serve_http._read_sidecar_json(tmp_path / "bad.json")[0] == SIDECAR_FETCH_FAILED
    (tmp_path / "good.json").write_bytes(b'{"a": 1}')
    assert serve_http._read_sidecar_json(tmp_path / "good.json") == (SIDECAR_PRESENT, {"a": 1})
    (tmp_path / "directory.json").mkdir()
    assert serve_http._read_sidecar_json(tmp_path / "directory.json")[0] == SIDECAR_FETCH_FAILED


# --- the running server -----------------------------------------------------------------------------


def _copy_baked_scope(tmp_path: Path) -> Path:
    """A manifests directory holding a copy of the baked ``pdf/go`` generation and its three sidecars."""
    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO / "pdf__go__self_extracted", manifests / "pdf__go__self_extracted")
    for name in (PRODUCT_REFERENCE, RELEASES, REGISTRY):
        shutil.copy(DEMO / name, manifests / name)
    return manifests


def _decode_sse(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return json.loads(text)


def _call_over_http(client: TestClient, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tolerance-test", "version": "0.0.1"},
            },
        },
        headers=HEADERS,
    )
    assert init.status_code == 200, init.text
    headers = dict(HEADERS, **{"mcp-session-id": init.headers["mcp-session-id"]})
    client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    answers: list[dict[str, Any]] = []
    for index, (name, arguments) in enumerate(calls, start=1):
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        answers.append(_decode_sse(response.text)["result"])
    return answers


COMBINATIONS: dict[str, tuple[bytes | None, bytes | None]] = {
    "corrupt_product_reference": (BAD_PRODUCT_REFERENCES["truncated"], None),
    "corrupt_releases": (None, BAD_RELEASES["json_object"]),
    "both_corrupt": (BAD_PRODUCT_REFERENCES["empty_object"], BAD_RELEASES["empty_file"]),
    "wrong_family_pair": (
        # re-pinned by TC-551: <removed pilot> -> product_reference_cells_go
        _good("product_reference_cells_go.json"),
        # re-pinned by TC-551: <removed pilot> -> recent_releases_cells_go
        _good("recent_releases_cells_go.json"),
    ),
}

TOOL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_symbols", {"query": "Document"}),
    ("get_product_reference", {"section": "install"}),
    ("get_product_reference", {"section": "agent_guidance"}),
    ("list_recent_changes", {}),
]


@pytest.mark.parametrize("combination", list(COMBINATIONS))
def test_build_app_starts_for_every_doctored_combination(
    combination: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _copy_baked_scope(tmp_path)
    product_reference, releases = COMBINATIONS[combination]
    if product_reference is not None:
        (manifests / PRODUCT_REFERENCE).write_bytes(product_reference)
    if releases is not None:
        (manifests / RELEASES).write_bytes(releases)
    with caplog.at_level(logging.WARNING):
        app = serve_http.build_app(
            CONFIG,
            manifest_store=GenerationManifestStore(manifests),
            allowed_origins=[],
            manifests_dir=manifests,
        )
    assert _sidecar_records(caplog)
    with TestClient(app) as client:
        assert client.get(serve_http.READYZ_PATH).status_code == 200
        assert client.get(serve_http.HEALTHZ_PATH).status_code == 200
        search, install, guidance, changes = _call_over_http(client, TOOL_CALLS)
    assert not search.get("isError", False)
    assert search["structuredContent"]["result"], "the generation is served whatever the sidecars hold"
    product_reference_failed = product_reference is not None
    install_answer = install["structuredContent"]["result"]
    assert not install.get("isError", False)
    if product_reference_failed:
        assert "could not be fetched" in install_answer["reason"]
        assert "is not present in this repository" not in json.dumps(guidance["structuredContent"])
        assert "could not be fetched" in guidance["structuredContent"]["result"]["reason"]
    else:
        assert install_answer["text"].startswith("go get ")
    if releases is not None:
        assert changes["isError"] is True
        assert changes["structuredContent"]["code"] == "unavailable"
    else:
        assert not changes.get("isError", False)
        assert isinstance(changes["structuredContent"]["result"], list)


def test_build_stdio_server_survives_a_corrupt_sidecar(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifests = _copy_baked_scope(tmp_path)
    (manifests / PRODUCT_REFERENCE).write_bytes(BAD_PRODUCT_REFERENCES["truncated"])
    (manifests / RELEASES).write_bytes(BAD_RELEASES["empty_file"])
    with caplog.at_level(logging.WARNING):
        server = serve_stdio.build_stdio_server(CONFIG, GenerationManifestStore(manifests), manifests)
    assert len(_sidecar_records(caplog)) == 2

    async def run() -> list[Any]:
        async with Client(server) as client:
            return [
                await client.call_tool("search_symbols", {"query": "Document"}),
                await client.call_tool("get_product_reference", {"section": "install"}),
                await client.call_tool("list_recent_changes", {}),
            ]

    search, install, changes = anyio.run(run)
    assert not search.is_error
    assert not install.is_error
    assert "could not be fetched" in install.structured_content["result"]["reason"]
    assert changes.is_error
    assert changes.structured_content["code"] == "unavailable"
