"""Private Unix control channel for one owned controller; never a network listener."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable

    from strix.core.agents import AgentCoordinator
    from strix.core.test_pause import TestPauseController
    from strix.telemetry.test_ledger import TestLedger

MAX_RESPONSE = 2 * 1024 * 1024


def validate_control_ref(value: str | None) -> None:
    if value is not None and (os.name != "posix" or str(uuid.UUID(value)) != value):
        raise ValueError("native control requires POSIX and a canonical controller UUID")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = path.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid():
        raise ValueError("native control requires an owned state directory")
    path.chmod(0o700)


def _read_private(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as source:
        meta = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.getuid()
            or meta.st_mode & 0o077
            or meta.st_nlink != 1
            or meta.st_size > 4096
        ):
            raise ValueError("private control state required")
        value = json.loads(source.read(4097))
    if not isinstance(value, dict):
        raise TypeError("invalid control state")
    return value


def _write_private(path: Path, value: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class NativeControl:
    """Attach only after the runner has acquired its controller lease.

    Each launch has an independent token and private socket. A browser never
    receives either. A revision-bound acknowledgment cannot release a later
    pause after a lost response, and cannot extend a budget or replace scope.
    """

    def __init__(
        self,
        directory: Path,
        *,
        scan_id: str,
        controller_ref: str,
        coordinator: AgentCoordinator,
        test_pause: TestPauseController,
        authorization_check: Callable[[], None] | None = None,
    ) -> None:
        validate_control_ref(controller_ref)
        _private_directory(directory)
        self.directory = directory
        self.scan_id = scan_id
        self.controller_ref = controller_ref
        self.coordinator = coordinator
        self.test_pause = test_pause
        self.authorization_check = authorization_check
        self.token = secrets.token_hex(32)
        self.server: asyncio.Server | None = None
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.ledger: TestLedger | None = None
        self._mutation_lock = asyncio.Lock()
        self._clients: set[asyncio.Task[Any]] = set()
        self.operator_path = directory / "operator_pause.json"
        try:
            saved = _read_private(self.operator_path)
        except FileNotFoundError:
            saved = {"version": 1, "paused": "operator" in coordinator.pause_reasons}
            _write_private(self.operator_path, saved)
        if saved.get("version") != 1 or type(saved.get("paused")) is not bool:
            raise ValueError("invalid durable operator pause")
        if saved["paused"]:
            coordinator.request_pause("operator")
        coordinator.set_pause_release_callback("operator", self._acknowledge_operator)

    def _acknowledge_operator(self) -> None:
        _write_private(self.operator_path, {"version": 1, "paused": False})

    def revision(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    self.controller_ref,
                    sorted(self.coordinator.pause_reasons),
                    self.coordinator.resume_epoch,
                    self.test_pause.completed_since_resume,
                    len(self.test_pause.seen),
                ]
            ).encode()
        ).hexdigest()

    async def snapshot(self) -> dict[str, Any]:
        # Never expose coordinator names, messages, errors, metadata or paths.
        statuses = list(self.coordinator.statuses.values())
        active = sum(status == "running" for status in statuses)
        paused = self.coordinator.budget_paused
        return {
            "version": 1,
            "scan_id": self.scan_id,
            "controller_ref": self.controller_ref,
            "revision": self.revision(),
            "state": ("pausing" if active else "paused") if paused else "running",
            "pause_reasons": sorted(self.coordinator.pause_reasons),
            "active_agents": active,
            "parked_agents": statuses.count("budget_paused"),
            "pause_every_n_tests": self.test_pause.interval,
            "completed_since_resume": self.test_pause.completed_since_resume,
            "telemetry": self.ledger.snapshot() if self.ledger is not None else None,
        }

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(request.get("token"), str)
            or not hmac.compare_digest(request["token"], self.token)
            or request.get("controller_ref") != self.controller_ref
        ):
            raise ValueError("unauthorized control request")
        action = request.get("action")
        if action == "status":
            return await self.snapshot()
        async with self._mutation_lock:
            if action == "pause":
                # Close admission even if durable storage subsequently refuses.
                self.coordinator.request_pause("operator")
                _write_private(self.operator_path, {"version": 1, "paused": True})
                await self.coordinator.pause_budget(reason="operator")
            elif action == "resume" and request.get("reason") in {"operator", "test"}:
                if self.authorization_check is not None:
                    await asyncio.to_thread(self.authorization_check)
                if request.get("revision") != self.revision():
                    raise ValueError("stale pause acknowledgment")
                reason = request["reason"]
                if reason not in self.coordinator.pause_reasons:
                    raise ValueError("pause reason is no longer active")
                await self.coordinator.resume_budget(reason=reason)
            else:
                raise ValueError("unsupported control operation")
            return await self.snapshot()

    async def start(self, ledger: TestLedger) -> None:
        self.ledger = ledger
        # A platform TMPDIR (especially macOS) can exceed Unix socket path limits.
        self.temporary = tempfile.TemporaryDirectory(
            prefix="strix-control-",
            dir="/tmp",  # nosec B108: mkdtemp allocates an exclusive 0700 directory
        )
        socket = Path(self.temporary.name) / "control.sock"
        try:
            self.server = await asyncio.start_unix_server(self._accept, path=socket, limit=4096)
            socket.chmod(0o600)
            _write_private(
                self.directory / "control.json",
                {
                    "version": 1,
                    "controller_ref": self.controller_ref,
                    "scan_id": self.scan_id,
                    "pid": os.getpid(),
                    "socket": str(socket),
                    "token": self.token,
                },
            )
        except BaseException:
            await self.close()
            raise

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            if len(self._clients) > 8:
                return
            try:
                async with asyncio.timeout(3):
                    raw = await reader.readline()
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise TypeError("invalid request")  # noqa: TRY301 -- Bound the IPC error response.
                    response = {"ok": True, "snapshot": await self.dispatch(request)}
                    payload = json.dumps(response, allow_nan=False).encode() + b"\n"
                    if len(payload) > MAX_RESPONSE:
                        raise ValueError("snapshot too large")  # noqa: TRY301 -- Bound IPC output.
            except (ValueError, TypeError, OSError, TimeoutError, RuntimeError):
                payload = b'{"ok":false,"error":"control_unavailable"}\n'
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=1)
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            writer.transport.abort()  # A non-reading local client cannot hold scan teardown.
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            if task is not None:
                self._clients.discard(task)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        self._clients.clear()
        path = self.directory / "control.json"
        with contextlib.suppress(OSError, ValueError, TypeError):
            if _read_private(path).get("token") == self.token:
                path.unlink()
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
