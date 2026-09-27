"""Build the lexical-index payload for one generation, over the embedded topology TC-004 chose.

Mirrors ``vector_index_writer.point_id``'s identity principle: ``doc_id`` folds
``generation_id`` into every posting's identity, so two generations built from identical chunk
content never share a lexical document either.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from foss_mcp.normalization.chunker import Chunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Splits a camelCase/PascalCase alphanumeric run into its case-boundary sub-words,
# e.g. "AddWatermarkAnnotation" -> ["Add", "Watermark", "Annotation"], while keeping a
# leading run of capitals that precedes a Titlecase word together as one acronym unit,
# e.g. "AFRelationship" -> ["AF", "Relationship"], and leaving an all-caps acronym or a
# plain lowercase identifier as a single piece ("URI" -> ["URI"], "watermark" ->
# ["watermark"]). Probed directly against real identifiers from
# tests/fixtures/pdf_net/api_surface.json (AddWatermarkAnnotation, GetOrCreateMetadata,
# AFRelationship, URI) before being trusted - see TC-073.
_CASE_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

# Standard Okapi BM25 constants (k1, b) - the well-established defaults. Replaces the
# raw TF-IDF-ish scoring below, which divided term frequency by raw document length with
# no saturation and so unfairly penalized a longer, genuinely relevant document (e.g. a
# real code example with boilerplate) against a much shorter, more tangentially related
# one purely for being long. See TC-073.
_BM25_K1 = 1.5
_BM25_B = 0.75

# A standard, hardcoded English stopword set - no new dependency (no nltk, no spacy),
# matching this project's own no-new-runtime-dependency discipline (TC-063's
# HashingEmbeddingProvider). Covers articles, common prepositions, common pronouns,
# common auxiliary/modal verbs, and question words - the closed-class function words
# that carry no distinguishing lexical content of their own. tokenize() is the ONE
# function both build_lexical_index and query_lexical_index call, so filtering here
# fixes indexing and querying consistently.
_STOPWORDS = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
        "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
        "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
        "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
        "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
        "hasn't", "have", "haven't", "having", "he", "her", "here", "hers", "herself",
        "him", "himself", "his", "how", "i", "if", "in", "into", "is", "isn't", "it",
        "its", "itself", "me", "more", "most", "my", "myself", "no", "nor", "not",
        "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves",
        "out", "over", "own", "same", "she", "should", "shouldn't", "so", "some",
        "such", "than", "that", "the", "their", "theirs", "them", "themselves",
        "then", "there", "these", "they", "this", "those", "through", "to", "too",
        "under", "until", "up", "very", "was", "wasn't", "we", "were", "weren't",
        "what", "when", "where", "which", "while", "who", "whom", "why", "will",
        "with", "won't", "would", "wouldn't", "you", "your", "yours", "yourself",
        "yourselves",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-run tokenizer with English stopword filtering and
    camelCase/PascalCase sub-word splitting - stdlib-only, offline.

    For each alphanumeric run, emits the whole run lowercased (so an exact-identifier
    query like "AFRelationship" still matches), PLUS - when the run's own casing splits
    into more than one sub-word - each sub-word lowercased too (so "AddWatermarkAnnotation"
    also yields "add", "watermark", "annotation" and a natural-language query using those
    words can match it, which it never could as a single merged token). Excluding common
    function words, from both the whole token and its sub-words, keeps a natural-language
    query's score concentrated on its actual distinguishing terms, so an honest empty Miss
    remains reachable instead of every chunk scoring weakly-nonzero from shared stopwords
    alone.
    """
    tokens: list[str] = []
    for run in _TOKEN_RE.findall(text.lower()):
        if run not in _STOPWORDS:
            tokens.append(run)
    for run in re.findall(r"[A-Za-z0-9]+", text):
        parts = _CASE_SPLIT_RE.findall(run)
        if len(parts) <= 1:
            continue
        for part in parts:
            sub = part.lower()
            if sub not in _STOPWORDS:
                tokens.append(sub)
    return tokens


def doc_id(generation_id: str, chunk_id: str) -> str:
    """A lexical document's identity - always generation-qualified, never chunk_id alone."""
    return f"{generation_id}::doc::{chunk_id}"


def build_lexical_index(chunks: Sequence[Chunk], chunk_ids: Sequence[str], generation_id: str) -> dict:
    """The lexical-index payload for ``generation_id``: one document per chunk, plus the
    document-frequency table a query scores against.
    """
    if len(chunks) != len(chunk_ids):
        raise ValueError("chunks and chunk_ids must be the same length, pairwise")
    documents: dict[str, dict] = {}
    for chunk_id, chunk in zip(chunk_ids, chunks, strict=True):
        documents[doc_id(generation_id, chunk_id)] = {
            "chunk_id": chunk_id,
            "tokens": tokenize(chunk.text),
            "text": chunk.text,
        }
    doc_freq: Counter = Counter()
    for document in documents.values():
        doc_freq.update(set(document["tokens"]))
    return {
        "generation_id": generation_id,
        "documents": documents,
        "doc_freq": dict(doc_freq),
    }


def query_lexical_index(payload: dict, query_text: str, top_k: int = 5) -> list[str]:
    """The ``top_k`` document ids most relevant to ``query_text`` under standard Okapi BM25.

    BM25 replaces this module's earlier raw-TF-IDF-ish score (count / doc length) *
    idf, which had no saturation and so divided a real code example's matching-term
    density down by its own boilerplate length, unfairly losing to a much shorter,
    more tangentially related chunk. BM25's saturating term-frequency component and
    length normalization relative to the corpus average (``avgdl``) fix that while
    still ranking an exact, rare term highly.
    """
    query_tokens = tokenize(query_text)
    documents: dict[str, dict] = payload["documents"]
    doc_freq: dict[str, int] = payload["doc_freq"]
    n_docs = max(len(documents), 1)
    doc_lengths = [len(document["tokens"]) for document in documents.values()]
    avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 1.0
    if avgdl <= 0.0:
        avgdl = 1.0
    scored: list[tuple[float, str]] = []
    for identifier, document in documents.items():
        tokens = document["tokens"]
        doc_len = len(tokens)
        if not doc_len:
            continue
        tf = Counter(tokens)
        score = 0.0
        for term in query_tokens:
            count = tf.get(term)
            if not count:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
            denominator = count + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / avgdl)
            score += idf * (count * (_BM25_K1 + 1)) / denominator
        if score > 0.0:
            scored.append((score, identifier))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [identifier for _, identifier in scored[:top_k]]
