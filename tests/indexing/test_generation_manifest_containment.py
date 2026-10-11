"""TC-380: the generation manifest store never leaves its root, and a read creates nothing.

An independent QA run found that ``report_index_freshness(source_kind=...)`` reached
``GenerationManifestStore._scope_dir`` unchecked: the raw scope string was joined onto the
store root and ``mkdir(parents=True)`` ran even on a READ. These tests pin the repaired
contract offline, on ``tmp_path`` only: every probe is rejected with ``InvalidScopeError`` and
creates nothing, a read of a valid-but-never-written scope returns the same "never written"
answers as before without creating a directory, and valid scopes behave exactly as they did.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import (
    GenerationManifestError,
    GenerationManifestStore,
    InvalidScopeError,
    UnknownGenerationError,
    build_manifest,
    publish,
)

BACKSLASH = chr(92)
NUL = chr(0)

# Each probe is a scope string a client could have steered into the store, or a close cousin.
PROBES = {
    "sibling_scope_via_dotdot": "pdf::net::x/../slides__python__self_extracted",
    "manifests_dir_via_double_dotdot": "pdf::net::self_extracted/../../manifests/cells__rust__self_extracted",
    "tmp_dir_via_four_dotdots": "pdf::net::../../../../tmp/qa_probe_dir",
    "backslash_variant": "pdf::net::x" + BACKSLASH + ".." + BACKSLASH + "slides__python__self_extracted",
    "nul_byte": "pdf::net::self_extracted" + NUL + "x",
    "component_of_300_chars": "pdf::net::" + "a" * 300,
    "two_components": "pdf::net",
    "four_components": "pdf::net::self_extracted::extra",
    "empty_string": "",
    "empty_component": "pdf::::self_extracted",
    "leading_dot_component": "pdf::net::.hidden",
    "bare_dotdot_component": "pdf::net::..",
    "control_character": "pdf::net::self_extracted" + chr(10),
    "forward_slash": "pdf::net::a/b",
    "drive_colon": "pdf::net::C:",
    "space_in_component": "pdf::net::self extracted",
}


def _snapshot(base: Path) -> set[str]:
    """Every path (directories and files) below *base*, as sorted-comparable strings."""
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(base):
        for name in (*dirnames, *filenames):
            found.add(str(Path(dirpath) / name))
    return found


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A sandbox deep enough that even a four-level ``..`` probe lands inside *tmp_path*, so a
    single recursive snapshot of the sandbox sees anything a probe would have created."""
    return tmp_path / "s1" / "s2" / "s3" / "s4" / "s5"


@pytest.fixture
def store(sandbox: Path) -> GenerationManifestStore:
    return GenerationManifestStore(sandbox / "manifests")


def _watched_base(sandbox: Path) -> Path:
    # tmp_path itself: the sandbox's ancestors up to it are all inside the snapshot.
    return sandbox.parents[4]


def _read_calls(store: GenerationManifestStore, scope: str):
    """Every store operation that turns a scope string into a path without writing."""
    return [
        lambda: store.read_active(scope),
        lambda: store.current_fencing_token(scope),
        lambda: store.generation_exists(scope, "pdf::net::self_extracted::v1"),
        lambda: store.read_generation(scope, "pdf::net::self_extracted::v1"),
    ]


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_every_probe_is_rejected_by_every_read_and_creates_nothing(
    store: GenerationManifestStore, sandbox: Path, probe: str
) -> None:
    scope = PROBES[probe]
    base = _watched_base(sandbox)
    before = _snapshot(base)
    for call in _read_calls(store, scope):
        with pytest.raises(InvalidScopeError):
            call()
    assert _snapshot(base) == before


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_every_probe_is_rejected_by_the_write_path_and_creates_nothing(
    store: GenerationManifestStore, sandbox: Path, probe: str
) -> None:
    scope = PROBES[probe]
    base = _watched_base(sandbox)
    before = _snapshot(base)
    with pytest.raises(InvalidScopeError):
        store.acquire_lease(scope, "worker", "pending")
    assert _snapshot(base) == before


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_the_rejection_message_does_not_repeat_the_offending_value(
    store: GenerationManifestStore, probe: str
) -> None:
    scope = PROBES[probe]
    with pytest.raises(InvalidScopeError) as caught:
        store.read_active(scope)
    message = str(caught.value)
    assert len(message) < 100
    if len(scope) >= 4:
        assert scope not in message
    assert "qa_probe_dir" not in message and "slides__python" not in message


def test_invalid_scope_error_is_both_a_manifest_error_and_a_value_error() -> None:
    assert issubclass(InvalidScopeError, GenerationManifestError)
    assert issubclass(InvalidScopeError, ValueError)


@pytest.mark.parametrize(
    "scope",
    ["pdf::net::self_extracted", "3d::java::self_extracted", "slides::python::self_extracted", "a::b::c"],
)
def test_valid_scopes_map_to_the_same_directory_name_as_before(
    store: GenerationManifestStore, scope: str
) -> None:
    expected = store.root / scope.replace("::", "__")
    assert store._contained_scope_dir(scope) == expected
    assert not expected.exists()  # mapping a scope never creates its directory


def test_a_component_of_exactly_64_characters_is_accepted_and_65_is_not(
    store: GenerationManifestStore,
) -> None:
    assert store.read_active("pdf::net::" + "a" * 64) is None
    with pytest.raises(InvalidScopeError):
        store.read_active("pdf::net::" + "a" * 65)


def test_reading_a_valid_but_never_written_scope_returns_never_written_and_creates_nothing(
    store: GenerationManifestStore, sandbox: Path
) -> None:
    scope = "pdf::net::self_extracted"
    base = _watched_base(sandbox)
    before = _snapshot(base)
    root_listing = sorted(os.listdir(store.root))

    assert store.read_active(scope) is None
    assert store.current_fencing_token(scope) == 0
    assert store.generation_exists(scope, "pdf::net::self_extracted::v1") is False
    with pytest.raises(UnknownGenerationError):
        store.read_generation(scope, "pdf::net::self_extracted::v1")

    assert sorted(os.listdir(store.root)) == root_listing == []
    assert _snapshot(base) == before


def test_reading_a_never_written_scope_works_when_the_root_is_read_only(
    store: GenerationManifestStore, sandbox: Path
) -> None:
    """On POSIX the root is made genuinely read-only; on Windows ``chmod`` cannot forbid
    directory creation, so the no-new-directory snapshot is the evidence there. Either way the
    read must answer "never written" rather than failing, and create nothing."""
    base = _watched_base(sandbox)
    before = _snapshot(base)
    posix = os.name == "posix"
    if posix:
        os.chmod(store.root, 0o555)
    try:
        assert store.read_active("pdf::net::self_extracted") is None
        assert store.current_fencing_token("cells::python::self_extracted") == 0
        assert (
            store.generation_exists("cells::python::self_extracted", "cells::python::self_extracted::v9")
            is False
        )
    finally:
        if posix:
            os.chmod(store.root, 0o755)
    assert _snapshot(base) == before


def _payload() -> dict:
    return {"lexical_index": {"documents": {"d1": {"text": "hello"}}}}


def test_a_valid_publish_then_read_round_trips_exactly_as_before(
    store: GenerationManifestStore,
) -> None:
    manifest = build_manifest("pdf", "net", "self_extracted", "v1", _payload())
    lease = store.acquire_lease(manifest.scope, "worker-1", manifest.generation_id)
    active = publish(store, manifest.scope, None, manifest, lease)

    assert active == "pdf::net::self_extracted::v1"
    assert store.read_active(manifest.scope) == manifest.generation_id
    assert store.read_generation(manifest.scope, manifest.generation_id) == manifest
    assert store.generation_exists(manifest.scope, manifest.generation_id) is True
    assert store.current_fencing_token(manifest.scope) == 1
    scope_dir = store.root / "pdf__net__self_extracted"
    assert sorted(p.name for p in scope_dir.iterdir()) == [
        "active_pointer.json",
        "generation__v1.json",
        "lease_state.json",
    ]


def test_publishing_into_one_scope_never_creates_a_sibling_scope_directory(
    store: GenerationManifestStore,
) -> None:
    manifest = build_manifest("pdf", "net", "self_extracted", "v1", _payload())
    lease = store.acquire_lease(manifest.scope, "worker-1", manifest.generation_id)
    publish(store, manifest.scope, None, manifest, lease)
    assert sorted(os.listdir(store.root)) == ["pdf__net__self_extracted"]


@pytest.mark.parametrize(
    "version",
    ["a/b", "../v", "..", "v..1", "a" + BACKSLASH + "b", "v" + NUL, "v" + chr(10), "x" * 129],
)
def test_a_version_that_could_leave_the_scope_directory_raises_before_any_file_is_written(
    store: GenerationManifestStore, sandbox: Path, version: str
) -> None:
    manifest = build_manifest("pdf", "net", "self_extracted", version, _payload())
    base = _watched_base(sandbox)
    before = _snapshot(base)
    with pytest.raises(InvalidScopeError):
        store.write_generation(manifest)
    assert _snapshot(base) == before


@pytest.mark.parametrize("version", ["a/b", "../v", "x" * 129, "v" + NUL])
def test_a_generation_id_with_an_unsafe_version_is_rejected_by_reads(
    store: GenerationManifestStore, version: str
) -> None:
    scope = "pdf::net::self_extracted"
    generation_id = "pdf::net::self_extracted::" + version
    with pytest.raises(InvalidScopeError):
        store.generation_exists(scope, generation_id)
    with pytest.raises(InvalidScopeError):
        store.read_generation(scope, generation_id)


def test_a_version_of_exactly_128_characters_is_accepted(store: GenerationManifestStore) -> None:
    manifest = build_manifest("pdf", "net", "self_extracted", "v" * 128, _payload())
    path = store.write_generation(manifest)
    assert path.name == "generation__" + "v" * 128 + ".json"
    assert path.is_file()


def test_an_identity_component_that_is_a_traversal_is_rejected_on_write(
    store: GenerationManifestStore, sandbox: Path
) -> None:
    base = _watched_base(sandbox)
    before = _snapshot(base)
    manifest = build_manifest("..", "net", "self_extracted", "v1", _payload())
    with pytest.raises(InvalidScopeError):
        store.write_generation(manifest)
    assert _snapshot(base) == before


def test_a_symlink_inside_the_root_that_points_outside_it_is_refused(
    store: GenerationManifestStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside_target"
    outside.mkdir()
    link = store.root / "pdf__net__self_extracted"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        # This platform/account cannot create a symlink: the same resolved-path refusal is
        # proven through a fake root in the next test, which always runs.
        link = None
    if link is not None:
        with pytest.raises(InvalidScopeError):
            store.read_active("pdf::net::self_extracted")
        with pytest.raises(InvalidScopeError):
            store.acquire_lease("pdf::net::self_extracted", "worker", "pending")
        assert list(outside.iterdir()) == []
    else:
        assert (
            store.read_active("pdf::net::self_extracted") is None
        )  # no symlink was created, so nothing escapes


class _FakeCandidate:
    """A candidate directory whose resolved location is somewhere else entirely."""

    def __init__(self, resolved: Path) -> None:
        self._resolved = resolved

    def resolve(self) -> Path:
        return self._resolved


class _FakeRoot:
    """A root whose ``/`` yields candidates that resolve outside the real (resolved) root."""

    def __init__(self, real_root: Path, escape_to: Path) -> None:
        self._real_root = real_root
        self._escape_to = escape_to

    def resolve(self) -> Path:
        return self._real_root.resolve()

    def __truediv__(self, name: str) -> _FakeCandidate:
        return _FakeCandidate(self._escape_to.resolve() / name)


def test_the_resolved_path_check_refuses_a_directory_that_resolves_outside_the_root(
    store: GenerationManifestStore, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    store.root = _FakeRoot(store.root, outside)  # type: ignore[assignment]
    with pytest.raises(InvalidScopeError):
        store._contained_scope_dir("pdf::net::self_extracted")
    with pytest.raises(InvalidScopeError):
        store.read_active("pdf::net::self_extracted")
    assert list(outside.iterdir()) == []


def test_the_resolved_path_check_refuses_a_directory_that_resolves_to_the_root_itself(
    store: GenerationManifestStore,
) -> None:
    store.root = _FakeRoot(store.root, store.root)  # type: ignore[assignment]

    class _CollapsesToRoot(_FakeRoot):
        def __truediv__(self, name: str) -> _FakeCandidate:
            return _FakeCandidate(self.resolve())

    store.root = _CollapsesToRoot(store.root._real_root, store.root._real_root)  # type: ignore[assignment,attr-defined]
    with pytest.raises(InvalidScopeError):
        store._contained_scope_dir("pdf::net::self_extracted")


def test_contained_scope_dir_is_the_only_function_that_builds_a_scope_path() -> None:
    """Every path-building helper delegates to the one containment function."""
    source = (
        Path(__file__).parents[2] / "src" / "foss_mcp" / "indexing" / "generation_manifest.py"
    ).read_text(encoding="utf-8")
    assert "def _contained_scope_dir(" in source
    assert source.count("self.root /") == 1
    assert ".mkdir(" in source and source.count(".mkdir(") == 2  # __init__ root + _atomic_write_json
