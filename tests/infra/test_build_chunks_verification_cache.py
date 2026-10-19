"""Offline tests for TC-441's persistent, committed verification cache (REQ-G3-007).

Every real compile/run verdict a toolchain produces is thrown away today: the ingestion Job
builds --out under /tmp (an emptyDir), so every re-ingestion clones, builds and compiles
everything again, even a candidate whose code, library commit, verifier platform and toolchain
have not changed since it was last verified true. infra/build_chunks.py now accepts
--verification-cache/--verification-cache-out/--no-verification-cache (all optional and inert
when absent) to reuse and persist those verdicts.

Nothing here touches git, a venv, a network, or a real interpreter: the rust and python prepare
functions are stubbed, and their verify functions are replaced inside _PLATFORM_DISPATCH itself
(the dispatch table captures the function object at import time), exactly as
tests/infra/test_build_chunks_degradation.py and tests/indexing/test_build_chunks_verification_report.py
already do. toolchain_id is always monkeypatched; it never runs a real toolchain under test.
"""

from __future__ import annotations

import dataclasses
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
DOCKERFILE_INGESTION = REPO_ROOT / "Dockerfile.ingestion"
VERIFIED_EXAMPLES_DIR = REPO_ROOT / "tests" / "fixtures" / "verified_examples"

LIBRARY_REPOSITORY = "example-org/reference-rust"
LIBRARY_COMMIT = "1" * 40
CRATE_NAME = "example_crate"
TOOLCHAIN = "rustc 1.99.0 (abcdef123 2026-01-01)"
TITLE = "Build a document"
CODE = 'fn main() {\n    println!("ok");\n}'

PY_LIBRARY_REPOSITORY = "example/Aspose.Slides-FOSS-for-Python"
PY_LIBRARY_COMMIT = "2" * 40
PY_TOOLCHAIN = "Python 3.13.1"
FIRST_TITLE = "Create the deck"
FIRST_CODE = 'import aspose.slides as slides\nwith slides.Presentation() as p:\n    p.save("out.pptx")'
SECOND_TITLE = "Open the saved deck"
SECOND_CODE = 'import aspose.slides as slides\nwith slides.Presentation("out.pptx") as p:\n    pass'


# ---------------------------------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------------------------------


def _write_rust_page(path: Path, blocks: list[tuple[str, str]]) -> Path:
    front_matter = {
        "title": "Rust cache fixture",
        "single": {
            "block": [
                {"title": title, "content": f"Prose for {title}.\n\n```rust\n{code}\n```\n"}
                for title, code in blocks
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _write_python_page(path: Path) -> Path:
    front_matter = {
        "title": "Python cache fixture",
        "single": {
            "block": [
                {"title": FIRST_TITLE, "content": f"Prose.\n\n```python\n{FIRST_CODE}\n```\n"},
                {"title": SECOND_TITLE, "content": f"Prose.\n\n```python\n{SECOND_CODE}\n```\n"},
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _argv_rust(out_dir: Path, page_path: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Cache fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page_path),
        "--library-repository",
        LIBRARY_REPOSITORY,
        "--library-commit",
        LIBRARY_COMMIT,
        "--library-platform",
        "rust",
        "--library-crate-name",
        CRATE_NAME,
    ] + (extra or [])


def _argv_python(out_dir: Path, page_path: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Cache fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page_path),
        "--library-repository",
        PY_LIBRARY_REPOSITORY,
        "--library-commit",
        PY_LIBRARY_COMMIT,
        "--library-platform",
        "python",
    ] + (extra or [])


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def _read_report(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.verification.json").read_text(encoding="utf-8"))


def _read_chunks(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def _example_chunk_texts(chunks: list[dict]) -> list[str]:
    return sorted(chunk["text"] for chunk in chunks if "FQN: Example: " in chunk["text"])


def _install_tracking_rust(
    monkeypatch: pytest.MonkeyPatch, verified_titles: set[str]
) -> tuple[list, list[str]]:
    prepare_calls: list[tuple] = []
    verify_calls: list[str] = []

    def fake_prepare(repository: str, commit: str, workdir: Path) -> Path:
        prepare_calls.append((repository, commit, workdir))
        return workdir

    def fake_verify(
        candidate, *, workdir: Path, library_crate_name: str, library_dir: Path
    ) -> VerificationResult:
        verify_calls.append(candidate.title)
        verified = candidate.title in verified_titles
        return VerificationResult(
            candidate=candidate, verified=verified, output=f"real output for {candidate.title}"
        )

    monkeypatch.setattr(build_chunks, "prepare_rust_library", fake_prepare)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
    return prepare_calls, verify_calls


def _install_tracking_python(
    monkeypatch: pytest.MonkeyPatch, verified_titles: set[str]
) -> tuple[list, list[str]]:
    prepare_calls: list[tuple] = []
    verify_calls: list[str] = []

    def fake_prepare(repository: str, commit: str, workdir: Path) -> Path:
        prepare_calls.append((repository, commit, workdir))
        return Path("dummy-venv") / "python"

    def fake_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
        verify_calls.append(candidate.title)
        verified = candidate.title in verified_titles
        return VerificationResult(
            candidate=candidate, verified=verified, output=f"python output for {candidate.title}"
        )

    monkeypatch.setattr(build_chunks, "prepare_python_library", fake_prepare)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)
    return prepare_calls, verify_calls


def _entry(
    *,
    provenance: str = "readme",
    title: str,
    code_sha256: str,
    library_commit: str,
    platform: str,
    toolchain: str,
    verified: bool,
    output: str,
) -> dict:
    return {
        "provenance": provenance,
        "title": title,
        "code_sha256": code_sha256,
        "library_commit": library_commit,
        "platform": platform,
        "toolchain": toolchain,
        "verified": verified,
        "output": output,
    }


def _write_cache(path: Path, entries: list[dict]) -> None:
    path.write_text(build_chunks.verification_cache_document(entries), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------
# normalised_code_sha256
# ---------------------------------------------------------------------------------------------------


def test_normalised_code_sha256_ignores_crlf_trailing_whitespace_and_blank_lines() -> None:
    a = 'fn main() {\n    println!("ok");   \n}\n\n'
    b = '\r\nfn main() {\r\n    println!("ok");\r\n}\r\n'
    assert build_chunks.normalised_code_sha256(a) == build_chunks.normalised_code_sha256(b)


def test_normalised_code_sha256_changes_for_a_changed_statement() -> None:
    a = 'fn main() {\n    println!("ok");\n}\n'
    b = 'fn main() {\n    println!("no");\n}\n'
    assert build_chunks.normalised_code_sha256(a) != build_chunks.normalised_code_sha256(b)


# ---------------------------------------------------------------------------------------------------
# hit / miss mechanics (rust: shared_page_workdir False)
# ---------------------------------------------------------------------------------------------------


def test_a_full_hit_calls_neither_prepare_nor_verify_and_marks_cached_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    prepare_calls, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        [
            _entry(
                title=TITLE,
                code_sha256=code_hash,
                library_commit=LIBRARY_COMMIT,
                platform="rust",
                toolchain=TOOLCHAIN,
                verified=True,
                output="cached output",
            )
        ],
    )

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert prepare_calls == []
    assert verify_calls == []
    report = _read_report(tmp_path)
    assert len(report) == 1
    record = report[0]
    assert record["cached"] is True
    assert record["verified"] is True
    assert record["code_sha256"] == code_hash
    assert record["toolchain"] == TOOLCHAIN
    assert "(1 from cache)" in capsys.readouterr().out


def test_a_candidate_with_no_cache_entry_at_all_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    prepare_calls, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, [])

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert verify_calls == [TITLE]
    assert len(prepare_calls) == 1


@pytest.mark.parametrize("field", ["code_sha256", "library_commit", "platform", "toolchain"])
def test_a_mismatch_in_any_key_field_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)
    entry = _entry(
        title=TITLE,
        code_sha256=code_hash,
        library_commit=LIBRARY_COMMIT,
        platform="rust",
        toolchain=TOOLCHAIN,
        verified=True,
        output="cached output",
    )
    entry[field] = "wrong-value"
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, [entry])

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert verify_calls == [TITLE]


def test_a_verified_false_cache_entry_is_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        [
            _entry(
                title=TITLE,
                code_sha256=code_hash,
                library_commit=LIBRARY_COMMIT,
                platform="rust",
                toolchain=TOOLCHAIN,
                verified=False,
                output="previously rejected",
            )
        ],
    )

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert verify_calls == [TITLE]


def test_no_verification_cache_flag_reverifies_despite_a_matching_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        [
            _entry(
                title=TITLE,
                code_sha256=code_hash,
                library_commit=LIBRARY_COMMIT,
                platform="rust",
                toolchain=TOOLCHAIN,
                verified=True,
                output="cached output",
            )
        ],
    )

    _run_main(
        _argv_rust(tmp_path, page_path, ["--verification-cache", str(cache_path), "--no-verification-cache"])
    )

    assert verify_calls == [TITLE]


def test_toolchain_id_none_reuses_nothing_and_adds_no_new_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: None)
    _, verify_calls = _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        [
            _entry(
                title=TITLE,
                code_sha256=code_hash,
                library_commit=LIBRARY_COMMIT,
                platform="rust",
                toolchain="irrelevant",
                verified=True,
                output="cached output",
            )
        ],
    )
    cache_out = tmp_path / "cache_out.json"

    _run_main(
        _argv_rust(
            tmp_path,
            page_path,
            ["--verification-cache", str(cache_path), "--verification-cache-out", str(cache_out)],
        )
    )

    assert verify_calls == [TITLE], "nothing is reused when toolchain_id returns None"
    report = _read_report(tmp_path)
    assert "cached" not in report[0], "the run's own records are untouched by inert cache machinery"
    written = json.loads(cache_out.read_text(encoding="utf-8"))
    assert len(written["entries"]) == 1, "the existing entry's history is kept, nothing new is added"
    assert written["entries"][0]["output"] == "cached output"
    assert "no rust toolchain detected" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------
# shared_page_workdir (python): whole-page reuse
# ---------------------------------------------------------------------------------------------------


def test_python_full_hit_verifies_no_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: PY_TOOLCHAIN)
    prepare_calls, verify_calls = _install_tracking_python(
        monkeypatch, verified_titles={FIRST_TITLE, SECOND_TITLE}
    )
    page_path = _write_python_page(tmp_path / "_index.md")
    entries = [
        _entry(
            title=FIRST_TITLE,
            code_sha256=build_chunks.normalised_code_sha256(FIRST_CODE),
            library_commit=PY_LIBRARY_COMMIT,
            platform="python",
            toolchain=PY_TOOLCHAIN,
            verified=True,
            output="cached first",
        ),
        _entry(
            title=SECOND_TITLE,
            code_sha256=build_chunks.normalised_code_sha256(SECOND_CODE),
            library_commit=PY_LIBRARY_COMMIT,
            platform="python",
            toolchain=PY_TOOLCHAIN,
            verified=True,
            output="cached second",
        ),
    ]
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, entries)

    _run_main(_argv_python(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert prepare_calls == [], "a fully cached page publishes with no network at all"
    assert verify_calls == []
    report = _read_report(tmp_path)
    assert len(report) == 2
    assert all(record["cached"] is True for record in report)


def test_python_partial_hit_reverifies_every_candidate_and_marks_none_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: PY_TOOLCHAIN)
    prepare_calls, verify_calls = _install_tracking_python(
        monkeypatch, verified_titles={FIRST_TITLE, SECOND_TITLE}
    )
    page_path = _write_python_page(tmp_path / "_index.md")
    # Only FIRST_TITLE has a cache entry: a partial hit reuses nothing on a shared-workdir page.
    entries = [
        _entry(
            title=FIRST_TITLE,
            code_sha256=build_chunks.normalised_code_sha256(FIRST_CODE),
            library_commit=PY_LIBRARY_COMMIT,
            platform="python",
            toolchain=PY_TOOLCHAIN,
            verified=True,
            output="cached first",
        )
    ]
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, entries)

    _run_main(_argv_python(tmp_path, page_path, ["--verification-cache", str(cache_path)]))

    assert verify_calls == [FIRST_TITLE, SECOND_TITLE]
    assert len(prepare_calls) == 1
    report = _read_report(tmp_path)
    assert len(report) == 2
    assert all(record["cached"] is False for record in report)


# ---------------------------------------------------------------------------------------------------
# cache file loading and validation
# ---------------------------------------------------------------------------------------------------


def test_missing_cache_file_starts_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    missing = tmp_path / "does-not-exist.json"

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(missing)]))

    assert "starting with an empty cache" in capsys.readouterr().out


def test_malformed_json_cache_exits_2_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as info:
        _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(bad)]))

    assert info.value.code == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert str(bad) in err


def test_wrong_schema_version_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 2, "entries": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as info:
        _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(bad)]))

    assert info.value.code == 2
    assert capsys.readouterr().err.count("\n") == 1


def test_cache_file_with_authored_top_level_key_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 1, "entries": [], "authored": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as info:
        _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache", str(bad)]))

    assert info.value.code == 2
    assert capsys.readouterr().err.count("\n") == 1


# ---------------------------------------------------------------------------------------------------
# cache writing semantics
# ---------------------------------------------------------------------------------------------------


def test_written_cache_is_sorted_by_key_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    blocks = [
        ("Zebra example", 'fn main() { println!("z"); }'),
        ("Alpha example", 'fn main() { println!("a"); }'),
    ]
    _install_tracking_rust(monkeypatch, verified_titles={title for title, _ in blocks})
    page_path = _write_rust_page(tmp_path / "_index.md", blocks)
    cache_out = tmp_path / "cache_out.json"

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache-out", str(cache_out)]))

    written = json.loads(cache_out.read_text(encoding="utf-8"))
    titles = [entry["title"] for entry in written["entries"]]
    assert titles == ["Alpha example", "Zebra example"]


def test_written_cache_keeps_entries_of_other_commits_and_toolchains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    old_entry = _entry(
        title=TITLE,
        code_sha256=build_chunks.normalised_code_sha256(CODE),
        library_commit="0" * 40,
        platform="rust",
        toolchain="rustc 1.0.0 (old)",
        verified=True,
        output="old run",
    )
    cache_in = tmp_path / "cache_in.json"
    _write_cache(cache_in, [old_entry])
    cache_out = tmp_path / "cache_out.json"

    _run_main(
        _argv_rust(
            tmp_path,
            page_path,
            ["--verification-cache", str(cache_in), "--verification-cache-out", str(cache_out)],
        )
    )

    written = json.loads(cache_out.read_text(encoding="utf-8"))
    commits = {entry["library_commit"] for entry in written["entries"]}
    assert "0" * 40 in commits
    assert LIBRARY_COMMIT in commits
    assert len(written["entries"]) == 2


def test_cache_entry_output_is_truncated_to_2000_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    long_output = "x" * 5000

    def fake_prepare(repository: str, commit: str, workdir: Path) -> Path:
        return workdir

    def fake_verify(
        candidate, *, workdir: Path, library_crate_name: str, library_dir: Path
    ) -> VerificationResult:
        return VerificationResult(candidate=candidate, verified=True, output=long_output)

    monkeypatch.setattr(build_chunks, "prepare_rust_library", fake_prepare)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    cache_out = tmp_path / "cache_out.json"

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache-out", str(cache_out)]))

    written = json.loads(cache_out.read_text(encoding="utf-8"))
    assert len(written["entries"][0]["output"]) == 2000
    assert written["entries"][0]["output"] == long_output[-2000:]
    report = _read_report(tmp_path)
    assert len(report[0]["output"]) == 5000, "the FULL output stays in the .verification.json report"


def test_second_identical_run_writes_identical_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    cache_out = tmp_path / "cache_out.json"

    _run_main(_argv_rust(tmp_path, page_path, ["--verification-cache-out", str(cache_out)]))
    first_bytes = cache_out.read_bytes()

    _run_main(
        _argv_rust(
            tmp_path,
            page_path,
            ["--verification-cache", str(cache_out), "--verification-cache-out", str(cache_out)],
        )
    )
    second_bytes = cache_out.read_bytes()

    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------------------------------
# verification_cache_key / provenance (TC-460 readiness)
# ---------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ReadmeStub:
    title: str
    code: str


@dataclasses.dataclass(frozen=True)
class _AuthoredStub:
    title: str
    code: str
    provenance: str


def test_verification_cache_key_distinguishes_authored_from_readme_same_code() -> None:
    readme = _ReadmeStub(title=TITLE, code=CODE)
    authored = _AuthoredStub(title=TITLE, code=CODE, provenance="authored")

    readme_key = build_chunks.verification_cache_key(readme, LIBRARY_COMMIT, "rust", TOOLCHAIN)
    authored_key = build_chunks.verification_cache_key(authored, LIBRARY_COMMIT, "rust", TOOLCHAIN)

    assert readme_key != authored_key
    assert readme_key[0] == "readme"
    assert authored_key[0] == "authored"


def test_verification_cache_key_ignores_title() -> None:
    a = _ReadmeStub(title="Title A", code=CODE)
    b = _ReadmeStub(title="Title B", code=CODE)

    key_a = build_chunks.verification_cache_key(a, LIBRARY_COMMIT, "rust", TOOLCHAIN)
    key_b = build_chunks.verification_cache_key(b, LIBRARY_COMMIT, "rust", TOOLCHAIN)

    assert key_a == key_b


# ---------------------------------------------------------------------------------------------------
# published chunk text is byte-identical regardless of the cache
# ---------------------------------------------------------------------------------------------------


def test_chunk_text_identical_with_and_without_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])

    without_dir = tmp_path / "without"
    without_dir.mkdir()
    _run_main(_argv_rust(without_dir, page_path))
    without_texts = _example_chunk_texts(_read_chunks(without_dir))

    with_dir = tmp_path / "with"
    with_dir.mkdir()
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, [])
    _run_main(_argv_rust(with_dir, page_path, ["--verification-cache", str(cache_path)]))
    with_texts = _example_chunk_texts(_read_chunks(with_dir))

    assert without_texts, "the page must yield a published example, or this test proves nothing"
    assert without_texts == with_texts


def test_chunk_text_identical_for_cached_and_fresh_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    page_path = _write_rust_page(tmp_path / "_index.md", [(TITLE, CODE)])
    code_hash = build_chunks.normalised_code_sha256(CODE)

    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    _install_tracking_rust(monkeypatch, verified_titles={TITLE})
    _run_main(_argv_rust(fresh_dir, page_path))
    fresh_texts = _example_chunk_texts(_read_chunks(fresh_dir))

    cached_dir = tmp_path / "cached"
    cached_dir.mkdir()
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        [
            _entry(
                title=TITLE,
                code_sha256=code_hash,
                library_commit=LIBRARY_COMMIT,
                platform="rust",
                toolchain=TOOLCHAIN,
                verified=True,
                output="cached output",
            )
        ],
    )
    _run_main(_argv_rust(cached_dir, page_path, ["--verification-cache", str(cache_path)]))
    cached_texts = _example_chunk_texts(_read_chunks(cached_dir))

    assert fresh_texts, "the page must yield a published example, or this test proves nothing"
    assert fresh_texts == cached_texts


# ---------------------------------------------------------------------------------------------------
# wiring: ingestion-job.yaml and Dockerfile.ingestion (AGENTS.md integration rule)
# ---------------------------------------------------------------------------------------------------


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-441's wiring test requires it: source "
            "scripts/toolchain/activate.ps1, which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def test_ingestion_job_yaml_wires_both_cache_flags_for_a_furnished_pilot() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    pilot = next(p for p in values["ingestion"]["pilots"] if p.get("furnishedPage"))

    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", "image.tag=tc441-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
    jobs = [doc for doc in docs if doc.get("kind") == "Job" and doc["metadata"]["name"].endswith(suffix)]
    assert len(jobs) == 1
    tokens = shlex.split(jobs[0]["spec"]["template"]["spec"]["containers"][0]["args"][0])

    assert "--verification-cache" in tokens
    cache_index = tokens.index("--verification-cache")
    expected_cache = f"/app/fixtures/verified_examples/{pilot['family']}_{pilot['platform']}.json"
    assert tokens[cache_index + 1] == expected_cache

    assert "--verification-cache-out" in tokens
    out_index = tokens.index("--verification-cache-out")
    expected_out = (
        f"{values['manifests']['mountPath']}/verification_cache_{pilot['family']}_{pilot['platform']}.json"
    )
    assert tokens[out_index + 1] == expected_out


def test_dockerfile_ingestion_copies_the_verified_examples_fixtures() -> None:
    lines = DOCKERFILE_INGESTION.read_text(encoding="utf-8").splitlines()
    copies = [
        line.strip() for line in lines if line.strip().startswith("COPY ") and "verified_examples" in line
    ]
    assert copies == ["COPY tests/fixtures/verified_examples/ ./fixtures/verified_examples/"], copies


def test_every_committed_verified_examples_fixture_parses_and_is_named_after_a_pilot() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    known = {f"{p['family']}_{p['platform']}" for p in values["ingestion"]["pilots"]}
    fixtures = sorted(VERIFIED_EXAMPLES_DIR.glob("*.json"))
    for path in fixtures:
        entries = build_chunks.parse_verification_cache(path.read_text(encoding="utf-8"))
        assert isinstance(entries, list)
        assert path.stem in known, f"{path.name} is not named after a pilot in values.yaml"


# ---------------------------------------------------------------------------------------------------
# TC-565 (REQ-G3-009): the run verdict is recorded beside the compile verdict
# ---------------------------------------------------------------------------------------------------

_RUNTIME_KEYS = {"compiled": True, "ran": True, "exit_code": 1, "produced_output": 0, "runtime_error": "boom"}


def _runtime_entry(**overrides: object) -> dict:
    entry = _entry(
        title="Save a box",
        code_sha256="b" * 64,
        library_commit=LIBRARY_COMMIT,
        platform="java",
        toolchain="javac 21",
        verified=True,
        output="",
    )
    entry.update(overrides)
    return entry


def test_tc565_an_entry_may_carry_the_run_verdict_beside_the_compile_verdict() -> None:
    text = build_chunks.verification_cache_document([_runtime_entry(**_RUNTIME_KEYS)])
    (entry,) = build_chunks.parse_verification_cache(text)
    assert {key: entry[key] for key in _RUNTIME_KEYS} == _RUNTIME_KEYS
    assert entry["verified"] is True, "verified keeps its meaning: the compile verdict"


@pytest.mark.parametrize("key", sorted(_RUNTIME_KEYS))
def test_tc565_each_run_field_is_optional_on_its_own(key: str) -> None:
    text = build_chunks.verification_cache_document([_runtime_entry(**{key: _RUNTIME_KEYS[key]})])
    assert build_chunks.parse_verification_cache(text)[0][key] == _RUNTIME_KEYS[key]


def test_tc565_a_legacy_entry_without_the_run_fields_is_still_valid() -> None:
    text = build_chunks.verification_cache_document([_runtime_entry()])
    (entry,) = build_chunks.parse_verification_cache(text)
    assert not set(entry) & set(_RUNTIME_KEYS)


@pytest.mark.parametrize("extra", ["authored", "stdout", "Ran", "runtime"])
def test_tc565_a_field_that_is_not_documented_is_still_rejected(extra: str) -> None:
    text = build_chunks.verification_cache_document([_runtime_entry(**{extra: 1})])
    with pytest.raises(ValueError, match="eight documented fields"):
        build_chunks.parse_verification_cache(text)


def test_tc565_an_entry_missing_a_required_field_is_still_rejected() -> None:
    entry = _runtime_entry(**_RUNTIME_KEYS)
    del entry["toolchain"]
    with pytest.raises(ValueError, match="eight documented fields"):
        build_chunks.parse_verification_cache(build_chunks.verification_cache_document([entry]))


def test_tc565_a_new_run_verdict_replaces_the_old_entry_with_the_same_key() -> None:
    legacy = _runtime_entry()
    rerun = _runtime_entry(**_RUNTIME_KEYS)
    merged = build_chunks.merge_verification_cache([legacy], [rerun])
    assert len(merged) == 1 and merged[0]["ran"] is True


def test_tc565_a_run_entry_survives_a_write_and_a_read_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    entries = [_runtime_entry(**_RUNTIME_KEYS)]
    assert build_chunks.write_verification_cache_if_changed(path, entries) is True
    assert build_chunks.load_verification_cache(path) == entries
    assert build_chunks.write_verification_cache_if_changed(path, entries) is False
