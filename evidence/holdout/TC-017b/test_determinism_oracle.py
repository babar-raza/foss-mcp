"""HOLDOUT oracle for TC-017b — supervisor-authored, not named by the card.

These three tools are the ones an agent will trust most literally, so their
failure mode is the worst available: a plausible answer to a question about a
symbol that does not exist. `get_symbol` returning a *near* match is
indistinguishable, to the caller, from a correct answer.

A card's own suite typically probes this with one obviously-absent name like
"NoSuchClass". That proves almost nothing. What actually tempts a retrieval
implementation into guessing is a name that is *nearly* right: correct prefix,
correct casing, a trailing character different, a real symbol's name with a
fabricated member appended. Those are the queries an agent actually sends after
half-remembering an API.

So this oracle attacks with near-misses drawn from a REAL symbol in the published
generation, and pairs every one with the anti-vacuity check that the real name
still resolves — otherwise "always NotFound" would score perfectly.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
for p in (str(WT), str(WT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from foss_mcp.indexing.generation_manifest import GenerationManifestStore  # noqa: E402
from foss_mcp.indexing.publisher import publish_generation  # noqa: E402
from foss_mcp.mcp.routing import Scope  # noqa: E402
from foss_mcp.mcp.tools.find_examples import find_examples  # noqa: E402
from foss_mcp.mcp.tools.get_symbol import NotFound, get_symbol  # noqa: E402
from foss_mcp.mcp.tools.list_members import list_members  # noqa: E402
from foss_mcp.normalization.chunker import chunk_document  # noqa: E402
from foss_mcp.normalization.document_schema import (  # noqa: E402
    Provenance,
    SourceKind,
    make_document,
)

FIXTURE = WT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"
PDF_NET = Scope(family="pdf", platform="net")
REAL_FQN = "Aspose.Pdf.AFRelationship"


class _Provider:
    dimension = 8

    def embed(self, texts):
        return [[float((len(t) + i) % 5) for i in range(self.dimension)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """Publish real pdf/net symbols in the FQN-line shape these tools read."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for entry in fixture["types"][:20]:
        fqn = entry.get("class_import") or entry.get("name", "")
        members = [m.get("name", "") for m in (entry.get("methods") or [])]
        members += [m.get("name", "") for m in (entry.get("enum_members") or [])]
        block = [f"## {fqn}", "", f"FQN: {fqn}", f"Kind: {entry.get('kind', '')}"]
        if members:
            block += ["Members:"] + [f"- {m}" for m in members if m]
        block += ["", "```csharp", f"var x = new {fqn}();", "```"]
        sections.append("\n".join(block))

    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"], commit=fixture["source_commit"], path="api_surface.json"
        ),
        title="pdf/net symbols",
        body="\n\n".join(sections),
    )
    chunks = chunk_document(doc)
    st = GenerationManifestStore(tmp_path_factory.mktemp("m") / "manifests")
    lease = st.acquire_lease("pdf::net::self_extracted", "holdout", "pending")
    publish_generation(
        st,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=_Provider(),
        lease=lease,
    )
    return st


def test_the_real_symbol_resolves(store):
    """Anti-vacuity. Every assertion below is worthless if nothing ever resolves."""
    got = get_symbol(store, PDF_NET, REAL_FQN)
    assert not isinstance(got, NotFound), f"a symbol that IS published did not resolve: {got}"
    assert got.fqn == REAL_FQN


# Near-misses built from a REAL symbol - the queries an agent sends after
# half-remembering an API, and the ones a ranked search is most tempted by.
NEAR_MISSES = [
    REAL_FQN + "s",  # plausible plural
    REAL_FQN[:-1],  # one character short
    REAL_FQN.lower(),  # wrong casing
    REAL_FQN.replace("Aspose.Pdf", "Aspose.PDF"),  # wrong namespace casing
    REAL_FQN + ".NoSuchMember",  # real type, fabricated member
    REAL_FQN.split(".")[-1],  # bare name, no namespace
    "Aspose.Pdf.AFRelationships",  # near-identical neighbour
    " " + REAL_FQN,  # leading whitespace
    REAL_FQN + " ",  # trailing whitespace
]


@pytest.mark.parametrize("wrong", NEAR_MISSES, ids=range(len(NEAR_MISSES)))
def test_get_symbol_never_answers_a_near_miss(store, wrong):
    """The hallucination case. A near match returned as an answer is worse than
    no answer, because the caller cannot tell the difference."""
    got = get_symbol(store, PDF_NET, wrong)
    assert isinstance(got, NotFound), (
        f"get_symbol({wrong!r}) returned a signature instead of NotFound: {got!r}. Matching must be "
        f"exact - a ranked or substring fallback here is indistinguishable from fabrication."
    )


def test_a_returned_signature_belongs_to_the_name_that_was_asked_for(store):
    """Even on a hit, the answer must be about the question."""
    got = get_symbol(store, PDF_NET, REAL_FQN)
    assert got.fqn == REAL_FQN, f"asked for {REAL_FQN!r}, answered about {got.fqn!r}"


def test_list_members_does_not_invent_children_for_an_absent_type(store):
    real = list_members(store, PDF_NET, REAL_FQN)
    assert not isinstance(real, NotFound), "a real type reported no members at all"

    for wrong in (REAL_FQN + "s", "Aspose.Pdf.TotallyMadeUp"):
        got = list_members(store, PDF_NET, wrong)
        assert isinstance(got, NotFound), f"list_members({wrong!r}) invented children: {got!r}"


def test_find_examples_prefers_the_exact_symbol_over_a_ranked_neighbour(store):
    """Exact-match-first is the contract; a semantic fallback must not outrank it."""
    hits = find_examples(store, PDF_NET, REAL_FQN)
    assert not isinstance(hits, tuple) or hits, "no examples at all for a symbol that has one"
    rendered = repr(hits)
    assert REAL_FQN in rendered, (
        f"find_examples({REAL_FQN!r}) returned examples that never mention it: {rendered[:200]}"
    )


def test_nothing_here_executes_a_snippet(store):
    """Plan rule: no snippet is ever executed inside the serving process.

    Sentinel check - the published example would raise on import if anything
    evaluated it, so reaching the assertion at all is the evidence.
    """
    import builtins

    original = builtins.eval
    calls = []

    def _tripwire(*a, **k):
        calls.append(a)
        raise AssertionError("serving path called eval() on published content")

    builtins.eval = _tripwire
    try:
        find_examples(store, PDF_NET, REAL_FQN)
        get_symbol(store, PDF_NET, REAL_FQN)
    finally:
        builtins.eval = original
    assert not calls, "a snippet was evaluated at query time"
