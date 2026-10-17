"""TC-547: a COMMITTED verification cache is verifiable offline, whether it is empty or populated.

tests/fixtures/verified_examples/<family>_<platform>.json is passed to infra/build_chunks.py
``--verification-cache`` by docker-compose.yml and the Helm chart, and Dockerfile.ingestion bakes it into
the ingestion image. The TC-499 guard forced these files to stay empty, which also forbade the verdicts
they exist to receive (scripts/release/pull_verification_cache.py merges them in from the cluster run).
This module is what every committed cache must satisfy instead: canonical and sorted, small, free of
credentials, and never contradicting the verified examples the pilot actually serves (read from the
pilot's committed generation in infra/demo-manifests). Offline and deterministic: no network, no
toolchain, no cluster. With empty files all of it passes vacuously except the structure checks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.mcp.tools.find_examples import _extract_example, _provenance, is_verified_example_text

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "tests" / "fixtures" / "verified_examples"
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
MAX_CACHE_BYTES = 100_000
# re-pinned by TC-551: 40 -> 32
EXPECTED_PILOT_COUNT = 32

# The shapes of a credential that must never be committed in a cache entry's compiler/interpreter output.
_CREDENTIAL_SHAPES: dict[str, re.Pattern[str]] = {
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "github fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "aws access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "api secret key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "private key block": re.compile(r"-----BEGIN"),
}

Triple = tuple[str, str, str]


# ---------------------------------------------------------------------------------------------------
# helpers (pure functions, exercised on synthetic data and on the committed files)
# ---------------------------------------------------------------------------------------------------


def contradictions(entries: list[dict], examples: list[Triple]) -> list[Triple]:
    """The served ``examples`` ((provenance, normalised code sha256, library commit) each) for which a cache
    entry with the SAME triple says ``verified`` is false: the cache and the generation disagree."""
    failed = {
        (entry["provenance"], entry["code_sha256"], entry["library_commit"])
        for entry in entries
        if entry["verified"] is False
    }
    return [example for example in examples if example in failed]


def oversize(raw: bytes) -> bool:
    """True when a cache document is not under MAX_CACHE_BYTES."""
    return len(raw) >= MAX_CACHE_BYTES


def credential_shapes(text: str) -> list[str]:
    """The names of the credential shapes found in ``text`` (empty for plain compiler output)."""
    return [name for name, pattern in _CREDENTIAL_SHAPES.items() if pattern.search(text)]


def _entry(**overrides: Any) -> dict:
    base: dict[str, Any] = {
        "provenance": "readme",
        "title": "Create a document",
        "code_sha256": "a" * 64,
        "library_commit": "1" * 40,
        "platform": "net",
        "toolchain": "10.0.100",
        "verified": True,
        "output": "Build succeeded.",
    }
    base.update(overrides)
    return base


def _cache_names() -> list[str]:
    return sorted(path.name for path in CACHE_DIR.glob("*.json"))


def _cache_cases() -> list[Any]:
    return [pytest.param(name, id=name.removesuffix(".json")) for name in _cache_names()]


def _generation_path(name: str) -> Path:
    family, _, platform = name.removesuffix(".json").partition("_")
    directory = MANIFESTS / f"{family}__{platform}__self_extracted"
    pointer = json.loads((directory / "active_pointer.json").read_text(encoding="utf-8"))
    version = pointer["generation_id"].rpartition("::")[2]
    return directory / f"generation__{version}.json"


def served_examples(name: str) -> list[Triple]:
    """(provenance, normalised code sha256, library commit) of every verified example the pilot serves."""
    generation = json.loads(_generation_path(name).read_text(encoding="utf-8"))
    served: list[Triple] = []
    for document in generation["payload"]["lexical_index"]["documents"].values():
        text = document["text"]
        if not is_verified_example_text(text):
            continue
        code = _extract_example(text)
        commits = [
            line[len("Source-Commit:") :].strip()
            for line in text.splitlines()
            if line.startswith("Source-Commit:")
        ]
        assert code is not None and commits, (
            f"{name}: a verified example chunk has no code or no Source-Commit line"
        )
        served.append((_provenance(text), build_chunks.normalised_code_sha256(code), commits[-1]))
    return served


# ---------------------------------------------------------------------------------------------------
# synthetic tests of the helpers
# ---------------------------------------------------------------------------------------------------


def test_contradictions_flags_a_failed_verdict_for_a_served_example() -> None:
    entries = [
        _entry(code_sha256="b" * 64, verified=False),
        _entry(code_sha256="c" * 64, verified=True),
    ]
    examples = [
        ("readme", "b" * 64, "1" * 40),
        ("readme", "c" * 64, "1" * 40),
        ("readme", "d" * 64, "1" * 40),
    ]
    assert contradictions(entries, examples) == [("readme", "b" * 64, "1" * 40)]


def test_contradictions_ignores_other_commits_and_other_hashes() -> None:
    entries = [_entry(code_sha256="b" * 64, library_commit="2" * 40, verified=False)]
    assert contradictions(entries, [("readme", "b" * 64, "1" * 40)]) == []
    assert contradictions(entries, [("readme", "e" * 64, "2" * 40)]) == []
    assert contradictions(entries, [("authored", "b" * 64, "2" * 40)]) == []
    assert contradictions([], [("readme", "b" * 64, "2" * 40)]) == []


def test_the_size_guard_flags_a_100001_byte_document() -> None:
    assert oversize(b"x" * 100_001)
    assert oversize(b"x" * 100_000)
    assert not oversize(b"x" * 99_999)


def test_the_credential_guard_flags_a_token_shape() -> None:
    assert credential_shapes("token ghp_" + "a" * 20 + " leaked") == ["github token"]
    assert credential_shapes("key AKIA" + "A" * 16 + " leaked") == ["aws access key"]
    assert credential_shapes("-----" + "BEGIN RSA PRIVATE KEY-----") == ["private key block"]
    assert (
        credential_shapes("Build succeeded.\n0 Warning(s)\n0 Error(s)\nerror CS0246: type 'Foo' not found")
        == []
    )
    assert credential_shapes("main.go:12:5: undefined: ghp_x\nwarning: unused variable `AKIA`") == []


def test_a_populated_synthetic_cache_round_trips_through_merge_and_document_deterministically() -> None:
    entries = [
        _entry(title="Zeta", code_sha256="f" * 64),
        _entry(title="Alpha", code_sha256="e" * 64, verified=False, output="error CS1002"),
        _entry(title="Alpha", code_sha256="e" * 64, toolchain="10.0.200"),
        _entry(title="Beta", provenance="authored", code_sha256="d" * 64),
    ]
    merged = build_chunks.merge_verification_cache([], entries)
    assert [e["title"] for e in merged] == ["Beta", "Alpha", "Alpha", "Zeta"]  # authored sorts before readme
    document = build_chunks.verification_cache_document(merged)
    assert build_chunks.parse_verification_cache(document) == merged
    assert build_chunks.merge_verification_cache([], merged) == merged
    assert build_chunks.merge_verification_cache(merged, merged) == merged
    assert (
        build_chunks.verification_cache_document(
            build_chunks.merge_verification_cache([], list(reversed(entries)))
        )
        == document
    )
    assert document.endswith("\n") and "\r" not in document


def test_there_is_one_committed_cache_per_pilot() -> None:
    assert len(_cache_names()) >= EXPECTED_PILOT_COUNT


# ---------------------------------------------------------------------------------------------------
# the committed caches, one case per pilot
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", _cache_cases())
def test_the_committed_cache_has_no_contradiction_with_the_served_examples(name: str) -> None:
    entries = build_chunks.load_verification_cache(CACHE_DIR / name)
    served = served_examples(name)
    assert contradictions(entries, served) == [], (
        f"{name}: a cache entry says verified=false for an example the pilot serves as verified"
    )


@pytest.mark.parametrize("name", _cache_cases())
def test_the_committed_cache_is_small_and_credential_free(name: str) -> None:
    raw = (CACHE_DIR / name).read_bytes()
    assert not oversize(raw), f"{name} is {len(raw)} bytes, not under {MAX_CACHE_BYTES}"
    found = credential_shapes(raw.decode("utf-8"))
    assert found == [], f"{name} contains a credential shape: {found}"


@pytest.mark.parametrize("name", _cache_cases())
def test_the_committed_cache_is_canonical_and_sorted(name: str) -> None:
    entries = build_chunks.load_verification_cache(CACHE_DIR / name)
    assert entries == build_chunks.merge_verification_cache([], entries), (
        f"{name}: entries are not unique and in merge_verification_cache's order"
    )


def test_the_cache_coverage_is_reported_not_asserted() -> None:
    total = 0
    covered = 0
    for name in _cache_names():
        entries = build_chunks.load_verification_cache(CACHE_DIR / name)
        verified = {
            (entry["provenance"], entry["code_sha256"], entry["library_commit"])
            for entry in entries
            if entry["verified"] is True
        }
        served = served_examples(name)
        total += len(served)
        covered += sum(1 for example in served if example in verified)
    print(f"verification cache coverage: {covered} of {total} served verified examples have a verified entry")
    assert 0 <= covered <= total


def test_the_thirty_two_committed_caches_hold_200_or_more_entries_in_total() -> None:
    # TC-548: the total pulled from the 40 serving pods was 261; TC-551 removed eight caches (231 entries remain); the floor leaves room for a refresh.
    total = sum(len(build_chunks.load_verification_cache(CACHE_DIR / name)) for name in _cache_names())
    assert total >= 200, f"only {total} entries across {len(_cache_names())} committed caches"


def test_every_committed_cache_entry_is_verified_true_or_a_recorded_failure() -> None:
    verified = 0
    for name in _cache_names():
        for entry in build_chunks.load_verification_cache(CACHE_DIR / name):
            assert isinstance(entry["verified"], bool), f"{name}: verified is not a boolean"
            verified += entry["verified"] is True
    assert verified >= 150, f"only {verified} entries are verified=true"
