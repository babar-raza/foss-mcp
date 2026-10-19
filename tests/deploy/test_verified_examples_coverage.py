"""G3/TC-440: every pilot serves verified examples on the shipped data, and the review ledger has no OPEN row (REQ-G3-007).

This is the acceptance test of the release. It is offline and deterministic: it reads only the committed
infra/demo-manifests tree, the chart's values.yaml, docs/REVIEW_LEDGER.md and docs/RELEASE.md.

A verified example is a chunk of a pilot's baked lexical index whose text carries the line ``Kind: verified_example``.
An AUTHORED example (the line ``Provenance: authored`` as well) counts only when it also carries
``Verification: passed``. Chunks that merely discuss examples never count.

One pilot is a DOCUMENTED EXCEPTION (docs/DECISION_LOG.md, "tex/python cannot reach two verified examples"):
tex/python's pinned upstream library does not import, so it is held to its measured count instead of the minimum.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
LEDGER = REPO_ROOT / "docs" / "REVIEW_LEDGER.md"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"

# re-pinned by TC-551: 40 -> 32
EXPECTED_PILOTS = 32
MIN_VERIFIED_EXAMPLES = 2
# pilot -> the minimum that pilot must still reach (its measured count on 2026-10-14). An entry is removed as soon as it
# no longer lowers anything.
DOCUMENTED_EXCEPTIONS = {"tex_python": 1}

KIND_LINE = "Kind: verified_example"
AUTHORED_LINE = "Provenance: authored"
PASSED_LINE = "Verification: passed"
# TC-565 (REQ-G3-009): the run verdict the ingestion verifier writes into an example chunk.
RUNTIME_NOT_PROVEN_LINES = ("Runtime-Status: failed", "Runtime-Status: no_output")

_ID_SEP = "::"
LEDGER_STATUSES = ("FIXED", "ACCEPTED", "OPEN")
LEDGER_HEADER = "| ID | Finding | Severity | Status | Evidence |"
_LEDGER_ROW = re.compile(r"^\| (L-\d+) \| .* \| (?:Blocker|Major|Minor|Polish) \| (\S+) \| .* \|$")


# -- the pilots, derived from the chart --------------------------------------------------------------------------------


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def pilot_key(pilot: Mapping[str, Any]) -> str:
    """family_platform, e.g. pdf_net (the name the deploy tools take)."""
    return f"{pilot['family']}_{pilot['platform']}"


def scope_name(pilot: Mapping[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


PILOTS = chart_pilots()
PILOT_KEYS = [pilot_key(p) for p in PILOTS]
PILOTS_BY_KEY = {pilot_key(p): p for p in PILOTS}


# -- loading the baked generation the way the serving code does ---------------------------------------------------------


def load_active_documents(pilot: Mapping[str, Any]) -> dict[str, Any]:
    """The documents of the pilot's active generation: follow active_pointer.json to generation__{version}.json.

    The version is everything after the last ``::`` of the pointer's generation id, as
    ``GenerationManifestStore._generation_path_for_id`` reads it.
    """
    scope_dir = DEMO_MANIFESTS / scope_name(pilot)
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    version = pointer["generation_id"].rsplit(_ID_SEP, 1)[-1]
    generation = json.loads((scope_dir / f"generation__{version}.json").read_text(encoding="utf-8"))
    return generation["payload"]["lexical_index"]["documents"]


# -- counting ----------------------------------------------------------------------------------------------------------


def chunk_lines(chunk: Mapping[str, Any]) -> list[str]:
    return str(chunk.get("text", "")).split("\n")


def is_verified_example(chunk: Mapping[str, Any]) -> bool:
    """Counts toward the minimum: the verified kind, and an authored example only when it passed verification."""
    lines = chunk_lines(chunk)
    if KIND_LINE not in lines:
        return False
    if AUTHORED_LINE in lines and PASSED_LINE not in lines:
        return False
    # TC-565: an example whose run failed or produced nothing is served with its honest label, but it
    # was not proven to work, so it never counts toward the minimum.
    if any(line in lines for line in RUNTIME_NOT_PROVEN_LINES):
        return False
    return True


def count_verified_examples(documents: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> int:
    chunks = documents.values() if isinstance(documents, Mapping) else documents
    return sum(1 for chunk in chunks if is_verified_example(chunk))


def required_minimum(key: str) -> int:
    return DOCUMENTED_EXCEPTIONS.get(key, MIN_VERIFIED_EXAMPLES)


# -- the coverage assertions on the shipped data --------------------------------------------------------------------------


def test_the_chart_names_thirty_two_pilots() -> None:
    assert len(PILOTS) == EXPECTED_PILOTS
    assert len(set(PILOT_KEYS)) == EXPECTED_PILOTS


def test_the_minimum_is_two() -> None:
    assert MIN_VERIFIED_EXAMPLES == 2


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_pilot_serves_the_required_number_of_verified_examples(key: str) -> None:
    count = count_verified_examples(load_active_documents(PILOTS_BY_KEY[key]))
    required = required_minimum(key)
    assert count >= required, f"{key} serves {count} verified examples, the required minimum is {required}"


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_no_chunk_carries_both_kinds_of_provenance(key: str) -> None:
    """A verified example is either taken from upstream (no Provenance line) or authored, never both and never mixed."""
    for chunk in load_active_documents(PILOTS_BY_KEY[key]).values():
        if KIND_LINE not in chunk_lines(chunk):
            continue
        provenance = {ln for ln in chunk_lines(chunk) if ln.startswith("Provenance:")}
        assert provenance <= {AUTHORED_LINE}, (
            f"{key} chunk {chunk.get('chunk_id')} carries {sorted(provenance)}"
        )


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_every_authored_example_on_the_shipped_data_passed_verification(key: str) -> None:
    for chunk in load_active_documents(PILOTS_BY_KEY[key]).values():
        lines = chunk_lines(chunk)
        if KIND_LINE in lines and AUTHORED_LINE in lines:
            assert PASSED_LINE in lines, (
                f"{key} chunk {chunk.get('chunk_id')} is authored without {PASSED_LINE!r}"
            )


# -- the counting helper on synthetic generations -----------------------------------------------------------------------


def _chunk(chunk_id: str, *lines: str) -> dict[str, Any]:
    return {"chunk_id": chunk_id, "text": "\n".join(lines), "tokens": []}


def _generation(*chunks: dict[str, Any]) -> dict[str, Any]:
    return {f"scope::doc::{c['chunk_id']}": c for c in chunks}


def _example(chunk_id: str) -> dict[str, Any]:
    return _chunk(chunk_id, f"FQN: Example: {chunk_id}", KIND_LINE, "Example:", "print(1)")


def test_counting_zero_examples() -> None:
    assert count_verified_examples({}) == 0
    assert count_verified_examples(_generation(_chunk("a", "FQN: Foo", "Kind: class"))) == 0


def test_counting_one_example() -> None:
    assert count_verified_examples(_generation(_example("a"), _chunk("b", "FQN: Foo", "Kind: class"))) == 1


def test_counting_two_examples() -> None:
    assert count_verified_examples(_generation(_example("a"), _example("b"))) == 2


def test_counting_accepts_a_plain_list_of_chunks() -> None:
    assert count_verified_examples([_example("a"), _example("b"), _example("c")]) == 3


def test_an_authored_example_without_verification_passed_does_not_count() -> None:
    authored_unverified = _chunk("a", "FQN: Example: a", KIND_LINE, AUTHORED_LINE, "Example:", "x = 1")
    assert count_verified_examples(_generation(authored_unverified)) == 0
    failed = _chunk(
        "b", "FQN: Example: b", KIND_LINE, AUTHORED_LINE, "Verification: failed", "Example:", "x = 1"
    )
    assert count_verified_examples(_generation(failed)) == 0


def test_an_authored_example_with_verification_passed_counts() -> None:
    authored = _chunk("a", "FQN: Example: a", KIND_LINE, AUTHORED_LINE, PASSED_LINE, "Example:", "x = 1")
    assert count_verified_examples(_generation(authored, _example("b"))) == 2


def test_a_documentation_chunk_that_mentions_the_word_verified_does_not_count() -> None:
    doc = _chunk(
        "a",
        "FQN: Doc: Testing",
        "Kind: documentation",
        "Every example in this guide was verified; a verified example is one that ran. Kind: verified_example is "
        "the label the index uses.",
    )
    other = _chunk("b", "FQN: Doc: Notes", "verified_example", "Kind: verified_examples")
    assert count_verified_examples(_generation(doc, other)) == 0


def test_an_unverified_example_kind_does_not_count() -> None:
    assert count_verified_examples(_generation(_chunk("a", "FQN: Example: a", "Kind: example"))) == 0


# -- TC-565: an example that compiled but failed at runtime, or produced nothing, is not a verified example ----------------


def _ran(chunk_id: str, status: str) -> dict[str, Any]:
    return _chunk(
        chunk_id, f"FQN: Example: {chunk_id}", KIND_LINE, f"Runtime-Status: {status}", "Example:", "print(1)"
    )


def test_tc565_an_example_that_ran_counts() -> None:
    assert count_verified_examples(_generation(_ran("a", "ran"))) == 1


def test_tc565_an_example_that_failed_at_runtime_does_not_count() -> None:
    assert count_verified_examples(_generation(_ran("a", "failed"))) == 0


def test_tc565_an_example_that_produced_no_output_does_not_count() -> None:
    assert count_verified_examples(_generation(_ran("a", "no_output"))) == 0


def test_tc565_a_legacy_chunk_without_a_runtime_line_still_counts() -> None:
    assert count_verified_examples(_generation(_example("a"))) == 1


def test_tc565_two_failing_examples_do_not_reach_the_minimum() -> None:
    generation = _generation(_ran("a", "failed"), _ran("b", "no_output"), _ran("c", "ran"))
    assert count_verified_examples(generation) == 1 < MIN_VERIFIED_EXAMPLES


# -- the documented exception --------------------------------------------------------------------------------------------


def test_the_exception_mapping_holds_only_tex_python() -> None:
    assert DOCUMENTED_EXCEPTIONS == {"tex_python": 1}


def test_every_exception_names_a_pilot_of_the_chart() -> None:
    assert set(DOCUMENTED_EXCEPTIONS) <= set(PILOT_KEYS)


def test_every_exception_still_lowers_the_minimum() -> None:
    """An exception whose value reaches the minimum is stale and must be removed."""
    for key, minimum in DOCUMENTED_EXCEPTIONS.items():
        assert minimum < MIN_VERIFIED_EXAMPLES, f"the exception for {key} no longer lowers anything"


def test_only_the_excepted_pilot_has_a_lower_minimum() -> None:
    lowered = {key for key in PILOT_KEYS if required_minimum(key) != MIN_VERIFIED_EXAMPLES}
    assert lowered == set(DOCUMENTED_EXCEPTIONS)


def test_release_md_names_the_excepted_pilot_as_a_known_limitation() -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    start = text.index("## 8. Known limitations")
    end = text.index("\n## 9.", start)
    section = " ".join(text[start:end].split())
    assert "tex/python" in section
    assert "one verified example" in section


# re-pinned by TC-553: test_release_md_names_the_mixed_pdf_typescript_index_as_a_known_limitation asserted "MIXED" in section 8 -> the baked
# pdf/typescript index is ONE commit (the chart pin) since the re-ingest, so section 8 must no longer call it mixed.
def test_release_md_no_longer_calls_the_pdf_typescript_index_mixed() -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    start = text.index("## 8. Known limitations")
    end = text.index("\n## 9.", start)
    section = " ".join(text[start:end].split())
    assert "MIXED" not in section
    assert "mixed index" not in section.lower()


# -- the review ledger -----------------------------------------------------------------------------------------------------


def _ledger_rows() -> list[tuple[str, str, str]]:
    """(id, status, the whole row) for each finding row of the ledger table."""
    rows: list[tuple[str, str, str]] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        match = _LEDGER_ROW.match(line)
        if match:
            rows.append((match.group(1), match.group(2), line))
    return rows


def test_the_review_ledger_exists() -> None:
    assert LEDGER.is_file(), "docs/REVIEW_LEDGER.md does not exist"


def test_the_review_ledger_has_the_header_row() -> None:
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    assert LEDGER_HEADER in lines
    assert lines[lines.index(LEDGER_HEADER) + 1].startswith("|---")


def test_the_review_ledger_has_finding_rows() -> None:
    rows = _ledger_rows()
    assert rows, "the ledger has no finding rows"
    ids = [row[0] for row in rows]
    assert len(set(ids)) == len(ids), "a finding id appears twice"


def test_every_ledger_table_line_is_a_well_formed_row() -> None:
    """No table line escapes the row pattern, so the status checks below see every row."""
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    table = [ln for ln in lines[lines.index(LEDGER_HEADER) + 2 :] if ln.startswith("|")]
    assert len(table) == len(_ledger_rows())


def test_every_ledger_status_is_fixed_accepted_or_open() -> None:
    for finding_id, status, _ in _ledger_rows():
        assert status in LEDGER_STATUSES, f"{finding_id} has the status {status!r}"


def test_no_ledger_row_is_open() -> None:
    open_rows = [finding_id for finding_id, status, _ in _ledger_rows() if status == "OPEN"]
    assert open_rows == [], f"ledger rows still OPEN: {open_rows}"


def test_every_fixed_ledger_row_names_a_card() -> None:
    fixed = [(finding_id, row) for finding_id, status, row in _ledger_rows() if status == "FIXED"]
    assert fixed, "the ledger has no FIXED row"
    missing = [finding_id for finding_id, row in fixed if not re.search(r"\bTC-\d+\b", row)]
    assert missing == [], f"FIXED rows with no TC- card id: {missing}"
