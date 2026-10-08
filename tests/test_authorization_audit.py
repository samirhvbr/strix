"""Durable authorization receipts, isolation, write failures and actual denial gates."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import subprocess
import sys
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from strix.core import runner
from strix.core.authorization_audit import AuthorizationAudit, AuthorizationAuditUnavailableError
from strix.report.result import compose_evaluation_result
from strix.report.state import ReportState
from strix.runtime import session_manager
from strix.tools.mcp import McpRegistry, SupervisedMcpSession, call_mcp
from strix.tools.mcp import client as mcp_client
from strix.tools.mcp import session as mcp_session


if TYPE_CHECKING:
    from pathlib import Path


_assessment = importlib.import_module("tests.test_assessment_policy")
_mcp = importlib.import_module("tests.test_mcp_client")
_coverage = importlib.import_module("tests.test_report_coverage")
_teardown = importlib.import_module("tests.test_runner_teardown")


def audit(path: Path, **overrides: Any) -> AuthorizationAudit:
    return AuthorizationAudit(
        path,
        **{
            "scan_id": "scan",
            "assessment_id": "lab-42",
            "policy_sha256": "a" * 64,
            "authorization_ref": "approval-7",
            "operator_ref": "operator-3",
            "approved_tools": {"lab": frozenset({"read"})},
            **overrides,
        },
    )


def rows(path: Path, table: str = "authorization_denials") -> list[dict[str, Any]]:
    assert table in {"authorization_denials", "audit_sessions", "audit_binding"}
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]  # noqa: S608


def test_receipts_survive_resume_without_rejected_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    summaries: list[dict[str, Any]] = []
    first = audit(path, on_change=summaries.append)
    event = first.record_denial(
        "mcp_dispatch", "argument_value_not_allowed", connection="lab", tool="read"
    )
    first.record_denial(
        "mcp_dispatch",
        "connection_not_allowed",
        connection="synthetic-secret-connection",
        tool="synthetic-secret-tool",
    )
    first.close()
    second = audit(path)
    assert second.summary()["committed_denials"] == 2
    assert second.summary()["prior_unclosed_sessions"] == 0
    assert rows(path)[0]["event_id"] == event
    assert rows(path)[1]["connection_ref"] is None
    assert rows(path)[1]["tool_ref"] is None
    assert "synthetic-secret" not in path.read_bytes().decode("utf-8", errors="ignore")
    assert summaries[-1]["status"] == "closed"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_suffix(".required").stat().st_mode & 0o777 == 0o600
    second.close()


def test_committed_receipt_survives_abrupt_process_exit(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    script = """
import os, sys
from pathlib import Path
from strix.core.authorization_audit import AuthorizationAudit
a = AuthorizationAudit(Path(sys.argv[1]), scan_id='scan', assessment_id='lab-42',
    policy_sha256='a'*64, authorization_ref='approval-7', operator_ref='operator-3',
    approved_tools={'lab': frozenset({'read'})})
assert a.record_denial('mcp_dispatch', 'tool_not_allowed', connection='lab')
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=15)  # noqa: S603
    resumed = audit(path)
    assert resumed.summary()["committed_denials"] == 1
    assert resumed.summary()["prior_unclosed_sessions"] == 1
    resumed.close()


def test_legacy_history_remains_unknown_after_later_clean_launches(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    first = audit(path, resuming=True)
    assert first.summary()["history_before_audit"] is True
    first.close()
    second = audit(path)
    assert second.summary()["history_before_audit"] is True
    second.close()


def test_duplicate_receipts_are_idempotent_and_conflicts_latch_failure(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    event = uuid.uuid4().hex
    for _ in range(2):
        assert store.record_denial("mcp_dispatch", "tool_not_allowed", event_id=event) == event
    assert len(rows(path)) == 1
    assert store.record_denial("mcp_transport", "endpoint_not_allowed", event_id=event) is None
    assert not store.available
    assert store.summary()["write_failures"] == 1
    store.close()
    resumed = audit(path)
    assert resumed.summary()["prior_failed_sessions"] == 1
    assert len(rows(path)) == 1
    resumed.close()


def test_concurrent_denials_each_get_one_durable_receipt(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(
            pool.map(lambda _: store.record_denial("mcp_dispatch", "tool_not_allowed"), range(32))
        )
    assert None not in receipts
    assert len(set(receipts)) == len(rows(path)) == 32
    store.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"scan_id": "other"},
        {"assessment_id": "other"},
        {"policy_sha256": "b" * 64},
        {"operator_ref": "other"},
        {"authorization_ref": "other"},
    ],
)
def test_cross_assessment_ledger_is_rejected_without_appending(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    path = tmp_path / "audit.db"
    audit(path).close()
    with pytest.raises(AuthorizationAuditUnavailableError):
        audit(path, **overrides)
    assert len(rows(path, "audit_sessions")) == 1


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt", "future", "symlink", "marker"])
def test_missing_or_invalid_store_never_silently_restarts(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "audit.db"
    audit(path).close()
    if kind == "missing":
        path.unlink()
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "corrupt":
        path.write_bytes(b"not a database")
    elif kind == "future":
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA user_version=2")
    elif kind == "symlink":
        other = tmp_path / "other.db"
        path.rename(other)
        path.symlink_to(other)
    else:
        path.with_suffix(".required").write_bytes(b"invalid")
    with pytest.raises(AuthorizationAuditUnavailableError):
        audit(path)


@pytest.mark.asyncio
async def test_write_failure_denies_original_and_subsequent_calls_without_secret_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TRIGGER fail_receipt AFTER INSERT ON authorization_denials "
            "BEGIN SELECT RAISE(ABORT, 'synthetic-secret-sqlite-error'); END"
        )
        db.commit()
    approved = _assessment.assessment_mcp_requests(_assessment.policy(), [_assessment.request()])[0]
    provider = _mcp.FakeMCPServer("lab", [_mcp._mcp_tool("read")])
    session = SupervisedMcpSession.adopt(
        provider, name="lab", config=approved.config, authorization_audit=store
    )
    result = await session.dispatch("read", {"project": "synthetic-secret"}, label="lab")
    assert result["error"] == "mcp_policy_denied"
    assert result["authorization_receipt"] is None
    assert result["authorization_audit_status"] == "unavailable"
    assert rows(path) == []
    result = await session.dispatch("read", {"project": "lab-42"}, label="lab")
    assert result["error"] == "authorization_audit_unavailable"
    assert provider.calls == []
    assert "synthetic-secret" not in caplog.text
    store.close()
    assert rows(path, "audit_sessions")[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_agent_gate_records_once_before_discovery_and_keeps_legacy_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    registry = McpRegistry(authorization_audit=store)
    approved = _assessment.assessment_mcp_requests(_assessment.policy(), [_assessment.request()])[0]
    entry = registry.register(approved)
    discovery = AsyncMock()
    monkeypatch.setattr(entry, "ensure_catalog", discovery)
    attempts = [
        {"connection": "unapproved-secret", "tool": "read", "arguments": {}},
        {"connection": "lab", "tool": "unapproved-secret", "arguments": {}},
        {"connection": "lab", "tool": "read", "arguments": {"project": "unapproved-secret"}},
        {"connection": "lab", "tool": "read", "arguments": "invalid-secret-json"},
    ]
    for attempt in attempts:
        result = await call_mcp.on_invoke_tool(_mcp._ctx(registry), json.dumps(attempt))
        assert result["authorization_audit_status"] == "recorded"
        assert result["authorization_receipt"] == rows(path)[-1]["event_id"]
    discovery.assert_not_called()
    assert len(rows(path)) == 4
    assert "secret" not in path.read_bytes().decode("utf-8", errors="ignore")
    legacy = await call_mcp.on_invoke_tool(_mcp._ctx(McpRegistry()), json.dumps(attempts[0]))
    assert legacy == "No MCP connections are configured for this run."
    store.close()


@pytest.mark.asyncio
async def test_transport_rejections_are_durable_without_urls_or_redirect_locations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    params: dict[str, Any] = {}
    sent: list[str] = []

    def capture_server(**kwargs: Any) -> object:
        params.update(kwargs["params"])
        return object()

    async def respond(req: httpx.Request) -> httpx.Response:
        sent.append(str(req.url))
        return httpx.Response(307, headers={"Location": "https://secret.invalid/token"})

    monkeypatch.setattr(mcp_client, "MCPServerStreamableHttp", capture_server)
    monkeypatch.setattr(
        mcp_client,
        "create_mcp_http_client",
        lambda **_: httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            follow_redirects=True,
        ),
    )
    approved = _assessment.assessment_mcp_requests(_assessment.policy(), [_assessment.request()])[0]
    mcp_client._build_server(approved.config, authorization_audit=store)
    async with params["httpx_client_factory"]() as client:
        with pytest.raises(ValueError, match="approved endpoint"):
            await client.get("https://unapproved-secret.invalid")
        assert sent == []
        with pytest.raises(ValueError, match="redirect blocked"):
            await client.post(approved.config.url)
    assert len(sent) == 1
    assert [row["reason"] for row in rows(path)] == ["endpoint_not_allowed", "redirect_not_allowed"]
    assert "secret" not in path.read_bytes().decode("utf-8", errors="ignore")
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,requests,reason",
    [
        ({"targets": []}, [_assessment.request()], "scope_rejected"),
        ({}, [], "mcp_configuration_rejected"),
    ],
)
async def test_runner_records_startup_refusal_before_sandbox_or_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    requests: list[Any],
    reason: str,
) -> None:
    monkeypatch.setattr(runner, "run_dir_for", lambda _: tmp_path)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: None)
    sandbox, model = AsyncMock(), AsyncMock()
    monkeypatch.setattr(session_manager, "create_or_reuse", sandbox)
    monkeypatch.setattr(runner, "run_agent_loop", model)
    with pytest.raises(ValueError):
        await runner.run_strix_scan(
            scan_config=_assessment.config(**overrides),
            scan_id="scan",
            image="unused",
            mcp_connection_requests=requests,
        )
    path = tmp_path / ".state" / "authorization_audit.db"
    assert rows(path)[0]["reason"] == reason
    assert rows(path, "audit_sessions")[0]["status"] == "closed"
    sandbox.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("ending", ["success", "failure", "cancelled"])
@pytest.mark.asyncio
async def test_runner_finalizes_audit_and_publishes_matching_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    _teardown._wire_runner(monkeypatch, tmp_path)
    report = ReportState("scan")
    monkeypatch.setattr(runner, "get_global_report_state", lambda: report)
    monkeypatch.setattr(report, "save_run_data", lambda: None)
    monkeypatch.setattr(
        session_manager,
        "create_or_reuse",
        AsyncMock(
            return_value={
                "client": types.SimpleNamespace(
                    network_guard=types.SimpleNamespace(image_id="sha256:fixture")
                ),
                "session": object(),
                "caido_client": None,
            }
        ),
    )

    async def run(**kwargs: Any) -> None:
        registry = kwargs["context"]["mcp_registry"]
        assert registry.authorization_audit is not None
        result = await call_mcp.on_invoke_tool(
            _mcp._ctx(registry),
            json.dumps({"connection": "unknown", "tool": "read", "arguments": {}}),
        )
        assert result["authorization_audit_status"] == "recorded"
        if ending == "failure":
            raise RuntimeError("simulated failure")
        if ending == "cancelled":
            raise asyncio.CancelledError

    monkeypatch.setattr(runner, "run_agent_loop", run)
    config = _assessment.config()
    config["assessment_policy"]["mcp_connections"] = {}
    invocation = runner.run_strix_scan(
        scan_config=config, scan_id="scan", image="unused", mcp_connection_requests=[]
    )
    if ending == "success":
        await invocation
    else:
        with pytest.raises(RuntimeError if ending == "failure" else asyncio.CancelledError):
            await invocation
    assert report.run_record["authorization_audit"]["status"] == "closed"
    assert report.run_record["authorization_audit"]["committed_denials"] == 1
    assert rows(tmp_path / "authorization_audit.db", "audit_sessions")[0]["status"] == "closed"


@pytest.mark.parametrize(
    "issue",
    [
        None,
        "recording",
        "failed",
        "prior_unclosed_sessions",
        "prior_failed_sessions",
        "write_failures",
        "history_before_audit",
    ],
)
def test_audit_gaps_prevent_a_clean_result_without_turning_denials_into_findings(
    issue: str | None,
) -> None:
    summary: dict[str, Any] = {"status": "closed", "committed_denials": 4}
    if issue in {"recording", "failed"}:
        summary["status"] = issue
    elif issue is not None:
        summary[issue] = 1
    coverage = _coverage._document(
        run_record={"status": "completed", "authorization_audit": summary},
        exit_reason="finished_by_tool",
    )
    result = compose_evaluation_result(
        coverage_document=coverage, findings_count=0, findings_policy_failed=False
    )
    assert result.operationally_complete is (issue is None)
    assert result.exit_code == (0 if issue is None else 3)


@pytest.mark.asyncio
async def test_queued_call_rechecks_audit_after_another_denial_loses_its_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    approved = _assessment.assessment_mcp_requests(_assessment.policy(), [_assessment.request()])[0]
    provider = _mcp.FakeMCPServer("lab", [_mcp._mcp_tool("read")])
    session = SupervisedMcpSession.adopt(
        provider, name="lab", config=approved.config, authorization_audit=store
    )
    waiting, release = asyncio.Event(), asyncio.Event()
    original = session._run_job

    async def wait_before_dispatch(job: Any, *, phase: Any) -> Any:
        waiting.set()
        await release.wait()
        return await original(job, phase=phase)

    monkeypatch.setattr(session, "_run_job", wait_before_dispatch)
    pending = asyncio.create_task(session.dispatch("read", {"project": "lab-42"}, label="lab"))
    await waiting.wait()
    path.unlink()
    assert store.record_denial("mcp_dispatch", "tool_not_allowed") is None
    release.set()
    assert (await pending)["error"] == "authorization_audit_unavailable"
    assert provider.calls == []
    await session.aclose()
    store.close()


@pytest.mark.asyncio
async def test_reconnect_carries_same_audit_and_registry_cannot_drop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "audit.db"
    store = audit(path)
    approved = _assessment.assessment_mcp_requests(_assessment.policy(), [_assessment.request()])[0]
    first = _mcp.FakeMCPServer("lab", [_mcp._mcp_tool("read")])
    replacement = _mcp.FakeMCPServer("lab", [_mcp._mcp_tool("read")])
    monkeypatch.setattr(first, "call_tool", AsyncMock(side_effect=httpx.ConnectError("fixture")))

    def build(_config: Any, *, authorization_audit: AuthorizationAudit) -> Any:
        assert authorization_audit is store
        return mcp_client.BuiltMcpServer(replacement, None)

    monkeypatch.setattr(mcp_client, "_build_server", build)
    monkeypatch.setattr(mcp_session, "_retry_delay", lambda *_: 0)
    session = SupervisedMcpSession.adopt(
        first, name="lab", config=approved.config, authorization_audit=store
    )
    with pytest.raises(ValueError, match="authorization audit"):
        McpRegistry().add(name="lab", session=session)
    result = await session.dispatch("read", {"project": "lab-42"}, label="lab")
    assert result["text"] == "routed:read"
    denied = await session.dispatch("delete", {}, label="lab")
    assert denied["authorization_receipt"] == rows(path)[0]["event_id"]
    assert replacement.calls == [("read", {"project": "lab-42"})]
    await session.aclose()
    store.close()
