# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/package_root.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""package_root.py â€” Detect the source root of a FOSS package repository.

Extracted from scout.py (step 4.2). Contains:
- detect_package_root(repo, platform) â€” dispatch function
- _detect_python_root, _detect_dotnet_root, _detect_java_root,
  _detect_js_root, _detect_cpp_root, _detect_go_root â€” language-specific implementations

No Scout class dependencies â€” these are pure functions usable in isolation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

LOG = logging.getLogger("scout.package_root")


def detect_package_root(repo: Path, platform: str) -> Path:
    """Return the package source root inside *repo*."""
    if platform == "python":
        return _detect_python_root(repo)
    if platform in ("net", "dotnet"):
        return _detect_dotnet_root(repo)
    if platform == "java":
        return _detect_java_root(repo)
    if platform in ("typescript", "javascript", "nodejs"):
        return _detect_js_root(repo, platform)
    if platform == "cpp":
        return _detect_cpp_root(repo)
    if platform == "go":
        return _detect_go_root(repo)
    if platform == "rust":
        return _detect_rust_root(repo)
    return repo


def _contains_nested_init_py(d: Path) -> bool:
    """Return True if *d* contains an ``__init__.py`` anywhere beneath it.

    Used to recognize a PEP 420 implicit namespace package: a top-level
    directory that has no ``__init__.py`` of its own but whose nested
    subpackage does (e.g. ``aspose/`` containing ``aspose/slides_foss/__init__.py``).
    """
    return any(d.rglob("__init__.py"))


def _detect_python_root(repo: Path) -> Path:
    for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (repo / marker).exists():
            # look for src layout first
            src = repo / "src"
            if src.is_dir():
                pkgs = [d for d in src.iterdir() if d.is_dir() and (d / "__init__.py").exists()]
                if pkgs:
                    return pkgs[0]
            # flat layout: first package dir at root
            pkgs = [
                d
                for d in repo.iterdir()
                if d.is_dir()
                and (d / "__init__.py").exists()
                and not d.name.lower().startswith((".", "test", "example", "doc", "api"))
            ]
            if pkgs:
                return pkgs[0]
            # PEP 420 implicit namespace package fallback: neither the src-layout
            # nor the flat-layout check above found a directory with its own
            # __init__.py. A top-level (or src/) directory that itself lacks
            # __init__.py but contains one somewhere beneath it (e.g. `aspose/`
            # containing `aspose/slides_foss/__init__.py`) is still a valid
            # package root - return it AS-IS, never descend into the nested
            # subpackage.
            if src.is_dir():
                ns_pkgs = [
                    d
                    for d in src.iterdir()
                    if d.is_dir()
                    and not (d / "__init__.py").exists()
                    and not d.name.lower().startswith((".", "test", "example", "doc", "api"))
                    and _contains_nested_init_py(d)
                ]
                if ns_pkgs:
                    return ns_pkgs[0]
            ns_pkgs = [
                d
                for d in repo.iterdir()
                if d.is_dir()
                and not (d / "__init__.py").exists()
                and not d.name.lower().startswith((".", "test", "example", "doc", "api"))
                and _contains_nested_init_py(d)
            ]
            if ns_pkgs:
                return ns_pkgs[0]
    return repo


def _detect_dotnet_root(repo: Path) -> Path:
    csproj_files = list(repo.rglob("*.csproj"))[:50]
    if not csproj_files:
        return repo
    # exclude test and exe projects
    candidates = []
    for cp in csproj_files:
        text = cp.read_text(encoding="utf-8", errors="replace").lower()
        if "test" in cp.stem.lower():
            continue
        if "<outputtype>exe</outputtype>" in text:
            continue
        candidates.append(cp)
    if not candidates:
        candidates = csproj_files
    # prefer shortest path (most likely the primary library project)
    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0].parent


def _detect_java_root(repo: Path) -> Path:
    for candidate in ("src/main/java", "app/src/main/java", "src"):
        p = repo / candidate
        if p.is_dir():
            return p
    return repo


def _detect_js_root(repo: Path, platform: str) -> Path:
    pkg_json = repo / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            for field in ("main", "module"):
                val = data.get(field, "")
                if val:
                    p = repo / val
                    if p.exists():
                        return p.parent
            exports = data.get("exports", {})
            if isinstance(exports, dict):
                dot = exports.get(".", {})
                if isinstance(dot, dict):
                    for key in ("import", "require", "default"):
                        val = dot.get(key, "")
                        if val and (repo / val).exists():
                            return (repo / val).parent
                elif isinstance(dot, str) and (repo / dot).exists():
                    return (repo / dot).parent
        except (json.JSONDecodeError, OSError):
            pass
    for d in ("src", "lib", "dist"):
        if (repo / d).is_dir():
            return repo / d
    return repo


def _detect_cpp_root(repo: Path) -> Path:
    inc = repo / "include"
    if inc.is_dir():
        return inc
    src = repo / "src"
    if src.is_dir():
        return src
    return repo


def _detect_rust_root(repo: Path) -> Path:
    """Return the Rust crate source root inside *repo*.

    For flat-layout crates (Cargo.toml at root), returns ``src/`` when it
    exists (the canonical crate source dir), else the repo root.
    For nested/workspace layouts, returns the first subdirectory containing
    a Cargo.toml (its ``src/`` when present).
    Logs a warning if no Cargo.toml is found and falls back to *repo*.
    """
    if (repo / "Cargo.toml").exists():
        src = repo / "src"
        return src if src.is_dir() else repo
    try:
        for candidate in sorted(repo.iterdir()):
            if candidate.is_dir() and (candidate / "Cargo.toml").exists():
                LOG.info("Rust crate root detected at %s (nested layout)", candidate)
                src = candidate / "src"
                return src if src.is_dir() else candidate
    except OSError:
        pass
    LOG.warning("No Cargo.toml found under %s â€” falling back to repo root", repo)
    return repo


def _detect_go_root(repo: Path) -> Path:
    """Return the Go package root inside *repo*.

    For flat-layout repos (go.mod at root), returns *repo*.
    For nested layouts (go.mod inside a subdirectory), returns that subdirectory.
    Logs a warning if no go.mod is found and falls back to *repo*.
    """
    # Flat layout: go.mod at repo root (most common for FOSS Go libraries)
    if (repo / "go.mod").exists():
        return repo
    # Nested layout: find first go.mod in immediate subdirectories
    try:
        for candidate in sorted(repo.iterdir()):
            if candidate.is_dir() and (candidate / "go.mod").exists():
                LOG.info("Go package root detected at %s (nested layout)", candidate)
                return candidate
    except OSError:
        pass
    LOG.warning("No go.mod found under %s â€” falling back to repo root", repo)
    return repo
