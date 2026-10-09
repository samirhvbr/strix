"""Real process ownership and interrupted HTTP/TLS/SSH observations stay incomplete."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import signal
import socket
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from strix.core import runner
from strix.core.run_lease import (
    RunAlreadyOwnedError,
    controller_lease,
    controller_owned,
    exclusive_scan,
)
from strix.core.transport_case import inspect_tls_drained
from tests.test_authorization_case import executor_at as http_executor
from tests.test_transport_case import executor_at as transport_executor


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Controlled profile requires POSIX")


def test_second_controller_is_refused_until_owner_exits(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from strix.core.run_lease import controller_lease, RunAlreadyOwnedError
try:
    with controller_lease(Path(sys.argv[1])):
        print('owned')
except RunAlreadyOwnedError:
    sys.exit(9)
"""
    with controller_lease(tmp_path):
        result = subprocess.run(  # noqa: S603 -- Fixed local ownership fixture.
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 9, result.stderr
        assert not result.stdout
        with (
            pytest.raises(RunAlreadyOwnedError, match="already owns"),
            controller_lease(tmp_path),
        ):
            pytest.fail("second owner entered")
    with controller_lease(tmp_path):
        assert (tmp_path / "controller.lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public"])
def test_unsafe_lock_is_rejected(tmp_path: Path, unsafe: str) -> None:
    destination = tmp_path / "controller.lock"
    source = tmp_path / "unrelated"
    source.write_text("preserved")
    source.chmod(0o600)
    if unsafe == "symlink":
        destination.symlink_to(source)
    elif unsafe == "hardlink":
        os.link(source, destination)
    else:
        destination.write_text("")
        destination.chmod(0o644)
    with pytest.raises(RunAlreadyOwnedError), controller_lease(tmp_path):
        pytest.fail("unsafe lock accepted")
    assert source.read_text() == "preserved"


def test_headless_owner_can_lend_to_one_runner_task(tmp_path: Path) -> None:
    async def execute() -> None:
        async def competing() -> None:
            with pytest.raises(RunAlreadyOwnedError), controller_lease(tmp_path, inherit=True):
                pytest.fail("a second task borrowed ownership")

        with controller_lease(tmp_path, inherit=True):
            assert controller_owned(tmp_path)
            await asyncio.create_task(competing())

    with controller_lease(tmp_path):
        asyncio.run(execute())
    assert not controller_owned(tmp_path)


def test_headless_refusal_cannot_rewrite_reports_or_clean_up_the_live_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = importlib.import_module("tests.test_main_launch")
    interface = fixture.cli_main
    monkeypatch.setattr(interface, "run_dir_for", lambda _: tmp_path)
    cleanup = Mock()
    monkeypatch.setattr(
        fixture.report_state_module, "get_global_report_state", lambda: Mock(cleanup=cleanup)
    )
    with (
        controller_lease(interface.runtime_state_dir(tmp_path)),
        pytest.raises(RunAlreadyOwnedError),
    ):
        fixture._launch(
            monkeypatch,
            needs_setup=False,
            args=argparse.Namespace(
                non_interactive=True,
                needs_setup=False,
                resume_picker=False,
                run_name="scan",
                fail_on=None,
            ),
        )
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_controller_releases_ownership(tmp_path: Path) -> None:
    entered = asyncio.Event()

    @exclusive_scan(lambda _: tmp_path)
    async def run(*, scan_id: str | None = None) -> str | None:
        entered.set()
        await asyncio.Event().wait()
        return scan_id

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    with pytest.raises(RunAlreadyOwnedError):
        await run(scan_id="same")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with controller_lease(tmp_path):
        pass


@pytest.mark.asyncio
async def test_runner_refuses_second_owner_before_binding_or_executor_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "run_dir_for", lambda _: tmp_path)
    binding = Mock(side_effect=AssertionError("policy must not be touched"))
    monkeypatch.setattr(runner, "bind_assessment_policy", binding)
    with (
        controller_lease(runner.runtime_state_dir(tmp_path)),
        pytest.raises(RunAlreadyOwnedError),
    ):
        await asyncio.create_task(
            runner.run_strix_scan(scan_config={}, scan_id="scan", image="unused")
        )
    binding.assert_not_called()


@pytest.mark.asyncio
async def test_tls_cancellation_drains_an_already_dispatched_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, released, finished = threading.Event(), threading.Event(), threading.Event()

    def observation(*_: Any) -> dict[str, Any]:
        entered.set()
        assert released.wait(timeout=5)
        finished.set()
        return {"verdict": "compliant"}

    monkeypatch.setattr("strix.core.transport_case.inspect_tls", observation)
    task = asyncio.create_task(inspect_tls_drained("127.0.0.1", 443, None))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["http", "openssl.tls", "ssh-audit"])
async def test_killed_observation_never_replays_or_becomes_a_clean_result(
    tmp_path: Path, profile: str
) -> None:
    script = """
import asyncio, sys
from pathlib import Path
from strix.core.run_lease import controller_lease
from tests.test_authorization_case import executor_at as http_executor
from tests.test_transport_case import executor_at as transport_executor
path, port, profile = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
async def run():
    with controller_lease(path):
        executor = (http_executor(path, port) if profile == 'http'
                    else transport_executor(path, port, profile))
        await executor.run_case(agent_ref='agent', case_ref=(
            'cross-tenant' if profile == 'http' else 'transport'))
asyncio.run(run())
"""
    # Accept the actual client connection, but never acknowledge the observation.
    with socket.socket() as target:
        target.bind(("127.0.0.1", 0))
        target.listen()
        target.settimeout(20)
        port = target.getsockname()[1]
        child = subprocess.Popen(  # noqa: S603 -- Disposable localhost crash fixture.
            [sys.executable, "-c", script, str(tmp_path), str(port), profile],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            connection, _ = await asyncio.to_thread(target.accept)
            with connection:
                os.killpg(child.pid, signal.SIGKILL)
                _, errors = await asyncio.to_thread(child.communicate, timeout=5)
                assert child.returncode == -signal.SIGKILL, errors.decode()
            # SIGKILL releases kernel ownership. Recovery still requires durable receipts.
            with controller_lease(tmp_path):
                executor = (
                    http_executor(tmp_path, port)
                    if profile == "http"
                    else transport_executor(tmp_path, port, profile)
                )
                try:
                    summary: dict[str, Any] = executor.ledger.summary()
                    assert summary["attempts"] == summary["unresolved_attempts"] == 1
                    assert summary["artifacts"] == 0
                    assert summary["case_evaluations"][0]["verdict"] == "missing"
                    assert summary["obligations"]["essential_unfulfilled"] > 0
                    document = importlib.import_module("tests.test_report_coverage")._document(
                        run_record={"status": "completed", "evidence_ledger": summary},
                    )
                    assert not document["completeness"]["complete"]
                    target.settimeout(0.2)
                    with pytest.raises(TimeoutError):
                        await asyncio.to_thread(target.accept)
                finally:
                    await executor.close()
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                await asyncio.to_thread(child.communicate, timeout=5)
