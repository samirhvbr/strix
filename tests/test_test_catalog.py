"""Tests for strix/core/test_catalog.py (Spec 01, .continue/pentest).

Covers the catalog in isolation (register/mark_status/persistence/resume) and
its wiring into AgentCoordinator's on_status_change callback -- not the deep
execution.py/runner.py spawn path, which has no unit-test precedent in this
suite (see tests/test_execution.py: it exercises AgentCoordinator directly,
never spawn_child_agent's dependency chain).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from strix.core.agents import AgentCoordinator
from strix.core.test_catalog import TestCatalog, catalog_from_context


if TYPE_CHECKING:
    from pathlib import Path


# --- TestCatalog.register ---------------------------------------------------


def test_register_catalogs_a_new_test() -> None:
    catalog = TestCatalog()
    unit = catalog.register(agent_id="a1", name="XSS Specialist", skills=["xss"], task="probe")

    assert unit.agent_id == "a1"
    assert unit.name == "XSS Specialist"
    assert unit.test_id.startswith("test_")
    assert unit.source == "emergent"
    assert unit.status == "running"
    assert unit.created_at
    assert unit.started_at == unit.created_at
    assert unit.ended_at is None
    assert unit.parent_test_id is None


def test_register_infers_recognized_vuln_class() -> None:
    catalog = TestCatalog()
    unit = catalog.register(agent_id="a1", name="XSS Specialist", skills=["xss"], task="")
    assert unit.vuln_class == "xss"


def test_register_prefers_first_matching_skill() -> None:
    catalog = TestCatalog()
    unit = catalog.register(
        agent_id="a1", name="Multi", skills=["reconnaissance", "sql_injection"], task=""
    )
    assert unit.vuln_class == "sql_injection"


def test_register_unrecognized_skill_leaves_vuln_class_none() -> None:
    catalog = TestCatalog()
    unit = catalog.register(agent_id="a1", name="Odd", skills=["not_a_real_skill"], task="")
    assert unit.vuln_class is None
    # Unrecognized does not mean uncatalogued.
    assert catalog._tests["a1"] is unit


def test_register_accepts_skill_with_category_prefix() -> None:
    catalog = TestCatalog()
    unit = catalog.register(agent_id="a1", name="XSS", skills=["vulnerabilities/xss"], task="")
    assert unit.vuln_class == "xss"


def test_register_extracts_surface_best_effort() -> None:
    catalog = TestCatalog()
    unit = catalog.register(
        agent_id="a1", name="XSS", skills=["xss"], task="test https://lab.example.com/login"
    )
    assert unit.surface == "https://lab.example.com/login"


def test_register_without_extractable_surface_stays_none() -> None:
    catalog = TestCatalog()
    unit = catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="look for stuff")
    assert unit.surface is None


def test_register_links_parent_test_id_when_parent_is_catalogued() -> None:
    catalog = TestCatalog()
    parent = catalog.register(agent_id="root-child", name="Recon", skills=[], task="")
    child = catalog.register(
        agent_id="a2", name="XSS", skills=["xss"], task="", parent_agent_id="root-child"
    )
    assert child.parent_test_id == parent.test_id


def test_register_root_agent_as_parent_leaves_parent_test_id_none() -> None:
    """The root agent (D6 §2.4) is never registered, so a child spawned
    directly under it has no catalogued parent test."""
    catalog = TestCatalog()
    unit = catalog.register(
        agent_id="a1", name="XSS", skills=["xss"], task="", parent_agent_id="root"
    )
    assert unit.parent_test_id is None


# --- TestCatalog.mark_status -------------------------------------------------


def test_mark_status_updates_known_test() -> None:
    catalog = TestCatalog()
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.mark_status("a1", "completed")
    assert catalog._tests["a1"].status == "completed"
    assert catalog._tests["a1"].ended_at is not None


@pytest.mark.parametrize("status", ["completed", "stopped", "crashed", "failed"])
def test_mark_status_sets_ended_at_once_for_every_terminal_status(status: str) -> None:
    catalog = TestCatalog()
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.mark_status("a1", status)
    first_ended = catalog._tests["a1"].ended_at
    assert first_ended is not None
    # A second terminal transition (should not happen, but must not clobber).
    catalog.mark_status("a1", "completed")
    assert catalog._tests["a1"].ended_at == first_ended


def test_mark_status_ignores_unknown_agent() -> None:
    """The root agent, in particular (it is never registered) -- must not
    raise and must not create a phantom entry."""
    catalog = TestCatalog()
    catalog.mark_status("root-agent-id", "completed")
    assert catalog._tests == {}


@pytest.mark.parametrize("status", ["waiting", "budget_paused"])
def test_mark_status_ignores_coordinator_only_statuses(status: str) -> None:
    catalog = TestCatalog()
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.mark_status("a1", status)
    # Still "running" -- a pause/wait is not a different *test* status.
    assert catalog._tests["a1"].status == "running"
    assert catalog._tests["a1"].ended_at is None


# --- persistence / resume ----------------------------------------------------


def test_register_persists_to_the_snapshot_path(tmp_path: Path) -> None:
    path = tmp_path / "test_catalog.json"
    catalog = TestCatalog()
    catalog.set_snapshot_path(path)
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tests"]["a1"]["name"] == "XSS"
    assert data["tests"]["a1"]["vuln_class"] == "xss"


def test_mark_status_persists_the_transition(tmp_path: Path) -> None:
    path = tmp_path / "test_catalog.json"
    catalog = TestCatalog()
    catalog.set_snapshot_path(path)
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.mark_status("a1", "completed")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tests"]["a1"]["status"] == "completed"
    assert data["tests"]["a1"]["ended_at"] is not None


def test_write_without_snapshot_path_is_a_harmless_noop() -> None:
    catalog = TestCatalog()
    # No set_snapshot_path call -- must not raise.
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.mark_status("a1", "completed")


def test_load_restores_entries_from_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "test_catalog.json"
    original = TestCatalog()
    original.set_snapshot_path(path)
    original.register(agent_id="a1", name="XSS Specialist", skills=["xss"], task="probe it")
    original.mark_status("a1", "completed")

    resumed = TestCatalog()
    resumed.set_snapshot_path(path)
    resumed.load()

    assert resumed._tests.keys() == {"a1"}
    restored = resumed._tests["a1"]
    assert restored.name == "XSS Specialist"
    assert restored.status == "completed"
    assert restored.ended_at is not None


def test_load_is_a_noop_on_first_run_with_no_file(tmp_path: Path) -> None:
    catalog = TestCatalog()
    catalog.set_snapshot_path(tmp_path / "does-not-exist.json")
    catalog.load()  # must not raise
    assert catalog._tests == {}


def test_load_skips_malformed_entries_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "test_catalog.json"
    path.write_text(
        json.dumps({"tests": {"a1": {"not": "a valid TestUnit shape"}}}), encoding="utf-8"
    )
    catalog = TestCatalog()
    catalog.set_snapshot_path(path)
    catalog.load()  # must not raise
    assert catalog._tests == {}


def test_load_tolerates_unreadable_json(tmp_path: Path) -> None:
    path = tmp_path / "test_catalog.json"
    path.write_text("{not json", encoding="utf-8")
    catalog = TestCatalog()
    catalog.set_snapshot_path(path)
    catalog.load()  # must not raise
    assert catalog._tests == {}


def test_resume_does_not_duplicate_entries_across_a_second_register() -> None:
    """Mirrors the acceptance criterion: strix --resume must never duplicate
    or lose entries. A second register() with the same agent_id (should not
    happen in practice -- create_agent mints a fresh id each time -- but
    confirms register() is a plain upsert, consistent with how
    AgentCoordinator.register behaves)."""
    catalog = TestCatalog()
    catalog.register(agent_id="a1", name="XSS", skills=["xss"], task="")
    catalog.register(agent_id="a1", name="XSS (renamed)", skills=["xss"], task="")
    assert len(catalog._tests) == 1
    assert catalog._tests["a1"].name == "XSS (renamed)"


# --- catalog_from_context ----------------------------------------------


def test_catalog_from_context_returns_the_catalog() -> None:
    catalog = TestCatalog()
    assert catalog_from_context({"test_catalog": catalog}) is catalog


def test_catalog_from_context_returns_none_when_absent_or_wrong_type() -> None:
    assert catalog_from_context({}) is None
    assert catalog_from_context({"test_catalog": "not-a-catalog"}) is None


# --- wiring into AgentCoordinator.on_status_change --------------------------


@pytest.mark.asyncio
async def test_coordinator_callback_drives_the_catalog_end_to_end() -> None:
    """The integration this spec exists for: AgentCoordinator never imports
    TestCatalog, but wiring set_status_change_callback(catalog.mark_status)
    is enough for every coordinator status transition to reach it."""
    coordinator = AgentCoordinator()
    catalog = TestCatalog()
    coordinator.set_status_change_callback(catalog.mark_status)

    await coordinator.register("root", "Root Agent", parent_id=None)
    await coordinator.register("a1", "XSS Specialist", parent_id="root", skills=["xss"])
    # The spawn path (execution.spawn_child_agent) calls catalog.register
    # itself, in lock-step with coordinator.register -- reproduced by hand
    # here since this test exercises the coordinator in isolation.
    catalog.register(
        agent_id="a1", name="XSS Specialist", skills=["xss"], task="", parent_agent_id="root"
    )

    await coordinator.set_status("a1", "completed")

    assert catalog._tests["a1"].status == "completed"
    assert catalog._tests["a1"].ended_at is not None
    # The root was never registered with the catalog -- the callback must not
    # have invented an entry for it.
    assert "root" not in catalog._tests


@pytest.mark.asyncio
async def test_coordinator_callback_is_optional() -> None:
    """A coordinator with no callback registered behaves exactly as before
    this spec -- set_status must not raise."""
    coordinator = AgentCoordinator()
    await coordinator.register("root", "Root Agent", parent_id=None)
    await coordinator.set_status("root", "completed")
    assert coordinator.statuses["root"] == "completed"


@pytest.mark.asyncio
async def test_coordinator_callback_exception_does_not_break_set_status() -> None:
    """A broken listener must never take the scan down with it."""
    coordinator = AgentCoordinator()

    def _broken(_agent_id: str, _status: str) -> None:
        raise RuntimeError("listener bug")

    coordinator.set_status_change_callback(_broken)
    await coordinator.register("root", "Root Agent", parent_id=None)
    await coordinator.set_status("root", "completed")  # must not raise
    assert coordinator.statuses["root"] == "completed"
