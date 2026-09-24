"""Read a product's own packaging manifest and surface its real fields - never flattened.

A .csproj's ``TargetFramework`` is a specific, verifiable claim - ``net8.0``, ``net472``, or a
semicolon-joined multi-target list are each a different fact about what the library runs on.
Reducing any of them to a generic bucket like "supports .NET" would state something the manifest
never actually said.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from base64 import b64decode
from dataclasses import dataclass
from xml.etree import ElementTree

_API_ROOT = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "foss-mcp-extraction"}


@dataclass(frozen=True)
class DotnetManifest:
    """The identity fields a .csproj actually states - verbatim, not a derived summary."""

    target_framework: str
    package_id: str
    version: str


def read_dotnet_manifest(csproj_text: str) -> DotnetManifest:
    """Parse a .csproj's text. ``target_framework`` is exactly what the file states: a single
    moniker (``TargetFramework``) or the raw semicolon-joined list (``TargetFrameworks``) -
    never split, summarized, or reduced to "supports .NET".
    """
    root = ElementTree.fromstring(csproj_text)

    def _field(tag: str) -> str:
        element = root.find(f".//{tag}")
        return element.text.strip() if element is not None and element.text else ""

    target_framework = _field("TargetFramework") or _field("TargetFrameworks")
    return DotnetManifest(
        target_framework=target_framework,
        package_id=_field("PackageId"),
        version=_field("Version"),
    )


def _load_toml(text: str) -> dict:
    """Best-effort TOML parse. Returns ``{}`` on any parse failure - callers degrade to
    empty fields rather than raising, exactly like ``read_dotnet_manifest``'s XML path
    is expected to for a well-formed document (a malformed one is the caller's problem,
    not this reader's).
    """
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class PythonManifest:
    """The identity fields a Python package manifest actually states - verbatim."""

    name: str = ""
    version: str = ""
    license: str = ""
    requires_python: str = ""


def read_python_manifest(text: str) -> PythonManifest:
    """Parse pyproject.toml text (``[project]`` table). If no ``name`` is found there -
    either because the text is not TOML at all, or because it is a ``[tool.*]``-only
    pyproject with no ``[project]`` table - fall back to the ``setup.py`` regex style
    the reference implementation uses, applied to the same text. Adapted from
    ``package_manifest._parse_python_manifest``; the file-discovery (trying
    ``pyproject.toml`` then ``setup.py`` on disk) is dropped since this takes text, not
    a repo.
    """
    data = _load_toml(text)
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    name = project.get("name") or ""
    version = project.get("version") or ""
    license_value = project.get("license", "")
    if isinstance(license_value, dict):
        license_value = license_value.get("text") or license_value.get("file") or ""
    if not isinstance(license_value, str):
        license_value = ""
    requires_python = project.get("requires-python") or ""

    if not name:
        match = re.search(r'name\s*=\s*["\']([^"\']+)', text)
        if match:
            name = match.group(1)
        match = re.search(r'version\s*=\s*["\']([^"\']+)', text)
        if match:
            version = match.group(1)

    return PythonManifest(
        name=name if isinstance(name, str) else "",
        version=version if isinstance(version, str) else "",
        license=license_value,
        requires_python=requires_python if isinstance(requires_python, str) else "",
    )


@dataclass(frozen=True)
class JavaManifest:
    """The identity fields a Maven or Gradle manifest actually states - verbatim."""

    group_id: str = ""
    artifact_id: str = ""
    version: str = ""
    runtime_min_version: str = ""


def read_java_manifest(text: str) -> JavaManifest:
    """Parse pom.xml-style text via regex; where a field is still missing, try the
    build.gradle-style patterns against the same text. Adapted from
    ``package_manifest._parse_java_manifest``; the on-disk pom.xml/build.gradle
    discovery is dropped since this takes text, not a repo.
    """
    group_id = ""
    artifact_id = ""
    version = ""
    runtime_min_version = ""

    for tag in ("groupId", "artifactId", "version"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", text)
        if not match:
            continue
        value = match.group(1).strip()
        if tag == "groupId":
            group_id = value
        elif tag == "artifactId":
            artifact_id = value
        else:
            version = value

    for prop_tag in ("maven.compiler.release", "maven.compiler.target", "maven.compiler.source"):
        match = re.search(rf"<{re.escape(prop_tag)}>(.*?)</{re.escape(prop_tag)}>", text)
        if match:
            runtime_min_version = match.group(1).strip()
            break

    if not artifact_id:
        match = re.search(r"group\s*=\s*['\"]([^'\"]+)", text)
        if match:
            group_id = match.group(1)
        match = re.search(r"version\s*=\s*['\"]([^'\"]+)", text)
        if match:
            version = match.group(1)

    if not runtime_min_version:
        for pattern in (
            r"targetCompatibility\s*=\s*['\"]?(\d+)['\"]?",
            r"sourceCompatibility\s*=\s*['\"]?(\d+)['\"]?",
            r"languageVersion\.of\((\d+)\)",
        ):
            match = re.search(pattern, text)
            if match:
                runtime_min_version = match.group(1).strip()
                break

    return JavaManifest(
        group_id=group_id, artifact_id=artifact_id, version=version, runtime_min_version=runtime_min_version
    )


@dataclass(frozen=True)
class JsManifest:
    """The identity fields a package.json actually states - verbatim."""

    name: str = ""
    version: str = ""
    license: str = ""
    dependencies: tuple[str, ...] = ()
    engines_node: str = ""


def read_js_manifest(text: str) -> JsManifest:
    """Parse package.json text via ``json.loads``. Adapted from
    ``package_manifest._parse_js_manifest``; a malformed or non-object document
    degrades to empty fields rather than raising.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return JsManifest()
    if not isinstance(data, dict):
        return JsManifest()

    name = data.get("name", "") or ""
    version = data.get("version", "") or ""
    license_value = data.get("license", "") or ""
    if not isinstance(license_value, str):
        license_value = ""
    dependencies_raw = data.get("dependencies") or {}
    dependencies = tuple(dependencies_raw.keys()) if isinstance(dependencies_raw, dict) else ()
    engines = data.get("engines") or {}
    engines_node = engines.get("node", "") if isinstance(engines, dict) else ""

    return JsManifest(
        name=name if isinstance(name, str) else "",
        version=version if isinstance(version, str) else "",
        license=license_value,
        dependencies=dependencies,
        engines_node=engines_node if isinstance(engines_node, str) else "",
    )


@dataclass(frozen=True)
class GoManifest:
    """The identity fields a go.mod actually states - verbatim."""

    name: str = ""
    go_version: str = ""


def read_go_manifest(text: str) -> GoManifest:
    """Parse go.mod text via regex: the module path and the go version directive.
    Adapted from ``package_manifest._parse_go_manifest``; the source-tree package-name
    enrichment (``_extract_go_package_name`` / ``_detect_go_package_subpath``) is
    dropped per the card's instruction - it scans every .go file in the repo and does
    not fit a single-text-argument convention.
    """
    name = ""
    go_version = ""
    match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    if match:
        name = match.group(1)
    match = re.search(r"^go\s+([\d.]+)", text, re.MULTILINE)
    if match:
        go_version = match.group(1)
    return GoManifest(name=name, go_version=go_version)


@dataclass(frozen=True)
class RustManifest:
    """The identity fields a Cargo.toml actually states - verbatim."""

    name: str = ""
    version: str = ""
    license: str = ""
    rust_version: str = ""


def read_rust_manifest(text: str) -> RustManifest:
    """Parse Cargo.toml text's ``[package]`` table. Adapted from
    ``package_manifest._parse_rust_manifest``; the workspace/nested-crate on-disk
    fallback is dropped since this takes text, not a repo. ``rust_version`` is left
    empty - never fabricated - when the manifest states no ``rust-version`` field.
    """
    data = _load_toml(text)
    package = data.get("package", {}) if isinstance(data.get("package"), dict) else {}

    name = package.get("name", "")
    name = name if isinstance(name, str) else ""
    version = package.get("version", "")
    version = version if isinstance(version, str) else ""
    license_value = package.get("license", "")
    license_value = license_value if isinstance(license_value, str) else ""
    rust_version = package.get("rust-version", "")
    rust_version = str(rust_version) if rust_version else ""

    return RustManifest(name=name, version=version, license=license_value, rust_version=rust_version)


@dataclass(frozen=True)
class CppManifest:
    """The identity fields a CMakeLists.txt actually states - verbatim."""

    name: str = ""
    version: str = ""
    cmake_min_version: str = ""
    library_target: str = ""
    cpp_standard: str = ""


def read_cpp_manifest(text: str) -> CppManifest:
    """Parse CMakeLists.txt text via regex. Adapted from
    ``package_manifest._parse_cpp_manifest``; identical field set (name, version,
    cmake_min_version, library_target, cpp_standard).
    """
    name = ""
    version = ""
    cmake_min_version = ""
    library_target = ""
    cpp_standard = ""

    match = re.search(r"project\s*\(\s*(\S+)(?:\s+VERSION\s+(\S+))?", text, re.IGNORECASE)
    if match:
        name = match.group(1)
        if match.group(2):
            version = match.group(2).rstrip(")")

    match = re.search(r"cmake_minimum_required\s*\(\s*VERSION\s+([\d.]+)", text, re.IGNORECASE)
    if match:
        cmake_min_version = match.group(1)

    match = re.search(r"add_library\s*\(\s*([A-Za-z][A-Za-z0-9_.-]*)\s+", text, re.IGNORECASE)
    if match:
        library_target = match.group(1)

    match = re.search(
        r"set_property\s*\([^)]*CXX_STANDARD\s+(\d+)|CMAKE_CXX_STANDARD\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if match:
        cpp_standard = match.group(1) or match.group(2) or ""

    return CppManifest(
        name=name,
        version=version,
        cmake_min_version=cmake_min_version,
        library_target=library_target,
        cpp_standard=cpp_standard,
    )


def fetch_manifest_file(
    repository: str, path: str, *, ref: str | None = None, etag: str | None = None
) -> str:
    """The raw text of *path* (a manifest file) in *repository* at *ref*, via the Contents API.

    Sends *etag* as ``If-None-Match``; the caller decides what "unchanged" means for its own
    cached copy (this raises rather than silently returning stale text on a 304).
    """
    url = f"{_API_ROOT}/repos/{repository}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    headers = dict(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return b64decode(payload["content"]).decode("utf-8")
