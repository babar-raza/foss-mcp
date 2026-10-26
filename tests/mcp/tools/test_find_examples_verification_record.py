"""G3/TC-544 (REQ-G3-008, REQ-G3-001, finding D-09): a ``find_examples`` answer says what the generation
really holds about the snippet and what ``verified`` means.

Before this card an answer said only that a snippet is ``verified`` (and its ``provenance``). The verification
the ingestion verifier ran compiles or runs the snippet together with the README's own page-level imports and an
entry point it adds around it, none of which the answer stated. The chunk the server reads holds only the
``Source-Commit:`` line and the code (no toolchain, no flags), so three ADDITIVE fields are filled from what the
generation really holds: ``language`` (from the deployment's platform), ``source_commit`` (the chunk's
``Source-Commit:`` value) and ``verification`` (one fixed sentence per provenance).

Offline and deterministic: synthetic generations are published through the real ``publish_generation``; the
real-data tests read a COPY of the committed ``infra/demo-manifests`` (the repository is never written) with the
embedding gateway replaced by a stand-in. No clock, no network, no skip.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import create_server
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    AUTHORED_VERIFICATION_NOTE,
    README_VERIFICATION_NOTE,
    RUNTIME_LABEL_COMPILED_ONLY,
    ExampleMatch,
    _source_commit,
    _verification_note,
    find_examples,
    is_verified_example_text,
    runtime_label,
)
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
VALUES_YAML = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
FIND_EXAMPLES_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py"

COMMIT = "0123456789abcdef0123456789abcdef01234567"
GUARD_SECONDS = 60
MISSING_QUERY = "zzqq wombat tangerine"
HEX = re.compile(r"[0-9a-f]{7,}")

README_TEXT = (
    "FQN: Example: Merge Two Documents\nKind: verified_example\nMerges two documents into one.\n\n"
    f"Example:\ndocument.Merge(other)\nSource-Commit: {COMMIT}"
)
AUTHORED_TEXT = (
    "FQN: Example: Rotate A Page\nKind: verified_example\nProvenance: authored\nVerification: passed\n"
    f"Rotates one page.\n\nExample:\npage.Rotate(90)\nSource-Commit: {COMMIT}"
)
NO_COMMIT_TEXT = (
    "FQN: Example: Split A File\nKind: verified_example\nSplits one file.\n\nExample:\nfile.Split()"
)

PLATFORM_LANGUAGES = [
    ("net", "C#"),
    ("python", "Python"),
    ("java", "Java"),
    ("typescript", "TypeScript"),
    ("nodejs", "JavaScript (Node.js)"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("cpp", "C++"),
    ("cobol", ""),
]


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


class _OwnVectorProvider:
    """A stand-in gateway whose query embedding is exactly one stored point's own vector."""

    vector: tuple[float, ...] = ()

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self.vector for _ in texts]


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    """Every test starts without a gateway and with a fresh vector-fallback circuit breaker."""
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _publish(
    root: Path, texts: list[str], family: str = "pdf", platform: str = "net"
) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts*, embedded 8-dimensionally."""
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    document = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="https://example.invalid/repo", commit=COMMIT),
        evidence_refs=(),
        title=f"{family}/{platform} synthetic",
        body=body,
    )
    chunks = chunk_document(document)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease(f"{family}::{platform}::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family=family,
        platform=platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


def _point_vector(store: GenerationManifestStore, scope: Scope, fqn: str) -> tuple[float, ...]:
    key = f"{scope.family}::{scope.platform}::self_extracted"
    manifest = store.read_generation(key, store.read_active(key) or "")
    points = [p for p in manifest.payload["vector_index"]["points"] if f"FQN: {fqn}\n" in p["text"]]
    assert len(points) == 1
    return tuple(points[0]["vector"])


def _use_vector_of(
    monkeypatch: pytest.MonkeyPatch, store: GenerationManifestStore, scope: Scope, fqn: str
) -> None:
    provider = type("Provider", (_OwnVectorProvider,), {"vector": _point_vector(store, scope, fqn)})
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", provider)


# --- language ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("platform", "language"), PLATFORM_LANGUAGES, ids=[p for p, _ in PLATFORM_LANGUAGES])
def test_language_follows_the_platform(tmp_path: Path, platform: str, language: str) -> None:
    scope = Scope(family="pdf", platform=platform)
    store = _publish(tmp_path / "m", [README_TEXT], platform=platform)
    result = find_examples(store, scope, "Example: Merge Two Documents")
    assert isinstance(result, list) and len(result) == 1
    assert result[0].language == language


# --- source_commit -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (README_TEXT, COMMIT),
        (NO_COMMIT_TEXT, ""),
        ("Example:\ncode()\nSource-Commit: abc1234\nSource-Commit: ffffffff", "abc1234"),
        ("Source-Commit:   deadbeef  \nExample:\ncode()", "deadbeef"),
        ("Example:\ncode()\r\nSource-Commit: 1234567\r\n", "1234567"),
        ("a Source-Commit: not-at-line-start", ""),
        ("Source-Commit:", ""),
        ("", ""),
    ],
    ids=["present", "absent", "first-of-two", "padded", "crlf", "mid-line", "no-value", "empty-text"],
)
def test_source_commit_is_the_chunks_source_commit_line_and_empty_without_one(
    text: str, expected: str
) -> None:
    assert _source_commit(text) == expected


def test_a_served_example_without_a_commit_line_has_an_empty_source_commit(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [NO_COMMIT_TEXT, README_TEXT])
    result = find_examples(store, Scope("pdf", "net"), "Example: Split A File")
    assert isinstance(result, list) and len(result) == 1
    assert result[0].source_commit == ""
    assert result[0].language == "C#"
    assert result[0].verification == README_VERIFICATION_NOTE


# --- verification --------------------------------------------------------------------------------


def test_a_readme_example_carries_the_readme_verification_sentence_and_an_authored_one_the_authored_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = Scope("pdf", "net")
    store = _publish(tmp_path / "m", [README_TEXT, AUTHORED_TEXT])

    # exact branch
    readme = find_examples(store, scope, "Example: Merge Two Documents")
    authored = find_examples(store, scope, "Example: Rotate A Page")
    assert isinstance(readme, list) and isinstance(authored, list)
    assert [(m.source, m.provenance, m.verification) for m in readme] == [
        ("exact", "readme", README_VERIFICATION_NOTE)
    ]
    assert [(m.source, m.provenance, m.verification) for m in authored] == [
        ("exact", "authored", AUTHORED_VERIFICATION_NOTE)
    ]

    # lexical branch
    readme = find_examples(store, scope, "merge two documents")
    authored = find_examples(store, scope, "rotate a page")
    assert isinstance(readme, list) and isinstance(authored, list)
    assert [(m.source, m.verification) for m in readme] == [("lexical", README_VERIFICATION_NOTE)]
    assert [(m.source, m.verification) for m in authored] == [("lexical", AUTHORED_VERIFICATION_NOTE)]
    assert readme[0].source_commit == authored[0].source_commit == COMMIT
    assert readme[0].language == authored[0].language == "C#"

    # vector branch
    _use_vector_of(monkeypatch, store, scope, "Example: Merge Two Documents")
    near_readme = find_examples(store, scope, MISSING_QUERY)
    _set_guard(None)
    _use_vector_of(monkeypatch, store, scope, "Example: Rotate A Page")
    near_authored = find_examples(store, scope, MISSING_QUERY)
    assert isinstance(near_readme, list) and isinstance(near_authored, list)
    by_fqn = {m.fqn: m for m in near_readme + near_authored}
    assert {m.source for m in by_fqn.values()} == {"vector"}
    assert by_fqn["Example: Merge Two Documents"].verification == README_VERIFICATION_NOTE
    assert by_fqn["Example: Rotate A Page"].verification == AUTHORED_VERIFICATION_NOTE
    for match in by_fqn.values():
        assert match.source_commit == COMMIT
        assert match.language == "C#"


def test_the_sentences_name_the_commit_field_and_never_promise_what_the_chunk_does_not_hold() -> None:
    for sentence in (README_VERIFICATION_NOTE, AUTHORED_VERIFICATION_NOTE):
        assert "source_commit" in sentence
        assert "imports and entry point" in sentence
        lowered = sentence.lower()
        for word in ("toolchain", "flag", "version", "sdk", "jdk", "-std", "target framework"):
            assert word not in lowered, (word, sentence)
    assert "README's own imports" in README_VERIFICATION_NOTE
    assert "entry point around this snippet" in README_VERIFICATION_NOTE
    assert README_VERIFICATION_NOTE != AUTHORED_VERIFICATION_NOTE
    assert AUTHORED_VERIFICATION_NOTE.startswith("Authored for this index")


def test_an_example_match_built_by_hand_still_defaults_the_new_fields_to_empty() -> None:
    match = ExampleMatch(Scope("pdf", "net"), "g", "Example: x", "code()", coverage=1.0, source="exact")
    assert (match.language, match.source_commit, match.verification) == ("", "", "")
    assert match.provenance == "readme" and match.match_basis == ""


def test_the_new_fields_are_the_last_three_fields_of_the_match() -> None:
    names = list(ExampleMatch.__dataclass_fields__)
    # TC-584 appended one more additive field (confidence) after verification.
    assert names[-4:] == ["language", "source_commit", "verification", "confidence"]
    assert names[-6:-4] == ["provenance", "match_basis"]


def test_the_source_never_builds_a_pattern_with_a_scanned_call() -> None:
    """test_symbol_tools scans this source for text that would suggest a snippet is executed."""
    source = FIND_EXAMPLES_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("eval(", "exec(", "compile(", "subprocess.", "import subprocess", "os.system("):
        assert forbidden not in source, forbidden


# --- through the real tools/call handler ----------------------------------------------------------


def _call(server: Any, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name="find_examples", arguments=arguments))

    return anyio.run(run)


def _pdf_net_example_titles() -> list[tuple[str, str]]:
    directory = DEMO_MANIFESTS / "pdf__net__self_extracted"
    generation = next(directory.glob("generation__*.json"))
    documents = json.loads(generation.read_text(encoding="utf-8"))["payload"]["lexical_index"]["documents"]
    return [
        (extract_fqn(d["text"]) or "", d["text"])
        for d in documents.values()
        if is_verified_example_text(d["text"])
    ]


def test_the_new_fields_reach_the_tools_call_json(tmp_path: Path) -> None:
    root = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / "pdf__net__self_extracted", root / "pdf__net__self_extracted")
    examples = _pdf_net_example_titles()
    assert examples
    fqn, text = examples[0]
    expected_note = (
        AUTHORED_VERIFICATION_NOTE
        if "Provenance: authored" in text.splitlines()
        else README_VERIFICATION_NOTE
    )
    server = create_server(
        DeploymentConfig(family="pdf", platform="net"), manifest_store=GenerationManifestStore(root)
    )

    result = _call(server, {"query": fqn})

    assert result.is_error is False
    structured = result.structured_content["result"]
    served = json.loads(result.content[0].text)
    assert served == structured
    assert isinstance(structured, list) and len(structured) == 1
    item = structured[0]
    assert item["fqn"] == fqn
    assert item["language"] == "C#"
    assert item["source_commit"] == _source_commit(text)
    assert item["verification"] == expected_note
    # The response envelope (TC-514) appends its own keys after the match's; the match's new keys come last.
    keys = list(item)
    start = keys.index("language")
    assert keys[start : start + 3] == ["language", "source_commit", "verification"]
    assert keys[start - 2 : start] == ["provenance", "match_basis"]


# --- every pilot ----------------------------------------------------------------------------------


def _pilots() -> list[tuple[str, str]]:
    values = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))
    return [(pilot["family"], pilot["platform"]) for pilot in values["ingestion"]["pilots"]]


VERIFIED_EXAMPLES = REPO_ROOT / "tests" / "fixtures" / "verified_examples"


def _recorded_runtime_error(family: str, platform: str, title: str) -> str:
    """The ``runtime_error`` the ingestion verifier recorded for *title* in the pilot's own
    ``tests/fixtures/verified_examples/<family>_<platform>.json`` cache, or ``""`` when the cache
    has no entry for it, the entry was never run, or it ran without error.

    TC-536: this is the ONE case (pdf/cpp's "Open a Password-Protected Document", TC-565's own
    intended, accepted behaviour) where the cache records ``ran: true`` with a real
    ``runtime_error``; every other entry of every other pilot has no recorded runtime failure.
    """
    path = VERIFIED_EXAMPLES / f"{family}_{platform}.json"
    if not path.is_file():
        return ""
    entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
    for entry in entries:
        if entry.get("title") == title and entry.get("ran") and entry.get("runtime_error"):
            return str(entry["runtime_error"])
    return ""


def test_every_served_example_of_every_pilot_carries_language_commit_and_verification(
    tmp_path: Path,
) -> None:
    pilots = _pilots()
    # re-pinned by TC-551: 40 -> 32
    assert len(pilots) == 32
    root = tmp_path / "manifests"
    for family, platform in pilots:
        name = f"{family}__{platform}__self_extracted"
        shutil.copytree(DEMO_MANIFESTS / name, root / name)
    store = GenerationManifestStore(root)

    seen = 0
    commits_present = 0
    for family, platform in pilots:
        scope = Scope(family=family, platform=platform)
        key = f"{family}::{platform}::self_extracted"
        manifest = store.read_generation(key, store.read_active(key) or "")
        documents = manifest.payload["lexical_index"]["documents"]
        # The exact branch answers with the FIRST verified document of a title; expect that one.
        first_by_fqn: dict[str, str] = {}
        for document in documents.values():
            text = document["text"]
            if is_verified_example_text(text):
                first_by_fqn.setdefault(extract_fqn(text) or "", text)
        pilot_seen = 0
        for fqn, text in first_by_fqn.items():
            result = find_examples(store, scope, fqn)
            assert isinstance(result, list) and len(result) == 1, (family, platform, fqn)
            match = result[0]
            assert match.source == "exact" and match.fqn == fqn
            assert match.language == dict(PLATFORM_LANGUAGES)[platform] != "", (family, platform)
            has_commit_line = any(line.startswith("Source-Commit:") for line in text.splitlines())
            if has_commit_line:
                assert HEX.fullmatch(match.source_commit), (family, platform, fqn, match.source_commit)
                commits_present += 1
            else:
                assert match.source_commit == ""
            authored = "Provenance: authored" in text.splitlines()
            # TC-536: this re-ingest is the first where the baked generation actually carries Runtime-*
            # lines for several examples (pdf/cpp, 3d/net and 3d/java - every real runtime outcome
            # runtime_label() knows: ran, no_output and failed, not only pdf/cpp's one failing case the
            # dispatch named), so a bare AUTHORED_VERIFICATION_NOTE/README_VERIFICATION_NOTE guess is no
            # longer honest for them. The oracle is find_examples.py's own runtime_label()/
            # _verification_note() read directly off the real chunk text - the same function that
            # produces match.verification - exactly as this file already does for match.source_commit
            # (_source_commit(text)) a few lines above and in test_synthetic_* below.
            expected = _verification_note(text)
            if runtime_label(text) == RUNTIME_LABEL_COMPILED_ONLY:
                # The common case (no Runtime-Status line at all) still equals the fixed sentence.
                assert expected == (AUTHORED_VERIFICATION_NOTE if authored else README_VERIFICATION_NOTE)
            elif family == "pdf" and platform == "cpp" and "Password-Protected" in fqn:
                # The one case the dispatch named explicitly: cross-checked against the real recorded
                # error text in tests/fixtures/verified_examples/pdf_cpp.json, not only against the
                # production function that also produced `expected` above.
                cache_error = _recorded_runtime_error("pdf", "cpp", "Open a Password-Protected Document")
                assert cache_error and cache_error in expected, (expected, cache_error)
            assert match.verification == expected, (family, platform, fqn)
            pilot_seen += 1
        assert pilot_seen >= 1, (family, platform)
        seen += pilot_seen

    assert seen >= 80, seen
    assert commits_present >= 80, commits_present
