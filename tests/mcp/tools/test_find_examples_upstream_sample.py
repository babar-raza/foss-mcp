"""G3/TC-874 (REQ-G3-014): the serving tools know the ``upstream-sample`` example chunks TC-872 introduced.

TC-872 lets a pilot sidecar publish ``kind: upstream-sample`` examples. Their chunk carries ``Provenance:
upstream-sample``, a label line starting ``Upstream sample:`` (``upstream_sample_label``) and a
``Sample-Source: <path>@<commit7>`` line, in this order::

    FQN: Example: <title>
    Kind: verified_example
    Provenance: upstream-sample
    Verification: passed
    Upstream sample: adapted from <path> of the pinned upstream repository (MIT); ...
    Verification-Level: ... / Verified-With: ...
    Sample-Source: <path>@<commit7>
    Runtime-Status: ... / Runtime-Produced-Bytes: ...
    <blank>
    <description>
    <blank>
    Example:
    <code>

Reproduced BEFORE the fix with a chunk of exactly this shape: ``find_examples._provenance`` answered ``readme`` (so
the example was described to a user as the upstream README's own snippet: "the verifier may have added the README's
own imports ..."), and ``relevance.example_prose`` returned the ``Upstream sample:`` label line and the
``Sample-Source:`` line (the upstream repository file name) as retrieval prose; ``example_lead_in`` leaked the label
when no blank line followed the header.

Pinned here, offline and deterministic (synthetic generations through the real ``publish_generation``, no gateway, no
network, no clock, no skip):

* a third provenance, ``upstream-sample``, with its own honest note (names the file and the commit, never the README);
* the header lines never become retrieval terms or the lead-in sentence;
* the README-only description rule above the rare-code-word cap does not admit an upstream sample, and an upstream
  sample is title-anchored exactly like an authored one;
* an authored chunk and a README chunk are described, tokenised and served exactly as before.

Negative control: reverse-applying the production part of the commit restores the README provenance and the leaked
header lines, so the tests below fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    KIND_UPSTREAM_SAMPLE,
    UPSTREAM_SAMPLE_LABEL,
    upstream_sample_label,
)
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import relevance
from foss_mcp.mcp.tools.find_examples import (
    AUTHORED_NOTE_TAIL,
    AUTHORED_VERIFICATION_NOTE,
    MATCH_BASIS_RARE_CODE_WORD,
    MATCH_BASIS_TITLE_WORD,
    README_NOTE_TAIL,
    README_VERIFICATION_NOTE,
    UPSTREAM_SAMPLE_NOTE_TAIL,
    NoExampleFound,
    _provenance,
    _verification_note,
    find_examples,
    is_verified_example_text,
    runtime_label,
)
from foss_mcp.mcp.tools.relevance import example_lead_in, example_prose
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
SAMPLE_PATH = "tests/test_widget_rotate.py"
SAMPLE_COMMIT7 = COMMIT[:7]
SCOPE = Scope("pdf", "python")
PROSE = "Flips the page upside down."
CODE = "page = Page()\npage.flange()\npage.run()"
LEVEL_LINES = "Verification-Level: ran-produced-output\nVerified-With: Python 3.12.4\n"
RUN_LINES = "Runtime-Status: ran\nRuntime-Produced-Bytes: 35\n"
SAMPLE_SOURCE_LINE = f"Sample-Source: {SAMPLE_PATH}@{SAMPLE_COMMIT7}\n"


def _upstream(
    title: str = "Flip a page",
    prose: str = PROSE,
    code: str = CODE,
    *,
    level: bool = True,
    run: bool = True,
    blank: bool = True,
    source: str = SAMPLE_SOURCE_LINE,
    passed: bool = True,
) -> str:
    """An upstream-sample chunk, header lines in the order ``infra/build_chunks.py`` writes them (TC-872)."""
    return (
        f"FQN: Example: {title}\nKind: verified_example\nProvenance: upstream-sample\n"
        f"{'Verification: passed' + chr(10) if passed else ''}{upstream_sample_label(SAMPLE_PATH)}\n"
        f"{LEVEL_LINES if level else ''}{source}{RUN_LINES if run else ''}"
        f"{chr(10) if blank else ''}{prose}\n\nExample:\n{code}\nSource-Commit: {COMMIT}"
    )


def _without_header(text: str) -> str:
    """The same chunk with the upstream-sample header lines (provenance, verification, label, source) removed."""
    drop = ("Provenance:", "Verification:", "Upstream sample:", "Sample-Source:")
    return "\n".join(line for line in text.split("\n") if not line.startswith(drop))


def _authored(title: str = "Flip a page", prose: str = PROSE, code: str = CODE) -> str:
    return (
        f"FQN: Example: {title}\nKind: verified_example\nProvenance: authored\nVerification: passed\n"
        f"{AUTHORED_LABEL}\n{LEVEL_LINES}\n{prose}\n\nExample:\n{code}\nSource-Commit: {COMMIT}"
    )


def _readme(title: str = "Flip a page", prose: str = PROSE, code: str = CODE) -> str:
    return f"FQN: Example: {title}\nKind: verified_example\n{LEVEL_LINES}\n{prose}\n\nExample:\n{code}\nSource-Commit: {COMMIT}"


# ---------------------------------------------------------------------------------------------------
# the provenance
# ---------------------------------------------------------------------------------------------------


def test_an_upstream_sample_chunk_is_its_own_provenance() -> None:
    assert _provenance(_upstream()) == "upstream-sample"
    assert _provenance(_authored()) == "authored"
    assert _provenance(_readme()) == "readme"


@pytest.mark.parametrize("kwargs", [{}, {"level": False}, {"run": False}, {"blank": False}, {"source": ""}])
def test_the_provenance_does_not_depend_on_the_other_header_lines(kwargs: dict) -> None:
    assert _provenance(_upstream(**kwargs)) == "upstream-sample"


def test_a_provenance_word_in_the_description_or_the_code_is_not_the_provenance_line() -> None:
    mention = _readme(prose="Provenance: upstream-sample is a word a README may say.", code="x = 1")
    assert _provenance(mention) == "readme"
    assert _provenance(_readme(code="# Provenance: upstream-sample\nx = 1")) == "readme"


def test_the_provenance_line_the_server_reads_is_the_line_the_entrypoint_writes() -> None:
    assert KIND_UPSTREAM_SAMPLE == "upstream-sample"
    assert find_examples_module._UPSTREAM_SAMPLE_PROVENANCE_LINE == f"Provenance: {KIND_UPSTREAM_SAMPLE}"
    assert find_examples_module._SAMPLE_SOURCE_PREFIX == "Sample-Source:"
    written = (REPO_ROOT / "infra" / "build_chunks.py").read_text(encoding="utf-8")
    assert 'f"Provenance: {KIND_UPSTREAM_SAMPLE}' in written
    assert 'f"Sample-Source: {sample_path}@{sample_commit[:7]}' in written


def test_an_upstream_sample_without_its_verification_line_is_not_a_verified_example() -> None:
    assert is_verified_example_text(_upstream())
    assert not is_verified_example_text(_upstream(passed=False))
    # exactly like an authored chunk, and a README chunk (which never has the line) is unaffected
    assert not is_verified_example_text(_authored().replace("Verification: passed\n", ""))
    assert is_verified_example_text(_readme())


# ---------------------------------------------------------------------------------------------------
# the honest verification note
# ---------------------------------------------------------------------------------------------------


def test_the_note_names_the_upstream_file_and_commit_and_never_the_readme() -> None:
    note = _verification_note(_upstream())
    assert note == (
        f"Adapted from {SAMPLE_PATH} at {SAMPLE_COMMIT7} of the upstream repository (MIT); "
        f"compiled and ran, produced 35 bytes (verified with Python 3.12.4); {UPSTREAM_SAMPLE_NOTE_TAIL}"
    )
    assert README_NOTE_TAIL not in note and "README" not in note
    assert "Authored for this index" not in note


def test_the_note_keeps_the_runtime_label_wording_of_the_chunk() -> None:
    for run, level, expected in (
        (True, True, "compiled and ran, produced 35 bytes (verified with Python 3.12.4)"),
        (False, True, "built and run to exit 0 and produced output (verified with Python 3.12.4)"),
        (False, False, "compiled only (run not verified)"),
    ):
        text = _upstream(run=run, level=level)
        assert expected in _verification_note(text)
        assert runtime_label(text) in expected


def test_a_note_without_a_readable_sample_source_line_still_says_it_is_an_upstream_file() -> None:
    for source in (
        "",
        "Sample-Source: no-commit-here\n",
        "Sample-Source: @abc1234\n",
        "Sample-Source: a.py@\n",
    ):
        note = _verification_note(_upstream(source=source))
        assert note.startswith("Adapted from a file of the upstream repository (MIT); ")
        assert README_NOTE_TAIL not in note


def test_the_note_never_reads_the_sample_source_line_from_the_code() -> None:
    text = _upstream(source="", code="Sample-Source: evil.py@abcdef0\nx = 1")
    assert _verification_note(text).startswith("Adapted from a file of the upstream repository (MIT); ")


def test_the_authored_and_readme_notes_are_exactly_what_they_were() -> None:
    assert _verification_note(_authored()) == (
        f"Authored for this index; built and run to exit 0 and produced output (verified with Python 3.12.4); "
        f"{AUTHORED_NOTE_TAIL}"
    )
    assert _verification_note(_readme()) == (
        f"built and run to exit 0 and produced output (verified with Python 3.12.4); {README_NOTE_TAIL}"
    )
    legacy_readme = "FQN: Example: X\nKind: verified_example\nDoes one thing.\n\nExample:\nrun()"
    assert _verification_note(legacy_readme) == README_VERIFICATION_NOTE
    assert _verification_note(legacy_readme.replace("Kind:", "Provenance: authored\nKind:")) == (
        AUTHORED_VERIFICATION_NOTE
    )


# ---------------------------------------------------------------------------------------------------
# the header lines are not retrieval prose and not the lead-in
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("level", [True, False])
@pytest.mark.parametrize("run", [True, False])
@pytest.mark.parametrize("blank", [True, False])
def test_the_prose_of_an_upstream_sample_is_its_description_alone(
    level: bool, run: bool, blank: bool
) -> None:
    text = _upstream(level=level, run=run, blank=blank)
    assert example_prose(text) == PROSE
    assert example_prose(text) == example_prose(_without_header(text))


@pytest.mark.parametrize("level", [True, False])
@pytest.mark.parametrize("run", [True, False])
@pytest.mark.parametrize("blank", [True, False])
def test_the_lead_in_of_an_upstream_sample_is_its_description_alone(
    level: bool, run: bool, blank: bool
) -> None:
    text = _upstream(level=level, run=run, blank=blank)
    assert example_lead_in(text) == "Flips the page upside down."
    assert example_lead_in(text) == example_lead_in(_without_header(text))


def test_the_label_and_the_file_name_never_become_retrieval_terms() -> None:
    prose = example_prose(_upstream())
    assert (
        "pstream" not in prose
        and "widget_rotate" not in prose
        and "pinned" not in prose
        and "MIT" not in prose
    )
    lead_in = example_lead_in(_upstream(blank=False, level=False, run=False))
    assert "pstream" not in lead_in and "widget_rotate" not in lead_in
    tokens = set(relevance.tokenize(prose))
    assert not tokens & {"upstream", "widget_rotate", "adapted", "repository", "mit", "sample", "pinned"}


def test_a_multi_sentence_description_keeps_every_sentence_and_the_lead_in_its_first() -> None:
    text = _upstream(
        prose="Flips the page. Then turns it back.\nSaves it.", blank=False, run=False, level=False
    )
    assert example_prose(text) == "Flips the page. Then turns it back.\nSaves it."
    assert example_lead_in(text) == "Flips the page."


def test_the_header_prefixes_the_server_skips_are_the_lines_the_entrypoint_writes() -> None:
    assert upstream_sample_label(SAMPLE_PATH).startswith(relevance._UPSTREAM_SAMPLE_HEADER_LINE_PREFIXES)
    assert UPSTREAM_SAMPLE_LABEL.startswith(relevance._UPSTREAM_SAMPLE_HEADER_LINE_PREFIXES)
    assert SAMPLE_SOURCE_LINE.startswith(relevance._CHUNK_METADATA_LINE_PREFIXES)
    assert "Provenance: upstream-sample".startswith(relevance._CHUNK_METADATA_LINE_PREFIXES)


def test_an_authored_and_a_readme_chunk_are_described_exactly_as_before() -> None:
    assert example_prose(_authored()) == PROSE
    assert example_prose(_readme()) == PROSE
    assert example_lead_in(_authored()) == PROSE
    assert example_lead_in(_readme()) == PROSE
    # a legacy authored header with no closing blank line: its prose is unchanged
    legacy = f"FQN: Example: X\nKind: verified_example\nProvenance: authored\nVerification: passed\n{AUTHORED_LABEL}\n{PROSE}\n\nExample:\nx = 1"
    assert example_prose(legacy) == PROSE


# ---------------------------------------------------------------------------------------------------
# served end to end
# ---------------------------------------------------------------------------------------------------


class _NoGatewayEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    document = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="https://example.invalid/repo", commit=COMMIT),
        evidence_refs=(),
        title="pdf/python synthetic",
        body=body,
    )
    chunks = chunk_document(document)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease("pdf::python::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="python",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


def _padding(count: int) -> list[str]:
    return [
        f"FQN: Example: Pad{index} Gadget{index}\nKind: verified_example\nExample:\ngadget{index} = Gadget{index}()\n"
        f"Source-Commit: {COMMIT}"
        for index in range(count)
    ]


def test_exact_and_lexical_answers_carry_the_upstream_sample_provenance_and_note(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_upstream(), _readme("Split a file", "Splits one file.", "split()")])
    expected = _verification_note(_upstream())
    for query, source in (("Example: Flip a page", "exact"), ("flip a page", "lexical")):
        result = find_examples(store, SCOPE, query)
        assert isinstance(result, list) and [match.fqn for match in result] == ["Example: Flip a page"]
        assert (result[0].source, result[0].provenance) == (source, "upstream-sample")
        assert result[0].verification == expected
        assert "README" not in result[0].verification
    readme = find_examples(store, SCOPE, "Example: Split a file")
    assert isinstance(readme, list) and readme[0].provenance == "readme"
    assert readme[0].verification == _verification_note(
        _readme("Split a file", "Splits one file.", "split()")
    )


def test_an_authored_answer_is_unchanged(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_authored()])
    for query in ("Example: Flip a page", "flip a page"):
        result = find_examples(store, SCOPE, query)
        assert isinstance(result, list) and result[0].provenance == "authored"
        assert result[0].verification == _verification_note(_authored())
        assert result[0].verification.startswith("Authored for this index; ")


def test_an_upstream_sample_is_title_anchored_like_an_authored_example(tmp_path: Path) -> None:
    # One title word is enough, with the SAME basis and confidence an authored example with that title gets.
    results = {}
    for name, text in (("upstream-sample", _upstream()), ("authored", _authored())):
        result = find_examples(_publish(tmp_path / name, [text]), SCOPE, "flip")
        assert isinstance(result, list) and len(result) == 1, name
        results[name] = (result[0].match_basis, result[0].confidence, result[0].coverage)
    assert results["upstream-sample"] == results["authored"]
    assert results["upstream-sample"][0] == MATCH_BASIS_TITLE_WORD


@pytest.mark.parametrize("query", ["upstream", "adapted from", "test_widget_rotate", "pinned repository MIT"])
def test_the_label_and_the_file_name_are_not_what_a_query_finds(tmp_path: Path, query: str) -> None:
    store = _publish(tmp_path / "m", [_upstream()])
    assert isinstance(find_examples(store, SCOPE, query), NoExampleFound)


# an example that sits ABOVE the rare-code-word cap (12 examples): the README-only description rule of TC-839


def _above_cap(tmp_path: Path, target: str) -> GenerationManifestStore:
    return _publish(tmp_path, [target, *_padding(13)])


def test_above_the_cap_a_readme_description_naming_the_word_answers(tmp_path: Path) -> None:
    readme = _readme("Mount a Bracket", "Mounts a bracket and trims its flange edge.", CODE)
    result = find_examples(_above_cap(tmp_path, readme), SCOPE, "flange")
    assert isinstance(result, list) and result[0].match_basis == MATCH_BASIS_RARE_CODE_WORD


def test_above_the_cap_an_upstream_sample_description_is_not_evidence(tmp_path: Path) -> None:
    # The description of an upstream sample is prose written here around an adapted upstream file, a workflow
    # summary like an authored one's, not the upstream author's own tie of the example to the word: not admitted
    # by the description, exactly as an authored example is not.
    prose = "Mounts a bracket and trims its flange edge."
    for name, text in (
        ("upstream", _upstream("Mount a Bracket", prose, CODE)),
        ("authored", _authored("Mount a Bracket", prose, CODE)),
    ):
        assert isinstance(
            find_examples(_above_cap(tmp_path / name, text), SCOPE, "flange"), NoExampleFound
        ), name


def test_the_source_reads_the_description_rule_for_readme_only() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py").read_text(
        encoding="utf-8"
    )
    assert '_provenance(text) == "readme" and present_terms(' in source
