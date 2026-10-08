"""Runtime-authored, assessment-bound attempts and immutable evidence receipts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


class EvidenceError(RuntimeError):
    """An evidence claim or its durable storage could not be verified."""


_SCHEMA = """
CREATE TABLE binding (scan_id TEXT, assessment_id TEXT, context_sha256 TEXT);
CREATE TABLE attempts (
    id TEXT PRIMARY KEY, agent_ref TEXT NOT NULL, case_ref TEXT NOT NULL,
    case_version INTEGER NOT NULL, identity_ref TEXT NOT NULL, operation_ref TEXT NOT NULL,
    credential_revision INTEGER, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('started','observed','blocked','uncertain'))
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
    occurred_at TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    content BLOB NOT NULL, sha256 TEXT NOT NULL, truncated INTEGER NOT NULL
);
CREATE TABLE identity_versions (
    identity_ref TEXT PRIMARY KEY, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


class EvidenceLedger:
    def __init__(
        self,
        path: Path,
        *,
        scan_id: str,
        assessment_id: str,
        context_sha256: str,
        owns_agent: Callable[[str], bool],
        on_change: Callable[[dict[str, Any]], None] | None = None,
        resuming: bool = False,
    ) -> None:
        self.path = path
        self._binding = (scan_id, assessment_id, context_sha256)
        self._owns_agent = owns_agent
        self._on_change = on_change
        self._lock = threading.RLock()
        self._failed = False
        self._closed = False
        if path.is_symlink() or (resuming and not path.is_file()):
            raise EvidenceError("Required evidence ledger is missing or unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
        with self._db(validate=False) as db:
            if created:
                db.executescript(_SCHEMA)
                db.execute("INSERT INTO binding VALUES (?, ?, ?)", self._binding)
            self._validate(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS execution_denials ("
                "id TEXT PRIMARY KEY, agent_ref TEXT NOT NULL, reason TEXT NOT NULL, "
                "occurred_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS network_snapshots ("
                "id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, counters TEXT)"
            )
        path.chmod(0o600)
        self._publish()

    def _validate(self, db: sqlite3.Connection) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute(
            "SELECT * FROM binding"
        ).fetchall() != [self._binding]:
            raise EvidenceError("Invalid or cross-assessment evidence ledger")

    @contextmanager
    def _db(self, *, validate: bool = True) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            if self._failed or self._closed or self.path.is_symlink():
                raise EvidenceError("Evidence ledger unavailable")
            try:
                with closing(
                    sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=1)
                ) as db:
                    db.execute("PRAGMA foreign_keys=ON")
                    db.execute("PRAGMA synchronous=FULL")
                    if validate:
                        self._validate(db)
                    with db:
                        yield db
            except (OSError, sqlite3.Error):
                self._failed = True
                self._publish_failed()
                raise EvidenceError("Evidence storage failed; execution blocked") from None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _publish_failed(self) -> None:
        if self._on_change is not None:
            self._on_change({"version": 1, "status": "failed", "unresolved_attempts": None})

    def summary(self) -> dict[str, Any]:
        if self._failed:
            return {"version": 1, "status": "failed", "unresolved_attempts": None}
        with self._db() as db:
            unresolved = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE status IN ('started','uncertain')"
            ).fetchone()[0]
            attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            artifacts = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            denials = db.execute("SELECT COUNT(*) FROM execution_denials").fetchone()[0]
            network_gaps = db.execute(
                "SELECT COUNT(*) FROM network_snapshots WHERE counters IS NULL"
            ).fetchone()[0]
        return {
            "version": 1,
            "status": "recording",
            "attempts": attempts,
            "artifacts": artifacts,
            "unresolved_attempts": unresolved,
            "authorization_denials": denials,
            "network_observation_gaps": network_gaps,
        }

    def _publish(self) -> None:
        if self._on_change is not None:
            self._on_change(self.summary())

    def begin(
        self,
        *,
        agent_ref: str,
        case_ref: str,
        case_version: int,
        identity_ref: str,
        operation_ref: str,
    ) -> str:
        if not self._owns_agent(agent_ref):
            raise EvidenceError("Attempt belongs to an unknown agent")
        attempt = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, 'started')",
                (
                    attempt,
                    agent_ref,
                    case_ref,
                    case_version,
                    identity_ref,
                    operation_ref,
                    self._now(),
                ),
            )
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, 'attempt_started')",
                (uuid.uuid4().hex, attempt, self._now()),
            )
        self._publish()
        return attempt

    def bind_credential(self, attempt: str, revision: int, fingerprint: str) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT identity_ref FROM attempts WHERE id=? AND status='started'", (attempt,)
            ).fetchone()
            if row is None:
                raise EvidenceError("Unknown or closed attempt")
            prior = db.execute(
                "SELECT revision, fingerprint FROM identity_versions WHERE identity_ref=?", row
            ).fetchone()
            if prior is not None and (
                revision < prior[0] or (revision == prior[0] and fingerprint != prior[1])
            ):
                raise EvidenceError("Credential revision rollback or unversioned replacement")
            db.execute(
                "INSERT OR REPLACE INTO identity_versions VALUES (?, ?, ?)",
                (row[0], revision, fingerprint),
            )
            db.execute("UPDATE attempts SET credential_revision=? WHERE id=?", (revision, attempt))

    def record_denial(self, agent_ref: str, reason: str) -> str:
        if not self._owns_agent(agent_ref) or reason not in {
            "scope_rejected",
            "authorization_rejected",
        }:
            raise EvidenceError("Invalid execution denial attribution")
        receipt = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO execution_denials VALUES (?, ?, ?, ?)",
                (receipt, agent_ref, reason, self._now()),
            )
        return receipt

    def record_network_snapshot(self, counters: dict[str, dict[str, int]] | None) -> str:
        receipt = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO network_snapshots VALUES (?, ?, ?)",
                (
                    receipt,
                    self._now(),
                    json.dumps(counters) if counters is not None else None,
                ),
            )
        self._publish()
        return receipt

    def finish(
        self, attempt: str, *, status: str, content: dict[str, Any], truncated: bool = False
    ) -> str:
        if status not in {"observed", "blocked", "uncertain"}:
            raise EvidenceError("Invalid attempt result")
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        if len(encoded) > 1048576:
            raise EvidenceError("Evidence exceeds the supported size")
        digest = hashlib.sha256(encoded).hexdigest()
        event, artifact = uuid.uuid4().hex, uuid.uuid4().hex
        with self._db() as db:
            changed = db.execute(
                "UPDATE attempts SET status=?, finished_at=? WHERE id=? AND status='started'",
                (status, self._now(), attempt),
            ).rowcount
            if changed != 1:
                raise EvidenceError("Unknown or already completed attempt")
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)", (event, attempt, self._now(), status)
            )
            db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
                (artifact, event, encoded, digest, int(truncated)),
            )
        self._publish()
        return artifact

    def read(
        self, artifact: str, *, case_ref: str | None = None, require_complete: bool = False
    ) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT a.content,a.sha256,a.truncated,t.id,t.agent_ref,t.case_ref,"
                "t.case_version,t.identity_ref,t.operation_ref,t.status,e.id,e.occurred_at "
                "FROM artifacts a JOIN events e ON a.event_id=e.id "
                "JOIN attempts t ON e.attempt_id=t.id WHERE a.id=?",
                (artifact,),
            ).fetchone()
        if row is None or (case_ref is not None and row[5] != case_ref):
            raise EvidenceError("Unknown or cross-case evidence reference")
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise EvidenceError("Evidence integrity verification failed")
        if require_complete and (row[2] or row[9] != "observed"):
            raise EvidenceError("Incomplete evidence cannot substantiate a completed observation")
        return {
            "evidence_ref": artifact,
            "sha256": row[1],
            "truncated": bool(row[2]),
            "attempt_ref": row[3],
            "agent_ref": row[4],
            "case_ref": row[5],
            "case_version": row[6],
            "identity_ref": row[7],
            "operation_ref": row[8],
            "status": row[9],
            "content": json.loads(row[0]),
            "source": "runtime_http_observation",
            "event_ref": row[10],
            "observed_at": row[11],
        }

    def close(self) -> None:
        if self._closed:
            return
        summary = self.summary()
        summary["status"] = "failed" if self._failed else "closed"
        self._closed = True
        if self._on_change is not None:
            self._on_change(summary)
