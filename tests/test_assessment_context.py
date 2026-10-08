# ruff: noqa: S105, S106, S107 -- Synthetic credentials for an isolated local HTTP fixture.
"""Local acceptance for isolated identities and runtime-authored durable evidence."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import subprocess
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext

from strix.core import runner
from strix.core.assessment import AssessmentPolicy, bind_assessment_policy
from strix.core.assessment_context import FileCredentials, bind_context, parse_context
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.interface.cli_args import parse_arguments
from strix.report.state import ReportState
from strix.tools.reporting import tool as reporting
from strix.tools.reporting.tool import _assessment_receipts


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def server() -> Any:
    calls: list[dict[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            token = self.headers.get("Authorization", "anonymous")
            cookie = self.headers.get("Cookie")
            calls.append({"path": self.path, "token": token, "cookie": cookie})
            status = 403 if token == "Bearer tenant-b-token" and self.path == "/private" else 200
            body = json.dumps({"cookie": cookie, "token": token, "private": status == 200}).encode()
            if self.path == "/redirect":
                status = 307
            if self.path == "/large":
                body = b"x" * 200000
            self.send_response(status)
            self.send_header("Set-Cookie", "session=" + token.replace("Bearer ", "") + "; Path=/")
            self.send_header("Content-Length", str(len(body)))
            if status == 307:
                self.send_header("Location", "/forbidden")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def config(port: int) -> tuple[AssessmentPolicy, dict[str, Any]]:
    base = f"http://127.0.0.1:{port}"
    policy = AssessmentPolicy.model_validate(
        {
            "version": 1,
            "assessment_id": "assessment",
            "authorization_ref": "approval",
            "operator_ref": "operator",
            "targets": [{"type": "ip_address", "value": "127.0.0.1"}],
            "network_policy": {
                "destinations": [{"address": "127.0.0.1", "protocol": "tcp", "ports": [port]}]
            },
            "mcp_connections": {},
        }
    )
    context = {
        "version": 1,
        "project_ref": "project",
        "environment_ref": "lab",
        "identities": {
            "a": {"tenant_ref": "tenant-a", "role_ref": "owner", "secret_ref": "secret-a"},
            "b": {"tenant_ref": "tenant-b", "role_ref": "reader", "secret_ref": "secret-b"},
        },
        "operations": {
            name: {"method": "GET", "url": base + "/" + name}
            for name in ["private", "cookie", "redirect", "large"]
        },
        "cases": {
            "cross-tenant": {
                "version": 1,
                "identities": ["a", "b"],
                "operations": ["private", "cookie", "redirect", "large"],
            }
        },
    }
    return policy, context


def write_credentials(
    path: Path, *, revision: int = 1, token: str = "tenant-a-token", expired: bool = False
) -> None:
    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 1)
    path.write_text(
        json.dumps(
            {
                "secret-" + actor: {
                    "assessment_id": "assessment",
                    "identity_ref": actor,
                    "revision": revision,
                    "expires_at": expiry.isoformat(),
                    "headers": {"Authorization": "Bearer " + value},
                }
                for actor, value in [("a", token), ("b", "tenant-b-token")]
            }
        )
    )
    path.chmod(0o600)


def make_executor(tmp_path: Path, port: int) -> IdentityExecutor:
    policy, raw = config(port)
    context = bind_context(tmp_path, "scan", policy, raw, resuming=False)
    assert context is not None
    credentials = tmp_path / "secrets.json"
    write_credentials(credentials)
    ledger = EvidenceLedger(
        tmp_path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda agent: agent == "agent",
    )
    return IdentityExecutor(context, FileCredentials(credentials, "assessment"), ledger)


async def request(
    executor: IdentityExecutor, actor: str = "a", operation: str = "private"
) -> dict[str, Any]:
    return await executor.request(
        agent_ref="agent", case_ref="cross-tenant", identity_ref=actor, operation_ref=operation
    )


@pytest.mark.asyncio
async def test_two_real_sessions_are_isolated_and_receipts_survive_resume(
    tmp_path: Path, server: Any
) -> None:
    port, calls = server
    executor = make_executor(tmp_path, port)
    first, second = await asyncio.gather(request(executor, "a"), request(executor, "b"))
    assert first["content"]["status_code"] == 200
    assert second["content"]["status_code"] == 403
    assert len(calls) == 2
    assert all(call["cookie"] is None for call in calls)
    await asyncio.gather(request(executor, "a", "cookie"), request(executor, "b", "cookie"))
    for call in calls[2:]:
        assert call["token"].removeprefix("Bearer ") in call["cookie"]
    assert "tenant-a-token" not in json.dumps(first)
    assert "tenant-b-token" not in json.dumps(second)
    receipts = _assessment_receipts(
        RunContextWrapper({"identity_executor": executor}),
        [first["evidence_ref"], second["evidence_ref"]],
    )
    assert {item["identity_ref"] for item in receipts} == {"a", "b"}
    await executor.close()
    assert b"tenant-a-token" not in (tmp_path / "evidence.db").read_bytes()
    restored = EvidenceLedger(
        tmp_path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=executor.context.digest,
        owns_agent=lambda _: True,
        resuming=True,
    )
    assert restored.read(first["evidence_ref"], require_complete=True) == first
    assert restored.summary()["attempts"] == 4
    restored.close()


@pytest.mark.asyncio
async def test_expiry_rotation_and_rollback_never_reuse_another_identity(
    tmp_path: Path, server: Any
) -> None:
    port, calls = server
    executor = make_executor(tmp_path, port)
    await request(executor)
    write_credentials(tmp_path / "secrets.json", expired=True)
    assert (await request(executor))["status"] == "blocked"
    assert len(calls) == 1
    write_credentials(tmp_path / "secrets.json", token="new-token")
    assert (await request(executor))["status"] == "blocked"
    write_credentials(tmp_path / "secrets.json", revision=2, token="new-token")
    assert (await request(executor))["status"] == "observed"
    assert calls[-1]["cookie"] is None
    assert calls[-1]["token"] == "Bearer new-token"
    write_credentials(tmp_path / "secrets.json")
    assert (await request(executor))["status"] == "blocked"
    assert len(calls) == 2
    await executor.close()
    # The revision floor also survives a process restart.
    resumed = make_executor(tmp_path, port)
    assert (await request(resumed))["status"] == "blocked"
    assert len(calls) == 2
    await resumed.close()


@pytest.mark.asyncio
async def test_redirect_and_truncation_cannot_substantiate_complete_evidence(
    tmp_path: Path, server: Any
) -> None:
    port, calls = server
    executor = make_executor(tmp_path, port)
    redirected = await request(executor, operation="redirect")
    large = await request(executor, operation="large")
    assert redirected["status"] == "blocked"
    assert large["truncated"] is True
    assert not any(call["path"] == "/forbidden" for call in calls)
    for item in [redirected, large]:
        with pytest.raises(EvidenceError, match="Incomplete"):
            executor.ledger.read(item["evidence_ref"], require_complete=True)
    await executor.close()


@pytest.mark.asyncio
async def test_credentials_missing_cross_identity_or_public_never_reach_target(
    tmp_path: Path, server: Any
) -> None:
    port, calls = server
    executor = make_executor(tmp_path, port)
    path = tmp_path / "secrets.json"
    data = json.loads(path.read_text())
    data["secret-a"]["identity_ref"] = "b"
    path.write_text(json.dumps(data))
    assert (await request(executor))["status"] == "blocked"
    write_credentials(path)
    path.chmod(0o644)
    assert (await request(executor))["status"] == "blocked"
    path.unlink()
    assert (await request(executor))["status"] == "blocked"
    assert calls == []
    await executor.close()


@pytest.mark.parametrize(
    "change", ["host", "path", "query", "scheme", "encoding", "method", "identity", "port"]
)
def test_context_rejects_operations_outside_host_approval(change: str) -> None:
    policy, raw = config(1234)
    if change == "identity":
        raw["cases"]["cross-tenant"]["identities"] = ["missing"]
    elif change == "method":
        raw["operations"]["private"]["method"] = "POST"
    elif change == "port":
        policy.network_policy.destinations[0].ports[:] = [5678]
    else:
        url = {
            "host": "http://example.invalid/private",
            "path": "http://127.0.0.1:1234/other",
            "query": "http://127.0.0.1:1234/private?token=secret",
            "scheme": "file:///private",
            "encoding": "http://127.0.0.1:1234/%70rivate",
        }[change]
        raw["operations"]["private"]["url"] = url
        if change == "path":
            policy = policy.model_copy(
                update={
                    "targets": [
                        importlib.import_module("strix.core.assessment").ScopeTarget(
                            type="web_application", value="http://127.0.0.1:1234/private"
                        )
                    ]
                }
            )
    with pytest.raises(ValueError):
        context = parse_context(raw)
        assert context is not None
        context.validate_scope(policy)


def test_context_binding_is_canonical_and_cannot_be_replaced_or_lost(tmp_path: Path) -> None:
    policy, raw = config(1234)
    first = bind_context(tmp_path, "scan", policy, raw, resuming=False)
    reordered = json.loads(json.dumps(raw, sort_keys=True))
    assert bind_context(tmp_path, "scan", policy, reordered, resuming=True).digest == first.digest
    with pytest.raises(ValueError, match="cross-assessment"):
        bind_context(tmp_path, "other", policy, None, resuming=True)
    raw["identities"]["a"]["tenant_ref"] = "other"
    with pytest.raises(ValueError, match="cannot change"):
        bind_context(tmp_path, "scan", policy, raw, resuming=True)
    (tmp_path / "evidence.db").touch()
    (tmp_path / "assessment-context.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        bind_context(tmp_path, "scan", policy, None, resuming=True)


def test_cli_restores_context_without_persisting_credentials_and_rejects_missing_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    policy, raw = config(1234)
    # The CLI rewrites loopback targets for Docker; use a literal documentation IP.
    policy = AssessmentPolicy.model_validate_json(
        policy.model_dump_json().replace("127.0.0.1", "192.0.2.10")
    )
    raw = json.loads(json.dumps(raw).replace("127.0.0.1", "192.0.2.10"))
    policy_path, context_path = tmp_path / "policy.json", tmp_path / "context.json"
    policy_path.write_text(policy.model_dump_json())
    context_path.write_text(json.dumps(raw))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "strix",
            "-n",
            "-t",
            "192.0.2.10",
            "--assessment-policy",
            str(policy_path),
            "--assessment-context",
            str(context_path),
            "--identity-credentials",
            "/private/secret.json",
        ],
    )
    args = parse_arguments()
    assert args.assessment_context == raw
    run = tmp_path / "strix_runs" / "scan"
    state = run / ".state"
    state.mkdir(parents=True)
    bind_assessment_policy(state, "scan", policy, resuming=False)
    context = bind_context(state, "scan", policy, raw, resuming=False)
    assert context is not None
    (state / "agents.json").write_text("{}")
    (run / "run.json").write_text(
        json.dumps(
            {
                "assessment": policy.summary(),
                "targets_info": args.targets_info,
                "network_policy": args.network_policy,
                "assessment_context": {"context_sha256": context.digest},
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["strix", "-n", "--resume", "scan"])
    resumed = parse_arguments()
    assert resumed.assessment_context == raw
    assert resumed.identity_credentials is None
    (state / "assessment-context.json").unlink()
    with pytest.raises(SystemExit):
        parse_arguments()


def test_committed_evidence_and_unfinished_attempt_survive_abrupt_process_exit(
    tmp_path: Path,
) -> None:
    script = """
import os, sys
from pathlib import Path
from strix.core.evidence_ledger import EvidenceLedger
ledger = EvidenceLedger(Path(sys.argv[1]), scan_id='scan', assessment_id='assessment',
                        context_sha256='digest', owns_agent=lambda _: True)
args = dict(agent_ref='agent', case_ref='case', case_version=1,
            identity_ref='a', operation_ref='read')
ref = ledger.finish(ledger.begin(**args), status='observed', content={'body': 'committed'})
ledger.begin(**args)
print(ref, flush=True)
os._exit(17)
"""
    path = tmp_path / "evidence.db"
    child = subprocess.run(  # noqa: S603 -- Controlled local crash fixture, no external input.
        [sys.executable, "-c", script, str(path)], capture_output=True, text=True, check=False
    )
    assert child.returncode == 17, child.stderr
    ledger = EvidenceLedger(
        path,
        scan_id="scan",
        assessment_id="assessment",
        context_sha256="digest",
        owns_agent=lambda _: True,
        resuming=True,
    )
    assert ledger.read(child.stdout.strip())["content"] == {"body": "committed"}
    assert ledger.summary()["unresolved_attempts"] == 1
    ledger.close()


def test_failed_observation_transaction_never_acknowledges_partial_evidence(tmp_path: Path) -> None:
    executor = make_executor(tmp_path, 1234)
    ledger = executor.ledger
    attempt = ledger.begin(
        agent_ref="agent", case_ref="case", case_version=1, identity_ref="a", operation_ref="read"
    )
    with closing(sqlite3.connect(ledger.path)) as db:
        db.execute(
            "CREATE TRIGGER deny_artifact BEFORE INSERT ON artifacts "
            "BEGIN SELECT RAISE(ABORT, 'fixture storage failure'); END"
        )
        db.commit()
    with pytest.raises(EvidenceError, match="storage failed"):
        ledger.finish(attempt, status="observed", content={"body": "not committed"})
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT status FROM attempts").fetchall() == [("started",)]
        assert db.execute("SELECT kind FROM events").fetchall() == [("attempt_started",)]
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert ledger.summary()["status"] == "failed"


def test_evidence_rejects_fabrication_cross_case_cross_run_and_tampering(tmp_path: Path) -> None:
    executor = make_executor(tmp_path, 1234)
    ledger = executor.ledger
    attempt = ledger.begin(
        agent_ref="agent", case_ref="case", case_version=2, identity_ref="a", operation_ref="read"
    )
    artifact = ledger.finish(attempt, status="observed", content={"body": "observed"})
    with pytest.raises(EvidenceError):
        ledger.read("invented")
    with pytest.raises(EvidenceError):
        ledger.read(artifact, case_ref="other")
    with pytest.raises(EvidenceError):
        ledger.finish(attempt, status="observed", content={"body": "replacement"})
    with pytest.raises(EvidenceError):
        EvidenceLedger(
            ledger.path,
            scan_id="other",
            assessment_id="assessment",
            context_sha256=executor.context.digest,
            owns_agent=lambda _: True,
        )
    with closing(sqlite3.connect(ledger.path)) as db:
        db.execute("UPDATE artifacts SET content=?", (b'{"body":"invented"}',))
        db.commit()
    with pytest.raises(EvidenceError, match="integrity"):
        ledger.read(artifact)
    ledger.close()


def test_concurrent_attempts_are_durable_and_unfinished_history_survives(tmp_path: Path) -> None:
    executor = make_executor(tmp_path, 1234)
    ledger = executor.ledger

    def run(_: int) -> str:
        attempt = ledger.begin(
            agent_ref="agent",
            case_ref="case",
            case_version=1,
            identity_ref="a",
            operation_ref="read",
        )
        return ledger.finish(attempt, status="observed", content={"body": "test"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(run, range(20)))
    assert len(set(refs)) == 20
    ledger.begin(
        agent_ref="agent", case_ref="case", case_version=1, identity_ref="a", operation_ref="read"
    )
    ledger.close()
    resumed = make_executor(tmp_path, 1234)
    assert resumed.ledger.summary()["unresolved_attempts"] == 1
    assert resumed.ledger.summary()["artifacts"] == 20
    resumed.ledger.close()


@pytest.mark.asyncio
async def test_storage_failure_prevents_network_execution(tmp_path: Path, server: Any) -> None:
    port, calls = server
    executor = make_executor(tmp_path, port)
    executor.ledger.path.unlink()
    with pytest.raises(EvidenceError, match="storage failed"):
        await request(executor)
    assert executor.ledger.summary()["status"] == "failed"
    assert calls == []
    await executor.close()


@pytest.mark.asyncio
async def test_runner_wires_identity_tools_and_closes_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:

    port, _ = server
    importlib.import_module("tests.test_runner_teardown")._wire_runner(monkeypatch, tmp_path)
    report = ReportState("scan")
    monkeypatch.setattr(report, "save_run_data", lambda: None)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: report)
    monkeypatch.setattr(
        runner.session_manager,
        "create_or_reuse",
        AsyncMock(
            return_value={
                "client": types.SimpleNamespace(
                    network_guard=types.SimpleNamespace(image_id="fixture")
                ),
                "session": object(),
                "caido_client": None,
            }
        ),
    )
    policy, context = config(port)
    secrets = tmp_path.parent / (tmp_path.name + "-secrets.json")
    write_credentials(secrets)

    async def run(**kwargs: Any) -> None:
        executor = kwargs["context"]["identity_executor"]
        result = await executor.request(
            agent_ref=kwargs["agent_id"],
            case_ref="cross-tenant",
            identity_ref="a",
            operation_ref="private",
        )
        assert result["content"]["status_code"] == 200

    monkeypatch.setattr(runner, "run_agent_loop", run)
    await runner.run_strix_scan(
        scan_config={
            "assessment_policy": policy.model_dump(),
            "assessment_context": context,
            "identity_credentials": str(secrets),
            "targets": [{"type": "ip_address", "details": {"target_ip": "127.0.0.1"}}],
        },
        scan_id="scan",
        image="fixture",
        mcp_connection_requests=[],
    )
    assert report.run_record["evidence_ledger"]["status"] == "closed"
    assert report.run_record["evidence_ledger"]["artifacts"] == 1


@pytest.mark.asyncio
async def test_findings_cannot_use_missing_or_incomplete_receipts_and_keep_verified_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    executor = make_executor(tmp_path, server[0])
    evidence = await request(executor)
    monkeypatch.chdir(tmp_path)
    ctx = ToolContext(
        context={"agent_id": "agent", "identity_executor": executor},
        tool_name="create_vulnerability_report",
        tool_call_id="fixture",
        tool_arguments="{}",
    )
    state = ReportState("scan")
    monkeypatch.setattr(state, "save_run_data", lambda **_: None)
    monkeypatch.setattr(
        importlib.import_module("strix.report.state"), "get_global_report_state", lambda: state
    )
    fields = dict.fromkeys(
        [
            "title",
            "description",
            "impact",
            "technical_analysis",
            "poc_description",
            "poc_script_code",
            "remediation_steps",
            "evidence",
            "assumptions",
            "counterevidence",
            "severity_change_conditions",
        ],
        "Fixture-supported interpretation",
    )
    fields.update(
        {
            "target": "http://127.0.0.1",
            "confidence": "high",
            "fix_effort": "low",
            "cvss_breakdown": importlib.import_module("tests.test_reporting_fields")._CVSS,
        }
    )
    for refs in [None, ["invented"]]:
        result = json.loads(
            await reporting.create_vulnerability_report.on_invoke_tool(
                ctx, json.dumps({**fields, "evidence_refs": refs})
            )
        )
        assert result["success"] is False
    assert state.vulnerability_reports == []
    result = json.loads(
        await reporting.create_vulnerability_report.on_invoke_tool(
            ctx, json.dumps({**fields, "evidence_refs": [evidence["evidence_ref"]]})
        )
    )
    assert result["success"] is True
    stored = state.vulnerability_reports[0]["assessment_evidence"][0]
    assert stored["sha256"] == evidence["sha256"]
    assert "content" not in stored
    replacement = await request(executor, "b")
    revised = json.loads(
        await reporting.update_vulnerability_report.on_invoke_tool(
            ctx,
            json.dumps(
                {
                    "report_id": result["report_id"],
                    "update_reason": "Add second identity control",
                    "evidence": "Owner and other tenant compared",
                    "evidence_refs": [evidence["evidence_ref"], replacement["evidence_ref"]],
                }
            ),
        )
    )
    assert revised["success"] is True
    assert len(state.vulnerability_reports[0]["assessment_evidence"]) == 2
    await executor.close()


@pytest.mark.parametrize(
    "field,value", [("scan_id", "other"), ("assessment_id", "other"), ("context_sha256", "other")]
)
def test_evidence_binding_rejects_each_cross_run_dimension(
    tmp_path: Path, field: str, value: str
) -> None:
    executor = make_executor(tmp_path, 1234)
    binding = {
        "scan_id": "scan",
        "assessment_id": "assessment",
        "context_sha256": executor.context.digest,
    }
    binding[field] = value
    with pytest.raises(EvidenceError, match="cross-assessment"):
        EvidenceLedger(executor.ledger.path, **binding, owns_agent=lambda _: True)
    executor.ledger.close()


@pytest.mark.parametrize(
    "status,truncated", [("blocked", False), ("uncertain", False), ("observed", True)]
)
def test_report_gate_rejects_nonconclusive_receipts(
    tmp_path: Path, status: str, truncated: bool
) -> None:
    executor = make_executor(tmp_path, 1234)
    attempt = executor.ledger.begin(
        agent_ref="agent", case_ref="case", case_version=1, identity_ref="a", operation_ref="read"
    )
    ref = executor.ledger.finish(
        attempt, status=status, content={"body": "partial"}, truncated=truncated
    )
    with pytest.raises(EvidenceError, match="Incomplete"):
        _assessment_receipts(RunContextWrapper({"identity_executor": executor}), [ref])
    executor.ledger.close()


@pytest.mark.parametrize(
    "status,unresolved,expected",
    [("closed", 0, True), ("closed", 1, False), ("failed", None, False)],
)
def test_evidence_uncertainty_prevents_clean_result(
    status: str, unresolved: int | None, expected: bool
) -> None:
    coverage = importlib.import_module("tests.test_report_coverage")._document(
        run_record={
            "status": "completed",
            "evidence_ledger": {"status": status, "unresolved_attempts": unresolved},
        }
    )
    assert coverage["completeness"]["complete"] is expected
