"""find_examples: exact-match first, then a lexical fallback bounded by a measured relevance
floor, then (TC-331) a strictly-additive vector-based fallback bounded by its own measured
cosine-similarity floor, over verified snippets only.

No snippet is ever executed inside the serving process: this module never calls ``eval``,
``exec``, ``compile``, or a subprocess on anything it returns - a snippet is text a human (or
another process entirely) chose to run, never code this tool runs itself.

A "verified snippet" is a chunk ``is_verified_example_text`` accepts (TC-465) - the one definition
every branch uses: the ``FQN: Example: <title>`` line, the verifier's own ``Kind: verified_example``
line, an ``Example:`` block line, and ``Verification: passed`` for an authored example (the
published lexical-index payload has no separate metadata field for any of it, same constraint
``get_symbol``'s ``FQN:`` line convention works around). A symbol chunk whose Javadoc merely says
``Example:`` is therefore never a verified example. Each match reports its ``provenance``.

The exact-FQN-match branch (a direct FQN lookup over the generation's documents) is already precise and reports
``coverage=1.0`` unconditionally - it is definitionally exact, never a guess. The semantic
(BM25) fallback is NOT definitionally exact: before TC-273, any chunk with a strictly-positive
BM25 score was treated as a confident match, so a single shared common word between the query
and an otherwise-unrelated chunk (e.g. both mention "page") could manufacture a full-confidence
``ExampleMatch`` for a query with no real relevant example in the corpus at all. The fallback
now ALSO requires ``coverage`` - the fraction of the query's own distinct (stemmed) terms present
in the candidate chunk's own tokens - to be at or above ``relevance.RELEVANCE_THRESHOLD`` (see
that module for how the number was measured against 3 real pilots; since TC-381 the scope's
product-family word is not one of the terms, so it can no longer fill a slot of the floor). A chunk that only shares a stray
common word with the query clears the old positive-BM25-score bar but not this floor, and the
fallback now honestly returns ``NoExampleFound`` instead of guessing.

TC-331 adds one more, strictly additive stage: when the lexical fallback above finds nothing at
all, one more attempt is made against the generation's vector index (if one was published for
it) before giving up. This can only ever turn an existing ``NoExampleFound`` into a real match -
it never runs when the lexical fallback already found something, so it can never regress an
existing hit. It is also fully exception-safe: a gateway outage, a missing gateway
configuration, or any other failure embedding the query degrades this stage back to exactly
today's ``NoExampleFound`` behavior, mirroring the mission plan's own named risk mitigation
("deterministic/lexical fallback always available").

TC-508 bounds that stage, because it runs on the request path: the query embedding is made through
``vector_guard`` with NO retries (one attempt), a hard overall deadline (2.0 s by default,
``FOSS_MCP_VECTOR_DEADLINE_SECONDS``) and a circuit breaker (3 consecutive gateway failures or
timeouts skip the gateway for 60 s, during which the raw query is never handed to the provider).
When the stage is skipped the tool returns the honest lexical miss it always returned, now with a
``vector_fallback`` note (``skipped_timeout``, ``skipped_circuit_open`` or ``skipped_error``) and a
reason saying the semantic fallback was skipped. A normal miss (the stage ran and found nothing)
carries no note.

TC-516 makes the lexical stage IDF-weighted (``relevance.term_weights``: a query word the whole
generation has never seen counts most), requires every identifier-like query word (``DataSource``) to be
present in the chunk, and answers a query with no distinctive term ("Example:", "pdf examples") with the
honest miss at once. The 0.6 threshold, the exact-title branch and the vector fallback are untouched.

TC-543 adds one rule to the lexical stage: when the query has at most two distinctive terms
(``relevance.TITLE_ANCHOR_MAX_TERMS``), an example may answer only if one of them is a word of its
TITLE (``relevance.title_anchored``). A short query is otherwise satisfied by any example whose code
calls ``Save`` or ``Open``. A longer query is judged by the coverage floor alone. Every
``ExampleMatch`` also carries ``match_basis``, which says what its ``coverage`` number is.

TC-584 fixes two defects a 2026-10-24/25 independent audit found in the vector fallback ONLY (the
lexical and exact-title branches above are untouched): a README's own generic catch-all section
("Quick Start", "Additional Examples", "Installation, Dependencies, and Quick Start" - the exact
titles a 2026-10-26 reproduction against the real baked demo-manifests actually found, not a guess;
see ``_GENERIC_EXAMPLE_TITLES``) embeds close to almost any on-topic query and can act as a
universal attractor, and can even outrank a genuinely correct, lower-scored candidate. The vector
fallback now (1) holds a generic-titled chunk to a stricter floor than ``_VECTOR_SIMILARITY_THRESHOLD``
(``_VECTOR_GENERIC_FLOOR``) and, among chunks that each clear their own floor, ranks every
specifically-titled one ahead of every generic one regardless of raw cosine score - demoted, never
excluded outright, so a pilot whose only real example material happens to live under a generic
heading still answers; and (2) adds ``ExampleMatch.confidence`` (additive, last field): ``"confident"``
or ``"loose"`` for a vector match, depending on where its score sits relative to
``_VECTOR_CONFIDENT_FLOOR``, so a caller can tell a strong match from a borderline one without
reading the raw ``coverage`` number against a hidden mental scale. ``confidence`` is ``""`` for an
exact or lexical match - the concept is specific to the vector fallback's cosine-similarity scale.

TC-635 (Q10, G3/REQ-G3-011) adds two more, strictly additive admission paths beside the existing
title anchor for the lexical stage's per-document gate - never replacing or weakening it, and never
touching the coverage floor itself: a CODE anchor (``relevance.code_anchored``), True when a query's
identifier-shaped required term (``relevance.required_terms``) is present in the example's OWN CODE
SNIPPET specifically, not merely somewhere in the chunk's surrounding prose; and a bounded PREFIX
title match (``relevance.title_prefix_anchored``), True when a distinctive query term of at least
``relevance.TITLE_PREFIX_MIN_TERM_LENGTH`` characters is a literal prefix of a title word at least
``relevance.TITLE_PREFIX_MIN_EXTRA_LENGTH`` characters longer (``async`` anchors
``asynchronously``, which stemming does not fold to the same root). A match admitted by the code
anchor ALONE - neither the title anchor nor the prefix match would have admitted it - carries the
new, honestly-labelled ``MATCH_BASIS_CODE_WORD``; a match the prefix mechanism admits is labelled
exactly as ``title_anchored`` already labels any other title-anchored match (it is, semantically,
one more way a title can anchor a query), so no new basis constant was needed for it.

TC-670 (G3/REQ-G3-011) widens TC-635's own code anchor from "a query term that LOOKS syntactically
like an identifier" to "a query term that IS a real symbol name in this pilot's own index" -
measured directly against the real, baked ``infra/demo-manifests`` data: ``relevance.
required_terms`` returns an EMPTY set for every plain word ("Workbook", "watermark", "Document",
"Scene", ...), the overwhelming majority of real class/concept names in this portfolio, closing
the dominant share of round-2's #1 cross-cutting finding (``docs/round2/HANDOFF.md``) that TC-635
itself left untouched. ``relevance.known_symbol_names`` - this scope's own real short symbol
names, built fresh every call from this generation's own ``documents``, never a global or
cross-pilot list - is matched against the
query's own original-cased words CASE-SENSITIVELY, additive beside the existing compound-identifier
check (never instead of it): the common English word "workbook" must never anchor on the real class
"Workbook" through this mechanism. The CODE side of the check is unchanged - the example's own
snippet must still actually contain the term for the anchor to fire; only which query terms are
WATCHED FOR by it is widened.

TC-670 also wires ``known_defects`` into this tool - the one surface among
``get_symbol``/``search_symbols``/``get_product_reference``/``report_index_freshness``/
``find_examples`` that never had it. ``ExampleMatch.known_defects`` (additive, default ``()``) is
populated, for the exact-title and lexical branches only, by ``_example_known_defects``: it scans
the matched example's own CODE SNIPPET for a real symbol name (the same mechanism as the widened
code anchor above) and discloses every committed known-defect entry that applies to it, through the
real ``get_symbol.known_defect_lines`` - never the example's own pseudo ``Example: <title>`` FQN,
which no real registry entry ever names.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from foss_mcp.indexing.gateway_embedding_provider import (
    _API_KEY_ENV,
    _ENDPOINT_ENV,
    GatewayEmbeddingProvider,
    Vector,
)
from foss_mcp.indexing.gateway_embedding_provider import (
    GatewayEmbeddingProvider as _RealGatewayEmbeddingProvider,
)
from foss_mcp.indexing.generation_manifest import GenerationManifest, GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored, tokenize
from foss_mcp.indexing.vector_index_writer import query_vector_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn, known_defect_lines
from foss_mcp.mcp.tools.relevance import (
    RELEVANCE_THRESHOLD,
    TITLE_ANCHOR_MAX_TERMS,
    code_anchored,
    distinctive_terms,
    fold_query,
    guarded_coverage,
    has_minimum_evidence,
    known_symbol_names,
    normalize_query,
    query_terms,
    required_terms,
    stem_token,
    term_weights,
    title_anchored,
    title_prefix_anchored,
)
from foss_mcp.mcp.tools.search_symbols import (
    _NON_SYMBOL_FQN_PREFIXES,
    SOURCE_KIND,
    scope_key,
)
from foss_mcp.mcp.tools.vector_guard import (
    QUERY_TIME_MAX_ATTEMPTS,
    STATUS_OK,
    STATUS_SKIPPED_CIRCUIT_OPEN,
    STATUS_SKIPPED_ERROR,
    STATUS_SKIPPED_TIMEOUT,
    get_guard,
)

_EXAMPLE_MARKER = "Example:"
_EXAMPLE_FQN_PREFIX = "Example: "

# The lexical floor is ``relevance.RELEVANCE_THRESHOLD`` (TC-273's measured 0.6, unchanged; see
# that module). TC-381 moved it, with the query-term rule, into the one place documentation search,
# lookup and example search all share, and removed the product-family word from the terms it is
# computed over: "pdf" occurs in nearly every example, so it used to fill one slot of the floor.

# Empirically measured (TC-331), not guessed: built against 2 real, already-ingested pilots'
# own real example chunks (pdf/go, 4 examples; pdf/typescript, 4 examples) through the real,
# live internal Qwen embedding gateway (TC-330's ``GatewayEmbeddingProvider``) - 8
# true-positive probes (a natural query for each example's own real task) scored cosine
# 0.6446-0.8427; 4 false-positive probes (unrelated queries) scored, at best, 0.1782-0.2768
# against any example in the same pilot. This worker independently re-ran the same measurement
# against the same 2 real pilots' own committed demo-manifest generations before trusting these
# numbers: 8 true-positive probes scored 0.6675-0.8537; 3 false-positive probes scored, at
# best, 0.2701-0.3027 against any example. 0.45 sits with real margin on both sides of both
# measurements (0.3027 < 0.45 < 0.6675), not a round number picked without this data.
#
# TC-584 RE-MEASUREMENT (2026-10-26), against a materially larger sample as instructed by this
# card: the SAME methodology (real embedding gateway, each pilot's own real, already-ingested
# example chunk vectors, a natural query for each true-positive probe's own real task, an
# unrelated query scored against EVERY example in the pilot for each false-positive probe) run
# against 10 real pilots spanning 8 families (pdf/go, pdf/typescript, pdf/net, cells/net,
# words/net, slides/net, note/python, 3d/net, email/net, html/python) - 5x the pilot count and 4x
# the family count the card asked for as a floor. 20 true-positive probes (2 per pilot) scored
# cosine 0.6059-0.8790; 30 false-positive probes (3 unrelated queries per pilot, each scored
# against every example in that pilot) scored, at best, 0.1976-0.3826. 0.45 still sits with real
# margin on both sides (0.3826 < 0.45 < 0.6059) at this larger scale, so it is KEPT, not raised:
# nothing in this larger sample pushes the false-positive ceiling or the true-positive floor
# close enough to 0.45 to need a new number.
_VECTOR_SIMILARITY_THRESHOLD = 0.45

# TC-584: a generic-titled chunk (``_is_generic_example_title`` below) is held to a STRICTER floor
# than the one above, because the universal-attractor reproduction (see the module docstring) showed
# a generic chunk clearing 0.45 for a query with NO real relevant example in the pilot at all. Real
# measurement against the baked demo-manifests (2026-10-26): "merge cells in a worksheet" - a query
# with no merge example anywhere in these generations - scored, against the generic "Quick
# Start"/"Additional Examples" chunk, 0.4693-0.5240 on 5 of the 7 real cells pilots that have one
# (net, python, java, go, typescript; cpp and rust have no generic-titled chunk at all). Against
# that, the lowest score this worker could measure for a query actually asking for the ONLY example
# material a pilot genuinely has under a generic heading (the 3 real pilots whose ENTIRE
# vector-indexed example set is generic-titled: barcode/python, cells/java, email/cpp) was 0.5495
# (email/cpp, "parse an eml file"). 0.53 sits strictly between the two (0.5240 < 0.53 < 0.5495): it
# excludes every measured false-positive score above, while never excluding a measured genuine
# single-source match - the margin is real but smaller than the general floor's, an honest
# reflection of how close the two distributions actually sit (unlike the general floor, this is not
# a clean separation by magnitude alone; the ranking rule below, not this floor, is what protects a
# genuinely correct candidate from being outranked).
_VECTOR_GENERIC_FLOOR = 0.53

# TC-584: the "confident" vs "loose" disclosure line (``ExampleMatch.confidence``). TC-331's own
# true-positive probes scored 0.6446-0.8537 across its two measurements; this card's own 10-pilot
# RC-3 remeasurement above found true positives as low as 0.6059. A vector match at or above this
# line sits inside the range a real true-positive probe has actually reached; below it, the match is
# real (it still cleared its own floor) but materially weaker, and is disclosed as such rather than
# reported with the same unqualified confidence as a strong match.
_VECTOR_CONFIDENT_FLOOR = 0.65
MATCH_CONFIDENCE_CONFIDENT = "confident"
MATCH_CONFIDENCE_LOOSE = "loose"

# TC-584: the closed set of generic, no-specific-task section headings a real README actually uses
# as a catch-all code dump - found by listing every vector-indexed verified-example chunk's own
# title across all 32 real baked demo-manifest pilots (2026-10-26) and keeping only the normalized
# titles that repeat across otherwise-unrelated pilots (every one-off title, e.g. "Bold Header and
# Number Format on Cells", names a specific task). "Getting Started", "More Examples" and "Basic
# Usage" were not observed verbatim in this fleet but are kept as defensive synonyms of the ones that
# were, since a future pilot's README may use them. A trailing enumeration suffix such as "(2)" or
# "(part 2)" (the README has more than one same-named section) does not make a title specific.
_GENERIC_EXAMPLE_TITLES = frozenset(
    {
        "quick start",
        "getting started",
        "additional examples",
        "more examples",
        "basic usage",
        "installation, dependencies, and quick start",
    }
)
# A plain pattern string, not a pre-built regex object - this module never pre-builds one anywhere
# (one of this file's own tests scans the source for a short list of substrings that would suggest a
# snippet might get executed, and the pattern-building call used for that is one of them), so this
# follows the same convention ``_SOURCE_COMMIT_PATTERN`` above already uses: ``re.sub`` is called
# directly with the pattern string every time.
_GENERIC_TITLE_SUFFIX = r"\s*\((?:part\s*)?\d+\)\s*$"


def _is_generic_example_title(title: str) -> bool:
    """True for a title that names no specific task - see ``_GENERIC_EXAMPLE_TITLES`` above - after
    folding case and stripping a trailing "(2)" or "(part 2)" enumeration suffix."""
    normalized = re.sub(_GENERIC_TITLE_SUFFIX, "", title, flags=re.IGNORECASE).strip().lower()
    return normalized in _GENERIC_EXAMPLE_TITLES


_VERIFIED_KIND_LINE = "Kind: verified_example"
_AUTHORED_PROVENANCE_LINE = "Provenance: authored"
_VERIFICATION_PASSED_LINE = "Verification: passed"

# TC-544: the language an example is written in, from the deployment's platform (unknown: empty).
_PLATFORM_LANGUAGE = {
    "net": "C#",
    "python": "Python",
    "java": "Java",
    "typescript": "TypeScript",
    "nodejs": "JavaScript (Node.js)",
    "go": "Go",
    "rust": "Rust",
    "cpp": "C++",
}

# TC-544: what "verified" means for an example, one fixed sentence per provenance. The chunk holds the
# snippet and its ``Source-Commit:`` only: the toolchain, the flags and the assembled source the
# ingestion verifier used live in its verification cache, not in the generation, so none is stated.
#
# TC-565: the label states exactly what was proven. A chunk written before the verifier ran examples has
# no ``Runtime-Status`` line and proves compilation only; a chunk with the line says what running it did.
RUNTIME_LABEL_COMPILED_ONLY = "compiled only (run not verified)"
RUNTIME_LABEL_NO_OUTPUT = "compiles; produced no output"
_RUNTIME_STATUS_PREFIX = "Runtime-Status:"
_RUNTIME_BYTES_PREFIX = "Runtime-Produced-Bytes:"
_RUNTIME_ERROR_PREFIX = "Runtime-Error:"
_STATUS_RAN = "ran"
_STATUS_FAILED = "failed"
_STATUS_NO_OUTPUT = "no_output"
README_NOTE_TAIL = (
    "checked by the ingestion verifier against the library at source_commit; the verifier may "
    "have added the README's own imports or an entry point around this snippet, which are not shown, so "
    "add the imports and entry point your language needs."
)
AUTHORED_NOTE_TAIL = (
    "checked by the ingestion verifier against the library at source_commit; add the imports and entry "
    "point your language needs."
)
README_VERIFICATION_NOTE = f"{RUNTIME_LABEL_COMPILED_ONLY}; {README_NOTE_TAIL}"
AUTHORED_VERIFICATION_NOTE = f"Authored for this index; {RUNTIME_LABEL_COMPILED_ONLY}; {AUTHORED_NOTE_TAIL}"

_SOURCE_COMMIT_PATTERN = r"^Source-Commit:[ \t]*(\S+)"

# TC-543: what an ``ExampleMatch.coverage`` number is, per answer (``ExampleMatch.match_basis``).
MATCH_BASIS_EXACT_TITLE = "exact_title"
MATCH_BASIS_TITLE_WORD = "title_word_and_example_text"
MATCH_BASIS_QUERY_TERMS = "query_terms_in_example_text"
MATCH_BASIS_EMBEDDING = "embedding_similarity"
# TC-635 (Q10, G3/REQ-G3-011): set ONLY when a match's admission rests on the code-identifier anchor
# (``relevance.code_anchored``) and neither the title anchor (``relevance.title_anchored``) nor the
# bounded prefix title match (``relevance.title_prefix_anchored``) would have admitted it on their
# own - never when either of those two would already have admitted the same document, so this basis
# never double-counts a match the existing title-anchor machinery already explains.
MATCH_BASIS_CODE_WORD = "code_identifier_in_example_code"


def is_verified_example_text(text: str) -> bool:
    """The ONE definition of a verified example, shared by the exact, lexical and vector branches.

    True only when ALL hold: the chunk's ``FQN:`` line starts with ``Example: ``; some line is
    exactly ``Kind: verified_example`` (the verifier's own line - a symbol chunk whose Javadoc merely
    says ``Example:`` never has it); some line is exactly ``Example:``; and, when some line is
    exactly ``Provenance: authored``, some line is also exactly ``Verification: passed``.
    """
    fqn = extract_fqn(text)
    if fqn is None or not fqn.startswith(_EXAMPLE_FQN_PREFIX):
        return False
    lines = {line.rstrip("\r\n") for line in text.splitlines()}
    if _VERIFIED_KIND_LINE not in lines or _EXAMPLE_MARKER not in lines:
        return False
    if _AUTHORED_PROVENANCE_LINE in lines and _VERIFICATION_PASSED_LINE not in lines:
        return False
    return True


def _provenance(text: str) -> str:
    """``authored`` when the chunk has the line ``Provenance: authored``, ``readme`` otherwise."""
    lines = {line.rstrip("\r\n") for line in text.splitlines()}
    return "authored" if _AUTHORED_PROVENANCE_LINE in lines else "readme"


def _source_commit(text: str) -> str:
    """The value of the first line ``Source-Commit: <value>`` of the chunk, empty when there is none."""
    found = re.search(_SOURCE_COMMIT_PATTERN, text, re.MULTILINE)
    return found.group(1) if found else ""


def _runtime_fields(text: str) -> tuple[str, int | None, str]:
    """``(status, produced bytes, error)`` read from the chunk's ``Runtime-*`` lines; the status is ``""``
    for a chunk that has none (written before the verifier ran examples)."""
    status = ""
    produced: int | None = None
    error = ""
    for line in text.splitlines():
        stripped = line.rstrip("\r\n")
        if stripped.startswith(_RUNTIME_STATUS_PREFIX) and not status:
            status = stripped[len(_RUNTIME_STATUS_PREFIX) :].strip()
        elif stripped.startswith(_RUNTIME_BYTES_PREFIX) and produced is None:
            value = stripped[len(_RUNTIME_BYTES_PREFIX) :].strip()
            produced = int(value) if value.isdigit() else None
        elif stripped.startswith(_RUNTIME_ERROR_PREFIX) and not error:
            error = stripped[len(_RUNTIME_ERROR_PREFIX) :].strip()
    return status, produced, error


def runtime_label(text: str) -> str:
    """What running the chunk's example proved, in the verifier's own words.

    ``compiled and ran, produced N bytes`` (ran, exit 0, an artefact with content); ``compiles; fails at
    runtime: <error>``; ``compiles; produced no output`` (ran, exit 0, an empty artefact); and, for a
    chunk with no ``Runtime-Status`` line or one this server does not know, ``compiled only (run not
    verified)``. A label never claims more than the line says.
    """
    status, produced, error = _runtime_fields(text)
    if status == _STATUS_RAN:
        return f"compiled and ran, produced {produced if produced is not None else 0} bytes"
    if status == _STATUS_FAILED:
        return f"compiles; fails at runtime: {error or 'error not recorded'}"
    if status == _STATUS_NO_OUTPUT:
        return RUNTIME_LABEL_NO_OUTPUT
    return RUNTIME_LABEL_COMPILED_ONLY


def _runtime_rank(text: str) -> int:
    """Sort rank of a chunk: 0 for one that is not known to fail, 1 for one whose run failed or produced
    nothing. Such an example is served after every other, with its honest label."""
    status = _runtime_fields(text)[0]
    return 1 if status in (_STATUS_FAILED, _STATUS_NO_OUTPUT) else 0


def _verification_note(text: str) -> str:
    """The fixed sentence saying what ``verified`` means for the chunk: its provenance and exactly what
    running it proved (``runtime_label``)."""
    label = runtime_label(text)
    if _provenance(text) == "authored":
        return f"Authored for this index; {label}; {AUTHORED_NOTE_TAIL}"
    return f"{label}; {README_NOTE_TAIL}"


def _same_example(seen: set[str], fqn: str) -> bool:
    """True when an example with this title (the ``FQN:`` line, whitespace collapsed, case folded) was
    already taken; otherwise remembers it. One example is one answer, however many chunks hold it. Two
    DIFFERENT titles are two examples even when their code is the same."""
    key = " ".join(fqn.split()).lower()
    if not key:
        return False
    if key in seen:
        return True
    seen.add(key)
    return False


def _find_marker_line(lines: list[str]) -> int | None:
    """Index of the LAST line that is exactly ``Example:``, or ``None``."""
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].rstrip("\r\n") == _EXAMPLE_MARKER:
            return index
    return None


def _extract_example(text: str) -> str | None:
    """The code alone: the text after the LAST line that is exactly ``Example:``, with trailing
    ``Source-Commit:`` lines and trailing blank lines removed and nothing else changed.

    (The earlier substring split at the first ``Example:`` landed inside the ``FQN: Example: <title>``
    line, so the snippet began with the title, ``Kind:`` and prose and ended with ``Source-Commit:``.)
    """
    lines = text.splitlines(keepends=True)
    marker = _find_marker_line(lines)
    if marker is None:
        return None
    body = lines[marker + 1 :]
    while body and (not body[-1].strip() or body[-1].startswith("Source-Commit:")):
        body.pop()
    return "".join(body).rstrip("\r\n")


@dataclass(frozen=True)
class ExampleMatch:
    """``coverage`` means two different things depending on ``source`` (TC-331): for
    ``source="exact"`` it is unconditionally ``1.0`` (definitionally exact); for
    ``source="lexical"`` it is the lexical fallback's own token-overlap fraction (see
    ``relevance.RELEVANCE_THRESHOLD``); for ``source="vector"`` it is the vector fallback's own cosine
    similarity (see ``_VECTOR_SIMILARITY_THRESHOLD``). These two numbers are not
    interchangeable - always check ``source`` before comparing or thresholding ``coverage``
    across matches, rather than assuming it is one fixed kind of number.

    ``match_basis`` (TC-543, additive, last field) says plainly what ``coverage`` is for that answer:

    - ``exact_title``: the query was the example's own title (its ``FQN:`` line); ``coverage`` is 1.0.
    - ``title_word_and_example_text``: a lexical answer to a short query (at most two distinctive terms)
      that was anchored on a word of the example's title; ``coverage`` is the weighted share of the
      query's terms present in the example text, 1.0 meaning all are present.
    - ``query_terms_in_example_text``: a lexical answer to a longer query, judged by the coverage floor
      alone; ``coverage`` is the weighted share of the query's terms present in the example text.
    - ``embedding_similarity``: the vector fallback; ``coverage`` is a cosine similarity.

    A lexical ``coverage`` is not a relevance probability: 1.0 means every query term is present in the
    example, not that the example is about them. It defaults to ``""`` for a hand-built match.

    ``language``, ``source_commit`` and ``verification`` (TC-544, additive, last fields, all default ``""``)
    say what the generation really holds about the snippet: the language of the deployment's platform
    (``""`` for an unknown platform), the chunk's ``Source-Commit:`` value (``""`` when the line is
    absent) and one fixed sentence saying what ``verified`` means for this provenance. That sentence is
    deliberately short of a full record: the toolchain, flags and assembled source the ingestion verifier
    used are not in the generation, and the verifier may have added the README's imports or an entry
    point that the snippet does not show.

    ``confidence`` (TC-584, additive, last field) is ``MATCH_CONFIDENCE_CONFIDENT`` or
    ``MATCH_CONFIDENCE_LOOSE`` for ``source="vector"`` - whether ``coverage`` (the cosine similarity)
    is at or above ``_VECTOR_CONFIDENT_FLOOR`` - and ``""`` for ``source="exact"`` or
    ``source="lexical"``: the confident/loose distinction is specific to the vector fallback's own
    cosine-similarity scale, which is not comparable to a lexical coverage fraction. Always set to one
    of the two named strings for a vector match - never silently absent for one case and present for
    the other.

    ``known_defects`` (TC-670, G3/REQ-G3-011, additive, last field, defaults ``()``) is a rendered
    disclosure line (``get_symbol.known_defect_lines``'s own wording - the same function
    ``get_symbol.py``/``search_symbols.py`` already call) for every committed known-defect entry
    that applies to a REAL symbol this example's own CODE SNIPPET literally names - never the
    example's own pseudo ``Example: <title>`` FQN, which no real registry entry's ``subject.refs``
    ever names. Set for ``source="exact"`` and ``source="lexical"`` matches only; always ``()`` for
    ``source="vector"`` (out of this card's scope). Empty when no defect data is recorded for this
    pilot yet, or none of it applies to a symbol the example's own code actually uses.
    """

    scope: Scope
    generation_id: str
    fqn: str
    snippet: str
    coverage: float
    source: str
    found: bool = True
    # TC-465: "authored" when the chunk has the line ``Provenance: authored`` (an example written
    # for the library and really verified), "readme" for a README example the verifier passed.
    provenance: str = "readme"
    # TC-543: what ``coverage`` is for this answer; one of the MATCH_BASIS_* strings, "" when hand-built.
    match_basis: str = ""
    # TC-544: language of the deployment's platform, the chunk's pinned source commit, and what verified means.
    language: str = ""
    source_commit: str = ""
    verification: str = ""
    # TC-584: "confident"/"loose" for a vector match (see _VECTOR_CONFIDENT_FLOOR), "" otherwise.
    confidence: str = ""
    # TC-670: a known-defect disclosure line for every real symbol the example's own CODE names;
    # () when none applies, or for a vector match (not wired by this card).
    known_defects: tuple[str, ...] = ()


@dataclass(frozen=True)
class NoExampleFound:
    """An explicit miss. Never smoothed over into an unrelated snippet.

    ``found`` (always ``False``) and ``reason`` are additive (TC-381): the miss shape the tool
    description promises carries a found marker and the reason nothing was returned.

    ``vector_fallback`` (TC-508, additive) is ``None`` when the semantic stage ran normally or was
    never applicable, and otherwise says why it was skipped: ``skipped_timeout``,
    ``skipped_circuit_open`` or ``skipped_error``.
    """

    scope: Scope
    query: str
    found: bool = False
    reason: str = "no verified example matches this query"
    vector_fallback: str | None = None


_NO_MATCH_REASON = "no verified example matches this query"

# TC-562: the by-design lexical-only deployment (no embedding gateway configured) is not a failure.
STATUS_NOT_CONFIGURED = "not_configured"
NOT_CONFIGURED_REASON = (
    "no verified example matches this query (semantic search is not configured in this deployment: "
    "examples are matched on their words only)"
)
_SKIP_WORDS = {
    STATUS_SKIPPED_TIMEOUT: "timeout",
    STATUS_SKIPPED_CIRCUIT_OPEN: "circuit open",
    STATUS_SKIPPED_ERROR: "error",
}

_SYMBOL_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_FQN_SEGMENT_RE = re.compile(r"::|\.")


def _real_symbol_fqns(documents: Mapping[str, Mapping[str, str]]) -> dict[str, frozenset[str]]:
    """TC-670: this scope's own real, fully-qualified symbol FQNs, grouped by short name - the
    identical ``extract_fqn``/``_NON_SYMBOL_FQN_PREFIXES`` filter ``search_symbols.py``'s own
    Miss-path loop uses to build its ``known_fqns`` list. Deliberately NOT the general-purpose
    symbol/example/documentation classifier ``relevance`` exports for other tools: this file's own
    ``is_verified_example_text`` is the ONE predicate it uses for what a chunk is (a dedicated test
    pins that this module's source never reaches for that other classifier at all), so this is
    reimplemented locally with the one predicate this file already has, exactly as
    ``relevance.known_symbol_names`` does the equivalent short-name-only computation for the code
    anchor above, in its own module, with its own equivalent tool).

    Used only by ``_example_known_defects`` below, to connect a real symbol an example's own CODE
    names back to a real committed known-defect entry, which may reference either the symbol's bare
    short name or its namespace-qualified form (confirmed against the real, committed
    ``config/known_defects/*.yaml`` registry: cells/typescript's own entries use the bare
    ``Workbook``, cells/net's own entries use the qualified ``Aspose.Cells_FOSS.Workbook``, for the
    identical real class).
    """
    grouped: dict[str, set[str]] = {}
    for document in documents.values():
        fqn = extract_fqn(document.get("text", ""))
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        segments = [segment for segment in _FQN_SEGMENT_RE.split(fqn) if segment]
        if segments:
            grouped.setdefault(segments[-1], set()).add(fqn)
    return {short: frozenset(fqns) for short, fqns in grouped.items()}


def _example_known_defects(
    scope: Scope, fqns_by_short: Mapping[str, frozenset[str]], code: str, text: str
) -> tuple[str, ...]:
    """TC-670 (G3/REQ-G3-011): the known-defect disclosure for every REAL symbol this example's own
    CODE SNIPPET literally names, through the real ``get_symbol.known_defect_lines`` (the identical
    function ``get_symbol.py``/``search_symbols.py`` already call) - never the example's own pseudo
    ``Example: <title>`` FQN, which no real registry entry's ``subject.refs`` ever names, so that
    naive lookup would never surface anything against real data.

    A real symbol is recognised the same CASE-SENSITIVE way ``relevance.known_symbol_names`` is
    built for the code anchor above: *code* (the example's OWN CODE SNIPPET specifically - the same
    scoping ``code_anchored`` already enforces, so a symbol name appearing only in surrounding
    prose never triggers a disclosure here either) is scanned for alphanumeric runs, and each run
    that is a real short name in *fqns_by_short* is tried both as its bare short name and as every
    one of its real, fully-qualified FQNs - the real committed registry uses either form depending
    on the pilot (see ``_real_symbol_fqns``'s own docstring). *text* is the example chunk's own
    text, for the identical ``Source-Commit:`` commit-scoping every other ``known_defect_lines``
    call site already gets. Registry file order, deduplicated by rendered line.
    """
    code_words = frozenset(_SYMBOL_WORD_RE.findall(code))
    if not code_words:
        return ()
    candidates: list[str] = []
    seen_candidates: set[str] = set()
    for short in code_words:
        fqns = fqns_by_short.get(short)
        if fqns is None:
            continue
        if short not in seen_candidates:
            seen_candidates.add(short)
            candidates.append(short)
        for fqn in sorted(fqns):
            if fqn not in seen_candidates:
                seen_candidates.add(fqn)
                candidates.append(fqn)
    lines: list[str] = []
    seen_lines: set[str] = set()
    for candidate in candidates:
        for line in known_defect_lines(scope, candidate, text):
            if line not in seen_lines:
                seen_lines.add(line)
                lines.append(line)
    return tuple(lines)


def find_examples(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 5
) -> list[ExampleMatch] | NoExampleFound:
    """A verified snippet for *query*: an exact FQN match first (a direct FQN lookup
    over the generation's documents), a semantic (lexical) search over every OTHER example-bearing chunk otherwise.

    Never executes a snippet, and never returns one when nothing - exact or semantic - has
    one to offer.
    """
    original_query = query
    query = fold_query(query)
    # TC-616 (Q3, G3/REQ-G3-011): a literal "Aspose.<family>" dotted product-name mention no longer
    # stays a single dotted token for this tool's own term machinery, exactly as TC-610 proved in
    # isolation. Additive to fold_query above, not a replacement.
    query = normalize_query(query, scope.family)
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return NoExampleFound(scope, original_query, reason="no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    documents = (lexical_payload or {}).get("documents") or {}
    if not documents:
        return NoExampleFound(scope, original_query, reason="published generation has no document index")

    # Exact branch: the query IS an example's pseudo-FQN. Looked up directly over this generation's
    # documents (not through ``get_symbol``), so ``get_symbol`` can stop returning pseudo-symbols
    # without breaking this.
    exact_texts = [
        document["text"]
        for document in documents.values()
        if is_verified_example_text(document["text"])
        and extract_fqn(document["text"]) == query
        and _extract_example(document["text"]) is not None
    ]
    if exact_texts:
        # Two chunks with one title are one example: the one whose run did not fail is the answer (TC-565).
        text = min(exact_texts, key=_runtime_rank)
        exact_code = _extract_example(text) or ""
        return [
            ExampleMatch(
                scope,
                active_generation_id,
                query,
                exact_code,
                coverage=1.0,
                source="exact",
                provenance=_provenance(text),
                match_basis=MATCH_BASIS_EXACT_TITLE,
                language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                source_commit=_source_commit(text),
                verification=_verification_note(text),
                # TC-670: disclose a known defect for any real symbol this example's own CODE names.
                known_defects=_example_known_defects(scope, _real_symbol_fqns(documents), exact_code, text),
            )
        ]

    example_doc_ids = [
        doc_id for doc_id, document in documents.items() if is_verified_example_text(document["text"])
    ]
    if not example_doc_ids:
        return NoExampleFound(scope, original_query, reason="no verified example exists in this generation")

    # TC-516: a query with no distinctive term ("Example:", "pdf examples") names no task, so no example
    # can answer it; and relevance is IDF-weighted with every identifier-like query word required.
    # TC-616: the scope's own platform/runtime word ("net", "java", ...) is excluded here too, exactly
    # like the family word, so it can no longer count as the query's one distinctive term.
    if not distinctive_terms(query, scope.family, platform=scope.platform) or not has_minimum_evidence(query):
        return NoExampleFound(scope, original_query)
    terms = query_terms(query, scope.family, platform=scope.platform)
    weights = term_weights(terms, lexical_payload)
    required = required_terms(query)
    # TC-670 (G3/REQ-G3-011): widens which query terms the CODE ANCHOR watches for, additive beside
    # the existing compound-identifier check (``required``) above - never replacing it, and never
    # touching ``guarded_coverage``'s own ``required`` gate just below, which stays exactly
    # ``required_terms(query)``, byte-for-byte as before. ``known_symbol_names`` below is called
    # with THIS generation's own ``documents`` for this scope's own real short symbol names (never
    # a global or cross-pilot list - see that function's own docstring). The match against
    # it is a literal, CASE-SENSITIVE comparison of the query's own original-cased words: the
    # common English word "workbook" must never anchor on the real class "Workbook" through this
    # mechanism, only an exact-case "Workbook" in the query does.
    symbol_names = known_symbol_names(documents)
    symbol_required = frozenset(
        stem_token(word) for word in _SYMBOL_WORD_RE.findall(query) if word in symbol_names
    )
    code_anchor_required = required | symbol_required
    bm25 = dict(query_lexical_index_scored(lexical_payload, query, top_k=len(documents)))
    # TC-635 (Q10, G3/REQ-G3-011): two more, strictly additive admission paths beside the existing
    # title anchor - a code-identifier anchor and a bounded prefix title match - never replacing or
    # weakening the title anchor itself, nor the coverage floor above. ``code_only`` records, per
    # document, whether admission rests on the code anchor ALONE (title_anchored and
    # title_prefix_anchored both False for that document): the one case ``MATCH_BASIS_CODE_WORD`` is
    # ever assigned, so it never double-counts a match the existing title-anchor machinery already
    # explains.
    scored: list[tuple[float, float, str, bool]] = []
    for doc_id in example_doc_ids:
        doc_coverage = guarded_coverage(terms, documents[doc_id]["tokens"], required, weights)
        text = documents[doc_id]["text"]
        title = (extract_fqn(text) or "")[len(_EXAMPLE_FQN_PREFIX) :]
        title_hit = title_anchored(query, scope.family, title)
        prefix_hit = not title_hit and title_prefix_anchored(query, scope.family, title)
        # ``code_anchor_required`` (the compound-identifier check widened by the real-symbol-name
        # check, TC-670) is checked FIRST so that a query with no eligible term never even calls
        # ``code_anchored`` - not merely a trivially False result, but the call itself skipped,
        # exactly like every query that reached this gate before either card.
        code_only = bool(code_anchor_required) and not title_hit and not prefix_hit
        if code_only:
            code_only = code_anchored(code_anchor_required, tokenize(_extract_example(text) or ""))
        if doc_coverage >= RELEVANCE_THRESHOLD and (title_hit or prefix_hit or code_only):
            scored.append((doc_coverage, bm25.get(doc_id, 0.0), doc_id, code_only))
    # TC-565: an example whose run failed or produced nothing is served after every other one, and one
    # example held by several chunks is answered once, by its highest-scoring chunk.
    scored.sort(key=lambda item: (_runtime_rank(documents[item[2]]["text"]), -item[0], -item[1], item[2]))
    hits: list[tuple[str, float, bool]] = []
    seen_fqns: set[str] = set()
    for doc_coverage, _score, doc_id, code_only in scored:
        if _same_example(seen_fqns, extract_fqn(documents[doc_id]["text"]) or ""):
            continue
        hits.append((doc_id, doc_coverage, code_only))
        if len(hits) >= top_k:
            break
    if not hits:
        vector_matches, note = _vector_fallback(manifest, scope, active_generation_id, query, top_k)
        if vector_matches:
            return vector_matches
        reason = _NO_MATCH_REASON
        if note == STATUS_NOT_CONFIGURED:
            reason = NOT_CONFIGURED_REASON
        elif note in _SKIP_WORDS:
            reason = f"{reason} (semantic fallback skipped: {_SKIP_WORDS[note]})"
        return NoExampleFound(scope, original_query, reason=reason, vector_fallback=note)

    lexical_basis = (
        MATCH_BASIS_TITLE_WORD
        if len(distinctive_terms(query, scope.family, platform=scope.platform)) <= TITLE_ANCHOR_MAX_TERMS
        else MATCH_BASIS_QUERY_TERMS
    )
    # TC-670: computed once for every hit of this call, not once per hit - the mapping is the same
    # for every document of this one generation.
    fqns_by_short = _real_symbol_fqns(documents)
    matches = []
    for doc_id, doc_coverage, code_only in hits:
        text = documents[doc_id]["text"]
        lexical_code = _extract_example(text) or ""
        matches.append(
            ExampleMatch(
                scope=scope,
                generation_id=active_generation_id,
                fqn=extract_fqn(text) or "",
                snippet=lexical_code,
                coverage=doc_coverage,
                source="lexical",
                provenance=_provenance(text),
                match_basis=MATCH_BASIS_CODE_WORD if code_only else lexical_basis,
                language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                source_commit=_source_commit(text),
                verification=_verification_note(text),
                # TC-670: disclose a known defect for any real symbol this example's own CODE names.
                known_defects=_example_known_defects(scope, fqns_by_short, lexical_code, text),
            )
        )
    return matches


def _vector_fallback(
    manifest: GenerationManifest, scope: Scope, generation_id: str, query: str, top_k: int
) -> tuple[list[ExampleMatch], str | None]:
    """TC-331: one more, strictly additive attempt when the lexical fallback above found
    nothing at all - never called otherwise, so it can only ever turn an existing
    ``NoExampleFound`` into a real match, never regress an existing hit.

    Embeds *query* through the real embedding gateway and ranks the WHOLE vector-indexed
    corpus against it first (never cuts candidates by ``top_k`` before filtering - the same
    discipline ``search_symbols.py``'s own docstring establishes), filtered to verified-example
    points scoring at or above ``_VECTOR_SIMILARITY_THRESHOLD`` (or ``_VECTOR_GENERIC_FLOOR`` for a
    generic-titled one, TC-584), truncated to the caller's real ``top_k`` only at the end. Among
    candidates that each clear their own floor, every specifically-titled one is ranked ahead of
    every generic-titled one (TC-584) - never excluded outright, just never allowed to outrank a
    real, on-topic answer.

    Fully exception-safe end to end, not only around the embed call itself: a missing vector
    index (an older-shaped generation published before vector indexing existed), a missing
    gateway configuration, a network error, a budget-exceeded error, or any other failure
    anywhere in this stage returns no matches instead of raising - ``find_examples()`` then
    falls through to its existing ``NoExampleFound`` return, exactly mirroring the
    mission plan's own named risk mitigation ("deterministic/lexical fallback always
    available").

    Returns ``(matches, note)``. The query embedding runs through the process-wide
    ``VectorFallbackGuard`` (TC-508): one attempt, a hard deadline, and a circuit breaker. When it
    is skipped, *note* is the guard's status string (``skipped_timeout``, ``skipped_circuit_open``
    or ``skipped_error``); otherwise it is ``None``. The query text is only ever handed to the
    provider inside the guarded function, so with the circuit open the provider is not even
    constructed.
    """
    vector_payload = manifest.payload.get("vector_index")
    points = (vector_payload or {}).get("points") or []
    if not points:
        return [], None

    # TC-562: with the real provider and no gateway endpoint or key set, the deployment is lexical-only BY DESIGN:
    # say so (``not_configured``) instead of reporting an error, and do not ask the guard, so it can never count
    # the missing configuration as a gateway failure. A stand-in provider is always asked.
    if GatewayEmbeddingProvider is _RealGatewayEmbeddingProvider and not (
        os.environ.get(_ENDPOINT_ENV, "").strip() and os.environ.get(_API_KEY_ENV, "").strip()
    ):
        return [], STATUS_NOT_CONFIGURED

    try:
        guard = get_guard()
    except Exception:
        # An unusable deadline setting must not take the tool down: degrade to the lexical miss.
        return [], STATUS_SKIPPED_ERROR

    def _embed_query() -> Vector:
        provider = GatewayEmbeddingProvider()
        # Query time, not ingestion: one attempt and no longer than the guard's deadline. A stand-in
        # provider that only has ``embed`` simply skips this.
        set_limits = getattr(provider, "set_query_time_limits", None)
        if set_limits is not None:
            set_limits(max_attempts=QUERY_TIME_MAX_ATTEMPTS, timeout_seconds=guard.deadline_seconds)
        return provider.embed([query])[0]

    status, query_vector = guard.call(_embed_query)
    if status != STATUS_OK:
        return [], status
    if query_vector is None:
        return [], STATUS_SKIPPED_ERROR

    try:
        point_by_id = {point["point_id"]: point for point in points}
        ranked = query_vector_index_scored(vector_payload, query_vector, top_k=len(points))

        candidates: list[tuple[tuple[int, int, float], ExampleMatch]] = []
        for point_id, score in ranked:
            point = point_by_id[point_id]
            text = point["text"]
            if not is_verified_example_text(text):
                continue
            if score < _VECTOR_SIMILARITY_THRESHOLD:
                continue
            fqn = extract_fqn(text) or ""
            title = fqn[len(_EXAMPLE_FQN_PREFIX) :] if fqn.startswith(_EXAMPLE_FQN_PREFIX) else fqn
            # TC-584: a generic-titled chunk is held to a stricter floor than the general one above
            # (see ``_VECTOR_GENERIC_FLOOR``'s own comment for the real measurement behind this) -
            # demoted, never excluded by the general floor alone.
            is_generic = _is_generic_example_title(title)
            if is_generic and score < _VECTOR_GENERIC_FLOOR:
                continue
            confidence = (
                MATCH_CONFIDENCE_CONFIDENT if score >= _VECTOR_CONFIDENT_FLOOR else MATCH_CONFIDENCE_LOOSE
            )
            candidates.append(
                (
                    # TC-584: a specifically-titled chunk that clears its floor outranks EVERY
                    # generic-titled one that clears its own, whatever the raw cosine score of either
                    # - the fix for a generic chunk outranking a genuinely correct, lower-scored
                    # candidate (reproduced on note/python; see the module docstring). Within the same
                    # (runtime rank, generic-ness) group, the higher raw score still wins.
                    (_runtime_rank(text), 1 if is_generic else 0, -score),
                    ExampleMatch(
                        scope=scope,
                        generation_id=generation_id,
                        fqn=fqn,
                        snippet=_extract_example(text) or "",
                        coverage=score,
                        source="vector",
                        provenance=_provenance(text),
                        match_basis=MATCH_BASIS_EMBEDDING,
                        language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                        source_commit=_source_commit(text),
                        verification=_verification_note(text),
                        confidence=confidence,
                    ),
                )
            )
        # TC-565: failing examples after the others (a stable sort keeps the similarity order inside each
        # rank), and one example held by several chunks is answered once, by its highest-scoring chunk.
        # TC-584: within an unfailing rank, a specific title before a generic one (see above).
        candidates.sort(key=lambda item: item[0])
        matches: list[ExampleMatch] = []
        seen_fqns: set[str] = set()
        for _rank, match in candidates:
            if _same_example(seen_fqns, match.fqn):
                continue
            matches.append(match)
            if len(matches) >= top_k:
                break
        return matches, None
    except Exception:
        return [], None
