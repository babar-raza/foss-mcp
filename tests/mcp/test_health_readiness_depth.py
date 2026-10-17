"""G3/TC-506 (REQ-G3-008): readiness proves scope, shape and composition, not "some document exists".

Offline and deterministic. Every test works on a COPY of the committed ``pdf/go`` baked scope
(``infra/demo-manifests/pdf__go__self_extracted``: the pointer and the one generation file) under
``tmp_path`` and edits the copied JSON the way the independent reviewer's doctored-state scenarios
did (``S02`` to ``S08``, ``S20a`` to ``S20d``). Nothing here touches the network or a container.
"""

from __future__ import annotations

import collections
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.health import (
    GENERATION_UNAVAILABLE_MESSAGE,
    READINESS_DOCUMENTATION_FLOOR,
    READINESS_SYMBOL_FLOOR,
    DeploymentGenerationStore,
    ReadinessReport,
    generation_unavailable_reason,
    readiness_report,
    round_trip_check,
)
from foss_mcp.mcp.tools.relevance import chunk_kind

REPO_ROOT = Path(__file__).parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SCOPE_DIR_NAME = "pdf__go__self_extracted"
SCOPE_KEY = "pdf::go::self_extracted"
CANARY = "canary-7f3a9c-do-not-quote"


class Env:
    """One copied baked scope under ``tmp_path`` plus helpers that edit its JSON."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "manifests"
        self.scope_dir = self.root / SCOPE_DIR_NAME
        shutil.copytree(DEMO_MANIFESTS / SCOPE_DIR_NAME, self.scope_dir)
        self.tmp_path = tmp_path

    @property
    def pointer_path(self) -> Path:
        return self.scope_dir / "active_pointer.json"

    @property
    def generation_path(self) -> Path:
        return next(self.scope_dir.glob("generation__*.json"))

    def store(self) -> DeploymentGenerationStore:
        """A FRESH store each time, so no parsed generation is carried over from an earlier edit."""
        return DeploymentGenerationStore(GenerationManifestStore(self.root), SCOPE_KEY)

    def load_generation(self) -> dict[str, Any]:
        return json.loads(self.generation_path.read_text(encoding="utf-8"))

    def save_generation(self, generation: dict[str, Any]) -> None:
        self.generation_path.write_text(json.dumps(generation), encoding="utf-8")

    def lexical_index(self, generation: dict[str, Any]) -> dict[str, Any]:
        return generation["payload"]["lexical_index"]

    def edit_documents(self, edit: Any) -> None:
        generation = self.load_generation()
        for document in self.lexical_index(generation)["documents"].values():
            edit(document)
        self.save_generation(generation)

    def keep_kinds(self, keep_symbols: int | None = None, drop: tuple[str, ...] = ()) -> None:
        """Keep every chunk except the kinds in *drop*; with *keep_symbols* given, keep only that
        many symbol chunks."""
        generation = self.load_generation()
        index = self.lexical_index(generation)
        kept: dict[str, Any] = {}
        symbols = 0
        for doc_id, document in index["documents"].items():
            kind = chunk_kind(document["text"])
            if kind in drop:
                continue
            if kind == "symbol" and keep_symbols is not None:
                if symbols >= keep_symbols:
                    continue
                symbols += 1
            kept[doc_id] = document
        index["documents"] = kept
        self.rebuild_doc_freq(generation)
        self.save_generation(generation)

    def rebuild_doc_freq(self, generation: dict[str, Any]) -> None:
        index = self.lexical_index(generation)
        counter: collections.Counter[str] = collections.Counter()
        for document in index["documents"].values():
            counter.update(set(document["tokens"]))
        index["doc_freq"] = dict(counter)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _foreign_scope_dir(name: str) -> Path:
    return DEMO_MANIFESTS / name


def _replace_scope_dir_contents(env: Env, source: Path) -> None:
    for child in env.scope_dir.iterdir():
        child.unlink()
    for child in source.iterdir():
        shutil.copy(child, env.scope_dir / child.name)


def _raw_composition(env: Env) -> dict[str, int]:
    documents = env.lexical_index(env.load_generation())["documents"]
    counts = collections.Counter(chunk_kind(document["text"]) for document in documents.values())
    return {
        "symbol": counts["symbol"],
        "doc": counts["documentation"],
        "example": counts["example"],
    }


# --- a healthy baked generation ----------------------------------------------------------------------


def test_a_baked_generation_is_ready(env: Env) -> None:
    report = readiness_report(env.store())
    assert report.ready is True
    assert report.reasons == ()
    assert report.generation_id is not None
    assert report.generation_id.startswith(SCOPE_KEY + "::")


def test_composition_counts_match_chunk_kind_computed_in_the_test(env: Env) -> None:
    report = readiness_report(env.store())
    assert report.composition == _raw_composition(env)
    assert report.composition["symbol"] >= READINESS_SYMBOL_FLOOR
    assert report.composition["doc"] >= READINESS_DOCUMENTATION_FLOOR


def test_to_dict_has_exactly_the_documented_keys(env: Env) -> None:
    report = readiness_report(env.store())
    body = report.to_dict()
    assert set(body) == {"ready", "reasons", "composition", "generation_id", "warnings"}
    assert isinstance(body["reasons"], list)
    assert isinstance(body["warnings"], list)
    assert set(body["composition"]) == {"symbol", "doc", "example"}
    json.dumps(body)  # the body is what /readyz serialises: it must be plain JSON
    assert isinstance(report, ReadinessReport)


def test_round_trip_check_returns_the_real_bool_of_the_report(env: Env) -> None:
    assert round_trip_check(env.store()) is True
    env.keep_kinds(drop=("documentation",))
    assert readiness_report(env.store()).ready is False
    assert round_trip_check(env.store()) is False


# --- a generation of another product behind this deployment's pointer (S20) --------------------------


def test_s20a_foreign_pointer_and_generation_in_this_scope_directory(env: Env) -> None:
    # re-pinned by TC-551: <removed pilot> -> cells__go__self_extracted
    _replace_scope_dir_contents(env, _foreign_scope_dir("cells__go__self_extracted"))
    report = readiness_report(env.store())
    assert report.ready is False
    assert "active pointer names a generation of another scope" in report.reasons


def test_s20b_foreign_generation_file_under_this_pointer(env: Env) -> None:
    foreign = json.loads(
        # re-pinned by TC-551: <removed pilot> -> cells__go__self_extracted
        next(_foreign_scope_dir("cells__go__self_extracted").glob("generation__*.json")).read_text(
            encoding="utf-8"
        )
    )
    version = json.loads(env.pointer_path.read_text(encoding="utf-8"))["generation_id"].rsplit("::", 1)[-1]
    env.generation_path.unlink()
    (env.scope_dir / f"generation__{version}.json").write_text(json.dumps(foreign), encoding="utf-8")
    report = readiness_report(env.store())
    assert report.ready is False
    assert report.reasons  # the generation file does not carry the id the pointer names


def test_s20c_foreign_content_relabelled_with_this_generation_id(env: Env) -> None:
    foreign = json.loads(
        next(_foreign_scope_dir("pdf__java__self_extracted").glob("generation__*.json")).read_text(
            encoding="utf-8"
        )
    )
    own = env.load_generation()
    for field in ("generation_id", "family", "platform", "version"):
        foreign[field] = own[field]
    env.save_generation(foreign)
    report = readiness_report(env.store())
    assert report.ready is False
    assert "lexical index belongs to a different generation" in report.reasons
    assert any("document ids are not under this generation" in reason for reason in report.reasons)


def test_s20d_foreign_generation_and_pointer(env: Env) -> None:
    source = _foreign_scope_dir("pdf__java__self_extracted")
    generation_file = next(source.glob("generation__*.json"))
    foreign = json.loads(generation_file.read_text(encoding="utf-8"))
    env.generation_path.unlink()
    (env.scope_dir / generation_file.name).write_text(json.dumps(foreign), encoding="utf-8")
    env.pointer_path.write_text(json.dumps({"generation_id": foreign["generation_id"]}), encoding="utf-8")
    report = readiness_report(env.store())
    assert report.ready is False
    assert "active pointer names a generation of another scope" in report.reasons
    assert "generation identity does not match this deployment" in report.reasons


# --- documents that cannot be searched ---------------------------------------------------------------


def test_documents_without_tokens_are_not_ready(env: Env) -> None:
    env.edit_documents(lambda document: document.pop("tokens", None))
    report = readiness_report(env.store())
    assert report.ready is False
    assert any("documents are malformed (chunk_id, text, tokens)" in reason for reason in report.reasons)
    assert report.composition == {"symbol": 0, "doc": 0, "example": 0}


def test_documents_without_text_are_not_ready(env: Env) -> None:
    env.edit_documents(lambda document: document.pop("text", None))
    report = readiness_report(env.store())
    assert report.ready is False
    assert any("documents are malformed (chunk_id, text, tokens)" in reason for reason in report.reasons)


def test_documents_without_chunk_id_are_not_ready(env: Env) -> None:
    env.edit_documents(lambda document: document.pop("chunk_id", None))
    report = readiness_report(env.store())
    assert report.ready is False
    assert any("documents are malformed (chunk_id, text, tokens)" in reason for reason in report.reasons)


def test_a_generation_without_doc_freq_is_not_ready(env: Env) -> None:
    generation = env.load_generation()
    env.lexical_index(generation).pop("doc_freq")
    env.save_generation(generation)
    report = readiness_report(env.store())
    assert report.ready is False
    assert "lexical index has no doc_freq" in report.reasons


# --- composition floors ------------------------------------------------------------------------------


@pytest.mark.parametrize("symbols", [0, 1, 2, READINESS_SYMBOL_FLOOR])
def test_symbol_count_below_the_floor_is_not_ready(env: Env, symbols: int) -> None:
    env.keep_kinds(keep_symbols=symbols)
    report = readiness_report(env.store())
    assert report.composition["symbol"] == symbols
    if symbols >= READINESS_SYMBOL_FLOOR:
        assert report.ready is True
        assert report.reasons == ()
    else:
        assert report.ready is False
        assert f"symbols {symbols} below the floor {READINESS_SYMBOL_FLOOR}" in report.reasons


def test_zero_documentation_is_not_ready(env: Env) -> None:
    env.keep_kinds(drop=("documentation",))
    report = readiness_report(env.store())
    assert report.ready is False
    assert f"documentation 0 below the floor {READINESS_DOCUMENTATION_FLOOR}" in report.reasons


def test_no_examples_is_ready_with_a_warning(env: Env) -> None:
    env.keep_kinds(drop=("example",))
    report = readiness_report(env.store())
    assert report.ready is True
    assert report.reasons == ()
    assert report.composition["example"] == 0
    assert report.warnings == ("no verified examples",)


def test_mixed_source_commits_is_ready_with_a_warning(env: Env) -> None:
    generation = env.load_generation()
    documents = env.lexical_index(generation)["documents"]
    changed = False
    for document in documents.values():
        if chunk_kind(document["text"]) == "symbol":
            lines = document["text"].split("\n")
            lines = [
                "Source-Commit: 0000000000000000000000000000000000000000"
                if line.startswith("Source-Commit:")
                else line
                for line in lines
            ]
            document["text"] = "\n".join(lines)
            changed = True
            break
    assert changed
    env.save_generation(generation)
    report = readiness_report(env.store())
    assert report.ready is True
    assert report.reasons == ()
    assert "generation mixes 2 source commits" in report.warnings


# --- the pointer -------------------------------------------------------------------------------------


def test_a_bom_prefixed_pointer_is_ready(env: Env) -> None:
    env.pointer_path.write_bytes(b"\xef\xbb\xbf" + env.pointer_path.read_bytes())
    assert readiness_report(env.store()).ready is True


def test_the_store_reads_a_bom_prefixed_pointer(env: Env) -> None:
    expected = json.loads(env.pointer_path.read_text(encoding="utf-8"))["generation_id"]
    env.pointer_path.write_bytes(b"\xef\xbb\xbf" + env.pointer_path.read_bytes())
    assert GenerationManifestStore(env.root).read_active(SCOPE_KEY) == expected


@pytest.mark.parametrize(
    ("content", "reason_part"),
    [
        (b"not json", "active pointer is unreadable (JSONDecodeError)"),
        (b"", "active pointer is unreadable (JSONDecodeError)"),
        (b"null", "no active generation"),
        (b"[]", "no active generation"),
        (b'["pdf::go::self_extracted::v1"]', "active pointer is unreadable (TypeError)"),
        (b'{"generation_id": 5}', "active pointer is malformed"),
        (b'{"generation_id": ""}', "active pointer is malformed"),
        (
            b'{"generation_id": "pdf::go::self_extracted::../../etc/passwd"}',
            "generation file is unreadable (InvalidScopeError)",
        ),
    ],
)
def test_pointer_variants_never_raise_and_are_not_ready(env: Env, content: bytes, reason_part: str) -> None:
    env.pointer_path.write_bytes(content)
    report = readiness_report(env.store())
    assert report.ready is False
    assert any(reason_part in reason for reason in report.reasons)
    assert round_trip_check(env.store()) is False


@pytest.mark.parametrize("damage", ["garbage", "truncated", "removed"])
def test_generation_file_problems_are_not_ready(env: Env, damage: str) -> None:
    if damage == "garbage":
        env.generation_path.write_bytes(b"\x00\xff\xfe not json at all {{{")
    elif damage == "truncated":
        data = env.generation_path.read_bytes()
        env.generation_path.write_bytes(data[: int(len(data) * 0.4)])
    else:
        env.generation_path.unlink()
    report = readiness_report(env.store())
    assert report.ready is False
    assert len(report.reasons) == 1
    assert report.reasons[0].startswith("generation file is unreadable (")
    assert report.generation_id is not None


def test_reasons_carry_no_path_and_no_exception_message(env: Env) -> None:
    # A pointer that holds the tmp path text, and a pointer file whose content is not JSON.
    env.pointer_path.write_text(json.dumps({"generation_id": str(env.tmp_path)}), encoding="utf-8")
    first = readiness_report(env.store())
    env.pointer_path.write_text(f"{CANARY} not json {env.tmp_path}", encoding="utf-8")
    second = readiness_report(env.store())
    env.pointer_path.write_text(
        json.dumps({"generation_id": f"{SCOPE_KEY}::{env.tmp_path}"}), encoding="utf-8"
    )
    third = readiness_report(env.store())
    for report in (first, second, third):
        assert report.ready is False
        assert report.generation_id is None  # a doctored pointer value is never echoed back
        for reason in (*report.reasons, *report.warnings):
            assert str(env.tmp_path) not in reason
            assert env.tmp_path.name not in reason
            assert CANARY not in reason
            assert "\\" not in reason
        assert str(env.tmp_path) not in json.dumps(report.to_dict())


def test_an_unexpected_exception_is_one_class_name_reason() -> None:
    class BrokenManifestStore:
        def read_active(self, scope: str) -> str:
            return SCOPE_KEY + "::v1"

        def read_generation(self, scope: str, generation_id: str) -> Any:
            return SimpleNamespace(key=None, payload={})

    store = DeploymentGenerationStore(BrokenManifestStore(), SCOPE_KEY)  # type: ignore[arg-type]
    report = readiness_report(store)
    assert report.ready is False
    assert report.reasons == ("readiness check failed (AttributeError)",)


# --- generation_unavailable_reason: the cheap check the tool dispatcher runs -------------------------


def test_generation_unavailable_reason_is_none_for_a_usable_generation(env: Env) -> None:
    assert generation_unavailable_reason(env.store().manifest_store, SCOPE_KEY) is None


@pytest.mark.parametrize(
    "state", ["corrupt pointer", "corrupt generation", "missing generation", "zero documents"]
)
def test_generation_unavailable_reason_names_each_unusable_state(env: Env, state: str) -> None:
    if state == "corrupt pointer":
        env.pointer_path.write_bytes(b"{{{ not json")
        expected = "active pointer is unreadable (JSONDecodeError)"
    elif state == "corrupt generation":
        env.generation_path.write_bytes(b"\x00\xff garbage")
        expected = "generation file is unreadable ("
    elif state == "missing generation":
        env.generation_path.unlink()
        expected = "generation file is unreadable (UnknownGenerationError)"
    else:
        generation = env.load_generation()
        env.lexical_index(generation)["documents"] = {}
        env.lexical_index(generation)["doc_freq"] = {}
        env.save_generation(generation)
        expected = "generation has no lexical documents"
    reason = generation_unavailable_reason(env.store().manifest_store, SCOPE_KEY)
    assert reason is not None
    assert expected in reason
    assert str(env.tmp_path) not in reason


@pytest.mark.parametrize("state", ["empty store", "null pointer"])
def test_generation_unavailable_reason_is_none_for_a_deployment_that_never_published(
    env: Env, state: str
) -> None:
    """No pointer at all is a fresh, unpopulated deployment, not a broken one: its tools answer
    with honest misses (the container-session e2e tests pin this). /readyz still says 503."""
    if state == "empty store":
        shutil.rmtree(env.scope_dir)
    else:
        env.pointer_path.write_text("null", encoding="utf-8")
    assert generation_unavailable_reason(env.store().manifest_store, SCOPE_KEY) is None
    assert readiness_report(env.store()).reasons == ("no active generation",)


@pytest.mark.parametrize("symbols", [1, 2])
def test_generation_unavailable_reason_ignores_the_readiness_floors(env: Env, symbols: int) -> None:
    """A thin but readable generation (below the symbol floor, no documentation) is not
    unavailable: only /readyz applies the composition floors."""
    env.keep_kinds(keep_symbols=symbols, drop=("documentation", "example"))
    assert readiness_report(env.store()).ready is False
    assert generation_unavailable_reason(env.store().manifest_store, SCOPE_KEY) is None


def test_generation_unavailable_reason_works_with_a_duck_typed_store() -> None:
    """The store of tests/mcp/test_tool_surface_truthful.py implements only ``read_active`` and
    ``read_generation`` (returning an object with a ``payload`` dict): nothing else may be touched."""

    class Duck:
        def __init__(self, active: Any, payload: Any) -> None:
            self.active = active
            self.payload = payload

        def read_active(self, scope: str) -> Any:
            return self.active

        def read_generation(self, scope: str, generation_id: str) -> Any:
            return SimpleNamespace(payload=self.payload)

    usable = Duck("a::b::c::v1", {"lexical_index": {"documents": {"d": {}}}})
    assert generation_unavailable_reason(usable, "a::b::c") is None
    assert generation_unavailable_reason(Duck(None, {}), "a::b::c") is None
    assert generation_unavailable_reason(Duck("", {}), "a::b::c") == "active pointer is malformed"
    assert (
        generation_unavailable_reason(Duck("a::b::c::v1", {"lexical_index": {"documents": {}}}), "a::b::c")
        == "generation has no lexical documents"
    )
    assert (
        generation_unavailable_reason(Duck("a::b::c::v1", {}), "a::b::c")
        == "generation has no lexical documents"
    )


def test_the_unavailable_message_is_fixed_and_path_free() -> None:
    assert GENERATION_UNAVAILABLE_MESSAGE.startswith("This deployment's documentation index is temporarily")
    assert "not a statement that the API or documentation does not exist" in GENERATION_UNAVAILABLE_MESSAGE
    assert "\\" not in GENERATION_UNAVAILABLE_MESSAGE
    assert "/" not in GENERATION_UNAVAILABLE_MESSAGE
