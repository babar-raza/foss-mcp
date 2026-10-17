"""TC-529: ``infra/rollback.py`` gains ``--list`` and ``--previous`` and refuses a bad target (GRR-06, GRR-08).

Before this card an operator had to type the full generation id by hand, nothing listed the retained
generations, and a target of another product with an existing version suffix was accepted. These tests
drive the real CLI in process (``rollback.main()`` with a monkeypatched ``sys.argv``) against a real
``GenerationManifestStore`` on ``tmp_path``: no sockets, no sleeps, no cluster.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import infra.rollback as rollback
from foss_mcp.indexing.generation_manifest import (
    GenerationManifestStore,
    ManifestValidationError,
    UnknownGenerationError,
    build_manifest,
    publish,
)
from foss_mcp.indexing.hashing_embedding_provider import HashingEmbeddingProvider
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance

FAMILY, PLATFORM, SOURCE_KIND = "pdf", "go", "self_extracted"
SCOPE = "::".join((FAMILY, PLATFORM, SOURCE_KIND))
SCOPE_DIR_NAME = "__".join((FAMILY, PLATFORM, SOURCE_KIND))
REPO_ROOT = Path(__file__).resolve().parents[2]

_PROVENANCE = Provenance(repository="org/repo-a", commit="c" * 40, path="api_surface.json")


def _chunks(symbols: int, docs: int, examples: int) -> list[Chunk]:
    texts = [f"FQN: Sym{i}\nKind: type_spec\nA documented type number {i}." for i in range(symbols)]
    texts += [f"FQN: Doc: Guide{i}\nA guide section number {i}." for i in range(docs)]
    texts += [f"FQN: Example: Demo{i}\nAn example number {i}." for i in range(examples)]
    return [
        Chunk(
            section_title=f"section-{i}",
            text=text,
            source_kind="self_extracted",
            content_type="api_surface",
            provenance=_PROVENANCE,
            trust_tier="highest",
        )
        for i, text in enumerate(texts)
    ]


def _publish(store: GenerationManifestStore, version: str, chunks: list[Chunk]) -> str:
    expected = store.read_active(SCOPE)
    lease = store.acquire_lease(SCOPE, "ingestion", "pending")
    return publish_generation(
        store,
        family=FAMILY,
        platform=PLATFORM,
        source_kind=SOURCE_KIND,
        expected_active=expected,
        chunks=chunks,
        embedding_provider=HashingEmbeddingProvider(),
        lease=lease,
        version=version,
    )


def _argv(store_path: Path, *mode: str) -> list[str]:
    return [
        "rollback.py",
        "--family",
        FAMILY,
        "--platform",
        PLATFORM,
        "--source-kind",
        SOURCE_KIND,
        "--manifest-store",
        str(store_path),
        *mode,
    ]


def _run(monkeypatch: pytest.MonkeyPatch, store_path: Path, *mode: str) -> None:
    monkeypatch.setattr(sys, "argv", _argv(store_path, *mode))
    rollback.main()


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "manifests"


@pytest.fixture
def store(store_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(store_path)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_list_marks_the_active_generation_and_shows_counts(
    store: GenerationManifestStore,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    first = _publish(store, "v1", _chunks(4, 2, 1))
    second = _publish(store, "v2", _chunks(5, 1, 3))
    capsys.readouterr()

    _run(monkeypatch, store_path, "--list")

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith(second) and "ACTIVE" in lines[0]
    assert lines[0].split()[-4:] == ["symbols=5", "docs=1", "examples=3", "total=9"]
    assert lines[1].startswith(first) and "ACTIVE" not in lines[1]
    assert lines[1].split()[-4:] == ["symbols=4", "docs=2", "examples=1", "total=7"]


def test_list_uses_the_persisted_stats_when_the_payload_has_them(
    store: GenerationManifestStore,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    manifest = build_manifest(
        FAMILY,
        PLATFORM,
        SOURCE_KIND,
        "v1",
        {
            "lexical_index": {
                "documents": {"d": {"chunk_id": "c", "tokens": ["a"], "text": "a"}},
                "doc_freq": {"a": 1},
            },
            "stats": {"symbols": 11, "docs": 22, "examples": 33, "total": 66},
        },
    )
    lease = store.acquire_lease(SCOPE, "w", manifest.generation_id)
    publish(store, SCOPE, None, manifest, lease)

    _run(monkeypatch, store_path, "--list")

    line = capsys.readouterr().out.strip()
    assert line.split()[-4:] == ["symbols=11", "docs=22", "examples=33", "total=66"]


def test_list_writes_nothing(
    store: GenerationManifestStore, store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(store, "v1", _chunks(4, 1, 0))
    _publish(store, "v2", _chunks(4, 1, 0))
    before = _snapshot(store_path)

    _run(monkeypatch, store_path, "--list")

    assert _snapshot(store_path) == before
    assert store.current_fencing_token(SCOPE) == 2, "--list must not take a lease"
    assert not (store_path / SCOPE_DIR_NAME / ".publish.lock").exists()


def test_previous_rolls_back_one_step(
    store: GenerationManifestStore,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    first = _publish(store, "v1", _chunks(4, 1, 0))
    second = _publish(store, "v2", _chunks(4, 1, 0))
    capsys.readouterr()

    _run(monkeypatch, store_path, "--previous")

    assert capsys.readouterr().out.strip() == f"rolled back to {first}"
    assert GenerationManifestStore(store_path).read_active(SCOPE) == first
    entries = [e for e in store.read_publish_log(SCOPE) if e["event"] == "rollback"]
    assert [(e["generation_id"], e["previous_active"]) for e in entries] == [(first, second)]

    _run(monkeypatch, store_path, "--previous")  # the rollback itself is one step: this goes forward again
    assert GenerationManifestStore(store_path).read_active(SCOPE) == second


def test_previous_without_history_raises(
    store: GenerationManifestStore, store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    only = _publish(store, "v1", _chunks(4, 1, 0))
    with pytest.raises(UnknownGenerationError):
        _run(monkeypatch, store_path, "--previous")
    assert store.read_active(SCOPE) == only


def test_target_generation_still_works(
    store: GenerationManifestStore,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    first = _publish(store, "v1", _chunks(4, 1, 0))
    _publish(store, "v2", _chunks(4, 1, 0))
    _publish(store, "v3", _chunks(4, 1, 0))
    capsys.readouterr()

    _run(monkeypatch, store_path, "--target-generation", first)

    assert capsys.readouterr().out.strip() == f"rolled back to {first}"
    assert GenerationManifestStore(store_path).read_active(SCOPE) == first


@pytest.mark.parametrize(
    "mode",
    [
        (),
        ("--list", "--previous"),
        ("--list", "--target-generation", "x"),
        ("--previous", "--target-generation", "x"),
    ],
    ids=["none", "list+previous", "list+target", "previous+target"],
)
def test_exactly_one_mode_is_required(
    store_path: Path, monkeypatch: pytest.MonkeyPatch, mode: tuple[str, ...]
) -> None:
    monkeypatch.setattr(sys, "argv", _argv(store_path, *mode))
    with pytest.raises(SystemExit) as raised:
        rollback.main()
    assert raised.value.code == 2


def test_foreign_prefix_target_is_refused_and_the_pointer_is_unchanged(
    store: GenerationManifestStore, store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(store, "v1", _chunks(4, 1, 0))
    active = _publish(store, "v2", _chunks(4, 1, 0))
    foreign_path = store.write_generation(
        build_manifest("email", PLATFORM, SOURCE_KIND, "v2", {"lexical_index": {"documents": {}}})
    )
    shutil.copyfile(foreign_path, store_path / SCOPE_DIR_NAME / "generation__v3.json")
    foreign_id = f"email::{PLATFORM}::{SOURCE_KIND}::v3"
    assert store.generation_exists(SCOPE, foreign_id) is True

    with pytest.raises(UnknownGenerationError):
        _run(monkeypatch, store_path, "--target-generation", foreign_id)

    assert GenerationManifestStore(store_path).read_active(SCOPE) == active


def test_malformed_target_is_refused_and_the_pointer_is_unchanged(
    store: GenerationManifestStore, store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(store, "v1", _chunks(4, 1, 0))
    active = _publish(store, "v2", _chunks(4, 1, 0))
    broken = build_manifest(
        FAMILY,
        PLATFORM,
        SOURCE_KIND,
        "v0",
        {
            "lexical_index": {
                "documents": {"d": {"chunk_id": "c", "tokens": ["a"], "text": ""}},
                "doc_freq": {},
            }
        },
    )
    store.write_generation(broken)

    with pytest.raises(ManifestValidationError):
        _run(monkeypatch, store_path, "--target-generation", broken.generation_id)

    assert GenerationManifestStore(store_path).read_active(SCOPE) == active


def test_deployment_md_documents_rollback() -> None:
    text = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    heading = "## Rolling back a generation"
    assert text.count(heading) == 1
    assert text.index(heading) < text.index("## Accepting an intentional shrink")
    assert text.index(heading) < text.index("## Troubleshooting")
    section = text[text.index(heading) : text.index("## Accepting an intentional shrink")]
    for needle in (
        "--list",
        "--previous",
        "--target-generation",
        "publish_log.jsonl",
        "nodeName",
        "runAsUser: 10001",
    ):
        assert needle in section, f"the rollback section lacks {needle!r}"
    assert "install_pilots.py" not in section
