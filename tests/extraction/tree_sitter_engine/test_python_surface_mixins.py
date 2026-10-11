"""TC-429: a public Python class gains the public methods of its private mixin bases.

``JmapClient`` is built from ``_CoreClientMixin``, ``_MailClientMixin`` and
``_SubmissionClientMixin``; the mixins are private so never public symbols, and nothing
flattened inheritance, so the client was served with no methods. Offline: synthetic packages
in ``tmp_path``, no clone.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine.python_surface import (
    PublicSymbol,
    inspect_public_surface,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _symbols(root: Path) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, ["pkg"]).symbols}


def _jmap(root: Path) -> None:
    _write(
        root,
        "pkg/__init__.py",
        "from .client_core import _CoreClientMixin\n"
        "from .client_mail import _MailClientMixin\n"
        "from .client_submission import _SubmissionClientMixin\n\n\n"
        "class JmapClient(_CoreClientMixin, _MailClientMixin, _SubmissionClientMixin):\n"
        "    '''The JMAP client.'''\n",
    )
    _write(
        root,
        "pkg/client_core.py",
        "class _CoreClientMixin:\n"
        "    def connect(self, url: str) -> None:\n        pass\n\n"
        "    def _secret(self):\n        pass\n",
    )
    _write(
        root,
        "pkg/client_mail.py",
        "class _MailClientMixin:\n"
        "    def list_mailboxes(self, account_id: str) -> list:\n        '''List mailboxes.'''\n\n"
        "    async def get_mailbox(self, mailbox_id: str):\n        pass\n\n"
        "    def create_mailbox(self, name, *, parent=None, **extra):\n        pass\n\n"
        "    @property\n    def inbox(self) -> str:\n        return ''\n\n"
        "    def _helper(self):\n        pass\n",
    )
    _write(
        root,
        "pkg/client_submission.py",
        "class _SubmissionClientMixin:\n    def submit(self, message: bytes) -> str:\n        pass\n",
    )


def test_the_jmap_shape_serves_the_mail_core_and_submission_methods(tmp_path: Path) -> None:
    _jmap(tmp_path)
    symbols = _symbols(tmp_path)
    for name in ("list_mailboxes", "get_mailbox", "create_mailbox", "connect", "submit"):
        assert f"pkg.JmapClient.{name}" in symbols, name


def test_a_contributed_method_keeps_kind_signature_and_params_from_the_mixin(tmp_path: Path) -> None:
    _jmap(tmp_path)
    symbols = _symbols(tmp_path)
    listed = symbols["pkg.JmapClient.list_mailboxes"]
    assert listed.kind == "method"
    assert listed.signature == "def list_mailboxes(self, account_id: str) -> list"
    assert listed.return_type == "list"
    assert listed.param_types == (("account_id", "str"),)
    assert listed.docstring == "List mailboxes."
    assert symbols["pkg.JmapClient.get_mailbox"].signature == "async def get_mailbox(self, mailbox_id: str)"
    assert symbols["pkg.JmapClient.create_mailbox"].params == (("name", ""), ("parent", ""), ("**extra", ""))
    assert symbols["pkg.JmapClient.inbox"].kind == "property"


def test_a_contributed_method_is_rehomed_to_the_subclass_location(tmp_path: Path) -> None:
    _jmap(tmp_path)
    symbols = _symbols(tmp_path)
    client = symbols["pkg.JmapClient"]
    method = symbols["pkg.JmapClient.list_mailboxes"]
    assert method.module == "pkg"
    assert method.source_path == "pkg/__init__.py"
    assert method.line == client.line
    assert method.name == "list_mailboxes"
    assert method.public_by == client.public_by


def test_the_mixins_themselves_stay_private_symbols(tmp_path: Path) -> None:
    _jmap(tmp_path)
    names = set(_symbols(tmp_path))
    assert not any("_MailClientMixin" in name and name.endswith(".list_mailboxes") for name in names)


def test_a_mixins_underscore_methods_are_not_copied(tmp_path: Path) -> None:
    _jmap(tmp_path)
    symbols = _symbols(tmp_path)
    assert "pkg.JmapClient._secret" not in symbols
    assert "pkg.JmapClient._helper" not in symbols


def test_a_mixin_constructor_is_copied_only_when_the_subclass_has_none(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._m import _Mixin\n\n\nclass A(_Mixin):\n    pass\n")
    _write(tmp_path, "pkg/_m.py", "class _Mixin:\n    def __init__(self, url: str):\n        pass\n")
    assert "pkg.A.__init__" in _symbols(tmp_path)
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from ._m import _Mixin\n\n\nclass A(_Mixin):\n    def __init__(self, token):\n        pass\n",
    )
    init = _symbols(tmp_path)["pkg.A.__init__"]
    assert init.params == (("token", ""),)


def test_the_subclasss_own_method_of_the_same_name_wins(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from ._m import _Mixin\n\n\nclass A(_Mixin):\n    def run(self, own: int) -> int:\n        pass\n",
    )
    _write(tmp_path, "pkg/_m.py", "class _Mixin:\n    def run(self, theirs: str) -> str:\n        pass\n")
    run = _symbols(tmp_path)["pkg.A.run"]
    assert run.params == (("own", "int"),)
    assert run.line == 5


def test_the_left_base_wins_over_the_right(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "class _Left:\n    def go(self, left: int):\n        pass\n\n\n"
        "class _Right:\n    def go(self, right: int):\n        pass\n\n    def only_right(self):\n        pass\n\n\n"
        "class A(_Left, _Right):\n    pass\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.A.go"].params == (("left", "int"),)
    assert "pkg.A.only_right" in symbols


def test_a_private_mixins_own_private_base_contributes_too(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from ._m import _Mixin\n\n\nclass A(_Mixin):\n    pass\n",
    )
    _write(
        tmp_path,
        "pkg/_m.py",
        "class _Deep:\n    def deep(self):\n        pass\n\n    def shared(self):\n        '''deep'''\n\n\n"
        "class _Mixin(_Deep):\n    def shallow(self):\n        pass\n\n    def shared(self):\n        '''shallow'''\n",
    )
    symbols = _symbols(tmp_path)
    assert "pkg.A.shallow" in symbols
    assert "pkg.A.deep" in symbols
    assert symbols["pkg.A.shared"].docstring == "shallow"


def test_a_public_bases_methods_are_not_copied(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "class Base:\n    def base_method(self):\n        pass\n\n\n"
        "class A(Base):\n    def own(self):\n        pass\n",
    )
    symbols = _symbols(tmp_path)
    assert "pkg.Base.base_method" in symbols
    assert "pkg.A.base_method" not in symbols
    assert "pkg.A.own" in symbols


def test_a_base_defined_in_a_private_module_counts_as_non_public(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._impl import Core\n\n\nclass A(Core):\n    pass\n")
    _write(tmp_path, "pkg/_impl.py", "class Core:\n    def from_private_module(self):\n        pass\n")
    assert "pkg.A.from_private_module" in _symbols(tmp_path)


def test_a_mixin_defined_in_the_same_module_is_resolved(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "class A(_Mixin):\n    pass\n\n\nclass _Mixin:\n    def local(self):\n        pass\n",
    )
    assert "pkg.A.local" in _symbols(tmp_path)


def test_an_unresolvable_base_changes_nothing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from ._gone import _Missing\nfrom elsewhere import _External\n\n\n"
        "class A(_Missing, _External, _Undefined, mod._Attr):\n    def own(self):\n        pass\n",
    )
    symbols = _symbols(tmp_path)
    assert sorted(name for name in symbols if name.startswith("pkg.A")) == ["pkg.A", "pkg.A.own"]


def test_a_base_cycle_terminates_without_change(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "class _X(_Y):\n    def x(self):\n        pass\n\n\n"
        "class _Y(_X):\n    def y(self):\n        pass\n\n\n"
        "class A(_X):\n    pass\n",
    )
    symbols = _symbols(tmp_path)
    assert "pkg.A.x" in symbols
    assert "pkg.A.y" in symbols


def test_a_forwarding_cycle_of_modules_terminates_with_no_change(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._a import _Mixin\n\n\nclass A(_Mixin):\n    pass\n")
    _write(tmp_path, "pkg/_a.py", "from ._b import _Mixin\n")
    _write(tmp_path, "pkg/_b.py", "from ._a import _Mixin\n")
    symbols = _symbols(tmp_path)
    assert sorted(name for name in symbols if name.startswith("pkg.A")) == ["pkg.A"]


def test_a_class_whose_bases_are_all_public_produces_todays_symbols(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "class Base:\n    def b(self):\n        pass\n\n\nclass A(Base, object):\n    def a(self):\n        pass\n",
    )
    names = sorted(_symbols(tmp_path))
    assert names == ["pkg", "pkg.A", "pkg.A.a", "pkg.Base", "pkg.Base.b"]


def test_a_class_reached_through_a_reexport_gains_its_private_mixins_methods(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._client import JmapClient\n")
    _write(
        tmp_path,
        "pkg/_client.py",
        "from ._mail import _MailMixin\n\n\n"
        "class JmapClient(_MailMixin):\n    def own(self):\n        pass\n",
    )
    _write(tmp_path, "pkg/_mail.py", "class _MailMixin:\n    def list_mailboxes(self):\n        pass\n")
    symbols = _symbols(tmp_path)
    assert symbols["pkg.JmapClient"].public_by == "reexport"
    assert "pkg.JmapClient.own" in symbols
    method = symbols["pkg.JmapClient.list_mailboxes"]
    assert method.module == "pkg"
    assert method.source_path == "pkg/__init__.py"


def test_contributed_methods_attach_to_the_class_in_the_extracted_types(tmp_path: Path) -> None:
    _jmap(tmp_path)
    surface = inspect_public_surface(tmp_path, ["pkg"])
    types = {entry["name"]: entry for entry in run_extraction._python_types_from_surface(surface)}
    client = next(entry for name, entry in types.items() if name.endswith("JmapClient"))
    method_names = {method["name"] for method in client.get("methods", [])}
    assert {"list_mailboxes", "get_mailbox", "create_mailbox", "connect", "submit"} <= method_names
