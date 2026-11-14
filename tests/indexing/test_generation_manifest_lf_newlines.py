"""TC-793 (G3/REQ-G3-017, H8): generation manifests are written with LF newlines on every platform.

``os.fdopen(fd, "w", encoding="utf-8")`` translates ``"\\n"`` to ``"\\r\\n"`` on Windows, so the same
manifest had different bytes on Windows and Linux. The writer now passes ``newline="\\n"``.
"""

from __future__ import annotations

import json
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore, build_manifest, publish


def _publish_small_manifest(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "manifests"
    store = GenerationManifestStore(root)
    payload = {
        "lexical_index": {"documents": {"doc-0": {"chunk_id": "chunk-0", "tokens": [], "text": ""}}},
        "pages": 10,
    }
    gen = build_manifest("pdf", "net", "api-reference", "1", payload=payload)
    lease = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    publish(store, gen.scope, None, gen, lease)
    return root, gen.scope, gen.generation_id


def test_every_json_document_the_store_wrote_has_lf_newlines(tmp_path: Path) -> None:
    root, _scope, _generation_id = _publish_small_manifest(tmp_path)

    written = [path for path in root.rglob("*.json") if path.is_file()]
    assert written, "publishing wrote no JSON document at all"
    for path in written:
        data = path.read_bytes()
        assert b"\r\n" not in data, f"{path.name} contains CRLF"
        assert data.endswith(b"\n") and not data.endswith(b"\n\n"), f"{path.name} must end in a single LF"
        json.loads(data.decode("utf-8"))


def test_atomic_write_json_bytes_are_exactly_the_lf_serialisation(tmp_path: Path) -> None:
    doc = {"b": [1, 2], "a": {"z": "é", "y": None}}
    target = tmp_path / "nested" / "doc.json"

    GenerationManifestStore._atomic_write_json(target, doc)

    expected = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert target.read_bytes() == expected
    assert json.loads(target.read_text(encoding="utf-8")) == doc
