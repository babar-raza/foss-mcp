"""G3/TC-353: when ingestion-time verification found a package coordinate absent from its real
registry, ``get_product_reference(section="install")`` still returns a TRUE answer - the registry
package was not found, here is the repository and the exact commit to build from - and never
presents the unresolved coordinate as an install command.

Before this card the answer was an explicit absence (``NotAvailable``). That stays exactly as it was
whenever the repository or the commit is not known; this file pins both sides, once per ecosystem
the tool supports (npm, PyPI, NuGet, Go, crates.io, Maven), because the live proof's check 7 reads
the returned object's ``text`` and an empty or absent one fails the pilot.
"""

from __future__ import annotations

import pytest

from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

CHECKED_AT = "2026-10-11T08:00:00+00:00"
REPOSITORY = "aspose-3d-foss/Aspose.3D-FOSS"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"

# Every way the tool could offer a registry coordinate as something to run. None may appear in the
# unresolved answer, in any letter case.
INSTALL_COMMAND_PREFIXES = (
    "npm install",
    "npm i ",
    "pip install",
    "dotnet add package",
    "go get",
    "go install",
    "cargo add",
    "cargo install",
    "mvn",
    "gradle",
)

CSPROJ_TEXT = "<Project><PropertyGroup><PackageId>Aspose.3D.FOSS</PackageId></PropertyGroup></Project>"
PYPROJECT_TEXT = '[project]\nname = "aspose-3d-foss"\nversion = "26.9.0"\n'
PACKAGE_JSON_TEXT = '{"name": "@aspose/3d", "version": "26.9.0"}'
GO_MOD_TEXT = "module github.com/aspose-3d-foss/aspose-3d-foss-for-go\n\ngo 1.21\n"
CARGO_TOML_TEXT = '[package]\nname = "aspose-3d-foss"\nversion = "26.9.0"\n'
POM_XML_TEXT = (
    "<project><groupId>com.aspose</groupId><artifactId>aspose-3d-foss</artifactId>"
    "<version>26.9.0</version></project>"
)

# (platform, registry name the answer must carry, manifest text, bare coordinate, the original
# install text served when the coordinate is not known to be absent)
ECOSYSTEMS = [
    pytest.param("typescript", "npm", PACKAGE_JSON_TEXT, "@aspose/3d", "npm install @aspose/3d", id="npm"),
    pytest.param("python", "pypi", PYPROJECT_TEXT, "aspose-3d-foss", "pip install aspose-3d-foss", id="pypi"),
    pytest.param(
        "net", "nuget", CSPROJ_TEXT, "Aspose.3D.FOSS", "dotnet add package Aspose.3D.FOSS", id="nuget"
    ),
    pytest.param(
        "go",
        "go",
        GO_MOD_TEXT,
        "github.com/aspose-3d-foss/aspose-3d-foss-for-go",
        "go get github.com/aspose-3d-foss/aspose-3d-foss-for-go",
        id="go",
    ),
    pytest.param("rust", "cargo", CARGO_TOML_TEXT, "aspose-3d-foss", "cargo add aspose-3d-foss", id="cargo"),
    pytest.param(
        "java", "maven", POM_XML_TEXT, "com.aspose:aspose-3d-foss", "com.aspose:aspose-3d-foss", id="maven"
    ),
]


def _inputs(
    platform: str,
    manifest_text: str,
    coordinate: str,
    *,
    verified: bool | None = False,
    verified_coordinate: str | None = None,
    source_repository: str | None = REPOSITORY,
    source_ref: str | None = COMMIT,
) -> ProductReferenceInputs:
    return ProductReferenceInputs(
        manifest_text=manifest_text,
        platform=platform,
        install_verified=verified,
        install_verified_coordinate=coordinate if verified_coordinate is None else verified_coordinate,
        install_verified_checked_at=CHECKED_AT,
        source_repository=source_repository,
        source_ref=source_ref,
    )


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_an_unresolved_coordinate_gets_the_repository_the_commit_and_no_command(
    platform: str, registry: str, manifest: str, coordinate: str, original: str
) -> None:
    result = get_product_reference(_inputs(platform, manifest, coordinate), "install")

    assert isinstance(result, ReferenceContent)
    assert result.section == "install"
    assert result.text.strip()  # the live proof's check 7 reads text
    assert REPOSITORY_URL in result.text
    assert COMMIT in result.text
    assert "not found" in result.text
    assert registry in result.text
    assert CHECKED_AT in result.text
    assert coordinate in result.text  # named to say it was not found, never as a command
    assert "build" in result.text
    assert "README" in result.text

    lowered = result.text.lower()
    for prefix in INSTALL_COMMAND_PREFIXES:
        assert prefix not in lowered, f"{prefix!r} must not appear in the unresolved answer"
    if original != coordinate:  # maven's original install text IS the bare coordinate, named in prose
        assert original not in result.text
    assert "```" not in result.text


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_the_unresolved_coordinate_is_never_inside_a_code_block(
    platform: str, registry: str, manifest: str, coordinate: str, original: str
) -> None:
    del registry, original
    result = get_product_reference(_inputs(platform, manifest, coordinate), "install")

    assert isinstance(result, ReferenceContent)
    in_fence = False
    for line in result.text.splitlines():
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        assert not (in_fence and coordinate in line)
    assert not in_fence


def test_a_repository_that_is_already_a_url_is_used_as_given() -> None:
    inputs = _inputs(
        "typescript",
        PACKAGE_JSON_TEXT,
        "@aspose/3d",
        source_repository="https://github.com/aspose-3d-foss/Aspose.3D-FOSS-for-TypeScript",
    )

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert "https://github.com/aspose-3d-foss/Aspose.3D-FOSS-for-TypeScript" in result.text
    assert "https://github.com/https://" not in result.text


def test_an_unresolved_coordinate_with_no_checked_at_still_answers() -> None:
    inputs = ProductReferenceInputs(
        manifest_text=PACKAGE_JSON_TEXT,
        platform="typescript",
        install_verified=False,
        install_verified_coordinate="@aspose/3d",
        install_verified_checked_at=None,
        source_repository=REPOSITORY,
        source_ref=COMMIT,
    )

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert "None" not in result.text
    assert "not found" in result.text
    assert "npm" in result.text
    assert REPOSITORY_URL in result.text
    assert COMMIT in result.text


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
@pytest.mark.parametrize(
    ("source_repository", "source_ref"),
    [
        pytest.param(None, COMMIT, id="no-repository"),
        pytest.param(REPOSITORY, None, id="no-commit"),
        pytest.param(None, None, id="neither"),
        pytest.param("", COMMIT, id="empty-repository"),
        pytest.param(REPOSITORY, "", id="empty-commit"),
    ],
)
def test_an_unresolved_coordinate_without_a_known_repository_or_commit_keeps_the_explicit_absence(
    platform: str,
    registry: str,
    manifest: str,
    coordinate: str,
    original: str,
    source_repository: str | None,
    source_ref: str | None,
) -> None:
    del original
    inputs = _inputs(
        platform,
        manifest,
        coordinate,
        source_repository=source_repository,
        source_ref=source_ref,
    )

    result = get_product_reference(inputs, "install")

    assert isinstance(result, NotAvailable)
    assert result.section == "install"
    assert result.reason == (
        f"{coordinate} ({registry}) was checked against its real registry at {CHECKED_AT} "
        f"and does not resolve there"
    )


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
@pytest.mark.parametrize("verified", [True, None])
def test_a_verified_or_never_checked_coordinate_is_served_exactly_as_before(
    platform: str, registry: str, manifest: str, coordinate: str, original: str, verified: bool | None
) -> None:
    del registry
    result = get_product_reference(_inputs(platform, manifest, coordinate, verified=verified), "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == original
    assert REPOSITORY_URL not in result.text
    assert COMMIT not in result.text


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_a_coordinate_that_changed_since_verification_is_served_as_before(
    platform: str, registry: str, manifest: str, coordinate: str, original: str
) -> None:
    del registry
    inputs = _inputs(platform, manifest, coordinate, verified_coordinate="some-other-package-entirely")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == original


def test_cpp_is_unchanged_even_when_verification_says_false_and_the_repository_is_known() -> None:
    cmake = "cmake_minimum_required(VERSION 3.20)\nproject(Aspose3dFoss VERSION 26.9.0)\n"
    inputs = _inputs("cpp", cmake, "Aspose3dFoss")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, NotAvailable)
    assert result.reason == "cpp has no package-manager install command"


@pytest.mark.parametrize(("platform", "registry", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_only_the_install_section_is_affected_by_the_source_pin(
    platform: str, registry: str, manifest: str, coordinate: str, original: str
) -> None:
    """The pin is read by the install branch alone: the other manifest-derived sections answer
    exactly what they answer without it."""
    del registry, original
    with_pin = _inputs(platform, manifest, coordinate)
    without_pin = _inputs(platform, manifest, coordinate, source_repository=None, source_ref=None)

    for section in ("compatibility", "license", "support"):
        assert get_product_reference(with_pin, section) == get_product_reference(without_pin, section)
