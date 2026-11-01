"""G3/TC-651 (REQ-G3-013, workstream I): ``FreshnessReport.documentation_content_type_counts``
maps EVERY one of ``search_docs.CONTENT_TYPES`` to its real integer documentation-chunk count for
the active generation - including an explicit ``0`` for an empty category, never an absent key.

15 of 17 round-2 issues share this exact shape: a documentation category (almost always ``faq``,
sometimes ``troubleshooting``) is completely empty for a pilot, with nothing telling a caller
that before it spends a query discovering the miss (e.g. ``PDF-NET-004``, ``docs/round2/issues.json``).

Offline, same pattern ``test_freshness_provenance.py`` already established: synthetic generations
are published through the real ``publish_generation``; one real-data test copies a committed demo
generation (pdf/net, confirmed below to be genuinely empty of faq chunks) to reproduce
``PDF-NET-004`` itself and prove the fix closes it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, chunk_kind
from foss_mcp.mcp.tools.report_index_freshness import FreshnessReport, report_index_freshness
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, classify_content_type
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

SCOPE = Scope(family="pdf", platform="net")
REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# Two distinct, deliberately fake-looking 40-character commits - only their length matters
# (``_abbreviation_warnings`` treats anything shorter as abbreviated); this file never asserts on
# warnings, so any unique strings would do, but full-length ones avoid an unrelated warning noise.
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _symbol(name: str, commit: str) -> str:
    return f"FQN: Aspose.Demo.{name}\nKind: class_declaration\nSummary of {name}.\nSource-Commit: {commit}"


def _example(name: str, commit: str) -> str:
    return f"FQN: Example: {name}\nAn example named {name}.\nSource-Commit: {commit}"


def _doc(title: str, body_first_line: str, commit: str) -> str:
    """A documentation chunk classified by its own title/first-body-line, TC-517's rule - never
    the fragile keyword-hint fallback, so each category below is deterministic."""
    return f"FQN: Doc: {title}\n{body_first_line}\nSource-Commit: {commit}"


# One deterministic chunk per real CONTENT_TYPES category, classified by title (or, for faq, by
# the FAQ: body-line rule) - confirmed against the real classify_content_type below, never assumed.
GETTING_STARTED_DOC = _doc("Quick Start Guide", "How to install the library.", COMMIT_A)
DEVELOPER_GUIDE_DOC = _doc("API Reference", "Reference content about available classes.", COMMIT_A)
TROUBLESHOOTING_DOC = _doc("Troubleshooting", "Known issues and common errors.", COMMIT_A)
FAQ_DOC = _doc("Frequently Asked Questions", "FAQ: Is this free?", COMMIT_A)

for _text, _expected in (
    (GETTING_STARTED_DOC, "getting_started"),
    (DEVELOPER_GUIDE_DOC, "developer_guide"),
    (TROUBLESHOOTING_DOC, "troubleshooting"),
    (FAQ_DOC, "faq"),
):
    assert chunk_kind(_text) == DOCUMENTATION, f"fixture chunk not classified as documentation: {_text!r}"
    assert classify_content_type(_text) == _expected, f"fixture chunk misclassified: {_text!r}"


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts* (one chunk per text) - the same
    helper (duplicated locally, not shared, per this suite's own convention) ``test_freshness_provenance.py``
    uses to build synthetic generations through the real indexing pipeline, never a hand-built
    manifest payload."""
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="demo/repo", commit=COMMIT_A),
        evidence_refs=(),
        title="synthetic",
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


def _report(store: GenerationManifestStore, current: str | None = None) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", current)


def test_all_four_categories_report_their_real_per_category_counts(tmp_path: Path) -> None:
    store = _publish(
        tmp_path / "m",
        [
            GETTING_STARTED_DOC,
            DEVELOPER_GUIDE_DOC,
            DEVELOPER_GUIDE_DOC.replace("API Reference", "API Reference 2"),
            TROUBLESHOOTING_DOC,
            FAQ_DOC,
        ],
    )
    report = _report(store)
    assert report.documentation_content_type_counts == {
        "getting_started": 1,
        "developer_guide": 2,
        "troubleshooting": 1,
        "faq": 1,
    }


def test_empty_category_reports_an_explicit_zero_not_an_absent_key(tmp_path: Path) -> None:
    # No faq chunk anywhere in this generation - the real symptom of all 15 round-2 issues.
    store = _publish(tmp_path / "m", [GETTING_STARTED_DOC, DEVELOPER_GUIDE_DOC, TROUBLESHOOTING_DOC])
    report = _report(store)
    counts = report.documentation_content_type_counts
    assert "faq" in counts
    assert counts["faq"] == 0
    assert counts == {"getting_started": 1, "developer_guide": 1, "troubleshooting": 1, "faq": 0}


def test_generation_with_no_documentation_chunks_at_all_reports_every_category_zero(
    tmp_path: Path,
) -> None:
    # Only symbol and example chunks - no documentation chunk at all. Every one of the 4 real
    # categories must still appear, all zero, never an empty dict.
    store = _publish(tmp_path / "m", [_symbol("Alpha", COMMIT_A), _example("sign a file", COMMIT_A)])
    report = _report(store)
    assert report.documentation_content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_never_published_report_also_carries_the_field_all_zero(tmp_path: Path) -> None:
    # The early "never published" return path (no generation at all) - no chunks of any kind, so
    # no documentation chunks either; the field must still be present and zero-filled.
    store = GenerationManifestStore(tmp_path / "m")
    report = _report(store)
    assert report.stale is True
    assert report.reason == "never published"
    assert report.documentation_content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_counts_combine_across_multiple_source_commits(tmp_path: Path) -> None:
    # A mixed-commit generation (TC-383's own scenario): the per-category tally is across the
    # WHOLE generation, not scoped to one commit - a caller asking "is faq empty for this pilot"
    # cares about the generation as a whole.
    store = _publish(
        tmp_path / "m",
        [
            _symbol("Alpha", COMMIT_A),
            GETTING_STARTED_DOC,
            _doc("Troubleshooting", "Known issues.", COMMIT_B),
            _doc("Frequently Asked Questions", "FAQ: Is this free?", COMMIT_B),
        ],
    )
    report = _report(store)
    assert len({entry.commit for entry in report.source_commits}) == 2
    assert report.documentation_content_type_counts == {
        "getting_started": 1,
        "developer_guide": 0,
        "troubleshooting": 1,
        "faq": 1,
    }


def test_counts_match_an_independent_manual_tally_via_the_real_classify_content_type(
    tmp_path: Path,
) -> None:
    # Never reimplements classification: tallies the same fixture chunks independently (via the
    # real, imported search_docs.classify_content_type) and checks the report agrees exactly.
    texts = [
        GETTING_STARTED_DOC,
        DEVELOPER_GUIDE_DOC,
        TROUBLESHOOTING_DOC,
        FAQ_DOC,
        _symbol("Beta", COMMIT_A),
    ]
    store = _publish(tmp_path / "m", texts)
    report = _report(store)
    expected = dict.fromkeys(CONTENT_TYPES, 0)
    for text in texts:
        if chunk_kind(text) == DOCUMENTATION:
            expected[classify_content_type(text)] += 1
    assert report.documentation_content_type_counts == expected


def test_keys_exactly_equal_the_real_search_docs_content_types(tmp_path: Path) -> None:
    # Compared against the real imported constant, never a tuple hardcoded in this test file, so
    # the two can never silently drift apart.
    store = _publish(tmp_path / "m", [FAQ_DOC])
    report = _report(store)
    assert set(report.documentation_content_type_counts) == set(CONTENT_TYPES)
    never_published = _report(GenerationManifestStore(tmp_path / "none"))
    assert set(never_published.documentation_content_type_counts) == set(CONTENT_TYPES)


def test_default_factory_never_shares_a_mutable_dict_across_instances() -> None:
    # The dataclass default is built by a factory precisely so two reports never alias the same
    # dict - mutating one's field must never leak into another's.
    first = FreshnessReport(SCOPE, "self_extracted", None, None, None, True, "a")
    second = FreshnessReport(SCOPE, "self_extracted", None, None, None, True, "b")
    assert first.documentation_content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)
    first.documentation_content_type_counts["faq"] = 99
    assert second.documentation_content_type_counts["faq"] == 0


def test_real_pdf_net_pilot_reports_faq_explicitly_zero_closing_pdf_net_004(tmp_path: Path) -> None:
    """End-to-end reproduction of ``PDF-NET-004`` (confirmed in the 15-id list in this card's own
    `plans/TC-651.yaml`): the real baked pdf/net generation genuinely has 0 faq chunks. Before
    this card, nothing told a caller that up front; after it, ``report_index_freshness`` says so
    directly."""
    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / "pdf__net__self_extracted", manifests / "pdf__net__self_extracted")
    store = GenerationManifestStore(manifests)

    report = report_index_freshness(store, SCOPE, "self_extracted", None)

    counts = report.documentation_content_type_counts
    assert set(counts) == set(CONTENT_TYPES)
    assert counts["faq"] == 0
    # Sanity: this pilot is not simply empty of documentation altogether - only faq is empty,
    # which is exactly the "no indication" gap PDF-NET-004 reports.
    assert sum(counts.values()) > 0
    assert counts["getting_started"] > 0
    assert counts["developer_guide"] > 0
    assert counts["troubleshooting"] > 0
