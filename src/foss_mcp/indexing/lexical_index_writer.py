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


def tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-run tokenizer - stdlib-only, offline."""
    return _TOKEN_RE.findall(text.lower())


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
