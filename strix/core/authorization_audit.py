"""Private, assessment-bound receipts for observed host-side authorization denials."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


logger = logging.getLogger(__name__)
_CHANNELS = frozenset({"startup", "mcp_dispatch", "mcp_transport"})
_REASONS = frozenset(
    {
        "scope_rejected",
        "mcp_configuration_rejected",
        "network_binding_rejected",
        "connection_not_allowed",
        "tool_not_allowed",
        "arguments_not_object",
        "argument_not_allowed",
        "required_argument_missing",
        "argument_value_not_allowed",
        "endpoint_not_allowed",
        "redirect_not_allowed",
    }
)
_SCHEMA = """
CREATE TABLE audit_binding (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    scan_id TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    authorization_ref TEXT NOT NULL,
    operator_ref TEXT NOT NULL,
    history_before_audit INTEGER NOT NULL CHECK (history_before_audit IN (0, 1))
);
CREATE TABLE audit_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'failed')),
    write_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authorization_denials (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES audit_sessions(session_id),
    occurred_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    reason TEXT NOT NULL,
    connection_ref TEXT,
    tool_ref TEXT
);
PRAGMA user_version = 1;
"""


class AuthorizationAuditUnavailableError(RuntimeError):
    """The current launch cannot acknowledge durable authorization records."""


class AuthorizationAudit:
    """Commit each receipt before acknowledging it; never retain rejected inputs.

    Connections are short-lived. A failed write latches this launch unavailable,
    and its session remains failed/open on disk if finalization is impossible.
    A later launch explicitly exposes prior failed or unclosed sessions.
    """

    def __init__(
        self,
        path: Path,
        *,
        scan_id: str,
        assessment_id: str,
        policy_sha256: str,
        authorization_ref: str,
        operator_ref: str,
        approved_tools: Mapping[str, frozenset[str]],
        on_change: Callable[[dict[str, Any]], None] | None = None,
        resuming: bool = False,
    ) -> None:
        self._path = path
        self._binding = (scan_id, assessment_id, policy_sha256, authorization_ref, operator_ref)
        self._approved_tools = dict(approved_tools)
        self._on_change = on_change
        self._lock = threading.RLock()
        self._session_id = uuid.uuid4().hex
        self._failed = False
        self._closed = False
        self._write_failures = 0
        self._committed = 0
        self._prior_unclosed = 0
        self._prior_failed = 0
        self._history_before_audit = False
        created = self._prepare_storage()
        try:
            with closing(self._connect(validate=False)) as db:
                if created:
                    db.executescript(_SCHEMA)
                    db.execute(
                        "INSERT INTO audit_binding VALUES (1, ?, ?, ?, ?, ?, ?)",
                        (*self._binding, int(resuming)),
                    )
                self._validate(db)
                self._history_before_audit = bool(
                    db.execute(
                        "SELECT history_before_audit FROM audit_binding WHERE singleton = 1"
                    ).fetchone()[0]
                )
                self._prior_unclosed = db.execute(
                    "SELECT COUNT(*) FROM audit_sessions WHERE closed_at IS NULL"
                ).fetchone()[0]
                self._prior_failed = db.execute(
                    "SELECT COUNT(*) FROM audit_sessions "
                    "WHERE status = 'failed' OR write_failures > 0"
                ).fetchone()[0]
                self._committed = db.execute(
                    "SELECT COUNT(*) FROM authorization_denials"
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO audit_sessions VALUES (?, ?, NULL, 'open', 0)",
                    (self._session_id, self._now()),
                )
                db.commit()
            path.chmod(0o600)
        except (OSError, sqlite3.Error, ValueError):
            raise AuthorizationAuditUnavailableError(
                "Cannot initialize the assessment authorization audit"
            ) from None
        self._publish()

    def _prepare_storage(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.is_symlink():
            raise AuthorizationAuditUnavailableError("Authorization audit must not be a symlink")
        marker = self._path.with_suffix(".required")
        if marker.is_symlink():
            raise AuthorizationAuditUnavailableError(
                "Authorization audit marker must not be a symlink"
            )
        if marker.exists():
            if marker.read_bytes() != b"1\n" or not self._path.is_file():
                raise AuthorizationAuditUnavailableError(
                    "Required authorization audit is missing or invalid"
                )
        else:
            try:
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raise AuthorizationAuditUnavailableError(
                    "Authorization audit initialization is already active"
                ) from None
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"1\n")
                stream.flush()
                os.fsync(stream.fileno())
        created = False
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
        return created

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _validate(self, db: sqlite3.Connection) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported authorization audit schema")
        binding = db.execute(
            "SELECT scan_id, assessment_id, policy_sha256, authorization_ref, operator_ref "
            "FROM audit_binding WHERE singleton = 1"
        ).fetchone()
        if binding != self._binding:
            raise ValueError("Authorization audit binding mismatch")

    def _connect(self, *, validate: bool = True) -> sqlite3.Connection:
        if self._path.is_symlink():
            raise ValueError("Authorization audit must not be a symlink")
        # mode=rw prevents an absent ledger from silently becoming an empty one.
        db = sqlite3.connect(self._path.absolute().as_uri() + "?mode=rw", uri=True, timeout=0.25)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            if validate:
                self._validate(db)
        except BaseException:
            db.close()
            raise
        return db

    @property
    def available(self) -> bool:
        return not self._failed and not self._closed

    def require_available(self) -> None:
        if not self.available:
            raise AuthorizationAuditUnavailableError(
                "Authorization audit unavailable; MCP execution is blocked"
            )

    def summary(self) -> dict[str, Any]:
        return {
            "version": 1,
            "scope": "observed_host_authorization_denials",
            "session_id": self._session_id,
            "status": "failed" if self._failed else "closed" if self._closed else "recording",
            "committed_denials": self._committed,
            "write_failures": self._write_failures,
            "prior_unclosed_sessions": self._prior_unclosed,
            "prior_failed_sessions": self._prior_failed,
            "history_before_audit": self._history_before_audit,
        }

    def _publish(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self.summary())
            except Exception:  # noqa: BLE001 -- A reporting sink must not alter authorization.
                logger.error("Authorization audit summary could not be published")  # noqa: TRY400 -- Never log sink exceptions.

    def record_denial(
        self,
        channel: str,
        reason: str,
        *,
        connection: str | None = None,
        tool: str | None = None,
        event_id: str | None = None,
    ) -> str | None:
        if channel not in _CHANNELS or reason not in _REASONS:
            raise ValueError("Unknown authorization audit reason or channel")
        receipt = uuid.UUID(event_id).hex if event_id is not None else uuid.uuid4().hex
        connection_ref = connection if connection in self._approved_tools else None
        tool_ref = tool if connection_ref and tool in self._approved_tools[connection_ref] else None
        payload = (channel, reason, connection_ref, tool_ref)
        with self._lock:
            if not self.available:
                return None
            try:
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    prior = db.execute(
                        "SELECT channel, reason, connection_ref, tool_ref "
                        "FROM authorization_denials WHERE event_id = ?",
                        (receipt,),
                    ).fetchone()
                    if prior is None:
                        db.execute(
                            "INSERT INTO authorization_denials "
                            "(event_id, session_id, occurred_at, channel, reason, "
                            "connection_ref, tool_ref) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (receipt, self._session_id, self._now(), *payload),
                        )
                    elif prior != payload:
                        raise ValueError("Conflicting authorization receipt")  # noqa: TRY301 -- Latch the audit failure.
                    db.commit()
                    self._committed = db.execute(
                        "SELECT COUNT(*) FROM authorization_denials"
                    ).fetchone()[0]
            except (OSError, sqlite3.Error, ValueError):
                self._failed = True
                self._write_failures += 1
                logger.error(  # noqa: TRY400 -- Do not log database exception content.
                    "Authorization audit write failed; subsequent MCP execution is blocked"
                )
                self._publish()
                return None
            self._publish()
            return receipt

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                with closing(self._connect()) as db:
                    db.execute(
                        "UPDATE audit_sessions SET closed_at = ?, status = ?, write_failures = ? "
                        "WHERE session_id = ?",
                        (
                            self._now(),
                            "failed" if self._failed else "closed",
                            self._write_failures,
                            self._session_id,
                        ),
                    )
                    db.commit()
            except (OSError, sqlite3.Error, ValueError):
                self._failed = True
                self._write_failures += 1
                logger.error("Authorization audit session could not be finalized")  # noqa: TRY400
            self._closed = True
            self._publish()
