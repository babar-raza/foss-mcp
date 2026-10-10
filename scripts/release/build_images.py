#!/usr/bin/env python3
"""Build the release images from one exact commit (TC-192 / REQ-G2-043).

Usage:
    python scripts/release/build_images.py [--tag TAG]

Steps, stopping with a non-zero exit at the first failure:
  1. Refuse unless git reports HEAD as a commit and the tracked tree is clean.
  2. Export HEAD with `git archive` into a fresh temporary directory, so the
     build context holds the committed bytes and nothing from the working tree.
  3. Build foss-mcp-serving (Dockerfile.serving) and foss-mcp-ingestion
     (Dockerfile.ingestion) from that export, each labelled with the full HEAD
     SHA as org.opencontainers.image.revision.
  4. Tag both images rev-<sha7>, unless --tag is given.
  5. Print both image names and the full SHA.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION_LABEL = "org.opencontainers.image.revision"
IMAGES = (
    # Dockerfile.serving gained a second, later stage (`demo`, a self-contained variant with
    # pre-built manifests baked in) - its own default stage if --target is omitted entirely,
    # since Docker always builds whichever stage is physically LAST in the file. Pinning
    # --target explicitly here preserves this script's own exact prior behavior (building the
    # plain `runtime` stage) regardless of anything added after it in the future.
    ("foss-mcp-serving", "Dockerfile.serving", "runtime"),
    ("foss-mcp-ingestion", "Dockerfile.ingestion", None),
)


def fail(message: str, code: int = 1) -> None:
    print(f"build_images: {message}", file=sys.stderr)
    sys.exit(code)


def run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        check=False,
    )


def head_sha() -> str:
    proc = run_git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if proc.returncode != 0:
        fail("HEAD is not a commit; refusing to build.")
    return proc.stdout.decode("utf-8").strip()


def refuse_if_dirty() -> None:
    proc = run_git("status", "--porcelain", "--untracked-files=no")
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        fail(f"git status failed; refusing to build: {detail}")
    if proc.stdout.strip():
        fail(
            "working tree is dirty (tracked changes present); "
            "commit or discard them, then rerun. Refusing to build."
        )


def export_head(sha: str, dest: Path) -> None:
    proc = run_git("archive", "--format=tar", sha)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        fail(f"git archive {sha} failed: {detail}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")


def build_image(name: str, dockerfile: str, context: Path, sha: str, tag: str, target: str | None) -> None:
    image = f"{name}:{tag}"
    cmd = [
        "docker",
        "build",
        "--file",
        str(context / dockerfile),
        "--label",
        f"{REVISION_LABEL}={sha}",
        "--tag",
        image,
    ]
    if target is not None:
        cmd += ["--target", target]
    cmd.append(str(context))
    print(
        f"build_images: building {image} from {dockerfile}" + (f" (target {target})" if target else ""),
        flush=True,
    )
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        fail(f"docker build of {image} failed with exit {proc.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build release images from one exact git commit.",
    )
    parser.add_argument(
        "--tag",
        help="tag for both images (default: rev-<sha7> of HEAD)",
    )
    args = parser.parse_args(argv)

    sha = head_sha()
    refuse_if_dirty()
    if shutil.which("docker") is None:
        fail("docker is not on PATH; activate the toolchain first.")
    tag = args.tag if args.tag is not None else f"rev-{sha[:7]}"

    export_dir = Path(tempfile.mkdtemp(prefix="foss-mcp-release-"))
    try:
        export_head(sha, export_dir)
        for name, dockerfile, target in IMAGES:
            build_image(name, dockerfile, export_dir, sha, tag, target)
    finally:
        shutil.rmtree(export_dir, ignore_errors=True)

    print(f"foss-mcp-serving:{tag}")
    print(f"foss-mcp-ingestion:{tag}")
    print(f"revision: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
