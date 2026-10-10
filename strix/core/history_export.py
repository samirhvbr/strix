"""Read a stopped controller's accounting without modifying its SQLite/WAL evidence."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from strix.core.assessment_review import load_review_scope
from strix.telemetry.read_snapshot import ledger_snapshot


def _read(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or metadata.st_size > limit
        ):
            raise ValueError("Invalid private history record")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("History record limit exceeded")
        return data


def _runtime(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("start_time", "end_time"):
        value = record.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError("Invalid history timestamp")
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("History timestamp requires an offset")
            value = parsed.isoformat()
        result[key] = value
    version = record.get("strix_version")
    result["strix_version"] = (
        version
        if isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        else None
    )
    result["scan_mode"] = (
        record.get("scan_mode")
        if record.get("scan_mode") in ("quick", "standard", "deep")
        else "unknown"
    )
    result["status"] = (
        record.get("status")
        if record.get("status")
        in ("running", "paused", "completed", "stopped", "failed", "crashed", "interrupted")
        else "unknown"
    )
    return result


def export_history(directory: Path) -> dict[str, Any]:
    if os.name != "posix":
        raise ValueError("Private history export requires POSIX")
    import fcntl  # noqa: PLC0415 -- POSIX-only ownership.

    directory = directory.absolute()
    policy, context = load_review_scope(directory)
    state = directory / ".state"
    # Existing ownership only: never create a lock, migrate a database or recover a writer.
    lock = os.open(state / "controller.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(lock)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
        ):
            raise ValueError("Invalid controller ownership record")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads(_read(directory / "run.json", 4 * 1024 * 1024))
        if record.get("run_id") != directory.name:
            raise ValueError("History belongs to a different run")
        catalog = json.loads(_read(state / "test_catalog.json", 4 * 1024 * 1024))
        parents = {agent: unit.get("parent_test_id") for agent, unit in catalog["tests"].items()}
        # A killed WAL writer may leave uncheckpointed committed frames. Copy both files
        # while holding its actual lease; SQLite can create SHM only in this private copy.
        with tempfile.TemporaryDirectory(prefix="strix-history-") as temporary:
            copied = Path(temporary) / "ledger.db"
            total = 0
            for suffix in ("", "-wal"):
                source = state / ("test_telemetry.db" + suffix)
                if suffix and not source.exists():
                    continue
                data = _read(source, 64 * 1024 * 1024 - total)
                total += len(data)
                target = copied.with_name(copied.name + suffix)
                target.write_bytes(data)
                target.chmod(0o600)
            with closing(sqlite3.connect(copied, timeout=1)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 3
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Unsupported history schema")
                db.execute("BEGIN")
                telemetry = ledger_snapshot(db, parents=parents)
                if telemetry["metadata"]["scan_id"] != directory.name:
                    raise ValueError("Accounting belongs to a different run")
                db.rollback()
        return {
            "version": 1,
            "source": "native_metadata_v1",
            "assessment_id": policy.assessment_id,
            "context_sha256": context.digest,
            "scan_id": directory.name,
            "runtime": _runtime(record),
            "telemetry": telemetry,
        }
    finally:
        os.close(lock)
