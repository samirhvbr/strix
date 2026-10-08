"""Capability denials must never reach MCP providers or consume their retry path."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import ValidationError

from strix.tools.mcp import (
    McpConnectionConfig,
    McpConnectionRequest,
    McpRegistry,
    McpToolPolicy,
    SupervisedMcpSession,
    call_mcp,
    client,
)
from strix.tools.mcp import session as session_module
from strix.tools.mcp.policy import McpDispatchPolicy


if TYPE_CHECKING:
    from pathlib import Path


_helpers = importlib.import_module("tests.test_mcp_client")
FakeMCPServer: Any = _helpers.FakeMCPServer
_mcp_tool: Any = _helpers._mcp_tool
_ctx: Any = _helpers._ctx


def _config(**overrides: Any) -> McpConnectionConfig:
    return McpConnectionConfig.model_validate(
        {
            "name": "lab",
            "url": "https://mcp.example.invalid",
            "tool_policies": {
                "read": {
                    "allowed_arguments": ["project", "query", "enabled"],
                    "required_arguments": ["query"],
                    "argument_values": {"project": ["approved"], "enabled": [False]},
                },
            },
            **overrides,
        }
    )


def _arguments(**overrides: Any) -> dict[str, Any]:
    return {"project": "approved", "query": "fixture", "enabled": False, **overrides}


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (_arguments(), None),
        (_arguments(project="other"), "argument_value_not_allowed"),
        (_arguments(project="APPROVED"), "argument_value_not_allowed"),
        (_arguments(project=["approved"]), "argument_value_not_allowed"),
        (_arguments(project={"name": "approved"}), "argument_value_not_allowed"),
        (_arguments(enabled=0), "argument_value_not_allowed"),
        (_arguments(enabled="false"), "argument_value_not_allowed"),
        (_arguments(extra="escape"), "argument_not_allowed"),
        ({"project": "approved", "enabled": False}, "required_argument_missing"),
        ({"query": "fixture", "enabled": False}, "required_argument_missing"),
    ],
)
def test_exact_argument_grants(arguments: dict[str, Any], reason: str | None) -> None:
    assert McpDispatchPolicy(_config()).rejection("read", arguments) == reason


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"allowed_arguments": ["project"], "argument_values": {"project": []}},
        {"allowed_arguments": [], "required_arguments": ["project"]},
        {"allowed_arguments": [], "argument_values": {"project": ["approved"]}},
        {"allowed_arguments": ["project"], "argument_values": {"project": [["approved"]]}},
        {"allowed_arguments": ["project"], "argument_values": {"project": [float("nan")]}},
        {"allowed_arguments": ["project"], "argument_values": {"project": [float("inf")]}},
        {"allowed_arguments": ["project"], "argument_value": {"project": ["approved"]}},
        {"allowed_arguments": [1]},
    ],
)
def test_invalid_policies_are_rejected(policy: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        McpToolPolicy.model_validate(policy)


@pytest.mark.parametrize(
    ("config", "allowed"),
    [
        (_config(tool_policies=None), True),
        (_config(tool_policies={}), False),
        (_config(allowed_tools=[]), False),
        (_config(allowed_tools=["other"]), False),
        (_config(allowed_tools=["read", "other"]), True),
    ],
)
def test_allowlists_intersect_with_tool_grants(config: McpConnectionConfig, allowed: bool) -> None:
    policy = McpDispatchPolicy(config)
    assert policy.allows_tool("read") is allowed
    if config.tool_policies is not None:
        assert not policy.allows_tool("other")


@pytest.mark.asyncio
async def test_adopted_server_cannot_override_allowlist_or_policy() -> None:
    server = FakeMCPServer("lab", [_mcp_tool("read"), _mcp_tool("delete")])
    session = SupervisedMcpSession.adopt(server, name="lab", config=_config())
    assert [tool.name for tool in await session.list_tools()] == ["read"]
    for tool, args in [("delete", {}), ("read", _arguments(project="other"))]:
        result = await session.dispatch(tool, args, label="lab_call")
        assert result["error"] == "mcp_policy_denied"
        assert result["success"] is False
    assert server.calls == []
    assert not session.is_dead
    assert not session.is_unavailable
    assert await session.dispatch("read", _arguments(), label="lab_read") == {
        "type": "text",
        "text": "routed:read",
    }
    await session.aclose()


@pytest.mark.asyncio
async def test_legacy_allowlist_is_enforced_for_direct_dispatch() -> None:
    server = FakeMCPServer("lab", [_mcp_tool("read"), _mcp_tool("delete")])
    session = SupervisedMcpSession.adopt(
        server, name="lab", config=_config(tool_policies=None, allowed_tools=["read"])
    )
    assert (await session.dispatch("delete", {}, label="lab_delete"))["success"] is False
    assert server.calls == []
    assert (await session.dispatch("read", {}, label="lab_read"))["text"] == "routed:read"
    await session.aclose()


@pytest.mark.asyncio
async def test_cold_registry_denies_without_connecting_or_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unexpected_connect(_config: McpConnectionConfig) -> Any:
        pytest.fail("A denied call must not connect or list tools")

    monkeypatch.setattr(client, "_build_server", unexpected_connect)
    registry = McpRegistry()
    config = _config()
    registry.register(McpConnectionRequest(config=config))
    config.tool_policies = None
    for tool, arguments in [("delete", {}), ("read", _arguments(project="synthetic-secret"))]:
        result = await call_mcp.on_invoke_tool(
            _ctx(registry), json.dumps({"connection": "lab", "tool": tool, "arguments": arguments})
        )
        assert result["error"] == "mcp_policy_denied"
        assert "synthetic-secret" not in json.dumps(result)
    assert registry.get("lab").state == "configured"
    assert "tool_not_allowed" in caplog.text
    assert "argument_value_not_allowed" in caplog.text
    assert "synthetic-secret" not in caplog.text
    await registry.close()


@pytest.mark.asyncio
async def test_queued_call_and_retry_use_the_approved_argument_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeMCPServer("lab", [_mcp_tool("read")])
    config = _config()
    session = SupervisedMcpSession.adopt(server, name="lab", config=config)
    waiting = asyncio.Event()
    release = asyncio.Event()
    original_run_job = session._run_job

    async def wait_before_dispatch(job: Any, *, phase: Any) -> Any:
        waiting.set()
        await release.wait()
        return await original_run_job(job, phase=phase)

    monkeypatch.setattr(session, "_run_job", wait_before_dispatch)
    arguments = _arguments(query={"nested": ["original"]})
    pending = asyncio.create_task(session.dispatch("read", arguments, label="lab_read"))
    await waiting.wait()
    arguments["project"] = "other"
    arguments["query"]["nested"].append("injected")
    config.tool_policies = None
    release.set()
    await pending
    assert server.calls == [("read", _arguments(query={"nested": ["original"]}))]
    assert (await session.dispatch("delete", {}, label="lab_delete"))["success"] is False
    await session.aclose()


@pytest.mark.asyncio
async def test_reconnect_preserves_grants_and_provider_mutation_does_not_reach_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeMCPServer("lab", [_mcp_tool("read")])
    replacement = FakeMCPServer("lab", [_mcp_tool("read"), _mcp_tool("delete")])
    config = _config()

    async def mutate_and_fail(_tool: str, arguments: dict[str, Any]) -> Any:
        arguments["project"] = "other"
        arguments["query"]["nested"].append("injected")
        raise httpx.ConnectError("fixture disconnect")

    monkeypatch.setattr(first, "call_tool", mutate_and_fail)
    monkeypatch.setattr(
        client, "_build_server", lambda _config: client.BuiltMcpServer(replacement, None)
    )
    monkeypatch.setattr(session_module, "_retry_delay", lambda *_args: 0)
    session = SupervisedMcpSession.adopt(first, name="lab", config=config)
    config.tool_policies = None
    result = await session.dispatch(
        "read", _arguments(query={"nested": ["original"]}), label="lab_read"
    )
    assert result["text"] == "routed:read"
    assert replacement.calls == [("read", _arguments(query={"nested": ["original"]}))]
    assert [tool.name for tool in await session.list_tools()] == ["read"]
    assert (await session.dispatch("delete", {}, label="lab_delete"))["success"] is False
    await session.aclose()


@pytest.mark.asyncio
async def test_registry_rejects_conflicting_policy_for_existing_session() -> None:
    session = SupervisedMcpSession.adopt(FakeMCPServer("lab", []), name="lab", config=_config())
    with pytest.raises(ValueError, match="match"):
        McpRegistry().add(name="lab", session=session, config=_config(tool_policies=None))
    await session.aclose()


@pytest.mark.asyncio
async def test_connections_do_not_share_mutable_grants() -> None:
    config = _config()
    first = SupervisedMcpSession.adopt(FakeMCPServer("lab", []), name="lab", config=config)
    assert config.tool_policies is not None
    config.tool_policies["read"].argument_values["project"][:] = ["second-project"]
    second = SupervisedMcpSession.adopt(FakeMCPServer("lab", []), name="lab", config=config)
    assert first.dispatch_policy.rejection("read", _arguments()) is None
    assert second.dispatch_policy.rejection("read", _arguments()) == "argument_value_not_allowed"
    assert second.dispatch_policy.rejection("read", _arguments(project="second-project")) is None
    assert (
        first.dispatch_policy.rejection("read", _arguments(project="second-project"))
        == "argument_value_not_allowed"
    )
    await first.aclose()
    await second.aclose()


@pytest.mark.parametrize("value", [None, 1, 1.0])
def test_null_and_number_choices_preserve_type_and_presence(value: Any) -> None:
    policy = McpDispatchPolicy(
        _config(
            tool_policies={
                "read": {"allowed_arguments": ["value"], "argument_values": {"value": [value]}}
            }
        )
    )
    assert policy.rejection("read", {"value": value}) is None
    assert policy.rejection("read", {}) == "required_argument_missing"
    assert policy.rejection("read", {"value": True}) == "argument_value_not_allowed"
    if value is not None:
        other = float(value) if isinstance(value, int) else int(value)
        assert policy.rejection("read", {"value": other}) == "argument_value_not_allowed"


@pytest.mark.asyncio
async def test_real_stdio_provider_only_receives_authorized_calls(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    calls = tmp_path / "calls.jsonl"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('local-policy-fixture')\n"
        "@server.tool()\n"
        "def read(project: str) -> str:\n"
        "    with Path(sys.argv[1]).open('a') as stream:\n"
        "        stream.write(json.dumps(project) + '\\n')\n"
        "    return 'authorized fixture result'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config = _config(
        transport="stdio",
        url=None,
        command=sys.executable,
        args=[str(script), str(calls)],
        tool_policies={
            "read": {"allowed_arguments": ["project"], "argument_values": {"project": ["approved"]}}
        },
    )
    registry = McpRegistry()
    registry.register(McpConnectionRequest(config=config))
    try:
        for arguments in [{"project": "other"}, {}, {"project": "approved", "extra": True}]:
            blocked = await call_mcp.on_invoke_tool(
                _ctx(registry),
                json.dumps({"connection": "lab", "tool": "read", "arguments": arguments}),
            )
            assert blocked["success"] is False
        assert not calls.exists()
        result = await call_mcp.on_invoke_tool(
            _ctx(registry),
            json.dumps({"connection": "lab", "tool": "read", "arguments": {"project": "approved"}}),
        )
        assert "authorized fixture result" in json.dumps(result)
        assert calls.read_text().splitlines() == ['"approved"']
    finally:
        await registry.close()
