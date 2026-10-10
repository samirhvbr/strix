"""Real private IPC, independent reasons, replay refusal and durable acknowledgment."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from strix.core import runner
from strix.core.agents import AgentCoordinator
from strix.core.native_control import NativeControl, _read_private
from strix.core.test_catalog import TestCatalog
from strix.core.test_pause import TestPauseController
from strix.telemetry.test_ledger import TestLedger
from tests.test_cli_target_list import cli_main
from tests.test_runner_teardown import _wire_runner


pytestmark = pytest.mark.skipif(os.name != "posix", reason="private Unix IPC")


def test_cli_requires_a_canonical_controller_before_bootstrap(monkeypatch):
    for value in ["not-a-controller", "8A6F0874-69D8-4E37-B120-1D1F6134AAFA"]:
        monkeypatch.setattr(
            sys, "argv", ["strix", "-n", "-t", "https://example.test", "--native-control", value]
        )
        with pytest.raises(SystemExit):
            cli_main.parse_arguments()
    controller = str(uuid.uuid4())
    monkeypatch.setattr(
        sys, "argv", ["strix", "-n", "-t", "https://example.test", "--native-control", controller]
    )
    assert cli_main.parse_arguments().native_control == controller


def setup_control(path: Path, check=None):
    path.mkdir(mode=0o700)
    coordinator = AgentCoordinator()
    coordinator.set_snapshot_path(path / "agents.json")
    catalog = TestCatalog()
    pause = TestPauseController(coordinator, path / "test_pause.json", pause_every_n_tests=1)
    ledger = TestLedger(
        path / "ledger.db", scan_id=path.name, catalog=catalog, owns_agent=lambda _: True
    )

    def observe(unit):
        pause.observe(unit)
        ledger.sync_test(unit)

    catalog.set_change_callback(observe)
    control = NativeControl(
        path,
        scan_id=path.name,
        controller_ref=str(uuid.uuid4()),
        coordinator=coordinator,
        test_pause=pause,
        authorization_check=check,
    )
    return control, catalog, ledger


async def request(control: NativeControl, **values):
    descriptor = _read_private(control.directory / "control.json")
    reader, writer = await asyncio.open_unix_connection(descriptor["socket"])
    try:
        writer.write(
            json.dumps(
                {"controller_ref": control.controller_ref, "token": descriptor["token"], **values}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_two_runs_are_isolated_and_reconnect_does_not_resume(tmp_path, monkeypatch):
    long_temp = tmp_path / ("long-temp-" * 15)
    long_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(long_temp))
    first, _, ledger = setup_control(tmp_path / "first")
    second, _, other_ledger = setup_control(tmp_path / "second")
    try:
        await first.start(ledger)
        await second.start(other_ledger)
        assert first.temporary.name != second.temporary.name
        assert Path(first.temporary.name).stat().st_mode & 0o777 == 0o700
        assert (first.directory / "control.json").stat().st_mode & 0o777 == 0o600
        assert (await request(first, action="pause"))["snapshot"]["pause_reasons"] == ["operator"]
        assert (await request(second, action="status"))["snapshot"]["pause_reasons"] == []
        for _ in range(2):
            result = await request(first, action="status")
            assert result["snapshot"]["pause_reasons"] == ["operator"]
        assert not (await request(first, action="resume", reason="operator", token=second.token))[
            "ok"
        ]
        assert not (await request(first, action="status", controller_ref=second.controller_ref))[
            "ok"
        ]
    finally:
        socket = _read_private(first.directory / "control.json")["socket"]
        await first.close()
        await second.close()
        ledger.close()
        other_ledger.close()
    assert not Path(socket).exists()
    assert not (first.directory / "control.json").exists()


@pytest.mark.asyncio
async def test_reasons_are_independent_and_old_ack_cannot_release_next_test(tmp_path):
    check = Mock()
    control, catalog, ledger = setup_control(tmp_path / "scan", check)
    try:
        await control.start(ledger)
        catalog.register(agent_id="first", name="DO NOT EXPOSE TASK OR SECRET")
        catalog.mark_status("first", "completed")
        await control.coordinator.pause_budget(reason="budget")
        paused = (await request(control, action="pause"))["snapshot"]
        assert paused["pause_reasons"] == ["budget", "operator", "test"]
        result = await request(
            control, action="resume", reason="operator", revision=paused["revision"]
        )
        assert result["snapshot"]["pause_reasons"] == ["budget", "test"]
        ack = {"action": "resume", "reason": "test", "revision": result["snapshot"]["revision"]}
        assert (await request(control, **ack))["snapshot"]["pause_reasons"] == ["budget"]
        assert check.call_count == 2
        catalog.register(agent_id="second", name="Second")
        catalog.mark_status("second", "completed")
        assert not (await request(control, **ack))["ok"]
        assert control.coordinator.pause_reasons == {"budget", "test"}
        assert not (
            await request(
                control,
                action="resume",
                reason="budget",
                revision=control.revision(),
                max_budget_usd=100,
            )
        )["ok"]
        snapshot = (await request(control, action="status"))["snapshot"]
        assert "DO NOT EXPOSE" not in json.dumps(snapshot)
        assert len(snapshot["telemetry"]["tests"]) == 2
    finally:
        await control.close()
        ledger.close()


@pytest.mark.asyncio
async def test_expired_authorization_and_failed_storage_leave_gate_closed(tmp_path, monkeypatch):
    control, _, ledger = setup_control(tmp_path / "scan", Mock(side_effect=ValueError("expired")))
    try:
        await control.start(ledger)
        await request(control, action="pause")
        assert not (
            await request(control, action="resume", reason="operator", revision=control.revision())
        )["ok"]
        assert control.coordinator.pause_reasons == {"operator"}
        control.authorization_check = None
        monkeypatch.setattr(
            "strix.core.native_control._write_private", Mock(side_effect=OSError("disk full"))
        )
        assert not (
            await request(control, action="resume", reason="operator", revision=control.revision())
        )["ok"]
        assert control.coordinator.pause_reasons == {"operator"}
    finally:
        await control.close()
        ledger.close()


@pytest.mark.asyncio
async def test_operator_pause_restores_without_coordinator_snapshot(tmp_path):
    control, _, ledger = setup_control(tmp_path / "scan")
    try:
        await control.start(ledger)
        await request(control, action="pause")
        # Model the crash gap: the durable operator acknowledgment is authoritative.
        coordinator = AgentCoordinator()
        pause = TestPauseController(
            coordinator, control.directory / "test_pause.json", pause_every_n_tests=None
        )
        restored = NativeControl(
            control.directory,
            scan_id="scan",
            controller_ref=str(uuid.uuid4()),
            coordinator=coordinator,
            test_pause=pause,
        )
        assert restored.coordinator.pause_reasons == {"operator"}
    finally:
        await control.close()
        ledger.close()


@pytest.mark.asyncio
async def test_oversized_or_unfinished_clients_cannot_hold_shutdown(tmp_path):
    control, _, ledger = setup_control(tmp_path / "scan")
    await control.start(ledger)
    descriptor = _read_private(control.directory / "control.json")
    reader, writer = await asyncio.open_unix_connection(descriptor["socket"])
    writer.write(b"x" * 5000 + b"\n")
    await writer.drain()
    assert not json.loads(await reader.readline())["ok"]
    writer.close()
    await writer.wait_closed()
    _, idle = await asyncio.open_unix_connection(descriptor["socket"])
    await asyncio.wait_for(control.close(), 1)
    idle.close()
    await idle.wait_closed()
    ledger.close()


def test_accounting_snapshot_marks_truncation_and_excludes_incident_text(tmp_path):
    _control, catalog, ledger = setup_control(tmp_path / "scan")
    try:
        for agent in ("first", "second"):
            catalog.register(agent_id=agent, name="credential", task="https://secret.invalid/token")
            ledger.record_status(agent, "failed")
        snapshot = ledger.snapshot(limit=1)
        assert snapshot["truncated"]
        assert len(snapshot["tests"]) == len(snapshot["incidents"]) == 1
        assert "secret.invalid" not in json.dumps(snapshot)
        assert "message" not in snapshot["incidents"][0]
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["success", "failure", "cancelled"])
async def test_runner_owns_control_for_its_exact_lifetime(tmp_path, monkeypatch, ending):
    _wire_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: None)
    controller = str(uuid.uuid4())
    observed = []

    async def run(**kwargs):
        discovery = _read_private(tmp_path / "control.json")
        assert discovery["controller_ref"] == controller
        reader, writer = await asyncio.open_unix_connection(discovery["socket"])
        writer.write(json.dumps({**discovery, "action": "pause"}).encode() + b"\n")
        await writer.drain()
        result = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
        assert result["snapshot"]["pause_reasons"] == ["operator"]
        assert kwargs["coordinator"].pause_reasons == {"operator"}
        observed.append(discovery["socket"])
        if ending == "failure":
            raise RuntimeError("fixture failure")
        if ending == "cancelled":
            raise asyncio.CancelledError

    monkeypatch.setattr(runner, "run_agent_loop", run)
    invocation = runner.run_strix_scan(
        scan_config={"targets": []}, scan_id="fixture", image="unused", native_control=controller
    )
    if ending == "success":
        await invocation
    else:
        with pytest.raises(RuntimeError if ending == "failure" else asyncio.CancelledError):
            await invocation
    assert len(observed) == 1
    assert not Path(observed[0]).exists()
    assert not (tmp_path / "control.json").exists()


@pytest.mark.parametrize("contents", ['{"version":1,"paused":"false"}', "{"])
def test_invalid_operator_pause_is_not_silently_discarded(tmp_path, contents):
    path = tmp_path / "operator_pause.json"
    path.write_text(contents)
    path.chmod(0o600)
    coordinator = AgentCoordinator()
    pause = TestPauseController(coordinator, tmp_path / "test_pause.json", pause_every_n_tests=None)
    with pytest.raises(ValueError):
        NativeControl(
            tmp_path,
            scan_id="fixture",
            controller_ref=str(uuid.uuid4()),
            coordinator=coordinator,
            test_pause=pause,
        )
