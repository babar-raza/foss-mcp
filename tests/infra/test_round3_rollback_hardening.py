"""G3/TC-564 (REQ-G3-009, RD3-121, RD3-122): the rollback tool is shipped, and it validates what it flips.

Round 3 found that ``infra/rollback.py`` was in no shipped image (the documented recovery could not run),
that it checked shape only (a 1-chunk, 0-symbol, 60-percent-regressed or different-repository target was
accepted), that it never probed readiness after the flip, that ``--list`` crashed on one corrupt file, and that
a publisher fenced out after writing its generation left an orphan file behind.

The expectations here come from independent oracles: the production floors and limits are read from the
publish policy's documented numbers (3 symbols, 1 documentation chunk, 20 and 50 percent), the pointer is read
back as raw JSON from disk, and the standalone-run test executes the real script in a fresh interpreter whose
import path holds ``src`` only (what the image has) and compares every third-party module it loaded with the
packages ``requirements-serving.lock`` installs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import infra.rollback as rollback
from foss_mcp.indexing.generation_manifest import (
    ActivePointerConflictError,
    GenerationManifestStore,
    StaleFencingTokenError,
    UnknownGenerationError,
    build_manifest,
    publish,
)
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.publisher import build_generation_stats, content_chunk_id
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILY, PLATFORM, SOURCE_KIND = "pdf", "go", "self_extracted"
SCOPE = "::".join((FAMILY, PLATFORM, SOURCE_KIND))
SCOPE_DIR = "__".join((FAMILY, PLATFORM, SOURCE_KIND))
GOOD_COMMIT = "c" * 40
OTHER_COMMIT = "b" * 40


def _chunks(symbols: int, docs: int, examples: int, repository: str, commit: str) -> list[Chunk]:
    provenance = Provenance(repository=repository, commit=commit, path="api_surface.json")
    footer = f"\nSource-Commit: {commit}"
    texts = [f"FQN: Sym{i}\nKind: type_spec\nA documented type number {i}.{footer}" for i in range(symbols)]
    texts += [f"FQN: Doc: Guide{i}\nA guide section number {i}.{footer}" for i in range(docs)]
    texts += [f"FQN: Example: Demo{i}\nAn example number {i}.{footer}" for i in range(examples)]
    return [
        Chunk(
            section_title=f"section-{i}",
            text=text,
            source_kind=SOURCE_KIND,
            content_type="api_surface",
            provenance=provenance,
            trust_tier="highest",
        )
        for i, text in enumerate(texts)
    ]


def make_generation(
    store: GenerationManifestStore,
    version: str,
    *,
    symbols: int = 10,
    docs: int = 4,
    examples: int = 0,
    repository: str = "org/repo-a",
    commit: str = GOOD_COMMIT,
    foreign_ids: bool = False,
) -> str:
    """Write (never activate) one generation with real chunk texts and the persisted stats a production
    publish writes. ``foreign_ids`` renames the lexical documents so they are not under the generation: a
    generation that passes every floor and the shape check but is not ready to serve."""
    chunks = _chunks(symbols, docs, examples, repository, commit)
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    generation_id = build_manifest(FAMILY, PLATFORM, SOURCE_KIND, version).generation_id
    lexical = build_lexical_index(chunks, chunk_ids, generation_id)
    if foreign_ids:
        lexical["documents"] = {f"foreign::{i}": doc for i, doc in enumerate(lexical["documents"].values())}
    payload = {"lexical_index": lexical, "stats": build_generation_stats(chunks, chunk_ids)}
    return store.write_and_validate(build_manifest(FAMILY, PLATFORM, SOURCE_KIND, version, payload))


def activate(store: GenerationManifestStore, generation_id: str) -> None:
    lease = store.acquire_lease(SCOPE, "test", generation_id)
    store.cas_activate(SCOPE, store.read_active(SCOPE), generation_id, lease)


def argv(store_path: Path, *mode: str) -> list[str]:
    return [
        "--family",
        FAMILY,
        "--platform",
        PLATFORM,
        "--source-kind",
        SOURCE_KIND,
        "--manifest-store",
        str(store_path),
        *mode,
    ]


def raw_pointer(store_path: Path) -> str:
    """The active pointer read straight from disk, not through the store under test."""
    return json.loads((store_path / SCOPE_DIR / "active_pointer.json").read_text(encoding="utf-8"))[
        "generation_id"
    ]


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "manifests"


@pytest.fixture
def store(store_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(store_path)


def _install_target_and_active(store: GenerationManifestStore, **target_options: Any) -> tuple[str, str]:
    """A rollback candidate (``target``, written first) and a healthy active generation (10 symbols, 6 docs)."""
    target = make_generation(store, "v1", **target_options)
    active = make_generation(store, "v2", symbols=10, docs=6)
    activate(store, target)
    activate(store, active)
    return target, active


# --- the checks a forward publish makes, applied to the rollback target ------------------------------


@pytest.mark.parametrize(
    ("options", "needle"),
    [
        ({"symbols": 1, "docs": 3}, "1 symbol chunk(s) is below the production floor of 3"),
        ({"symbols": 0, "docs": 6}, "0 symbol chunk(s) is below the production floor of 3"),
        ({"symbols": 5, "docs": 0}, "0 documentation chunk(s) is below the production floor of 1"),
        ({"symbols": 4, "docs": 2}, "symbols would fall from 10 to 4, more than 20% below"),
        ({"symbols": 10, "docs": 2}, "docs would fall from 6 to 2, more than 50% below"),
        ({"repository": "org/another-repo"}, "the target was indexed from ['org/another-repo']"),
    ],
    ids=[
        "one-symbol",
        "zero-symbols",
        "zero-docs",
        "symbols-regressed",
        "docs-regressed",
        "other-repository",
    ],
)
def test_an_unsafe_target_is_refused_and_the_pointer_does_not_move(
    store: GenerationManifestStore, store_path: Path, options: dict[str, Any], needle: str
) -> None:
    target, active = _install_target_and_active(store, **options)
    token_before = store.current_fencing_token(SCOPE)

    with pytest.raises(rollback.RollbackRefusedError) as raised:
        rollback.main(argv(store_path, "--target-generation", target))

    assert needle in str(raised.value)
    assert "--force" in str(raised.value)
    assert raw_pointer(store_path) == active
    assert store.current_fencing_token(SCOPE) == token_before, "a refused rollback must not take a lease"


@pytest.mark.parametrize(
    "options",
    [
        {"symbols": 1, "docs": 3},
        {"symbols": 0, "docs": 6},
        {"symbols": 4, "docs": 2},
        {"repository": "org/another-repo"},
    ],
    ids=["one-symbol", "zero-symbols", "symbols-regressed", "other-repository"],
)
def test_force_applies_an_unsafe_target(
    store: GenerationManifestStore, store_path: Path, options: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    target, _ = _install_target_and_active(store, **options)

    code = rollback.main(argv(store_path, "--target-generation", target, "--force"))

    assert code == 0
    assert raw_pointer(store_path) == target
    assert f"rolled back to {target}" in capsys.readouterr().out


def test_a_good_target_is_applied_without_force(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target, _ = _install_target_and_active(store, symbols=9, docs=4)

    assert rollback.main(argv(store_path, "--target-generation", target)) == 0

    assert raw_pointer(store_path) == target
    assert capsys.readouterr().out.startswith(f"rolled back to {target}")


# --- the readiness probe after the flip ---------------------------------------------------------------


def test_a_target_that_is_not_ready_is_flipped_back_and_exits_3(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target, active = _install_target_and_active(store, foreign_ids=True)

    code = rollback.main(argv(store_path, "--target-generation", target))

    assert code == rollback.EXIT_REVERTED == 3
    assert raw_pointer(store_path) == active
    err = capsys.readouterr().err
    assert "failed the readiness probe" in err
    assert "document ids are not under this generation" in err
    assert f"flipped back to {active}" in err
    events = [
        (e["event"], e["generation_id"]) for e in store.read_publish_log(SCOPE) if e["event"] == "rollback"
    ]
    assert events == [("rollback", target), ("rollback", active)]


def test_force_keeps_a_target_that_is_not_ready_and_says_so(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target, _ = _install_target_and_active(store, foreign_ids=True)

    code = rollback.main(argv(store_path, "--target-generation", target, "--force"))

    assert code == 0
    assert raw_pointer(store_path) == target
    captured = capsys.readouterr()
    assert "is not ready" in captured.err
    assert captured.out.strip() == f"rolled back to {target}", (
        "the warning is on stderr, stdout stays one line"
    )


def test_nothing_to_flip_back_to_exits_4(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target = make_generation(store, "v1", foreign_ids=True)  # written, never activated

    code = rollback.main(argv(store_path, "--target-generation", target))

    assert code == rollback.EXIT_REVERT_FAILED == 4
    assert "could NOT flip back" in capsys.readouterr().err


# --- the sidecar for this identity is re-checked --------------------------------------------------------


def _write_sidecar(store_path: Path, **fields: object) -> None:
    store_path.mkdir(parents=True, exist_ok=True)
    (store_path / f"product_reference_{FAMILY}_{PLATFORM}.json").write_text(
        json.dumps({"platform": PLATFORM, **fields}), encoding="utf-8"
    )


def test_a_sidecar_naming_another_repository_refuses_the_rollback(
    store: GenerationManifestStore, store_path: Path
) -> None:
    target, active = _install_target_and_active(store, symbols=9)
    _write_sidecar(store_path, source_repository="someone/else", source_ref=GOOD_COMMIT)

    with pytest.raises(rollback.RollbackRefusedError, match="product-reference sidecar names repository"):
        rollback.main(argv(store_path, "--target-generation", target))

    assert raw_pointer(store_path) == active


@pytest.mark.parametrize("spelling", ["org/repo-a", "ORG/Repo-A", "https://github.com/org/repo-a.git"])
def test_the_same_repository_in_any_spelling_passes_the_sidecar_check(
    store: GenerationManifestStore, store_path: Path, spelling: str
) -> None:
    target, _ = _install_target_and_active(store, symbols=9)
    _write_sidecar(store_path, source_repository=spelling, source_ref=GOOD_COMMIT)

    assert rollback.main(argv(store_path, "--target-generation", target)) == 0


def test_a_sidecar_that_records_a_newer_commit_is_reported_after_the_flip(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target, _ = _install_target_and_active(store, symbols=9)
    _write_sidecar(store_path, source_repository="org/repo-a", source_ref=OTHER_COMMIT)

    assert rollback.main(argv(store_path, "--target-generation", target)) == 0

    out = capsys.readouterr().out
    assert f"records commit {OTHER_COMMIT[:12]}" in out
    assert "install notice follows the active generation's own commit" in out


def test_a_sidecar_on_the_targets_own_commit_prints_no_note(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target, _ = _install_target_and_active(store, symbols=9)
    _write_sidecar(store_path, source_repository="org/repo-a", source_ref=GOOD_COMMIT)

    rollback.main(argv(store_path, "--target-generation", target))

    assert "note:" not in capsys.readouterr().out


def test_the_sidecar_file_name_matches_the_one_the_ingestion_writer_uses() -> None:
    sys.path.insert(0, str(REPO_ROOT / "infra"))
    try:
        from fetch_product_reference import sidecar_name
    finally:
        sys.path.remove(str(REPO_ROOT / "infra"))
    assert rollback._PRODUCT_REFERENCE_SIDECAR.format(family="pdf", platform="go") == sidecar_name(
        "pdf", "go"
    )


# --- the legacy contract is kept ------------------------------------------------------------------------


def test_an_unknown_target_still_raises_unknown_generation(
    store: GenerationManifestStore, store_path: Path
) -> None:
    _install_target_and_active(store)
    with pytest.raises(UnknownGenerationError):
        rollback.main(argv(store_path, "--target-generation", f"{SCOPE}::nope"))


def test_the_command_line_wrapper_prints_one_line_and_exits_1_on_a_refusal(
    store: GenerationManifestStore,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    target, _ = _install_target_and_active(store, symbols=1, docs=3)
    monkeypatch.setattr(sys, "argv", ["rollback.py", *argv(store_path, "--target-generation", target)])

    with pytest.raises(SystemExit) as raised:
        rollback.cli()

    assert raised.value.code == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("rollback: refusing to roll back to")
    assert "\n" not in err and "Traceback" not in err


# --- --list over a damaged store -----------------------------------------------------------------------


def test_list_names_a_corrupt_generation_and_skips_a_file_with_no_id(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture
) -> None:
    good = make_generation(store, "v1")
    activate(store, good)
    corrupt_id = make_generation(store, "v2")
    corrupt_path = store_path / SCOPE_DIR / "generation__v2.json"
    corrupt_path.write_text(
        corrupt_path.read_text(encoding="utf-8")[:-200], encoding="utf-8"
    )  # cut the tail off
    (store_path / SCOPE_DIR / "generation__v3.json").write_bytes(b"\x00\xff not json")

    code = rollback.main(argv(store_path, "--list"))

    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.strip().splitlines()
    assert any(line.startswith(good) and "ACTIVE" in line and "total=14" in line for line in lines)
    assert any(line.startswith(corrupt_id) and "CORRUPT (" in line for line in lines)
    assert "skipped generation__v3.json: holds no readable generation id" in captured.err


@pytest.mark.parametrize("content", [b"", b"{", b"\xff\xfe\x00", b"[]", b'{"generation_id": 5}'])
def test_list_survives_a_corrupt_active_pointer(
    store: GenerationManifestStore, store_path: Path, capsys: pytest.CaptureFixture, content: bytes
) -> None:
    good = make_generation(store, "v1")
    activate(store, good)
    (store_path / SCOPE_DIR / "active_pointer.json").write_bytes(content)

    code = rollback.main(argv(store_path, "--list"))

    captured = capsys.readouterr()
    assert code == 0
    assert good in captured.out
    if content not in (b"[]", b'{"generation_id": 5}'):
        assert "the active pointer is unreadable" in captured.err


# --- fenced publishers leave no orphan generation files ----------------------------------------------


def _generation_files(store_path: Path) -> list[str]:
    scope_dir = store_path / SCOPE_DIR
    return sorted(p.name for p in scope_dir.glob("generation__*.json")) if scope_dir.exists() else []


def _manifest(version: str) -> Any:
    chunks = _chunks(5, 2, 0, "org/repo-a", GOOD_COMMIT)
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    generation_id = build_manifest(FAMILY, PLATFORM, SOURCE_KIND, version).generation_id
    payload = {"lexical_index": build_lexical_index(chunks, chunk_ids, generation_id)}
    return build_manifest(FAMILY, PLATFORM, SOURCE_KIND, version, payload)


def test_a_fenced_out_publisher_writes_no_generation_file(
    store: GenerationManifestStore, store_path: Path
) -> None:
    stale = store.acquire_lease(SCOPE, "slow-publisher", "pending")
    store.acquire_lease(SCOPE, "newer-publisher", "pending")  # fences the first lease out

    with pytest.raises(StaleFencingTokenError):
        publish(store, SCOPE, None, _manifest("v1"), stale)

    assert _generation_files(store_path) == []
    assert store.read_active(SCOPE) is None


def test_a_publisher_that_loses_the_compare_removes_the_file_it_wrote(
    store: GenerationManifestStore, store_path: Path
) -> None:
    first = store.acquire_lease(SCOPE, "a", "pending")
    publish(store, SCOPE, None, _manifest("v1"), first)
    lease = store.acquire_lease(SCOPE, "b", "pending")

    with pytest.raises(ActivePointerConflictError):
        publish(store, SCOPE, "pdf::go::self_extracted::stale-expectation", _manifest("v2"), lease)

    assert _generation_files(store_path) == ["generation__v1.json"]


def test_a_generation_that_already_existed_is_not_removed_by_a_failed_publish(
    store: GenerationManifestStore, store_path: Path
) -> None:
    existing = _manifest("v1")
    store.write_generation(existing)
    stale = store.acquire_lease(SCOPE, "slow", "pending")
    store.acquire_lease(SCOPE, "fast", "pending")

    with pytest.raises(StaleFencingTokenError):
        publish(store, SCOPE, None, existing, stale)

    assert _generation_files(store_path) == ["generation__v1.json"]


# --- the shipped script runs where it is shipped ------------------------------------------------------


def _serving_lock_packages() -> set[str]:
    names = set()
    for line in (REPO_ROOT / "requirements-serving.lock").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            names.add(re.sub(r"[-_.]+", "_", match.group(1)).lower())
    return names


_PROBE_SOURCE = r"""
import json, runpy, sys
script, arguments = sys.argv[1], json.loads(sys.argv[2])
sys.argv = [script, *arguments]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code or 0
print("MODULES=" + json.dumps(sorted({m.split(".")[0] for m, mod in sys.modules.items() if "site-packages" in (getattr(mod, "__file__", None) or "")})))
sys.exit(code)
"""


def test_the_script_runs_with_only_src_on_the_path_and_imports_nothing_the_serving_image_lacks(
    store: GenerationManifestStore, store_path: Path, tmp_path: Path
) -> None:
    """What ``python /app/infra/rollback.py --list`` does in the image: the interpreter finds ``foss_mcp`` on
    PYTHONPATH (src) and the script directory, and nothing else of this repository."""
    good = make_generation(store, "v1")
    activate(store, good)
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE_SOURCE, encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
        [
            sys.executable,
            str(probe),
            str(REPO_ROOT / "infra" / "rollback.py"),
            json.dumps(argv(store_path, "--list")),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(good) or good in completed.stdout
    loaded = json.loads(
        next(line for line in completed.stdout.splitlines() if line.startswith("MODULES="))[8:]
    )
    third_party = {
        name
        for name in loaded
        if name not in sys.stdlib_module_names
        and not name.startswith(("_", "pywin32"))
        and name != "foss_mcp"
    }
    normalised_lock = _serving_lock_packages()
    missing_from_image = {
        name for name in third_party if re.sub(r"[-_.]+", "_", name).lower() not in normalised_lock
    }
    assert not missing_from_image, sorted(missing_from_image)
