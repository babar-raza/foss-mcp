"""Static checks that the pinned .NET 8 SDK and its activation script agree.

Offline and static. Reads only files inside the repository. Never reads the
tools root (C:\\dev-tools), so CI can run these tests without the SDK installed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "scripts" / "toolchain" / "toolchain.lock.json"
ACTIVATE_PATH = REPO_ROOT / "scripts" / "toolchain" / "activate.ps1"

SDK_NAME = "dotnet-8-sdk"
SDK_VERSION = "8.0.425"
SDK_RELATIVE_PATH = "dotnet-8"

SYSTEM_NAME = "dotnet-sdk"
SYSTEM_ENTRY = {
    "name": "dotnet-sdk",
    "version": "10.0.204",
    "relative_path": "C:\\Program Files\\dotnet",
    "kind": "installed-system",
    "source": "https://dotnet.microsoft.com/download (SDK installer)",
    "used_by": "example verifier (dotnet platform). Already installed on C:; not copied into tools/.",
}


def _lock_tools() -> list[dict]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))["tools"]


def _lock_entry(name: str) -> dict:
    matches = [tool for tool in _lock_tools() if tool.get("name") == name]
    assert len(matches) == 1, f"expected exactly one lock entry named {name!r}, found {len(matches)}"
    return matches[0]


def _activate_text() -> str:
    return ACTIVATE_PATH.read_text(encoding="utf-8-sig")


def test_lock_pins_dotnet_8_sdk_8_0_425():
    entry = _lock_entry(SDK_NAME)
    assert entry["version"] == SDK_VERSION
    assert entry["relative_path"] == SDK_RELATIVE_PATH

    sha512 = re.findall(r"sha512 ([0-9a-f]+)", entry["source"])
    assert len(sha512) == 1, "expected exactly one 'sha512 <hex>' in the dotnet-8-sdk source field"
    assert re.fullmatch(r"[0-9a-f]{128}", sha512[0]), "sha512 must be 128 lowercase hex characters"


def test_activate_puts_dotnet_8_folder_on_path_and_dotnet_root():
    lock_rel = _lock_entry(SDK_NAME)["relative_path"]
    text = _activate_text()

    path_block = re.search(r"\$pathDirs\s*=\s*@\((.*?)\n\)", text, re.S)
    assert path_block, "could not find the $pathDirs list in activate.ps1"
    path_folders = re.findall(r"Join-Path \$tools '([^']+)'", path_block.group(1))
    assert lock_rel in path_folders, (
        f"activate.ps1 PATH list does not contain the lock's {lock_rel!r} folder; found {path_folders}"
    )

    dotnet_root = re.findall(r"^\$env:DOTNET_ROOT\s*=\s*Join-Path\s+\$tools\s+'([^']+)'\s*$", text, re.M)
    assert dotnet_root == [lock_rel], (
        f"activate.ps1 must assign DOTNET_ROOT once, to the lock's {lock_rel!r} folder; found {dotnet_root}"
    )


def test_system_dotnet_sdk_entry_is_unchanged():
    assert _lock_entry(SYSTEM_NAME) == SYSTEM_ENTRY
