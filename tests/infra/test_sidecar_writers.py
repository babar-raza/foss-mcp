"""The sidecar writers: atomic writes, the typed fetch_failed placeholder, provenance (G3/TC-511).

Before this card a failed product-reference fetch wrote NOTHING (the serving pod's init gate then
waited on a file that never came), every sidecar was written with a plain ``write_text`` (a kill
mid-write left a truncated file), and no sidecar recorded when or for which family it was fetched.

Fully offline: the network is a monkeypatched ``urllib.request.urlopen`` or a monkeypatched
``fetch_releases``, every sleep is a no-op, tokens are fake values, and nothing opens a socket.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from foss_mcp.extraction import github_http
from foss_mcp.extraction.github_http import GITHUB_TOKEN_ENV, GitHubAuthError, reset_default_budget
from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.mcp.routing import DeploymentConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INFRA = _REPO_ROOT / "infra"
# infra/ is not a package; its modules are imported bare, as tests/infra/test_serve_manifest_sidecar.py does.
sys.path.insert(0, str(_INFRA))
import fetch_product_reference as fpr  # noqa: E402
import fetch_recent_releases as frr  # noqa: E402
import serve_http  # noqa: E402
import verify_package_registry as vpr  # noqa: E402
import verify_product_reference_install as vpri  # noqa: E402

_WRITERS = [
    pytest.param(fpr, id="fetch_product_reference"),
    pytest.param(frr, id="fetch_recent_releases"),
    pytest.param(vpr, id="verify_package_registry"),
]
_WRITER_FILES = [
    "infra/fetch_product_reference.py",
    "infra/fetch_recent_releases.py",
    "infra/verify_package_registry.py",
    "infra/verify_product_reference_install.py",
]
_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-Python"
_REF = "a" * 40
_NEW_REF = "b" * 40
_TOKEN = "ghp_FAKE0000000000000000000000000000000"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GITHUB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(github_http, "_sleep", lambda seconds: None)
    monkeypatch.setattr(github_http, "_jitter", lambda: 0.0)
    reset_default_budget()


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


# --- atomic_write_json -----------------------------------------------------------------------


@pytest.mark.parametrize("module", _WRITERS)
def test_atomic_write_leaves_the_old_file_when_replace_fails_and_no_temp_file_remains(
    module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sidecar.json"
    path.write_text('{"old": true}', encoding="utf-8")

    def failing_replace(source: object, destination: object) -> None:
        raise OSError("simulated kill before the rename")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated kill"):
        module.atomic_write_json(path, {"new": True})
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert _names(tmp_path) == ["sidecar.json"]


@pytest.mark.parametrize("module", _WRITERS)
def test_atomic_write_leaves_the_old_file_when_serialisation_fails(module, tmp_path: Path) -> None:
    path = tmp_path / "sidecar.json"
    path.write_text('{"old": true}', encoding="utf-8")
    with pytest.raises(TypeError):
        module.atomic_write_json(path, {"new": object()})
    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert _names(tmp_path) == ["sidecar.json"]


@pytest.mark.parametrize("module", _WRITERS)
def test_atomic_write_fsyncs_before_replace(module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def recording_fsync(descriptor: int) -> None:
        events.append("fsync")
        real_fsync(descriptor)

    def recording_replace(source: object, destination: object) -> None:
        events.append("replace")
        real_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)
    path = tmp_path / "sidecar.json"
    document = {"a": [1, 2], "b": "text"}
    module.atomic_write_json(path, document)
    monkeypatch.undo()
    assert events == ["fsync", "replace"]
    assert path.read_text(encoding="utf-8") == json.dumps(document)  # the compact form used today
    assert _names(tmp_path) == ["sidecar.json"]


@pytest.mark.parametrize("relative", _WRITER_FILES)
def test_no_writer_uses_a_bare_write_text_for_a_sidecar(relative: str) -> None:
    source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "atomic_write_json" in source
    assert re.search(r"write_text\(\s*json\.dumps", source) is None


def test_the_three_atomic_writer_definitions_are_identical() -> None:
    reference = inspect.getsource(fpr.atomic_write_json)
    assert inspect.getsource(frr.atomic_write_json) == reference
    assert inspect.getsource(vpr.atomic_write_json) == reference
    # verify_product_reference_install uses the one in fetch_product_reference, not a fourth copy.
    assert vpri.atomic_write_json is fpr.atomic_write_json


def test_fetch_recent_releases_loads_without_infra_on_sys_path(monkeypatch: pytest.MonkeyPatch) -> None:
    infra = os.path.normcase(os.path.abspath(_INFRA))
    kept = [entry for entry in sys.path if os.path.normcase(os.path.abspath(entry or ".")) != infra]
    monkeypatch.setattr(sys, "path", kept)
    for name in ("fetch_recent_releases", "verify_package_registry"):
        spec = importlib.util.spec_from_file_location(f"{name}_isolated_tc511", _INFRA / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.atomic_write_json)


# --- the product reference: failure ----------------------------------------------------------


def _set_argv(
    monkeypatch: pytest.MonkeyPatch, output: Path, *extra: str, ref: str = _REF, manifest: bool = True
) -> None:
    argv = ["fetch_product_reference.py", "--repository", _REPOSITORY, "--platform", "python", "--ref", ref]
    if manifest:
        argv += ["--manifest-path", "pyproject.toml"]
    monkeypatch.setattr(sys, "argv", [*argv, "--output", str(output), *extra])


def _urlopen_raising(monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], BaseException]) -> None:
    def fake_urlopen(request: object, timeout: float = 30) -> object:
        raise make_error()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _http_error(
    code: int, headers: dict[str, str] | None = None, msg: str = "failure"
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/repos/o/r/contents/pyproject.toml",
        code,
        msg,
        headers or {},
        None,  # type: ignore[arg-type]
    )


def _run_failing_main(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    with pytest.raises(SystemExit) as exit_info:
        fpr.main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    return captured.out, captured.err


def _good_sidecar(ref: str = _REF) -> dict:
    return {
        "manifest_text": "[project]\nname = 'x'\n",
        "platform": "python",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
        "source_repository": _REPOSITORY,
        "source_ref": ref,
    }


def _output(tmp_path: Path) -> Path:
    return tmp_path / "product_reference_pdf_python.json"


def test_failed_product_reference_fetch_with_no_file_writes_the_typed_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output)
    _run_failing_main(capsys)
    placeholder = json.loads(output.read_text(encoding="utf-8"))
    assert placeholder["reason"] == "auth_failed"
    assert placeholder["error_class"] == "GitHubAuthError"
    assert _TIMESTAMP.fullmatch(placeholder["fetched_at"])
    assert placeholder["family"] == "pdf"
    assert placeholder["platform"] == "python"
    assert placeholder["source_repository"] == _REPOSITORY
    assert placeholder["source_ref"] == _REF
    assert _names(tmp_path) == [
        "product_reference_pdf_python.json",
        "product_reference_pdf_python.json.failed",
    ]


@pytest.mark.parametrize(
    ("make_error", "expected_reason"),
    [
        pytest.param(lambda: _http_error(401), "auth_failed", id="401"),
        pytest.param(
            lambda: _http_error(
                403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 3600)}
            ),
            "rate_limited",
            id="403-remaining-zero-one-hour-reset",
        ),
        pytest.param(
            lambda: _http_error(403, {"Retry-After": "3600", "x-ratelimit-remaining": "57"}),
            "rate_limited",
            id="secondary-403-long-retry-after",
        ),
        pytest.param(lambda: _http_error(502), "http_502", id="502"),
        pytest.param(lambda: TimeoutError("timed out"), "network", id="timeout"),
    ],
)
def test_the_placeholder_reason_matches_the_failure_class(
    make_error: Callable[[], BaseException],
    expected_reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _output(tmp_path)
    _urlopen_raising(monkeypatch, make_error)
    _set_argv(monkeypatch, output)
    _out, err = _run_failing_main(capsys)
    placeholder = json.loads(output.read_text(encoding="utf-8"))
    assert placeholder["reason"] == expected_reason
    assert f"reason={expected_reason}" in err


def test_failed_fetch_never_overwrites_a_good_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    output.write_text(json.dumps(_good_sidecar(_REF)), encoding="utf-8")
    before = output.read_bytes()
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output, ref=_NEW_REF)  # a different pin, so the pin-unchanged skip does not apply
    _out, err = _run_failing_main(capsys)
    assert output.read_bytes() == before
    assert fpr._failed_marker(output).is_file()
    assert "kept the existing sidecar" in err


@pytest.mark.parametrize(
    "junk", ["{not json", "[1, 2, 3]", "", "null"], ids=["truncated", "list", "empty", "null"]
)
def test_failed_fetch_over_a_malformed_file_writes_the_placeholder(
    junk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    output.write_text(junk, encoding="utf-8")
    _urlopen_raising(monkeypatch, lambda: _http_error(502))
    _set_argv(monkeypatch, output)
    _out, err = _run_failing_main(capsys)
    placeholder = json.loads(output.read_text(encoding="utf-8"))
    assert fpr.is_fetch_failed_placeholder(placeholder)
    assert "wrote a fetch_failed placeholder" in err


def test_failed_fetch_over_an_older_placeholder_replaces_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    output.write_text(
        json.dumps({"state": "fetch_failed", "status": "fetch_failed", "reason": "error"}), encoding="utf-8"
    )
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output)
    _run_failing_main(capsys)
    assert json.loads(output.read_text(encoding="utf-8"))["reason"] == "auth_failed"


def test_failed_fetch_exits_one_writes_the_marker_and_logs_an_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output)
    out, err = _run_failing_main(capsys)  # exit status 1 is asserted inside
    marker = fpr._failed_marker(output)
    assert re.fullmatch(
        r"GitHubAuthError \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\n", marker.read_text(encoding="utf-8")
    )
    error_lines = [line for line in err.splitlines() if line.startswith("ERROR ")]
    assert len(error_lines) == 1
    line = error_lines[0]
    assert "GitHubAuthError" in line and "auth_failed" in line and _REPOSITORY in line
    assert "placeholder" in line
    assert out == ""


def test_the_placeholder_and_the_log_line_contain_no_url_token_or_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, _TOKEN)
    output = _output(tmp_path)
    secret_message = f"UNIQUEMESSAGE {_TOKEN} https://api.github.com/x?access_token=QUERYSECRET"
    _urlopen_raising(monkeypatch, lambda: _http_error(401, msg=secret_message))
    _set_argv(monkeypatch, output)
    out, err = _run_failing_main(capsys)
    written = output.read_text(encoding="utf-8") + fpr._failed_marker(output).read_text(encoding="utf-8")
    for text in (written, out, err):
        assert _TOKEN not in text
        assert "UNIQUEMESSAGE" not in text
        assert "QUERYSECRET" not in text
        assert "://" not in text
        assert "api.github.com" not in text


def test_a_placeholder_never_triggers_the_pin_unchanged_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    placeholder = {
        "state": "fetch_failed",
        "status": "fetch_failed",
        "source_repository": _REPOSITORY,
        "source_ref": _REF,
    }
    output.write_text(json.dumps(placeholder), encoding="utf-8")
    assert fpr._existing_sidecar_source(output) is None
    calls: list[str] = []

    def fake_build(**kwargs: object) -> dict:
        calls.append("build")
        return _good_sidecar(_REF)

    monkeypatch.setattr(fpr, "build_sidecar", fake_build)
    _set_argv(monkeypatch, output)
    fpr.main()
    assert calls == ["build"]
    assert not fpr.is_fetch_failed_placeholder(json.loads(output.read_text(encoding="utf-8")))
    assert "skipped" not in capsys.readouterr().out


def test_a_failed_marker_forces_a_refetch_even_with_an_unchanged_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    output.write_text(json.dumps(_good_sidecar(_REF)), encoding="utf-8")
    fpr._failed_marker(output).write_text("HTTPError 2026-01-01T00:00:00Z\n", encoding="utf-8")
    calls: list[str] = []

    def fake_build(**kwargs: object) -> dict:
        calls.append("build")
        return _good_sidecar(_REF)

    monkeypatch.setattr(fpr, "build_sidecar", fake_build)
    _set_argv(monkeypatch, output)
    fpr.main()
    assert calls == ["build"]
    assert not fpr._failed_marker(output).exists()
    # Control: with no marker the same pin IS skipped.
    fpr.main()
    assert calls == ["build"]
    assert "skipped fetch" in capsys.readouterr().out


# --- the product reference: success ----------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "extra", "expected_family"),
    [
        pytest.param("product_reference_pdf_python.json", [], "pdf", id="parsed-from-the-file-name"),
        pytest.param(
            "product_reference_pdf_python.json", ["--family", "words"], "words", id="family-override"
        ),
        pytest.param("product_reference.json", [], None, id="no-identity-in-the-name"),
        pytest.param("product_reference_pdf.json", [], None, id="not-the-exact-form"),
    ],
)
def test_success_removes_the_marker_and_records_provenance(
    file_name: str,
    extra: list[str],
    expected_family: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / file_name
    marker = fpr._failed_marker(output)
    marker.write_text("HTTPError 2026-01-01T00:00:00Z\n", encoding="utf-8")

    def fake_build(*, repository: str, platform: str, ref: str | None, **kwargs: object) -> dict:
        sidecar = _good_sidecar()
        sidecar.update(platform=platform, source_repository=repository, source_ref=ref)
        return sidecar

    monkeypatch.setattr(fpr, "build_sidecar", fake_build)
    _set_argv(monkeypatch, output, *extra)
    fpr.main()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert _TIMESTAMP.fullmatch(written["fetched_at"])
    assert written["family"] == expected_family
    assert written["platform"] == "python"
    assert written["source_repository"] == _REPOSITORY
    assert written["source_ref"] == _REF
    assert not marker.exists()
    assert _names(tmp_path) == [file_name]
    assert "wrote product reference inputs" in capsys.readouterr().out
    # A good sidecar with the additive keys still loads.
    assert fpr.load_manifest_sidecar(output) is not None


def test_the_placeholder_carries_state_and_status_both_fetch_failed_and_is_recognised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output)
    _run_failing_main(capsys)
    placeholder = json.loads(output.read_text(encoding="utf-8"))
    assert fpr.FETCH_FAILED_STATUS == "fetch_failed"
    assert placeholder["state"] == "fetch_failed"
    assert placeholder["status"] == "fetch_failed"
    assert set(placeholder) == {
        "state",
        "status",
        "reason",
        "error_class",
        "fetched_at",
        "family",
        "platform",
        "source_repository",
        "source_ref",
    }
    assert fpr.is_fetch_failed_placeholder(placeholder) is True
    assert fpr.is_fetch_failed_placeholder({"status": "fetch_failed"}) is True
    assert fpr.is_fetch_failed_placeholder({"state": "fetch_failed"}) is True
    assert fpr.is_fetch_failed_placeholder(_good_sidecar()) is False
    for not_a_placeholder in ([], [placeholder], None, 42, "fetch_failed", {}, {"state": "ok"}):
        assert fpr.is_fetch_failed_placeholder(not_a_placeholder) is False


def test_serving_reads_the_placeholder_as_fetch_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(tmp_path)
    _urlopen_raising(monkeypatch, lambda: _http_error(401))
    _set_argv(monkeypatch, output)
    _run_failing_main(capsys)
    inputs = serve_http._serving_product_reference_inputs(
        tmp_path, DeploymentConfig(family="pdf", platform="python")
    )
    assert inputs is not None
    assert inputs.sidecar_state == "fetch_failed"
    assert _TIMESTAMP.fullmatch(inputs.sidecar_fetched_at or "")


# --- the verification steps ------------------------------------------------------------------

_PYPROJECT = '[project]\nname = "aspose-email-foss"\nversion = "26.9.0"\n'


def _write_manifest_sidecar(directory: Path, **overrides: object) -> Path:
    path = directory / fpr.sidecar_name("email", "python")
    sidecar = {
        "manifest_text": _PYPROJECT,
        "platform": "python",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
    }
    sidecar.update(overrides)
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def _verify_argv(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_product_reference_install.py",
            "--family",
            "email",
            "--platform",
            "python",
            "--manifests-dir",
            str(directory),
        ],
    )


def test_verify_product_reference_install_skips_a_placeholder_without_calling_the_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / fpr.sidecar_name("email", "python")
    path.write_text(
        json.dumps({"state": "fetch_failed", "status": "fetch_failed", "reason": "auth_failed"}),
        encoding="utf-8",
    )

    def loader_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_manifest_sidecar must not be called for a placeholder")

    monkeypatch.setattr(vpri, "load_manifest_sidecar", loader_must_not_run)
    _verify_argv(monkeypatch, tmp_path)
    vpri.main()  # exits 0: returns without raising
    assert capsys.readouterr().out.strip() == "nothing to verify: the product reference fetch failed"
    assert _names(tmp_path) == [path.name]


def test_verify_product_reference_install_writes_provenance_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest_sidecar(tmp_path, source_repository=_REPOSITORY, source_ref=_REF)
    monkeypatch.setitem(vpr._CHECKERS, "pypi", lambda coordinate: True)
    writes: list[Path] = []
    real_write = vpri.atomic_write_json

    def spy(path: Path, doc: object) -> None:
        writes.append(path)
        real_write(path, doc)

    monkeypatch.setattr(vpri, "atomic_write_json", spy)
    _verify_argv(monkeypatch, tmp_path)
    vpri.main()
    output = tmp_path / vpr.verify_package_registry_sidecar_name("email", "python")
    assert writes == [output]
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["ecosystem"] == "pypi" and written["coordinate"] == "aspose-email-foss"
    assert written["verified"] is True
    assert written["family"] == "email" and written["platform"] == "python"
    assert written["fetched_at"] == written["checked_at"]
    assert written["source_repository"] == _REPOSITORY and written["source_ref"] == _REF
    assert vpr.load_package_registry_sidecar(output) is not None
    assert [name for name in _names(tmp_path) if name.startswith(".tmp-")] == []


def test_verify_package_registry_writes_provenance_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / vpr.verify_package_registry_sidecar_name("email", "python")
    monkeypatch.setitem(vpr._CHECKERS, "pypi", lambda coordinate: False)
    writes: list[Path] = []
    real_write = vpr.atomic_write_json

    def spy(path: Path, doc: object) -> None:
        writes.append(path)
        real_write(path, doc)

    monkeypatch.setattr(vpr, "atomic_write_json", spy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_package_registry.py",
            "--family",
            "email",
            "--platform",
            "python",
            "--ecosystem",
            "pypi",
            "--coordinate",
            "aspose-email-foss",
            "--output",
            str(output),
        ],
    )
    vpr.main()
    assert writes == [output]
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["verified"] is False
    assert written["family"] == "email" and written["platform"] == "python"
    assert written["fetched_at"] == written["checked_at"]
    assert vpr.load_package_registry_sidecar(output) is not None
    assert _names(tmp_path) == [output.name]


# --- the recent releases sidecar -------------------------------------------------------------

_RELEASES_NAME = "recent_releases_pdf_python.json"


def _releases_argv(monkeypatch: pytest.MonkeyPatch, output: Path, *extra: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_recent_releases.py", "--repository", _REPOSITORY, "--output", str(output), *extra],
    )


class _FakeFetch:
    """A replacement for ``fetch_releases`` that records its keyword arguments."""

    def __init__(
        self,
        releases: list[Release] | None = None,
        *,
        error: BaseException | None = None,
        etag: str | None = None,
        not_modified: bool = False,
    ) -> None:
        self.releases = releases if releases is not None else []
        self.error = error
        self.etag = etag
        self.not_modified = not_modified
        self.calls: list[dict[str, object]] = []

    def __call__(self, repository: str, **kwargs: object) -> list[Release]:
        self.calls.append({"repository": repository, **kwargs})
        if self.error is not None:
            raise self.error
        meta = kwargs.get("meta")
        if isinstance(meta, dict):
            if self.etag is not None:
                meta["etag"] = self.etag
            if self.not_modified:
                meta["not_modified"] = True
        return list(self.releases)


def test_releases_success_writes_the_list_and_a_meta_with_identity_and_etag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / _RELEASES_NAME
    release = Release(
        "v1", "Fixed `x` in 3 places", "detailed", target_commitish=_REF, created_at="2026-01-01T00:00:00Z"
    )
    fake = _FakeFetch([release], etag='"e1"')
    monkeypatch.setattr(frr, "fetch_releases", fake)
    _releases_argv(monkeypatch, output)
    frr.main()
    listing = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(listing, list) and listing[0]["tag_name"] == "v1"
    assert listing[0]["target_commitish"] == _REF and listing[0]["created_at"] == "2026-01-01T00:00:00Z"
    meta = json.loads(frr.releases_meta_path(output).read_text(encoding="utf-8"))
    assert frr.releases_meta_path(output).name == _RELEASES_NAME + ".meta.json"
    assert meta["schema"] == 1 and meta["state"] == "ok"
    assert _TIMESTAMP.fullmatch(meta["fetched_at"])
    assert meta["family"] == "pdf" and meta["platform"] == "python"
    assert meta["source_repository"] == _REPOSITORY
    assert meta["release_count"] == 1 and meta["etag"] == '"e1"'
    assert "etag" not in fake.calls[0] and isinstance(fake.calls[0]["meta"], dict)
    assert _names(tmp_path) == [_RELEASES_NAME, _RELEASES_NAME + ".meta.json"]
    # The list stays loadable by the unchanged loader, and an explicit identity wins over the file name.
    assert frr.load_recent_releases_sidecar(output) == (release,)
    _releases_argv(monkeypatch, output, "--family", "words", "--platform", "go", "--force")
    frr.main()
    meta = json.loads(frr.releases_meta_path(output).read_text(encoding="utf-8"))
    assert meta["family"] == "words" and meta["platform"] == "go"


def test_releases_failure_with_nothing_usable_writes_empty_list_marker_and_a_fetch_failed_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / _RELEASES_NAME
    monkeypatch.setattr(frr, "fetch_releases", _FakeFetch(error=GitHubAuthError(_http_error(401))))
    _releases_argv(monkeypatch, output)
    frr.main()  # exit status stays 0
    assert json.loads(output.read_text(encoding="utf-8")) == []
    assert frr._fetch_failed_marker(output).is_file()
    meta = json.loads(frr.releases_meta_path(output).read_text(encoding="utf-8"))
    assert meta["state"] == "fetch_failed"
    assert meta["reason"] == "auth_failed" and meta["error_class"] == "GitHubAuthError"
    assert _TIMESTAMP.fullmatch(meta["fetched_at"])
    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("ERROR ")]
    assert len(err_lines) == 1
    assert "auth_failed" in err_lines[0] and _REPOSITORY in err_lines[0] and "placeholder" in err_lines[0]
    assert "://" not in err_lines[0]


def test_releases_failure_keeps_a_good_list_and_logs_an_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / _RELEASES_NAME
    output.write_text(json.dumps([{"tag_name": "old", "body": "b", "richness": "none"}]), encoding="utf-8")
    os.utime(output, (1.0, 1.0))
    meta_path = frr.releases_meta_path(output)
    meta_path.write_text(json.dumps({"schema": 1, "state": "ok", "etag": '"e0"'}), encoding="utf-8")
    list_before, meta_before = output.read_bytes(), meta_path.read_bytes()
    monkeypatch.setattr(frr, "fetch_releases", _FakeFetch(error=_http_error(502)))
    _releases_argv(monkeypatch, output)
    frr.main()
    assert output.read_bytes() == list_before
    assert meta_path.read_bytes() == meta_before
    assert frr._fetch_failed_marker(output).is_file()
    err = capsys.readouterr().err
    assert any(
        line.startswith("ERROR ") and "http_502" in line and "kept" in line for line in err.splitlines()
    )


def test_releases_etag_304_keeps_the_bytes_and_refreshes_mtime_and_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / _RELEASES_NAME
    output.write_text(json.dumps([{"tag_name": "v1", "body": "b", "richness": "none"}]), encoding="utf-8")
    os.utime(output, (1.0, 1.0))
    meta_path = frr.releases_meta_path(output)
    old_meta = {
        "schema": 1,
        "state": "ok",
        "fetched_at": "2000-01-01T00:00:00Z",
        "family": "pdf",
        "platform": "python",
        "source_repository": _REPOSITORY,
        "release_count": 1,
        "etag": '"stored"',
    }
    meta_path.write_text(json.dumps(old_meta), encoding="utf-8")
    frr._fetch_failed_marker(output).write_text("HTTPError 2026-01-01T00:00:00Z\n", encoding="utf-8")
    before = output.read_bytes()
    fake = _FakeFetch(not_modified=True, etag='"stored"')
    monkeypatch.setattr(frr, "fetch_releases", fake)
    _releases_argv(monkeypatch, output)
    frr.main()
    assert fake.calls[0]["etag"] == '"stored"' and isinstance(fake.calls[0]["meta"], dict)
    assert output.read_bytes() == before
    assert output.stat().st_mtime > 1.0 + 3600
    new_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert new_meta["fetched_at"] != old_meta["fetched_at"] and _TIMESTAMP.fullmatch(new_meta["fetched_at"])
    assert new_meta["etag"] == '"stored"' and new_meta["release_count"] == 1 and new_meta["state"] == "ok"
    assert not frr._fetch_failed_marker(output).exists()
    assert "not modified" in capsys.readouterr().out
    # The refreshed mtime makes the sidecar fresh again: the next run does not fetch at all.
    frr.main()
    assert len(fake.calls) == 1


def test_releases_without_a_stored_etag_does_a_plain_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / _RELEASES_NAME
    output.write_text(json.dumps([{"tag_name": "v0", "body": "b", "richness": "none"}]), encoding="utf-8")
    os.utime(output, (1.0, 1.0))  # a good but stale list with no meta
    fake = _FakeFetch([Release("v1", "b", "none")], etag='"first"')
    monkeypatch.setattr(frr, "fetch_releases", fake)
    _releases_argv(monkeypatch, output)
    frr.main()
    assert "etag" not in fake.calls[0]
    assert isinstance(fake.calls[0]["meta"], dict)
    assert json.loads(frr.releases_meta_path(output).read_text(encoding="utf-8"))["etag"] == '"first"'
    assert [entry["tag_name"] for entry in json.loads(output.read_text(encoding="utf-8"))] == ["v1"]
