"""Exclusive POSIX controller ownership, released by the kernel after process death."""

from __future__ import annotations

import asyncio
import inspect
import os
import stat
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator
    from pathlib import Path


P = ParamSpec("P")
T = TypeVar("T")


class RunAlreadyOwnedError(RuntimeError):
    """Another live controller owns this run; no executor may start."""


@dataclass
class _Ownership:
    directory: Path
    pid: int
    task: object
    borrower: object = None


_ownership: ContextVar[_Ownership | None] = ContextVar("controller_ownership", default=None)


def _task() -> object:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def controller_owned(directory: Path) -> bool:
    owner = _ownership.get()
    return bool(owner and owner.pid == os.getpid() and owner.directory == directory.absolute())


@contextmanager
def controller_lease(directory: Path, *, inherit: bool = False) -> Generator[None, None, None]:
    # The controlled profile requires POSIX already. Preserve legacy Windows execution.
    if os.name != "posix":
        yield
        return
    import fcntl  # noqa: PLC0415 -- Not available on Windows.

    directory = directory.absolute()
    owner, task = _ownership.get(), _task()
    if inherit and controller_owned(directory) and owner is not None:
        if owner.task is not task and (
            owner.task is not None or (owner.borrower is not None and owner.borrower is not task)
        ):
            raise RunAlreadyOwnedError("Another controller task already owns this run")
        previous = owner.borrower
        owner.borrower = task
        try:
            yield
        finally:
            owner.borrower = previous
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o022:
        raise RunAlreadyOwnedError("Run state directory must be private to its owner")
    path = directory / "controller.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        raise RunAlreadyOwnedError("Run controller lock is unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o077
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise RunAlreadyOwnedError("Run controller lock must be an owner-only regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunAlreadyOwnedError("Another controller already owns this run") from None
        token = _ownership.set(_Ownership(directory, os.getpid(), task))
        try:
            yield
        finally:
            _ownership.reset(token)
    finally:
        # Never unlink: a waiter and a new arrival must continue to lock the same inode.
        os.close(descriptor)


def exclusive_scan(
    state_directory: Callable[[str], Path],
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Acquire ownership before policy binding, sandbox startup or evidence mutation."""

    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        signature = inspect.signature(function)

        @wraps(function)
        async def owned(*args: P.args, **kwargs: P.kwargs) -> T:
            bound = signature.bind(*args, **kwargs)
            scan_id = bound.arguments.get("scan_id") or f"scan-{uuid.uuid4().hex[:8]}"
            bound.arguments["scan_id"] = scan_id
            with controller_lease(state_directory(scan_id), inherit=True):
                return await function(*bound.args, **bound.kwargs)

        return owned

    return decorate
