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

from foss_mcp.extraction.manifest_reader import read_dotnet_manifest
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentResult

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


def _xml_field(manifest_text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", manifest_text, re.DOTALL)
    return match.group(1).strip() if match else None


def _document_section(section: Section, document: DocumentResult | None) -> ReferenceContent | NotAvailable:
    if document is None or isinstance(document, DocumentNotPresent):
        return NotAvailable(section, f"{section} is not present in this repository")
    return ReferenceContent(section, document.content)


def get_product_reference(inputs: ProductReferenceInputs, section: Section) -> ReferenceContent | NotAvailable:
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

    if section == "compatibility":
        manifest = read_dotnet_manifest(inputs.manifest_text)
        if not manifest.target_framework:
            return NotAvailable(section, "manifest does not state a TargetFramework")
        return ReferenceContent(section, manifest.target_framework)

    if section == "license":
        license_expression = _xml_field(inputs.manifest_text, "PackageLicenseExpression")
        if not license_expression:
            return NotAvailable(section, "manifest does not state a license expression")
        return ReferenceContent(section, license_expression)

    if section == "install":
        manifest = read_dotnet_manifest(inputs.manifest_text)
        if not manifest.package_id:
            return NotAvailable(section, "manifest does not state a package id")
        return ReferenceContent(section, f"dotnet add package {manifest.package_id}")

    # section == "support"
    project_url = _xml_field(inputs.manifest_text, "PackageProjectUrl")
    if not project_url:
        return NotAvailable(section, "manifest does not state a project url")
    return ReferenceContent(section, project_url)
