"""get_product_reference dispatches compatibility/install/license/support by
``ProductReferenceInputs.platform`` instead of unconditionally calling the
.NET-specific readers.

``platform`` defaults to ``"net"`` so every existing caller - including TC-017c's
accepted holdout, which constructs ``ProductReferenceInputs`` with no ``platform``
argument at all - keeps its exact current behaviour. This file exercises the new
non-.NET branches: each one must return the ecosystem's REAL verbatim value (never
flattened, never fabricated), and a platform this tool does not know about must be
refused with an explicit ``NotAvailable`` rather than a guess.
"""

from __future__ import annotations

from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

PYPROJECT_TEXT = """
[project]
name = "aspose-pdf-foss"
version = "26.9.0"
license = { text = "MIT" }
requires-python = ">=3.9"
"""

POM_XML_TEXT = """
<project>
  <groupId>com.aspose</groupId>
  <artifactId>aspose-pdf-foss</artifactId>
  <version>26.9.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
  </properties>
</project>
"""

PACKAGE_JSON_TEXT = """
{
  "name": "aspose-pdf-foss",
  "version": "26.9.0",
  "license": "MIT",
  "engines": {"node": ">=18"}
}
"""

GO_MOD_TEXT = """
module github.com/aspose/pdf-foss-for-go

go 1.21
"""

CARGO_TOML_NO_RUST_VERSION_TEXT = """
[package]
name = "aspose-pdf-foss"
version = "26.9.0"
license = "MIT"
"""

CMAKE_LISTS_TEXT = """
cmake_minimum_required(VERSION 3.20)
project(AsposePdfFoss VERSION 26.9.0)
set_property(TARGET aspose_pdf_foss PROPERTY CXX_STANDARD 17)
"""


def test_python_compatibility_returns_requires_python_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.9"


def test_java_compatibility_returns_runtime_min_version_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=POM_XML_TEXT, platform="java")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == "17"


def test_typescript_compatibility_returns_engines_node_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_TEXT, platform="typescript")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=18"


def test_go_compatibility_returns_go_version_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=GO_MOD_TEXT, platform="go")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == "1.21"


def test_cpp_compatibility_returns_cpp_standard_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=CMAKE_LISTS_TEXT, platform="cpp")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == "17"


def test_rust_compatibility_is_not_available_when_manifest_states_no_rust_version() -> None:
    """rust-version is optional in Cargo.toml; absence must stay NotAvailable, never a guess."""
    inputs = ProductReferenceInputs(manifest_text=CARGO_TOML_NO_RUST_VERSION_TEXT, platform="rust")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, NotAvailable)
    assert "rust-version" in result.reason


def test_python_install_returns_pip_install_command() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-pdf-foss"


def test_js_install_returns_npm_install_command() -> None:
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_TEXT, platform="javascript")
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "npm install aspose-pdf-foss"


def test_java_install_returns_maven_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=POM_XML_TEXT, platform="java")
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "com.aspose:aspose-pdf-foss"


def test_python_license_returns_manifest_value() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    result = get_product_reference(inputs, "license")
    assert isinstance(result, ReferenceContent)
    assert result.text == "MIT"


def test_unhandled_platform_compatibility_is_not_available_never_a_guess() -> None:
    inputs = ProductReferenceInputs(manifest_text=GO_MOD_TEXT, platform="cobol")
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, NotAvailable)
    assert "cobol" in result.reason


def test_default_platform_is_still_net_and_uses_the_dotnet_reader() -> None:
    """No platform argument at all - TC-017c's holdout's exact calling convention -
    must still dispatch to the .NET reader."""
    csproj_text = (
        "<Project><PropertyGroup>"
        "<TargetFramework>net8.0</TargetFramework>"
        "<PackageId>Aspose.PDF-FOSS</PackageId>"
        "</PropertyGroup></Project>"
    )
    inputs = ProductReferenceInputs(manifest_text=csproj_text)
    assert inputs.platform == "net"
    result = get_product_reference(inputs, "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == "net8.0"
