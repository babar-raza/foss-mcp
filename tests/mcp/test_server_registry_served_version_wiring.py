"""G3/TC-622 (REQ-G3-012), part (c): the already-written, already-landed ``served_version`` sidecar
field now reaches ``report_index_freshness``'s ``registry_served_version`` parameter through the real,
public, wired ``create_server`` / the real ``_live_report_index_freshness`` call site - not merely
through ``report_index_freshness`` called directly, which ``tests/mcp/tools/test_freshness_registry_version.py``
(TC-621) already covers in isolation.

Reuses this project's own in-process tool-call pattern (``tests/mcp/test_response_envelope.py``'s own
``_call`` helper: ``server.get_request_handler("tools/call")`` driven directly, no HTTP transport needed)
and the synthetic generation/``ProductReferenceInputs`` fixtures ``tests/mcp/tools/test_freshness_registry_version.py``
itself already built theirs with (``_publish``/``_symbol``/``SYMBOL_COMMIT``, ``_PYTHON_26_7``), rather than
inventing new ones.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import anyio
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import _to_jsonable, create_server
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from tests.mcp.tools.test_freshness_provenance import SYMBOL_COMMIT, _publish, _symbol
from tests.mcp.tools.test_freshness_tristate import _PYTHON_26_7

CONFIG = DeploymentConfig(family="pdf", platform="net")
SCOPE = Scope(family="pdf", platform="net")
GUARD_SECONDS = 60


def _store(tmp_path: Path) -> GenerationManifestStore:
    """A real generation at the pdf/net/self_extracted scope - the same symbols
    ``test_freshness_registry_version.py``'s own ``_single`` helper publishes."""
    return _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


# --- (a) a real install_verified_served_version string, through the real wired tool, produces -----
# --- a stale verdict with basis "registry_served_version" - the whole chain, end to end -----------


def test_a_real_served_version_string_produces_a_stale_verdict_through_the_real_wired_tool(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    # _PYTHON_26_7 declares manifest version 26.7.0 and has no source_repository commit alignment
    # signal and no releases are given to create_server, so no earlier-resolving signal exists:
    # the only thing that can produce a verdict is the registry-served-version comparison.
    inputs = dataclasses.replace(_PYTHON_26_7, install_verified_served_version="26.8.0")
    server = create_server(CONFIG, store, product_reference_inputs=inputs)

    result = _call(server, "report_index_freshness", {})

    assert result.is_error is False
    freshness = result.structured_content["result"]
    assert freshness["freshness_state"] == "stale"
    assert freshness["freshness_basis"] == "registry_served_version"
    assert "26.8.0" in freshness["reason"]
    assert "26.7.0" in freshness["reason"]


# --- (b) install_verified_served_version=None (the default, and what every EXISTING test already --
# --- constructs) produces byte-identical output to the pre-card call signature - the backward- -----
# --- compatibility proof -----------------------------------------------------------------------------


def test_a_none_served_version_is_byte_identical_to_the_pre_card_call_signature(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # _PYTHON_26_7 carries no install_verified_served_version field value at all; it defaults to
    # None, exactly what every ProductReferenceInputs built before this card also carries.
    server = create_server(CONFIG, store, product_reference_inputs=_PYTHON_26_7)

    actual = _call(server, "report_index_freshness", {}).structured_content["result"]

    # The exact call server.py made BEFORE this card: no registry_served_version argument at all
    # (it defaults to None in report_index_freshness's own signature, unchanged by this card).
    expected_report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        pinned_library_commit=_PYTHON_26_7.source_ref,
        product_reference=_PYTHON_26_7,
        releases=(),
    )
    assert actual == _to_jsonable(expected_report)


# --- (c) a malformed version string (parse_release_version returns None for it) degrades to --------
# --- registry_served_version=None being passed through, never raising ------------------------------


def test_a_malformed_served_version_string_degrades_to_none_without_raising(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inputs = dataclasses.replace(_PYTHON_26_7, install_verified_served_version="not-a-version")
    server = create_server(CONFIG, store, product_reference_inputs=inputs)

    result = _call(server, "report_index_freshness", {})

    assert result.is_error is False
    actual = result.structured_content["result"]
    expected_report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        pinned_library_commit=inputs.source_ref,
        product_reference=inputs,
        releases=(),
        registry_served_version=None,
    )
    assert actual == _to_jsonable(expected_report)
