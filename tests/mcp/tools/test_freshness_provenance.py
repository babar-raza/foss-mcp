"""G3/TC-383: report_index_freshness lists EVERY source commit a generation was built from.

Offline. Synthetic generations are published through the real ``publish_generation`` (the same
helper the consumer-tools tests use); the real-data tests copy committed demo generations:
pdf/typescript genuinely mixes two commits (300 API-symbol chunks at one commit, the documentation
and example chunks at the library's pinned commit) and pdf/net records exactly one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.report_index_freshness import (
    CommitCount,
    FreshnessReport,
    _commit_matches,
    report_index_freshness,
)
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

SCOPE = Scope(family="pdf", platform="net")
REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

SYMBOL_COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"
PINNED_COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"


def _symbol(name: str, commit: str | None, extra: str = "") -> str:
    lines = [f"FQN: Aspose.Pdf.{name}", "Kind: class_declaration", f"Summary of {name}."]
    if extra:
        lines.append(extra)
    if commit is not None:
        lines.append(f"Source-Commit: {commit}")
    return "\n".join(lines)


def _example(name: str, commit: str | None) -> str:
    lines = [f"FQN: Example: {name}", f"A verified example named {name}."]
    if commit is not None:
        lines.append(f"Source-Commit: {commit}")
    return "\n".join(lines)


def _doc(name: str, commit: str | None) -> str:
    lines = [f"FQN: Doc: {name}", f"Documentation page named {name}."]
    if commit is not None:
        lines.append(f"Source-Commit: {commit}")
    return "\n".join(lines)


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts* (one chunk per text)."""
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit=SYMBOL_COMMIT),
        evidence_refs=(),
        title="pdf/net synthetic",
        body=body,
    )
    chunks = chunk_document(doc)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


def _mixed_texts() -> list[str]:
    """Three symbol chunks at SYMBOL_COMMIT, one example and two doc chunks at PINNED_COMMIT."""
    return [
        _symbol("Alpha", SYMBOL_COMMIT),
        _symbol("Beta", SYMBOL_COMMIT),
        _symbol("Gamma", SYMBOL_COMMIT),
        _example("sign a file", PINNED_COMMIT),
        _doc("Install", PINNED_COMMIT),
        _doc("Limits", PINNED_COMMIT),
    ]


def _report(store: GenerationManifestStore, current: str | None, **kwargs: object) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", current, **kwargs)  # type: ignore[arg-type]


# --- one commit -----------------------------------------------------------------------------


def test_single_commit_is_fresh_for_the_full_sha(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])
    report = _report(store, SYMBOL_COMMIT)
    assert report.stale is False
    assert report.reason == "index matches the current source commit"
    assert report.indexed_source_commit == SYMBOL_COMMIT
    assert report.source_commits == (CommitCount(SYMBOL_COMMIT, 2, symbol_chunks=2),)
    assert report.chunks_without_commit == 0


def test_single_commit_is_stale_for_a_different_commit_with_todays_reason(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    other = "a" * 40
    report = _report(store, other)
    assert report.stale is True
    assert report.reason == f"indexed {SYMBOL_COMMIT}, current source is {other}"


def test_a_seven_character_short_sha_matches(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    report = _report(store, SYMBOL_COMMIT[:7])
    assert report.stale is False
    assert report.indexed_source_commit == SYMBOL_COMMIT


def test_uppercase_and_padded_values_match(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    assert _report(store, f"  {SYMBOL_COMMIT.upper()}  ").stale is False
    assert _report(store, f" {SYMBOL_COMMIT[:10].upper()}\n").stale is False


def test_a_six_character_prefix_does_not_match(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    report = _report(store, SYMBOL_COMMIT[:6])
    assert report.stale is True
    assert "current source is" in report.reason


def test_a_non_prefix_of_seven_characters_does_not_match(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    assert _report(store, "0a36383").stale is True


def test_unknown_current_commit_is_stale_with_todays_reason(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    report = _report(store, None)
    assert report.stale is True
    assert report.reason == "current source commit is unknown; staleness cannot be ruled out"


# --- _commit_matches ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("indexed", "current", "expected"),
    [
        (SYMBOL_COMMIT, SYMBOL_COMMIT, True),
        (SYMBOL_COMMIT, SYMBOL_COMMIT.upper(), True),
        (SYMBOL_COMMIT, f"  {SYMBOL_COMMIT[:7]} ", True),
        (SYMBOL_COMMIT, SYMBOL_COMMIT[:6], False),
        (SYMBOL_COMMIT, "", False),
        (SYMBOL_COMMIT, PINNED_COMMIT, False),
        (SYMBOL_COMMIT[:8], SYMBOL_COMMIT, False),
    ],
)
def test_commit_matches(indexed: str, current: str, expected: bool) -> None:
    assert _commit_matches(indexed, current) is expected


# --- several commits ------------------------------------------------------------------------


def test_two_commits_split_by_kind_with_exact_counts(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", _mixed_texts())
    report = _report(store, PINNED_COMMIT)
    assert report.source_commits == (
        CommitCount(SYMBOL_COMMIT, 3, symbol_chunks=3, example_chunks=0, documentation_chunks=0),
        CommitCount(PINNED_COMMIT, 3, symbol_chunks=0, example_chunks=1, documentation_chunks=2),
    )
    assert report.indexed_source_commit == SYMBOL_COMMIT
    assert report.chunks_without_commit == 0


def test_source_commits_are_ordered_by_chunks_descending_then_commit(tmp_path: Path) -> None:
    texts = [_doc("One", PINNED_COMMIT), _doc("Two", PINNED_COMMIT), _symbol("Alpha", SYMBOL_COMMIT)] + [
        _symbol("Zeta", "f" * 40),
        _symbol("Eta", "e" * 40),
    ]
    report = _report(_publish(tmp_path / "m", texts), None)
    assert [(entry.commit, entry.chunks) for entry in report.source_commits] == [
        (PINNED_COMMIT, 2),
        (SYMBOL_COMMIT, 1),
        ("e" * 40, 1),
        ("f" * 40, 1),
    ]


def test_caller_matching_only_the_documentation_commit_is_stale_and_the_reason_says_so(
    tmp_path: Path,
) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), PINNED_COMMIT)
    assert report.stale is True
    assert report.reason.startswith("index is mixed: ")
    assert f"API symbols from {SYMBOL_COMMIT[:12]} (3 chunks" in report.reason
    assert f"documentation and examples from {PINNED_COMMIT[:12]} (3 chunks" in report.reason
    assert f"current {PINNED_COMMIT[:12]} matches documentation and examples only" in report.reason


def test_caller_matching_only_the_symbol_commit_is_stale_and_the_reason_says_so(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), SYMBOL_COMMIT[:9])
    assert report.stale is True
    assert f"current {SYMBOL_COMMIT[:9]} matches API symbols only" in report.reason


def test_caller_matching_neither_commit_is_stale(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), "c" * 40)
    assert report.stale is True
    assert "matches none of them" in report.reason


def test_unknown_current_commit_on_a_mixed_index_is_stale_and_still_lists_the_commits(
    tmp_path: Path,
) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), None)
    assert report.stale is True
    assert "index is mixed" in report.reason
    assert "current source commit is unknown" in report.reason
    assert len(report.source_commits) == 2


def test_indexed_source_commit_is_the_symbol_commit_even_when_documentation_has_more_chunks(
    tmp_path: Path,
) -> None:
    texts = [_symbol("Alpha", SYMBOL_COMMIT)] + [_doc(f"Page {i}", PINNED_COMMIT) for i in range(5)]
    report = _report(_publish(tmp_path / "m", texts), None)
    assert report.indexed_source_commit == SYMBOL_COMMIT


def test_indexed_source_commit_without_symbols_is_the_commit_with_most_chunks(tmp_path: Path) -> None:
    texts = [_doc("A", SYMBOL_COMMIT), _doc("B", PINNED_COMMIT), _example("C", PINNED_COMMIT)]
    report = _report(_publish(tmp_path / "m", texts), None)
    assert report.indexed_source_commit == PINNED_COMMIT


def test_indexed_source_commit_ties_break_to_the_smaller_commit_string(tmp_path: Path) -> None:
    symbols = [_symbol("Alpha", "b" * 40), _symbol("Beta", "a" * 40)]
    assert _report(_publish(tmp_path / "s", symbols), None).indexed_source_commit == "a" * 40
    docs = [_doc("Alpha", "d" * 40), _doc("Beta", "c" * 40)]
    assert _report(_publish(tmp_path / "d", docs), None).indexed_source_commit == "c" * 40


def test_report_is_independent_of_chunk_insertion_order(tmp_path: Path) -> None:
    texts = _mixed_texts()
    forward = _report(_publish(tmp_path / "forward", texts), PINNED_COMMIT)
    backward = _report(_publish(tmp_path / "backward", list(reversed(texts))), PINNED_COMMIT)
    assert forward.indexed_source_commit == backward.indexed_source_commit == SYMBOL_COMMIT
    assert forward.source_commits == backward.source_commits
    assert forward.reason == backward.reason
    # Tie between two symbol commits is also order independent.
    tied = [_symbol("Alpha", "b" * 40), _symbol("Beta", "a" * 40)]
    first = _report(_publish(tmp_path / "t1", tied), None)
    second = _report(_publish(tmp_path / "t2", list(reversed(tied))), None)
    assert first.indexed_source_commit == second.indexed_source_commit == "a" * 40


# --- chunks without a commit ----------------------------------------------------------------


def test_chunks_without_a_source_commit_line_are_counted_separately(tmp_path: Path) -> None:
    texts = [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", None), _doc("Page", None)]
    report = _report(_publish(tmp_path / "m", texts), SYMBOL_COMMIT)
    assert report.chunks_without_commit == 2
    assert report.source_commits == (CommitCount(SYMBOL_COMMIT, 1, symbol_chunks=1),)
    assert report.stale is False


def test_a_generation_with_no_commit_anywhere_is_unknown_and_stale(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", [_symbol("Alpha", None), _symbol("Beta", None)]), SYMBOL_COMMIT)
    assert report.stale is True
    assert report.indexed_source_commit is None
    assert report.source_commits == ()
    assert report.chunks_without_commit == 2
    assert report.reason == "indexed generation does not record its source commit"


def test_a_chunk_without_an_fqn_line_is_counted_for_its_commit_but_not_as_any_kind(tmp_path: Path) -> None:
    texts = [_symbol("Alpha", SYMBOL_COMMIT), f"Free text paragraph.\nSource-Commit: {PINNED_COMMIT}"]
    report = _report(_publish(tmp_path / "m", texts), None)
    assert report.source_commits == (
        CommitCount(SYMBOL_COMMIT, 1, symbol_chunks=1),
        CommitCount(PINNED_COMMIT, 1),
    )
    assert "chunks of unknown kind from" in report.reason


# --- the pinned library commit --------------------------------------------------------------


def test_pinned_commit_matching_the_symbol_commit(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _doc("Page", SYMBOL_COMMIT)])
    report = _report(store, SYMBOL_COMMIT, pinned_library_commit=SYMBOL_COMMIT[:8])
    assert report.pinned_library_commit == SYMBOL_COMMIT[:8]
    assert report.pinned_matches_symbols is True
    assert report.stale is False
    assert "pinned library commit" not in report.reason


def test_pinned_commit_not_matching_the_symbol_commit_adds_one_sentence(tmp_path: Path) -> None:
    report = _report(
        _publish(tmp_path / "m", _mixed_texts()), PINNED_COMMIT, pinned_library_commit=PINNED_COMMIT
    )
    assert report.pinned_library_commit == PINNED_COMMIT
    assert report.pinned_matches_symbols is False
    assert report.stale is True
    assert report.reason.count("were not extracted at the pinned library commit") == 1
    assert f"{PINNED_COMMIT[:12]} (they come from {SYMBOL_COMMIT[:12]})" in report.reason


def test_pinned_mismatch_sentence_is_added_to_a_single_commit_reason(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT)])
    report = _report(store, SYMBOL_COMMIT, pinned_library_commit=PINNED_COMMIT)
    assert report.stale is False
    assert report.reason.startswith("index matches the current source commit")
    assert "not extracted at the pinned library commit" in report.reason


def test_pinned_commit_without_symbol_chunks_is_unknown(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_doc("A", PINNED_COMMIT), _example("B", PINNED_COMMIT)])
    report = _report(store, PINNED_COMMIT, pinned_library_commit=SYMBOL_COMMIT)
    assert report.pinned_library_commit == SYMBOL_COMMIT
    assert report.pinned_matches_symbols is None
    assert "pinned library commit" not in report.reason


def test_without_a_pinned_commit_the_pinned_fields_stay_none(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), PINNED_COMMIT)
    assert report.pinned_library_commit is None
    assert report.pinned_matches_symbols is None


# --- API coverage ---------------------------------------------------------------------------


def test_api_coverage_is_parsed_and_noted_when_the_index_holds_part_of_the_surface(tmp_path: Path) -> None:
    texts = [
        _symbol("Alpha", SYMBOL_COMMIT, extra="API-Coverage: 300 of 1234 types"),
        _symbol("Beta", SYMBOL_COMMIT),
    ]
    report = _report(_publish(tmp_path / "m", texts), SYMBOL_COMMIT)
    assert report.indexed_types == 300
    assert report.total_types == 1234
    assert report.coverage_note == (
        "the API index holds 300 of 1234 types; a type missing from it may still exist upstream"
    )


def test_api_coverage_with_everything_indexed_has_no_note(tmp_path: Path) -> None:
    texts = [_symbol("Alpha", SYMBOL_COMMIT, extra="API-Coverage: 42 of 42 types")]
    report = _report(_publish(tmp_path / "m", texts), SYMBOL_COMMIT)
    assert (report.indexed_types, report.total_types) == (42, 42)
    assert report.coverage_note is None


def test_absent_api_coverage_is_unknown_not_guessed(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", _mixed_texts()), SYMBOL_COMMIT)
    assert report.indexed_types is None
    assert report.total_types is None
    assert report.coverage_note is None


def test_api_coverage_on_a_documentation_chunk_is_ignored(tmp_path: Path) -> None:
    doc = _doc("Page", PINNED_COMMIT) + "\nAPI-Coverage: 1 of 2 types"
    report = _report(_publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), doc]), None)
    assert report.indexed_types is None
    assert report.coverage_note is None


# --- real data ------------------------------------------------------------------------------


# The refreshed (2026-10-14) committed pilots: pdf/net was rebuilt from ONE source commit, and pdf/typescript
# is the committed generation that still genuinely mixes two (API symbols at one commit, documentation and
# examples at the library's pinned commit). Both are read from the committed generations, never retyped.
PDF_NET_COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"
TS_SYMBOL_COMMIT = "c672b3918318ccd0c13bc9dc619620cac2674c9e"
TS_PINNED_COMMIT = "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"


def _real_store(tmp_path: Path, directory: str) -> GenerationManifestStore:
    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / directory, manifests / directory)
    return GenerationManifestStore(manifests)


def test_real_pdf_net_generation_reports_its_one_commit(tmp_path: Path) -> None:
    store = _real_store(tmp_path, "pdf__net__self_extracted")

    report = _report(store, PDF_NET_COMMIT)

    commits = {entry.commit: entry for entry in report.source_commits}
    assert set(commits) == {PDF_NET_COMMIT}
    assert commits[PDF_NET_COMMIT].symbol_chunks >= 300
    assert commits[PDF_NET_COMMIT].documentation_chunks + commits[PDF_NET_COMMIT].example_chunks >= 1
    assert report.indexed_source_commit == PDF_NET_COMMIT
    assert report.chunks_without_commit == 0
    assert report.stale is False
    assert "index is mixed" not in report.reason
    assert _report(store, SYMBOL_COMMIT).stale is True


def test_real_pdf_typescript_generation_reports_both_commits(tmp_path: Path) -> None:
    store = _real_store(tmp_path, "pdf__typescript__self_extracted")
    ts_scope = Scope(family="pdf", platform="typescript")

    def report(current: str | None, pinned_library_commit: str | None = None) -> FreshnessReport:
        return report_index_freshness(
            store, ts_scope, "self_extracted", current, pinned_library_commit=pinned_library_commit
        )

    by_symbols = report(TS_SYMBOL_COMMIT)
    by_pinned = report(TS_PINNED_COMMIT)

    commits = {entry.commit: entry for entry in by_symbols.source_commits}
    assert set(commits) == {TS_SYMBOL_COMMIT, TS_PINNED_COMMIT}
    assert commits[TS_SYMBOL_COMMIT].symbol_chunks >= 300
    assert commits[TS_SYMBOL_COMMIT].documentation_chunks == 0
    assert commits[TS_PINNED_COMMIT].symbol_chunks == 0
    assert commits[TS_PINNED_COMMIT].documentation_chunks + commits[TS_PINNED_COMMIT].example_chunks >= 1
    assert by_symbols.indexed_source_commit == TS_SYMBOL_COMMIT
    for item in (by_symbols, by_pinned):
        assert item.stale is True
        assert "index is mixed" in item.reason
        assert TS_SYMBOL_COMMIT[:12] in item.reason
        assert TS_PINNED_COMMIT[:12] in item.reason
    assert f"current {TS_PINNED_COMMIT[:12]} matches documentation and examples only" in by_pinned.reason
    assert f"current {TS_SYMBOL_COMMIT[:12]} matches API symbols only" in by_symbols.reason

    pinned = report(TS_PINNED_COMMIT, pinned_library_commit=TS_PINNED_COMMIT)
    assert pinned.pinned_matches_symbols is False
