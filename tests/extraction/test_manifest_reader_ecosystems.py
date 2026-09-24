"""Text-based manifest parsers for the six non-.NET ecosystems.

Each parser follows the convention ``read_dotnet_manifest`` already established: a
function taking raw manifest TEXT (already fetched elsewhere - never a repo Path)
and returning a frozen dataclass of identity fields, stated verbatim. Their parsing
logic is adapted from the repo-Path-based reference implementations in
``tree_sitter_engine/package_manifest.py`` - only the field extraction, not the
on-disk file discovery those originals also do.

Every parser must also degrade gracefully: malformed or empty text yields empty
fields, never a raised exception - a caller has no repo to fall back to here.
"""

from __future__ import annotations

from foss_mcp.extraction.manifest_reader import (
    CppManifest,
    GoManifest,
    JavaManifest,
    JsManifest,
    PythonManifest,
    RustManifest,
    read_cpp_manifest,
    read_go_manifest,
    read_java_manifest,
    read_js_manifest,
    read_python_manifest,
    read_rust_manifest,
)

PYPROJECT_TEXT = """
[project]
name = "aspose-pdf-foss"
version = "26.9.0"
license = { text = "MIT" }
requires-python = ">=3.9"
dependencies = ["numpy"]
"""

SETUP_PY_TEXT = """
from setuptools import setup

setup(
    name="legacy-widget",
    version="1.2.3",
)
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

BUILD_GRADLE_TEXT = """
group = 'com.aspose'
version = '26.9.0'

java {
    targetCompatibility = '11'
}
"""

PACKAGE_JSON_TEXT = """
{
  "name": "aspose-pdf-foss",
  "version": "26.9.0",
  "license": "MIT",
  "dependencies": {"lodash": "^4.17.21", "chalk": "^5.0.0"},
  "engines": {"node": ">=18"}
}
"""

GO_MOD_TEXT = """
module github.com/aspose/pdf-foss-for-go

go 1.21

require github.com/stretchr/testify v1.9.0
"""

CARGO_TOML_TEXT = """
[package]
name = "aspose-pdf-foss"
version = "26.9.0"
license = "MIT"
rust-version = "1.75"

[dependencies]
serde = "1.0"
"""

CMAKE_LISTS_TEXT = """
cmake_minimum_required(VERSION 3.20)
project(AsposePdfFoss VERSION 26.9.0)

add_library(aspose_pdf_foss SHARED src/lib.cpp)
set_property(TARGET aspose_pdf_foss PROPERTY CXX_STANDARD 17)
"""


def test_python_manifest_reads_pyproject_project_table() -> None:
    manifest = read_python_manifest(PYPROJECT_TEXT)
    assert manifest == PythonManifest(
        name="aspose-pdf-foss", version="26.9.0", license="MIT", requires_python=">=3.9"
    )


def test_python_manifest_falls_back_to_setup_py_regex_when_no_name_found() -> None:
    manifest = read_python_manifest(SETUP_PY_TEXT)
    assert manifest.name == "legacy-widget"
    assert manifest.version == "1.2.3"


def test_python_manifest_degrades_to_empty_fields_on_malformed_text() -> None:
    manifest = read_python_manifest("not valid toml {{{ nor setup.py")
    assert manifest == PythonManifest()


def test_java_manifest_reads_pom_xml() -> None:
    manifest = read_java_manifest(POM_XML_TEXT)
    assert manifest == JavaManifest(
        group_id="com.aspose",
        artifact_id="aspose-pdf-foss",
        version="26.9.0",
        runtime_min_version="17",
    )


def test_java_manifest_falls_back_to_gradle_style_fields() -> None:
    manifest = read_java_manifest(BUILD_GRADLE_TEXT)
    assert manifest.group_id == "com.aspose"
    assert manifest.version == "26.9.0"
    assert manifest.runtime_min_version == "11"


def test_java_manifest_degrades_to_empty_fields_on_empty_text() -> None:
    assert read_java_manifest("") == JavaManifest()


def test_js_manifest_reads_package_json() -> None:
    manifest = read_js_manifest(PACKAGE_JSON_TEXT)
    assert manifest.name == "aspose-pdf-foss"
    assert manifest.version == "26.9.0"
    assert manifest.license == "MIT"
    assert set(manifest.dependencies) == {"lodash", "chalk"}
    assert manifest.engines_node == ">=18"


def test_js_manifest_degrades_to_empty_fields_on_malformed_json() -> None:
    assert read_js_manifest("{not json") == JsManifest()


def test_go_manifest_reads_go_mod() -> None:
    manifest = read_go_manifest(GO_MOD_TEXT)
    assert manifest == GoManifest(name="github.com/aspose/pdf-foss-for-go", go_version="1.21")


def test_go_manifest_degrades_to_empty_fields_on_empty_text() -> None:
    assert read_go_manifest("") == GoManifest()


def test_rust_manifest_reads_cargo_toml() -> None:
    manifest = read_rust_manifest(CARGO_TOML_TEXT)
    assert manifest == RustManifest(
        name="aspose-pdf-foss", version="26.9.0", license="MIT", rust_version="1.75"
    )


def test_rust_manifest_leaves_rust_version_empty_when_manifest_states_none() -> None:
    text = '[package]\nname = "widget"\nversion = "0.1.0"\n'
    manifest = read_rust_manifest(text)
    assert manifest.name == "widget"
    assert manifest.rust_version == "", "a rust-version the manifest never stated must not be fabricated"


def test_rust_manifest_degrades_to_empty_fields_on_malformed_toml() -> None:
    assert read_rust_manifest("not [ valid toml") == RustManifest()


def test_cpp_manifest_reads_cmake_lists() -> None:
    manifest = read_cpp_manifest(CMAKE_LISTS_TEXT)
    assert manifest == CppManifest(
        name="AsposePdfFoss",
        version="26.9.0",
        cmake_min_version="3.20",
        library_target="aspose_pdf_foss",
        cpp_standard="17",
    )


def test_cpp_manifest_degrades_to_empty_fields_on_empty_text() -> None:
    assert read_cpp_manifest("") == CppManifest()
