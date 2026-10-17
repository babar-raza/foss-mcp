"""Offline tests for TC-454: the Rust verifier assembles README blocks the way a reader would.

A README Rust block is usually a statement fragment, not a program. The verifier compiles a block
that has its own ``fn main`` exactly as given; wraps any other block in a ``Result``-returning
``main`` so ``?`` works; adds, at crate level, only the page ``use`` statements the block names;
declares ``serde_json`` only when the source names it; rejects a block that is only ``use``
declarations; bounds the cargo subprocess; and records every assembly step in the output.

Nothing here runs a real toolchain: ``_run_cargo`` is replaced by a fake with the real
``(args, *, cwd)`` signature that snapshots ``src/main.rs`` and ``Cargo.toml`` at call time. The
page is a small synthetic furnished page that keeps the block shapes of a real README (re-pinned by TC-551: the
tests read a removed pilot's committed page before): a quick start that carries its own ``use``, a fragment that
needs that ``use`` from the page, and a block that declares its own ``use`` of other items and names ``serde_json``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample, extract_candidate_examples

CRATE = "sample-client-foss"
HEADER = "fn main() -> Result<(), Box<dyn std::error::Error>> {"
OWN_USE = "use sample_client_foss::{SampleClient, ClientOptions};"

_QUICK_START = """```rust
use sample_client_foss::{SampleClient, ClientOptions};

let options = ClientOptions {
    session_url: "https://sample.example.test/.well-known/session".to_string(),
    username: "user@example.test".to_string(),
    password: "secret".to_string(),
    bearer_token: None,
};
let mut client = SampleClient::new(options);
let session = client.connect()?;
```
"""
_BEARER = """Set `bearer_token` to authenticate every request with `Authorization: Bearer <token>` instead of HTTP Basic:

```rust
let options = ClientOptions {
    session_url: "https://sample.example.test/.well-known/session".to_string(),
    username: String::new(),
    password: String::new(),
    bearer_token: Some("my-oauth-access-token".to_string()),
};
let mut client = SampleClient::new(options);
let session = client.connect()?;
```
"""
_BATCH = """Several calls can be batched into one round trip, chaining a later call to an earlier one's result:

```rust
use sample_client_foss::{Invocation, ResultReference};
use serde_json::json;
use std::collections::HashMap;

let query = Invocation {
    name: "Item/query".to_string(),
    arguments: HashMap::from([("accountId".to_string(), json!(account_id))]),
    method_call_id: "c1".to_string(),
};
let get = Invocation {
    name: "Item/get".to_string(),
    arguments: HashMap::from([
        ("accountId".to_string(), json!(account_id)),
        (
            "#ids".to_string(),
            serde_json::to_value(ResultReference {
                result_of: "c1".to_string(),
                name: "Item/query".to_string(),
                path: "/ids".to_string(),
            })
            .unwrap(),
        ),
    ]),
    method_call_id: "c2".to_string(),
};
let resp = client.send_request(vec![query, get], vec!["urn:example:items".to_string()])?;
```
"""
PAGE: dict[str, object] = {
    "single": {
        "block": [
            {"title": "Quick Start", "content": _QUICK_START},
            {"title": "Bearer token authentication", "content": _BEARER},
            {"title": "Bearer token authentication (2)", "content": _BATCH},
        ]
    }
}


def _candidates() -> list[CandidateExample]:
    return extract_candidate_examples(PAGE)


def _sample(title: str) -> CandidateExample:
    matches = [candidate for candidate in _candidates() if candidate.title == title]
    assert len(matches) == 1, f"expected exactly one candidate titled {title!r}"
    return matches[0]


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["cargo", "build"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FakeCargo:
    """A replacement for ``example_verifier._run_cargo`` that snapshots the crate at call time."""

    def __init__(self, result: subprocess.CompletedProcess[str] | None = None) -> None:
        self.result = result if result is not None else _completed()
        self.calls: list[list[str]] = []
        self.main_rs: list[str] = []
        self.cargo_toml: list[str] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        self.main_rs.append((cwd / "src" / "main.rs").read_text(encoding="utf-8"))
        self.cargo_toml.append((cwd / "Cargo.toml").read_text(encoding="utf-8"))
        return self.result


def _verify(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidate: CandidateExample,
    cargo: FakeCargo | None = None,
) -> tuple[ev.VerificationResult, FakeCargo]:
    fake = cargo if cargo is not None else FakeCargo()
    monkeypatch.setattr(ev, "_run_cargo", fake)
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    result = ev.verify_rust_example(
        candidate, library_crate_name=CRATE, library_dir=library, workdir=tmp_path / "work"
    )
    return result, fake


def _block(code: str, page_code: tuple[str, ...] = ()) -> CandidateExample:
    return CandidateExample(title="t", description="d", language="rust", code=code, page_code=page_code)


def test_quick_start_is_wrapped_with_its_own_use_inside_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Quick Start")

    result, cargo = _verify(monkeypatch, tmp_path, candidate)

    source = cargo.main_rs[0]
    assert source.startswith(f"{HEADER}\n{OWN_USE}\n")
    assert source.rstrip("\n").endswith("    Ok(())\n}")
    assert source.count("use ") == 1
    assert "serde_json" not in cargo.cargo_toml[0]
    assert result.output.startswith(
        "[foss-mcp] assembly: rust mode=wrapped-fragment; page-uses-added=none; extra-dependencies=none\n"
    )


def test_bearer_token_block_gets_the_page_use_at_crate_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Bearer token authentication")

    result, cargo = _verify(monkeypatch, tmp_path, candidate)

    source = cargo.main_rs[0]
    assert source.startswith(f"{OWN_USE}\n{HEADER}\n")
    assert source.index(OWN_USE) < source.index(HEADER)
    assert "serde_json" not in cargo.cargo_toml[0]
    assert f"page-uses-added={OWN_USE}; extra-dependencies=none" in result.output.splitlines()[0]


def test_serde_json_is_declared_at_default_requirement_without_a_library_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Bearer token authentication (2)")

    result, cargo = _verify(monkeypatch, tmp_path, candidate)

    assert 'serde_json = "1"' in cargo.cargo_toml[0].splitlines()
    assert result.output.splitlines()[0].endswith('extra-dependencies=serde_json = "1"')


def test_serde_json_uses_the_library_own_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "Cargo.toml").write_text(
        '[package]\nname = "x"\n\n[dependencies]\nserde_json = { version = "1.0.100", features = ["std"] }\n',
        encoding="utf-8",
    )

    _, cargo = _verify(monkeypatch, tmp_path, _sample("Bearer token authentication (2)"))

    assert 'serde_json = "1.0.100"' in cargo.cargo_toml[0].splitlines()


def test_third_block_keeps_its_own_uses_and_adds_no_page_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Bearer token authentication (2)")

    result, cargo = _verify(monkeypatch, tmp_path, candidate)

    assert cargo.main_rs[0].startswith(f"{HEADER}\nuse sample_client_foss::{{Invocation, ResultReference}};")
    assert "page-uses-added=none;" in result.output.splitlines()[0]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("use a::{B, C as D};", {"B", "D"}),
        ("use a::{b::{C, D}, E as F};", {"C", "D", "F"}),
        ("use a::{self, B};", {"a", "B"}),
        ("use a::B;", {"B"}),
        ("pub use a::b::C;", {"C"}),
        ("use a::\n    {B,\n     C};", {"B", "C"}),
    ],
)
def test_use_names(statement: str, expected: set[str]) -> None:
    assert ev._rust_use_names(statement) == frozenset(expected)


def test_use_names_of_a_glob_is_none() -> None:
    assert ev._rust_use_names("use a::b::*;") is None
    assert ev._rust_use_names("use a::{B, c::*};") is None


def test_use_statements_span_lines_and_keep_order() -> None:
    code = "use a::B;\npub use c::{\n    D,\n    E,\n};\nlet x = 1;\n"

    assert ev._rust_use_statements(code) == ["use a::B;", "pub use c::{\n    D,\n    E,\n};"]


def test_a_complete_program_is_written_byte_for_byte(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code = "fn main() {}"

    result, cargo = _verify(monkeypatch, tmp_path, _block(code, page_code=("use a::B;",)))

    assert cargo.main_rs == [code]
    assert "serde_json" not in cargo.cargo_toml[0]
    assert result.output.startswith(
        "[foss-mcp] assembly: rust mode=as-given; page-uses-added=none; extra-dependencies=none\n"
    )


def test_a_use_only_block_is_rejected_without_calling_cargo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _block("use a::B;\n// nothing else\n#![allow(dead_code)]\n")

    result, cargo = _verify(monkeypatch, tmp_path, candidate)

    assert cargo.calls == []
    assert result.verified is False
    assert result.output == ev._NO_STATEMENTS_MESSAGE.format(kind="use declarations")


def test_an_inner_attribute_moves_to_the_first_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    candidate = _block("let x = 1;\n#![allow(dead_code)]\nlet y = 2;")

    _, cargo = _verify(monkeypatch, tmp_path, candidate)

    lines = cargo.main_rs[0].splitlines()
    assert lines[0] == "#![allow(dead_code)]"
    assert lines[1] == HEADER
    assert cargo.main_rs[0].count("#![allow(dead_code)]") == 1


def test_a_glob_page_use_is_always_added_and_an_unused_name_is_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = ("use a::*;", "use b::Unused;", "use c::Used;")

    _, cargo = _verify(monkeypatch, tmp_path, _block("let v = Used::new();", page))

    source = cargo.main_rs[0]
    assert source.startswith(f"use a::*;\nuse c::Used;\n{HEADER}\n")
    assert "Unused" not in source


def test_the_end_of_a_long_output_keeps_the_real_rustc_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    error = "error[E0425]: cannot find value 'account_id' in this scope"
    stderr = "   Compiling lib v0.1.0\n" * 1000 + error + "\n"
    assert len(stderr) > 20000
    candidate = _sample("Bearer token authentication (2)")

    result, _ = _verify(monkeypatch, tmp_path, candidate, FakeCargo(_completed(101, stderr=stderr)))

    assert result.verified is False
    assert error in result.output
    assert result.candidate is candidate


def test_a_network_failure_without_a_diagnostic_raises_the_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cargo = FakeCargo(_completed(101, stderr="warning: spurious network error (3 tries remaining)"))

    with pytest.raises(ev.ExampleEnvironmentError):
        _verify(monkeypatch, tmp_path, _sample("Quick Start"), cargo)


def test_a_passing_build_is_verified_and_returns_the_original_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _sample("Quick Start")

    result, _ = _verify(monkeypatch, tmp_path, candidate)

    assert result.verified is True
    assert result.candidate is candidate


def test_cargo_timeout_returns_exit_124_and_the_timeout_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args, 1800, output=b"partial", stderr=None)

    monkeypatch.setattr(ev.subprocess, "run", fake_run)

    result = ev._run_cargo_subprocess(["cargo", "build"], cwd=tmp_path)

    assert seen["timeout"] == 1800
    assert result.returncode == 124
    assert result.stdout == "partial"
    assert result.stderr.splitlines()[-1] == "[foss-mcp] TIMEOUT: cargo was killed after 1800 seconds"
    assert ev._match_network_failure_marker(f"{result.stdout}\n{result.stderr}") is None


def test_wiring_the_platform_dispatch_uses_the_assembling_verifier() -> None:
    import inspect

    assert build_chunks._PLATFORM_DISPATCH["rust"]["verify"] is ev.verify_rust_example
    assert "candidate.page_code" in inspect.getsource(ev.verify_rust_example)
