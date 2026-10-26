"""G3/TC-465 (REQ-G3-007): a "verified example" is only ever something the verifier really passed.

``find_examples`` used to present as a verified snippet any chunk whose text merely contained
``Example:``. ``is_verified_example_text`` is now the ONE definition every branch (exact, lexical,
vector) uses: the FQN line starts with ``Example: ``, some line is exactly ``Kind: verified_example``
(the verifier's own line), some line is exactly ``Example:``, and an authored example also needs
``Verification: passed``.

Unit tests pin the predicate. The REAL-DATA tests pin the committed 3d/java ``DracoFormat`` API-symbol
chunk (its Javadoc says ``Example:``; it is in both the lexical and the vector index) and the equality
between the predicate and the verifier's own line across all 40 committed pilot scope directories.
Synthetic generations pin ``provenance`` and the authored-without-``Verification: passed`` rejection
in every branch.

Offline and deterministic: the gateway embedding provider is always monkeypatched and never
constructed for real; no clock, no network; nothing is written inside the repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.indexing.vector_index_writer import query_vector_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    MATCH_CONFIDENCE_CONFIDENT,
    MATCH_CONFIDENCE_LOOSE,
    ExampleMatch,
    NoExampleFound,
    _is_generic_example_title,
    find_examples,
    is_verified_example_text,
)
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
VALUES_YAML = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

THREED_JAVA = Scope(family="3d", platform="java")
DRACO_FQN = "com.aspose.threed.DracoFormat"
PDF_NET = Scope(family="pdf", platform="net")

NO_LEXICAL_HIT_QUERY = "zyzzyx quproc negfoo"


def _pilots() -> list[tuple[str, str, str]]:
    """The (family, platform, sourceKind) of every pilot in the helm values - never retyped."""
    values = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))
    return [
        (pilot["family"], pilot["platform"], pilot["sourceKind"]) for pilot in values["ingestion"]["pilots"]
    ]


PILOTS = _pilots()


class _FakeQueryEmbeddingProvider:
    """Stands in for ``GatewayEmbeddingProvider()``: every text embeds to the one given vector."""

    def __init__(self, vector: list[float] | tuple[float, ...]) -> None:
        self._vector = vector

    def embed(self, texts):
        return [self._vector for _ in texts]


class _FixedVectorEmbeddingProvider:
    """A hand-constructed provider for synthetic generations: a pre-chosen vector per exact text."""

    dimension = 2

    def __init__(self, vectors_by_text: dict[str, tuple[float, float]]) -> None:
        self._vectors_by_text = vectors_by_text

    def embed(self, texts):
        return [self._vectors_by_text[text] for text in texts]


def _use_query_vector(monkeypatch: pytest.MonkeyPatch, vector) -> None:
    monkeypatch.setattr(
        "foss_mcp.mcp.tools.find_examples.GatewayEmbeddingProvider",
        lambda: _FakeQueryEmbeddingProvider(vector),
    )


@pytest.fixture(autouse=True)
def _never_construct_the_real_gateway_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: constructing the real provider fails (``find_examples`` degrades to a miss). A test
    that needs a vector stage overrides this with ``_use_query_vector``."""

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("the real gateway embedding provider must never be constructed in this module")

    monkeypatch.setattr("foss_mcp.mcp.tools.find_examples.GatewayEmbeddingProvider", _refuse)


# ---- (a) unit tests: the predicate ------------------------------------------------------------

README_BODY = (
    "FQN: Example: Merge Two Documents\n"
    "Kind: verified_example\n"
    "Merges two documents into one.\n\n"
    "Example:\ndocument.Merge(other)\n"
    "Source-Commit: abc123"
)

AUTHORED_PASSED_BODY = (
    "FQN: Example: Rotate A Page\n"
    "Kind: verified_example\n"
    "Provenance: authored\n"
    "Verification: passed\n"
    "Rotates one page.\n\n"
    "Example:\npage.Rotate(90)"
)

AUTHORED_UNPASSED_BODY = (
    "FQN: Example: Rotate A Page\nKind: verified_example\nProvenance: authored\nRotates one page.\n\n"
    "Example:\npage.Rotate(90)"
)


def test_a_real_readme_example_body_passes() -> None:
    assert is_verified_example_text(README_BODY) is True


def test_an_authored_body_with_verification_passed_passes() -> None:
    assert is_verified_example_text(AUTHORED_PASSED_BODY) is True


def test_an_authored_body_without_verification_passed_fails() -> None:
    assert is_verified_example_text(AUTHORED_UNPASSED_BODY) is False


def test_a_verification_line_that_is_not_passed_does_not_count() -> None:
    body = AUTHORED_UNPASSED_BODY.replace(
        "Provenance: authored\n", "Provenance: authored\nVerification: failed\n"
    )

    assert is_verified_example_text(body) is False


def test_a_symbol_chunk_whose_javadoc_contains_example_fails() -> None:
    # Even with a line that is exactly "Example:", a symbol chunk has no verifier line.
    text = (
        "FQN: com.aspose.threed.DracoFormat\nKind: class_declaration\nGoogle Draco format\n\n"
        "Example:\nMesh mesh = (new Sphere()).toMesh();\n"
    )

    assert is_verified_example_text(text) is False


def test_a_documentation_chunk_that_mentions_verified_example_fails() -> None:
    text = (
        "FQN: Doc: How examples are verified\n"
        "Every verified example is compiled. The line Kind: verified_example marks one.\n\n"
        "Example:\nfoo()\n"
    )

    assert is_verified_example_text(text) is False


def test_a_fqn_example_chunk_without_the_kind_line_fails() -> None:
    text = "FQN: Example: Merge Two Documents\nMerges two documents.\n\nExample:\ndocument.Merge(other)\n"

    assert is_verified_example_text(text) is False


def test_a_chunk_without_an_fqn_line_fails() -> None:
    assert is_verified_example_text("Kind: verified_example\n\nExample:\nfoo()\n") is False


def test_a_verified_kind_without_the_example_marker_line_fails() -> None:
    text = "FQN: Example: Demo\nKind: verified_example\nSee Example: inline only.\n"

    assert is_verified_example_text(text) is False


def test_a_kind_line_with_extra_text_is_not_the_kind_line() -> None:
    text = "FQN: Example: Demo\nKind: verified_example_draft\n\nExample:\nfoo()\n"

    assert is_verified_example_text(text) is False


def test_crlf_line_endings_do_not_defeat_the_predicate() -> None:
    assert is_verified_example_text(README_BODY.replace("\n", "\r\n")) is True


# ---- (b) real data: 3d/java DracoFormat -------------------------------------------------------


def _copied_store(tmp_path: Path, scope: Scope) -> GenerationManifestStore:
    """A store over a COPY of the committed demo-manifest scope directory (the repo is never written)."""
    directory = f"{scope.family}__{scope.platform}__self_extracted"
    shutil.copytree(DEMO_MANIFESTS / directory, tmp_path / directory)
    return GenerationManifestStore(tmp_path)


def _generation_payload(store: GenerationManifestStore, scope: Scope) -> dict:
    key = scope_key(scope, SOURCE_KIND)
    active = store.read_active(key)
    assert active is not None
    return store.read_generation(key, active).payload


def _draco_point(payload: dict) -> dict:
    points = [point for point in payload["vector_index"]["points"] if f"FQN: {DRACO_FQN}\n" in point["text"]]
    assert len(points) == 1
    return points[0]


def test_the_draco_javadoc_chunk_is_the_defect_shaped_case(tmp_path: Path) -> None:
    payload = _generation_payload(_copied_store(tmp_path, THREED_JAVA), THREED_JAVA)
    point = _draco_point(payload)
    lines = point["text"].splitlines()

    # Its Javadoc says "Example:" (the old substring rule accepted any such chunk) but it is not a
    # verified example: the verifier's own Kind line is absent and its Kind is a class declaration.
    assert "Example:" in point["text"]
    assert "Kind: class_declaration" in lines
    assert "Kind: verified_example" not in lines
    assert is_verified_example_text(point["text"]) is False
    # And it is in the lexical index too, under the same chunk id.
    lexical_chunk_ids = {document["chunk_id"] for document in payload["lexical_index"]["documents"].values()}
    assert point["chunk_id"] in lexical_chunk_ids


def _assert_matches_are_verified_and_never_the_draco_chunk(
    result: object, payload: dict, point: dict
) -> None:
    """Whatever ``find_examples`` returns, no match is the DracoFormat Javadoc chunk and every match is a
    chunk that passes the verified-example check. 3d/java now holds genuine verified README examples, so
    a list of matches is legitimate; a NoExampleFound is legitimate too."""
    if isinstance(result, NoExampleFound):
        return
    assert isinstance(result, list) and result, result
    verified_fqns = {
        document["text"].splitlines()[0].removeprefix("FQN: ")
        for document in payload["lexical_index"]["documents"].values()
        if is_verified_example_text(document["text"])
    }
    for match in result:
        assert isinstance(match, ExampleMatch)
        assert match.fqn != DRACO_FQN
        assert match.fqn.startswith("Example: ")
        assert match.fqn in verified_fqns, f"{match.fqn!r} is not a verified-example chunk"
        assert match.fqn != point["text"].splitlines()[0].removeprefix("FQN: ")


def test_find_examples_by_the_draco_fqn_is_not_an_example_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _copied_store(tmp_path, THREED_JAVA)
    payload = _generation_payload(store, THREED_JAVA)
    point = _draco_point(payload)
    _use_query_vector(monkeypatch, point["vector"])

    result = find_examples(store, THREED_JAVA, DRACO_FQN)

    _assert_matches_are_verified_and_never_the_draco_chunk(result, payload, point)


def test_the_vector_path_no_longer_serves_the_draco_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _copied_store(tmp_path, THREED_JAVA)
    payload = _generation_payload(store, THREED_JAVA)
    point = _draco_point(payload)
    # The query embeds to the point's own stored vector: cosine 1.0, far above any threshold.
    ranked = dict(query_vector_index_scored(payload["vector_index"], point["vector"], top_k=5))
    assert ranked[point["point_id"]] == pytest.approx(1.0)
    _use_query_vector(monkeypatch, point["vector"])
    # ... and the query has no lexical hit, so the vector stage is the only thing that could answer.
    assert find_examples_module.query_terms(NO_LEXICAL_HIT_QUERY, THREED_JAVA.family)

    result = find_examples(store, THREED_JAVA, NO_LEXICAL_HIT_QUERY)

    # The Draco point is the closest vector (cosine 1.0) yet it is not served; 3d/java now has genuine
    # verified README examples, so the vector stage may return those (and only those).
    _assert_matches_are_verified_and_never_the_draco_chunk(result, payload, point)
    if isinstance(result, NoExampleFound):
        assert result.found is False
    else:
        assert all(match.source == "vector" for match in result)


# ---- (c) real data: all pilots ----------------------------------------------------------------


def test_the_pilot_list_is_the_thirty_two_committed_pilots() -> None:
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) == 32
    for family, platform, source_kind in PILOTS:
        assert (DEMO_MANIFESTS / f"{family}__{platform}__{source_kind}").is_dir(), (family, platform)


@pytest.mark.parametrize(
    ("family", "platform", "source_kind"), PILOTS, ids=[f"{f}/{p}" for f, p, _ in PILOTS]
)
def test_the_predicate_equals_the_verifier_line_on_every_pilot(
    family: str, platform: str, source_kind: str
) -> None:
    pilot = f"{family}/{platform}"
    store = GenerationManifestStore(DEMO_MANIFESTS)  # read in place; nothing is written
    scope = Scope(family=family, platform=platform)
    payload = _generation_payload(store, scope)

    documents = payload["lexical_index"]["documents"]
    accepted = {
        doc_id for doc_id, document in documents.items() if is_verified_example_text(document["text"])
    }
    verifier_marked = {
        doc_id
        for doc_id, document in documents.items()
        if "Kind: verified_example" in document["text"].splitlines()
    }
    assert accepted == verifier_marked, f"pilot {pilot}: predicate and verifier line disagree"

    lexical_chunk_ids = {documents[doc_id]["chunk_id"] for doc_id in accepted}
    all_lexical_chunk_ids = {document["chunk_id"] for document in documents.values()}
    for point in (payload.get("vector_index") or {}).get("points") or []:
        if is_verified_example_text(point["text"]):
            assert point["chunk_id"] in lexical_chunk_ids, (
                f"pilot {pilot}: an accepted vector point has no accepted lexical document with chunk id "
                f"{point['chunk_id']} (in the lexical index at all: {point['chunk_id'] in all_lexical_chunk_ids})"
            )


def test_no_example_mentioning_chunk_is_served_unless_the_verifier_passed_it() -> None:
    """Across every pilot, a chunk containing ``Example:`` but not the verifier's line is rejected."""
    store = GenerationManifestStore(DEMO_MANIFESTS)
    mentioning = 0
    verified = 0
    for family, platform, _source_kind in PILOTS:
        payload = _generation_payload(store, Scope(family=family, platform=platform))
        for document in payload["lexical_index"]["documents"].values():
            text = document["text"]
            if "Example:" not in text:
                continue
            mentioning += 1
            if is_verified_example_text(text):
                verified += 1
                assert "Kind: verified_example" in text.splitlines()
    assert verified >= 14
    assert mentioning > verified  # the DracoFormat-shaped chunks exist, and none of them is accepted


# ---- (d) synthetic generations: provenance, and authored-without-verification is never served --


def _chunks(title: str, body: str) -> list:
    document = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title=title,
        body=f"# Example: {title}\n\n{body}",
    )
    return chunk_document(document)


def _publish_with_vectors(
    tmp_path: Path, chunks: list, vectors_by_text: dict[str, tuple[float, float]]
) -> GenerationManifestStore:
    store = GenerationManifestStore(tmp_path / "manifests")
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family=PDF_NET.family,
        platform=PDF_NET.platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=_FixedVectorEmbeddingProvider(vectors_by_text),
        lease=lease,
    )
    return store


def _two_example_store(tmp_path: Path) -> GenerationManifestStore:
    readme_chunks = _chunks("Merge Two Documents", README_BODY)
    authored_chunks = _chunks("Rotate A Page", AUTHORED_PASSED_BODY)
    chunks = readme_chunks + authored_chunks
    vectors = {readme_chunks[0].text: (1.0, 0.0), authored_chunks[0].text: (0.0, 1.0)}
    return _publish_with_vectors(tmp_path, chunks, vectors)


def test_a_readme_example_is_returned_with_provenance_readme_in_the_exact_branch(tmp_path: Path) -> None:
    result = find_examples(_two_example_store(tmp_path), PDF_NET, "Example: Merge Two Documents")

    assert isinstance(result, list) and len(result) == 1
    assert result[0].source == "exact"
    assert result[0].provenance == "readme"


def test_an_authored_example_is_returned_with_provenance_authored_in_the_exact_branch(tmp_path: Path) -> None:
    result = find_examples(_two_example_store(tmp_path), PDF_NET, "Example: Rotate A Page")

    assert isinstance(result, list) and len(result) == 1
    assert result[0].source == "exact"
    assert result[0].provenance == "authored"
    assert result[0].snippet == "page.Rotate(90)"


def test_provenance_is_reported_per_match_in_the_lexical_branch(tmp_path: Path) -> None:
    store = _two_example_store(tmp_path)

    readme = find_examples(store, PDF_NET, "merge two documents")
    authored = find_examples(store, PDF_NET, "rotate a page")

    assert isinstance(readme, list) and readme
    assert [(match.source, match.provenance) for match in readme] == [("lexical", "readme")]
    assert isinstance(authored, list) and authored
    assert [(match.source, match.provenance) for match in authored] == [("lexical", "authored")]


def test_provenance_is_reported_per_match_in_the_vector_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _two_example_store(tmp_path)

    _use_query_vector(monkeypatch, (0.9, (1.0 - 0.9**2) ** 0.5))
    near_readme = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)
    _use_query_vector(monkeypatch, ((1.0 - 0.9**2) ** 0.5, 0.9))
    near_authored = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)

    assert isinstance(near_readme, list) and [m.provenance for m in near_readme] == ["readme"]
    assert isinstance(near_authored, list) and [m.provenance for m in near_authored] == ["authored"]
    assert all(match.source == "vector" for match in near_readme + near_authored)


def test_example_match_provenance_defaults_to_readme() -> None:
    match = ExampleMatch(PDF_NET, "g", "Example: x", "code()", coverage=1.0, source="exact")

    assert match.provenance == "readme"


def _unverified_authored_store(tmp_path: Path) -> GenerationManifestStore:
    chunks = _chunks("Rotate A Page", AUTHORED_UNPASSED_BODY)
    return _publish_with_vectors(tmp_path, chunks, {chunks[0].text: (1.0, 0.0)})


def test_an_authored_example_without_verification_passed_is_never_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _unverified_authored_store(tmp_path)
    # The chunk really is in the published generation, so each miss below is the predicate's doing.
    payload = _generation_payload(store, PDF_NET)
    assert any("Provenance: authored" in d["text"] for d in payload["lexical_index"]["documents"].values())
    assert any("Provenance: authored" in p["text"] for p in payload["vector_index"]["points"])

    _use_query_vector(monkeypatch, (0.9, (1.0 - 0.9**2) ** 0.5))  # cosine 0.9 with the stored vector

    exact = find_examples(store, PDF_NET, "Example: Rotate A Page")  # the exact branch's query
    lexical = find_examples(store, PDF_NET, "rotate a page")  # a lexical hit on the words
    vector = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)  # only the vector stage could answer

    for result in (exact, lexical, vector):
        assert isinstance(result, NoExampleFound)


def test_find_examples_uses_the_one_predicate_in_all_three_branches() -> None:
    source = Path(find_examples_module.__file__).read_text(encoding="utf-8")

    assert "def is_verified_example_text(" in source
    assert source.count("is_verified_example_text(") >= 4  # the definition + exact, lexical, vector
    assert "_is_example_chunk" not in source
    assert "chunk_kind" not in source


# ---- (e) TC-584: generic-chunk demotion and the loose/confident disclosure --------------------

GENERIC_BODY = (
    "FQN: Example: Quick Start\n"
    "Kind: verified_example\n"
    "A minimal end-to-end example.\n\n"
    'Example:\nworkbook.Save("out.xlsx")\n'
    "Source-Commit: abc123"
)

SPECIFIC_BODY = (
    "FQN: Example: Merge Cells In A Worksheet\n"
    "Kind: verified_example\n"
    "Merges a range of cells into one.\n\n"
    "Example:\ncells.Merge(0, 0, 2, 2)\n"
    "Source-Commit: abc123"
)

LOOSE_BODY = (
    "FQN: Example: Validate Cell Input\n"
    "Kind: verified_example\n"
    "Validates what a user types into a cell.\n\n"
    "Example:\ncell.Validation = validation\n"
    "Source-Commit: abc123"
)

CONFIDENT_BODY = (
    "FQN: Example: Build A Table With Totals\n"
    "Kind: verified_example\n"
    "Builds a table that totals its own rows.\n\n"
    "Example:\ntable.TotalsRowShown = True\n"
    "Source-Commit: abc123"
)


@pytest.mark.parametrize(
    "title",
    [
        "Quick Start",
        "quick start",
        "Quick Start (2)",
        "Quick Start (part 2)",
        "QUICK START (3)",
        "Additional Examples",
        "Additional Examples (5)",
        "Installation, Dependencies, and Quick Start",
        "Installation, Dependencies, and Quick Start (part 2)",
        "Getting Started",
        "More Examples",
        "Basic Usage",
    ],
)
def test_generic_example_titles_are_detected(title: str) -> None:
    assert _is_generic_example_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Merge Cells In A Worksheet",
        "Build a Table With Totals",
        "A quick example",  # a real words/net title - close in spirit, but not one of the real repeated ones
        "Bold Header and Number Format on Cells",
        "Quick Start Guide",  # not the exact observed title
    ],
)
def test_specific_titles_are_never_misclassified_as_generic(title: str) -> None:
    assert _is_generic_example_title(title) is False


def test_a_specific_match_outranks_a_higher_scoring_generic_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves this is NOT simply "sort by raw score": the generic chunk scores HIGHER raw cosine
    similarity than the specific one, yet the specific one is the accepted top match - and the
    generic chunk is demoted, not excluded: it still appears, just ranked after."""
    generic_chunks = _chunks("Quick Start", GENERIC_BODY)
    specific_chunks = _chunks("Merge Cells In A Worksheet", SPECIFIC_BODY)
    chunks = generic_chunks + specific_chunks
    vectors = {
        generic_chunks[0].text: (1.0, 0.0),  # cosine 1.0 with the query below
        specific_chunks[0].text: (0.6, 0.8),  # cosine 0.6 - lower, but still clears every floor
    }
    store = _publish_with_vectors(tmp_path, chunks, vectors)
    _use_query_vector(monkeypatch, (1.0, 0.0))

    result = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)

    assert isinstance(result, list) and len(result) == 2
    assert result[0].fqn == "Example: Merge Cells In A Worksheet"
    assert result[0].coverage == pytest.approx(0.6)
    assert result[0].provenance == "readme"
    assert result[1].fqn == "Example: Quick Start"
    assert result[1].coverage == pytest.approx(1.0)


def test_a_weak_generic_match_below_its_own_stricter_floor_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.50 clears the general floor (0.45) but not the stricter generic one (0.53, see
    ``_VECTOR_GENERIC_FLOOR``'s own comment) - an honest miss, not a confidently-wrong answer."""
    chunks = _chunks("Quick Start", GENERIC_BODY)
    vectors = {chunks[0].text: (0.50, (1.0 - 0.50**2) ** 0.5)}
    store = _publish_with_vectors(tmp_path, chunks, vectors)
    _use_query_vector(monkeypatch, (1.0, 0.0))

    result = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)

    assert isinstance(result, NoExampleFound)


def test_a_generic_match_above_its_own_floor_still_answers_when_it_is_the_only_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic floor demotes, it never excludes outright: a pilot whose only real example
    material happens to live under a generic heading still gets an answer once that chunk clears
    its own (stricter) floor."""
    chunks = _chunks("Quick Start", GENERIC_BODY)
    vectors = {chunks[0].text: (0.60, (1.0 - 0.60**2) ** 0.5)}
    store = _publish_with_vectors(tmp_path, chunks, vectors)
    _use_query_vector(monkeypatch, (1.0, 0.0))

    result = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)

    assert isinstance(result, list) and len(result) == 1
    assert result[0].fqn == "Example: Quick Start"
    assert result[0].coverage == pytest.approx(0.60)


def test_a_loose_match_and_a_confident_match_are_each_disclosed_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither case is silent: a loose match carries ``MATCH_CONFIDENCE_LOOSE`` and a confident one
    carries ``MATCH_CONFIDENCE_CONFIDENT`` - never empty for either, so a caller can always tell
    which one it has without reading the raw ``coverage`` number against a hidden mental scale."""
    loose_chunks = _chunks("Validate Cell Input", LOOSE_BODY)
    confident_chunks = _chunks("Build A Table With Totals", CONFIDENT_BODY)
    chunks = loose_chunks + confident_chunks
    vectors = {
        loose_chunks[0].text: (0.50, (1.0 - 0.50**2) ** 0.5),  # below _VECTOR_CONFIDENT_FLOOR (0.65)
        confident_chunks[0].text: (0.80, (1.0 - 0.80**2) ** 0.5),  # at or above it
    }
    store = _publish_with_vectors(tmp_path, chunks, vectors)
    _use_query_vector(monkeypatch, (1.0, 0.0))

    result = find_examples(store, PDF_NET, NO_LEXICAL_HIT_QUERY)

    assert isinstance(result, list) and len(result) == 2
    by_fqn = {match.fqn: match for match in result}
    confident = by_fqn["Example: Build A Table With Totals"]
    loose = by_fqn["Example: Validate Cell Input"]
    assert confident.coverage == pytest.approx(0.80)
    assert confident.confidence == MATCH_CONFIDENCE_CONFIDENT
    assert loose.coverage == pytest.approx(0.50)
    assert loose.confidence == MATCH_CONFIDENCE_LOOSE
    assert confident.confidence != "" and loose.confidence != "" and confident.confidence != loose.confidence


def test_confidence_is_not_applicable_outside_the_vector_branch(tmp_path: Path) -> None:
    store = _two_example_store(tmp_path)

    exact = find_examples(store, PDF_NET, "Example: Merge Two Documents")
    lexical = find_examples(store, PDF_NET, "merge two documents")

    assert isinstance(exact, list) and exact[0].source == "exact" and exact[0].confidence == ""
    assert isinstance(lexical, list) and lexical[0].source == "lexical" and lexical[0].confidence == ""
