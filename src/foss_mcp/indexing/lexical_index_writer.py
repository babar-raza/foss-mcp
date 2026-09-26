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
    """Lowercase, alphanumeric-run tokenizer with English stopword filtering - stdlib-only,
    offline. Excluding common function words keeps a natural-language query's score
    concentrated on its actual distinguishing terms, so an honest empty Miss remains
    reachable instead of every chunk scoring weakly-nonzero from shared stopwords alone.
    """
    return [token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]


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
    """The ``top_k`` document ids most relevant to ``query_text`` by a TF-IDF-ish score."""
    query_tokens = tokenize(query_text)
    documents: dict[str, dict] = payload["documents"]
    doc_freq: dict[str, int] = payload["doc_freq"]
    n_docs = max(len(documents), 1)
    scored: list[tuple[float, str]] = []
    for identifier, document in documents.items():
        tokens = document["tokens"]
        if not tokens:
            continue
        tf = Counter(tokens)
        score = 0.0
        for term in query_tokens:
            count = tf.get(term)
            if not count:
                continue
            idf = math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1.0
            score += (count / len(tokens)) * idf
        if score > 0.0:
            scored.append((score, identifier))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [identifier for _, identifier in scored[:top_k]]
