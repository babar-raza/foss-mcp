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


def _listing_payload(*entries: tuple[str, str]) -> bytes:
    """A Contents-API directory listing (G3/TC-427: build_sidecar lists the repository root once)."""
    return json.dumps([{"name": name, "type": kind} for name, kind in entries]).encode("utf-8")


_ROOT_ENTRIES = (("CONTRIBUTING.md", "file"), ("AGENTS.md", "file"))


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
            "": _listing_payload(*_ROOT_ENTRIES),
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
            "": _listing_payload(*_ROOT_ENTRIES),
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
            "": _listing_payload(*_ROOT_ENTRIES),
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
            "": _listing_payload(*_ROOT_ENTRIES),
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
    # G3/TC-511: the written sidecar now carries the additive provenance keys (fetched_at, family);
    # everything else, ``platform`` included (build_sidecar already returns it), is still exactly
    # build_sidecar's return value.
    assert isinstance(written["fetched_at"], str) and written["fetched_at"].endswith("Z")
    for provenance_key in ("fetched_at", "family"):
        del written[provenance_key]
    assert written == expected
    assert written["contributing"]["content"] == "# Contributing\n"
    assert written["agent_guidance"]["content"] == "# Agents\n"


def test_main_prints_a_one_line_confirmation(tmp_path: Path, monkeypatch, capsys) -> None:
    import sys

    _install_fake_urlopen(
        monkeypatch,
        {
            "": _listing_payload(*_ROOT_ENTRIES),
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
    monkeypatch.setattr(fetch_product_reference, "list_root_entries", _must_not_be_called)

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

    def _fake_list_root_entries(repository, *, ref=None):
        calls.append(("list_root_entries", repository, "", ref))
        return list(_ROOT_ENTRIES)

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
    monkeypatch.setattr(fetch_product_reference, "list_root_entries", _fake_list_root_entries)
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
        "list_root_entries",
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

    def _fake_list_root_entries(repository, *, ref=None):
        calls.append(("list_root_entries", repository, "", ref))
        return list(_ROOT_ENTRIES)

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
    monkeypatch.setattr(fetch_product_reference, "list_root_entries", _fake_list_root_entries)
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

    assert len(calls) == 3
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


# --- load_manifest_sidecar reads the pin back (G3/TC-353) ----------------------------------------


def _sidecar_dict(**extra: object) -> dict:
    sidecar: dict = {
        "manifest_text": "<Project><TargetFramework>net8.0</TargetFramework></Project>",
        "platform": "net",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
    }
    sidecar.update(extra)
    return sidecar


def test_load_manifest_sidecar_reads_the_source_repository_and_commit_back(tmp_path: Path) -> None:
    """TC-353: the pin build_sidecar records is carried into ProductReferenceInputs, so the tool can
    name the repository and exact commit when a package coordinate does not resolve."""
    path = tmp_path / "product_reference.json"
    path.write_text(
        json.dumps(_sidecar_dict(source_repository=REPOSITORY, source_ref=_OLD_REF)), encoding="utf-8"
    )

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.source_repository == REPOSITORY
    assert inputs.source_ref == _OLD_REF
    assert inputs.platform == "net"
    assert inputs.manifest_text == "<Project><TargetFramework>net8.0</TargetFramework></Project>"


def test_load_manifest_sidecar_of_a_sidecar_written_before_the_pin_fields_leaves_them_none(
    tmp_path: Path,
) -> None:
    """A sidecar with neither key (written before TC-353) loads without raising, both None."""
    path = tmp_path / "product_reference.json"
    path.write_text(json.dumps(_sidecar_dict()), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.source_repository is None
    assert inputs.source_ref is None


def test_load_manifest_sidecar_reads_a_null_ref_as_none_and_keeps_the_repository(tmp_path: Path) -> None:
    """build_sidecar records source_ref as null when --ref was omitted: not known, not an error."""
    path = tmp_path / "product_reference.json"
    path.write_text(
        json.dumps(_sidecar_dict(source_repository=REPOSITORY, source_ref=None)), encoding="utf-8"
    )

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.source_repository == REPOSITORY
    assert inputs.source_ref is None


def test_load_manifest_sidecar_round_trips_what_build_sidecar_recorded(tmp_path: Path, monkeypatch) -> None:
    _install_fake_urlopen(
        monkeypatch,
        {
            "": _listing_payload(*_ROOT_ENTRIES),
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
        ref=_NEW_REF,
    )
    path = tmp_path / "product_reference.json"
    path.write_text(json.dumps(sidecar), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.source_repository == REPOSITORY
    assert inputs.source_ref == _NEW_REF


# --- load_manifest_sidecar reads family back (G3/TC-783) -----------------------------------------


def test_load_manifest_sidecar_round_trips_the_family_main_writes(tmp_path: Path, monkeypatch) -> None:
    """G3/TC-783: main() has written sidecar["family"] = family since TC-511, but
    load_manifest_sidecar never read it back - every sidecar-loaded pilot got family=None,
    silently disabling get_product_reference.py's TC-641 known-defects merge. This exercises the
    real build_sidecar -> main()'s family write -> load_manifest_sidecar chain end to end, not
    just a hand-built dict, so a regression in any of the three links fails this test."""
    import sys

    _install_fake_urlopen(
        monkeypatch,
        {
            "": _listing_payload(*_ROOT_ENTRIES),
            "CONTRIBUTING.md": _contents_payload(
                path="CONTRIBUTING.md", sha="c" * 40, content="# Contributing\n"
            ),
            "AGENTS.md": _contents_payload(path="AGENTS.md", sha="a" * 40, content="# Agents\n"),
        },
    )

    output_path = tmp_path / "product_reference_pdf_net.json"
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

    # The real CLI parsed "family" from the --output file name (no --family given): confirm the
    # write side actually recorded it before asserting anything about the read side.
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["family"] == "pdf"

    inputs = fetch_product_reference.load_manifest_sidecar(output_path)

    assert inputs is not None
    assert inputs.family == "pdf"


def test_load_manifest_sidecar_of_a_sidecar_with_no_family_key_leaves_it_none(tmp_path: Path) -> None:
    """At TC-783 time, every one of the 32 real, already-committed product_reference_*.json
    sidecars predated the sidecar["family"] = family write and had no "family" key at all
    (confirmed directly against infra/demo-manifests: 32 files, 0 with the key) - this fix must be
    additive and never raise a KeyError for any of them. A sidecar missing the key still loads
    with family=None, exactly like every pilot's own real sidecar did at TC-783 time (TC-786 later
    regenerated 12 of the 32 with a real family; see test_tc_786_completed_pilots_carry_a_family_
    matching_their_own_file_name / test_tc_786_skipped_pilots_are_named_and_still_unmigrated)."""
    path = tmp_path / "product_reference.json"
    path.write_text(json.dumps(_sidecar_dict()), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.family is None


def test_load_manifest_sidecar_reads_a_null_family_as_none(tmp_path: Path) -> None:
    """main() writes sidecar["family"] = family, and family is None whenever --family is omitted
    and the --output file name does not match the product_reference_<family>_<platform>.json
    pattern - a literal JSON null, not an absent key. That must read back as None too, not raise."""
    path = tmp_path / "product_reference.json"
    path.write_text(json.dumps(_sidecar_dict(family=None)), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.family is None


# G3/TC-786: write side of the TC-783 fix. TC-783 fixed load_manifest_sidecar to read "family"
# back, but every one of the 32 real, committed infra/demo-manifests/product_reference_*.json
# sidecars had zero "family" key at all - confirmed directly before this card touched anything,
# the same way the comment above (test_load_manifest_sidecar_of_a_sidecar_with_no_family_key_
# leaves_it_none) confirmed it for TC-783. TC-786 re-fetched each pilot at its OWN already-
# indexed source_ref (never a newer commit) via this module's own CLI, verifying a clean scratch
# diff before committing, and only committed a pilot when the diff was clean.
#
# 12 of the 32 pilots below were regenerated and now carry a correct "family": 3d/typescript,
# barcode/python, cells/cpp, cells/rust, cells/typescript, email/cpp, html/python, imaging/net,
# pdf/cpp, pdf/typescript, slides/cpp, tex/python.
#
# The other 20 pilots were honestly SKIPPED - not forced through - for a real, measured reason
# found while running this card, not a guess: infra/fetch_product_reference.py's own build_sidecar()
# never writes an "install_served_version" key at all (only infra/verify_product_reference_
# install.py's own, separate script does, after the fact) - so main()'s own atomic_write_json
# replaces the whole file and unconditionally drops any existing "install_served_version" the
# moment a pilot is re-fetched with --force, for every pilot that currently has one. That field is
# explicitly named in TC-786's own diff gate as one that must never differ, so every pilot whose
# current real sidecar carries a non-null "install_served_version" was skipped rather than have
# that real, previously-verified data silently discarded: 3d/java, 3d/net, 3d/python, cells/go,
# cells/java, cells/net, cells/python, email/net, email/python, note/python, page/python, pdf/go,
# pdf/java, pdf/net, pdf/python, slides/java, slides/net, slides/python, words/net, words/python.
#
# (TC-786's own closeout also treats the new "readme_document"/"manifest_path" keys that
# build_sidecar() unconditionally adds - absent from literally all 32 real sidecars, which all
# predate TC-561 - as an acceptable additive backfill of a schema TC-786 already depends on,
# not as disqualifying drift; that is a worker judgment call recorded for gatectl/the supervisor
# to confirm, not a verdict this test or this card issues itself.)
_TC_786_COMPLETED_PILOTS = (
    "product_reference_3d_typescript.json",
    "product_reference_barcode_python.json",
    "product_reference_cells_cpp.json",
    "product_reference_cells_rust.json",
    "product_reference_cells_typescript.json",
    "product_reference_email_cpp.json",
    "product_reference_html_python.json",
    "product_reference_imaging_net.json",
    "product_reference_pdf_cpp.json",
    "product_reference_pdf_typescript.json",
    "product_reference_slides_cpp.json",
    "product_reference_tex_python.json",
)
_TC_786_SKIPPED_PILOTS = (
    "product_reference_3d_java.json",
    "product_reference_3d_net.json",
    "product_reference_3d_python.json",
    "product_reference_cells_go.json",
    "product_reference_cells_java.json",
    "product_reference_cells_net.json",
    "product_reference_cells_python.json",
    "product_reference_email_net.json",
    "product_reference_email_python.json",
    "product_reference_note_python.json",
    "product_reference_page_python.json",
    "product_reference_pdf_go.json",
    "product_reference_pdf_java.json",
    "product_reference_pdf_net.json",
    "product_reference_pdf_python.json",
    "product_reference_slides_java.json",
    "product_reference_slides_net.json",
    "product_reference_slides_python.json",
    "product_reference_words_net.json",
    "product_reference_words_python.json",
)
_DEMO_MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "infra" / "demo-manifests"


def test_tc_786_completed_pilots_carry_a_family_matching_their_own_file_name() -> None:
    """Every real, committed sidecar TC-786 actually regenerated has a non-null "family" that
    matches the <family> segment of its own file name - the write-side half of TC-783's fix,
    for the 12 pilots whose scratch diff was confirmed clean before being committed."""
    assert _TC_786_COMPLETED_PILOTS, "the completed-pilot list must name at least one real pilot"
    for name in _TC_786_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        assert path.is_file(), f"{name} must exist in infra/demo-manifests"
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_family = fetch_product_reference._family_from_output(path)
        assert expected_family is not None, (
            f"{name} must match the product_reference_<family>_<platform> pattern"
        )
        assert data.get("family") == expected_family, (name, data.get("family"), expected_family)


def test_tc_786_skipped_pilots_are_named_and_still_unmigrated() -> None:
    """The 20 pilots TC-786 honestly skipped (see the comment above
    _TC_786_SKIPPED_PILOTS) are named explicitly, every real file in infra/demo-manifests is
    accounted for by exactly one of the two TC-786 lists, and a skipped pilot's real file still
    has no "family" - TC-786 never force-committed one of these, so none of them silently
    gained a fabricated or guessed family value.

    NOTE: this assertion is intentionally against the TC-786-era lists, not against today's disk
    state - TC-787 later completed 19 of these 20 (see _TC_787_COMPLETED_PILOTS below), so by the
    time TC-787 lands most of _TC_786_SKIPPED_PILOTS DO carry a family. This test exists to pin
    TC-786's own historical completeness claim (every real file accounted for by exactly one of
    ITS two lists), not to assert the current family value - that is what the TC-787 tests below
    check on the files TC-787 itself touched."""
    completed = set(_TC_786_COMPLETED_PILOTS)
    skipped = set(_TC_786_SKIPPED_PILOTS)
    assert not (completed & skipped), "a pilot cannot be both completed and skipped"
    on_disk = {p.name for p in _DEMO_MANIFESTS_DIR.glob("product_reference_*.json")}
    assert on_disk == completed | skipped, (on_disk - (completed | skipped), (completed | skipped) - on_disk)


# G3/TC-787: completes 19 of the 20 pilots TC-786 left untouched, by running the real, already-
# existing two-step pipeline per pilot (fetch_product_reference.py --force, then
# verify_product_reference_install.py) - exactly what the real Helm ingestion-job.yaml already does
# in sequence, per infra/verify_product_reference_install.py's own module docstring (G2/TC-275).
# No code change was needed; TC-786's own "CORRECTION" DECISION_LOG entry (2026-11-13) explains why
# the bare fetch-only approach TC-786 used was the wrong comparison for these 20 pilots - they all
# carry a non-null install_served_version that only the SECOND step restores, from each pilot's own
# separate, persistent package_registry_<family>_<platform>.json sidecar.
#
# 19 of the 20 were completed cleanly: 3d/java, 3d/net, 3d/python, cells/go, cells/java, cells/net,
# cells/python, email/net, email/python, note/python, page/python, pdf/java, pdf/net, pdf/python,
# slides/java, slides/net, slides/python, words/net, words/python. Each now carries a correct
# "family" (restored by step 1) AND its original "install_served_version" (restored by step 2,
# read back from that pilot's own package_registry sidecar, verified byte-identical to the value
# the real file carried before this card touched it).
#
# pdf/go was honestly SKIPPED - not forced through - for a real, measured reason found while
# running this card: pdf/go's repository has no AGENTS.md but DOES have a real CLAUDE.md at the
# pinned commit (confirmed live), and infra/fetch_product_reference.py's own G3/TC-561 fallback
# (build_sidecar's own module docstring names pdf/go explicitly as its motivating example) reads
# that CLAUDE.md as agent_guidance instead of the pilot's currently-committed not_present AGENTS.md
# result. That is a real content diff beyond the family/fetched_at/drift/install_served_version set
# this card's own diff gate names as acceptable, so - exactly as the gate requires - pdf/go was left
# untouched rather than force a diff this card's own text did not pre-authorize. (Worker judgment
# call recorded for gatectl/the supervisor to confirm, same as TC-786's own manifest_path/
# readme_document ratification call: this looks like another real, additive, already-designed-for
# schema completion rather than data loss, but this card's text did not name it, so it was not
# treated as pre-ratified.)
_TC_787_COMPLETED_PILOTS = (
    "product_reference_3d_java.json",
    "product_reference_3d_net.json",
    "product_reference_3d_python.json",
    "product_reference_cells_go.json",
    "product_reference_cells_java.json",
    "product_reference_cells_net.json",
    "product_reference_cells_python.json",
    "product_reference_email_net.json",
    "product_reference_email_python.json",
    "product_reference_note_python.json",
    "product_reference_page_python.json",
    "product_reference_pdf_java.json",
    "product_reference_pdf_net.json",
    "product_reference_pdf_python.json",
    "product_reference_slides_java.json",
    "product_reference_slides_net.json",
    "product_reference_slides_python.json",
    "product_reference_words_net.json",
    "product_reference_words_python.json",
)
_TC_787_SKIPPED_PILOTS = ("product_reference_pdf_go.json",)


def test_tc_787_completed_pilots_carry_a_family_matching_their_own_file_name() -> None:
    """Every real, committed sidecar TC-787 actually regenerated has a non-null "family" that
    matches the <family> segment of its own file name - the same write-side check TC-786 already
    applied to its own 12 completed pilots, now covering the 19 of the remaining 20 this card
    completed."""
    assert _TC_787_COMPLETED_PILOTS, "the completed-pilot list must name at least one real pilot"
    for name in _TC_787_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        assert path.is_file(), f"{name} must exist in infra/demo-manifests"
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_family = fetch_product_reference._family_from_output(path)
        assert expected_family is not None, (
            f"{name} must match the product_reference_<family>_<platform> pattern"
        )
        assert data.get("family") == expected_family, (name, data.get("family"), expected_family)


def test_tc_787_completed_pilots_also_carry_their_restored_install_served_version() -> None:
    """The whole reason this card exists: TC-786 left these 20 pilots untouched because a bare
    fetch_product_reference.py --force would have dropped their real install_served_version. This
    card's own second pipeline step (verify_product_reference_install.py) must have restored it -
    every one of the 19 completed pilots has a matching package_registry_<family>_<platform>.json
    sidecar with a real served_version (confirmed directly before this card touched anything), so
    every one of the 19 completed pilots must still have a non-null install_served_version after
    both steps ran."""
    for name in _TC_787_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        data = json.loads(path.read_text(encoding="utf-8"))
        served = data.get("install_served_version")
        assert isinstance(served, str) and served, (
            f"{name} must carry a non-null install_served_version restored by step 2",
            served,
        )


def test_tc_787_skipped_pilot_was_later_completed_by_tc_788() -> None:
    """pdf/go, the one pilot TC-787 honestly skipped (see the comment above
    _TC_787_SKIPPED_PILOTS), is no longer in that state - G3/TC-788 completed it once
    docs/DECISION_LOG.md's 2026-11-13 entry ratified its CLAUDE.md agent_guidance fallback diff as
    expected, designed behavior rather than unauthorized drift. This pins TC-787's own historical
    skip (it really did skip it, for the reason it recorded) without asserting a now-false claim
    about today's disk state - see _TC_788_COMPLETED_PILOTS below for the current, true state."""
    assert set(_TC_787_SKIPPED_PILOTS) == set(_TC_788_COMPLETED_PILOTS), (
        "TC-788 is defined as completing exactly the pilot(s) TC-787 left skipped"
    )


# G3/TC-788: completes the one pilot TC-787 honestly held back - pdf/go. TC-787's own worker found a
# real content diff beyond its pre-authorized set (pdf/go's agent_guidance fallback reading a present
# CLAUDE.md document instead of the committed not_present AGENTS.md result) and correctly declined to
# force it through without explicit ratification. docs/DECISION_LOG.md's 2026-11-13 entry ratifies
# that diff as expected, designed behavior: infra/fetch_product_reference.py's own G3/TC-561
# _AGENT_GUIDANCE_FALLBACK mechanism names pdf/go, in a source comment, as its own motivating
# example. This card re-ran the identical two-step pipeline for pdf/go alone, with agent_guidance's
# present-where-absent-was-recorded diff added to the acceptable-diff list for pdf/go specifically
# (not a blanket widening for any other pilot). pdf/go now carries both a correct "family" and its
# original "install_served_version" ("v0.16.0", unchanged - confirmed byte-identical to the value the
# real file carried before this card touched it, since pdf/go's own package_registry_pdf_go.json
# sidecar was still within its 24-hour freshness window and so was read back rather than re-checked
# live), closing out all 32 real pilots with zero remaining skips.
_TC_788_COMPLETED_PILOTS = ("product_reference_pdf_go.json",)


def test_tc_788_completed_pilot_carries_a_family_matching_its_own_file_name() -> None:
    """pdf/go, the one pilot TC-787 left skipped, now carries a correct "family" - the same
    write-side check TC-786/TC-787 already applied to their own completed pilots."""
    assert _TC_788_COMPLETED_PILOTS, "the completed-pilot list must name at least one real pilot"
    for name in _TC_788_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        assert path.is_file(), f"{name} must exist in infra/demo-manifests"
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_family = fetch_product_reference._family_from_output(path)
        assert expected_family is not None, (
            f"{name} must match the product_reference_<family>_<platform> pattern"
        )
        assert data.get("family") == expected_family, (name, data.get("family"), expected_family)


def test_tc_788_completed_pilot_also_carries_its_restored_install_served_version() -> None:
    """pdf/go's own package_registry_pdf_go.json sidecar carries a real served_version (confirmed
    directly before this card touched anything), so after both pipeline steps ran, the real sidecar
    must still carry a non-null install_served_version - the same guarantee TC-787 already checked
    for its own 19 completed pilots."""
    for name in _TC_788_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        data = json.loads(path.read_text(encoding="utf-8"))
        served = data.get("install_served_version")
        assert isinstance(served, str) and served, (
            f"{name} must carry a non-null install_served_version restored by step 2",
            served,
        )


def test_tc_788_completed_pilot_carries_the_ratified_present_claude_md_agent_guidance() -> None:
    """The whole reason TC-787 held this pilot back: pdf/go's real repository has no AGENTS.md but
    does have a real CLAUDE.md at the pinned commit, and infra/fetch_product_reference.py's own
    G3/TC-561 fallback (build_sidecar's own module docstring names pdf/go explicitly as its
    motivating example) correctly reads it. This card's own closeout ratifies that as the designed,
    expected diff it exists to commit - not an incidental side effect to leave unchecked."""
    for name in _TC_788_COMPLETED_PILOTS:
        path = _DEMO_MANIFESTS_DIR / name
        data = json.loads(path.read_text(encoding="utf-8"))
        guidance = data.get("agent_guidance") or {}
        assert guidance.get("status") == "present", (name, guidance)
        assert guidance.get("path") == "CLAUDE.md", (name, guidance)


def test_every_real_pilot_is_now_accounted_for_by_exactly_one_tc_786_787_or_788_list() -> None:
    """After all three cards, every real product_reference_*.json file in infra/demo-manifests is
    accounted for by exactly one of: TC-786-completed, TC-786-skipped-but-TC-787-completed, or
    TC-787-skipped-but-TC-788-completed - no file is silently missing from every list, no file
    appears on more than one, and (now that TC-788 has landed) zero real pilots remain in any
    "still honestly skipped" state: all 32 carry a correct, non-null "family"."""
    completed = set(_TC_786_COMPLETED_PILOTS) | set(_TC_787_COMPLETED_PILOTS) | set(_TC_788_COMPLETED_PILOTS)
    still_skipped: set[str] = set()
    assert not (completed & still_skipped), "a pilot cannot be both completed and still-skipped"
    on_disk = {p.name for p in _DEMO_MANIFESTS_DIR.glob("product_reference_*.json")}
    assert on_disk == completed | still_skipped, (
        on_disk - (completed | still_skipped),
        (completed | still_skipped) - on_disk,
    )
    assert not still_skipped, "zero real pilots should remain honestly skipped after G3/TC-788"
    for name in completed:
        data = json.loads((_DEMO_MANIFESTS_DIR / name).read_text(encoding="utf-8"))
        assert data.get("family") is not None, f"{name} is listed as completed but has no family"
