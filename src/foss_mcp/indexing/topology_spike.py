"""foss_mcp.indexing.topology_spike — TC-004 storage-topology spike.

Plan 18.1 asks for a *measured* decision between storage topologies for a
product's retrieval index, not a guess. This module builds both candidate
profiles over ONE common interface (:class:`TopologyProfile`) so that
``measure()`` compares the topology decision itself rather than two
unrelated retrieval algorithms:

* :class:`EmbeddedProfile` — a pure in-process index: tokenizes and scores
  documents with a small stdlib-only TF-IDF-ish scorer (:class:`_TfidfIndex`)
  held entirely in this process's memory. No serialization boundary, no
  external process to run or operate. Smaller footprint.

* :class:`ServiceShapedProfile` — the SAME scoring core (:class:`_TfidfIndex`),
  but every query is routed through a modelled request/response boundary
  (:class:`_ServiceServer`): the query is serialized, "sent" to the server,
  scored, and the result is serialized back and deserialized by the caller.
  This models the per-query overhead a real external store would add that an
  in-process index does not pay, without this spike actually needing to run
  one (the module is offline and stdlib-only by design — plan 18.1: "This
  runs OFFLINE").

Because both profiles share the identical scoring core, they answer the
identical query set with identical results; the measured difference between
them is the topology overhead, which is exactly what ``decide()`` weighs.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-run tokenizer. Stdlib-only (offline)."""
    return _TOKEN_RE.findall(text.lower())


class TopologyProfile(Protocol):
    """Common interface both candidate topologies answer queries through.

    Keeping both candidates behind one interface is what makes the
    comparison in ``measure()`` apples-to-apples: the caller cannot tell,
    from the interface alone, which topology it is talking to.
    """

    name: str

    def build(self, corpus: dict[str, str]) -> None:
        """Index ``corpus`` (doc_id -> document text)."""
        ...

    def query(self, text: str, top_k: int = 5) -> list[str]:
        """Return up to ``top_k`` doc_ids, most relevant first."""
        ...


@dataclass
class _TfidfIndex:
    """Shared scoring core for both profiles.

    Deliberately naive (term-frequency times a smoothed inverse-document-
    frequency, no external libraries) - the point of this spike is the
    storage-topology decision, not retrieval quality, so both candidate
    profiles must use the exact same ranking math for the comparison to
    isolate topology overhead rather than algorithm differences.
    """

    doc_tokens: dict[str, list[str]]
    doc_freq: Counter
    doc_ids: list[str]

    @classmethod
    def build(cls, corpus: dict[str, str]) -> "_TfidfIndex":
        doc_tokens = {doc_id: tokenize(text) for doc_id, text in corpus.items()}
        doc_freq: Counter = Counter()
        for tokens in doc_tokens.values():
            doc_freq.update(set(tokens))
        return cls(doc_tokens=doc_tokens, doc_freq=doc_freq, doc_ids=list(corpus))

    def score(self, query_tokens: list[str], top_k: int) -> list[str]:
        n_docs = max(len(self.doc_ids), 1)
        scored: list[tuple[float, str]] = []
        for doc_id in self.doc_ids:
            tokens = self.doc_tokens[doc_id]
            if not tokens:
                continue
            tf = Counter(tokens)
            score = 0.0
            for term in query_tokens:
                count = tf.get(term)
                if not count:
                    continue
                idf = math.log((n_docs + 1) / (self.doc_freq[term] + 1)) + 1.0
                score += (count / len(tokens)) * idf
            if score > 0.0:
                scored.append((score, doc_id))
        # Sort by descending score, then doc_id for a deterministic tie-break.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [doc_id for _, doc_id in scored[:top_k]]


class EmbeddedProfile:
    """Candidate (a): pure in-process index (smaller footprint).

    One process, no serialization boundary, nothing external to deploy or
    operate.
    """

    name = "embedded"

    def __init__(self) -> None:
        self._index: _TfidfIndex | None = None

    def build(self, corpus: dict[str, str]) -> None:
        self._index = _TfidfIndex.build(corpus)

    def query(self, text: str, top_k: int = 5) -> list[str]:
        if self._index is None:
            raise RuntimeError("EmbeddedProfile.build() must run before query()")
        return self._index.score(tokenize(text), top_k)


# Modelled per-query round-trip cost for the service-shaped profile: what an
# out-of-process call pays for network transmission on top of the real (also
# paid, below) JSON serialization cost, which an in-process call pays
# neither of. Implemented as a deterministic busy-wait on the same clock
# `measure()` uses, rather than `time.sleep`, so the modelled gap is a
# precise, stable number of seconds regardless of OS sleep-timer
# granularity or scheduler jitter.
_MODELLED_ROUND_TRIP_SECONDS = 0.0003


def _simulate_round_trip_latency(seconds: float) -> None:
    """Busy-wait for at least ``seconds`` of wall-clock time."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        pass


class _ServiceServer:
    """Stands in for the far side of a service-shaped index.

    Receives a serialized request, scores it against the shared index, and
    returns a serialized response — modelling the boundary a real external
    store would sit behind, without this offline spike actually running one.
    """

    def __init__(self, index: _TfidfIndex) -> None:
        self._index = index

    def handle(self, request: bytes) -> bytes:
        payload = json.loads(request.decode("utf-8"))
        doc_ids = self._index.score(tokenize(payload["text"]), payload["top_k"])
        return json.dumps({"doc_ids": doc_ids}).encode("utf-8")


class ServiceShapedProfile:
    """Candidate (b): same scoring core as :class:`EmbeddedProfile`, but
    structured as a client talking to a server across a modelled boundary.

    Every query pays an explicit serialize -> transmit -> handle ->
    transmit -> deserialize round trip (:meth:`query`), which is the
    per-query overhead an external store would add that an in-process index
    does not: real JSON serialization cost plus a modelled network
    transmission cost (:func:`_simulate_round_trip_latency`).
    """

    name = "service_shaped"

    def __init__(self) -> None:
        self._server: _ServiceServer | None = None

    def build(self, corpus: dict[str, str]) -> None:
        self._server = _ServiceServer(_TfidfIndex.build(corpus))

    def query(self, text: str, top_k: int = 5) -> list[str]:
        if self._server is None:
            raise RuntimeError("ServiceShapedProfile.build() must run before query()")
        request = json.dumps({"text": text, "top_k": top_k}).encode("utf-8")
        _simulate_round_trip_latency(_MODELLED_ROUND_TRIP_SECONDS)
        response = self._server.handle(request)
        _simulate_round_trip_latency(_MODELLED_ROUND_TRIP_SECONDS)
        return json.loads(response.decode("utf-8"))["doc_ids"]


@dataclass(frozen=True)
class LabelledQuery:
    """One hand-labelled query: query text plus the doc_id it should surface."""

    text: str
    correct_doc_id: str


@dataclass(frozen=True)
class Measurement:
    """Per-profile measurement returned by :func:`measure`."""

    profile_name: str
    p95_latency_seconds: float
    top5_relevance: float
    n_queries: int


def measure(
    profile: TopologyProfile,
    corpus: dict[str, str],
    queries: list[LabelledQuery],
    top_k: int = 5,
) -> Measurement:
    """Build ``profile`` over ``corpus`` and answer every query in
    ``queries`` through the common :class:`TopologyProfile` interface,
    timing each query and checking whether the labelled-correct doc lands in
    the top ``top_k`` results.

    Returns p95 query latency and the fraction of queries whose
    labelled-correct doc appears in the top ``top_k`` ("top-5 relevance"
    when ``top_k == 5``, plan 18.1's metric).
    """
    if not queries:
        raise ValueError("measure() requires at least one labelled query")

    profile.build(corpus)

    latencies: list[float] = []
    hits = 0
    for labelled in queries:
        start = time.perf_counter()
        result_ids = profile.query(labelled.text, top_k=top_k)
        latencies.append(time.perf_counter() - start)
        if labelled.correct_doc_id in result_ids:
            hits += 1

    latencies.sort()
    p95_index = min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)
    p95 = latencies[p95_index]
    relevance = hits / len(queries)

    return Measurement(
        profile_name=profile.name,
        p95_latency_seconds=p95,
        top5_relevance=relevance,
        n_queries=len(queries),
    )


def decide(embedded: Measurement, service_shaped: Measurement) -> str:
    """Plan 18.1 decision rule.

    Pick the smaller-footprint profile (``embedded``) UNLESS:

    * its relevance is more than 10% worse (relative) than the other
      profile's, or
    * its p95 latency is more than 2x the other profile's.

    Either condition being true means the smaller-footprint profile is not
    good enough, and the other (``service_shaped``) profile is chosen
    instead.
    """
    if service_shaped.top5_relevance > 0.0:
        relevance_ratio = embedded.top5_relevance / service_shaped.top5_relevance
    else:
        # Both relevances are 0 (degenerate fixture): treat as tied.
        relevance_ratio = 1.0
    relevance_too_low = relevance_ratio < 0.90

    if service_shaped.p95_latency_seconds > 0.0:
        latency_ratio = embedded.p95_latency_seconds / service_shaped.p95_latency_seconds
    else:
        latency_ratio = 0.0
    latency_too_high = latency_ratio > 2.0

    if relevance_too_low or latency_too_high:
        return service_shaped.profile_name
    return embedded.profile_name
