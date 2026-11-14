"""G3/TC-823 (REQ-G3-011, RD2-100): a query word that occurs only in a directory-tree diagram is not a match.

cells/rust ``search_docs("license")`` answered the "Key Capabilities and API Reference" chunk with
``match_quality: exact`` although the only occurrence of the word in it is ``LICENSE.txt`` in the repository
layout drawn at the end of the chunk: an incidental token, a file NAME, not documentation of licensing. The
passage matcher now drops the lines of an ASCII/box-drawing tree (and the rest of a fenced block that is mostly
tree) before it scores a window. Nothing else is dropped: a word a paragraph, a table or an ordinary code fence
really uses still counts, and so does a word of the chunk's own title.

Offline and deterministic: synthetic chunks plus the committed ``infra/demo-manifests`` copied to a temp dir.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_docs import DocMatch, Miss, _tree_diagram_lines, search_docs
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_DOCS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "search_docs.py"

_GENERATION_ID = "gen-tc823-incidental-token"
SCOPE = Scope(family="demo", platform="rust")

_TREE = [
    "```",
    "demo-rs/",
    "├── src/",
    "│   ├── lib.rs                  #   Crate root",
    "│   └── Worksheet.rs            #   Worksheet model",
    "├── samples/                    # Runnable feature examples",
    "│   └── README.md               #   Sample overview",
    "├── License/LICENSE.txt         # MIT license (also mirrored at the repository root)",
    "├── Cargo.toml",
    "└── README.md",
    "```",
]


class _InMemoryStore:
    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _doc(title: str, *body: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body]) + "\n"


def _titles(result: object) -> list[str]:
    assert isinstance(result, list)
    return [match.text.splitlines()[0] for match in result if isinstance(match, DocMatch)]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("incidental-token-corpus"))


# ---- the tree detector itself ---------------------------------------------------------------------


def test_every_line_of_a_box_drawing_tree_fence_is_a_tree_line() -> None:
    lines = _TREE
    assert _tree_diagram_lines(lines) == frozenset(range(len(lines)))


def test_the_glyph_free_root_line_of_a_tree_fence_goes_with_it_but_a_prose_line_does_not() -> None:
    lines = ["The layout is:", *_TREE, "That is all."]
    flagged = _tree_diagram_lines(lines)
    assert 0 not in flagged and len(lines) - 1 not in flagged
    assert 2 in flagged  # "demo-rs/": no glyph, but inside a fence that is a tree


def test_an_ascii_branch_tree_outside_any_fence_is_detected() -> None:
    lines = ["|-- src", "|   |-- lib.rs", "`-- LICENSE.txt"]
    assert _tree_diagram_lines(lines) == frozenset({0, 1, 2})


def test_an_ordinary_code_fence_a_table_and_prose_are_never_tree_lines() -> None:
    lines = [
        "```rust",
        "let workbook = Workbook::new();",
        'workbook.save("out.xlsx")?;',
        "```",
        "| Name | Meaning |",
        "|------|---------|",
        "| License | MIT |",
        "The license is MIT.",
    ]
    assert _tree_diagram_lines(lines) == frozenset()


# ---- synthetic search_docs behaviour ----------------------------------------------------------------


def test_a_query_word_found_only_in_a_tree_diagram_is_a_miss() -> None:
    store = _InMemoryStore(
        _doc("Key Capabilities and API Reference", "Worksheet and cell model overview.", *_TREE),
        _doc("Installation", "Add the crate to Cargo.toml and build with cargo."),
    )
    result = search_docs(store, SCOPE, "license")  # type: ignore[arg-type]
    assert isinstance(result, Miss), _titles(result)


def test_the_same_word_in_a_paragraph_still_matches() -> None:
    store = _InMemoryStore(
        _doc("Key Capabilities and API Reference", "Worksheet and cell model overview.", *_TREE),
        _doc("Licensing", "The crate is released under the MIT license."),
    )
    result = search_docs(store, SCOPE, "license")  # type: ignore[arg-type]
    assert _titles(result) == ["FQN: Doc: Licensing"]


def test_the_same_word_in_an_ordinary_code_fence_still_matches() -> None:
    store = _InMemoryStore(
        _doc(
            "Quick start",
            "Open a workbook and save it.",
            "```rust",
            "let workbook = Workbook::new();",
            'workbook.save("out.xlsx")?;',
            "```",
        ),
    )
    result = search_docs(store, SCOPE, "workbook save")  # type: ignore[arg-type]
    assert _titles(result) == ["FQN: Doc: Quick start"]


def test_a_chunk_whose_own_title_is_the_word_still_matches_despite_a_tree() -> None:
    store = _InMemoryStore(_doc("License", "Terms follow.", *_TREE))
    result = search_docs(store, SCOPE, "license")  # type: ignore[arg-type]
    assert _titles(result) == ["FQN: Doc: License"]


def test_lookup_does_not_answer_exact_from_a_tree_only_chunk() -> None:
    store = _InMemoryStore(_doc("Key Capabilities and API Reference", "Worksheet model overview.", *_TREE))
    answer = lookup(store, SCOPE, "license")  # type: ignore[arg-type]
    docs = answer.doc_matches if isinstance(answer, TaskAnswer) else []
    assert not any(match.match_quality == MATCH_EXACT for match in docs)


# ---- the real cells/rust repro of RD2-100 -----------------------------------------------------------


def test_cells_rust_license_is_no_longer_answered_by_the_repository_layout_chunk(
    corpus: ConformanceCorpus,
) -> None:
    pilot = corpus.pilots["cells__rust"]
    result = search_docs(corpus.store, pilot.scope, "license")
    titles = _titles(result) if isinstance(result, list) else []
    assert "FQN: Doc: Key Capabilities and API Reference" not in titles, titles
    answer = lookup(corpus.store, pilot.scope, "license")
    if isinstance(answer, TaskAnswer):
        assert not any(
            match.text.startswith("FQN: Doc: Key Capabilities and API Reference")
            for match in answer.doc_matches
        )


# ---- liveness: the production passage matcher itself calls the tree filter ------------------------------


def test_the_passage_matcher_calls_the_tree_filter() -> None:
    source = SEARCH_DOCS_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _passage_match(")
    end = source.index("\ndef ", start + 1)
    assert "_tree_diagram_lines(lines)" in source[start:end]
