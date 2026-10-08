"""Target secrets cannot cross the model, report, export or viewer boundary."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError

import httpx
import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext

from strix.core.evidence_ledger import EvidenceError
from strix.interface.viewer.server import serve
from strix.report.state import ReportState
from strix.tools.assessment.tools import read_assessment_evidence
from strix.tools.reporting.tool import _assessment_receipts
from tests.test_assessment_context import make_executor, request
from tests.test_viewer import _get, _session_cookie


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_unknown_secrets_are_private_across_tools_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    executor = make_executor(run_dir / ".state", 8080)
    synthetic = "SYNTHETIC-PRIVATE-RESPONSE-91c742"
    variants = [
        synthetic,
        base64.b64encode(synthetic.encode()).decode(),
        "".join(f"%{ord(char):02X}" for char in synthetic),
    ]
    body = json.dumps({"password": variants, "personal_data": "synthetic@example.invalid"})
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body.encode()))
    )
    executor._clients["a"] = client
    executor._revisions["a"] = 1
    receipt = await request(executor)
    private = executor.ledger.read_private(receipt["evidence_ref"])
    assert private["content"]["body"] == body
    assert receipt["content"] == {"status_code": 200, "restricted_content": True}
    assert receipt["sha256"] == private["sha256"]
    ctx: RunContextWrapper[dict[str, Any]] = RunContextWrapper({"identity_executor": executor})
    arguments = json.dumps({"evidence_ref": receipt["evidence_ref"], "case_ref": "cross-tenant"})
    tool_ctx = ToolContext(
        context=ctx.context,
        tool_name="read_assessment_evidence",
        tool_call_id="fixture",
        tool_arguments=arguments,
    )
    reread = await read_assessment_evidence.on_invoke_tool(tool_ctx, arguments)
    receipts = _assessment_receipts(ctx, [receipt["evidence_ref"]])
    public = json.dumps({"first": receipt, "reread": reread, "finding": receipts})
    assert all(value not in public for value in variants)
    assert "synthetic@example.invalid" not in public
    state = ReportState("artifacts")
    state._run_dir = run_dir
    state.add_vulnerability_report(
        title="Synthetic receipt linkage",
        severity="info",
        description=public,
        assessment_evidence=receipts,
    )
    state.final_scan_result = public
    state.save_run_data(mark_complete=True)
    for name in [
        "run.json",
        "vulnerabilities.json",
        "findings.sarif",
        "penetration_test_report.md",
        "vulnerabilities.csv",
    ]:
        content = (run_dir / name).read_text()
        assert all(value not in content for value in variants)
    assert executor.ledger.path.stat().st_mode & 0o777 == 0o600
    assert executor.ledger.path.parent.stat().st_mode & 0o777 == 0o700
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("safe viewer")
    monkeypatch.setattr("strix.interface.viewer.server.bundle_dir", lambda: assets)
    httpd, url, token = serve(run_dir, open_browser=False)
    try:
        cookie = _session_cookie(url, token)
        for endpoint in [
            "/api/report",
            "/api/vulnerabilities",
            "/api/transcript",
            "/.state/evidence.db",
            "/assets/../.state/evidence.db",
        ]:
            try:
                _, _, content = _get(url + endpoint, cookie=cookie)
                assert all(value.encode() not in content for value in variants)
                assert not content.startswith(b"SQLite format 3")
            except HTTPError as exc:
                assert exc.code in {403, 404}
    finally:
        httpd.shutdown()
        httpd.server_close()
        await executor.close()


@pytest.mark.asyncio
async def test_permission_change_blocks_both_raw_and_projected_access(tmp_path: Path) -> None:
    executor = make_executor(tmp_path, 8080)
    attempt = executor.ledger.begin(
        agent_ref="agent",
        case_ref="cross-tenant",
        case_version=1,
        identity_ref="a",
        operation_ref="private",
    )
    ref = executor.ledger.finish(attempt, status="observed", content={"body": "private"})
    executor.ledger.path.chmod(0o644)
    for read in [executor.ledger.read, executor.ledger.read_private]:
        with pytest.raises(EvidenceError, match="permissions"):
            read(ref)
    with pytest.raises(EvidenceError, match="owner-only"):
        make_executor(tmp_path, 8080)
    executor.ledger.path.chmod(0o600)
    await executor.close()
