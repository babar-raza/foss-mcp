"""G3/TC-514 (REQ-G3-008): the handshake and tools/list identify the deployment truthfully.

An independent review measured ``initialize`` and ``tools/list`` byte-identical for every pilot it
tried, and descriptions that quote ``Aspose.Pdf.Document`` (a pdf/net example) on pdf/go, cells/rust
and words/net. These tests pin the repair: ``serverInfo.title``, the instructions and every advertised
description name the deployment's own family, platform, language and a real indexed example, with
neutral wording when no generation is readable.

Offline and deterministic: stores are built by COPYING the committed scope directories under
``infra/demo-manifests`` (the repository is never written) and the embedding gateway settings are
removed from the environment, as the standalone image has none.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
import yaml
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import identity as identity_module
from foss_mcp.mcp.identity import (
    FAMILY_DISPLAY,
    NEUTRAL_FACTS,
    PLATFORM_PROFILE,
    DeploymentFacts,
    deployment_instructions,
    deployment_title,
    read_deployment_facts,
    render_description,
)
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import (
    _INPUT_MODELS,
    SERVER_INSTRUCTIONS,
    TOOL_DESCRIPTIONS,
    create_server,
    tool_description,
    tool_input_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
SERVER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py"

# re-pinned by TC-551: 40 -> 32
MIN_PILOTS = 32

LEGACY_TEXTS = (
    "Aspose.Pdf.Document",
    "a pdf/net example",
    "in the pdf/net index",
    "add a watermark to a PDF",
    "add a digital signature to a PDF",
    "font error",
    "lexical/semantic",
)


def _pilots() -> list[tuple[str, str]]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [(entry["family"], entry["platform"]) for entry in values["ingestion"]["pilots"]]


PILOTS = _pilots()
PILOT_IDS = [f"{family}/{platform}" for family, platform in PILOTS]
# re-pinned by TC-551: <removed pilot>; nodejs -> 3d; typescript
DESCRIPTION_PILOTS = [
    ("pdf", "net"),
    ("pdf", "go"),
    ("cells", "rust"),
    ("words", "net"),
    ("3d", "typescript"),
]


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """The standalone image has no embedding gateway: find_examples is lexical-only."""
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def pilot_stores() -> dict[str, GenerationManifestStore]:
    return {}


def _store(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    family: str,
    platform: str,
) -> GenerationManifestStore:
    directory = f"{family}__{platform}__self_extracted"
    if directory not in pilot_stores:
        root = tmp_path_factory.mktemp(f"handshake_{directory}")
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
        pilot_stores[directory] = GenerationManifestStore(root)
    return pilot_stores[directory]


def _baked_chunk_texts(family: str, platform: str) -> list[str]:
    directory = DEMO_MANIFESTS / f"{family}__{platform}__self_extracted"
    pointer = json.loads((directory / "active_pointer.json").read_text(encoding="utf-8"))
    suffix = pointer["generation_id"].split("::")[-1]
    document = json.loads((directory / f"generation__{suffix}.json").read_text(encoding="utf-8"))
    return [entry["text"] for entry in document["payload"]["lexical_index"]["documents"].values()]


def _fqn_lines(texts: list[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            if line.startswith("FQN: "):
                found.add(line[len("FQN: ") :].strip())
                break
    return found


@asynccontextmanager
async def _session(server: Any):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(lambda: server.run(*server_streams, server.create_initialization_options()))
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                yield session
                task_group.cancel_scope.cancel()


def _advertised(server: Any) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with _session(server) as session:
            return {tool.name: tool for tool in (await session.list_tools()).tools}

    return asyncio.run(run())


def _server(store: GenerationManifestStore, family: str, platform: str) -> Any:
    return create_server(DeploymentConfig(family=family, platform=platform), store)


def _all_descriptions(tools: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name, tool in tools.items():
        found.append((name, tool.description))
        for field, prop in tool.input_schema["properties"].items():
            found.append((f"{name}.{field}", prop["description"]))
    return found


class _FakeStore:
    """A store holding one in-memory generation made of the given chunk texts."""

    def __init__(self, texts: list[str]) -> None:
        documents = {
            f"doc-{index}": {"chunk_id": f"c{index}", "text": text} for index, text in enumerate(texts)
        }
        self._manifest = SimpleNamespace(payload={"lexical_index": {"documents": documents}})

    def read_active(self, scope: str) -> str:
        return "gen-1"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


class _NoActiveStore:
    def read_active(self, scope: str) -> None:
        return None


class _RaisingStore:
    def read_active(self, scope: str) -> str:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------------------------
# the display tables
# ---------------------------------------------------------------------------------------------


def test_every_shipped_pilot_family_and_platform_has_a_display_entry() -> None:
    assert len(PILOTS) == MIN_PILOTS
    assert {family for family, _ in PILOTS} <= set(FAMILY_DISPLAY)
    assert {platform for _, platform in PILOTS} <= set(PLATFORM_PROFILE)
    # re-pinned by TC-551: 13 -> 12
    assert len(FAMILY_DISPLAY) == 12 and len(PLATFORM_PROFILE) == 8
    for language, ecosystem, fqn_style in PLATFORM_PROFILE.values():
        assert language and ecosystem and fqn_style


def test_unknown_family_and_platform_fall_back_without_raising() -> None:
    scope = Scope(family="Quux", platform="Zig")
    title = deployment_title(scope)
    assert "quux" in title and "zig" in title
    text = deployment_instructions("base.", scope, NEUTRAL_FACTS)
    assert text.startswith("base. ") and "quux" in text and "zig" in text and "\n" not in text
    assert render_description("x {example_fqn}", NEUTRAL_FACTS)


@pytest.mark.parametrize(("family", "platform"), PILOTS, ids=PILOT_IDS)
def test_every_pilot_title_is_short_and_names_its_family_and_language(family: str, platform: str) -> None:
    title = deployment_title(Scope(family=family, platform=platform))
    assert len(title) <= 80
    assert FAMILY_DISPLAY[family] in title
    assert PLATFORM_PROFILE[platform][0] in title
    assert title.startswith("Aspose ") and "\n" not in title


def test_the_title_reads_as_the_documented_example() -> None:
    assert (
        deployment_title(Scope(family="pdf", platform="net"))
        == "Aspose PDF FOSS for .NET - API and documentation context"
    )


# ---------------------------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------------------------


def test_initialize_options_differ_and_name_their_own_family_and_platform(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    # re-pinned by TC-551: <removed pilot>; nodejs -> 3d; typescript
    chosen = [("pdf", "net"), ("cells", "rust"), ("3d", "typescript")]
    options = {}
    for family, platform in chosen:
        store = _store(pilot_stores, tmp_path_factory, family, platform)
        options[(family, platform)] = _server(store, family, platform).create_initialization_options()
    assert len({o.title for o in options.values()}) == 3
    assert len({o.instructions for o in options.values()}) == 3
    for family, platform in chosen:
        option = options[(family, platform)]
        assert option.title is not None
        assert FAMILY_DISPLAY[family] in option.title
        assert PLATFORM_PROFILE[platform][0] in option.title
        assert FAMILY_DISPLAY[family] in option.instructions
        assert PLATFORM_PROFILE[platform][0] in option.instructions
        for other_family, other_platform in chosen:
            if other_family == family:
                continue
            assert FAMILY_DISPLAY[other_family] not in option.title
            assert FAMILY_DISPLAY[other_family] not in option.instructions
            assert PLATFORM_PROFILE[other_platform][0] not in option.title


@pytest.mark.parametrize(("family", "platform"), PILOTS, ids=PILOT_IDS)
def test_instructions_are_at_most_1100_characters_single_line_and_keep_the_base_guidance(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    family: str,
    platform: str,
) -> None:
    store = _store(pilot_stores, tmp_path_factory, family, platform)
    instructions = _server(store, family, platform).create_initialization_options().instructions
    assert instructions is not None
    assert instructions.startswith(SERVER_INSTRUCTIONS)
    assert len(instructions) <= 1100
    assert "\n" not in instructions
    extra = instructions[len(SERVER_INSTRUCTIONS) :].strip()
    assert 0 < len(extra) <= 400
    assert read_deployment_facts(store, Scope(family=family, platform=platform)).example_fqn in extra


def test_the_base_instructions_text_is_unchanged_and_short() -> None:
    assert len(SERVER_INSTRUCTIONS) <= 800
    assert "one open-source product" in SERVER_INSTRUCTIONS


def test_instructions_are_rendered_when_the_handshake_is_built_not_when_the_server_is_created(
    tmp_path: Path,
) -> None:
    """A server started before its first generation exists must still name its example once one does."""
    root = tmp_path / "manifests"
    root.mkdir()
    server = create_server(DeploymentConfig(family="pdf", platform="go"), GenerationManifestStore(root))
    neutral = server.create_initialization_options().instructions
    assert neutral is not None and "for example" not in neutral.split("FQNs are written as", 1)[1]
    shutil.copytree(DEMO_MANIFESTS / "pdf__go__self_extracted", root / "pdf__go__self_extracted")
    ready = server.create_initialization_options().instructions
    assert ready is not None and ready != neutral and "for example" in ready


# ---------------------------------------------------------------------------------------------
# the facts read from the active generation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "platform"),
    # re-pinned by TC-551: <removed pilot>; nodejs -> 3d; typescript
    [("pdf", "net"), ("pdf", "go"), ("cells", "rust"), ("3d", "typescript"), ("tex", "python")],
    # re-pinned by TC-551: <removed pilot>; nodejs -> 3d; typescript
    ids=["pdf/net", "pdf/go", "cells/rust", "3d/typescript", "tex/python"],
)
def test_the_example_symbol_is_a_real_indexed_fqn(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    family: str,
    platform: str,
) -> None:
    facts = read_deployment_facts(
        _store(pilot_stores, tmp_path_factory, family, platform), Scope(family=family, platform=platform)
    )
    symbols = {
        fqn
        for fqn in _fqn_lines(_baked_chunk_texts(family, platform))
        if not fqn.startswith(("Example: ", "Doc: "))
    }
    assert facts.example_fqn in symbols
    assert facts.example_short and facts.example_fqn.endswith(facts.example_short)
    examples = {
        fqn[len("Example: ") :]
        for fqn in _fqn_lines(_baked_chunk_texts(family, platform))
        if fqn.startswith("Example: ")
    }
    assert facts.task_example is not None
    assert facts.task_example in {title.lower() for title in examples}
    assert facts.example_search == "lexical-only"


def _type_text(fqn: str, methods: int, properties: int = 0) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration", "", "Methods:"]
    lines += [f"  - M{index}()" for index in range(methods)]
    lines += ["", "Properties:"] if properties else []
    lines += [f"  - P{index}: int" for index in range(properties)]
    return "\n".join(lines) + "\n"


def test_the_example_symbol_pick_is_most_members_then_smallest_fqn() -> None:
    texts = [
        _type_text("Lib.Small", 1),
        _type_text("Lib.Zeta", 2, 1),
        _type_text("Lib.Alpha", 2, 1),
        _type_text("Lib.Fewer", 2),
        "FQN: Example: Zebra crossing\nKind: verified_example\nExample:\nrun()\n",
        "FQN: Example: Apple Pie\nKind: verified_example\nExample:\nrun()\n",
        "FQN: Doc: Many things\nKind: documentation\nMethods:\n  - a\n  - b\n  - c\n  - d\n  - e\n",
        "no fqn line here\nMethods:\n  - a\n  - b\n  - c\n  - d\n  - e\n  - f\n",
    ]
    scope = Scope(family="pdf", platform="net")
    forward = read_deployment_facts(_FakeStore(texts), scope)
    backward = read_deployment_facts(_FakeStore(list(reversed(texts))), scope)
    assert forward == backward
    assert forward.example_fqn == "Lib.Alpha"
    assert forward.example_short == "Alpha"
    assert forward.task_example == "apple pie"


def test_the_short_name_is_the_last_segment_for_dots_and_double_colons() -> None:
    scope = Scope(family="pdf", platform="cpp")
    assert read_deployment_facts(_FakeStore([_type_text("A::B::Widget", 1)]), scope).example_short == "Widget"
    assert (
        read_deployment_facts(_FakeStore([_type_text("pkg.mod.Widget", 1)]), scope).example_short == "Widget"
    )
    assert read_deployment_facts(_FakeStore([_type_text("Widget", 1)]), scope).example_short == "Widget"


def test_a_hostile_example_title_cannot_inject_a_placeholder_or_a_line() -> None:
    texts = [
        _type_text("Lib.A", 1),
        "FQN: Example: {example_fqn} it's\tbad\x07 title\nKind: verified_example\n",
    ]
    facts = read_deployment_facts(_FakeStore(texts), Scope(family="pdf", platform="net"))
    assert facts.task_example is not None
    assert "{" not in facts.task_example and "'" not in facts.task_example
    assert all(ord(ch) >= 32 for ch in facts.task_example)


def test_neutral_facts_when_no_generation_is_readable() -> None:
    scope = Scope(family="pdf", platform="net")
    for store in (_NoActiveStore(), _RaisingStore(), _FakeStore([])):
        facts = read_deployment_facts(store, scope)
        assert facts == DeploymentFacts(None, None, None, "lexical-only")
    assert read_deployment_facts(object(), scope) == NEUTRAL_FACTS


# ---------------------------------------------------------------------------------------------
# render_description
# ---------------------------------------------------------------------------------------------


def test_none_renders_the_legacy_pdf_net_values() -> None:
    template = "{example_fqn}|{example_short}|how do I {task_example}|{example_search_clause}"
    assert render_description(template, None) == (
        "Aspose.Pdf.Document|Document|how do I add a watermark to a PDF|a lexical/semantic search"
    )


def test_neutral_facts_render_wording_with_no_example_value() -> None:
    template = "{example_fqn}|{example_short}|how do I {task_example}|{example_search_clause}"
    text = render_description(template, NEUTRAL_FACTS)
    assert text == (
        "the FQN that search_symbols returned|the short name of a type|a short description of the task|"
        "a lexical (word-overlap) search; this deployment has no semantic search"
    )
    assert not any(legacy in text for legacy in LEGACY_TEXTS)


def test_real_facts_render_their_own_values() -> None:
    facts = DeploymentFacts("pkg.Thing", "Thing", "open a thing", "lexical-only")
    assert render_description("{example_fqn} {example_short} how do I {task_example}", facts) == (
        "pkg.Thing Thing how do I open a thing"
    )


@pytest.mark.parametrize(
    ("mode", "expected", "unexpected"),
    [
        ("lexical-only", "no semantic search", "embedding gateway"),
        ("lexical+vector(gateway)", "semantic (vector) fallback through the embedding gateway", "degraded"),
        (
            "lexical+vector(gateway) degraded",
            "currently degraded, so expect lexical results only",
            "no semantic",
        ),
    ],
)
def test_the_search_clause_follows_the_mode_string(mode: str, expected: str, unexpected: str) -> None:
    text = render_description("{example_search_clause}", DeploymentFacts(None, None, None, mode))
    assert expected in text
    assert unexpected not in text


def test_a_template_with_braces_is_never_formatted() -> None:
    assert render_description('json {"a": 1} {example_short}', NEUTRAL_FACTS) == (
        'json {"a": 1} the short name of a type'
    )


def test_static_helpers_still_render_the_legacy_text() -> None:
    assert "Aspose.Pdf.Document" in tool_description("get_symbol")
    assert "Aspose.Pdf.Document" in tool_input_schema("get_symbol")["properties"]["fqn"]["description"]
    assert "add a watermark to a PDF" in tool_description("lookup")
    assert "{" not in "".join(tool_description(name) for name in TOOL_DESCRIPTIONS)


# ---------------------------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "platform"), DESCRIPTION_PILOTS, ids=[f"{f}/{p}" for f, p in DESCRIPTION_PILOTS]
)
def test_tools_list_never_contains_a_legacy_example(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    family: str,
    platform: str,
) -> None:
    tools = _advertised(_server(_store(pilot_stores, tmp_path_factory, family, platform), family, platform))
    assert len(tools) == 9
    texts = _all_descriptions(tools)
    assert len(texts) > 9
    for where, text in texts:
        assert "{" not in text and "}" not in text, f"{where} holds an unrendered placeholder"
        if (family, platform) == ("pdf", "net"):
            # pdf/net really has Aspose.Pdf.Document, so only the pdf/net-only phrasing is banned there.
            banned = tuple(legacy for legacy in LEGACY_TEXTS if legacy != "Aspose.Pdf.Document")
        else:
            banned = LEGACY_TEXTS
        for legacy in banned:
            assert legacy not in text, f"{where} still holds {legacy!r}"


@pytest.mark.parametrize(
    ("family", "platform"), DESCRIPTION_PILOTS, ids=[f"{f}/{p}" for f, p in DESCRIPTION_PILOTS]
)
def test_tools_list_descriptions_use_the_pilots_own_example(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    family: str,
    platform: str,
) -> None:
    store = _store(pilot_stores, tmp_path_factory, family, platform)
    facts = read_deployment_facts(store, Scope(family=family, platform=platform))
    assert facts.example_fqn is not None and facts.task_example is not None
    tools = _advertised(_server(store, family, platform))
    assert facts.example_fqn in tools["get_symbol"].description
    assert facts.example_fqn in tools["get_symbol"].input_schema["properties"]["fqn"]["description"]
    assert facts.example_fqn in tools["list_members"].description
    assert facts.example_short in tools["search_symbols"].description
    assert facts.task_example in tools["find_examples"].input_schema["properties"]["query"]["description"]


def test_tools_list_over_an_unreadable_generation_uses_neutral_wording(tmp_path: Path) -> None:
    root = tmp_path / "manifests"
    root.mkdir()
    tools = _advertised(
        create_server(DeploymentConfig(family="cells", platform="rust"), GenerationManifestStore(root))
    )
    for where, text in _all_descriptions(tools):
        assert not any(legacy in text for legacy in LEGACY_TEXTS), where
    assert "the FQN that search_symbols returned" in tools["get_symbol"].description


def test_the_advertised_schemas_keep_their_types_bounds_and_enums() -> None:
    """Rendering changes descriptions only: every other schema key is identical to the legacy render."""
    facts = DeploymentFacts("pkg.Thing", "Thing", "open a thing", "lexical-only")
    for name in _INPUT_MODELS:
        legacy = tool_input_schema(name)
        rendered = tool_input_schema(name, facts)
        assert set(legacy) == set(rendered)
        assert legacy.get("required") == rendered.get("required")
        for field, prop in legacy["properties"].items():
            assert {k: v for k, v in prop.items() if k != "description"} == {
                k: v for k, v in rendered["properties"][field].items() if k != "description"
            }


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("lexical-only", "no semantic search"),
        ("lexical+vector(gateway)", "semantic (vector) fallback"),
        ("lexical+vector(gateway) degraded", "currently degraded"),
    ],
)
def test_find_examples_description_says_lexical_only_without_a_gateway_and_vector_with_one(
    monkeypatch: pytest.MonkeyPatch,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    mode: str,
    expected: str,
) -> None:
    monkeypatch.setattr(identity_module, "example_search_mode", lambda payload: (mode, "test"))
    store = _store(pilot_stores, tmp_path_factory, "pdf", "go")
    tools = _advertised(_server(store, "pdf", "go"))
    text = tools["find_examples"].description
    assert expected in text
    assert "lexical/semantic" not in text
    assert "It takes only query and top_k." in text
    assert "prefixed with the literal text Example:" in text


def test_find_examples_description_is_lexical_only_in_the_standalone_image(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = _store(pilot_stores, tmp_path_factory, "slides", "java")
    text = _advertised(_server(store, "slides", "java"))["find_examples"].description
    assert "lexical (word-overlap) search; this deployment has no semantic search" in text


def test_report_index_freshness_texts_say_only_self_extracted_is_populated() -> None:
    description = tool_description("report_index_freshness")
    assert "Only 'self_extracted' holds an index in this product" in description
    for dead in ("furnished", "repo_native_manifest", "repo_native_release_notes"):
        assert dead in description
    assert "never published" in description
    field = tool_input_schema("report_index_freshness")["properties"]["source_kind"]
    assert "only 'self_extracted' holds an index" in field["description"]
    assert "never published" in field["description"]
    # Documented, not removed: the enum still lists the four SourceKind values.
    assert len(field["enum"]) == 4 and "self_extracted" in field["enum"]


def test_get_product_reference_description_does_not_claim_authoritative_absence() -> None:
    text = tool_description("get_product_reference")
    assert "authoritative absence" not in text
    assert "currently NotAvailable for every product" not in text
    assert "could not be fetched" in text


def test_the_untouched_descriptions_keep_their_once_only_caveats_and_cross_references() -> None:
    names = sorted(TOOL_DESCRIPTIONS)
    assert sum("hold only part of a library" in tool_description(n) for n in names) == 1
    assert sum("is not an import path" in tool_description(n) for n in names) == 1
    assert "search_symbols" in tool_description("get_symbol") and "get_symbol" in tool_description(
        "search_symbols"
    )


# ---------------------------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------------------------


def test_list_tools_and_server_call_the_identity_renderers() -> None:
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    assert source.count("title=deployment_title(scope)") == 1
    assert "deployment_instructions(" in source
    assert "read_deployment_facts(" in source
    assert "from foss_mcp.mcp.identity import" in source
    assert "tool_description(name, facts)" in source
    assert "tool_input_schema(name, facts)" in source
