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

import json
import re
from dataclasses import dataclass
from typing import Literal

from foss_mcp.extraction.manifest_reader import (
    _load_toml,
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

# CMake's project() command has accepted a HOMEPAGE_URL argument since CMake 3.12
# (https://cmake.org/cmake/help/latest/command/project.html) - a real, standard,
# commonly-populated field, unlike go.mod, which has no equivalent (its module path,
# already surfaced by read_go_manifest().name for the "install" section, is the
# closest thing it has, and is not a separate homepage/repository field).
_CPP_HOMEPAGE_URL_RE = re.compile(r'HOMEPAGE_URL\s+"([^"]*)"', re.IGNORECASE)

# G2/TC-275 (C2 part 2): the registry each platform's "install" coordinate is checked against by
# infra/verify_package_registry.py's own _CHECKERS dict - one key per ecosystem that module
# actually implements a checker for. cpp has no package-manager install command already (see the
# "install" section's own cpp branch below) and so has no entry here either; a platform with no
# entry here is exactly a platform install_coordinate_for_verification() answers None for.
_REGISTRY_ECOSYSTEM_BY_PLATFORM: dict[str, str] = {
    "net": "nuget",
    "dotnet": "nuget",
    "python": "pypi",
    "typescript": "npm",
    "javascript": "npm",
    "js": "npm",
    "nodejs": "npm",
    "java": "maven",
    "go": "go",
    "rust": "cargo",
}

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
    # G2/TC-275 (C2 part 2): the ingestion-time registry-verification result for THIS platform's
    # current install coordinate (infra/verify_package_registry.py via
    # infra/verify_product_reference_install.py), merged in by infra/serve_http.py when its
    # sidecar is present. None/None/None (the default) means "never checked yet" - served exactly
    # as before this card landed. install_verified is only ever read as a literal `is False` below,
    # never truthiness, so True and None are deliberately indistinguishable to the "install"
    # branch - both mean "serve the manifest's own claim, unmodified".
    install_verified: bool | None = None
    install_verified_coordinate: str | None = None
    install_verified_checked_at: str | None = None


def _xml_field(manifest_text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", manifest_text, re.DOTALL)
    return match.group(1).strip() if match else None


def _document_section(section: Section, document: DocumentResult | None) -> ReferenceContent | NotAvailable:
    if document is None or isinstance(document, DocumentNotPresent):
        return NotAvailable(section, f"{section} is not present in this repository")
    return ReferenceContent(section, document.content)


def install_coordinate_for_verification(inputs: ProductReferenceInputs) -> tuple[str, str] | None:
    """The ``(ecosystem, bare_coordinate)`` pair the "install" section branch below would serve
    right now for *inputs*, or ``None`` exactly where that branch would itself answer
    ``NotAvailable`` (no manifest, a missing manifest field, cpp, or a platform this tool does not
    know about) - this function reads the identical manifest fields that branch reads, and must
    never raise where that branch would not error either.

    ``infra/verify_product_reference_install.py`` (G2/TC-275, C2 part 2) is this function's one
    real caller: it feeds the returned coordinate to ``infra/verify_package_registry.py``'s own
    checker for the returned ecosystem, at ingestion time, once per pilot - never from inside a
    request handler.
    """
    if inputs.manifest_text is None:
        return None
    platform = inputs.platform
    ecosystem = _REGISTRY_ECOSYSTEM_BY_PLATFORM.get(platform)
    if ecosystem is None:
        return None

    if platform in _DOTNET_PLATFORMS:
        package_id = read_dotnet_manifest(inputs.manifest_text).package_id
        return (ecosystem, package_id) if package_id else None
    if platform == "python":
        name = read_python_manifest(inputs.manifest_text).name
        return (ecosystem, name) if name else None
    if platform in _JS_PLATFORMS:
        name = read_js_manifest(inputs.manifest_text).name
        return (ecosystem, name) if name else None
    if platform == "java":
        java_manifest = read_java_manifest(inputs.manifest_text)
        if not java_manifest.artifact_id:
            return None
        coordinate = (
            f"{java_manifest.group_id}:{java_manifest.artifact_id}"
            if java_manifest.group_id
            else java_manifest.artifact_id
        )
        return (ecosystem, coordinate)
    if platform == "go":
        name = read_go_manifest(inputs.manifest_text).name
        return (ecosystem, name) if name else None
    if platform == "rust":
        name = read_rust_manifest(inputs.manifest_text).name
        return (ecosystem, name) if name else None
    # Unreachable: every key _REGISTRY_ECOSYSTEM_BY_PLATFORM carries is handled above. Kept as an
    # explicit None rather than falling off the end, so a future ecosystem-dict entry without a
    # matching branch here fails safe (None) instead of silently returning nothing via a missing
    # return path.
    return None


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
        content: ReferenceContent | NotAvailable
        if platform in _DOTNET_PLATFORMS:
            manifest = read_dotnet_manifest(inputs.manifest_text)
            if not manifest.package_id:
                content = NotAvailable(section, "manifest does not state a package id")
            else:
                content = ReferenceContent(section, f"dotnet add package {manifest.package_id}")
        elif platform == "python":
            name = read_python_manifest(inputs.manifest_text).name
            if not name:
                content = NotAvailable(section, "manifest does not state a package name")
            else:
                content = ReferenceContent(section, f"pip install {name}")
        elif platform in _JS_PLATFORMS:
            name = read_js_manifest(inputs.manifest_text).name
            if not name:
                content = NotAvailable(section, "manifest does not state a package name")
            else:
                content = ReferenceContent(section, f"npm install {name}")
        elif platform == "java":
            java_manifest = read_java_manifest(inputs.manifest_text)
            if not java_manifest.artifact_id:
                content = NotAvailable(section, "manifest does not state an artifact id")
            else:
                coordinate = (
                    f"{java_manifest.group_id}:{java_manifest.artifact_id}"
                    if java_manifest.group_id
                    else java_manifest.artifact_id
                )
                content = ReferenceContent(section, coordinate)
        elif platform == "go":
            name = read_go_manifest(inputs.manifest_text).name
            if not name:
                content = NotAvailable(section, "manifest does not state a module path")
            else:
                content = ReferenceContent(section, f"go get {name}")
        elif platform == "rust":
            name = read_rust_manifest(inputs.manifest_text).name
            if not name:
                content = NotAvailable(section, "manifest does not state a package name")
            else:
                content = ReferenceContent(section, f"cargo add {name}")
        elif platform == "cpp":
            content = NotAvailable(section, "cpp has no package-manager install command")
        else:
            content = NotAvailable(section, f"platform {platform!r} is not supported")

        # G2/TC-275 (C2 part 2): "False information is more dangerous than missing information" -
        # once ingestion-time verification has confirmed THIS EXACT coordinate does not resolve on
        # its real registry, stop serving it as confident content. install_verified is read as a
        # literal `is False` (never truthiness): True and None (never checked, or checked under a
        # since-changed coordinate) both fall through to the unmodified ReferenceContent below,
        # exactly as before this card.
        if isinstance(content, ReferenceContent) and inputs.install_verified is False:
            current = install_coordinate_for_verification(inputs)
            if current is not None and current[1] == inputs.install_verified_coordinate:
                ecosystem, coordinate = current
                return NotAvailable(
                    section,
                    f"{coordinate} ({ecosystem}) was checked against its real registry at "
                    f"{inputs.install_verified_checked_at} and does not resolve there",
                )
        return content

    # section == "support"
    if platform in _DOTNET_PLATFORMS:
        project_url = _xml_field(inputs.manifest_text, "PackageProjectUrl")
        if not project_url:
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform == "python":
        data = _load_toml(inputs.manifest_text)
        project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
        urls = project.get("urls", {}) if isinstance(project.get("urls"), dict) else {}
        tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
        poetry = tool.get("poetry", {}) if isinstance(tool.get("poetry"), dict) else {}
        project_url = (
            urls.get("Homepage")
            or urls.get("Repository")
            or poetry.get("homepage")
            or poetry.get("repository")
        )
        if not project_url or not isinstance(project_url, str):
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform in _JS_PLATFORMS:
        try:
            data = json.loads(inputs.manifest_text)
        except (json.JSONDecodeError, TypeError):
            data = None
        project_url = None
        if isinstance(data, dict):
            homepage = data.get("homepage")
            if isinstance(homepage, str) and homepage:
                project_url = homepage
            else:
                repository = data.get("repository")
                if isinstance(repository, str) and repository:
                    project_url = repository
                elif isinstance(repository, dict):
                    repo_url = repository.get("url")
                    if isinstance(repo_url, str) and repo_url:
                        project_url = repo_url
        if not project_url:
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform == "java":
        project_url = _xml_field(inputs.manifest_text, "url")
        if not project_url:
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform == "rust":
        data = _load_toml(inputs.manifest_text)
        package = data.get("package", {}) if isinstance(data.get("package"), dict) else {}
        project_url = package.get("homepage") or package.get("repository")
        if not project_url or not isinstance(project_url, str):
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform == "cpp":
        match = _CPP_HOMEPAGE_URL_RE.search(inputs.manifest_text)
        project_url = match.group(1).strip() if match else ""
        if not project_url:
            return NotAvailable(section, "manifest does not state a project url")
        return ReferenceContent(section, project_url)
    if platform not in _KNOWN_PLATFORMS:
        return NotAvailable(section, f"platform {platform!r} is not supported")
    # go has no equivalent standard field in go.mod - see _CPP_HOMEPAGE_URL_RE's
    # module comment for why cpp is handled above but go is not.
    return NotAvailable(section, "manifest does not state a project url")
