# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/package_manifest.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""package_manifest.py â€” Extract identity fields from platform package manifests.

Extracted from scout.py (step 4.3). Contains:
- parse_manifest(repo, platform) â€” dispatch function
- _parse_python_manifest, _parse_dotnet_manifest, _parse_java_manifest,
  _parse_js_manifest, _parse_cpp_manifest â€” language-specific implementations

No Scout class dependencies â€” these are pure functions usable in isolation.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger("scout.package_manifest")


def parse_manifest(repo: Path, platform: str) -> dict[str, Any]:
    """Extract identity fields from the platform manifest."""
    if platform == "python":
        return _parse_python_manifest(repo)
    if platform in ("net", "dotnet"):
        return _parse_dotnet_manifest(repo)
    if platform == "java":
        return _parse_java_manifest(repo)
    if platform in ("typescript", "javascript", "nodejs"):
        return _parse_js_manifest(repo)
    if platform == "cpp":
        return _parse_cpp_manifest(repo)
    if platform == "go":
        return _parse_go_manifest(repo)
    if platform == "rust":
        return _parse_rust_manifest(repo)
    return {}


def _parse_python_manifest(repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    pyp = repo / "pyproject.toml"
    if pyp.exists():
        try:
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(pyp.read_text(encoding="utf-8", errors="replace"))
            proj = data.get("project", {})
            info["name"] = proj.get("name", "")
            info["version"] = proj.get("version", "")
            lic = proj.get("license", "")
            if isinstance(lic, dict):
                lic = lic.get("text", lic.get("file", ""))
            info["license"] = lic
            info["dependencies"] = proj.get("dependencies", [])
            info["requires_python"] = proj.get("requires-python", "")
            # Extract canonical package path from [tool.setuptools.packages.find].
            # include lists package names (dotted for namespace packages, flat
            # for ordinary ones); the first non-wildcard entry is the canonical
            # import root.
            # e.g. include = ["aspose.email_foss", "aspose.email_foss.*"]
            #   â†’ canonical_package = "aspose.email_foss" (namespace package)
            # e.g. include = ["aspose_pdf", "aspose_pdf.*"]
            #   â†’ canonical_package = "aspose_pdf" (flat package)
            #
            # HARDEN-py-flat-package (2026-07-28, MT013): the original condition
            # required a "." in the entry, which only matches namespace packages.
            # A flat top-level package whose PyPI distribution name differs from
            # its import name (e.g. PyPI "aspose-pdf-foss-for-python" vs. real
            # `import aspose_pdf`) is the ORDINARY setuptools case, not an edge
            # case -- requiring a dot silently skipped it, leaving
            # canonical_package unset and falling through to the wrong
            # name.replace("-", "_") fallback (which uses the PyPI name, not the
            # real import name). Found live on pdf/python: every generated page
            # would have shown `import aspose_pdf_foss_for_python`, which does
            # not exist -- the real package imports as `import aspose_pdf`.
            setuptools_cfg = data.get("tool", {}).get("setuptools", {})
            packages_find = setuptools_cfg.get("packages", {}).get("find", {})
            includes = packages_find.get("include", [])
            for inc in includes:
                if inc and not inc.endswith(".*") and not inc.endswith("*"):
                    info["canonical_package"] = inc
                    break
        except Exception as exc:
            LOG.warning("pyproject.toml parse error: %s", exc)
    if not info.get("name"):
        sp = repo / "setup.py"
        if sp.exists():
            text = sp.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'name\s*=\s*["\']([^"\']+)', text)
            if m:
                info["name"] = m.group(1)
            m = re.search(r'version\s*=\s*["\']([^"\']+)', text)
            if m:
                info["version"] = m.group(1)
    return info


def _parse_dotnet_manifest(repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    csproj_files = list(repo.rglob("*.csproj"))[:20]
    if not csproj_files:
        return info
    csproj_files.sort(key=lambda p: len(p.parts))
    text = csproj_files[0].read_text(encoding="utf-8", errors="replace")
    for tag, key in [("PackageId", "name"), ("AssemblyName", "name"),
                     ("Version", "version")]:
        if key in info and info[key]:
            continue
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text)
        if m:
            info[key] = m.group(1)

    # FIX-11: Extract ALL target frameworks from multi-target csproj files.
    # <TargetFrameworks> (plural) contains semicolon-separated values;
    # <TargetFramework> (singular) contains a single value.
    m_plural = re.search(r"<TargetFrameworks>(.*?)</TargetFrameworks>", text)
    m_single = re.search(r"<TargetFramework>(.*?)</TargetFramework>", text)
    if m_plural:
        targets = [t.strip() for t in m_plural.group(1).split(";") if t.strip()]
        info["target_frameworks"] = targets
        info["target_framework"] = targets[0] if targets else ""
        # Determine minimum compatible framework
        info["min_framework"] = _lowest_dotnet_framework(targets)
    elif m_single:
        tf = m_single.group(1).strip()
        info["target_frameworks"] = [tf]
        info["target_framework"] = tf
        info["min_framework"] = tf

    return info


# Ranking for .NET framework targets (lower = broader compatibility)
_DOTNET_FRAMEWORK_RANK: dict[str, int] = {
    "netstandard1.0": 10,
    "netstandard1.1": 11,
    "netstandard1.2": 12,
    "netstandard1.3": 13,
    "netstandard1.4": 14,
    "netstandard1.5": 15,
    "netstandard1.6": 16,
    "netstandard2.0": 20,
    "netstandard2.1": 21,
    "net5.0": 50,
    "net6.0": 60,
    "net7.0": 70,
    "net8.0": 80,
    "net9.0": 90,
}


def _lowest_dotnet_framework(targets: list[str]) -> str:
    """Return the lowest (most compatible) framework from a list of targets.

    netstandard targets are ranked lower than net* targets because they
    support the widest range of runtimes. Unknown targets get rank 100.
    """
    if not targets:
        return ""
    ranked = sorted(targets, key=lambda t: _DOTNET_FRAMEWORK_RANK.get(t, 100))
    return ranked[0]


def _parse_java_manifest(repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    pom = repo / "pom.xml"
    if pom.exists():
        text = pom.read_text(encoding="utf-8", errors="replace")
        for tag, key in [("groupId", "group_id"), ("artifactId", "artifact_id"),
                         ("version", "version")]:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", text)
            if m:
                info[key] = m.group(1)
        info["name"] = info.get("artifact_id", "")
        # Extract Java compiler target version (priority: release > target > source)
        for prop_tag in ["maven.compiler.release", "maven.compiler.target",
                         "maven.compiler.source"]:
            m = re.search(
                rf"<{re.escape(prop_tag)}>(.*?)</{re.escape(prop_tag)}>", text
            )
            if m:
                info["runtime_min_version"] = m.group(1).strip()
                break
    gradle = repo / "build.gradle"
    if not info.get("name") and gradle.exists():
        text = gradle.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"group\s*=\s*['\"]([^'\"]+)", text)
        if m:
            info["group_id"] = m.group(1)
        m = re.search(r"version\s*=\s*['\"]([^'\"]+)", text)
        if m:
            info["version"] = m.group(1)
    if "runtime_min_version" not in info and gradle.exists():
        text = gradle.read_text(encoding="utf-8", errors="replace")
        for pattern in [
            r"targetCompatibility\s*=\s*['\"]?(\d+)['\"]?",
            r"sourceCompatibility\s*=\s*['\"]?(\d+)['\"]?",
            r"languageVersion\.of\((\d+)\)",
        ]:
            m = re.search(pattern, text)
            if m:
                info["runtime_min_version"] = m.group(1).strip()
                break
    return info


def _parse_js_manifest(repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            info["name"] = data.get("name", "")
            info["version"] = data.get("version", "")
            info["license"] = data.get("license", "")
            info["dependencies"] = list((data.get("dependencies") or {}).keys())
            info["engines_node"] = (data.get("engines") or {}).get("node", "")
        except (json.JSONDecodeError, OSError):
            pass
    return info


def _extract_go_package_name(repo: Path) -> str:
    """Return the declared package name from the first non-test .go file at repo root.

    Scans *.go files at repo root (excludes *_test.go and files in subdirs).
    Returns the package name string, or "" if none found.  Skips ``package main``.
    """
    for go_file in sorted(repo.glob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        try:
            text = go_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^package\s+(\w+)", text, re.MULTILINE)
        if m:
            pkg = m.group(1)
            if pkg != "main":
                return pkg
    return ""


def _has_go_declarations(go_file: Path) -> bool:
    """True when *go_file* declares at least one func/type/var/const at file scope.

    A file containing only a `package X` line plus doc comments (a `doc.go`
    stub) returns False.
    """
    try:
        text = go_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"^(func|type|var|const)\s", text, re.MULTILINE))


def _detect_go_package_subpath(repo: Path, pkg_name: str) -> str:
    """Return the subdirectory (relative to *repo*) holding the real
    implementation of *pkg_name*, or "" when the root-level declaration
    itself already has real declarations (the common case).

    Found live (cells/go, 2026-07-28): some Go repos put `go.mod` at the
    module root (mandatory -- that's simply where Go module boundaries
    live) but keep only a doc-comment-only `doc.go` stub there (e.g.
    `// Package cells_foss is a pure-Go Excel library.` with no func/type
    declarations), while the real implementation lives in a subdirectory
    under the SAME package name. `_extract_go_package_name` matches the
    stub's `package cells_foss` line first (it only scans repo root) and
    returns a package name that is real, but whose import path
    (`{module}`) resolves to an empty package -- `cells_foss.NewWorkbook`
    is `undefined` at that path. The real import path needs the
    subdirectory appended: `{module}/aspose/cells_foss`.
    """
    root_files = [
        f for f in sorted(repo.glob("*.go"))
        if not f.name.endswith("_test.go")
    ]
    root_has_real_decl = any(
        _has_go_declarations(f) for f in root_files
        if re.search(rf"^package\s+{re.escape(pkg_name)}\b", f.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
    )
    if root_has_real_decl:
        return ""
    try:
        subdirs = [d for d in repo.rglob("*") if d.is_dir()]
    except OSError:
        return ""
    for subdir in sorted(subdirs):
        go_files = [f for f in subdir.glob("*.go") if not f.name.endswith("_test.go")]
        for f in go_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(rf"^package\s+{re.escape(pkg_name)}\b", text, re.MULTILINE) and _has_go_declarations(f):
                return subdir.relative_to(repo).as_posix()
    return ""


def _parse_go_manifest(repo: Path) -> dict[str, Any]:
    """Extract identity fields from go.mod."""
    info: dict[str, Any] = {}
    go_mod = repo / "go.mod"
    if not go_mod.exists():
        return info
    text = go_mod.read_text(encoding="utf-8", errors="replace")
    # Module path (first non-blank line: "module github.com/...")
    m = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    if m:
        info["name"] = m.group(1)
    # Go version requirement
    m = re.search(r"^go\s+([\d.]+)", text, re.MULTILINE)
    if m:
        info["go_version"] = m.group(1)
        info["runtime_requirement"] = f"Go {m.group(1)}+"
    # Package name from source (distinct from module path)
    pkg_name = _extract_go_package_name(repo)
    if pkg_name:
        info["package_name"] = pkg_name
        subpath = _detect_go_package_subpath(repo, pkg_name)
        if subpath:
            info["package_subpath"] = subpath
            LOG.info(
                "Go package %r has no real declarations at repo root; "
                "real implementation found at %s (import path needs this appended)",
                pkg_name, subpath,
            )
    return info


def _parse_rust_manifest(repo: Path) -> dict[str, Any]:
    """Extract identity fields from Cargo.toml.

    Captures ``[package]`` name/version/license/edition/rust-version plus the
    ``[lib] name`` (the real import path when it differs from the crate name,
    stored as ``canonical_package`` â€” same key the Python manifest uses for
    the analogous name-vs-import split).
    """
    info: dict[str, Any] = {}
    cargo = repo / "Cargo.toml"
    if not cargo.exists():
        # Nested/workspace layout: use the first subdirectory crate.
        try:
            for candidate in sorted(repo.iterdir()):
                if candidate.is_dir() and (candidate / "Cargo.toml").exists():
                    cargo = candidate / "Cargo.toml"
                    break
        except OSError:
            pass
    if not cargo.exists():
        return info
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(cargo.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        LOG.warning("Cargo.toml parse error: %s", exc)
        return info
    pkg = data.get("package", {})
    info["name"] = pkg.get("name", "") if isinstance(pkg.get("name", ""), str) else ""
    version = pkg.get("version", "")
    info["version"] = version if isinstance(version, str) else ""
    lic = pkg.get("license", "")
    info["license"] = lic if isinstance(lic, str) else ""
    if pkg.get("edition"):
        info["edition"] = str(pkg["edition"])
    if pkg.get("rust-version"):
        info["rust_version"] = str(pkg["rust-version"])
        info["runtime_requirement"] = f"Rust {pkg['rust-version']}+"
    deps = data.get("dependencies", {})
    if isinstance(deps, dict):
        info["dependencies"] = list(deps.keys())
    lib_name = (data.get("lib") or {}).get("name", "")
    info["canonical_package"] = (
        lib_name if isinstance(lib_name, str) and lib_name
        else info["name"].replace("-", "_")
    )
    return info


def _parse_cpp_manifest(repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    cmake = repo / "CMakeLists.txt"
    if cmake.exists():
        text = cmake.read_text(encoding="utf-8", errors="replace")
        # Project name and version
        m = re.search(r"project\s*\(\s*(\S+)(?:\s+VERSION\s+(\S+))?", text,
                      re.IGNORECASE)
        if m:
            info["name"] = m.group(1)
            if m.group(2):
                info["version"] = m.group(2).rstrip(")")
        # CMake minimum required version
        m = re.search(r"cmake_minimum_required\s*\(\s*VERSION\s+([\d.]+)",
                      text, re.IGNORECASE)
        if m:
            info["cmake_min_version"] = m.group(1)
        # Library target name (first add_library call that is not INTERFACE-only)
        m = re.search(r"add_library\s*\(\s*([A-Za-z][A-Za-z0-9_.-]*)\s+", text,
                      re.IGNORECASE)
        if m:
            info["library_target"] = m.group(1)
        # C++ standard requirement
        m = re.search(r"set_property\s*\([^)]*CXX_STANDARD\s+(\d+)|"
                      r"CMAKE_CXX_STANDARD\s+(\d+)",
                      text, re.IGNORECASE)
        if m:
            std = m.group(1) or m.group(2)
            if std:
                info["cpp_standard"] = std
    return info
