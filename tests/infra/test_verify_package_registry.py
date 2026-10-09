"""infra.verify_package_registry's first test coverage (G2/TC-260).

Before this card, ``get_product_reference(section="install")`` presented whatever package name a
product's own packaging manifest stated as if it were a verified, installable public package -
confirmed, fleet-wide, that 15 of 33 install-claiming pilots serve a coordinate that does not
actually exist on its stated registry (``docs/DECISION_LOG.md``'s "C2" entry). This module is the
ingestion-time verification step that closes that gap: one checker per ecosystem, resolving a
coordinate against its real registry, plus the sidecar that records the result.

Fully offline per this card's own ``network: false``: every test here mocks
``urllib.request.urlopen`` itself (never a higher-level wrapper), so no test in this file ever
reaches the real network. A genuine transport failure (any non-404 HTTPError, or a URLError) must
propagate rather than being swallowed - the exact discipline this card exists to uphold - so
several tests assert that propagation explicitly, both at the single-checker level and through
``main()``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Callable

import pytest

import infra.verify_package_registry as verify_package_registry


class _FakeResponse:
    """A minimal stand-in for ``http.client.HTTPResponse`` as a context manager."""

    def __init__(self, body: bytes = b"{}") -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _urlopen_returning(body: bytes = b"{}") -> Callable[..., _FakeResponse]:
    def _fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(body)

    return _fake_urlopen


def _urlopen_raising(exc: Exception) -> Callable[..., _FakeResponse]:
    def _fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        raise exc

    return _fake_urlopen


def _urlopen_capturing(calls: list[str], body: bytes = b"{}") -> Callable[..., _FakeResponse]:
    def _fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        calls.append(request.full_url)
        return _FakeResponse(body)

    return _fake_urlopen


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/x", code, "status", {}, None)


# --- sidecar identity -------------------------------------------------------------------------


def test_sidecar_name_matches_the_expected_shape() -> None:
    assert (
        verify_package_registry.verify_package_registry_sidecar_name("pdf", "python")
        == "package_registry_pdf_python.json"
    )


def test_sidecar_name_rejects_an_uppercase_identity_part() -> None:
    with pytest.raises(ValueError):
        verify_package_registry.verify_package_registry_sidecar_name("PDF", "python")


def test_sidecar_name_rejects_a_hyphenated_identity_part() -> None:
    with pytest.raises(ValueError):
        verify_package_registry.verify_package_registry_sidecar_name("pdf", "net-core")


# --- per-ecosystem checkers: exists / does-not-exist / transport failure -----------------------


def test_check_pypi_true_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_capturing(calls))
    assert verify_package_registry.check_pypi("aspose-pdf-foss-for-python") is True
    assert calls == ["https://pypi.org/pypi/aspose-pdf-foss-for-python/json"]


def test_check_pypi_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_pypi("does-not-exist") is False


def test_check_npm_true_on_200_and_encodes_scoped_package_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_capturing(calls))
    assert verify_package_registry.check_npm("@aspose/3d") is True
    assert calls == ["https://registry.npmjs.org/@aspose%2f3d"]


def test_check_npm_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_npm("@aspose/3d") is False


def test_check_cargo_true_on_200_and_uses_the_sparse_index_sharding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_capturing(calls))
    assert verify_package_registry.check_cargo("Aspose-Cells-Foss-Rust") is True
    assert calls == ["https://index.crates.io/as/po/aspose-cells-foss-rust"]


def test_check_cargo_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_cargo("aspose-cells-foss-rust") is False


@pytest.mark.parametrize(
    ("name", "expected_path"),
    [
        ("a", "1/a"),
        ("ab", "2/ab"),
        ("abc", "3/a/abc"),
        ("abcd", "ab/cd/abcd"),
        ("abcde", "ab/cd/abcde"),
    ],
)
def test_cargo_sparse_index_path_matches_crates_io_sharding(name: str, expected_path: str) -> None:
    assert verify_package_registry._cargo_sparse_index_path(name) == expected_path


def test_check_maven_true_on_200_and_replaces_dots_with_slashes_in_the_group_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_capturing(calls))
    assert verify_package_registry.check_maven("org.aspose:aspose-pdf-foss") is True
    assert calls == ["https://repo1.maven.org/maven2/org/aspose/aspose-pdf-foss/"]


def test_check_maven_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_maven("com.aspose:aspose-jmap-foss") is False


def test_check_maven_rejects_a_coordinate_without_a_colon() -> None:
    with pytest.raises(ValueError):
        verify_package_registry.check_maven("org.aspose-aspose-pdf-foss")


def test_check_nuget_true_on_200_and_lowercases_the_coordinate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_capturing(calls))
    assert verify_package_registry.check_nuget("Aspose.Imaging.Foss") is True
    assert calls == ["https://api.nuget.org/v3-flatcontainer/aspose.imaging.foss/index.json"]


def test_check_nuget_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_nuget("Aspose.Imaging.Foss") is False


def test_check_go_true_on_200_with_non_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        calls.append(request.full_url)
        return _FakeResponse(b'{"Version":"v0.0.0-20260101000000-abcdef123456"}')

    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _fake_urlopen)
    assert verify_package_registry.check_go("github.com/aspose-pdf-foss/aspose-pdf-foss-for-go") is True
    assert calls == ["https://proxy.golang.org/github.com/aspose-pdf-foss/aspose-pdf-foss-for-go/@latest"]


def test_check_go_false_on_200_with_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_returning(b""))
    assert verify_package_registry.check_go("github.com/example/missing") is False


def test_check_go_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
    assert verify_package_registry.check_go("github.com/example/missing") is False


@pytest.mark.parametrize(
    "checker_name",
    ["check_pypi", "check_npm", "check_cargo", "check_nuget"],
)
def test_checker_propagates_a_non_404_http_error_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch, checker_name: str
) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(500)))
    checker = getattr(verify_package_registry, checker_name)
    with pytest.raises(urllib.error.HTTPError):
        checker("some-coordinate")


def test_check_maven_propagates_a_non_404_http_error_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(503)))
    with pytest.raises(urllib.error.HTTPError):
        verify_package_registry.check_maven("org.aspose:aspose-pdf-foss")


def test_check_go_propagates_a_non_404_http_error_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_package_registry.urllib.request, "urlopen", _urlopen_raising(_http_error(500)))
    with pytest.raises(urllib.error.HTTPError):
        verify_package_registry.check_go("github.com/example/thing")


def test_check_pypi_propagates_a_url_error_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verify_package_registry.urllib.request,
        "urlopen",
        _urlopen_raising(urllib.error.URLError("boom: simulated connection failure")),
    )
    with pytest.raises(urllib.error.URLError):
        verify_package_registry.check_pypi("aspose-pdf-foss-for-python")


# --- main(): the CLI end to end against a mocked checker ---------------------------------------


def _set_argv(
    monkeypatch: pytest.MonkeyPatch,
    output_path: Path,
    *,
    family: str = "jmap",
    platform: str = "python",
    ecosystem: str = "pypi",
    coordinate: str = "aspose-jmap-foss",
) -> None:
    argv = [
        "verify_package_registry.py",
        "--family",
        family,
        "--platform",
        platform,
        "--ecosystem",
        ecosystem,
        "--coordinate",
        coordinate,
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)


def test_main_writes_the_real_shaped_json_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: True)
    output_path = tmp_path / "package_registry.json"
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["ecosystem"] == "pypi"
    assert written["coordinate"] == "aspose-jmap-foss"
    assert written["verified"] is True
    assert isinstance(written["checked_at"], str) and written["checked_at"]


def test_main_writes_verified_false_when_the_checker_confirms_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: False)
    output_path = tmp_path / "package_registry.json"
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["verified"] is False


def test_main_lets_a_checker_transport_failure_propagate_without_writing_a_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _checker_that_fails(coordinate: str) -> bool:
        raise urllib.error.URLError("boom: simulated registry outage")

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _checker_that_fails)
    output_path = tmp_path / "package_registry.json"
    _set_argv(monkeypatch, output_path)

    with pytest.raises(urllib.error.URLError):
        verify_package_registry.main()

    assert not output_path.exists()


def test_main_skips_the_check_when_the_existing_output_is_within_the_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output_path = tmp_path / "package_registry.json"
    existing = {
        "ecosystem": "pypi",
        "coordinate": "aspose-jmap-foss",
        "verified": False,
        "checked_at": "2026-01-01T00:00:00+00:00",
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    def _must_not_be_called(coordinate: str) -> bool:
        raise AssertionError("the checker must not be called")

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _must_not_be_called)
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()

    out = capsys.readouterr().out
    assert "skip" in out.lower()
    # The file on disk is untouched - the skip returns before any write.
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_main_checks_again_when_the_existing_output_is_older_than_the_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "package_registry.json"
    output_path.write_text(
        json.dumps(
            {
                "ecosystem": "pypi",
                "coordinate": "aspose-jmap-foss",
                "verified": False,
                "checked_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    stale_time = time.time() - verify_package_registry._SIDECAR_FRESHNESS_SECONDS - 1
    os.utime(output_path, (stale_time, stale_time))

    calls: list[str] = []

    def _checker(coordinate: str) -> bool:
        calls.append(coordinate)
        return True

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _checker)
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()

    assert calls == ["aspose-jmap-foss"]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["verified"] is True


def test_main_treats_a_malformed_existing_output_as_absent_and_checks_live_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "package_registry.json"
    output_path.write_text("{this is not valid json at all", encoding="utf-8")

    calls: list[str] = []

    def _checker(coordinate: str) -> bool:
        calls.append(coordinate)
        return True

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _checker)
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()  # must not raise

    assert calls == ["aspose-jmap-foss"]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["verified"] is True


def test_main_rejects_an_uppercase_family_before_doing_any_network_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_be_called(coordinate: str) -> bool:
        raise AssertionError("the checker must not be called")

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _must_not_be_called)
    output_path = tmp_path / "package_registry.json"
    _set_argv(monkeypatch, output_path, family="JMAP")

    with pytest.raises(ValueError):
        verify_package_registry.main()
    assert not output_path.exists()


# --- load_package_registry_sidecar --------------------------------------------------------------


def test_load_package_registry_sidecar_round_trips_what_main_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: True)
    output_path = tmp_path / "package_registry.json"
    _set_argv(monkeypatch, output_path)

    verify_package_registry.main()

    loaded = verify_package_registry.load_package_registry_sidecar(output_path)
    assert loaded is not None
    assert loaded["ecosystem"] == "pypi"
    assert loaded["coordinate"] == "aspose-jmap-foss"
    assert loaded["verified"] is True


def test_load_package_registry_sidecar_returns_none_when_the_file_is_absent(tmp_path: Path) -> None:
    assert verify_package_registry.load_package_registry_sidecar(tmp_path / "missing.json") is None


def test_load_package_registry_sidecar_raises_when_the_json_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "package_registry.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_package_registry.load_package_registry_sidecar(path)


def test_load_package_registry_sidecar_raises_on_a_present_but_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "package_registry.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        verify_package_registry.load_package_registry_sidecar(path)


def test_load_package_registry_sidecar_raises_when_a_required_key_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "package_registry.json"
    path.write_text(
        json.dumps({"ecosystem": "pypi", "coordinate": "aspose-jmap-foss", "verified": False}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        verify_package_registry.load_package_registry_sidecar(path)
