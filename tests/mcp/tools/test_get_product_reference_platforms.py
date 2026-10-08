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

PYPROJECT_WITH_HOMEPAGE_TEXT = """
[project]
name = "aspose-pdf-foss"
version = "26.9.0"

[project.urls]
Homepage = "https://github.com/aspose/pdf-foss-for-python"
Repository = "https://github.com/aspose/pdf-foss-for-python.git"
"""

PYPROJECT_WITH_POETRY_HOMEPAGE_TEXT = """
[tool.poetry]
name = "aspose-pdf-foss"
version = "26.9.0"
homepage = "https://github.com/aspose/pdf-foss-for-python"
repository = "https://github.com/aspose/pdf-foss-for-python.git"
"""

PACKAGE_JSON_WITH_HOMEPAGE_TEXT = """
{
  "name": "aspose-pdf-foss",
  "version": "26.9.0",
  "homepage": "https://github.com/aspose/pdf-foss-for-js",
  "repository": {"type": "git", "url": "https://github.com/aspose/pdf-foss-for-js.git"}
}
"""

PACKAGE_JSON_WITH_STRING_REPOSITORY_TEXT = """
{
  "name": "aspose-pdf-foss",
  "version": "26.9.0",
  "repository": "https://github.com/aspose/pdf-foss-for-js.git"
}
"""

POM_XML_WITH_URL_TEXT = """
<project>
  <groupId>com.aspose</groupId>
  <artifactId>aspose-pdf-foss</artifactId>
  <version>26.9.0</version>
  <url>https://github.com/aspose/pdf-foss-for-java</url>
</project>
"""

CARGO_TOML_WITH_HOMEPAGE_TEXT = """
[package]
name = "aspose-pdf-foss"
version = "26.9.0"
homepage = "https://github.com/aspose/pdf-foss-for-rust"
repository = "https://github.com/aspose/pdf-foss-for-rust.git"
"""

CMAKE_LISTS_WITH_HOMEPAGE_URL_TEXT = """
cmake_minimum_required(VERSION 3.20)
project(AsposePdfFoss VERSION 26.9.0 HOMEPAGE_URL "https://github.com/aspose/pdf-foss-for-cpp")
set_property(TARGET aspose_pdf_foss PROPERTY CXX_STANDARD 17)
"""

CSPROJ_WITH_PROJECT_URL_TEXT = (
    "<Project><PropertyGroup>"
    "<TargetFramework>net8.0</TargetFramework>"
    "<PackageId>Aspose.PDF-FOSS</PackageId>"
    "<PackageProjectUrl>https://github.com/aspose/pdf-foss-for-net</PackageProjectUrl>"
    "</PropertyGroup></Project>"
)


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


# --- section == "support" (TC-254: B09 - previously unconditional NotAvailable for
# every non-.NET platform, and zero test coverage for any platform, including .NET) ---


def test_dotnet_support_returns_package_project_url_verbatim() -> None:
    """Characterization test for the EXISTING .NET behaviour: there was no test for
    this at all before TC-254, despite the .NET branch being the one platform that
    already worked. Pins today's real behaviour so a future change cannot silently
    break it unnoticed."""
    inputs = ProductReferenceInputs(manifest_text=CSPROJ_WITH_PROJECT_URL_TEXT)
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-net"


def test_dotnet_support_is_not_available_when_manifest_states_no_project_url() -> None:
    csproj_text = (
        "<Project><PropertyGroup>"
        "<TargetFramework>net8.0</TargetFramework>"
        "<PackageId>Aspose.PDF-FOSS</PackageId>"
        "</PropertyGroup></Project>"
    )
    inputs = ProductReferenceInputs(manifest_text=csproj_text)
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_python_support_returns_homepage_from_project_urls() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_WITH_HOMEPAGE_TEXT, platform="python")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-python"


def test_python_support_falls_back_to_poetry_homepage() -> None:
    """No [project.urls] table at all - the legacy [tool.poetry] homepage must still
    be read, in priority order after project.urls.Homepage/Repository."""
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_WITH_POETRY_HOMEPAGE_TEXT, platform="python")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-python"


def test_python_support_is_not_available_when_manifest_states_no_project_url() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_js_support_returns_homepage_over_repository() -> None:
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_WITH_HOMEPAGE_TEXT, platform="javascript")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-js"


def test_js_support_handles_string_repository_shape() -> None:
    """package.json's "repository" field may be a bare string instead of an
    {"url": ...} object - both shapes must be handled."""
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_WITH_STRING_REPOSITORY_TEXT, platform="typescript")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-js.git"


def test_js_support_is_not_available_when_manifest_states_no_project_url() -> None:
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_TEXT, platform="js")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_java_support_returns_first_url_tag_verbatim() -> None:
    inputs = ProductReferenceInputs(manifest_text=POM_XML_WITH_URL_TEXT, platform="java")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-java"


def test_java_support_is_not_available_when_manifest_states_no_url() -> None:
    inputs = ProductReferenceInputs(manifest_text=POM_XML_TEXT, platform="java")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_rust_support_returns_homepage_over_repository() -> None:
    inputs = ProductReferenceInputs(manifest_text=CARGO_TOML_WITH_HOMEPAGE_TEXT, platform="rust")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-rust"


def test_rust_support_is_not_available_when_manifest_states_no_project_url() -> None:
    inputs = ProductReferenceInputs(manifest_text=CARGO_TOML_NO_RUST_VERSION_TEXT, platform="rust")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_cpp_support_returns_homepage_url_from_project_command() -> None:
    """CMake's project() command has accepted HOMEPAGE_URL since CMake 3.12 - a real,
    standard field, unlike go.mod, which has no equivalent."""
    inputs = ProductReferenceInputs(manifest_text=CMAKE_LISTS_WITH_HOMEPAGE_URL_TEXT, platform="cpp")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, ReferenceContent)
    assert result.text == "https://github.com/aspose/pdf-foss-for-cpp"


def test_cpp_support_is_not_available_when_manifest_states_no_homepage_url() -> None:
    inputs = ProductReferenceInputs(manifest_text=CMAKE_LISTS_TEXT, platform="cpp")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason


def test_go_support_is_not_available_go_mod_has_no_equivalent_field() -> None:
    """go.mod has no standard homepage/repository field - its module path is already
    surfaced by the "install" section, so support stays an honest NotAvailable."""
    inputs = ProductReferenceInputs(manifest_text=GO_MOD_TEXT, platform="go")
    result = get_product_reference(inputs, "support")
    assert isinstance(result, NotAvailable)
    assert "project url" in result.reason
