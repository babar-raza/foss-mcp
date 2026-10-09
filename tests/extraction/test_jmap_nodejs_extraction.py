"""Offline tests against the committed jmap/nodejs fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (TC-233 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_nodejs/api_surface.json``, using the TC-233 fix that routes platform
"nodejs" through the "javascript" tree-sitter bucket instead of "typescript" - the real
repository is plain JavaScript (every source file under src/ ends in .js, none in .ts), so the
previous "typescript" mapping found zero source files and produced 0/0 types.

TC-311 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that TC-252 (centrality-ranked ``reduce_fixture()``) and TC-261
(``inherited_from`` provenance tagging in ``_flatten_inheritance()``) are both on main. The
commit pin (``93f22fb57ba83484d14af5af2017590a0f5bf55f``) and repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js``) came back unchanged, confirmed against a
fresh clone whose HEAD matched exactly.

This re-run surfaced a real, hand-verified, pre-existing extraction-engine defect, closed by
a separate card (TC-315) before this fixture could be pinned cleanly: the real repository
declares TWO different classes both literally named ``JmapClient`` -
``src/client-core.js``'s real, full implementation (session negotiation, request sending,
``connect``/``close``/``sendRequest``/``echo``/``uploadBlob``/``downloadBlob``) and
``src/index.js``'s near-empty mixin-composition wrapper
(``import { JmapClient as CoreClient } from "./client-core.js"; class JmapClient extends
CoreClient {}``). Before TC-315, "javascript" had no ``canonical_namespace``/``class_import``
derivation branch at all (unlike cpp/java/csharp/python/typescript/rust), so both entries fell
into the same empty-namespace dedup group, and ``consolidate_classes``'s stub-vs-full
heuristic (has-bases implies "full") backfired: it kept the empty wrapper (which has a
``bases`` entry) and silently discarded the real implementation (which, being the root, has
none). TC-315 added the missing javascript branch; re-running after that fix, both
``JmapClient`` entries now survive as distinct records (``client-core.JmapClient`` vs the
unqualified ``src/index.js`` one, which gets no ``class_import`` at all because its file is
named ``index.js`` and the module-path derivation strips a bare ``index`` module segment, the
same way the existing python/typescript branches strip ``__init__``/``index``), and the real
implementation's seven real methods are the ones that appear, not the wrapper's empty shell.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_nodejs" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "nodejs.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "javascript"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture (re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js` if this
    # ever needs re-pinning; do not change it to make a test pass). Unchanged from the prior,
    # pre-TC-315 pin - the repository itself did not move, only the engine's handling of its
    # one same-name-across-files collision did.
    assert data["source_commit"] == "93f22fb57ba83484d14af5af2017590a0f5bf55f"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_untruncated() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository's full public surface is only 31 types (one more than the
    # pre-TC-315 pin's 30 - see test_both_jmap_client_classes_survive_as_distinct_records
    # below) - well under the CLI's default --max-types 300 cap, so this fixture is the
    # complete surface, not a sub-sample.
    assert data["type_count"] == 31
    assert data["truncated"] is False


def test_the_fixture_contains_real_nodejs_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Node.js types, not placeholders - would not survive an empty
    # or synthetic fixture.
    assert {"JmapClient", "Mailbox", "Session"} <= names
    assert all(entry["file"].endswith(".js") for entry in data["types"])


def test_every_entry_is_a_real_javascript_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # tree_helpers.py's own javascript bucket (_CLASS_TYPES["javascript"]) recognizes exactly
    # one class-level declaration kind; this fixture's own types show only that kind.
    assert kinds == {"class_declaration"}


def test_both_jmap_client_classes_survive_as_distinct_records() -> None:
    """TC-315's headline fix, confirmed directly in this live re-run: the real repository
    declares two different classes both literally named ``JmapClient`` - the real, full
    implementation in ``src/client-core.js`` and a near-empty mixin-composition wrapper in
    ``src/index.js`` (``class JmapClient extends CoreClient {}``, ``CoreClient`` being an
    import-aliased re-import of the very same ``client-core.js`` class). Before TC-315,
    "javascript" had no ``canonical_namespace``/``class_import`` derivation at all, so both
    entries landed in the same empty-namespace dedup group and ``consolidate_classes``'s
    stub-vs-full heuristic kept the wrapper (which has a non-empty ``bases``) and silently
    discarded the real implementation (which, being the root, has none) - a real engine
    defect, not a fixture problem, closed by TC-315 before this card could re-pin cleanly.
    """
    data = _load_fixture()
    jmap_clients = [entry for entry in data["types"] if entry["name"] == "JmapClient"]
    assert len(jmap_clients) == 2

    wrapper = next(e for e in jmap_clients if e["file"] == "src/index.js")
    full = next(e for e in jmap_clients if e["file"] == "src/client-core.js")

    # The wrapper is a real, distinct record (it legitimately extends the real
    # implementation under an import alias) but contributes no methods of its own.
    assert wrapper["bases"] == ["CoreClient"]
    assert wrapper["methods"] == []
    # "index.js" strips to an empty module-path segment (mirrors the python/typescript
    # branches stripping "__init__"/"index"), so this entry gets no class_import at all.
    assert "class_import" not in wrapper

    # The real implementation is the one that survives with its real methods - this is the
    # headline regression TC-315 fixed: before it, this exact set of real methods was
    # silently dropped from the fixture entirely.
    assert full["bases"] == []
    assert full["class_import"] == "client-core.JmapClient"
    assert full["canonical_namespace"] == "client-core"
    method_names = {m["name"] for m in full["methods"]}
    assert {
        "constructor",
        "connect",
        "close",
        "sendRequest",
        "echo",
        "uploadBlob",
        "downloadBlob",
    } <= method_names


def test_a_real_inherited_member_carries_correct_provenance() -> None:
    """JmapError (src/models/CommonTypes.js) extends the JS builtin ``Error`` - external and
    unresolvable, so correctly nothing is copied onto JmapError itself. JmapNetworkError and
    JmapProtocolError both extend JmapError directly - the only in-repository, resolvable
    inheritance relationship in this fixture (hand-verified against a fresh clone of the
    pinned commit: every other type's "bases" is either empty or points at an external,
    unresolvable name - "Error" or "CoreClient"). TC-261's ``_flatten_inheritance()``
    correctly copies JmapError's real constructor down onto both subclasses, tagged with
    "inherited_from" rooted at JmapError's own class_import (not a bare name, now that
    TC-315 gives javascript entries a real class_import) - alongside each subclass's own,
    distinct, locally-declared constructor, which never carries the key at all. No deeper
    multi-level chain exists anywhere in this repository's full, untruncated 31-type public
    surface (confirmed by listing every type's "bases" directly): "Error" and "CoreClient"
    are both external to the repo and never themselves resolve as a parent.
    """
    data = _load_fixture()
    by_name: dict[str, list[dict]] = {}
    for entry in data["types"]:
        by_name.setdefault(entry["name"], []).append(entry)

    jmap_error = by_name["JmapError"][0]
    assert jmap_error["bases"] == ["Error"]
    assert jmap_error["class_import"] == "models.CommonTypes.JmapError"
    # JmapError's own constructor is genuinely locally declared - never tagged.
    assert all("inherited_from" not in m for m in jmap_error["methods"])

    for child_name in ("JmapNetworkError", "JmapProtocolError"):
        child = by_name[child_name][0]
        assert child["bases"] == ["JmapError"]
        own = [m for m in child["methods"] if "inherited_from" not in m]
        inherited = [m for m in child["methods"] if "inherited_from" in m]
        # Each subclass declares its own real constructor...
        assert len(own) == 1
        assert own[0]["name"] == "constructor"
        # ...and also inherits JmapError's real constructor, correctly rooted.
        assert len(inherited) == 1
        assert inherited[0]["name"] == "constructor"
        assert inherited[0]["inherited_from"] == jmap_error["class_import"]

    # Every other real type in this fixture (other than the JmapClient/CoreClient and
    # JmapError-family relationships already checked above) has no base at all.
    others = [
        e
        for e in data["types"]
        if e["name"] not in ("JmapClient", "JmapError", "JmapNetworkError", "JmapProtocolError")
    ]
    assert all(e["bases"] == [] for e in others)
