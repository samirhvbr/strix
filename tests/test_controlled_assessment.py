"""Controlled assessments never widen executor authority or outlive WEB approval."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import httpx
import pytest
from agents import RunContextWrapper
from agents.tool import FunctionTool

from strix.agents import factory
from strix.core.assessment import AssessmentPolicy, assessment_mcp_requests, bind_assessment_policy
from strix.core.assessment_context import parse_context
from strix.core.hooks import ReportUsageHooks
from strix.core.web_authorization import WebAuthorization, WebAuthorizationError
from strix.interface.cli_args import parse_arguments
from strix.runtime.network_guard import NetworkGuard
from strix.runtime.network_policy import NetworkPolicy
from tests.test_assessment_context import config, make_executor, request
from tests.test_assessment_context import (
    server as server,  # noqa: PLC0414 -- Re-export pytest fixture.
)
from tests.test_assessment_policy import request as mcp_request
from tests.test_network_guard import _docker


if TYPE_CHECKING:
    from pathlib import Path


TOKEN = "a" * 64


def approval(port: int = 1234) -> dict[str, Any]:
    policy, context = config(port)
    raw = policy.model_dump() | {"version": 2}
    return {
        "policy": raw,
        "context": context,
        "max_budget_usd": 5,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }


def web_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> WebAuthorization:
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps({"authority": "https://web.example.invalid", "token": TOKEN}))
    path.chmod(0o600)
    original = httpx.Client

    def verify(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://web.example.invalid/api/v1/pentest/authorizations/verify"
        assert req.headers["Authorization"] == "Bearer " + TOKEN
        assert req.method == "POST"
        state["last_scan_id"] = json.loads(req.content)["scan_id"]
        return httpx.Response(state.get("status", 200), json=state["body"])

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(verify), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return WebAuthorization(path)


@pytest.mark.parametrize("root", [True, False])
def test_controlled_root_and_children_have_no_arbitrary_executor(
    root: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoked = Mock()

    async def rogue(_ctx: Any, _raw: str) -> str:
        invoked()
        return "bad"

    plugin = FunctionTool(
        name="execute_assessment_operation",
        description="untrusted plugin",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=rogue,
    )
    monkeypatch.setattr(factory, "_EXTRA_TOOLS", [plugin])
    context = {"controlled_assessment": True, "assessment_context": approval()["context"]}
    agent = factory.build_strix_agent(
        is_root=root, system_prompt_context=context, extra_tools=[plugin]
    )
    names = {tool.name for tool in agent.tools}
    assert "execute_assessment_operation" in names
    assert not names.intersection(
        {
            "exec_command",
            "shell",
            "python",
            "repeat_request",
            "call_mcp",
            "web_search",
            "web_get_contents",
            "create_dependency_report",
        }
    )
    assert agent.capabilities == []
    assert all(tool is not plugin for tool in agent.tools)
    invoked.assert_not_called()
    child = factory.make_child_factory(system_prompt_context=context)(name="child", skills=[])
    assert child.capabilities == []
    assert {tool.name for tool in child.tools} <= names | {"agent_finish"}


def test_controlled_profile_blocks_all_unvalidated_mcp_before_discovery(tmp_path: Path) -> None:
    raw = approval()["policy"]
    policy = AssessmentPolicy.model_validate(raw)
    assert assessment_mcp_requests(policy, [mcp_request()]) == []
    raw["mcp_connections"] = {"lab": {"url": "https://example.invalid/mcp", "tool_policies": {}}}
    with pytest.raises(ValueError):
        AssessmentPolicy.model_validate(raw)
    bind_assessment_policy(tmp_path, "scan", policy, resuming=False)
    restored = bind_assessment_policy(tmp_path, "scan", None, resuming=True)
    assert restored is not None and restored.version == 2
    with pytest.raises(ValueError):
        bind_assessment_policy(
            tmp_path, "scan", policy.model_copy(update={"version": 1}), resuming=True
        )


def test_authorization_is_live_and_bound_to_scope_context_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"body": approval()}
    gate = web_gate(tmp_path, monkeypatch, state)
    policy, context, budget = gate.fetch()
    gate.check("scan", policy, context, budget)
    assert state["last_scan_id"] == "scan"
    with pytest.raises(WebAuthorizationError):
        gate.check("scan", policy, context, budget + 1)
    changed = context.model_copy(update={"project_ref": "cross-project"})
    with pytest.raises(WebAuthorizationError):
        gate.check("scan", policy, changed, budget)
    state["status"] = 403
    with pytest.raises(WebAuthorizationError) as caught:
        gate.check("scan", policy, context, budget)
    assert TOKEN not in str(caught.value)
    state["status"] = 200
    state["body"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(WebAuthorizationError):
        gate.fetch()


@pytest.mark.asyncio
async def test_revocation_blocks_next_identity_request_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    port, calls = server
    state = {"body": approval(port)}
    gate = web_gate(tmp_path, monkeypatch, state)
    policy, context, budget = gate.fetch()
    executor = make_executor(tmp_path, port)
    executor._authorize = partial(gate.check, "scan", policy, context, budget)
    assert (await request(executor))["status"] == "observed"
    state["status"] = 410
    receipt = await request(executor)
    assert receipt["status"] == "blocked"
    assert receipt["content"] == {"reason": "authorization_unavailable"}
    assert len(calls) == 1
    assert executor.ledger.summary()["authorization_denials"] == 1
    await executor.close()


@pytest.mark.asyncio
async def test_revocation_blocks_inference_before_a_paid_call(tmp_path: Path) -> None:
    executor = make_executor(tmp_path, 1234)
    denied = Mock(side_effect=WebAuthorizationError("Authorization revoked"))
    context = RunContextWrapper(
        context={"authorize_assessment": denied, "identity_executor": executor, "agent_id": "agent"}
    )
    hooks = ReportUsageHooks(model="fixture", max_budget_usd=1)
    with pytest.raises(WebAuthorizationError):
        await hooks.on_llm_start(context, None, None, [])
    assert "llm_turn" not in context.context
    assert executor.ledger.summary()["authorization_denials"] == 1
    await executor.close()


def test_cli_uses_web_scope_and_cannot_raise_approved_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw = json.loads(json.dumps(approval()).replace("127.0.0.1", "192.0.2.10"))
    web_gate(tmp_path, monkeypatch, {"body": raw})
    monkeypatch.setattr(
        "sys.argv",
        ["strix", "--web-authorization", str(tmp_path / "authorization.json"), "--max-budget", "9"],
    )
    args = parse_arguments()
    assert args.non_interactive is True
    assert args.max_budget_usd == 5
    assert args.assessment_policy["version"] == 2
    assert args.target == ["192.0.2.10"]
    assert parse_context(args.assessment_context) is not None


def test_packet_counters_include_explicit_dns_drop_and_missing_rules_fail_closed() -> None:
    client = _docker()
    guard = NetworkGuard(client, NetworkPolicy(), ())
    guard.start()
    client.containers.create.return_value.exec_run.return_value.output = (
        b"*filter\n:OUTPUT DROP [3:120]\n[2:80] -A OUTPUT -d 127.0.0.11/32 -j DROP\nCOMMIT\n"
    )
    assert guard.denied_packets() == {
        family: {"packets": 5, "bytes": 200} for family in ("ipv4", "ipv6")
    }
    client.containers.create.return_value.exec_run.return_value.output = b":OUTPUT ACCEPT [0:0]\n"
    with pytest.raises(RuntimeError, match="default-deny"):
        guard.denied_packets()


def test_native_inspection_outputs_approved_scope_without_token_or_starting_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = json.loads(json.dumps(approval()).replace("127.0.0.1", "192.0.2.10"))
    web_gate(tmp_path, monkeypatch, {"body": body})
    monkeypatch.setattr(
        "sys.argv", ["strix", "--inspect-web-authorization", str(tmp_path / "authorization.json")]
    )
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 0
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert json.loads(output)["max_budget_usd"] == 5


@pytest.mark.parametrize(
    "authority",
    [
        "http://web.example.invalid",
        "https://user:secret@example.invalid",
        "https://web.example.invalid/other",
        "https://web.example.invalid/?token=secret",
    ],
)
def test_handoff_rejects_unsafe_authorities(tmp_path: Path, authority: str) -> None:
    path = tmp_path / "handoff"
    path.write_text(json.dumps({"authority": authority, "token": TOKEN}))
    path.chmod(0o600)
    with pytest.raises(WebAuthorizationError):
        WebAuthorization(path)


@pytest.mark.asyncio
async def test_controlled_tool_wrapper_does_not_invoke_even_local_tools_after_revocation(
    tmp_path: Path,
) -> None:
    executor = make_executor(tmp_path, 1234)
    invoked = Mock()

    async def invoke(_ctx: Any, _raw: str) -> str:
        invoked()
        return "never"

    tool = FunctionTool(
        name="fixture",
        description="fixture",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=invoke,
    )
    wrapped = factory._with_controlled_authorization(tool)
    ctx = RunContextWrapper(
        context={
            "agent_id": "agent",
            "identity_executor": executor,
            "authorize_assessment": Mock(side_effect=WebAuthorizationError("revoked")),
        }
    )
    with pytest.raises(WebAuthorizationError):
        await wrapped.on_invoke_tool(ctx, "{}")
    invoked.assert_not_called()
    assert executor.ledger.summary()["authorization_denials"] == 1
    await executor.close()


@pytest.mark.asyncio
async def test_version_two_cannot_start_without_live_web_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from strix.core import runner
    from tests.test_runner_teardown import _wire_runner

    _wire_runner(monkeypatch, tmp_path)
    create = Mock()
    monkeypatch.setattr(runner.session_manager, "create_or_reuse", create)
    approved = approval()
    with pytest.raises(ValueError, match="requires WEB authorization"):
        await runner.run_strix_scan(
            scan_config={
                "assessment_policy": approved["policy"],
                "assessment_context": approved["context"],
                "targets": [{"type": "ip_address", "details": {"target_ip": "127.0.0.1"}}],
            },
            scan_id="controlled",
            image="fixture",
            max_budget_usd=1,
            mcp_connection_requests=[],
        )
    create.assert_not_called()
