"""G3/TC-544 (REQ-G3-008, ISOPROV-05): a trailing-slash path is a 404, never a redirect built from the
client's ``Host`` header.

Before this card ``POST /mcp/`` (and ``GET /mcp/``, ``/healthz/``) answered ``307`` with
``Location: http://<the client's Host header>/mcp``, because the Starlette router the MCP SDK builds has
``redirect_slashes`` on and builds the redirect URL from the request's ``Host`` header (the independent
reviewer reproduced ``Location: http://evil.example/mcp``). ``build_app`` now turns that off on the app it
wraps, so the server never builds a URL from a client header.

Offline and deterministic: ``serve_http.build_app`` over a COPY of the committed ``pdf/go`` baked scope under
``tmp_path`` (the repository is never written), driven through Starlette's in-process ``TestClient`` used as a
context manager so the lifespan runs. Every request carries ``Host: evil.example``. Nothing opens a socket.
"""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig

INFRA_DIR = Path(__file__).parents[2] / "infra"
sys.path.insert(0, str(INFRA_DIR))
import serve_http  # noqa: E402

REPO_ROOT = Path(__file__).parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SERVE_HTTP_SOURCE = INFRA_DIR / "serve_http.py"
SCOPE_DIR_NAME = "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
EVIL = "evil.example"
EVIL_HOST = {"Host": EVIL}

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.0.1"},
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Host": EVIL,
}


def _app(tmp_path: Path, allowed_origins: list[str] | None = None):
    root = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / SCOPE_DIR_NAME, root / SCOPE_DIR_NAME)
    store = GenerationManifestStore(root)
    return serve_http.build_app(
        CONFIG, manifest_store=store, allowed_origins=[] if allowed_origins is None else allowed_origins
    )


def _client(tmp_path: Path, allowed_origins: list[str] | None = None) -> TestClient:
    return TestClient(_app(tmp_path, allowed_origins), follow_redirects=False)


@pytest.mark.parametrize("method", ["POST", "GET"])
@pytest.mark.parametrize("path", ["/mcp/", "/healthz/", "/readyz/", "/metrics/"])
def test_a_trailing_slash_path_is_a_404_with_no_location_header(
    tmp_path: Path, method: str, path: str
) -> None:
    with _client(tmp_path) as client:
        response = client.request(method, path, headers=EVIL_HOST)
    assert response.status_code == 404, (method, path, response.status_code, response.text)
    assert "location" not in response.headers
    assert EVIL not in response.text
    assert EVIL not in str(dict(response.headers))


def test_the_canonical_paths_are_unchanged(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        healthz = client.get("/healthz", headers=EVIL_HOST)
        readyz = client.get("/readyz", headers=EVIL_HOST)
        metrics = client.get("/metrics", headers=EVIL_HOST)
        initialize = client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert healthz.status_code == 200
    assert readyz.status_code == 200, readyz.text
    assert metrics.status_code == 200
    assert initialize.status_code == 200, initialize.text
    assert EVIL not in initialize.text
    assert "location" not in initialize.headers


def test_the_origin_rejection_still_runs_before_routing(tmp_path: Path) -> None:
    """Exactly what tests/mcp/test_server_wiring.py pins: a present, disallowed Origin is a 400 ``rejected``."""
    with _client(tmp_path, allowed_origins=["https://example.com"]) as client:
        response = client.post(
            "/mcp",
            json=INITIALIZE_BODY,
            headers=dict(
                MCP_HEADERS, Origin="https://evil.example", **{"MCP-Protocol-Version": "2025-06-18"}
            ),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "rejected"
        assert "origin" in response.json()["reason"].lower()
        # The rejection also runs ahead of the router for a trailing-slash path: still no redirect.
        slashed = client.post(
            "/mcp/", json=INITIALIZE_BODY, headers=dict(MCP_HEADERS, Origin="https://evil.example")
        )
        assert slashed.status_code == 400
        assert "location" not in slashed.headers


def test_an_unsupported_protocol_version_is_still_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp", json=INITIALIZE_BODY, headers=dict(MCP_HEADERS, **{"MCP-Protocol-Version": "not-a-date"})
        )
    assert response.status_code == 400
    assert response.json()["error"] == "rejected"


def test_build_app_sets_redirect_slashes_false_exactly_once() -> None:
    source = SERVE_HTTP_SOURCE.read_text(encoding="utf-8")
    assert source.count("inner.router.redirect_slashes = False") == 1
    assert "redirect_slashes = True" not in source


def test_the_redirect_line_comes_right_after_the_app_is_built() -> None:
    """The line sits inside ``build_app``, after ``inner = server.streamable_http_app(...)``."""
    tree = ast.parse(SERVE_HTTP_SOURCE.read_text(encoding="utf-8"))
    build_app = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_app")
    statements = build_app.body
    assigned = [
        i
        for i, stmt in enumerate(statements)
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "inner" for t in stmt.targets)
    ]
    assert len(assigned) == 1
    follower = statements[assigned[0] + 1]
    assert isinstance(follower, ast.Assign)
    target = follower.targets[0]
    assert ast.unparse(target) == "inner.router.redirect_slashes"
    assert isinstance(follower.value, ast.Constant) and follower.value.value is False


def test_main_serves_build_app() -> None:
    """Production wiring: ``main`` hands a ``build_app(...)`` call to ``uvicorn.run``, so the line above is live."""
    tree = ast.parse(SERVE_HTTP_SOURCE.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    run_calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "uvicorn.run"
    ]
    assert len(run_calls) == 1
    first = run_calls[0].args[0]
    assert isinstance(first, ast.Call) and ast.unparse(first.func) == "build_app"
