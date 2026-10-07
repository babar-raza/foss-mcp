"""infra.fetch_product_reference's first test coverage (TC-145).

Before this card, ``foss_mcp.extraction.manifest_reader.fetch_manifest_file`` and
``foss_mcp.extraction.repo_native_reader.read_repo_document`` were real, already-implemented
Contents-API readers with zero production callers - ``fetch_manifest_file`` had zero test
coverage anywhere in the repo too. These tests exercise ``infra/fetch_product_reference.py`` as
their first real production caller: ``build_sidecar`` covers (a) a present manifest with both
governance documents present, (b) ``manifest_path`` omitted (``fetch_manifest_file`` never
called at all), and (c) a real 404 on CONTRIBUTING.md producing the honest not_present shape.

The network is mocked exactly the way ``tests/extraction/test_repo_native.py`` already does -
``monkeypatch.setattr(reader.urllib.request, 'urlopen', fake_urlopen)`` - applied to both
``manifest_reader.urllib.request`` and ``repo_native_reader.urllib.request`` (two separate
monkeypatch targets, since the card calls them out as different modules). Both attribute paths
resolve to the literal same ``urllib.request`` module object at runtime, so a single fake
dispatches on the request URL's path rather than call order, and is installed at both targets.
"""

from __future__ import annotations

import json
import urllib.error
from base64 import b64encode
from pathlib import Path

import foss_mcp.extraction.manifest_reader as manifest_reader
import foss_mcp.extraction.repo_native_reader as repo_native_reader
import infra.fetch_product_reference as fetch_product_reference

REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _contents_payload(*, path: str, sha: str, content: str) -> bytes:
    encoded = b64encode(content.encode("utf-8")).decode("ascii")
    return json.dumps(
        {"path": path, "sha": sha, "size": len(content.encode("utf-8")), "content": encoded}
    ).encode("utf-8")


def _install_fake_urlopen(monkeypatch, responses: dict) -> None:
    """One fake, routed by the Contents-API path at the end of the request URL, installed at
    both ``manifest_reader.urllib.request`` and ``repo_native_reader.urllib.request``. Both
    names resolve to the same real ``urllib.request`` module object, so a second, independent
    monkeypatch of the same attribute would simply overwrite the first rather than compose with
    it - routing by path (not by patch target or call order) is what lets one fake correctly
    serve both readers in the same test.

    ``responses`` maps the Contents-API path (e.g. ``"CONTRIBUTING.md"``) to either ``bytes``
    (a real 200 response body) or an ``int`` (an HTTP status code to raise, e.g. 404).
    """

    def fake_urlopen(request, timeout=30):
        path = request.full_url.rsplit("/contents/", 1)[1].split("?", 1)[0]
        outcome = responses[path]
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(request.full_url, outcome, "Not Found", {}, None)
        return _FakeResponse(outcome)

    monkeypatch.setattr(manifest_reader.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(repo_native_reader.urllib.request, "urlopen", fake_urlopen)


def test_build_sidecar_with_a_present_manifest_and_both_documents_present(monkeypatch) -> None:
    manifest_text = "<Project><TargetFramework>net8.0</TargetFramework></Project>"
    _install_fake_urlopen(
        monkeypatch,
        {
            "Aspose.PDF.FOSS.csproj": _contents_payload(
                path="Aspose.PDF.FOSS.csproj", sha="m" * 40, content=manifest_text
            ),
            "CONTRIBUTING.md": _contents_payload(
                path="CONTRIBUTING.md", sha="c" * 40, content="# Contributing\n"
            ),
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    sidecar = fetch_product_reference.build_sidecar(
        repository=REPOSITORY,
        platform="net",
        manifest_path="Aspose.PDF.FOSS.csproj",
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )

    assert sidecar["manifest_text"] == manifest_text
    assert sidecar["platform"] == "net"
    assert sidecar["contributing"] == {
        "status": "present",
        "path": "CONTRIBUTING.md",
        "sha": "c" * 40,
        "size": len(b"# Contributing\n"),
        "content": "# Contributing\n",
    }
    assert sidecar["agent_guidance"] == {
        "status": "present",
        "path": "AGENTS.md",
        "sha": "a" * 40,
        "size": len(b"# Agents\n"),
        "content": "# Agents\n",
    }


def test_build_sidecar_with_manifest_path_omitted_never_calls_fetch_manifest_file(monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("fetch_manifest_file must not be called when manifest_path is omitted")

    monkeypatch.setattr(fetch_product_reference, "fetch_manifest_file", _must_not_be_called)
    _install_fake_urlopen(
        monkeypatch,
        {
            "CONTRIBUTING.md": _contents_payload(
                path="CONTRIBUTING.md", sha="c" * 40, content="# Contributing\n"
            ),
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    sidecar = fetch_product_reference.build_sidecar(
        repository=REPOSITORY,
        platform="net",
        manifest_path=None,
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )

    assert sidecar["manifest_text"] is None
    assert sidecar["contributing"]["status"] == "present"
    assert sidecar["agent_guidance"]["status"] == "present"


def test_build_sidecar_reports_a_real_404_on_contributing_as_not_present(monkeypatch) -> None:
    _install_fake_urlopen(
        monkeypatch,
        {
            "CONTRIBUTING.md": 404,
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    sidecar = fetch_product_reference.build_sidecar(
        repository=REPOSITORY,
        platform="net",
        manifest_path=None,
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )

    assert sidecar["contributing"] == {"status": "not_present", "path": "CONTRIBUTING.md"}
    assert sidecar["agent_guidance"]["status"] == "present"


def test_main_writes_the_real_sidecar_json_matching_build_sidecars_return_value(
    tmp_path: Path, monkeypatch
) -> None:
    """Runs the real CLI's main() against a tmp_path output file and asserts the written JSON
    file's real content matches build_sidecar's own return value - not merely that the process
    exits 0."""
    import sys

    _install_fake_urlopen(
        monkeypatch,
        {
            "CONTRIBUTING.md": _contents_payload(
                path="CONTRIBUTING.md", sha="c" * 40, content="# Contributing\n"
            ),
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    output_path = tmp_path / "product_reference.json"
    argv = [
        "fetch_product_reference.py",
        "--repository",
        REPOSITORY,
        "--platform",
        "net",
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    fetch_product_reference.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    expected = fetch_product_reference.build_sidecar(
        repository=REPOSITORY,
        platform="net",
        manifest_path=None,
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )
    assert written == expected
    assert written["contributing"]["content"] == "# Contributing\n"
    assert written["agent_guidance"]["content"] == "# Agents\n"


def test_main_prints_a_one_line_confirmation(tmp_path: Path, monkeypatch, capsys) -> None:
    import sys

    _install_fake_urlopen(
        monkeypatch,
        {
            "CONTRIBUTING.md": _contents_payload(
                path="CONTRIBUTING.md", sha="c" * 40, content="# Contributing\n"
            ),
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    output_path = tmp_path / "product_reference.json"
    argv = [
        "fetch_product_reference.py",
        "--repository",
        REPOSITORY,
        "--platform",
        "net",
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    fetch_product_reference.main()

    out = capsys.readouterr().out
    assert out.strip() == f"wrote product reference inputs for {REPOSITORY} (net) -> {output_path}"


_OLD_REF = "a" * 40
_NEW_REF = "b" * 40


def _must_not_be_called(*args, **kwargs):
    raise AssertionError(
        "fetch_manifest_file/read_repo_document must not be called when the pin is unchanged"
    )


def test_main_skips_the_live_fetch_when_the_existing_sidecar_records_the_identical_pin(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """TC-243: a re-run whose --repository/--ref exactly match what the existing sidecar already
    records makes zero network calls - fetch_manifest_file and read_repo_document are mocked to
    raise if called at all, never merely to return a mocked value."""
    import sys

    output_path = tmp_path / "product_reference.json"
    existing = {
        "manifest_text": None,
        "platform": "net",
        "contributing": {
            "status": "present",
            "path": "CONTRIBUTING.md",
            "sha": "c" * 40,
            "size": 15,
            "content": "# Contributing\n",
        },
        "agent_guidance": {
            "status": "present",
            "path": "AGENTS.md",
            "sha": "a" * 40,
            "size": 9,
            "content": "# Agents\n",
        },
        "source_repository": REPOSITORY,
        "source_ref": _OLD_REF,
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    monkeypatch.setattr(fetch_product_reference, "fetch_manifest_file", _must_not_be_called)
    monkeypatch.setattr(fetch_product_reference, "read_repo_document", _must_not_be_called)

    argv = [
        "fetch_product_reference.py",
        "--repository",
        REPOSITORY,
        "--platform",
        "net",
        "--ref",
        _OLD_REF,
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    fetch_product_reference.main()

    out = capsys.readouterr().out
    assert "skip" in out.lower()
    assert REPOSITORY in out
    assert _OLD_REF in out
    # The file on disk is untouched - the skip returns before any write.
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_main_fetches_live_when_the_existing_sidecars_ref_differs_from_a_new_pin(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """TC-243: a real re-pin to a new commit (repository unchanged, ref different) must not be
    mistaken for an unchanged pin - the live fetch runs exactly as it always did."""
    import sys

    calls: list[tuple[str, ...]] = []

    def _fake_fetch_manifest_file(repository, path, *, ref=None):
        calls.append(("fetch_manifest_file", repository, path, ref))
        return "<Project><TargetFramework>net8.0</TargetFramework></Project>"

    def _fake_read_repo_document(repository, path, *, ref=None):
        calls.append(("read_repo_document", repository, path, ref))
        return repo_native_reader.DocumentPresent(path=path, sha="f" * 40, size=1, content="fresh")

    output_path = tmp_path / "product_reference.json"
    output_path.write_text(
        json.dumps(
            {
                "manifest_text": "<Project><TargetFramework>net7.0</TargetFramework></Project>",
                "platform": "net",
                "contributing": {
                    "status": "present",
                    "path": "CONTRIBUTING.md",
                    "sha": "c" * 40,
                    "size": 15,
                    "content": "# Contributing\n",
                },
                "agent_guidance": {
                    "status": "present",
                    "path": "AGENTS.md",
                    "sha": "a" * 40,
                    "size": 9,
                    "content": "# Agents\n",
                },
                "source_repository": REPOSITORY,
                "source_ref": _OLD_REF,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(fetch_product_reference, "fetch_manifest_file", _fake_fetch_manifest_file)
    monkeypatch.setattr(fetch_product_reference, "read_repo_document", _fake_read_repo_document)

    argv = [
        "fetch_product_reference.py",
        "--repository",
        REPOSITORY,
        "--platform",
        "net",
        "--manifest-path",
        "Aspose.PDF.FOSS.csproj",
        "--ref",
        _NEW_REF,
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    fetch_product_reference.main()

    assert [call[0] for call in calls] == [
        "fetch_manifest_file",
        "read_repo_document",
        "read_repo_document",
    ]
    assert all(call[3] == _NEW_REF for call in calls)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["source_repository"] == REPOSITORY
    assert written["source_ref"] == _NEW_REF
    assert written["manifest_text"] == "<Project><TargetFramework>net8.0</TargetFramework></Project>"
    assert written["contributing"]["content"] == "fresh"
    assert written["agent_guidance"]["content"] == "fresh"


def test_main_treats_a_malformed_existing_sidecar_as_absent_and_fetches_live(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """TC-243: a corrupt/unparseable existing --output file must never raise and must never
    block ingestion - it is treated exactly like an absent file, and main() fetches live and
    overwrites it with a correct, freshly-fetched sidecar."""
    import sys

    calls: list[tuple[str, ...]] = []

    def _fake_read_repo_document(repository, path, *, ref=None):
        calls.append(("read_repo_document", repository, path, ref))
        if path == "CONTRIBUTING.md":
            return repo_native_reader.DocumentPresent(
                path=path, sha="c" * 40, size=15, content="# Contributing\n"
            )
        return repo_native_reader.DocumentPresent(path=path, sha="a" * 40, size=9, content="# Agents\n")

    output_path = tmp_path / "product_reference.json"
    output_path.write_text("{this is not valid json at all", encoding="utf-8")

    monkeypatch.setattr(fetch_product_reference, "fetch_manifest_file", _must_not_be_called)
    monkeypatch.setattr(fetch_product_reference, "read_repo_document", _fake_read_repo_document)

    argv = [
        "fetch_product_reference.py",
        "--repository",
        REPOSITORY,
        "--platform",
        "net",
        "--ref",
        _NEW_REF,
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    fetch_product_reference.main()

    assert len(calls) == 2
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["contributing"] == {
        "status": "present",
        "path": "CONTRIBUTING.md",
        "sha": "c" * 40,
        "size": 15,
        "content": "# Contributing\n",
    }
    assert written["agent_guidance"] == {
        "status": "present",
        "path": "AGENTS.md",
        "sha": "a" * 40,
        "size": 9,
        "content": "# Agents\n",
    }
    assert written["source_repository"] == REPOSITORY
    assert written["source_ref"] == _NEW_REF
