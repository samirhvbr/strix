"""Test boundaries share the catalog stream and never release another pause."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strix.core import runner
from strix.core.agents import AgentCoordinator
from strix.core.hooks import BudgetPausedError, ReportUsageHooks
from strix.core.test_catalog import TestCatalog
from strix.core.test_pause import TestPauseController, validate_interval
from tests.test_budget_pause_policy import _Scan, _wait_until
from tests.test_cli_target_list import _write_run_record, cli_main
from tests.test_runner_teardown import _wire_runner


if TYPE_CHECKING:
    from pathlib import Path


def wired(tmp_path: Path, n: int = 1):
    coordinator = AgentCoordinator()
    catalog = TestCatalog()
    catalog.set_snapshot_path(tmp_path / "catalog.json")
    pause = TestPauseController(coordinator, tmp_path / "pause.json", pause_every_n_tests=n)
    catalog.set_change_callback(pause.observe)
    coordinator.set_status_change_callback(catalog.mark_status)
    return coordinator, catalog, pause


@pytest.mark.asyncio
async def test_unique_terminal_events_pause_without_counting_root_or_parked_status(tmp_path: Path):
    coordinator, catalog, pause = wired(tmp_path, 2)
    await coordinator.register("root", "root", parent_id=None)
    for agent_id in ("a", "b"):
        await coordinator.register(agent_id, agent_id, parent_id="root")
        catalog.register(agent_id=agent_id, name=agent_id)
    await coordinator.set_status("root", "completed")
    await coordinator.set_status("a", "budget_paused")
    assert pause.completed_since_resume == 0
    await coordinator.set_status("a", "completed")
    await coordinator.set_status("a", "completed")
    assert pause.completed_since_resume == 1
    assert not coordinator.budget_paused
    await coordinator.set_status("b", "failed")
    assert coordinator.pause_reasons == {"test"}
    assert pause.completed_since_resume == 2
    await coordinator.resume_budget(reason="test")
    await coordinator.set_status("a", "running")
    await coordinator.set_status("a", "completed")
    assert pause.completed_since_resume == 0
    assert not coordinator.budget_paused


@pytest.mark.asyncio
async def test_n_one_parks_real_agent_loops_under_stop_policy_without_duplicate_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scan = _Scan(tmp_path, monkeypatch, max_budget_usd=None)
    scan.hooks = ReportUsageHooks(model="test-model", budget_policy="stop")
    scan.coordinator.set_budget_policy("stop")
    catalog = TestCatalog()
    pause = TestPauseController(scan.coordinator, tmp_path / "pause.json", pause_every_n_tests=1)
    catalog.set_change_callback(pause.observe)
    scan.coordinator.set_status_change_callback(catalog.mark_status)
    catalog.register(agent_id="child", name="child")
    with patch("strix.core.hooks.get_global_report_state", return_value=scan.ledger):
        try:
            await scan.start_root(calls=4)
            await scan.start_child("child", calls=1)
            await _wait_until(lambda: scan.coordinator.statuses["root"] == "budget_paused")
            spent = scan.ledger.cost
            assert scan.coordinator.statuses["child"] == "completed"
            await asyncio.sleep(0.025)
            assert scan.ledger.cost == spent
            assert scan.coordinator.pause_reasons == {"test"}
            await scan.coordinator.resume_budget()
            await asyncio.wait_for(asyncio.gather(*scan.tasks()), timeout=5)
            assert scan.ledger.calls.count("root") == 4
            assert scan.ledger.calls.count("child") == 1
            assert scan.ledger.cost == 5
            assert pause.completed_since_resume == 0
        finally:
            await scan.teardown()


@pytest.mark.asyncio
async def test_three_reasons_release_independently_and_budget_is_rechecked_before_dispatch(
    tmp_path: Path,
):
    coordinator, catalog, _ = wired(tmp_path)
    catalog.register(agent_id="done", name="done")
    catalog.mark_status("done", "completed")
    await coordinator.pause_budget(reason="operator")
    hooks = ReportUsageHooks(model="test", max_budget_usd=1, budget_policy="pause")
    coordinator.set_budget_limit_setter(hooks.set_max_budget_usd)
    wrapper = MagicMock(context={"coordinator": coordinator, "agent_id": "root"})
    ledger = MagicMock()
    ledger.get_total_llm_cost.return_value = 1
    with patch("strix.core.hooks.get_global_report_state", return_value=ledger):
        with pytest.raises(BudgetPausedError):
            await hooks.on_llm_start(wrapper, MagicMock(), None, [])
        assert coordinator.pause_reasons == {"test", "operator", "budget"}
        assert await coordinator.resume_budget(reason="test") == []
        assert coordinator.pause_reasons == {"operator", "budget"}
        assert await coordinator.resume_budget(reason="operator") == []
        assert coordinator.pause_reasons == {"budget"}
        await coordinator.resume_budget(reason="budget")
        with pytest.raises(BudgetPausedError):
            await hooks.on_llm_start(wrapper, MagicMock(), None, [])
        assert "llm_turn" not in wrapper.context
        await coordinator.pause_budget(reason="operator")
        await coordinator.resume_budget(reason="budget", max_budget_usd=2)
        with pytest.raises(BudgetPausedError):
            await hooks.on_llm_start(wrapper, MagicMock(), None, [])
        assert coordinator.pause_reasons == {"operator"}
        await coordinator.resume_budget(reason="operator")
        await hooks.on_llm_start(wrapper, MagicMock(), None, [])
        assert wrapper.context["llm_turn"] == 1
        assert ledger.get_total_llm_cost() == 1


@pytest.mark.asyncio
async def test_restart_restores_pending_pause_and_hydration_does_not_recount(tmp_path: Path):
    coordinator, catalog, _ = wired(tmp_path)
    catalog.register(agent_id="done", name="done")
    catalog.mark_status("done", "completed")
    await coordinator.pause_budget(reason="operator")
    saved = await coordinator.snapshot()
    restored = AgentCoordinator()
    await restored.restore(saved)
    await restored.reset_budget_stops(budget_stopped=False, reserve_stopped=False)
    assert restored.pause_reasons == {"operator", "test"}
    loaded = TestCatalog()
    loaded.load(tmp_path / "catalog.json")
    pause = TestPauseController(restored, tmp_path / "pause.json", pause_every_n_tests=None)
    loaded.set_change_callback(pause.observe)
    assert pause.interval == 1
    assert pause.completed_since_resume == 1
    await restored.resume_budget(reason="test")
    assert restored.pause_reasons == {"operator"}
    restarted = TestPauseController(
        AgentCoordinator(), tmp_path / "pause.json", pause_every_n_tests=None
    )
    loaded.set_change_callback(restarted.observe)
    assert restarted.completed_since_resume == 0
    assert not restarted.coordinator.budget_paused


def test_catalog_hydration_repairs_crash_before_pause_snapshot(tmp_path: Path):
    _, catalog, _ = wired(tmp_path)
    catalog.register(agent_id="done", name="done")
    catalog.set_change_callback(None)
    catalog.mark_status("done", "completed")
    restored = AgentCoordinator()
    loaded = TestCatalog()
    loaded.load(tmp_path / "catalog.json")
    pause = TestPauseController(restored, tmp_path / "pause.json", pause_every_n_tests=None)
    loaded.set_change_callback(pause.observe)
    assert restored.pause_reasons == {"test"}
    assert pause.completed_since_resume == 1


@pytest.mark.asyncio
async def test_failed_acknowledgment_never_releases_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    coordinator, catalog, pause = wired(tmp_path)
    catalog.register(agent_id="done", name="done")
    catalog.mark_status("done", "completed")

    def fail(**_kwargs):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(pause, "_write", fail)
    with pytest.raises(OSError):
        await coordinator.resume_budget(reason="test")
    assert coordinator.pause_reasons == {"test"}
    assert json.loads((tmp_path / "pause.json").read_text())["paused"] is True


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_invalid_intervals_fail_before_work(value):
    with pytest.raises(ValueError):
        validate_interval(value)


def test_disabled_default_does_not_create_pause_state(tmp_path: Path):
    coordinator = AgentCoordinator()
    pause = TestPauseController(coordinator, tmp_path / "pause.json", pause_every_n_tests=None)
    catalog = TestCatalog()
    catalog.set_change_callback(pause.observe)
    catalog.register(agent_id="done", name="done")
    catalog.mark_status("done", "completed")
    assert not coordinator.budget_paused
    assert not pause.path.exists()


def test_malformed_state_and_changed_interval_are_refused(tmp_path: Path):
    _, _, pause = wired(tmp_path, 2)
    with pytest.raises(ValueError, match="cannot change"):
        TestPauseController(AgentCoordinator(), pause.path, pause_every_n_tests=3)
    data = json.loads(pause.path.read_text())
    for key, value in [
        ("version", 2),
        ("paused", "false"),
        ("completed_since_resume", -1),
        ("seen", ["duplicate", "duplicate"]),
    ]:
        pause.path.write_text(json.dumps({**data, key: value}))
        with pytest.raises(ValueError):
            TestPauseController(AgentCoordinator(), pause.path, pause_every_n_tests=None)


@pytest.mark.asyncio
async def test_process_death_before_coordinator_snapshot_still_restores_test_pause(tmp_path: Path):
    program = """
import asyncio, json, os, sys
from pathlib import Path
from strix.core.agents import AgentCoordinator
from strix.core.test_catalog import TestCatalog
from strix.core.test_pause import TestPauseController
root = Path(sys.argv[1])
coordinator = AgentCoordinator()
(root / 'agents.json').write_text(json.dumps(asyncio.run(coordinator.snapshot())))
catalog = TestCatalog()
catalog.set_snapshot_path(root / 'catalog.json')
pause = TestPauseController(coordinator, root / 'pause.json', pause_every_n_tests=1)
catalog.set_change_callback(pause.observe)
catalog.register(agent_id='done', name='done')
catalog.mark_status('done', 'completed')
os._exit(19)
"""
    result = subprocess.run(  # noqa: S603 -- Fixed synthetic child, no target/provider dispatch.
        [sys.executable, "-c", program, str(tmp_path)],
        env={**os.environ, "LITELLM_MODE": "PRODUCTION"},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 19, result.stderr.decode()
    coordinator = AgentCoordinator()
    await coordinator.restore(json.loads((tmp_path / "agents.json").read_text()))
    assert not coordinator.budget_paused
    pause = TestPauseController(coordinator, tmp_path / "pause.json", pause_every_n_tests=None)
    assert coordinator.pause_reasons == {"test"}
    assert pause.completed_since_resume == 1


@pytest.mark.asyncio
async def test_runner_uses_one_catalog_stream_for_pause_and_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):

    _wire_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: None)
    coordinator = AgentCoordinator()

    async def run(**kwargs):
        catalog = kwargs["context"]["test_catalog"]
        await coordinator.register("child", "child", parent_id=kwargs["agent_id"])
        catalog.register(agent_id="child", name="child")
        await coordinator.set_status("child", "completed")
        assert coordinator.pause_reasons == {"test"}

    monkeypatch.setattr(runner, "run_agent_loop", run)
    await runner.run_strix_scan(
        scan_config={"targets": []},
        scan_id="fixture",
        image="unused",
        coordinator=coordinator,
        pause_every_n_tests=1,
    )
    assert json.loads((tmp_path / "test_pause.json").read_text())["paused"] is True
    assert (tmp_path / "test_telemetry.db").exists()


@pytest.mark.asyncio
async def test_corrupt_pause_snapshot_refuses_before_sandbox_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):

    _wire_runner(monkeypatch, tmp_path)
    sandbox = AsyncMock()
    monkeypatch.setattr(runner.session_manager, "create_or_reuse", sandbox)
    (tmp_path / "test_pause.json").write_text("{")
    with pytest.raises(ValueError):
        await runner.run_strix_scan(scan_config={"targets": []}, scan_id="fixture", image="unused")
    sandbox.assert_not_called()


def test_cli_restores_interval_and_rejects_invalid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):

    monkeypatch.chdir(tmp_path)
    for value in ["0", "-1", "1.5"]:
        monkeypatch.setattr(
            sys,
            "argv",
            ["strix", "-n", "-t", "https://example.test", "--pause-every-n-tests", value],
        )
        with pytest.raises(SystemExit):
            cli_main.parse_arguments()
    _write_run_record(
        tmp_path / "strix_runs",
        "fixture",
        {
            "run_name": "fixture",
            "user_instruction": "synthetic",
            "pause_every_n_tests": 2,
        },
    )
    monkeypatch.setattr(sys, "argv", ["strix", "-n", "--resume", "fixture"])
    assert cli_main.parse_arguments().pause_every_n_tests == 2
    monkeypatch.setattr(
        sys, "argv", ["strix", "-n", "--resume", "fixture", "--pause-every-n-tests", "3"]
    )
    with pytest.raises(SystemExit):
        cli_main.parse_arguments()


def test_resumed_cli_never_probes_models_before_restoring_pause(monkeypatch: pytest.MonkeyPatch):
    warmup = AsyncMock()
    monkeypatch.setattr(cli_main, "warm_up_llm", warmup)
    monkeypatch.setattr(cli_main, "persist_current", lambda: None)
    monkeypatch.setattr(cli_main, "prepare_run", lambda _args: None)
    monkeypatch.setattr(cli_main, "telemetry_start", lambda _args: None)
    cli_main._bootstrap_scan(SimpleNamespace(resume="fixture", web_authorization=None))
    warmup.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_user_message_cannot_release_an_independent_pause():
    coordinator = AgentCoordinator()
    extender = MagicMock()
    coordinator.set_budget_extender(extender)
    await coordinator.pause_budget(reason="budget")
    await coordinator.pause_budget(reason="test")
    await coordinator.resume_from_budget_pause()
    assert coordinator.pause_reasons == {"budget", "test"}
    extender.assert_not_called()
