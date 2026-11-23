"""Offline tests for TC-872 (REQ-G3-014): how ``infra/build_chunks.py`` verifies, labels and publishes a
``kind: upstream-sample`` entry of a pilot's authored sidecar.

The verifier is replaced the way ``tests/indexing/test_authored_examples.py`` replaces it (a fake python
prepare and verify); for the run step the python verifier is ALSO registered as a runnable one and
``run_compiled_candidate`` is replaced, so a run that failed or printed nothing can be simulated without a
toolchain. Nothing here reaches a network, a toolchain or a real interpreter.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    SIDECAR_NAME,
    UPSTREAM_SAMPLE_LABEL,
    upstream_sample_label,
)
from foss_mcp.indexing.example_candidates import normalised_code_sha256
from foss_mcp.indexing.example_verifier import (
    LEVEL_BUILT,
    LEVEL_RAN_PRODUCED_OUTPUT,
    RuntimeRecord,
    VerificationResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
API_SURFACE = REPO_ROOT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"

LIBRARY_REPOSITORY = "example/Demo-FOSS-for-Python"
LIBRARY_COMMIT = "c" * 40
PAGE_SYMBOL = "Aspose.Pdf.Page"
TOOLCHAIN = "Python 3.13.0 (test)"
README_FIRST = "Create the deck"
README_SECOND = "Save the deck"
AUTHORED_TITLE = "Page walk-through"
UP_TITLE = "Rotate a page like the upstream test"
UP_SECOND_TITLE = "Flip a page like the upstream test"
UP_PATH = "tests/test_page.py"


def _upstream_code(n: int = 1) -> str:
    return f"import demo\npage = demo.Page()\npage.rotate({n})\nprint(page)\n"


def _authored_code() -> str:
    return f"# Authored example: {AUTHORED_TITLE}\nimport demo\npage = demo.Page()\npage.flip()\n"


def _authored_entry() -> dict:
    code = _authored_code()
    return {
        "title": AUTHORED_TITLE,
        "description": "Walk through the page API.",
        "symbols": [PAGE_SYMBOL],
        "code": code,
        "code_sha256": normalised_code_sha256(code),
    }


def _upstream_entry(
    title: str = UP_TITLE, n: int = 1, path: str = UP_PATH, commit: str = LIBRARY_COMMIT
) -> dict:
    code = _upstream_code(n)
    return {
        "kind": "upstream-sample",
        "title": title,
        "description": "Rotate a page the way the upstream test does.",
        "symbols": [PAGE_SYMBOL],
        "code": code,
        "code_sha256": normalised_code_sha256(code),
        "source": {
            "path": path,
            "commit": commit,
            "license": "MIT",
            "adaptation": "Removed the unittest scaffolding.",
        },
    }


def _write_pilot(root: Path, entries: list[dict], *, commit: str = LIBRARY_COMMIT) -> Path:
    pilot_dir = root / "demo_python"
    page = pilot_dir / "pages" / "_index.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    front_matter = {
        "title": "Demo",
        "overview": {"enable": True, "title": "Overview", "content": "Demo library overview prose."},
        "single": {
            "block": [
                {
                    "title": README_FIRST,
                    "content": "Make a deck.\n\n```python\nimport demo\ndemo.make()\n```\n",
                },
                {
                    "title": README_SECOND,
                    "content": "Save the deck.\n\n```python\nimport demo\ndemo.save()\n```\n",
                },
            ]
        },
    }
    page.write_text(
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8", newline="\n"
    )
    document = {
        "schema_version": 1,
        "pilot": "demo_python",
        "library_repository": LIBRARY_REPOSITORY,
        "library_commit": commit,
        "language": "python",
        "examples": entries,
    }
    (pilot_dir / SIDECAR_NAME).write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return page


def _argv(out_dir: Path, page: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface", str(API_SURFACE),
        "--title", "Upstream samples fixture",
        "--out", str(out_dir / "chunks.json"),
        "--furnished-page", str(page),
        "--library-repository", LIBRARY_REPOSITORY,
        "--library-commit", LIBRARY_COMMIT,
        "--library-platform", "python",
        *(extra or []),
    ]  # fmt: skip


def _run_main(argv: list[str]) -> None:
    old = sys.argv
    sys.argv = ["build_chunks.py", *argv]
    try:
        build_chunks.main()
    finally:
        sys.argv = old


class _Pipeline:
    """A fake python prepare and verify. ``verified`` is the set of titles that compile; ``runtime`` maps a
    title to the ``RuntimeRecord`` its (simulated) run produces; a title without one is only compiled."""

    def __init__(self, verified: set[str], runtime: dict[str, RuntimeRecord] | None = None) -> None:
        self.verified = verified
        self.runtime = runtime or {}
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Pipeline:
        def fake_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
            self.calls.append(candidate.title)
            ok = candidate.title in self.verified
            return VerificationResult(
                candidate=candidate,
                verified=ok,
                output="ok\n" if ok else "rejected\n",
                level=LEVEL_BUILT if ok else "",
            )

        def fake_run(platform, candidate, **_kw) -> RuntimeRecord:
            return self.runtime.get(
                candidate.title, RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=5)
            )

        monkeypatch.setattr(
            build_chunks, "prepare_python_library", lambda repo, commit, workdir: Path("venv") / "python"
        )
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)
        if self.runtime:
            monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "python", fake_verify)
            monkeypatch.setattr(build_chunks, "run_compiled_candidate", fake_run)
        return self


def _ran(output_bytes: int = 5) -> RuntimeRecord:
    return RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=output_bytes)


def _chunks(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def _report(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.verification.json").read_text(encoding="utf-8"))


def _example(chunks: list[dict], title: str) -> dict | None:
    found = [chunk for chunk in chunks if f"FQN: Example: {title}\n" in chunk["text"]]
    assert len(found) <= 1
    return found[0] if found else None


def _build(tmp_path: Path, monkeypatch, entries, pipeline: _Pipeline, **kwargs) -> tuple[Path, Path]:
    page = _write_pilot(tmp_path / "pilots", entries, **kwargs)
    pipeline.install(monkeypatch)
    out = tmp_path / "out"
    _run_main(_argv(out, page))
    return out, page


ALL_OK = {README_FIRST, README_SECOND, AUTHORED_TITLE, UP_TITLE, UP_SECOND_TITLE}


# ---------------------------------------------------------------------------------------------------
# a verified upstream sample is published, labelled and attributed
# ---------------------------------------------------------------------------------------------------


def test_a_verified_upstream_sample_is_published_with_its_own_three_lines_and_a_sample_source_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry()], _Pipeline(ALL_OK, {UP_TITLE: _ran()}))

    chunk = _example(_chunks(out), UP_TITLE)
    assert chunk is not None, "the verified upstream sample must be published"
    lines = chunk["text"].split("\n")
    kind = lines.index("Kind: verified_example")
    assert lines[kind + 1 : kind + 4] == [
        "Provenance: upstream-sample",
        "Verification: passed",
        upstream_sample_label(UP_PATH),
    ]
    assert lines[kind + 3] == (
        f"Upstream sample: adapted from {UP_PATH} of the pinned upstream repository (MIT); published only after "
        "the ingestion verifier ran it against that commit."
    )
    assert lines[kind + 4] == "Verification-Level: ran-produced-output"
    assert lines[kind + 5] == f"Verified-With: {TOOLCHAIN}"
    assert lines[kind + 6] == f"Sample-Source: {UP_PATH}@{LIBRARY_COMMIT[:7]}"
    assert "Example:\n" + _upstream_code().strip() in chunk["text"]
    assert AUTHORED_LABEL not in chunk["text"] and "Provenance: authored" not in chunk["text"]

    printed = capsys.readouterr().out.splitlines()
    assert "upstream samples: 1 of 1 verified" in printed
    assert not any(line.startswith("authored examples:") for line in printed)


def test_the_sample_source_line_closes_the_header_even_when_the_verifier_states_no_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _Pipeline(ALL_OK)

    def silent_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
        return VerificationResult(candidate=candidate, verified=True, output="ok\n")

    page = _write_pilot(tmp_path / "pilots", [_upstream_entry()])
    pipeline.install(monkeypatch)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", silent_verify)
    _run_main(_argv(tmp_path / "out", page))

    text = _example(_chunks(tmp_path / "out"), UP_TITLE)["text"]  # type: ignore[index]
    assert "Verification-Level:" not in text
    sample = f"Sample-Source: {UP_PATH}@{LIBRARY_COMMIT[:7]}\n"
    assert f"{sample}\nRotate a page the way the upstream test does." in text


def test_the_published_chunk_text_states_the_label_the_sample_source_and_the_commit7_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry()], _Pipeline(ALL_OK, {UP_TITLE: _ran()}))
    text = _example(_chunks(out), UP_TITLE)["text"]  # type: ignore[index]
    assert text.count("Sample-Source:") == 1
    assert text.count("Upstream sample:") == 1
    assert len(re.findall(r"^Sample-Source: \S+@[0-9a-f]{7}$", text, re.MULTILINE)) == 1


def test_a_mixed_sidecar_publishes_each_kind_with_its_own_label_and_no_cross_contamination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = [
        _authored_entry(),
        _upstream_entry(),
        _upstream_entry(UP_SECOND_TITLE, 2, "tests/test_flip.py"),
    ]
    out, _ = _build(tmp_path, monkeypatch, entries, _Pipeline(ALL_OK))
    chunks = _chunks(out)
    authored = _example(chunks, AUTHORED_TITLE)["text"]  # type: ignore[index]
    first = _example(chunks, UP_TITLE)["text"]  # type: ignore[index]
    second = _example(chunks, UP_SECOND_TITLE)["text"]  # type: ignore[index]
    assert (
        "Provenance: authored" in authored and AUTHORED_LABEL in authored and "Sample-Source" not in authored
    )
    assert "Provenance: upstream-sample" in first and upstream_sample_label(UP_PATH) in first
    assert "Provenance: upstream-sample" in second and "tests/test_flip.py@" in second
    assert (
        upstream_sample_label("tests/test_flip.py") in second and upstream_sample_label(UP_PATH) not in second
    )
    printed = capsys.readouterr().out.splitlines()
    assert "authored examples: 1 of 1 verified" in printed
    assert "upstream samples: 2 of 2 verified" in printed


def test_the_label_constant_is_the_text_the_chunk_carries() -> None:
    assert UPSTREAM_SAMPLE_LABEL.replace("{path}", UP_PATH) == upstream_sample_label(UP_PATH)


# ---------------------------------------------------------------------------------------------------
# honest publish (TC-802) applies identically
# ---------------------------------------------------------------------------------------------------


def test_an_upstream_sample_that_did_not_compile_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry()], _Pipeline(ALL_OK - {UP_TITLE}))
    chunks = _chunks(out)
    assert _example(chunks, UP_TITLE) is None
    assert not any(
        "Upstream sample:" in chunk["text"] or "Sample-Source" in chunk["text"] for chunk in chunks
    )
    record = _report(out)[-1]
    assert (record["title"], record["kind"], record["verified"]) == (UP_TITLE, "upstream-sample", False)
    assert "upstream samples: 0 of 1 verified" in capsys.readouterr().out


def test_an_upstream_sample_whose_run_failed_is_not_published_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = RuntimeRecord(compiled=True, ran=True, exit_code=1, produced_output=0, error="Traceback boom")
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry()], _Pipeline(ALL_OK, {UP_TITLE: failed}))
    assert _example(_chunks(out), UP_TITLE) is None
    record = _report(out)[-1]
    assert record["kind"] == "upstream-sample" and record["verified"] is False
    assert record["not_published"] == "the run failed" and record["runtime_status"] == "failed"


def test_an_upstream_sample_that_printed_nothing_is_not_published_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    silent = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry()], _Pipeline(ALL_OK, {UP_TITLE: silent}))
    assert _example(_chunks(out), UP_TITLE) is None
    record = _report(out)[-1]
    assert record["not_published"] == "the run produced no output" and record["runtime_status"] == "no_output"


def test_an_authored_example_is_judged_the_same_way_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = {
        AUTHORED_TITLE: RuntimeRecord(compiled=True, ran=True, exit_code=1, error="x"),
        UP_TITLE: _ran(),
    }
    out, _ = _build(tmp_path, monkeypatch, [_authored_entry(), _upstream_entry()], _Pipeline(ALL_OK, runtime))
    chunks = _chunks(out)
    assert _example(chunks, AUTHORED_TITLE) is None
    assert _example(chunks, UP_TITLE) is not None


def test_a_hand_built_upstream_candidate_without_a_source_is_never_published_unlabelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    page = _write_pilot(tmp_path / "pilots", [_upstream_entry()])
    pipeline = _Pipeline(ALL_OK).install(monkeypatch)
    real_loader = build_chunks._load_authored_candidates

    def stripped(args):
        return tuple(dataclasses.replace(c, sample_source=()) for c in real_loader(args))

    monkeypatch.setattr(build_chunks, "_load_authored_candidates", stripped)
    _run_main(_argv(tmp_path / "out", page))
    assert UP_TITLE in pipeline.calls, "it was verified"
    assert _example(_chunks(tmp_path / "out"), UP_TITLE) is None


# ---------------------------------------------------------------------------------------------------
# the verifier, the report and the cache
# ---------------------------------------------------------------------------------------------------


def test_an_upstream_sample_goes_through_the_same_verifier_after_the_readme_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _Pipeline(ALL_OK)
    _build(tmp_path, monkeypatch, [_upstream_entry(), _authored_entry()], pipeline)
    assert pipeline.calls == [README_FIRST, README_SECOND, UP_TITLE, AUTHORED_TITLE]


def test_the_verification_record_of_an_upstream_sample_has_its_own_kind_and_continues_the_numbering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry(), _authored_entry()], _Pipeline(ALL_OK))
    assert [(r["title"], r["kind"], r["index"]) for r in _report(out)] == [
        (README_FIRST, "candidate", 0),
        (README_SECOND, "candidate", 1),
        (UP_TITLE, "upstream-sample", 2),
        (AUTHORED_TITLE, "authored", 3),
    ]


def test_every_readme_chunk_is_identical_with_and_without_upstream_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _write_pilot(tmp_path / "with", [_upstream_entry()])
    _Pipeline(ALL_OK).install(monkeypatch)
    _run_main(_argv(tmp_path / "out_with", page))
    (page.parent.parent / SIDECAR_NAME).unlink()
    _run_main(_argv(tmp_path / "out_without", page))
    readme = [c for c in _chunks(tmp_path / "out_with") if UP_TITLE not in c["text"]]
    assert readme == _chunks(tmp_path / "out_without")


def test_an_authored_only_sidecar_publishes_byte_identically_to_the_old_three_line_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _ = _build(tmp_path, monkeypatch, [_authored_entry()], _Pipeline(ALL_OK))
    text = _example(_chunks(out), AUTHORED_TITLE)["text"]  # type: ignore[index]
    lines = text.split("\n")
    kind = lines.index("Kind: verified_example")
    assert lines[kind + 1 : kind + 4] == ["Provenance: authored", "Verification: passed", AUTHORED_LABEL]
    assert "Sample-Source" not in text and "upstream" not in text.lower().split("example:")[0]
    printed = capsys.readouterr().out.splitlines()
    assert "authored examples: 1 of 1 verified" in printed
    assert not any(line.startswith("upstream samples:") for line in printed)


def test_the_cache_keeps_the_upstream_sample_verdict_apart_from_an_authored_and_a_readme_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    page = _write_pilot(tmp_path / "pilots", [_upstream_entry()])
    _Pipeline(ALL_OK).install(monkeypatch)
    extra = ["--verification-cache", str(cache), "--verification-cache-out", str(cache)]
    _run_main(_argv(tmp_path / "first", page, extra))
    entries = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    sample = [e for e in entries if e["title"] == UP_TITLE]
    assert [e["provenance"] for e in sample] == ["upstream-sample"]
    assert sample[0]["code_sha256"] == normalised_code_sha256(_upstream_code())

    # a second run is served from the cache and publishes the identical chunk text
    pipeline = _Pipeline(ALL_OK).install(monkeypatch)
    _run_main(_argv(tmp_path / "second", page, extra))
    assert UP_TITLE not in pipeline.calls, "served from the cache"
    assert _example(_chunks(tmp_path / "first"), UP_TITLE) == _example(_chunks(tmp_path / "second"), UP_TITLE)


# ---------------------------------------------------------------------------------------------------
# an invalid sidecar never publishes any sample
# ---------------------------------------------------------------------------------------------------


def test_a_source_commit_other_than_the_library_commit_ignores_the_whole_sidecar_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pipeline = _Pipeline(ALL_OK)
    out, _ = _build(tmp_path, monkeypatch, [_upstream_entry(commit="d" * 40)], pipeline)
    printed = capsys.readouterr().out
    assert printed.count("authored examples ignored:") == 1 and "source.commit" in printed
    assert pipeline.calls == [README_FIRST, README_SECOND]
    assert _example(_chunks(out), UP_TITLE) is None


def test_a_sidecar_pinned_to_another_commit_is_ignored_and_publishes_no_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _ = _build(
        tmp_path, monkeypatch, [_upstream_entry(commit="d" * 40)], _Pipeline(ALL_OK), commit="d" * 40
    )
    assert "authored examples ignored:" in capsys.readouterr().out
    assert _example(_chunks(out), UP_TITLE) is None


# ---------------------------------------------------------------------------------------------------
# the wiring is in the real entrypoint file
# ---------------------------------------------------------------------------------------------------


def test_build_chunks_source_wires_the_upstream_sample_label_and_source_line() -> None:
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    for needle in (
        "upstream_sample_label(",
        "KIND_UPSTREAM_SAMPLE",
        "Sample-Source: {sample_path}@{sample_commit[:7]}",
        "sample_source_line",
    ):
        assert needle in source, needle
    assert LEVEL_RAN_PRODUCED_OUTPUT == "ran-produced-output"
