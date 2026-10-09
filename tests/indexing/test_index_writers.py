"""Vector and lexical index writers over the embedded topology TC-004 chose.

``DeterministicEmbeddingProvider`` is the offline, deterministic embedding provider the test
suite uses. It is defined ONLY here, per TC-015's inputs: never importable from a ``src/``
production path (``foss_mcp.indexing.embedding_provider`` ships the interface only, no
concrete provider) - a test double in a production import path is how it quietly becomes the
production behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import yaml

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.lexical_index_writer import build_lexical_index, doc_id, query_lexical_index, tokenize
from foss_mcp.indexing.vector_index_writer import build_vector_index, point_id, query_vector_index
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance, SourceKind, make_document

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
PDF_NET_API_SURFACE = FIXTURES_DIR / "pdf_net" / "api_surface.json"
PDF_NET_FURNISHED_PAGE = FIXTURES_DIR / "furnished" / "pdf_net" / "pages" / "_index.md"


class DeterministicEmbeddingProvider:
    """Offline and deterministic: the same text always maps to the same vector, no randomness,
    no model, no network - enough to test indexing and point-id behavior, nothing about
    embedding quality.
    """

    dimension = 8

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(tuple(byte / 255.0 for byte in digest[: self.dimension]))
        return vectors


PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            "Document",
            "The Document class opens and saves PDF files.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
        Chunk(
            "Page",
            "A Page belongs to a PageCollection.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
    ]


def test_point_id_is_generation_qualified_not_content_only() -> None:
    assert point_id("gen-a", "chunk-1") != point_id("gen-b", "chunk-1")
    assert point_id("gen-a", "chunk-1") == point_id("gen-a", "chunk-1")


def test_doc_id_is_generation_qualified_not_content_only() -> None:
    assert doc_id("gen-a", "chunk-1") != doc_id("gen-b", "chunk-1")


def test_the_same_text_always_embeds_to_the_same_vector() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.embed(["hello world"]) == provider.embed(["hello world"])


def test_build_vector_index_produces_one_point_per_chunk_with_a_real_vector() -> None:
    chunks = _chunks()
    provider = DeterministicEmbeddingProvider()
    payload = build_vector_index(chunks, ["c1", "c2"], provider, "gen-1")
    assert payload["dimension"] == provider.dimension
    assert [p["point_id"] for p in payload["points"]] == [point_id("gen-1", "c1"), point_id("gen-1", "c2")]
    assert len(payload["points"][0]["vector"]) == provider.dimension


def test_query_vector_index_ranks_the_closest_point_first() -> None:
    chunks = _chunks()
    provider = DeterministicEmbeddingProvider()
    payload = build_vector_index(chunks, ["c1", "c2"], provider, "gen-1")
    query_vector = provider.embed([chunks[0].text])[0]
    assert query_vector_index(payload, query_vector, top_k=1) == [point_id("gen-1", "c1")]


def test_build_lexical_index_produces_one_document_per_chunk() -> None:
    payload = build_lexical_index(_chunks(), ["c1", "c2"], "gen-1")
    assert set(payload["documents"]) == {doc_id("gen-1", "c1"), doc_id("gen-1", "c2")}


def test_query_lexical_index_finds_the_matching_document() -> None:
    payload = build_lexical_index(_chunks(), ["c1", "c2"], "gen-1")
    assert query_lexical_index(payload, "PageCollection", top_k=1) == [doc_id("gen-1", "c2")]


def test_tokenize_drops_stopwords_but_keeps_distinguishing_terms() -> None:
    tokens = tokenize("how do I add a watermark to a PDF")
    for stopword in ("how", "do", "i", "a", "to"):
        assert stopword not in tokens
    for real_term in ("add", "watermark", "pdf"):
        assert real_term in tokens


def test_natural_language_query_matches_its_real_symbol_but_not_a_stopword_only_sentence() -> None:
    """A real build/query round trip, no mocking: a natural-language question whose only
    distinguishing term is the chunk's real symbol name must match it, while a different
    sentence sharing only stopwords with that chunk's text must return no match at all -
    proving stopword filtering lets an honest empty Miss stay reachable.
    """
    chunks = [
        Chunk(
            "AddWatermarkAnnotation",
            "AddWatermarkAnnotation lets you add a watermark to a PDF document.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
        Chunk(
            "Page",
            "A Page belongs to a PageCollection.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
    ]
    payload = build_lexical_index(chunks, ["c1", "c2"], "gen-1")

    matches = query_lexical_index(payload, "how do I add a watermark to a PDF", top_k=5)
    assert matches
    assert matches[0] == doc_id("gen-1", "c1")

    # Shares only stopwords ("a", "to", "the") with either chunk's text - no distinguishing
    # term in common with anything indexed, so this must score exactly 0.0 for every document
    # and return no match at all.
    no_match = query_lexical_index(payload, "how do I get to the store", top_k=5)
    assert no_match == []


# --- TC-073: camelCase/PascalCase tokenization + Okapi BM25 -----------------------------------
#
# Real identifiers below (AddFloatingBox, GetOrCreateMetadata, BDCProperties) are pulled directly
# from tests/fixtures/pdf_net/api_surface.json - probed against the real regex before being
# trusted, per this project's own standing discipline against guessing at pattern-matching
# behavior (see lexical_index_writer._CASE_SPLIT_RE's own docstring comment).
#
# TC-263 (rework attempt 2): the fixture was regenerated with TC-252's centrality-ranked
# selection in place of the old alphabetical one. "AddWatermarkAnnotation" and "AFRelationship"
# (the identifiers this section used to probe) no longer survive the new, real cut - replaced
# below with real identifiers confirmed present in the regenerated fixture that exhibit the exact
# same shapes these tests need. "GetOrCreateMetadata" (on Document, itself now the #2-centrality
# type) still survives unchanged.


def _pdf_net_fixture() -> dict:
    return json.loads(PDF_NET_API_SURFACE.read_text(encoding="utf-8"))


def _real_identifiers_from_fixture() -> dict[str, str]:
    """A handful of real identifiers straight from the committed pdf/net fixture, covering
    PascalCase (multi-word), an acronym-prefixed PascalCase name, and a plain lowercase name -
    exactly the shapes the case-split regex was probed against before being trusted.

    "BDCProperties" (a type name) and "AddFloatingBox" (a method on "Page" itself - the
    headline symbol TC-252's fix recovers) replace "AFRelationship"/"AddWatermarkAnnotation",
    which no longer survive the centrality-ranked cut; "GetOrCreateMetadata" (on "Document")
    still does.
    """
    fixture = _pdf_net_fixture()
    names: dict[str, str] = {}
    for entry in fixture["types"]:
        name = entry.get("name", "")
        if name == "BDCProperties":
            names["acronym_prefixed"] = name
        for method in entry.get("methods") or []:
            method_name = method.get("name", "")
            if method_name == "AddFloatingBox":
                names["pascal_case"] = method_name
            if method_name == "GetOrCreateMetadata":
                names["pascal_case_with_stopword"] = method_name
    assert names.keys() == {"acronym_prefixed", "pascal_case", "pascal_case_with_stopword"}
    return names


def test_tokenize_splits_camelcase_and_pascalcase_identifiers_into_subwords() -> None:
    """Real identifiers from the committed pdf/net fixture - a natural-language query built
    from an identifier's constituent words must be able to match it, in addition to the whole,
    merged identifier still matching an exact-identifier query.
    """
    names = _real_identifiers_from_fixture()

    tokens = tokenize(names["pascal_case"])
    assert names["pascal_case"].lower() in tokens  # AddFloatingBox, merged
    assert "add" in tokens
    assert "floating" in tokens
    assert "box" in tokens

    # GetOrCreateMetadata - "Or" is itself an English stopword; splitting must not smuggle it
    # back in as a distinguishing term.
    tokens = tokenize(names["pascal_case_with_stopword"])
    assert names["pascal_case_with_stopword"].lower() in tokens
    assert "get" in tokens
    assert "create" in tokens
    assert "metadata" in tokens
    assert "or" not in tokens

    # BDCProperties - a leading run of capitals immediately before a Titlecase word is kept
    # together as one acronym unit ("bdc"), not split letter by letter.
    tokens = tokenize(names["acronym_prefixed"])
    assert names["acronym_prefixed"].lower() in tokens
    assert "bdc" in tokens
    assert "properties" in tokens

    # An ALL-CAPS acronym with nothing following it, and a plain lowercase identifier, are each
    # left as a single token - splitting only ever ADDS tokens, it never fragments a name that
    # has no internal case boundary.
    assert tokenize("URI") == ["uri"]
    assert tokenize("watermark") == ["watermark"]


def _real_enum_chunk_containing_page() -> Chunk:
    """The real ``ArtifactType`` enum chunk, built by this project's own real
    ``build_chunks_from_api_surface`` (TC-062/TC-065) from the committed pdf/net fixture, sliced
    to that one type. A real, short (member-list-only, 31-token) chunk that happens to contain
    the distinguishing term "page" once, as one of its enum member names - a real shape that
    still outranks a much longer, genuinely more relevant example chunk under the old raw-TF-IDF
    scoring (recomputed directly against the TC-263 regenerated fixture; the originally-used
    "ArtifactSubtype"/"watermark" pair no longer demonstrates this once the fixture and its
    member-splitting token count changed - see the TC-263 rework note above).
    """
    fixture = _pdf_net_fixture()
    (entry,) = [t for t in fixture["types"] if t.get("name") == "ArtifactType"]
    assert entry["kind"] == "enum_declaration"
    assert any(member.get("name") == "Page" for member in entry["enum_members"])
    sliced_fixture = {**fixture, "types": [entry]}
    (chunk,) = build_chunks_from_api_surface(sliced_fixture, title="pdf/net", max_types=1)
    return chunk


def _real_example_chunk_mentioning_page_repeatedly() -> Chunk:
    """The real, compile-verified "Open a PDF, Add a Link Annotation, and Save" example chunk,
    built the same way ``infra/build_chunks.py`` (TC-068/TC-069/TC-070) builds a verified example
    chunk from a real furnished page's real candidate - description, real code, and the trailing
    ``Source-Commit:`` line included, minus only the real ``verify_dotnet_example`` compile step
    itself (which needs a live container - REQ-G2-049's own live proof exercises that separately;
    this unit test's concern is scoring, not compilation). Mentions "page" 3 times across its
    110 real tokens.
    """
    front_matter = PDF_NET_FURNISHED_PAGE.read_text(encoding="utf-8").split("---", 2)[1]
    page = yaml.safe_load(front_matter)
    (candidate,) = [
        c for c in extract_candidate_examples(page) if c.title == "Open a PDF, Add a Link Annotation, and Save"
    ]
    commit = "b7172877651413cff57a8bfe41fb8a8befb2406b"
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="example",
        provenance=Provenance(
            repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
            commit=commit,
            path=str(PDF_NET_FURNISHED_PAGE),
        ),
        evidence_refs=(f"aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET@{commit}",),
        title=candidate.title,
        body=(
            f"# Example: {candidate.title}\n\n"
            f"FQN: Example: {candidate.title}\n"
            f"Kind: verified_example\n"
            f"{candidate.description}\n\n"
            f"Example:\n{candidate.code}"
        ),
    )
    (chunk,) = chunk_document(doc)
    return Chunk(
        chunk.section_title,
        f"{chunk.text}\nSource-Commit: {commit}",
        chunk.source_kind,
        chunk.content_type,
        chunk.provenance,
        chunk.trust_tier,
        chunk.evidence_refs,
        chunk.validation,
    )


def _old_raw_tf_idf_score(
    tokens: list[str], query_tokens: list[str], *, n_docs: int, doc_freq: dict[str, int]
) -> float:
    """This module's OLD (pre-TC-073) scoring formula, reproduced here only so this regression
    test can demonstrate the real ranking it used to produce - not something production code
    still calls.
    """
    tf = Counter(tokens)
    score = 0.0
    for term in query_tokens:
        count = tf.get(term)
        if not count:
            continue
        idf = math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1.0
        score += (count / len(tokens)) * idf
    return score


def test_bm25_ranks_the_longer_real_example_over_the_shorter_real_enum_match() -> None:
    """Reproduces the real finding, recomputed directly against the TC-263 regenerated
    fixture: a real 31-token enum chunk (ArtifactType, containing "page" once as a member name)
    against a real, longer example chunk (containing "page" three times across 110 real tokens,
    diluted by real code, boilerplate, and a 41-character commit-hash token) - both real, both
    containing the query's one matching term. Under the OLD raw-TF-IDF-ish formula the short,
    only-incidentally-matching enum chunk out-scores the long, genuinely relevant example chunk
    purely from dividing by raw document length (verified: 1/31 ~= 0.0323 > 3/110 ~= 0.0273).
    Under BM25 the example chunk must rank first instead.
    """
    enum_chunk = _real_enum_chunk_containing_page()
    example_chunk = _real_example_chunk_mentioning_page_repeatedly()

    payload = build_lexical_index([enum_chunk, example_chunk], ["enum_c", "example_c"], "gen-1")
    query_tokens = tokenize("page")

    enum_tokens = payload["documents"][doc_id("gen-1", "enum_c")]["tokens"]
    example_tokens = payload["documents"][doc_id("gen-1", "example_c")]["tokens"]
    n_docs = len(payload["documents"])
    doc_freq = payload["doc_freq"]

    old_enum_score = _old_raw_tf_idf_score(enum_tokens, query_tokens, n_docs=n_docs, doc_freq=doc_freq)
    old_example_score = _old_raw_tf_idf_score(example_tokens, query_tokens, n_docs=n_docs, doc_freq=doc_freq)
    # The real regression this card fixes: under the old formula, the short enum chunk actually
    # outranks the long, genuinely relevant example chunk.
    assert old_enum_score > old_example_score

    bm25_order = query_lexical_index(payload, "page", top_k=5)
    assert bm25_order[0] == doc_id("gen-1", "example_c")


def test_bm25_exact_rare_term_query_still_returns_the_correct_chunk() -> None:
    """BM25 must not regress exact, rare-term matching: querying the real, distinctive symbol
    name "DigestHashAlgorithm" must still return its own chunk first, ahead of an unrelated chunk
    that shares no distinguishing term with it. ("AFRelationship", used here before TC-263, no
    longer survives the centrality-ranked cut; "DigestHashAlgorithm" is a real enum confirmed
    present in the regenerated fixture.)
    """
    fixture = _pdf_net_fixture()
    (af_entry,) = [t for t in fixture["types"] if t.get("name") == "DigestHashAlgorithm"]
    assert af_entry["kind"] == "enum_declaration"
    sliced_fixture = {**fixture, "types": [af_entry]}
    (af_chunk,) = build_chunks_from_api_surface(sliced_fixture, title="pdf/net", max_types=1)

    unrelated_chunk = Chunk(
        "Page",
        "A Page belongs to a PageCollection.",
        "self_extracted",
        "api_surface",
        af_chunk.provenance,
        "highest",
        (),
        NOT_CHECKED,
    )

    payload = build_lexical_index([af_chunk, unrelated_chunk], ["af_c", "page_c"], "gen-1")
    assert query_lexical_index(payload, "DigestHashAlgorithm", top_k=5) == [doc_id("gen-1", "af_c")]
