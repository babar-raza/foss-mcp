"""TC-510: the production publish policy (REQ-G3-008, findings GRR-05 and GRR-06 override part).

An independent review drove the real publisher with synthetic generations and found that the
guard compared only the TOTAL document count with the active generation: a generation that lost
every symbol, every doc or every example, one that was wholly different content, blank chunks,
chunks with no ``FQN:`` line, 100 identical chunks and a first publish of one trivial chunk were
all activated, and repeated 50 percent cuts eroded a scope without ever tripping the check. These
tests rebuild that scenario helper (``mk``) and pin the repaired contract offline, on ``tmp_path``
only: ``policy=None`` is byte for byte the old behaviour; a ``PublishPolicy`` adds per-kind stats,
floors, per-kind limits against the maximum of the last three generations, duplicate / empty /
identity refusals, an exactly matching logged override and a ``publish_log.jsonl``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from foss_mcp.indexing import generation_manifest as gm
from foss_mcp.indexing.generation_manifest import (
    MIN_BASELINE_FOR_PERCENT_RULE,
    PRODUCTION_MIN_DOCS,
    PRODUCTION_MIN_SYMBOLS,
    PUBLISH_LOG_NAME,
    RETENTION_KEEP_LAST,
    GenerationManifestStore,
    InvalidScopeError,
    PublishPolicy,
    PublishSafetyError,
    classify_chunk_text,
    payload_stats,
)
from foss_mcp.indexing.hashing_embedding_provider import HashingEmbeddingProvider
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILY, PLATFORM, SOURCE_KIND = "fam", "plat", "src"
SCOPE = "::".join((FAMILY, PLATFORM, SOURCE_KIND))
SCOPE_DIR_NAME = "__".join((FAMILY, PLATFORM, SOURCE_KIND))

_TEXT = {
    "symbol": "FQN: Sym{i}\nKind: type_spec\nA documented type number {i}.",
    "doc": "FQN: Doc: Guide{i}\nA guide section number {i}.",
    "example": "FQN: Example: Demo{i}\nAn example number {i}.",
}


def _chunk(title: str, text: str | None, repo: str = "org/repo-a") -> Chunk:
    return Chunk(
        section_title=title,
        text=text,  # type: ignore[arg-type]
        source_kind="self_extracted",
        content_type="api_surface",
        provenance=Provenance(repository=repo, commit="c" * 40, path="api_surface.json"),
        trust_tier="highest",
    )


def mk(kind: str, i: int, repo: str = "org/repo-a") -> Chunk:
    return _chunk(f"{kind}-{i}", _TEXT[kind].format(i=i), repo)


def counts(symbols: int, docs: int, examples: int, repo: str = "org/repo-a") -> list[Chunk]:
    return (
        [mk("symbol", i, repo) for i in range(symbols)]
        + [mk("doc", i, repo) for i in range(docs)]
        + [mk("example", i, repo) for i in range(examples)]
    )


def publish(
    store: GenerationManifestStore,
    chunks: list[Chunk],
    policy: PublishPolicy | None = None,
    *,
    held_by: str = "test",
) -> str:
    expected = store.read_active(SCOPE)
    lease = store.acquire_lease(SCOPE, held_by, "pending")
    return publish_generation(
        store,
        family=FAMILY,
        platform=PLATFORM,
        source_kind=SOURCE_KIND,
        expected_active=expected,
        chunks=chunks,
        embedding_provider=HashingEmbeddingProvider(),
        lease=lease,
        policy=policy,
    )


def _scope_dir(store: GenerationManifestStore) -> Path:
    return store.root / SCOPE_DIR_NAME


def _generation_files(store: GenerationManifestStore) -> list[str]:
    return sorted(path.name for path in _scope_dir(store).glob("generation__*.json"))


def _published(store: GenerationManifestStore) -> list[dict]:
    return [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "published"]


@pytest.fixture
def store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


# --------------------------------------------------------------------------------------
# Classification and statistics
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("FQN: Aspose.Pdf.Document\nKind: type_spec", "symbol"),
        ("FQN:Aspose.Pdf.Document", "symbol"),
        ("FQN: Doc: Install guide\nbody", "doc"),
        ("FQN: Example: Open a file\nbody", "example"),
        ("Just prose, no FQN line at all.", "doc"),
        ("", "doc"),
        ("intro line\nFQN: Example: Late\nmore", "example"),
        ("FQN: Doc: First\nFQN: Sym.Second", "doc"),
        ("FQN: Examples are not examples", "symbol"),
        ("  FQN: Sym", "doc"),
    ],
)
def test_classify_chunk_text_kinds(text: str, kind: str) -> None:
    assert classify_chunk_text(text) == kind


def _baked_generation_files() -> list[Path]:
    return sorted((REPO_ROOT / "infra" / "demo-manifests").glob("*/generation__*.json"))


def test_classification_agrees_with_relevance_chunk_kind_on_every_baked_document() -> None:
    expected = {SYMBOL: "symbol", EXAMPLE: "example", DOCUMENTATION: "doc"}
    files = _baked_generation_files()
    # re-pinned by TC-551: 40 -> 32
    assert len(files) >= 32
    checked = 0
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for document in doc["payload"]["lexical_index"]["documents"].values():
            assert classify_chunk_text(document["text"]) == expected[chunk_kind(document["text"])], (
                path.parent.name
            )
            checked += 1
    assert checked > 0


def test_payload_stats_counts_each_kind() -> None:
    documents = {
        f"d{i}": {"text": text}
        for i, text in enumerate(
            [
                _TEXT["symbol"].format(i=1),
                _TEXT["symbol"].format(i=2),
                _TEXT["doc"].format(i=1),
                "plain prose",
                _TEXT["example"].format(i=1),
            ]
        )
    }
    documents["bad"] = {"text": None}
    stats = payload_stats({"lexical_index": {"documents": documents}})
    assert stats == {"symbols": 2, "docs": 3, "examples": 1, "total": 6}
    assert payload_stats({}) == {"symbols": 0, "docs": 0, "examples": 0, "total": 0}
    assert payload_stats({"lexical_index": {}}) == {"symbols": 0, "docs": 0, "examples": 0, "total": 0}


def test_production_constants_are_the_reviewed_values() -> None:
    assert PRODUCTION_MIN_SYMBOLS == 3
    assert PRODUCTION_MIN_DOCS == 1
    assert RETENTION_KEEP_LAST == 3
    assert MIN_BASELINE_FOR_PERCENT_RULE == 5
    assert PUBLISH_LOG_NAME == "publish_log.jsonl"
    policy = PublishPolicy()
    assert (policy.min_symbols, policy.min_docs) == (3, 1)
    assert (policy.max_drop, policy.max_symbol_drop, policy.history) == (0.5, 0.2, 3)
    assert policy.accept_regressions == ()
    assert policy.reason is None


# --------------------------------------------------------------------------------------
# policy=None is today's behaviour; a policy adds stats and a log
# --------------------------------------------------------------------------------------


def test_policy_none_adds_no_stats_or_log_and_keeps_the_legacy_guard(store: GenerationManifestStore) -> None:
    generation_id = publish(store, counts(2, 0, 0))  # legacy guard accepts a 2-chunk first publish
    payload = store.read_generation(SCOPE, generation_id).payload
    assert set(payload) == {"vector_index", "lexical_index"}
    assert sorted(path.name for path in _scope_dir(store).iterdir()) == sorted(
        ["active_pointer.json", f"generation__{generation_id.rsplit('::', 1)[-1]}.json", "lease_state.json"]
    )
    assert store.read_publish_log(SCOPE) == []
    with pytest.raises(PublishSafetyError):
        publish(store, [])
    with pytest.raises(PublishSafetyError):
        gm.assert_publish_is_safe(0, None)
    with pytest.raises(PublishSafetyError):
        gm.assert_publish_is_safe(1, 4)
    gm.assert_publish_is_safe(2, 4)
    gm.assert_publish_is_safe(5, 4)


def test_policy_publish_persists_stats_and_appends_log_line(store: GenerationManifestStore) -> None:
    generation_id = publish(store, counts(5, 3, 2), PublishPolicy(held_by="job-1"))
    payload = store.read_generation(SCOPE, generation_id).payload
    assert payload["stats"] == {
        "symbols": 5,
        "docs": 3,
        "examples": 2,
        "total": 10,
        "duplicate_chunk_ids": 0,
        "empty_text_chunks": 0,
        "repositories": ["org/repo-a"],
    }
    log = store.read_publish_log(SCOPE)
    assert len(log) == 1
    entry = log[0]
    assert entry["event"] == "published"
    assert entry["scope"] == SCOPE
    assert entry["generation_id"] == generation_id
    assert entry["previous_active"] is None
    assert entry["held_by"] == "job-1"
    assert entry["stats"] == payload["stats"]
    assert entry["baseline"] == {"symbols": 0, "docs": 0, "examples": 0, "total": 0}
    assert entry["overrides"] == []
    assert entry["floors"] == {"symbols": 3, "docs": 1}
    assert entry["ts"]
    assert store.read_active(SCOPE) == generation_id


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------


def _identical_100() -> list[Chunk]:
    return [mk("symbol", 0)] * 100


def _blank_50() -> list[Chunk]:
    return [_chunk(f"blank-{i}", "") for i in range(50)]


def _whitespace_50() -> list[Chunk]:
    return [_chunk(f"ws-{i}", "  \n\t ") for i in range(50)]


def _text_none() -> list[Chunk]:
    return [*counts(5, 3, 1), _chunk("none", None)]


def _prose_50() -> list[Chunk]:
    return [_chunk(f"prose-{i}", f"Unrelated prose number {i} with no marker line.") for i in range(50)]


_BASE = (40, 50, 10)

REFUSAL_CASES: list[tuple[str, tuple[int, int, int] | None, Callable[[], list[Chunk]]]] = [
    ("empty_first_publish", None, lambda: []),
    ("single_trivial_chunk", None, lambda: [mk("doc", 0)]),
    ("two_symbols", None, lambda: counts(2, 5, 1)),
    ("zero_docs", None, lambda: counts(5, 0, 1)),
    ("all_symbols_lost", _BASE, lambda: counts(0, 50, 10)),
    ("all_docs_lost", _BASE, lambda: counts(40, 0, 10)),
    ("all_examples_lost", _BASE, lambda: counts(40, 50, 0)),
    ("symbols_more_than_20_percent_down", _BASE, lambda: counts(31, 50, 10)),
    ("docs_50_to_20", _BASE, lambda: counts(40, 20, 10)),
    ("total_over_50_percent_down", _BASE, lambda: counts(20, 25, 4)),
    ("hundred_identical_chunks", None, _identical_100),
    ("fifty_empty_text_chunks", None, _blank_50),
    ("fifty_whitespace_only_chunks", None, _whitespace_50),
    ("a_text_none_chunk", None, _text_none),
    ("fifty_prose_chunks_with_no_fqn_line", None, _prose_50),
    ("changed_provenance_repository", (10, 10, 3), lambda: counts(10, 10, 3, repo="org/other-repo")),
]


@pytest.mark.parametrize(("name", "prior", "build"), REFUSAL_CASES, ids=[case[0] for case in REFUSAL_CASES])
def test_refusals(
    store: GenerationManifestStore,
    name: str,
    prior: tuple[int, int, int] | None,
    build: Callable[[], list[Chunk]],
) -> None:
    policy = PublishPolicy()
    active = publish(store, counts(*prior), policy) if prior is not None else None
    files_before = _generation_files(store)
    log_before = len(store.read_publish_log(SCOPE))

    with pytest.raises(PublishSafetyError):
        publish(store, build(), policy)

    assert store.read_active(SCOPE) == active
    assert _generation_files(store) == files_before
    log = store.read_publish_log(SCOPE)
    assert len(log) == log_before + 1
    assert log[-1]["event"] == "refused"
    assert log[-1]["scope"] == SCOPE
    assert log[-1]["reason"]


def test_boundaries_are_accepted(tmp_path: Path) -> None:
    def fresh(name: str) -> GenerationManifestStore:
        return GenerationManifestStore(tmp_path / name)

    # symbols 40 -> 32 is exactly 20 percent; docs 50 -> 25 and examples 10 -> 5 are exactly 50 percent.
    boundary = fresh("symbols")
    publish(boundary, counts(40, 50, 10), PublishPolicy())
    publish(boundary, counts(32, 50, 10), PublishPolicy())
    publish(boundary, counts(32, 25, 5), PublishPolicy())

    # total 100 -> 50 exactly (floors lowered so only the total rule is in play).
    total = fresh("total")
    lenient = PublishPolicy(min_symbols=0)
    publish(total, counts(0, 50, 50), lenient)
    publish(total, counts(0, 25, 25), lenient)

    # examples 2 -> 0: a baseline below 3 does not trip the zero-examples rule.
    examples = fresh("examples")
    publish(examples, counts(10, 10, 2), PublishPolicy())
    publish(examples, counts(10, 10, 0), PublishPolicy())

    # baselines below MIN_BASELINE_FOR_PERCENT_RULE carry no percent rule at all.
    tiny = fresh("tiny")
    publish(tiny, counts(4, 2, 0), PublishPolicy())
    publish(tiny, counts(3, 1, 0), PublishPolicy())

    # one kind moving up while another moves down within its limit is a normal publish.
    mixed = fresh("mixed")
    publish(mixed, counts(10, 10, 3), PublishPolicy())
    publish(mixed, counts(30, 6, 3), PublishPolicy())


def test_the_legacy_total_guard_still_applies_below_the_percent_rule_baseline(
    store: GenerationManifestStore,
) -> None:
    lenient = PublishPolicy(min_symbols=0, min_docs=0)
    active = publish(store, counts(0, 4, 0), lenient)
    with pytest.raises(PublishSafetyError) as excinfo:
        publish(store, counts(0, 1, 0), lenient)  # 1 < half of 4
    assert str(excinfo.value).endswith("--accept-regression total:4->1 --reason TEXT")
    assert store.read_active(SCOPE) == active
    accepted = PublishPolicy(
        min_symbols=0, min_docs=0, accept_regressions=(("total", 4, 1),), reason="deliberate"
    )
    publish(store, counts(0, 1, 0), accepted)
    assert _published(store)[-1]["overrides"] == ["total:4->1"]
    other = GenerationManifestStore(store.root.parent / "exactly-half")
    publish(other, counts(0, 4, 0), lenient)
    publish(other, counts(0, 2, 0), lenient)  # exactly half is accepted


def test_repeated_cuts_cannot_erode_a_scope(store: GenerationManifestStore) -> None:
    policy = PublishPolicy()
    publish(store, counts(100, 5, 0), policy)
    publish(store, counts(80, 5, 0), policy)  # exactly 20 percent below the previous one
    # 64 is exactly 20 percent below 80 (the previous generation alone would accept it) but 36 percent
    # below the maximum of the last three.
    with pytest.raises(PublishSafetyError) as excinfo:
        publish(store, counts(64, 5, 0), policy)
    assert "symbols fell from 100 to 64" in str(excinfo.value)
    assert [e["stats"]["symbols"] for e in _published(store)] == [100, 80]


def test_baseline_is_the_max_of_the_last_three_not_just_the_active(store: GenerationManifestStore) -> None:
    policy = PublishPolicy()
    for symbols in (100, 90, 85):
        publish(store, counts(symbols, 5, 0), policy)
    with pytest.raises(PublishSafetyError):
        publish(store, counts(78, 5, 0), policy)  # 100 is still inside the window of three
    publish(store, counts(82, 5, 0), policy)  # window [85, 90, 100] -> 80 allowed
    # the 100 has now left the window of three: [82, 85, 90] -> floor 72
    publish(store, counts(73, 5, 0), policy)
    assert _published(store)[-1]["baseline"]["symbols"] == 90
    assert store.read_active(SCOPE) == _published(store)[-1]["generation_id"]


def test_a_legacy_active_generation_without_stats_is_the_baseline_via_text_classification(
    store: GenerationManifestStore,
) -> None:
    legacy_id = publish(store, counts(40, 50, 10))  # policy=None: no stats, no log
    assert "stats" not in store.read_generation(SCOPE, legacy_id).payload
    assert store.read_publish_log(SCOPE) == []
    with pytest.raises(PublishSafetyError):
        publish(store, counts(31, 50, 10), PublishPolicy())
    # a legacy generation has no repositories list, so the identity check is skipped for it
    publish(store, counts(40, 50, 10, repo="org/repo-b"), PublishPolicy())
    assert _published(store)[-1]["baseline"] == {"symbols": 40, "docs": 50, "examples": 10, "total": 100}


# --------------------------------------------------------------------------------------
# The explicit override
# --------------------------------------------------------------------------------------

_SHRINK = (("symbols", 40, 24), ("docs", 50, 20), ("examples", 10, 2), ("total", 100, 46))


def test_accept_regression_publishes_and_logs_the_reason(store: GenerationManifestStore) -> None:
    publish(store, counts(*_BASE), PublishPolicy())
    with pytest.raises(PublishSafetyError):
        publish(store, counts(24, 20, 2), PublishPolicy())
    policy = PublishPolicy(accept_regressions=_SHRINK, reason="upstream removed the vendored directory")
    generation_id = publish(store, counts(24, 20, 2), policy)
    assert store.read_active(SCOPE) == generation_id
    entry = _published(store)[-1]
    assert entry["overrides"] == ["symbols:40->24", "docs:50->20", "examples:10->2", "total:100->46"]
    assert entry["reason"] == "upstream removed the vendored directory"
    assert entry["baseline"] == {"symbols": 40, "docs": 50, "examples": 10, "total": 100}
    assert entry["stats"]["total"] == 46


def test_accept_regression_with_wrong_numbers_is_refused(store: GenerationManifestStore) -> None:
    active = publish(store, counts(*_BASE), PublishPolicy())
    wrong = (("symbols", 40, 25), *_SHRINK[1:])
    with pytest.raises(PublishSafetyError) as excinfo:
        publish(store, counts(24, 20, 2), PublishPolicy(accept_regressions=wrong, reason="why"))
    assert "symbols:40->24" in str(excinfo.value)  # names the observed numbers
    wrong_baseline = (("symbols", 41, 24), *_SHRINK[1:])
    with pytest.raises(PublishSafetyError):
        publish(store, counts(24, 20, 2), PublishPolicy(accept_regressions=wrong_baseline, reason="why"))
    partial = _SHRINK[:3]  # total is still refused
    with pytest.raises(PublishSafetyError) as partial_error:
        publish(store, counts(24, 20, 2), PublishPolicy(accept_regressions=partial, reason="why"))
    assert "total fell from 100 to 46" in str(partial_error.value)
    assert store.read_active(SCOPE) == active


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_accept_regression_without_reason_is_refused(
    store: GenerationManifestStore, reason: str | None
) -> None:
    active = publish(store, counts(*_BASE), PublishPolicy())
    with pytest.raises(PublishSafetyError):
        publish(store, counts(24, 20, 2), PublishPolicy(accept_regressions=_SHRINK, reason=reason))
    assert store.read_active(SCOPE) == active


OVERRIDE_CASES: list[
    tuple[str, tuple[int, int, int], Callable[[], list[Chunk]], tuple[tuple[str, int, int], ...]]
] = [
    ("floor", _BASE, lambda: counts(2, 50, 10), (("symbols", 40, 2), ("total", 100, 62))),
    ("docs_floor", _BASE, lambda: counts(40, 0, 10), (("docs", 50, 0), ("total", 100, 50))),
    ("duplicates", _BASE, lambda: [*counts(40, 50, 10), mk("symbol", 0)], (("total", 100, 101),)),
    (
        "empty_text",
        _BASE,
        lambda: [*counts(40, 50, 10), _chunk("blank", "")],
        (("total", 100, 101),),
    ),
    (
        "repository_change",
        _BASE,
        lambda: counts(40, 50, 10, repo="org/other-repo"),
        (("total", 100, 100),),
    ),
    ("empty_generation", _BASE, lambda: [], (("total", 100, 0), ("symbols", 40, 0))),
]


@pytest.mark.parametrize(
    ("name", "prior", "build", "overrides"), OVERRIDE_CASES, ids=[case[0] for case in OVERRIDE_CASES]
)
def test_override_never_waives(
    store: GenerationManifestStore,
    name: str,
    prior: tuple[int, int, int],
    build: Callable[[], list[Chunk]],
    overrides: tuple[tuple[str, int, int], ...],
) -> None:
    active = publish(store, counts(*prior), PublishPolicy())
    files_before = _generation_files(store)
    with pytest.raises(PublishSafetyError):
        publish(store, build(), PublishPolicy(accept_regressions=overrides, reason="deliberate"))
    assert store.read_active(SCOPE) == active
    assert _generation_files(store) == files_before
    assert store.read_publish_log(SCOPE)[-1]["event"] == "refused"


def test_an_accepted_shrink_resets_history_so_the_next_publish_at_the_new_size_passes(
    store: GenerationManifestStore,
) -> None:
    publish(store, counts(*_BASE), PublishPolicy())
    publish(store, counts(24, 20, 2), PublishPolicy(accept_regressions=_SHRINK, reason="deliberate"))
    # Without the reset the baseline would still be 40 symbols / 100 documents and refuse this.
    publish(store, counts(24, 20, 2), PublishPolicy())
    last = _published(store)[-1]
    assert last["baseline"] == {"symbols": 24, "docs": 20, "examples": 2, "total": 46}
    assert last["overrides"] == []
    # and the new size is now protected like any other
    with pytest.raises(PublishSafetyError):
        publish(store, counts(10, 20, 2), PublishPolicy())


def test_refusal_message_carries_the_exact_accept_flags(store: GenerationManifestStore) -> None:
    publish(store, counts(*_BASE), PublishPolicy())
    with pytest.raises(PublishSafetyError) as one:
        publish(store, counts(31, 50, 10), PublishPolicy())
    assert str(one.value).endswith("to accept deliberately: --accept-regression symbols:40->31 --reason TEXT")
    with pytest.raises(PublishSafetyError) as many:
        publish(store, counts(24, 20, 2), PublishPolicy())
    assert str(many.value).endswith(
        "to accept deliberately: --accept-regression symbols:40->24 --accept-regression docs:50->20 "
        "--accept-regression examples:10->2 --accept-regression total:100->46 --reason TEXT"
    )
    for hard in (counts(2, 50, 10), counts(40, 0, 10), [*counts(40, 50, 10), mk("symbol", 0)], []):
        with pytest.raises(PublishSafetyError) as hard_error:
            publish(store, hard, PublishPolicy())
        assert "--accept-regression" not in str(hard_error.value)
    with pytest.raises(PublishSafetyError) as identity:
        publish(store, counts(40, 50, 10, repo="org/other-repo"), PublishPolicy())
    assert "--accept-regression" not in str(identity.value)
    assert "needs a new store" in str(identity.value)


# --------------------------------------------------------------------------------------
# The publish log
# --------------------------------------------------------------------------------------


def test_the_publish_log_round_trips_and_skips_a_malformed_line(store: GenerationManifestStore) -> None:
    assert store.read_publish_log(SCOPE) == []
    assert not _scope_dir(store).exists()  # a read creates nothing
    first = {"event": "published", "b": 2, "a": 1}
    store.append_publish_log(SCOPE, first)
    path = _scope_dir(store) / PUBLISH_LOG_NAME
    assert path.read_text(encoding="utf-8") == json.dumps(first, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("{this is not json\n[1, 2]\n\n")
    second = {"event": "refused", "reason": "ünïcode"}
    store.append_publish_log(SCOPE, second)
    assert store.read_publish_log(SCOPE) == [first, second]
    assert b"\r" not in path.read_bytes()
    with pytest.raises(InvalidScopeError):
        store.append_publish_log("../escape::a::b", first)
    with pytest.raises(InvalidScopeError):
        store.read_publish_log("only::two")


def test_the_32_baked_generations_satisfy_the_production_floors() -> None:
    files = _baked_generation_files()
    scopes = {path.parent.name for path in files}
    # re-pinned by TC-551: 40 -> 32
    assert len(scopes) >= 32
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        stats = payload_stats(doc["payload"])
        assert stats["symbols"] >= PRODUCTION_MIN_SYMBOLS, (path.parent.name, stats)
        assert stats["docs"] >= PRODUCTION_MIN_DOCS, (path.parent.name, stats)
