"""TC-510: ``infra/ingest.py`` hands the production publish policy to the publisher.

A green unit test of the policy proves nothing if the entrypoint never passes it, so these tests
run the REAL ``ingest.main()`` against a REAL ``GenerationManifestStore`` in ``tmp_path`` and read
the result back: a two-symbol generation is refused by default, the floors move only with the
explicit flags, a deliberate shrink publishes only with an exactly matching ``--accept-regression``
plus a reason (flag or the two environment variables a Kubernetes Job can receive), and neither the
chart's ingestion Job nor docker-compose.yml passes any of those flags. Offline, deterministic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore, PublishSafetyError

REPO_ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_PROVIDER_SPEC = "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"
ENV_ACCEPT = "FOSS_MCP_ACCEPT_REGRESSION"
ENV_REASON = "FOSS_MCP_ACCEPT_REGRESSION_REASON"

_PROVENANCE = {"repository": "org/repo-a", "commit": "c" * 40, "path": "api_surface.json"}
_TEXT = {
    "symbol": "FQN: Sym{i}\nKind: type_spec\nA documented type number {i}.",
    "doc": "FQN: Doc: Guide{i}\nA guide section number {i}.",
    "example": "FQN: Example: Demo{i}\nAn example number {i}.",
}


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ACCEPT, raising=False)
    monkeypatch.delenv(ENV_REASON, raising=False)


def _entries(symbols: int, docs: int, examples: int) -> list[dict]:
    entries = []
    for kind, number in (("symbol", symbols), ("doc", docs), ("example", examples)):
        for i in range(number):
            entries.append(
                {
                    "section_title": f"{kind}-{i}",
                    "text": _TEXT[kind].format(i=i),
                    "source_kind": "self_extracted",
                    "content_type": "api_surface",
                    "provenance": dict(_PROVENANCE),
                    "trust_tier": "highest",
                }
            )
    return entries


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    entries: list[dict],
    *extra: str,
    held_by: str | None = None,
) -> None:
    chunks_path = tmp_path / "chunks.json"
    chunks_path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")
    argv = [
        "ingest.py",
        "--family",
        "pdf",
        "--platform",
        "net",
        "--source-kind",
        "self_extracted",
        "--chunks",
        str(chunks_path),
        "--manifest-store",
        str(tmp_path / "manifests"),
        "--embedding-provider",
        EMBEDDING_PROVIDER_SPEC,
    ]
    if held_by is not None:
        argv += ["--held-by", held_by]
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    ingest.main()


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def test_cli_refuses_a_two_symbol_generation_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(PublishSafetyError, match="symbol"):
        _run(monkeypatch, tmp_path, _entries(2, 5, 1))
    store = _store(tmp_path)
    assert store.read_active(SCOPE) is None
    assert [entry["event"] for entry in store.read_publish_log(SCOPE)] == ["refused"]


def test_cli_floors_can_be_lowered_only_by_the_explicit_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(PublishSafetyError):
        _run(monkeypatch, tmp_path, _entries(2, 0, 0), "--min-symbols", "2")  # docs floor still 1
    assert _store(tmp_path).read_active(SCOPE) is None
    _run(monkeypatch, tmp_path, _entries(2, 0, 0), "--min-symbols", "2", "--min-docs", "0")
    store = _store(tmp_path)
    active = store.read_active(SCOPE)
    assert active is not None
    assert store.read_publish_log(SCOPE)[-1]["floors"] == {"symbols": 2, "docs": 0}


def _publish_153(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    _run(monkeypatch, tmp_path, _entries(100, 40, 13))
    active = _store(tmp_path).read_active(SCOPE)
    assert active is not None
    return active


_SHRINK_FLAGS = (
    "--accept-regression",
    "symbols:100->46",
    "--accept-regression",
    "docs:40->10",
    "--accept-regression",
    "examples:13->2",
    "--accept-regression",
    "total:153->58",
)


def test_cli_accept_regression_publishes_a_153_to_58_shrink_and_logs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = _publish_153(monkeypatch, tmp_path)
    with pytest.raises(PublishSafetyError) as refused:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2))
    assert "--accept-regression total:153->58" in str(refused.value)
    assert _store(tmp_path).read_active(SCOPE) == before

    _run(
        monkeypatch,
        tmp_path,
        _entries(46, 10, 2),
        *_SHRINK_FLAGS,
        "--reason",
        "upstream removed the vendored dir",
    )
    store = _store(tmp_path)
    after = store.read_active(SCOPE)
    assert after is not None and after != before
    entry = store.read_publish_log(SCOPE)[-1]
    assert entry["event"] == "published"
    assert entry["generation_id"] == after
    assert entry["overrides"] == ["symbols:100->46", "docs:40->10", "examples:13->2", "total:153->58"]
    assert entry["reason"] == "upstream removed the vendored dir"
    assert entry["stats"]["total"] == 58


def test_cli_accept_regression_requires_a_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before = _publish_153(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2), *_SHRINK_FLAGS)
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit) as blank:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2), *_SHRINK_FLAGS, "--reason", "   ")
    assert blank.value.code == 2
    assert _store(tmp_path).read_active(SCOPE) == before


def test_cli_accept_regression_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = _publish_153(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_ACCEPT, "symbols:100->46; docs:40->10;examples:13->2;total:153->58")
    monkeypatch.setenv(ENV_REASON, "chart value ingestion.acceptRegression")
    _run(monkeypatch, tmp_path, _entries(46, 10, 2))
    store = _store(tmp_path)
    assert store.read_active(SCOPE) != before
    entry = store.read_publish_log(SCOPE)[-1]
    assert entry["overrides"] == ["symbols:100->46", "docs:40->10", "examples:13->2", "total:153->58"]
    assert entry["reason"] == "chart value ingestion.acceptRegression"


def test_cli_environment_override_without_a_reason_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = _publish_153(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_ACCEPT, "symbols:100->46;docs:40->10;examples:13->2;total:153->58")
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2))
    assert exit_info.value.code == 2
    monkeypatch.setenv(ENV_REASON, "  ")
    with pytest.raises(SystemExit) as blank:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2))
    assert blank.value.code == 2
    assert _store(tmp_path).read_active(SCOPE) == before


def test_cli_a_malformed_environment_override_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _publish_153(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_ACCEPT, "symbols:100->46;colour:1->2")
    monkeypatch.setenv(ENV_REASON, "because")
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, tmp_path, _entries(46, 10, 2))
    assert exit_info.value.code == 2


def test_cli_flag_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _publish_153(monkeypatch, tmp_path)
    # The environment names numbers that do not match: used alone it would be refused.
    monkeypatch.setenv(ENV_ACCEPT, "symbols:100->45;total:153->57")
    monkeypatch.setenv(ENV_REASON, "reason from the environment")
    _run(monkeypatch, tmp_path, _entries(46, 10, 2), *_SHRINK_FLAGS, "--reason", "reason from the flag")
    entry = _store(tmp_path).read_publish_log(SCOPE)[-1]
    assert entry["event"] == "published"
    assert entry["reason"] == "reason from the flag"
    assert "symbols:100->45" not in entry["overrides"]


@pytest.mark.parametrize(
    "value", ["symbols:1-2", "colour:1->2", "symbols:a->2", "symbols:1->", "symbols:-1->2"]
)
def test_cli_rejects_malformed_accept_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, tmp_path, _entries(5, 3, 1), "--accept-regression", value, "--reason", "because")
    assert exit_info.value.code == 2
    assert _store(tmp_path).read_active(SCOPE) is None


def test_cli_override_does_not_waive_the_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(PublishSafetyError):
        _run(
            monkeypatch,
            tmp_path,
            _entries(2, 5, 1),
            "--accept-regression",
            "symbols:0->2",
            "--reason",
            "trying to waive the floor",
        )
    assert _store(tmp_path).read_active(SCOPE) is None


def test_cli_passes_a_policy_to_publish_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = (REPO_ROOT / "infra" / "ingest.py").read_text(encoding="utf-8")
    assert "policy=policy" in source
    assert "PublishPolicy(" in source
    # and the behaviour that call site buys: the production floor refuses a two-symbol generation
    with pytest.raises(PublishSafetyError):
        _run(monkeypatch, tmp_path, _entries(2, 3, 0))
    assert _store(tmp_path).read_active(SCOPE) is None
    _run(monkeypatch, tmp_path, _entries(3, 1, 0))
    assert _store(tmp_path).read_active(SCOPE) is not None


def test_cli_records_the_holder_in_the_publish_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run(monkeypatch, tmp_path, _entries(5, 3, 1), held_by="ingestion-job-7")
    assert _store(tmp_path).read_publish_log(SCOPE)[-1]["held_by"] == "ingestion-job-7"


def test_chart_and_compose_do_not_pass_floor_flags() -> None:
    for relative in ("infra/helm/foss-mcp/templates/ingestion-job.yaml", "docker-compose.yml"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for flag in ("--min-symbols", "--min-docs", "--accept-regression"):
            assert flag not in text, f"{relative} must not pass {flag}"


def test_cli_still_refuses_empty_and_halving_generations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(PublishSafetyError, match="empty generation"):
        _run(monkeypatch, tmp_path, [])
    assert _store(tmp_path).read_active(SCOPE) is None
    _run(monkeypatch, tmp_path, _entries(60, 40, 0))
    before = _store(tmp_path).read_active(SCOPE)
    with pytest.raises(PublishSafetyError):
        _run(monkeypatch, tmp_path, _entries(30, 19, 0))  # 49 of 100
    assert _store(tmp_path).read_active(SCOPE) == before


def test_deployment_md_documents_the_shrink_override() -> None:
    text = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    heading = text.index("## Accepting an intentional shrink")
    assert heading < text.index("## Troubleshooting")
    section = text[heading : text.index("## Troubleshooting")]
    for needle in ("--accept-regression", "--reason", "FOSS_MCP_ACCEPT_REGRESSION", "publish_log.jsonl"):
        assert needle in section, needle
    assert "install_pilots.py" not in section
