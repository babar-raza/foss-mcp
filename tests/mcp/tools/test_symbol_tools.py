"""The three deterministic symbol tools (get_symbol, list_members, find_examples) against a
real published pdf/net self-extracted generation.

Deterministic by design: get_symbol never guesses and never returns a near match on a miss;
find_examples is exact-match first with a semantic fallback, over verified snippets only, and
never executes a snippet at query time.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import NoExampleFound, find_examples
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FIXTURE = Path(__file__).parents[2] / "fixtures" / "pdf_net" / "api_surface.json"
PDF_NET_SCOPE = Scope(family="pdf", platform="net")

# The two real symbols this suite publishes: one WITH a verified example, one without, so
# find_examples's exact-match-first behaviour and its "no example here" honesty are both
# provable against real published content.
WITH_EXAMPLE = "AnnotationCollection"
WITHOUT_EXAMPLE = "Border"


def _format_signature(entry: dict) -> str:
    def method_line(method: dict) -> str:
        params = ", ".join(
            f"{p.get('type', '')} {p.get('name', '')}".strip() for p in method.get("params", [])
        )
        return f"{method['name']}({params})"

    def property_line(prop: dict) -> str:
        return f"{prop['name']}: {prop.get('type', '')}"

    lines = [f"FQN: {entry['name']}", f"Kind: {entry['kind']}"]
    if entry.get("methods"):
        lines.append("Methods:")
        lines.extend(f"  - {method_line(m)}" for m in entry["methods"])
    if entry.get("properties"):
        lines.append("Properties:")
        lines.extend(f"  - {property_line(p)}" for p in entry["properties"])
    if entry["name"] == WITH_EXAMPLE:
        lines.append("Example:")
        lines.append(
            "var annotations = page.Annotations;\n"
            'annotations.AddTextAnnotation(rect, "A note", "Reviewer", true);'
        )
    return "\n".join(lines)


def _publish_pdf_net_symbols(store: GenerationManifestStore) -> tuple[str, dict]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_name = {t["name"]: t for t in fixture["types"]}
    entries = [by_name[WITH_EXAMPLE], by_name[WITHOUT_EXAMPLE]]
    body = "\n\n".join(f"## {entry['name']}\n\n{_format_signature(entry)}" for entry in entries)
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"], commit=fixture["source_commit"], path="api_surface.json"
        ),
        evidence_refs=(),
        title="pdf/net API surface",
        body=body,
    )
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    generation_id = publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return generation_id, by_name


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def test_get_symbol_returns_the_real_signature_for_a_known_fqn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, by_name = _publish_pdf_net_symbols(store)

    result = get_symbol(store, PDF_NET_SCOPE, WITHOUT_EXAMPLE)

    assert isinstance(result, SymbolSignature)
    assert result.fqn == WITHOUT_EXAMPLE
    assert result.kind == by_name[WITHOUT_EXAMPLE]["kind"]
    real_method_names = {m["name"] for m in by_name[WITHOUT_EXAMPLE]["methods"]}
    returned_method_names = {method.split("(", 1)[0] for method in result.methods}
    assert real_method_names <= returned_method_names


def test_get_symbol_returns_an_explicit_not_found_never_a_near_match(tmp_path: Path) -> None:
    """A near-miss of a real FQN (a plausible typo/suffix) must NOT resolve - proving this is
    exact matching, not fuzzy matching."""
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    result = get_symbol(store, PDF_NET_SCOPE, WITHOUT_EXAMPLE + "Extended")

    assert isinstance(result, NotFound)
    assert result.fqn == WITHOUT_EXAMPLE + "Extended"


def test_list_members_returns_the_real_children(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, by_name = _publish_pdf_net_symbols(store)

    members = list_members(store, PDF_NET_SCOPE, WITHOUT_EXAMPLE)

    assert isinstance(members, tuple) and members
    real_property_names = {p["name"] for p in by_name[WITHOUT_EXAMPLE]["properties"]}
    returned_names = {member.split(":", 1)[0].split("(", 1)[0] for member in members}
    assert real_property_names <= returned_names


def test_list_members_propagates_the_identical_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    result = list_members(store, PDF_NET_SCOPE, "TotallyMadeUpClassName")

    assert result == get_symbol(store, PDF_NET_SCOPE, "TotallyMadeUpClassName")
    assert isinstance(result, NotFound)


def test_find_examples_prefers_an_exact_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    result = find_examples(store, PDF_NET_SCOPE, WITH_EXAMPLE)

    assert isinstance(result, list) and len(result) == 1
    assert result[0].fqn == WITH_EXAMPLE
    assert "AddTextAnnotation" in result[0].snippet


def test_find_examples_falls_back_to_semantic_search(tmp_path: Path) -> None:
    """A query that is not itself an FQN still finds the verified snippet by lexical search."""
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    result = find_examples(store, PDF_NET_SCOPE, "text annotation")

    assert isinstance(result, list) and result
    assert any(match.fqn == WITH_EXAMPLE for match in result)


def test_find_examples_returns_an_explicit_miss_when_nothing_has_an_example(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    # WITHOUT_EXAMPLE resolves as a real symbol (exact match) but carries no Example: block,
    # and nothing else in this generation mentions its distinguishing terms either.
    result = find_examples(store, PDF_NET_SCOPE, WITHOUT_EXAMPLE)

    assert isinstance(result, NoExampleFound)


def test_no_snippet_is_ever_executed_at_query_time(tmp_path: Path) -> None:
    """A snippet that would raise or have a visible side effect if actually run must come
    back unchanged as inert text - find_examples only ever returns it, never runs it."""
    store = _store(tmp_path)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    entry = fixture["types"][0]
    hostile_snippet = "raise RuntimeError('find_examples executed this snippet')"
    body = f"## {entry['name']}\n\nFQN: {entry['name']}\nKind: {entry['kind']}\nExample:\n{hostile_snippet}"
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository=fixture["source_repository"], commit=fixture["source_commit"]),
        evidence_refs=(),
        title="pdf/net API surface",
        body=body,
    )
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )

    result = find_examples(store, PDF_NET_SCOPE, entry["name"])

    assert isinstance(result, list) and len(result) == 1
    assert result[0].snippet == hostile_snippet  # returned as inert text, not raised

    source = inspect.getsource(find_examples_module)
    for forbidden in ("eval(", "exec(", "compile(", "subprocess.", "import subprocess", "os.system("):
        assert forbidden not in source, f"find_examples.py must never call {forbidden!r}"
