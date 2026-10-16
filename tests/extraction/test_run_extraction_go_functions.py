"""TC-525 (REQ-G3-008): Go package functions are selected by source order within a bounded quota.

Offline. The artifacts are synthetic dicts, except the last test, which runs
``extract_pinned_repository`` over the committed ``go_sample`` package with ``clone_shallow``
replaced by a copy of it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction

GO_SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "go_sample"


def _type(name: str, *, refs: tuple[str, ...] = (), file: str = "pkg/types.go") -> dict[str, Any]:
    return {
        "name": name,
        "kind": "type_spec",
        "file": file,
        "line": 1,
        "bases": [],
        "methods": [
            {"name": f"M{index}", "params": [], "return_type": ref} for index, ref in enumerate(refs)
        ],
        "properties": [],
    }


def _function(name: str, *, file: str, line: int, receiver: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": "function",
        "file": file,
        "line": line,
        "bases": [],
        "methods": [],
        "properties": [],
        "params": [],
        "return_type": "",
    }
    if receiver:
        entry["receiver_type"] = receiver
    return entry


def _artifact(entries: list[dict[str, Any]], language: str = "go") -> dict[str, Any]:
    return {
        "source_repository": "acme/widgets",
        "source_commit": "a" * 40,
        "language": language,
        "type_count": len(entries),
        "types": entries,
    }


def _ring(count: int) -> list[dict[str, Any]]:
    """*count* types that reference one another in a ring, so every one has centrality 1."""
    return [_type(f"T{index:03d}", refs=(f"T{(index + 1) % count:03d}",)) for index in range(count)]


def _names(artifact: dict[str, Any], kind: str) -> list[str]:
    return [entry["name"] for entry in artifact["types"] if entry["kind"] == kind]


def test_go_functions_are_selected_by_source_order_not_centrality() -> None:
    functions = [_function(f"Func{index:02d}", file="pkg/api.go", line=10 + index) for index in range(12)]
    reduced = run_extraction.reduce_fixture(_artifact(_ring(40) + functions), max_types=30)
    assert set(_names(reduced, "function")) == {f"Func{index:02d}" for index in range(12)}
    assert len(reduced["types"]) == 30


def test_go_function_quota_is_bounded() -> None:
    functions = [_function(f"Func{index:03d}", file="pkg/api.go", line=index + 1) for index in range(100)]
    reduced = run_extraction.reduce_fixture(_artifact(_ring(40) + functions), max_types=20)
    assert run_extraction.GO_FUNCTION_QUOTA_PERCENT == 40
    assert sorted(_names(reduced, "function")) == [f"Func{index:03d}" for index in range(8)]
    assert len(_names(reduced, "type_spec")) == 12


def test_quota_follows_file_then_line_order() -> None:
    functions = [
        _function("LateInB", file="pkg/b.go", line=1),
        _function("SecondInA", file="pkg/a.go", line=20),
        _function("FirstInA", file="pkg/a.go", line=3),
        _function("Overflow", file="pkg/c.go", line=1),
    ]
    reduced = run_extraction.reduce_fixture(_artifact(_ring(30) + functions), max_types=5)
    # The quota of 5 slots is 2: the first two by (file, line), whatever order the artifact lists them.
    assert sorted(_names(reduced, "function")) == ["FirstInA", "SecondInA"]


def test_must_keep_types_still_come_first_for_go() -> None:
    functions = [_function(f"Func{index:02d}", file="pkg/api.go", line=index + 1) for index in range(20)]
    entries = [_type("Wanted"), *_ring(30), *functions]
    reduced = run_extraction.reduce_fixture(_artifact(entries), max_types=10, must_keep={"Wanted"})
    names = {entry["name"] for entry in reduced["types"]}
    assert "Wanted" in names
    assert len(_names(reduced, "function")) == 4
    assert len(reduced["types"]) == 10


def test_methods_with_a_receiver_are_not_counted_as_package_functions() -> None:
    marked = [
        _function(f"Marked{index}", file="pkg/a.go", line=index + 1, receiver="Missing") for index in range(6)
    ]
    plain = [_function(f"Plain{index}", file="pkg/z.go", line=index + 1) for index in range(3)]
    reduced = run_extraction.reduce_fixture(_artifact(_ring(30) + marked + plain), max_types=10)
    # The quota is 4: the three plain functions come first although the marked ones sort earlier.
    assert {"Plain0", "Plain1", "Plain2"} <= set(_names(reduced, "function"))
    assert not {"Marked0", "Marked1", "Marked2", "Marked3", "Marked4", "Marked5"} & set(
        _names(reduced, "function")
    )


def test_a_non_go_artifact_selects_exactly_as_before() -> None:
    functions = [_function(f"Func{index:02d}", file="pkg/api.ts", line=index + 1) for index in range(12)]
    entries = _ring(40) + functions
    reduced = run_extraction.reduce_fixture(_artifact(entries, language="typescript"), max_types=30)
    assert _names(reduced, "function") == []
    assert len(_names(reduced, "type_spec")) == 30


def test_reduce_is_deterministic_for_go() -> None:
    functions = [
        _function(f"Func{index:02d}", file=f"pkg/f{index % 3}.go", line=index) for index in range(25)
    ]
    artifact = _artifact(_ring(40) + functions)
    first = run_extraction.reduce_fixture(artifact, max_types=20)
    second = run_extraction.reduce_fixture(artifact, max_types=20)
    assert [entry["name"] for entry in first["types"]] == [entry["name"] for entry in second["types"]]


def test_api_counts_separates_types_functions_and_methods() -> None:
    entries = [
        _type("A", refs=("B", "C")),
        _type("B", refs=("A",)),
        _function("Open", file="pkg/a.go", line=1),
        _function("Run", file="pkg/a.go", line=2, receiver="Missing"),
    ]
    assert run_extraction.api_counts(entries) == {"types": 2, "functions": 2, "methods": 3}
    assert run_extraction.api_counts([]) == {"types": 0, "functions": 0, "methods": 0}


def test_extract_pinned_repository_records_the_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_clone(repository: str, dest: Path, *, commit: str | None = None) -> str:
        for source in GO_SAMPLE.glob("*.go"):
            shutil.copy(source, dest / source.name)
        return "b" * 40

    monkeypatch.setattr(run_extraction, "clone_shallow", fake_clone)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("platform: go\nfamily: pdf\nrepository: acme/pdf-go\n", encoding="utf-8")
    artifact = run_extraction.extract_pinned_repository(manifest)
    assert artifact["language"] == "go"
    assert artifact["type_count"] == len(artifact["types"]) == 10
    assert artifact["counts"] == {"types": 5, "functions": 5, "methods": 3}
