"""Offline tests against the committed jmap/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-170 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_python/api_surface.json``.

JMAP is a genuinely distinct product line from the already-onboarded Email family
(config/products/email/python.yaml) - a separate repository under the same
aspose-email-foss org, not a variant of it.

Like slides/python (its structural template), this fixture was produced by the
independent pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``,
adapted in ``run_extraction._python_types_from_surface``): its ``language`` field reads
literally ``"python"`` and its observed ``kind`` vocabulary across the full 71-entry
surface is only ``{"class"}`` - no ``"enum"`` or ``"function"`` entries exist in this
repository's public surface, and there is no tree-sitter grammar name here at all.

Regenerated under TC-321 (same commit f2137fa6bade0002c317e3a73081f2a0b5173a23 the
previous TC-170-era fixture already pinned - no commit-pin divergence) because that
fixture predated the entire bases/inheritance pipeline (TC-252/261/265/274/281/293/294):
every one of its types had ``bases: []``. This live re-run under the full pipeline now
populates 14 real non-empty ``bases`` lists - but ``_flatten_inheritance()`` still
copies zero ``inherited_from`` tags anywhere, hand-verified against this repository's
own real pinned source (``src/aspose_jmap_foss/``) as a genuine absence, not a stale or
broken run:

- ``JmapError``/``JmapNetworkError``/``JmapProtocolError`` (``models/common_types.py``)
  base to ``Exception``, a stdlib class outside this corpus entirely, and
  ``JmapError`` itself adds no method beyond ``__init__`` (which this pure-``ast``
  reader never captures as a "method" for any Python pilot - leading-underscore names
  are filtered before kind-specific handling).
- ``JmapTransport``/``UrllibJmapTransport`` (``models/transport.py``): the only member
  ``JmapTransport`` declares is the abstract ``send``, and ``UrllibJmapTransport``
  overrides (not additively inherits) that exact method - correctly deduplicated as a
  local override, never a copied inherited member.
- ``JmapClient`` (``__init__.py``) is composed from three real mixins -
  ``_CoreClientMixin`` (``client_core.py``), ``_MailClientMixin`` (``client_mail.py``),
  ``_SubmissionClientMixin`` (``client_submission.py``) - each carrying real public
  methods (``connect``, ``send_request``, ``list_mailboxes``, ``send``, etc.), and every
  one of those three names is leading-underscore and never exported under any public
  alias anywhere in the repository, so none of the three is ever its own public symbol in
  this fixture (unchanged, re-confirmed below). TC-321 recorded that as the reason
  ``_flatten_inheritance`` had no base entry to resolve against, so ``JmapClient`` came
  back with zero methods - a real, structural gap, not a defect in any already-accepted
  pilot's own (still correct) pattern of excluding a private mixin from being its own
  public symbol.

  TC-401's own re-run closes that gap: TC-429 ("give a public Python class its private
  mixins' public methods") is now on main and reads each private mixin's class body
  directly off disk, re-homing its real public methods onto ``JmapClient`` itself - never
  through ``_flatten_inheritance``, so none of them carries "inherited_from". This is the
  concrete, live proof the fix reaches this pilot: ``JmapClient`` now carries 21 real
  methods (``connect``, ``send_request``, ``list_mailboxes``, ``send`` among them),
  observed directly from this run, not guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Python classes, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Account", "Mailbox", "EmailSubmission"} <= names
    assert all(entry["class_import"].startswith("aspose_jmap_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed.
    assert kinds == {"class"}


def test_the_fixture_now_carries_real_bases() -> None:
    data = _load_fixture()
    # TC-321: the pre-regeneration fixture had bases: [] on every single type (it predated
    # TC-265's real bases population entirely). This live re-run recovers 14 real non-empty
    # bases lists - observed directly, not guessed.
    by_class_import = {entry["class_import"]: entry for entry in data["types"]}
    with_bases = [entry for entry in data["types"] if entry.get("bases")]
    assert len(with_bases) == 14
    assert by_class_import["aspose_jmap_foss.JmapNetworkError"]["bases"] == ["JmapError"]
    assert by_class_import["aspose_jmap_foss.JmapProtocolError"]["bases"] == ["JmapError"]
    assert by_class_import["aspose_jmap_foss.UrllibJmapTransport"]["bases"] == ["JmapTransport"]
    assert by_class_import["aspose_jmap_foss.JmapClient"]["bases"] == [
        "_CoreClientMixin",
        "_MailClientMixin",
        "_SubmissionClientMixin",
    ]


def test_jmap_client_now_carries_its_private_mixins_real_public_methods() -> None:
    # TC-401's own re-run: TC-429 ("give a public Python class its private mixins' public
    # methods") is now on main, which did not exist when TC-321 last regenerated this
    # fixture - JmapClient's own real ``methods`` list was ``[]`` then, hand-verified as a
    # genuine absence because its three mixin bases are never public anywhere in this
    # fixture (still true, re-confirmed below) so ``_flatten_inheritance()`` had no base
    # entry to copy from. TC-429 closes exactly this gap: a public class built from
    # non-public bases now gets their real public methods re-homed directly onto itself -
    # never through ``_flatten_inheritance``, so none of these carry "inherited_from".
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    assert not ({"_CoreClientMixin", "_MailClientMixin", "_SubmissionClientMixin"} & names)
    jmap_client = next(entry for entry in data["types"] if entry["name"] == "JmapClient")
    assert jmap_client["bases"] == ["_CoreClientMixin", "_MailClientMixin", "_SubmissionClientMixin"]
    method_names = {m["name"] for m in jmap_client["methods"]}
    # Real public methods of the three mixins, named directly in this module's own
    # docstring (``connect``, ``send_request``, ``list_mailboxes``, ``send``) plus the
    # constructor and context-manager dunders TC-393/TC-473 now also recover - observed
    # directly from this live run, not guessed.
    assert {"connect", "send_request", "list_mailboxes", "send", "__init__"} <= method_names
    assert all("inherited_from" not in m for m in jmap_client["methods"])
    # _flatten_inheritance's own tagging mechanism is still untouched for this pilot:
    # these are re-homed as JmapClient's own methods, never copied-and-tagged.
    total_inherited = sum(
        1
        for entry in data["types"]
        for member in (*entry.get("methods", []), *entry.get("properties", []))
        if "inherited_from" in member
    )
    assert total_inherited == 0
