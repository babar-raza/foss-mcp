"""Read a product's own packaging manifest and surface its real fields - never flattened.

A .csproj's ``TargetFramework`` is a specific, verifiable claim - ``net8.0``, ``net472``, or a
semicolon-joined multi-target list are each a different fact about what the library runs on.
Reducing any of them to a generic bucket like "supports .NET" would state something the manifest
never actually said.
"""

from __future__ import annotations

import json
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
