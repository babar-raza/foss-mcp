"""TC-174: the container inputs are pinned, and they agree with the toolchain lock.

Offline and static. It parses Dockerfile.ingestion, Dockerfile.serving and
scripts/toolchain/toolchain.lock.json. It never runs docker and never touches the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "scripts" / "toolchain" / "toolchain.lock.json"
DOCKERFILES = [ROOT / "Dockerfile.ingestion", ROOT / "Dockerfile.serving"]
INGESTION = ROOT / "Dockerfile.ingestion"

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")
SHA256_HEX_RE = re.compile(r"\b[0-9a-f]{64}\b")


def _lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _logical_lines(path: Path) -> list[str]:
    """Return the non-comment instructions, with backslash continuations joined."""
    out: list[str] = []
    buf = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        out.append(buf + s)
        buf = ""
    if buf:
        out.append(buf)
    return out


def _arg_default(lines: list[str], name: str) -> str | None:
    for line in lines:
        m = re.fullmatch(rf"ARG\s+{re.escape(name)}=(\S+)", line)
        if m:
            return m.group(1)
    return None


def test_every_from_is_pinned_by_digest_and_matches_lock() -> None:
    lock_digest = _lock()["container_pins"]["base_images"]["python:3.13-slim"]["digest"]
    for df in DOCKERFILES:
        froms = [ln for ln in _logical_lines(df) if re.match(r"FROM\s", ln)]
        assert froms, f"{df.name} has no FROM line"
        for ln in froms:
            assert DIGEST_RE.search(ln), f"{df.name}: FROM is not digest-pinned: {ln!r}"
            assert lock_digest in ln, f"{df.name}: FROM digest differs from the lock: {ln!r}"


def test_arg_version_defaults_equal_lock_values() -> None:
    pins = _lock()["container_pins"]
    lines = _logical_lines(INGESTION)
    expected = {
        "GO_VERSION": pins["downloads"]["go"]["version"],
        "NODE_VERSION": pins["downloads"]["node"]["version"],
        "RUST_VERSION": pins["rust_toolchain"]["version"],
    }
    for arg, want in expected.items():
        got = _arg_default(lines, arg)
        assert got is not None, f"Dockerfile.ingestion has no 'ARG {arg}=<default>' line"
        assert got == want, f"ARG {arg} default {got!r} != lock value {want!r}"


def test_default_toolchain_is_the_locked_rust_version() -> None:
    want = _lock()["container_pins"]["rust_toolchain"]["version"]
    code = "\n".join(_logical_lines(INGESTION))
    m = re.search(r'--default-toolchain\s+"?\$\{RUST_VERSION\}"?', code)
    assert m, "Dockerfile.ingestion does not install rustup with --default-toolchain ${RUST_VERSION}"
    assert _arg_default(_logical_lines(INGESTION), "RUST_VERSION") == want


def test_every_curl_download_is_sha256_checked_against_lock() -> None:
    downloads = _lock()["container_pins"]["downloads"]
    for df in DOCKERFILES:
        for ln in _logical_lines(df):
            if not re.search(r"\bcurl\b", ln):
                continue
            matches = [
                (name, entry)
                for name, entry in downloads.items()
                if entry.get("url_marker") and entry["url_marker"] in ln
            ]
            assert len(matches) == 1, f"{df.name}: curl download matches {len(matches)} lock rows: {ln!r}"
            name, entry = matches[0]
            if entry.get("status") == "UNPINNED":
                assert entry.get("reason"), f"{name} is UNPINNED without a recorded reason"
                continue
            want = entry["sha256"]
            assert "sha256sum" in ln, f"{df.name}: {name} curl has no sha256sum check: {ln!r}"
            assert want in ln, f"{df.name}: {name} sha256 check does not carry the lock value"
            assert set(SHA256_HEX_RE.findall(ln)) == {want}, (
                f"{df.name}: {name} curl line carries a different sha256 than the lock: {ln!r}"
            )


def test_no_stable_channel_or_latest_tag_in_instructions() -> None:
    for df in DOCKERFILES:
        code = "\n".join(_logical_lines(df))
        assert not re.search(r"--default-toolchain\s+stable\b", code), f"{df.name} floats rust"
        assert not re.search(r"(?<![\w.-])stable(?![\w.-])", code), f"{df.name} uses 'stable'"
        assert not re.search(r":latest\b", code), f"{df.name} uses a latest tag"
        assert not re.search(r"/latest\b", code), f"{df.name} uses a latest URL"


def test_host_tool_versions_equal_container_lock_values() -> None:
    lock = _lock()
    host = {t["name"]: t["version"].split()[0] for t in lock["tools"]}
    assert host["go"] == lock["container_pins"]["downloads"]["go"]["version"]
    assert host["nodejs"] == lock["container_pins"]["downloads"]["node"]["version"]
