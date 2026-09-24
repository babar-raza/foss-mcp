"""get_product_reference: install/formats/limitations/compatibility/license/support/
contributing/agent_guidance - real content or an explicit absence, never a fabricated summary.

compatibility comes from the repo's OWN packaging manifest (``manifest_reader``) - for
pdf/net a .csproj - and is never flattened across products: the exact ``TargetFramework`` a
manifest states, not a generic "supports .NET". contributing and agent_guidance come from
``repo_native_reader``'s honest presence/absence result: most repos lack both, and a caller
gets an explicit ``NotAvailable``, never an invented paragraph standing in for a file that was
never there.

This tool does no network I/O itself - ``ProductReferenceInputs`` carries whatever
``foss_mcp.extraction``'s readers already fetched (offline at verification; TC-012's readers
do the live reads elsewhere). ``formats`` and ``limitations`` have no data source anywhere in
this project yet, so they are honestly ``NotAvailable`` too - the same rule that protects
contributing/agent_guidance from fabrication applies uniformly, not only where a real file
happens to be missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from foss_mcp.extraction.manifest_reader import (
    read_cpp_manifest,
    read_dotnet_manifest,
    read_go_manifest,
    read_java_manifest,
    read_js_manifest,
    read_python_manifest,
    read_rust_manifest,
)
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentResult

_DOTNET_PLATFORMS = ("net", "dotnet")
_JS_PLATFORMS = ("typescript", "javascript", "js", "nodejs")
_KNOWN_PLATFORMS = _DOTNET_PLATFORMS + ("python", "java") + _JS_PLATFORMS + ("go", "rust", "cpp")

Section = Literal[
    "install",
    "formats",
    "limitations",
    "compatibility",
    "license",
    "support",
    "contributing",
    "agent_guidance",
]
SECTIONS: tuple[Section, ...] = (
    "install",
    "formats",
    "limitations",
    "compatibility",
    "license",
    "support",
    "contributing",
    "agent_guidance",
)


@dataclass(frozen=True)
class ReferenceContent:
    section: Section
    text: str


@dataclass(frozen=True)
class NotAvailable:
    """An explicit absence - never a fabricated summary."""

    section: Section
    reason: str


@dataclass(frozen=True)
class ProductReferenceInputs:
    """Everything ``get_product_reference`` needs, already fetched by
    ``foss_mcp.extraction``'s readers - this tool never fetches anything itself.
    """

    manifest_text: str | None = None
    contributing: DocumentResult | None = None
    agent_guidance: DocumentResult | None = None
    platform: str = "net"


def _xml_field(manifest_text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", manifest_text, re.DOTALL)
    return match.group(1).strip() if match else None


def _document_section(section: Section, document: DocumentResult | None) -> ReferenceContent | NotAvailable:
    if document is None or isinstance(document, DocumentNotPresent):
        return NotAvailable(section, f"{section} is not present in this repository")
    return ReferenceContent(section, document.content)


def get_product_reference(
    inputs: ProductReferenceInputs, section: Section
) -> ReferenceContent | NotAvailable:
    """Real content for *section*, or an explicit ``NotAvailable`` - never a guess."""
    if section not in SECTIONS:
        raise ValueError(f"section must be one of {SECTIONS}, got {section!r}")

    if section == "contributing":
        return _document_section(section, inputs.contributing)
    if section == "agent_guidance":
        return _document_section(section, inputs.agent_guidance)

    if section in ("formats", "limitations"):
        return NotAvailable(section, f"{section} has no data source in this project yet")

    if inputs.manifest_text is None:
        return NotAvailable(section, "no packaging manifest was provided")

    # A single read of inputs.platform, reused by every branch below - this is what
    # the card's negative control targets: force this back to a literal 'net' and every
    # non-.NET platform's dispatch collapses onto the .NET path, which raises when it
    # tries to parse non-XML manifest text as a .csproj.
    platform = inputs.platform

    if section == "compatibility":
        if platform in _DOTNET_PLATFORMS:
            manifest = read_dotnet_manifest(inputs.manifest_text)
            if not manifest.target_framework:
                return NotAvailable(section, "manifest does not state a TargetFramework")
            return ReferenceContent(section, manifest.target_framework)
        if platform == "python":
            value = read_python_manifest(inputs.manifest_text).requires_python
            reason = "manifest does not state requires-python"
        elif platform == "java":
            value = read_java_manifest(inputs.manifest_text).runtime_min_version
            reason = "manifest does not state a compiler target version"
        elif platform in _JS_PLATFORMS:
            value = read_js_manifest(inputs.manifest_text).engines_node
            reason = "manifest does not state an engines.node range"
        elif platform == "go":
            value = read_go_manifest(inputs.manifest_text).go_version
            reason = "manifest does not state a go version"
        elif platform == "rust":
            value = read_rust_manifest(inputs.manifest_text).rust_version
            reason = "manifest does not state a rust-version"
        elif platform == "cpp":
            value = read_cpp_manifest(inputs.manifest_text).cpp_standard
            reason = "manifest does not state a C++ standard"
        else:
            return NotAvailable(section, f"platform {platform!r} is not supported")
        if not value:
            return NotAvailable(section, reason)
        return ReferenceContent(section, value)

    if section == "license":
        if platform in _DOTNET_PLATFORMS:
            license_expression = _xml_field(inputs.manifest_text, "PackageLicenseExpression")
            if not license_expression:
                return NotAvailable(section, "manifest does not state a license expression")
            return ReferenceContent(section, license_expression)
        if platform == "python":
            value = read_python_manifest(inputs.manifest_text).license
        elif platform in _JS_PLATFORMS:
            value = read_js_manifest(inputs.manifest_text).license
        elif platform == "rust":
            value = read_rust_manifest(inputs.manifest_text).license
        elif platform in ("java", "go", "cpp"):
            value = ""
        else:
            return NotAvailable(section, f"platform {platform!r} is not supported")
        if not value:
            return NotAvailable(section, "manifest does not state a license expression")
        return ReferenceContent(section, value)

    if section == "install":
        if platform in _DOTNET_PLATFORMS:
            manifest = read_dotnet_manifest(inputs.manifest_text)
            if not manifest.package_id:
                return NotAvailable(section, "manifest does not state a package id")
            return ReferenceContent(section, f"dotnet add package {manifest.package_id}")
        if platform == "python":
            name = read_python_manifest(inputs.manifest_text).name
            if not name:
                return NotAvailable(section, "manifest does not state a package name")
            return ReferenceContent(section, f"pip install {name}")
        if platform in _JS_PLATFORMS:
            name = read_js_manifest(inputs.manifest_text).name
            if not name:
                return NotAvailable(section, "manifest does not state a package name")
            return ReferenceContent(section, f"npm install {name}")
        if platform == "java":
            java_manifest = read_java_manifest(inputs.manifest_text)
            if not java_manifest.artifact_id:
                return NotAvailable(section, "manifest does not state an artifact id")
            coordinate = (
                f"{java_manifest.group_id}:{java_manifest.artifact_id}"
                if java_manifest.group_id
                else java_manifest.artifact_id
            )
            return ReferenceContent(section, coordinate)
        if platform == "go":
            name = read_go_manifest(inputs.manifest_text).name
            if not name:
                return NotAvailable(section, "manifest does not state a module path")
            return ReferenceContent(section, f"go get {name}")
        if platform == "rust":
            name = read_rust_manifest(inputs.manifest_text).name
            if not name:
                return NotAvailable(section, "manifest does not state a package name")
            return ReferenceContent(section, f"cargo add {name}")
        if platform == "cpp":
            return NotAvailable(section, "cpp has no package-manager install command")
        return NotAvailable(section, f"platform {platform!r} is not supported")

    # section == "support"
    if platform in _DOTNET_PLATFORMS:
        project_url = _xml_field(inputs.manifest_text, "PackageProjectUrl")
        if not project_url:
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform not in _KNOWN_PLATFORMS:
        return NotAvailable(section, f"platform {platform!r} is not supported")
    return NotAvailable(section, "manifest does not state a project url")
