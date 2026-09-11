"""Clone a pinned product repository, run the tree-sitter engine, emit a reproducible fixture.

Not itself exercised at check time: ``gatectl`` verifies TC-011 with the network proxied to a
dead port, so its tests run only against the fixture this script already produced
(``tests/fixtures/pdf_net/api_surface.json``). This module is the tool that produced it - run by
hand, with real network access, never by the offline test suite itself.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface, package_root

# Platform name (as written in a product manifest) to the tree-sitter-language-pack spelling
# extract_api_surface expects. package_root.detect_package_root takes the manifest spelling
# directly (it already accepts both "net" and "dotnet").
_LANGUAGE_BY_PLATFORM = {
    "net": "csharp",
    "dotnet": "csharp",
    "java": "java",
    "cpp": "cpp",
    "go": "go",
    "rust": "rust",
    "typescript": "typescript",
    "javascript": "typescript",
    "nodejs": "typescript",
}


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))


def clone_shallow(repository: str, dest: Path) -> str:
    """Shallow-clone *repository* ("owner/name") into *dest*; return the exact commit SHA.

    Read-only: this never writes into the clone and never pushes to it. Cleanup is the
    caller's responsibility.
    """
    url = f"https://github.com/{repository}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def extract_from_clone(clone_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the engine over an already-cloned checkout, per the manifest's declared platform."""
    platform = manifest["platform"]
    language = _LANGUAGE_BY_PLATFORM[platform]
    pkg_root = package_root.detect_package_root(clone_root, platform)
    parser = get_parser(language)
    types, *_rest = api_surface.extract_api_surface(
        parser, language, pkg_root, clone_root, manifest["family"]
    )
    return types


def extract_pinned_repository(manifest_path: Path) -> dict[str, Any]:
    """Clone the manifest's pinned repository shallowly, run the engine over it, and return an
    api_surface-equivalent artifact recording the source repository and the exact commit
    extracted - the fixture is meaningless without that commit.
    """
    manifest = load_manifest(manifest_path)
    workdir = Path(tempfile.mkdtemp(prefix="foss-mcp-extract-"))
    try:
        commit_sha = clone_shallow(manifest["repository"], workdir)
        types = extract_from_clone(workdir, manifest)
        return {
            "source_repository": manifest["repository"],
            "source_commit": commit_sha,
            "language": _LANGUAGE_BY_PLATFORM[manifest["platform"]],
            "type_count": len(types),
            "types": types,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def reduce_fixture(artifact: dict[str, Any], *, max_types: int = 300) -> dict[str, Any]:
    """A representative, deterministic subset of *artifact*, under the fixture size budget.

    The point of the fixture is a reproducible offline test, not a data dump: sorted by
    qualified name so the same source commit always reduces to the same fixture, and the
    truncation is recorded rather than hidden.
    """
    types = sorted(artifact["types"], key=lambda entry: entry.get("class_import") or entry.get("name") or "")
    reduced = types[:max_types]
    return {
        **{key: value for key, value in artifact.items() if key != "types"},
        "reduced_type_count": len(reduced),
        "truncated": len(reduced) < len(types),
        "types": reduced,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="product manifest, e.g. config/products/pdf/net.yaml")
    parser.add_argument(
        "output", type=Path, help="fixture path, e.g. tests/fixtures/pdf_net/api_surface.json"
    )
    parser.add_argument("--max-types", type=int, default=300)
    args = parser.parse_args()

    artifact = extract_pinned_repository(args.manifest)
    fixture = reduce_fixture(artifact, max_types=args.max_types)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {args.output} ({args.output.stat().st_size} bytes); "
        f"{fixture['reduced_type_count']}/{artifact['type_count']} types; "
        f"commit {artifact['source_commit']}"
    )


if __name__ == "__main__":
    main()
