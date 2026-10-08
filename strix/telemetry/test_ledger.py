"""Local, resume-safe accounting of observed LLM attempts, not case verdicts.

Only scalar accounting fields are stored. Provider bodies, headers, error text,
agent tasks and credentials are deliberately outside this database's contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from agents.usage import Usage

from strix.config import codex
from strix.report.usage import estimate_usage_cost


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from strix.core.test_catalog import TestCatalog, TestUnit
    from strix.llm.request_log import LlmRequestEvent


logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1

_SCHEMA = """
BEGIN;
CREATE TABLE ledger_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    scan_id TEXT NOT NULL,
    ingestion_status TEXT NOT NULL,
    write_errors INTEGER NOT NULL DEFAULT 0,
    history_started_at TEXT NOT NULL,
    history_scope TEXT NOT NULL,
    interrupted_writers INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE test_units (
    test_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    vuln_class TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    duration_s REAL
);
CREATE TABLE test_events (
    event_id TEXT PRIMARY KEY,
    test_id TEXT REFERENCES test_units(test_id),
    agent_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    route TEXT NOT NULL,
    provider TEXT,
    model TEXT NOT NULL,
    retry_attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    status_code INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_input_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL,
    cost_basis TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX events_by_agent ON test_events(agent_id);
CREATE INDEX events_by_test ON test_events(test_id);
CREATE TABLE scan_incidents (
    incident_id TEXT PRIMARY KEY,
    test_id TEXT REFERENCES test_units(test_id),
    agent_id TEXT NOT NULL,
    event_id TEXT REFERENCES test_events(event_id),
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE VIEW agent_usage AS
SELECT agent_id, COUNT(*) AS attempt_count,
       SUM(CASE WHEN retry_attempt > 0 THEN 1 ELSE 0 END) AS replay_attempt_count,
       SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) AS error_count,
       SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
       SUM(cached_input_tokens) AS cached_input_tokens,
       SUM(total_tokens) AS total_tokens, SUM(cost_usd) AS total_cost_usd,
       SUM(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_token_attempts,
       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_cost_attempts,
       SUM(duration_ms) AS provider_duration_ms
FROM test_events GROUP BY agent_id;
CREATE VIEW test_summary AS
SELECT t.*, COALESCE(u.attempt_count, 0) AS attempt_count,
       COALESCE(u.replay_attempt_count, 0) AS replay_attempt_count,
       COALESCE(u.error_count, 0) AS error_count,
       u.input_tokens, u.output_tokens, u.cached_input_tokens,
       u.total_tokens, u.total_cost_usd,
       COALESCE(u.unknown_token_attempts, 0) AS unknown_token_attempts,
       COALESCE(u.unknown_cost_attempts, 0) AS unknown_cost_attempts,
       COALESCE(u.provider_duration_ms, 0) AS provider_duration_ms
FROM test_units t LEFT JOIN agent_usage u ON u.agent_id = t.agent_id;
CREATE VIEW model_usage AS
SELECT test_id, agent_id, route, provider, model, cost_basis,
       COUNT(*) AS attempt_count, SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens, SUM(total_tokens) AS total_tokens,
       SUM(cost_usd) AS total_cost_usd,
       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_cost_attempts,
       SUM(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_token_attempts,
       SUM(duration_ms) AS provider_duration_ms
FROM test_events GROUP BY test_id, agent_id, route, provider, model, cost_basis;
PRAGMA user_version = 1;
"""


def _identity(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()


def _event_cost(event: LlmRequestEvent, subscription: bool) -> tuple[float | None, str]:
    if subscription or codex.subscription_model(event.model):
        return 0.0, "subscription"
    if event.cost_usd is not None and math.isfinite(event.cost_usd) and event.cost_usd >= 0:
        return event.cost_usd, "reported"
    # Missing usage must not be interpreted as a free request. Use the same
    # estimator as run.json, including cache tokens, only with complete inputs.
    if event.input_tokens is None or event.output_tokens is None:
        return None, "unknown"
    usage = Usage(input_tokens=event.input_tokens, output_tokens=event.output_tokens)
    usage.total_tokens = event.total_tokens or event.input_tokens + event.output_tokens
    usage.input_tokens_details.cached_tokens = event.cached_input_tokens or 0
    cost = estimate_usage_cost(usage, event.model)
    if cost is not None and math.isfinite(cost) and cost >= 0:
        return cost, "estimated"
    return None, "unknown"


class TestLedger:
    """One host-side SQLite ledger per scan, with serialized, durable writes.

    A write failure degrades accounting, not the agent. The persisted ingestion
    status remains collecting after a process crash and degraded after a known
    dropped write; closed means the writer shut down, not that a scan passed.
    """

    __test__: ClassVar[bool] = False

    def __init__(
        self,
        path: Path,
        *,
        scan_id: str,
        catalog: TestCatalog,
        owns_agent: Callable[[str], bool],
        subscription: bool = False,
        resuming: bool = False,
    ) -> None:
        self._catalog = catalog
        self._owns_agent = owns_agent
        self._subscription = subscription
        self._lock = threading.RLock()
        self._closed = False
        self._write_errors = 0
        self._statuses: dict[str, str] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=0.25, check_same_thread=False)
        try:
            path.chmod(0o600)
            self._initialize(scan_id, resuming=resuming)
        except BaseException:
            self._db.close()
            raise

    def _initialize(self, scan_id: str, *, resuming: bool) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise ValueError(f"Unsupported test telemetry schema: {version}")
        if version == 0:
            self._db.executescript(_SCHEMA)
            self._db.execute(
                "INSERT INTO ledger_metadata VALUES (1, ?, 'collecting', 0, ?, ?, 0)",
                (
                    scan_id,
                    datetime.now(UTC).isoformat(),
                    "from_resume" if resuming else "from_scan_start",
                ),
            )
            self._db.commit()
        metadata = self._db.execute(
            "SELECT scan_id, write_errors, ingestion_status "
            "FROM ledger_metadata WHERE singleton = 1"
        ).fetchone()
        if metadata is None or metadata[0] != scan_id:
            raise ValueError("Test telemetry belongs to a different scan")
        self._write_errors = metadata[1]
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute("PRAGMA foreign_keys = ON")
        interrupted = int(version != 0 and metadata[2] == "collecting")
        with self._db:
            self._db.execute(
                "UPDATE ledger_metadata SET ingestion_status = ?, "
                "interrupted_writers = interrupted_writers + ? WHERE singleton = 1",
                ("degraded" if self._write_errors else "collecting", interrupted),
            )

    def _write(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                with self._db:
                    for sql, values in statements:
                        self._db.execute(sql, values)
                    if self._write_errors:
                        self._db.execute(
                            "UPDATE ledger_metadata SET ingestion_status = 'degraded', "
                            "write_errors = ? WHERE singleton = 1",
                            (self._write_errors,),
                        )
            except sqlite3.Error:
                self._write_errors += 1
                logger.exception("Test telemetry write failed; accounting is incomplete")

    def sync_test(self, unit: TestUnit) -> None:
        duration = None
        if unit.started_at and unit.ended_at:
            duration = max(
                0.0,
                (
                    datetime.fromisoformat(unit.ended_at) - datetime.fromisoformat(unit.started_at)
                ).total_seconds(),
            )
        self._write(
            [
                (
                    "INSERT INTO test_units VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(test_id) DO UPDATE SET status=excluded.status, "
                    "vuln_class=excluded.vuln_class, started_at=excluded.started_at, "
                    "ended_at=excluded.ended_at, duration_s=excluded.duration_s",
                    (
                        unit.test_id,
                        unit.agent_id,
                        unit.vuln_class,
                        unit.status,
                        unit.started_at,
                        unit.ended_at,
                        duration,
                    ),
                )
            ]
        )

    def record_llm_event(self, event: LlmRequestEvent) -> None:
        # A callback may be pricing an attempt on another thread when teardown
        # begins. Closing must wait for that whole callback, not just its INSERT.
        with self._lock:
            if self._closed:
                return
            try:
                self._record_llm_event(event)
            except Exception:
                self._write_errors += 1
                logger.exception("Test telemetry event failed; accounting is incomplete")

    def _record_llm_event(self, event: LlmRequestEvent) -> None:
        # request_log sinks are process-wide. Never account another run's agents
        # or guess the owner of an event without an agent identity.
        if event.agent_id is None or not self._owns_agent(event.agent_id):
            return
        unit = self._catalog.get(event.agent_id)
        test_id = unit.test_id if unit else None
        cost, basis = _event_cost(event, self._subscription)
        started, finished = _iso(event.started_at), _iso(event.finished_at)
        event_id = _identity(
            event.agent_id, event.route, event.call_id, event.retry_attempt, started
        )
        total_tokens = event.total_tokens
        if (
            total_tokens is None
            and event.input_tokens is not None
            and event.output_tokens is not None
        ):
            total_tokens = event.input_tokens + event.output_tokens
        statements = [
            (
                "INSERT OR IGNORE INTO test_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    test_id,
                    event.agent_id,
                    event.call_id,
                    event.route,
                    event.provider,
                    event.model,
                    event.retry_attempt,
                    event.outcome,
                    event.status_code,
                    event.input_tokens,
                    event.output_tokens,
                    event.cached_input_tokens,
                    total_tokens,
                    cost,
                    basis,
                    started,
                    finished,
                    event.duration_ms,
                ),
            )
        ]
        if event.retry_attempt > 0:
            statements.append(
                self._incident(
                    _identity(event_id, "llm_retry"),
                    test_id,
                    event.agent_id,
                    event_id,
                    "warning",
                    "llm_retry",
                    "Provider attempt during a replayed agent turn",
                    finished,
                )
            )
        if event.outcome == "error":
            source = "rate_limited" if event.status_code == 429 else "llm_error"
            statements.append(
                self._incident(
                    _identity(event_id, source),
                    test_id,
                    event.agent_id,
                    event_id,
                    "warning" if source == "rate_limited" else "error",
                    source,
                    "Provider attempt failed; inspect the restricted local run log",
                    finished,
                )
            )
        self._write(statements)

    @staticmethod
    def _incident(
        incident_id: str,
        test_id: str | None,
        agent_id: str,
        event_id: str | None,
        severity: str,
        source: str,
        message: str,
        occurred_at: str,
    ) -> tuple[str, tuple[Any, ...]]:
        return (
            "INSERT OR IGNORE INTO scan_incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (incident_id, test_id, agent_id, event_id, severity, source, message, occurred_at),
        )

    def record_status(self, agent_id: str, status: str) -> None:
        with self._lock:
            if self._statuses.get(agent_id) == status:
                return
            self._statuses[agent_id] = status
        if status not in {"crashed", "failed"}:
            return
        unit = self._catalog.get(agent_id)
        occurred_at = (unit.ended_at if unit else None) or datetime.now(UTC).isoformat()
        self._write(
            [
                self._incident(
                    _identity(agent_id, status, occurred_at),
                    unit.test_id if unit else None,
                    agent_id,
                    None,
                    "crash" if status == "crashed" else "error",
                    "agent_crashed" if status == "crashed" else "agent_failed",
                    f"Agent entered {status} state",
                    occurred_at,
                )
            ]
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._write(
                [
                    (
                        "UPDATE ledger_metadata SET ingestion_status = ?, write_errors = ? "
                        "WHERE singleton = 1",
                        ("degraded" if self._write_errors else "closed", self._write_errors),
                    )
                ]
            )
            self._db.close()
            self._closed = True
