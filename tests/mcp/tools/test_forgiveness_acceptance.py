"""G3/TC-516 (REQ-G3-008, REQ-G3-001): the reviewers' regression tables, measured offline on the baked pilots.

Four tables, each over a COPY of ``infra/demo-manifests`` (the repository is never written) with the
embedding gateway replaced by a stub whose ``embed`` raises, as the standalone image behaves with no
gateway:

1. SYMBOL PHRASES - for every pilot, the first ten type names that occur as a whole word in one of the
   pilot's own documentation or example chunks; ``lookup("how do I use <Name>")`` and
   ``lookup("<Name> methods")`` must carry that type in its first three symbols (0 percent before).
2. VERIFIED-ABSENT PROBES - 14 questions about features the libraries do not have, asked on every pilot of
   the families that have them (78 probes): forgiveness must not buy irrelevant answers.
3. EXAMPLE QUERIES - 26 (pilot, target example) pairs asked concisely and verbosely.
4. FIXED NEGATIVES - identifier-like and empty-identifier example queries that used to match anything.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, scope_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

MIN_NAMES = 300
MIN_FOUND_PERCENT = 90.0
MIN_HONEST_MISSES = 54
MAX_IRRELEVANT_ANSWERS = 7
MIN_CONCISE_HITS = 23
MIN_VERBOSE_HITS = 14
MAX_VERBOSE_WRONG = 2

_TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9]{4,}$")
_FINAL_SEGMENT = re.compile(r"\.|::")

# (family, platform) -> the questions below were measured on these baked pilots.
PROBES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("create a pivot table", r"pivot", ("cells",)),
    ("export workbook to docx", r"docx", ("cells",)),
    ("connect to a SQL database", r"sql|database", ("cells",)),
    ("run a VBA macro", r"vba|macro", ("cells",)),
    ("OCR a scanned pdf", r"ocr", ("pdf",)),
    ("convert pdf to docx", r"docx", ("pdf",)),
    ("convert pdf to powerpoint", r"pptx|powerpoint", ("pdf",)),
    ("connect over IMAP", r"imap", ("jmap",)),
    ("send via SMTP", r"smtp", ("jmap",)),
    ("parse an EML file", r"eml", ("jmap",)),
    ("use BibTeX bibliography", r"bibtex", ("tex",)),
    ("run TikZ drawing", r"tikz", ("tex",)),
    ("render image of a 3d scene", r"render", ("3d",)),
    ("convert to epub", r"epub", ("words",)),
)

# pilot, target example title, concise query, verbose query.
EXAMPLE_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "pdf/net",
        "Add a Watermark Annotation",
        "add watermark to a pdf page",
        "I need to stamp a CONFIDENTIAL watermark on every page of an existing PDF file using C#, how do I do that?",
    ),
    (
        "pdf/net",
        "Extract Text Fragments",
        "extract text from a pdf page",
        "How can I read all the text on the first page of a PDF and print each fragment?",
    ),
    (
        "pdf/python",
        "Merge PDF Files",
        "merge pdf files",
        "combine several PDFs into a single document in python",
    ),
    (
        "pdf/python",
        "Extract Text From a PDF",
        "extract text from pdf",
        "get the plain text out of every page of a PDF in python",
    ),
    (
        "pdf/python",
        "Render a Page to an Image",
        "render pdf page to png image",
        "convert the first page of a PDF into a PNG with a given DPI",
    ),
    (
        "pdf/python",
        "Build a Table",
        "add a table to a pdf",
        "draw a table with header row and totals on a PDF page",
    ),
    (
        "pdf/go",
        "Split and Merge",
        "split pdf into pages",
        "how do I split a PDF into separate one-page files and then merge two PDFs in Go",
    ),
    (
        "pdf/go",
        "Encrypt with Permissions",
        "encrypt pdf with password",
        "protect a PDF with user and owner passwords and restrict printing",
    ),
    (
        "pdf/go",
        "Fill AcroForm",
        "fill pdf form fields",
        "set the value of a text box and tick a checkbox in an existing PDF form",
    ),
    (
        "pdf/java",
        "Inspect Document Page",
        "get page size of pdf",
        "how do I read the width and height of a page in a PDF in Java",
    ),
    (
        "pdf/java",
        "Widget Annotation",
        "add a button to a pdf",
        "create a PDF with a form button widget annotation",
    ),
    (
        "cells/net",
        "Build a Table With Totals",
        "create excel table with totals",
        "how to create a table over a range of cells and add a totals row with sum",
    ),
    (
        "cells/net",
        "Convert XLSX to PDF",
        "convert xlsx to pdf",
        "export an Excel workbook to PDF in .NET",
    ),
    (
        "cells/net",
        "Add Hyperlinks",
        "add hyperlink to cell",
        "put a hyperlink in a cell and define a named range",
    ),
    (
        "cells/python",
        "Export to CSV",
        "export worksheet to csv",
        "save my spreadsheet as a CSV file using python",
    ),
    (
        "cells/python",
        "Password-Protect",
        "password protect excel file",
        "how can I encrypt an xlsx file with a password",
    ),
    (
        "slides/java",
        "Clone a Slide",
        "duplicate a slide",
        "copy a slide inside a presentation and append it",
    ),
    (
        "slides/java",
        "Speaker Notes",
        "add speaker notes",
        "write presenter notes for slide 1",
    ),
    (
        "note/python",
        "Extract Hyperlinks",
        "extract hyperlinks from onenote",
        "list all the links in a OneNote document",
    ),
    (
        "note/python",
        "Save Embedded Images",
        "save images from onenote",
        "extract embedded pictures from a .one file and write them to disk",
    ),
    (
        "pdf/typescript",
        "Create a New PDF",
        "create a new pdf",
        "generate a brand new PDF document with text in typescript",
    ),
    (
        "pdf/typescript",
        "Password-Protect",
        "encrypt pdf",
        "set a password on a PDF in node",
    ),
    (
        "email/net",
        "Convert EML to MSG",
        "convert eml to msg",
        "how do I turn an .eml email file into an Outlook .msg",
    ),
    (
        "3d/python",
        "Cube and Export It to 3MF",
        "export cube to 3mf",
        "create a box mesh and save it as 3MF",
    ),
    (
        "words/net",
        "Merge Two Documents",
        "merge two word documents",
        "append one docx to another in C#",
    ),
    (
        "cells/rust",
        "Format numbers",
        "format number as percentage",
        "apply percentage number format to a cell",
    ),
)

# (query, pilot) pairs that used to return unrelated examples and now must be misses.
FIXED_NEGATIVES: tuple[tuple[str, str], ...] = (
    ("DataSource example", "pdf/python"),
    ("DataSource example", "pdf/net"),
    ("DataSource example", "cells/net"),
    ("Example: Nonexistent title", "pdf/go"),
    ("Example: Nonexistent title", "cells/net"),
    ("Example:", "pdf/net"),
    ("Example:", "pdf/go"),
    ("Example:", "cells/net"),
)


def _discover_pilots() -> list[str]:
    return sorted(
        directory.name
        for directory in DEMO_MANIFESTS.iterdir()
        if directory.is_dir() and (directory / "active_pointer.json").is_file()
    )


PILOTS = _discover_pilots()


def _directory_of(pilot: str) -> str:
    family, platform = pilot.split("/")
    return f"{family}__{platform}__self_extracted"


def _scope_of(directory: str) -> Scope:
    family, platform, source_kind = directory.split("__")
    return Scope(family=family, platform=platform, source_kind=source_kind)


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory) -> _Stores:
    root = tmp_path_factory.mktemp("forgiveness_acceptance")
    return _Stores(root)


class _Stores:
    """Lazily copies each pilot's committed scope directory and opens a store over the copy."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._by_directory: dict[str, GenerationManifestStore] = {}

    def of(self, directory: str) -> GenerationManifestStore:
        if directory not in self._by_directory:
            shutil.copytree(DEMO_MANIFESTS / directory, self._root / directory)
            self._by_directory[directory] = GenerationManifestStore(self._root)
        return self._by_directory[directory]


def _documents(store: GenerationManifestStore, scope: Scope) -> dict[str, dict]:
    key = scope_key(scope, "self_extracted")
    active = store.read_active(key)
    assert active is not None
    return store.read_generation(key, active).payload["lexical_index"]["documents"]


def _final_segment(fqn: str) -> str:
    return _FINAL_SEGMENT.split(fqn.strip())[-1]


# ---- table 1: symbol phrases ------------------------------------------------------------------------


def _symbol_names(store: GenerationManifestStore, scope: Scope) -> list[str]:
    documents = _documents(store, scope)
    candidates: set[str] = set()
    prose: list[str] = []
    for document in documents.values():
        text = document["text"]
        kind = chunk_kind(text)
        if kind == SYMBOL:
            fqn = extract_fqn(text)
            if fqn is not None:
                segment = _final_segment(fqn)
                if _TYPE_NAME.match(segment):
                    candidates.add(segment)
        elif kind in (DOCUMENTATION, EXAMPLE):
            prose.append(text)
    corpus = "\n".join(prose)
    present = {name for name in candidates if re.search(rf"\b{re.escape(name)}\b", corpus)}
    return sorted(present)[:10]


def _symbols_of(answer: object) -> list[SymbolMatch]:
    if isinstance(answer, list):
        return [item for item in answer if isinstance(item, SymbolMatch)]
    if isinstance(answer, TaskAnswer):
        return list(getattr(answer, "symbols", ()))
    return []


def _names_of_first_three(answer: object) -> set[str]:
    return {_final_segment(extract_fqn(match.text) or "") for match in _symbols_of(answer)[:3]}


@pytest.fixture(scope="module")
def symbol_phrase_results(stores: _Stores) -> dict[str, tuple[int, int]]:
    """template -> (found, asked), over every baked pilot."""
    found = {"how do I use {}": 0, "{} methods": 0}
    asked = 0
    for directory in PILOTS:
        scope = _scope_of(directory)
        store = stores.of(directory)
        for name in _symbol_names(store, scope):
            asked += 1
            for template in found:
                answer = lookup(store, scope, template.format(name))
                if name in _names_of_first_three(answer):
                    found[template] += 1
    return {template: (count, asked) for template, count in found.items()}


@pytest.mark.parametrize("template", ["how do I use {}", "{} methods"])
def test_symbol_phrase_found_in_the_top_three_at_least_90_percent(
    template: str, symbol_phrase_results: dict[str, tuple[int, int]]
) -> None:
    found, asked = symbol_phrase_results[template]
    assert asked >= MIN_NAMES, f"only {asked} names across {len(PILOTS)} pilots"
    percent = 100.0 * found / asked
    assert percent >= MIN_FOUND_PERCENT, f"{template!r}: {found}/{asked} = {percent:.1f}%"


# ---- table 2: verified-absent probes ----------------------------------------------------------------


def _answer_text(answer: object) -> str:
    parts: list[str] = []
    if isinstance(answer, TaskAnswer):
        parts.extend(match.text for match in answer.doc_matches)
        if answer.example is not None:
            parts.extend([answer.example.fqn, answer.example.snippet])
        parts.extend(match.text for match in _symbols_of(answer))
    elif isinstance(answer, list):
        for item in answer:
            if isinstance(item, SymbolMatch):
                parts.append(item.text)
            elif isinstance(item, ExampleMatch):
                parts.extend([item.fqn, item.snippet])
            else:
                parts.append(getattr(item, "text", ""))
    return "\n".join(parts).lower().replace("_", "")


@dataclass(frozen=True)
class _ProbeCounts:
    total: int
    honest: int
    relevant: int
    irrelevant: int


@pytest.fixture(scope="module")
def probe_counts(stores: _Stores) -> _ProbeCounts:
    honest = relevant = irrelevant = 0
    for question, pattern, families in PROBES:
        for directory in PILOTS:
            scope = _scope_of(directory)
            if scope.family not in families:
                continue
            answer = lookup(stores.of(directory), scope, question)
            if isinstance(answer, Miss):
                honest += 1
            elif re.search(pattern, _answer_text(answer)):
                relevant += 1
            else:
                irrelevant += 1
    return _ProbeCounts(honest + relevant + irrelevant, honest, relevant, irrelevant)


def test_verified_absent_probes_keep_honest_misses_and_cap_irrelevant_answers(
    probe_counts: _ProbeCounts,
) -> None:
    message = (
        f"{probe_counts.total} probes: honest misses {probe_counts.honest}, "
        f"relevant answers {probe_counts.relevant}, irrelevant answers {probe_counts.irrelevant}"
    )
    assert probe_counts.total == 78, message
    assert probe_counts.honest >= MIN_HONEST_MISSES, message
    assert probe_counts.irrelevant <= MAX_IRRELEVANT_ANSWERS, message


# ---- table 3: example queries -----------------------------------------------------------------------


def _example_outcome(stores: _Stores, pilot: str, title: str, query: str) -> str:
    """``hit`` (the target is among the first three examples), ``miss`` (nothing returned) or ``wrong``."""
    directory = _directory_of(pilot)
    result = find_examples(stores.of(directory), _scope_of(directory), query, top_k=3)
    if isinstance(result, NoExampleFound):
        return "miss"
    if any(title.lower() in match.fqn.lower() for match in result[:3]):
        return "hit"
    return "wrong"


def _example_counts(stores: _Stores, column: int) -> dict[str, int]:
    counts = {"hit": 0, "miss": 0, "wrong": 0}
    for row in EXAMPLE_ROWS:
        counts[_example_outcome(stores, row[0], row[1], row[column])] += 1
    return counts


def test_the_example_table_has_26_rows() -> None:
    assert len(EXAMPLE_ROWS) == 26
    assert len(PROBES) == 14
    assert len(FIXED_NEGATIVES) == 8


def test_concise_example_queries_still_hit_23_of_26(stores: _Stores) -> None:
    counts = _example_counts(stores, 2)
    assert counts["hit"] >= MIN_CONCISE_HITS and counts["wrong"] == 0, f"concise: {counts}"


def test_verbose_example_queries_do_not_regress(stores: _Stores) -> None:
    counts = _example_counts(stores, 3)
    assert counts["hit"] >= MIN_VERBOSE_HITS and counts["wrong"] <= MAX_VERBOSE_WRONG, f"verbose: {counts}"


# ---- table 4: fixed negatives -----------------------------------------------------------------------


@pytest.mark.parametrize(("query", "pilot"), FIXED_NEGATIVES)
def test_fixed_negative_example_queries_are_misses(query: str, pilot: str, stores: _Stores) -> None:
    directory = _directory_of(pilot)
    result = find_examples(stores.of(directory), _scope_of(directory), query, top_k=3)
    assert isinstance(result, NoExampleFound), (
        f"{pilot} {query!r} returned {[match.fqn for match in result]}"
        if isinstance(result, list)
        else "unexpected result"
    )
