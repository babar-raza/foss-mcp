"""G3/TC-542: ``get_product_reference(section="compatibility")`` states the Java release as ``Java N+``.

The pom states a bare number (``11``, ``17``, ``21``) which a reader cannot tell from a library version;
the C++ answers already read ``C++17`` (TC-471). The raw number is still what the README disagreement
notice (TC-518) compares. Offline and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foss_mcp.extraction.manifest_reader import read_java_manifest
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections

DEMO_MANIFESTS = Path(__file__).resolve().parents[3] / "infra" / "demo-manifests"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _pom(property_name: str, value: str) -> str:
    return (
        "<project><groupId>com.demo</groupId><artifactId>demo</artifactId>"
        f"<properties><{property_name}>{value}</{property_name}></properties></project>"
    )


def _compatibility(manifest: str, platform: str = "java") -> ReferenceContent | NotAvailable:
    return get_product_reference(
        ProductReferenceInputs(manifest_text=manifest, platform=platform), "compatibility"
    )


def _readme(install_text: str) -> ReadmeSections:
    section = ReadmeSection(title="Installation", text=install_text, commit=COMMIT, ordered=True, parts=1)
    return ReadmeSections(limitations=None, install=section, capabilities=None)


def _content(result: ReferenceContent | NotAvailable) -> ReferenceContent:
    assert isinstance(result, ReferenceContent), result
    return result


@pytest.mark.parametrize("release", ["11", "17", "21"])
def test_a_pom_release_17_is_java_17_plus(release: str) -> None:
    result = _content(_compatibility(_pom("maven.compiler.release", release)))
    assert result.text == f"Java {release}+"


def test_a_dotted_old_style_value_keeps_its_dots() -> None:
    assert _content(_compatibility(_pom("maven.compiler.target", "1.8"))).text == "Java 1.8+"
    assert _content(_compatibility(_pom("maven.compiler.source", "1.7.0"))).text == "Java 1.7.0+"


@pytest.mark.parametrize("value", ["${java.version}", "17-ea", "latest", "1.", ".8"])
def test_a_non_numeric_value_is_returned_as_it_is(value: str) -> None:
    result = _content(_compatibility(_pom("maven.compiler.release", value)))
    assert result.text == value.strip()
    assert not result.text.startswith("Java ")


def test_no_compiler_target_is_not_available_with_the_unchanged_reason() -> None:
    result = _compatibility("<project><artifactId>demo</artifactId></project>")
    assert isinstance(result, NotAvailable)
    assert result.reason == "manifest does not state a compiler target version"


def test_no_compiler_target_with_a_readme_never_says_java_plus() -> None:
    result = get_product_reference(
        ProductReferenceInputs(
            manifest_text="<project><artifactId>demo</artifactId></project>", platform="java"
        ),
        "compatibility",
        readme=_readme("Requires nothing."),
    )
    if isinstance(result, ReferenceContent):
        assert not result.text.startswith("Java ")
    else:
        assert result.reason == "manifest does not state a compiler target version"


@pytest.mark.parametrize(
    ("platform", "manifest", "expected"),
    [
        ("python", '[project]\nname = "demo"\nrequires-python = ">=3.10"\n', ">=3.10"),
        ("go", "module x\n\ngo 1.21\n", "1.21"),
        ("cpp", "cmake_minimum_required(VERSION 3.22)\nproject(demo)\nset(CMAKE_CXX_STANDARD 20)\n", "C++20"),
        (
            "net",
            "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
            "net8.0",
        ),
        ("typescript", '{"name": "demo", "engines": {"node": ">=18"}}', ">=18"),
    ],
)
def test_other_platforms_compatibility_is_unchanged(platform: str, manifest: str, expected: str) -> None:
    result = _content(_compatibility(manifest, platform))
    assert result.text == expected
    assert not result.text.startswith("Java ")


# On main d7b594fd the answers of the four remaining Java pilots are 3d "Java 21+", cells "Java 17+", pdf "Java 11+" and
# slides "Java 21+" (re-pinned by TC-551: five -> four, the fifth Java pilot was removed from the product).
JAVA_PILOTS = ["3d", "cells", "pdf", "slides"]


def _sidecar_manifest(family: str) -> str:
    path = DEMO_MANIFESTS / f"product_reference_{family}_java.json"
    return json.loads(path.read_text(encoding="utf-8"))["manifest_text"]


@pytest.mark.parametrize("family", JAVA_PILOTS)
def test_the_committed_java_sidecars_answer_java_n_plus(family: str) -> None:
    manifest = _sidecar_manifest(family)
    number = read_java_manifest(manifest).runtime_min_version
    assert number
    result = _content(_compatibility(manifest))
    assert result.text == f"Java {number}+"
    assert result.text.startswith("Java ") and result.text.endswith("+")


@pytest.mark.parametrize("family", JAVA_PILOTS)
def test_the_install_coordinate_for_verification_is_unchanged_for_java(family: str) -> None:
    manifest = _sidecar_manifest(family)
    java = read_java_manifest(manifest)
    expected = f"{java.group_id}:{java.artifact_id}" if java.group_id else java.artifact_id
    result = _content(
        get_product_reference(ProductReferenceInputs(manifest_text=manifest, platform="java"), "install")
    )
    assert result.text == expected
    assert "Java" not in result.text


def test_the_disagreement_notice_still_compares_the_java_minimum() -> None:
    manifest = _pom("maven.compiler.release", "17")
    inputs = ProductReferenceInputs(manifest_text=manifest, platform="java")

    agreeing = _content(get_product_reference(inputs, "compatibility", readme=_readme("Requires Java 17.")))
    assert agreeing.text == "Java 17+"
    assert agreeing.notice is None or "Sources disagree" not in agreeing.notice

    disagreeing = _content(
        get_product_reference(inputs, "compatibility", readme=_readme("Requires Java 11."))
    )
    assert disagreeing.text == "Java 17+"
    assert disagreeing.notice is not None
    assert "Sources disagree: the packaging manifest says 17; the README says 11." in disagreeing.notice
