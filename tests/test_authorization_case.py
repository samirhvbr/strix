"""A retest needs a private object, valid identities and a legitimate owner control."""

from __future__ import annotations

import importlib
import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from strix.core import runner
from strix.core.assessment_context import FileCredentials, bind_context, parse_context
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.report.state import ReportState
from tests.test_assessment_context import config, write_credentials


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def private_server() -> Any:
    state: dict[str, Any] = {"mode": "vulnerable", "calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            state["calls"] += 1
            identity = "a" if self.headers.get("Authorization") == "Bearer tenant-a-token" else "b"
            code = 200
            if self.path == "/control":
                data = {
                    "identity_ref": identity,
                    "tenant_ref": "tenant-" + identity,
                    "authenticated": state["mode"] != "logged-out",
                }
            else:
                data = {
                    "resource_ref": "private-42",
                    "owner_ref": "a",
                    "tenant_ref": "tenant-a",
                    "private": True,
                    "data": "SYNTHETIC-PRIVATE-CONTENT-42",
                }
                if identity == "b" and state["mode"] != "vulnerable":
                    code, data = 403, {}
                if identity == "a" and state["mode"] == "broken-owner":
                    code, data = 404, {}
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def executor_at(path: Path, port: int) -> IdentityExecutor:
    policy, raw = config(port)
    raw["version"] = 2
    raw["operations"] = {
        name: {"method": "GET", "url": f"http://127.0.0.1:{port}/{name}"}
        for name in ["private", "control"]
    }
    raw["cases"]["cross-tenant"].update(
        operations=["private", "control"],
        authorization={
            "adapter": "http.private-read",
            "version": 1,
            "owner_ref": "a",
            "other_ref": "b",
            "control_operation": "control",
            "resource_operation": "private",
            "resource_ref": "private-42",
        },
    )
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
        owns_agent=lambda agent: agent == "agent",
    )
    return IdentityExecutor(context, FileCredentials(credentials, "assessment"), ledger)


@pytest.mark.asyncio
async def test_private_resource_baseline_retest_and_resume(
    tmp_path: Path, private_server: Any
) -> None:
    port, target = private_server
    executor = executor_at(tmp_path, port)
    assert executor.ledger.summary()["case_evaluations"][0]["verdict"] == "missing"
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    assert first["verdict"] == "vulnerable"
    assert first["controls_valid"] and first["legitimate_owner"]
    assert "SYNTHETIC-PRIVATE" not in json.dumps(first)
    assert target["calls"] == 6
    document = importlib.import_module("tests.test_report_coverage")._document(
        run_record={
            "status": "completed",
            "evidence_ledger": executor.ledger.summary() | {"status": "closed"},
        }
    )
    assert not document["completeness"]["complete"]
    assert any(gap["kind"] == "unreported_case_finding" for gap in document["gaps"])
    baseline = first["case_result_ref"]
    target["mode"] = "fixed"
    retest = await executor.run_case(
        agent_ref="agent", case_ref="cross-tenant", baseline_ref=baseline
    )
    assert retest["verdict"] == "compliant"
    assert retest["retest_status"] == "fixed"
    await executor.close()
    resumed = executor_at(tmp_path, port)
    assert resumed.ledger.read_case_result(baseline, "cross-tenant") == first
    assert resumed.ledger.summary()["case_evaluations"][0]["retest_status"] == "fixed"
    await resumed.request(
        agent_ref="agent", case_ref="cross-tenant", identity_ref="b", operation_ref="private"
    )
    assert resumed.ledger.summary()["case_evaluations"][0]["verdict"] == "needs_retest"
    await resumed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limitation", ["expired", "logged-out", "broken-owner"])
async def test_limitations_cannot_close_a_vulnerable_baseline(
    tmp_path: Path, private_server: Any, limitation: str
) -> None:
    port, target = private_server
    executor = executor_at(tmp_path, port)
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    target["mode"] = limitation
    if limitation == "expired":
        write_credentials(tmp_path / "credentials.json", expired=True)
    retest = await executor.run_case(
        agent_ref="agent", case_ref="cross-tenant", baseline_ref=first["case_result_ref"]
    )
    assert retest["verdict"] == "inconclusive"
    assert retest["retest_status"] == "inconclusive"
    summary = executor.ledger.summary() | {"status": "closed"}
    document = importlib.import_module("tests.test_report_coverage")._document(
        run_record={"status": "completed", "evidence_ledger": summary}
    )
    assert not document["completeness"]["complete"]
    await executor.close()


@pytest.mark.asyncio
async def test_forged_baseline_cannot_dispatch(tmp_path: Path, private_server: Any) -> None:
    port, target = private_server
    executor = executor_at(tmp_path, port)
    with pytest.raises(EvidenceError, match="Unknown"):
        await executor.run_case(agent_ref="agent", case_ref="cross-tenant", baseline_ref="invented")
    assert target["calls"] == 0
    await executor.close()


def test_version_one_context_serialization_is_unchanged() -> None:
    _, raw = config(8080)
    context = parse_context(raw)
    assert context is not None
    assert context.model_dump() == raw


@pytest.mark.asyncio
async def test_runner_publishes_runtime_finding_without_model_attestation(
    tmp_path: Path, private_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _ = private_server
    source = executor_at(tmp_path / "config", port)
    raw = source.context.model_dump()
    secrets = tmp_path / "config" / "credentials.json"
    await source.close()
    importlib.import_module("tests.test_runner_teardown")._wire_runner(
        monkeypatch, tmp_path / "run"
    )
    report = ReportState("scan")
    monkeypatch.setattr(report, "save_run_data", lambda **_: None)
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

    async def run(**kwargs: Any) -> None:
        executor = kwargs["context"]["identity_executor"]
        result = await executor.run_case(agent_ref=kwargs["agent_id"], case_ref="cross-tenant")
        assert result["verdict"] == "vulnerable"

    monkeypatch.setattr(runner, "run_agent_loop", run)
    policy, _ = config(port)
    await runner.run_strix_scan(
        scan_config={
            "assessment_policy": policy.model_dump(),
            "assessment_context": raw,
            "identity_credentials": str(secrets),
            "targets": [{"type": "ip_address", "details": {"target_ip": "127.0.0.1"}}],
        },
        scan_id="scan",
        image="fixture",
        mcp_connection_requests=[],
    )
    assert len(report.vulnerability_reports) == 1
    assert report.vulnerability_reports[0]["cwe"] == "CWE-639"
    assert len(report.vulnerability_reports[0]["assessment_evidence"]) == 6
    assert "SYNTHETIC-PRIVATE" not in json.dumps(report.vulnerability_reports)
