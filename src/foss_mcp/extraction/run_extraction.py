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

from foss_mcp.extraction.tree_sitter_engine import api_surface, package_root, python_surface

# Platform name (as written in a product manifest) to the tree-sitter-language-pack spelling
# extract_api_surface expects. package_root.detect_package_root takes the manifest spelling
# directly (it already accepts both "net" and "dotnet"). "python" is deliberately absent: it
# never reaches get_parser/extract_api_surface at all (see extract_from_clone) - it is routed
# to python_surface.inspect_public_surface, the independent pure-``ast`` reader, instead.
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


def _python_package_dirs(clone_root: Path) -> list[str]:
    """Repository-relative package directory name(s) for
    ``python_surface.inspect_public_surface``'s ``package_dirs`` argument.

    ``inspect_public_surface(repository_root, package_dirs)`` wants *names relative to the
    repository root* (see tests/extraction/tree_sitter_engine/test_lang_python_parity.py:
    ``inspect_public_surface(tmp_path, ["aspose"])``), but
    ``package_root._detect_python_root`` - already correct, and not re-derived here - returns
    a pre-resolved absolute ``Path`` to the package directory itself (e.g. ``repo/src/aspose``
    for a src-layout, or ``repo/aspose`` flat). This just re-expresses that same Path relative
    to *clone_root*, so both layouts route through the identical detection logic.
    """
    pkg_root = package_root.detect_package_root(clone_root, "python")
    try:
        relative = pkg_root.relative_to(clone_root).as_posix()
    except ValueError:
        relative = "."
    # "." means _detect_python_root fell back to the repo root itself (no marker file, or no
    # package directory found under it) - inspect_public_surface has no equivalent "scan
    # everything" mode, so there is nothing meaningful to pass; the caller then rightly gets
    # an empty surface rather than a mis-scanned one.
    return [] if relative == "." else [relative]


def _python_types_from_surface(surface: python_surface.PublicSurface) -> list[dict[str, Any]]:
    """Adapt ``python_surface``'s ``PublicSymbol`` stream into the exact dict shape
    ``extract_api_surface`` produces (see tests/fixtures/pdf_net/api_surface.json), so
    downstream fixture/citation code needs no changes regardless of which reader ran.

    ``PublicSymbol`` carries no base-class, parameter, return-type, or enum-member data (the
    pure-``ast`` reader was never asked to extract them), so ``bases``, ``params``,
    ``return_type``, and ``enum_members`` are always empty for a python-sourced entry - the
    *keys* still match exactly, only their python-specific values are trivial.
    """
    entries: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for symbol in surface.symbols:
        if symbol.kind not in ("class", "enum", "function"):
            continue
        entry: dict[str, Any] = {
            "name": symbol.name,
            "kind": symbol.kind,
            "file": symbol.source_path,
            "line": symbol.line,
            "doc": symbol.docstring or "",
            "visibility": "public",
            "deprecated": False,
            "deprecated_reason": "",
            "bases": [],
            "class_import": symbol.qualified_name,
            "canonical_namespace": symbol.module,
            "methods": [],
            "properties": [],
        }
        if symbol.kind == "enum":
            entry["enum_members"] = []
        entries[symbol.qualified_name] = entry
        order.append(symbol.qualified_name)

    for symbol in surface.symbols:
        if symbol.kind != "method":
            continue
        owner = entries.get(symbol.qualified_name.rsplit(".", 1)[0])
        if owner is None:
            continue  # the owning class was itself unresolved/excluded; never guess one up
        owner["methods"].append(
            {
                "name": symbol.name,
                "doc": symbol.docstring or "",
                "file": symbol.source_path,
                "line": symbol.line,
                "params": [],
                "return_type": "",
                "deprecated": False,
                "deprecated_reason": "",
                "is_constructor": symbol.name == "__init__",
            }
        )

    return [entries[key] for key in order]


def _extract_python_surface(clone_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the independent pure-``ast`` reader over a python checkout, adapted to
    ``extract_api_surface``'s dict shape. Returns ``(types, unresolved)`` - the second element
    is ``python_surface.PublicSurface.unresolved`` verbatim, so an unresolved re-export
    surfaces to the caller rather than being silently dropped.
    """
    package_dirs = _python_package_dirs(clone_root)
    surface = python_surface.inspect_public_surface(clone_root, package_dirs)
    return _python_types_from_surface(surface), list(surface.unresolved)


def extract_from_clone(
    clone_root: Path, manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the engine over an already-cloned checkout, per the manifest's declared platform.

    Returns ``(types, unresolved)``. A python manifest never reaches ``_LANGUAGE_BY_PLATFORM``,
    ``get_parser``, or ``api_surface.extract_api_surface`` at all - it is routed to
    ``python_surface.inspect_public_surface`` instead, and ``unresolved`` is always empty for
    every other, tree-sitter-backed platform (that engine has no equivalent concept).
    """
    platform = manifest["platform"]
    if platform == "python":
        return _extract_python_surface(clone_root)
    language = _LANGUAGE_BY_PLATFORM[platform]
    pkg_root = package_root.detect_package_root(clone_root, platform)
    parser = get_parser(language)
    types, *_rest = api_surface.extract_api_surface(
        parser, language, pkg_root, clone_root, manifest["family"]
    )
    return types, []


def extract_pinned_repository(manifest_path: Path) -> dict[str, Any]:
    """Clone the manifest's pinned repository shallowly, run the engine over it, and return an
    api_surface-equivalent artifact recording the source repository and the exact commit
    extracted - the fixture is meaningless without that commit.
    """
    manifest = load_manifest(manifest_path)
    workdir = Path(tempfile.mkdtemp(prefix="foss-mcp-extract-"))
    try:
        commit_sha = clone_shallow(manifest["repository"], workdir)
        types, unresolved = extract_from_clone(workdir, manifest)
        platform = manifest["platform"]
        # "python" is recorded literally here, never a tree-sitter grammar name, so downstream
        # code can tell which reader produced a given fixture.
        language = "python" if platform == "python" else _LANGUAGE_BY_PLATFORM[platform]
        artifact: dict[str, Any] = {
            "source_repository": manifest["repository"],
            "source_commit": commit_sha,
            "language": language,
            "type_count": len(types),
            "types": types,
        }
        if unresolved:
            artifact["unresolved"] = unresolved
        return artifact
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
