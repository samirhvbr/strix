"""Persistent synthetic credit, controlled concurrency and interrupted effect reconciliation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.assessment_context import FileCredentials, bind_context
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from tests.test_assessment_context import config, write_credentials


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def credit_server(tmp_path: Path) -> Any:
    database = tmp_path / "target.db"
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            "CREATE TABLE credit (balance INTEGER, applications INTEGER);"
            "INSERT INTO credit VALUES (100,0);"
            "CREATE TABLE effects (id TEXT PRIMARY KEY, outcome TEXT);"
        )
    barrier = threading.Barrier(2)
    committed, release = threading.Event(), threading.Event()
    state: dict[str, Any] = {"mode": "safe", "posts": 0, "committed": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def reply(self, value: dict[str, Any], code: int = 200) -> None:
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(body)

        def do_GET(self) -> None:
            with closing(sqlite3.connect(database)) as db:
                balance, applications = db.execute("SELECT * FROM credit").fetchone()
                effects = {
                    key: {"outcome": value, "settled": True}
                    for key, value in db.execute("SELECT * FROM effects").fetchall()
                }
            self.reply(
                {
                    "resource_ref": "credit-42",
                    "balance": balance,
                    "applications": applications,
                    "effects": effects,
                }
            )

        def do_POST(self) -> None:
            state["posts"] += 1
            action = self.headers.get("Idempotency-Key", "")
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if len(action) != 32 or payload != {"resource_ref": "credit-42"}:
                self.reply({}, 400)
                return
            if self.path == "/cleanup" and state["mode"] == "cleanup-failed":
                self.reply({}, 503)
                return
            with closing(sqlite3.connect(database, timeout=5)) as db, db:
                if self.path == "/redeem" and state["mode"] == "vulnerable":
                    eligible = db.execute("SELECT applications FROM credit").fetchone()[0] == 0
                    barrier.wait(timeout=5)
                    db.execute("BEGIN IMMEDIATE")
                else:
                    db.execute("BEGIN IMMEDIATE")
                    eligible = db.execute("SELECT applications FROM credit").fetchone()[0] == 0
                existing = db.execute(
                    "SELECT outcome FROM effects WHERE id=?", (action,)
                ).fetchone()
                if existing is not None:
                    outcome = existing[0]
                elif self.path == "/cleanup":
                    db.execute("UPDATE credit SET balance=100,applications=0")
                    outcome = "applied"
                elif eligible:
                    db.execute("UPDATE credit SET balance=balance+10,applications=applications+1")
                    outcome = "applied"
                else:
                    outcome = "not_applied"
                db.execute("INSERT OR IGNORE INTO effects VALUES (?,?)", (action, outcome))
            if self.path == "/redeem":
                state["committed"] += 1
                if state["committed"] == 2:
                    committed.set()
                if state["mode"] == "interrupted":
                    release.wait(timeout=5)
                if state["mode"] == "lost-response":
                    self.close_connection = True
                    return
            self.reply({"accepted": True})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, state, database, committed, release
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def executor_at(path: Path, port: int) -> IdentityExecutor:
    policy, raw = config(port)
    raw["version"] = 2
    raw["operations"] = {"state": {"method": "GET", "url": f"http://127.0.0.1:{port}/state"}}
    raw["operations"].update(
        {
            op: {
                "method": "POST",
                "url": f"http://127.0.0.1:{port}/{op}",
                "resource_ref": "credit-42",
            }
            for op in ["redeem", "cleanup"]
        }
    )
    raw["cases"] = {
        "single-credit": {
            "version": 1,
            "identities": ["a", "b"],
            "operations": ["state", "redeem", "cleanup"],
            "business": {
                "adapter": "http.single-credit",
                "version": 1,
                "allow_effects": True,
                "state_operation": "state",
                "redeem_operation": "redeem",
                "cleanup_operation": "cleanup",
                "resource_ref": "credit-42",
                "credit": 10,
            },
        }
    }
    context = bind_context(path, "scan", policy, raw, resuming=False)
    assert context is not None
    credentials = path / "credentials.json"
    if not credentials.exists():
        write_credentials(credentials)
    ledger = EvidenceLedger(
        path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda actor: actor == "agent",
    )
    return IdentityExecutor(context, FileCredentials(credentials, "assessment"), ledger)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected",
    [("safe", "compliant"), ("vulnerable", "vulnerable"), ("lost-response", "compliant")],
)
async def test_persistent_invariant_and_reconciled_cleanup(
    tmp_path: Path, credit_server: Any, mode: str, expected: str
) -> None:
    port, state, database, _, _ = credit_server
    state["mode"] = mode
    executor = executor_at(tmp_path / "runner", port)
    result = await executor.run_case(agent_ref="agent", case_ref="single-credit")
    assert result["verdict"] == expected
    assert result["cleanup_complete"] is True
    assert state["posts"] == 4
    assert all(effect["outcome"] != "pending" for effect in result["effects"])
    assert executor.ledger.summary()["pending_effects"] == 0
    assert executor.ledger.summary()["unresolved_attempts"] == 0
    assert executor.ledger.summary()["obligations"]["essential_unfulfilled"] == 0
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM credit").fetchone() == (100, 0)
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 4
    await executor.close()


@pytest.mark.asyncio
async def test_cleanup_failure_remains_visible(tmp_path: Path, credit_server: Any) -> None:
    port, state, _, _, _ = credit_server
    state["mode"] = "cleanup-failed"
    executor = executor_at(tmp_path / "runner", port)
    result = await executor.run_case(agent_ref="agent", case_ref="single-credit")
    assert result["verdict"] == "compliant"
    assert not result["cleanup_complete"]
    assert executor.ledger.summary()["pending_effects"] == 2
    await executor.close()


@pytest.mark.asyncio
async def test_cancelled_effects_are_reconciled_without_silent_replay(
    tmp_path: Path, credit_server: Any
) -> None:
    port, state, database, committed, release = credit_server
    state["mode"] = "interrupted"
    path = tmp_path / "runner"
    executor = executor_at(path, port)
    task = asyncio.create_task(executor.run_case(agent_ref="agent", case_ref="single-credit"))
    assert await asyncio.to_thread(committed.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await executor.close()
    release.set()
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM credit").fetchone() == (110, 1)
    resumed = executor_at(path, port)
    assert resumed.ledger.summary()["unresolved_attempts"] == 2
    recovered = await resumed.run_case(agent_ref="agent", case_ref="single-credit")
    assert recovered["verdict"] == "inconclusive"
    assert recovered["reason"] == "reconciled_without_replaying_effects"
    assert resumed.ledger.summary()["unresolved_attempts"] == 0
    assert state["posts"] == 2
    await resumed.close()


@pytest.mark.asyncio
async def test_model_cannot_dispatch_a_write_outside_the_case_controller(
    tmp_path: Path, credit_server: Any
) -> None:
    port, state, _, _, _ = credit_server
    executor = executor_at(tmp_path / "runner", port)
    with pytest.raises(EvidenceError, match="outside"):
        await executor.request(
            agent_ref="agent", case_ref="single-credit", identity_ref="a", operation_ref="redeem"
        )
    assert state["posts"] == 0
    await executor.close()


@pytest.mark.asyncio
async def test_killed_controller_recovers_effects_without_dispatching_again(
    tmp_path: Path, credit_server: Any
) -> None:
    port, state, _, committed, release = credit_server
    state["mode"] = "interrupted"
    path = tmp_path / "crashed-runner"
    script = """
import asyncio, sys
from pathlib import Path
from tests.test_business_case import executor_at
async def main():
    executor = executor_at(Path(sys.argv[1]), int(sys.argv[2]))
    await executor.run_case(agent_ref='agent', case_ref='single-credit')
asyncio.run(main())
"""
    child = subprocess.Popen(  # noqa: S603 -- Owned local crash fixture, fixed Python program.
        [sys.executable, "-c", script, str(path), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert await asyncio.to_thread(committed.wait, 5)
        child.kill()
        await asyncio.to_thread(child.wait, 5)
        release.set()
        resumed = executor_at(path, port)
        assert resumed.ledger.summary()["pending_effects"] == 2
        recovered = await resumed.run_case(agent_ref="agent", case_ref="single-credit")
        assert recovered["verdict"] == "inconclusive"
        assert resumed.ledger.summary()["pending_effects"] == 0
        assert resumed.ledger.summary()["unresolved_attempts"] == 0
        assert state["posts"] == 2
        await resumed.close()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
