"""Runtime-authored, assessment-bound attempts and immutable evidence receipts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from strix.core.assessment_context import AssessmentContext


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
        if path.parent.is_symlink():
            raise EvidenceError("Evidence directory must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.stat().st_mode & 0o077:
            raise EvidenceError("Evidence directory must be owner-only")
        created = False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            mode = path.stat()
            if not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077:
                raise EvidenceError("Existing evidence must be owner-only") from None
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
            db.execute(
                "CREATE TABLE IF NOT EXISTS obligation_plan (version INTEGER, context_sha256 TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS obligations (case_ref TEXT, case_version INTEGER, "
                "identity_ref TEXT, operation_ref TEXT, "
                "PRIMARY KEY(case_ref,case_version,identity_ref,operation_ref))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS attempts_obligation "
                "ON attempts(case_ref,case_version,identity_ref,operation_ref)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS events_attempt ON events(attempt_id)")
            db.execute("CREATE TABLE IF NOT EXISTS case_plans (case_ref TEXT PRIMARY KEY)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS effects (attempt_ref TEXT PRIMARY KEY "
                "REFERENCES attempts(id), "
                "resource_ref TEXT, outcome TEXT, reconciliation_ref TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS business_runs "
                "(case_ref TEXT PRIMARY KEY, checkpoint TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS case_results (id TEXT PRIMARY KEY, "
                "case_ref TEXT, attempt_seq INTEGER, content BLOB, sha256 TEXT)"
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
                if self.path.stat().st_mode & 0o077 or self.path.parent.stat().st_mode & 0o077:
                    raise EvidenceError("Evidence permissions are no longer private")
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
            self._verify_reconciliations(db)
            unresolved = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE status IN ('started','uncertain') "
                "AND id NOT IN (SELECT attempt_ref FROM effects "
                "WHERE outcome IN ('applied','not_applied'))"
            ).fetchone()[0]
            attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            artifacts = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            denials = db.execute("SELECT COUNT(*) FROM execution_denials").fetchone()[0]
            obligations = self._obligations(db)
            case_evaluations = self._case_evaluations(db)
            pending_effects = db.execute(
                "SELECT COUNT(*) FROM effects WHERE outcome='pending'"
            ).fetchone()[0]
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
            "obligations": obligations,
            "case_evaluations": case_evaluations,
            "pending_effects": pending_effects,
        }

    def bind_obligations(self, context: AssessmentContext) -> None:
        """All approved case/identity/operation combinations are essential in plan version 1."""
        if context.digest != self._binding[2]:
            raise EvidenceError("Obligation context does not match the assessment")
        count = sum(len(c.identities) * len(c.operations) for c in context.cases.values())
        if not count or count > 4096:
            raise EvidenceError("Assessment needs between 1 and 4096 essential obligations")
        expected = sorted(
            {
                (name, case.version, identity, operation)
                for name, case in context.cases.items()
                for identity in case.identities
                for operation in case.operations
            }
        )
        with self._db() as db:
            plan = db.execute("SELECT * FROM obligation_plan").fetchall()
            if not plan:
                db.execute("INSERT INTO obligation_plan VALUES (1, ?)", (context.digest,))
                db.executemany("INSERT INTO obligations VALUES (?,?,?,?)", expected)
            actual = db.execute("SELECT * FROM obligations ORDER BY 1,2,3,4").fetchall()
            if (
                db.execute("SELECT * FROM obligation_plan").fetchall() != [(1, context.digest)]
                or actual != expected
            ):
                raise EvidenceError("Persisted obligation plan changed; execution blocked")
            expected_cases = sorted(
                key
                for key, case in context.cases.items()
                if case.authorization or case.business or case.transport
            )
            db.executemany(
                "INSERT OR IGNORE INTO case_plans VALUES (?)", [(key,) for key in expected_cases]
            )
            if db.execute("SELECT case_ref FROM case_plans ORDER BY 1").fetchall() != [
                (key,) for key in expected_cases
            ]:
                raise EvidenceError("Persisted case plan changed")
        self._publish()

    def record_case_result(self, case_ref: str, result: dict[str, Any]) -> dict[str, Any]:
        ref = uuid.uuid4().hex
        data = {**result, "case_result_ref": ref, "case_ref": case_ref, "evaluated_at": self._now()}
        encoded = json.dumps(data, sort_keys=True).encode()
        with self._db() as db:
            if not db.execute("SELECT 1 FROM case_plans WHERE case_ref=?", (case_ref,)).fetchone():
                raise EvidenceError("Unknown executable case")
            seq = db.execute(
                "SELECT MAX(rowid) FROM attempts WHERE case_ref=?", (case_ref,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO case_results VALUES (?,?,?,?,?)",
                (ref, case_ref, seq, encoded, hashlib.sha256(encoded).hexdigest()),
            )
        self._publish()
        return data

    def claim_business_run(self, case_ref: str, pre_ref: str) -> tuple[bool, dict[str, Any]]:
        checkpoint = {"pre_ref": pre_ref, "phase": "dispatch_reserved"}
        with self._db() as db:
            changed = db.execute(
                "INSERT OR IGNORE INTO business_runs VALUES (?,?)",
                (case_ref, json.dumps(checkpoint)),
            ).rowcount
            saved = db.execute(
                "SELECT checkpoint FROM business_runs WHERE case_ref=?", (case_ref,)
            ).fetchone()[0]
        return changed == 1, json.loads(saved)

    def business_checkpoint(self, case_ref: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT checkpoint FROM business_runs WHERE case_ref=?", (case_ref,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def register_effect(self, attempt: str, resource_ref: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO effects VALUES (?,?,'pending',NULL)", (attempt, resource_ref))
        self._publish()

    def reconcile_effects(self, case_ref: str, state_operation: str, evidence_ref: str) -> None:
        receipt = self.read_private(evidence_ref, case_ref=case_ref, require_complete=True)
        if (
            receipt["operation_ref"] != state_operation
            or receipt["content"].get("status_code") != 200
        ):
            raise EvidenceError("Reconciliation requires the approved state observation")
        try:
            body = json.loads(receipt["content"].get("body", ""))
            outcomes = body["effects"]
            if not isinstance(outcomes, dict):
                return
        except (KeyError, TypeError, ValueError):
            return
        with self._db() as db:
            effects = db.execute(
                "SELECT e.attempt_ref,e.resource_ref FROM effects e JOIN attempts a "
                "ON a.id=e.attempt_ref WHERE a.case_ref=? AND e.outcome='pending'",
                (case_ref,),
            ).fetchall()
            for ref, resource in effects:
                outcome = outcomes.get(ref)
                if (
                    body.get("resource_ref") == resource
                    and isinstance(outcome, dict)
                    and outcome.get("settled") is True
                    and isinstance(outcome.get("outcome"), str)
                    and outcome.get("outcome") in {"applied", "not_applied"}
                ):
                    db.execute(
                        "UPDATE effects SET outcome=?,reconciliation_ref=? "
                        "WHERE attempt_ref=? AND outcome='pending'",
                        (outcome["outcome"], evidence_ref, ref),
                    )
        self._publish()

    def effect_history(self, case_ref: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT e.attempt_ref,e.resource_ref,e.outcome,"
                "e.reconciliation_ref,a.operation_ref "
                "FROM effects e JOIN attempts a ON a.id=e.attempt_ref "
                "WHERE a.case_ref=? ORDER BY a.rowid",
                (case_ref,),
            ).fetchall()
        return [
            dict(
                zip(
                    (
                        "attempt_ref",
                        "resource_ref",
                        "outcome",
                        "reconciliation_ref",
                        "operation_ref",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]

    def _verify_reconciliations(self, db: sqlite3.Connection) -> None:
        for ref, resource, outcome, evidence_ref in db.execute(
            "SELECT * FROM effects WHERE outcome!='pending'"
        ).fetchall():
            receipt = self.read_private(evidence_ref, require_complete=True)
            try:
                data = json.loads(receipt["content"].get("body", ""))
                valid = (
                    isinstance(data, dict)
                    and data.get("resource_ref") == resource
                    and data["effects"][ref]
                    == {
                        "outcome": outcome,
                        "settled": True,
                    }
                )
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise EvidenceError("Effect reconciliation evidence is invalid")

    def read_case_result(self, ref: str, case_ref: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT content,sha256 FROM case_results WHERE id=? AND case_ref=?", (ref, case_ref)
            ).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise EvidenceError("Unknown, crossed or invalid case result")
        data: dict[str, Any] = json.loads(row[0])
        self._verify_case_evidence(data)
        return data

    def _verify_case_evidence(self, data: dict[str, Any]) -> None:
        for receipt in data.get("evidence", []):
            actual = self.read_private(
                receipt["evidence_ref"],
                case_ref=data["case_ref"],
                require_complete=data["verdict"] in {"compliant", "vulnerable"},
            )
            if actual["sha256"] != receipt["sha256"]:
                raise EvidenceError("Case evidence changed")

    def _case_evaluations(self, db: sqlite3.Connection) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for (case_ref,) in db.execute("SELECT case_ref FROM case_plans ORDER BY 1").fetchall():
            row = db.execute(
                "SELECT attempt_seq,content,sha256 FROM case_results WHERE case_ref=? "
                "ORDER BY rowid DESC LIMIT 1",
                (case_ref,),
            ).fetchone()
            item: dict[str, Any] = {"case_ref": case_ref, "verdict": "missing"}
            if row is not None:
                seq = db.execute(
                    "SELECT MAX(rowid) FROM attempts WHERE case_ref=?", (case_ref,)
                ).fetchone()[0]
                if hashlib.sha256(row[1]).hexdigest() != row[2]:
                    item["verdict"] = "invalid_evidence"
                elif seq != row[0]:
                    item["verdict"] = "needs_retest"
                else:
                    item = json.loads(row[1])
                    try:
                        self._verify_case_evidence(item)
                    except EvidenceError:
                        item = {"case_ref": case_ref, "verdict": "invalid_evidence"}
            items.append(item)
        return items

    def _obligations(self, db: sqlite3.Connection) -> dict[str, Any] | None:
        plan = db.execute("SELECT * FROM obligation_plan").fetchall()
        if not plan:
            return None
        if plan != [(1, self._binding[2])]:
            raise EvidenceError("Invalid obligation plan")
        obligations = db.execute("SELECT * FROM obligations ORDER BY 1,2,3,4").fetchall()
        rows: list[dict[str, Any]] = []
        for case, version, identity, operation in obligations:
            attempt = db.execute(
                "SELECT id,status FROM attempts WHERE case_ref=? AND case_version=? "
                "AND identity_ref=? AND operation_ref=? ORDER BY rowid DESC LIMIT 1",
                (case, version, identity, operation),
            ).fetchone()
            status = "missing" if attempt is None else attempt[1]
            evidence_ref = None
            if status in {"started", "uncertain"} and attempt is not None:
                effect = db.execute(
                    "SELECT reconciliation_ref FROM effects WHERE attempt_ref=? "
                    "AND outcome IN ('applied','not_applied')",
                    (attempt[0],),
                ).fetchone()
                if effect is not None:
                    status, evidence_ref = "reconciled", effect[0]
            if status == "observed" and attempt is not None:
                artifact = db.execute(
                    "SELECT a.id,a.content,a.sha256,a.truncated FROM artifacts a "
                    "JOIN events e ON a.event_id=e.id WHERE e.attempt_id=? AND e.kind='observed'",
                    (attempt[0],),
                ).fetchall()
                if (
                    len(artifact) != 1
                    or hashlib.sha256(artifact[0][1]).hexdigest() != artifact[0][2]
                ):
                    status = "invalid_evidence"
                elif artifact[0][3]:
                    status = "truncated"
                else:
                    evidence_ref = artifact[0][0]
            rows.append(
                {
                    "case_ref": case,
                    "case_version": version,
                    "identity_ref": identity,
                    "operation_ref": operation,
                    "essential": True,
                    "status": status,
                    "attempt_ref": attempt[0] if attempt else None,
                    "evidence_ref": evidence_ref,
                }
            )
        return {
            "version": 1,
            "source": "approved_context_and_runtime_receipts",
            "essential_total": len(rows),
            "essential_unfulfilled": sum(
                row["status"] not in {"observed", "reconciled"} for row in rows
            ),
            "items": rows,
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
            if (
                db.execute("SELECT COUNT(*) FROM obligation_plan").fetchone()[0]
                and not db.execute(
                    "SELECT 1 FROM obligations WHERE case_ref=? AND case_version=? "
                    "AND identity_ref=? AND operation_ref=?",
                    (case_ref, case_version, identity_ref, operation_ref),
                ).fetchone()
            ):
                raise EvidenceError("Attempt is outside the approved obligation plan")
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
        """Model/report boundary: target text is restricted even when secrets are unknown."""
        receipt = self.read_private(artifact, case_ref=case_ref, require_complete=require_complete)
        content = receipt["content"]
        public: dict[str, Any] = {}
        code = content.get("status_code")
        if type(code) is int and 100 <= code <= 599:
            public["status_code"] = code
        reason = content.get("reason")
        if isinstance(reason, str) and reason in {
            "authorization_unavailable",
            "identity_expired",
            "identity_unavailable",
            "identity_rejected",
            "redirect_blocked",
            "transport_outcome_unknown",
        }:
            public["reason"] = reason
        public["restricted_content"] = True
        receipt["content"] = public
        receipt["sanitization"] = {
            "version": 1,
            "policy": "metadata_only",
            "digest_scope": "restricted_content",
        }
        return receipt

    def read_private(
        self, artifact: str, *, case_ref: str | None = None, require_complete: bool = False
    ) -> dict[str, Any]:
        """Trusted host inspection only; never register this method as an agent/viewer tool."""
        with self._db() as db:
            row = db.execute(
                "SELECT a.content,a.sha256,a.truncated,t.id,t.agent_ref,t.case_ref,"
                "t.case_version,t.identity_ref,t.operation_ref,t.status,e.id,e.occurred_at,"
                "t.credential_revision "
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
            "source": (
                "runtime_transport_observation"
                if json.loads(row[0]).get("adapter") in {"openssl.tls", "ssh-audit"}
                else "runtime_http_observation"
            ),
            "event_ref": row[10],
            "observed_at": row[11],
            "credential_revision": row[12],
        }

    def close(self) -> None:
        if self._closed:
            return
        summary = self.summary()
        summary["status"] = "failed" if self._failed else "closed"
        self._closed = True
        if self._on_change is not None:
            self._on_change(summary)
