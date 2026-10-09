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
    install_coordinate_for_verification,
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
    inputs = ProductReferenceInputs(
        manifest_text=PACKAGE_JSON_WITH_STRING_REPOSITORY_TEXT, platform="typescript"
    )
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


# --- install_coordinate_for_verification (G2/TC-275, C2 part 2) ---------------------------------


def test_install_coordinate_for_verification_dotnet_returns_nuget_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=CSPROJ_WITH_PROJECT_URL_TEXT, platform="net")
    assert install_coordinate_for_verification(inputs) == ("nuget", "Aspose.PDF-FOSS")


def test_install_coordinate_for_verification_python_returns_pypi_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    assert install_coordinate_for_verification(inputs) == ("pypi", "aspose-pdf-foss")


def test_install_coordinate_for_verification_js_returns_npm_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=PACKAGE_JSON_TEXT, platform="typescript")
    assert install_coordinate_for_verification(inputs) == ("npm", "aspose-pdf-foss")


def test_install_coordinate_for_verification_java_returns_maven_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=POM_XML_TEXT, platform="java")
    assert install_coordinate_for_verification(inputs) == ("maven", "com.aspose:aspose-pdf-foss")


def test_install_coordinate_for_verification_go_returns_go_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=GO_MOD_TEXT, platform="go")
    assert install_coordinate_for_verification(inputs) == ("go", "github.com/aspose/pdf-foss-for-go")


def test_install_coordinate_for_verification_rust_returns_cargo_coordinate() -> None:
    inputs = ProductReferenceInputs(manifest_text=CARGO_TOML_NO_RUST_VERSION_TEXT, platform="rust")
    assert install_coordinate_for_verification(inputs) == ("cargo", "aspose-pdf-foss")


def test_install_coordinate_for_verification_is_none_for_cpp() -> None:
    """cpp has no package-manager install command already (the "install" section's own cpp
    branch), so there is no ecosystem entry for it and this must answer None, not raise."""
    inputs = ProductReferenceInputs(manifest_text=CMAKE_LISTS_TEXT, platform="cpp")
    assert install_coordinate_for_verification(inputs) is None


def test_install_coordinate_for_verification_is_none_for_an_unsupported_platform() -> None:
    inputs = ProductReferenceInputs(manifest_text=GO_MOD_TEXT, platform="cobol")
    assert install_coordinate_for_verification(inputs) is None


def test_install_coordinate_for_verification_is_none_when_no_manifest_was_provided() -> None:
    inputs = ProductReferenceInputs(manifest_text=None, platform="python")
    assert install_coordinate_for_verification(inputs) is None


def test_install_coordinate_for_verification_is_none_when_the_manifest_field_is_missing() -> None:
    """The "install" branch itself would answer NotAvailable here (no PackageId/AssemblyName) -
    this function must mirror that exactly, never raise."""
    csproj_text = (
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
    )
    inputs = ProductReferenceInputs(manifest_text=csproj_text, platform="net")
    assert install_coordinate_for_verification(inputs) is None


# --- the "install" branch's registry-verification suppression (G2/TC-275, C2 part 2) ------------


def test_install_returns_not_available_when_verification_confirmed_the_current_coordinate_absent() -> None:
    """ "False information is more dangerous than missing information": once ingestion-time
    verification has confirmed THIS EXACT coordinate does not resolve on its real registry, the
    "install" section must stop serving it as confident content."""
    inputs = ProductReferenceInputs(
        manifest_text=PYPROJECT_TEXT,
        platform="python",
        install_verified=False,
        install_verified_coordinate="aspose-pdf-foss",
        install_verified_checked_at="2026-10-08T00:00:00+00:00",
    )
    result = get_product_reference(inputs, "install")
    assert isinstance(result, NotAvailable)
    assert "aspose-pdf-foss" in result.reason
    assert "pypi" in result.reason
    assert "2026-10-08T00:00:00+00:00" in result.reason


def test_install_returns_unmodified_content_when_verification_is_true() -> None:
    inputs = ProductReferenceInputs(
        manifest_text=PYPROJECT_TEXT,
        platform="python",
        install_verified=True,
        install_verified_coordinate="aspose-pdf-foss",
        install_verified_checked_at="2026-10-08T00:00:00+00:00",
    )
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-pdf-foss"


def test_install_returns_unmodified_content_when_never_checked_the_default() -> None:
    inputs = ProductReferenceInputs(manifest_text=PYPROJECT_TEXT, platform="python")
    assert inputs.install_verified is None
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-pdf-foss"


def test_install_returns_unmodified_content_when_verified_false_but_the_coordinate_has_since_changed() -> (
    None
):
    """Guards against serving a stale verification result for a manifest that has since changed:
    install_verified=False only suppresses when install_verified_coordinate still matches the
    coordinate install_coordinate_for_verification would derive right now."""
    inputs = ProductReferenceInputs(
        manifest_text=PYPROJECT_TEXT,
        platform="python",
        install_verified=False,
        install_verified_coordinate="some-other-package-entirely",
        install_verified_checked_at="2026-10-08T00:00:00+00:00",
    )
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-pdf-foss"
