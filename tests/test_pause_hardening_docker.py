"""Opt-in real Docker/process evidence; provider and installed-app acceptance stays open."""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from strix.core.agents import AgentCoordinator
from strix.core.run_lease import controller_lease
from strix.core.sessions import open_agent_session, recover_session_items
from strix.core.test_catalog import TestCatalog
from strix.core.test_pause import TestPauseController
from strix.runtime.docker_connection import connect_docker


pytestmark = pytest.mark.skipif(
    os.getenv("STRIX_PAUSE_HARDENING_TESTS") != "1", reason="requires owned local Docker fixture"
)
IMAGE = "shvia-strix-network-fixture:1"
SERVER = """
import http.server, time
class Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        print('HIT ' + self.path, flush=True)
        if self.path.startswith('/slow/'):
            time.sleep(3)
        body = b'owned disposable target'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
server = http.server.ThreadingHTTPServer(('127.0.0.1', 18080), Target)
print('READY', flush=True)
server.serve_forever()
"""


def emit(value: Any) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


async def worker(directory: Path, container_id: str) -> None:  # noqa: PLR0912, PLR0915
    """Drive production durable components without replacing any implementation."""
    os.umask(0o077)
    directory.mkdir(mode=0o700, exist_ok=True)
    docker = connect_docker()
    target = docker.containers.get(container_id)
    with controller_lease(directory):
        coordinator = AgentCoordinator()
        catalog = TestCatalog()
        coordinator.set_snapshot_path(directory / "agents.json")
        catalog.set_snapshot_path(directory / "catalog.json")
        if (directory / "agents.json").exists():
            await coordinator.restore(json.loads((directory / "agents.json").read_text()))
            catalog.load(directory / "catalog.json")
        else:
            await coordinator.register("root", "fixture controller", parent_id=None)
            await coordinator.set_status("root", "waiting")
        pause = TestPauseController(coordinator, directory / "pause.json", pause_every_n_tests=1)
        catalog.set_change_callback(pause.observe)
        coordinator.set_status_change_callback(catalog.mark_status)
        # An interrupted external observation remains uncertain; never replay it on restore.
        for agent_id, status in list(coordinator.statuses.items()):
            if status == "running":
                await coordinator.set_status(agent_id, "crashed")
                await coordinator.pause_budget(reason="operator")
        session = open_agent_session("root", directory / "agents.sqlite")
        if not await session.get_items():
            await session.add_items(
                [{"role": "user", "content": "Owned lab only; retain case identity."}]
            )
        emit({"ready": True})
        try:
            for line in sys.stdin:
                request = json.loads(line)
                action = request["action"]
                if action == "quit":
                    return
                if action == "probe":
                    if coordinator.budget_paused:
                        emit({"blocked": True})
                        continue
                    agent_id = request["id"]
                    if agent_id in coordinator.statuses:
                        emit({"duplicate": True})
                        continue
                    await coordinator.register(agent_id, agent_id, parent_id="root")
                    catalog.register(
                        agent_id=agent_id, name=agent_id, task="owned Docker HTTP probe"
                    )
                    await coordinator.set_status(agent_id, "running")
                    call = {
                        "type": "function_call",
                        "name": "owned_http_probe",
                        "call_id": agent_id,
                        "arguments": json.dumps({"id": agent_id}),
                    }
                    before = await session.get_items()
                    await session.add_items([call])
                    path = "/slow/" if request.get("slow") else "/case/"
                    result = await asyncio.to_thread(
                        target.exec_run,
                        [
                            "curl",
                            "--fail",
                            "--max-time",
                            "8",
                            "--silent",
                            "http://127.0.0.1:18080" + path + agent_id,
                        ],
                    )
                    if result.exit_code != 0:
                        raise RuntimeError("owned Docker probe failed")
                    output = {
                        "type": "function_call_output",
                        "call_id": agent_id,
                        "output": result.output.decode(),
                    }
                    # Exercise the real SDK crash-history merger twice: no duplicated call/result.
                    await recover_session_items(session, before, [*before, call, output])
                    await recover_session_items(session, before, [*before, call, output])
                    await coordinator.set_status(agent_id, "completed")
                elif action == "resume":
                    await coordinator.resume_budget(reason=request["reason"])
                elif action == "pause":
                    await coordinator.pause_budget(reason=request["reason"])
                elif action != "status":
                    raise ValueError("unknown fixture command")
                emit(
                    {
                        "coordinator": await coordinator.snapshot(),
                        "catalog": catalog.snapshot(),
                        "history": await session.get_items(),
                        "completed_since_resume": pause.completed_since_resume,
                    }
                )
        finally:
            session.close()
            docker.close()


class Process:
    def __init__(self, directory: Path, container_id: str):
        self.child = subprocess.Popen(  # noqa: S603 -- Fixed local fixture module and owned container.
            [
                sys.executable,
                "-m",
                "tests.test_pause_hardening_docker",
                str(directory),
                container_id,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "HOME", "DOCKER_HOST"}
            }
            | {"LITELLM_MODE": "PRODUCTION"},
        )
        try:
            assert self.read() == {"ready": True}
        except BaseException:
            self.stop()
            raise

    def read(self) -> Any:
        assert self.child.stdout is not None
        ready, _, _ = select.select([self.child.stdout], [], [], 20)
        assert ready, "owned fixture process did not respond"
        line = self.child.stdout.readline()
        assert line, "owned fixture exited before replying"
        return json.loads(line)

    def write(self, **value: Any) -> None:
        assert self.child.stdin is not None
        self.child.stdin.write(json.dumps(value) + "\n")
        self.child.stdin.flush()

    def command(self, **value: Any) -> Any:
        self.write(**value)
        return self.read()

    def stop(self) -> None:
        if self.child.poll() is None:
            self.child.terminate()
        self.child.wait(timeout=10)
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            if stream is not None:
                stream.close()


@pytest.fixture
def target() -> Any:
    docker = connect_docker()
    assert docker.api.base_url.startswith("http+docker://"), "local Docker socket required"
    container = docker.containers.run(
        IMAGE,
        ["python3", "-u", "-c", SERVER],
        detach=True,
        network_mode="none",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        mem_limit="128m",
        pids_limit=32,
        user="65534:65534",
    )
    try:
        deadline = time.monotonic() + 15
        while b"READY" not in container.logs():
            assert time.monotonic() < deadline, "owned Docker target did not start"
            time.sleep(0.1)
        yield container
    finally:
        container.remove(force=True)
        docker.close()


@pytest.mark.parametrize("repeat", [1, 2])
@pytest.mark.parametrize(
    "scenario",
    [
        "clean_boundary",
        "killed_paused",
        "killed_running",
        "independent_reasons",
        "retained_context",
    ],
)
def test_durable_pause_against_owned_docker(  # noqa: PLR0915
    target: Any, tmp_path: Path, scenario: str, repeat: int
) -> None:
    process = Process(tmp_path, target.id)
    started = time.monotonic()
    try:
        if scenario == "killed_running":
            process.write(action="probe", id="case-1", slow=True)
            deadline = time.monotonic() + 10
            while b"HIT /slow/case-1" not in target.logs():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            process.stop()
            process = Process(tmp_path, target.id)
            restored = process.command(action="status")
            assert restored["coordinator"]["statuses"]["case-1"] == "crashed"
            assert sorted(restored["coordinator"]["pause_reasons"]) == ["operator", "test"]
            assert (
                len([item for item in restored["history"] if item.get("call_id") == "case-1"]) == 1
            )
        else:
            first = process.command(action="probe", id="case-1")
            assert first["coordinator"]["pause_reasons"] == ["test"]
            assert "running" not in first["coordinator"]["statuses"].values()
            if scenario in {"killed_paused", "retained_context"}:
                history = first["history"]
                catalog = first["catalog"]
                process.stop()
                time.sleep(0.2)
                process = Process(tmp_path, target.id)
                restored = process.command(action="status")
                assert restored["history"] == history
                assert restored["catalog"] == catalog
                assert restored["coordinator"]["pause_reasons"] == ["test"]
            elif scenario == "independent_reasons":
                process.command(action="pause", reason="budget")
                process.command(action="pause", reason="operator")
        hits_before = target.logs().count(b"HIT ")
        assert process.command(action="probe", id="forbidden") == {"blocked": True}
        time.sleep(0.05)
        assert target.logs().count(b"HIT ") == hits_before
        released = process.command(action="resume", reason="test")
        if scenario in {"independent_reasons", "killed_running"}:
            assert released["coordinator"]["pause_reasons"]
            assert process.command(action="probe", id="forbidden") == {"blocked": True}
            if scenario == "independent_reasons":
                process.command(action="resume", reason="budget")
                assert process.command(action="probe", id="forbidden") == {"blocked": True}
            process.command(action="resume", reason="operator")
        for number in (2, 3):
            after = process.command(action="probe", id=f"case-{number}")
            assert after["coordinator"]["pause_reasons"] == ["test"]
            assert after["completed_since_resume"] == 1
            process.command(action="resume", reason="test")
        assert process.command(action="probe", id="case-1") == {"duplicate": True}
        logs = target.logs().decode()
        assert logs.count("HIT ") == 3
        final = process.command(action="status")
        calls = [
            item["call_id"] for item in final["history"] if item.get("type") == "function_call"
        ]
        assert calls == ["case-1", "case-2", "case-3"]
        if evidence := os.getenv("STRIX_HARDENING_EVIDENCE_DIR"):
            directory = Path(evidence)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            record = {
                "scenario": scenario,
                "repeat": repeat,
                "result": "component_integration_passed",
                "provider_scan": "not_exercised",
                "provider_compaction": "not_exercised",
                "installed_app": "not_exercised",
                "elapsed_s": round(time.monotonic() - started, 3),
                "image_id": target.image.id,
                "requests": logs.splitlines(),
                "state": final,
            }
            (directory / f"{scenario}-{repeat}.json").write_text(
                json.dumps(record, indent=2) + "\n"
            )
    finally:
        process.stop()


if __name__ == "__main__":
    asyncio.run(worker(Path(sys.argv[1]), sys.argv[2]))
