"""Offline tests for the ``live`` marker hooks in tests/conftest.py (TC-216).

The hooks are called directly with small fake items and a fake config, so these tests
prove the deselect and summary behaviour without touching the network.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

import tests.conftest as conftest

OPT_IN = "FOSS_MCP_NETWORK_TESTS"


class FakeItem:
    def __init__(self, name: str, live: bool) -> None:
        self.name = name
        self._live = live

    def get_closest_marker(self, name: str):
        if name == "live" and self._live:
            return object()
        return None


class FakeHook:
    def __init__(self) -> None:
        self.deselected: list[FakeItem] = []

    def pytest_deselected(self, items) -> None:
        self.deselected.extend(items)


def make_config():
    hook = FakeHook()
    return SimpleNamespace(hook=hook, stash=pytest.Stash()), hook


def test_live_item_dropped_and_plain_item_kept_when_opt_in_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPT_IN, raising=False)
    config, hook = make_config()
    live = FakeItem("live_one", live=True)
    plain = FakeItem("plain_one", live=False)
    items = [live, plain]

    conftest.pytest_collection_modifyitems(config, items)

    assert items == [plain]
    assert hook.deselected == [live]


def test_live_items_kept_when_opt_in_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPT_IN, "1")
    config, hook = make_config()
    live = FakeItem("live_one", live=True)
    plain = FakeItem("plain_one", live=False)
    items = [live, plain]

    conftest.pytest_collection_modifyitems(config, items)

    assert items == [live, plain]
    assert hook.deselected == []


def test_opt_in_other_than_one_still_deselects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPT_IN, "true")
    config, hook = make_config()
    live = FakeItem("live_one", live=True)
    items = [live]

    conftest.pytest_collection_modifyitems(config, items)

    assert items == []
    assert hook.deselected == [live]


def test_summary_prints_count_only_when_something_was_deselected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPT_IN, raising=False)
    lines: list[str] = []
    reporter = SimpleNamespace(write_line=lines.append)

    config, _ = make_config()
    conftest.pytest_collection_modifyitems(config, [FakeItem("plain_one", live=False)])
    conftest.pytest_terminal_summary(reporter, 0, config)
    assert lines == []

    config, _ = make_config()
    conftest.pytest_collection_modifyitems(
        config, [FakeItem("live_one", live=True), FakeItem("live_two", live=True)]
    )
    conftest.pytest_terminal_summary(reporter, 0, config)
    assert lines == [f"2 live test(s) deselected; set {OPT_IN}=1 to run them"]


def test_java_live_test_carries_live_marker() -> None:
    module = importlib.import_module("tests.extraction.tree_sitter_engine.test_api_surface_java_live")
    marks = [m.name for m in getattr(module.test_java_record_live_regression, "pytestmark", [])]
    assert "live" in marks
    assert not any(m.name == "skipif" for m in getattr(module.test_java_record_live_regression, "pytestmark", []))
