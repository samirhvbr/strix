"""Real private IPC, independent reasons, replay refusal and durable acknowledgment."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
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


async def answer_on_a_small_socket_buffer(tmp_path, monkeypatch, size):
    """A started control whose ``status`` answer is ``size`` bytes, sent on a tiny send buffer.

    macOS local stream sockets default to about 8 KB of send buffer where Linux's is about
    200 KB, which is why a large answer only breaks there. The real accept path runs
    untouched; the only change is ``SO_SNDBUF`` forced small on the accepted socket before it
    answers, and a ledger whose snapshot is as large as asked.
    """
    control, _, ledger = setup_control(tmp_path / "scan")
    accept = control._accept

    async def with_small_buffer(reader, writer):
        writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        await accept(reader, writer)

    monkeypatch.setattr(control, "_accept", with_small_buffer)
    await control.start(ledger)
    control.ledger = SimpleNamespace(snapshot=lambda: {"padding": "x" * size})
    return control, ledger


async def ask_status(control):
    descriptor = _read_private(control.directory / "control.json")
    reader, writer = await asyncio.open_unix_connection(descriptor["socket"])
    request = {"controller_ref": control.controller_ref, "token": descriptor["token"]}
    writer.write(json.dumps({**request, "action": "status"}).encode() + b"\n")
    await writer.drain()
    return reader, writer


@pytest.mark.asyncio
async def test_a_large_answer_reaches_a_slow_client_whole_on_a_small_socket_buffer(
    tmp_path, monkeypatch
):
    size = 200_000
    control, ledger = await answer_on_a_small_socket_buffer(tmp_path, monkeypatch, size)
    reader, writer = await ask_status(control)
    received = bytearray()

    async def read_in_small_chunks():
        while chunk := await reader.read(8192):
            received.extend(chunk)
            await asyncio.sleep(0.001)

    try:
        await asyncio.wait_for(read_in_small_chunks(), 10)
    finally:
        writer.close()
        await asyncio.wait_for(control.close(), 5)
        ledger.close()
    # The Desktop refuses an answer that does not end in a newline, and a cut answer cannot parse.
    assert received.endswith(b"\n")
    answer = json.loads(received)
    assert answer["ok"] is True
    assert len(answer["snapshot"]["telemetry"]["padding"]) == size


@pytest.mark.asyncio
async def test_a_client_that_never_reads_cannot_hold_the_handler_or_the_shutdown(
    tmp_path, monkeypatch
):
    control, ledger = await answer_on_a_small_socket_buffer(tmp_path, monkeypatch, 200_000)
    loop = asyncio.get_running_loop()
    _, writer = await ask_status(control)
    try:
        started = loop.time()
        await asyncio.sleep(0.5)
        assert control._clients, "the answer should still be waiting for a client that never reads"
        while control._clients:
            assert loop.time() - started < 3, (
                "the handler is holding on to a client that never reads"
            )
            await asyncio.sleep(0.05)
        await asyncio.wait_for(control.close(), 1)
    finally:
        # The client leaves last, so a handler that wrongly waits for it fails the test above
        # instead of hanging the suite here.
        writer.close()
        await asyncio.wait_for(control.close(), 5)
        ledger.close()
