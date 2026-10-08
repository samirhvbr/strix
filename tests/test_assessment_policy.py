"""Assessment grants must survive resume and reject mismatches before side effects."""

from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

import strix.tools.mcp as mcp_pkg
from strix.core import runner
from strix.core.assessment import (
    AssessmentPolicy,
    assessment_mcp_requests,
    bind_assessment_policy,
    parse_assessment_policy,
    read_assessment_policy,
    validate_assessment_scope,
)
from strix.interface.cli_args import parse_arguments
from strix.report.state import ReportState
from strix.runtime import session_manager
from strix.tools.mcp import McpConnectionConfig, McpConnectionRequest, SupervisedMcpSession
from strix.tools.mcp import client as mcp_client


if TYPE_CHECKING:
    from pathlib import Path


def policy(**overrides: Any) -> AssessmentPolicy:
    return AssessmentPolicy.model_validate(
        {
            "version": 1,
            "assessment_id": "lab-42",
            "authorization_ref": "approval-7",
            "operator_ref": "operator-3",
            "targets": [{"type": "web_application", "value": "https://192.0.2.10"}],
            "network_policy": {
                "destinations": [{"address": "192.0.2.10", "protocol": "tcp", "ports": [443]}]
            },
            "mcp_connections": {
                "lab": {
                    "url": "https://mcp.example.invalid/mcp",
                    "tool_policies": {
                        "read": {
                            "allowed_arguments": ["project"],
                            "argument_values": {"project": ["lab-42"]},
                        }
                    },
                }
            },
            **overrides,
        }
    )


def config(**overrides: Any) -> dict[str, Any]:
    return {
        "targets": [
            {
                "type": "web_application",
                "original": "https://192.0.2.10",
                "details": {"target_url": "https://192.0.2.10"},
            }
        ],
        "assessment_policy": policy().model_dump(),
        **overrides,
    }


def request(**overrides: Any) -> McpConnectionRequest:
    return McpConnectionRequest(
        config=McpConnectionConfig.model_validate(
            {
                "name": "lab",
                "url": "https://mcp.example.invalid/mcp",
                **overrides,
            }
        )
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": True},
        {"version": "1"},
        {"version": 2},
        {"targets": []},
        {"operator_ref": ""},
        {"authorization_ref": " "},
        {"network_policy": None},
        {"mcp_connections": None},
        {"typo": True},
        {"targets": [{"type": "repository", "value": "https://example.invalid/repo"}]},
    ],
)
def test_invalid_contract_does_not_expose_rejected_values(overrides: dict[str, Any]) -> None:
    raw = policy().model_dump() | overrides
    raw["private-secret"] = "synthetic-secret"
    with pytest.raises(ValueError, match="Invalid assessment") as caught:
        parse_assessment_policy(raw)
    assert "synthetic-secret" not in str(caught.value)
    raw.pop("private-secret")
    with pytest.raises(ValueError):
        parse_assessment_policy(raw)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mcp",
        "https://user:synthetic-secret@example.invalid/mcp",
        "https://example.invalid/mcp?token=synthetic-secret",
        "https://example.invalid/#x",
        "https://",
    ],
)
def test_manifest_rejects_credential_bearing_or_non_http_endpoints(url: str) -> None:
    raw = policy().model_dump()
    raw["mcp_connections"]["lab"]["url"] = url
    with pytest.raises(ValueError):
        parse_assessment_policy(raw)


def test_binding_restores_deep_snapshot_and_rejects_changes(tmp_path: Path) -> None:
    approved = policy()
    assert bind_assessment_policy(tmp_path, "scan", approved, resuming=False) == approved
    assert (tmp_path / "assessment-policy.json").stat().st_mode & 0o777 == 0o600
    approved.mcp_connections.clear()
    saved = bind_assessment_policy(tmp_path, "scan", None, resuming=True)
    assert saved == policy()
    for changed in [policy(assessment_id="other"), approved, policy(operator_ref="other")]:
        with pytest.raises(ValueError, match="cannot change"):
            bind_assessment_policy(tmp_path, "scan", changed, resuming=True)
    with pytest.raises(ValueError, match="cross-run"):
        bind_assessment_policy(tmp_path, "other", None, resuming=True)


@pytest.mark.parametrize("before,after", [(False, 0), (True, 1), (1, 1.0)])
def test_scalar_types_cannot_change_on_resume_or_override_connection_policy(
    tmp_path: Path,
    before: Any,
    after: Any,
) -> None:
    original = policy().model_dump()
    original["mcp_connections"]["lab"]["tool_policies"]["read"]["argument_values"] = {
        "project": [before]
    }
    approved = AssessmentPolicy.model_validate(original)
    bind_assessment_policy(tmp_path, "scan", approved, resuming=False)
    original["mcp_connections"]["lab"]["tool_policies"]["read"]["argument_values"]["project"] = [
        after
    ]
    changed = AssessmentPolicy.model_validate(original)
    with pytest.raises(ValueError, match="cannot change"):
        bind_assessment_policy(tmp_path, "scan", changed, resuming=True)
    with pytest.raises(ValueError, match="differs"):
        assessment_mcp_requests(
            approved,
            [
                request(
                    tool_policies=changed.mcp_connections["lab"].tool_policies,
                )
            ],
        )


def test_mutated_nested_model_is_revalidated() -> None:
    approved = policy()
    approved.mcp_connections["lab"].tool_policies["read"].required_arguments.append("unapproved")
    with pytest.raises(ValueError, match="Invalid assessment"):
        parse_assessment_policy(approved)


def test_large_unicode_grant_remains_resumable_within_the_size_limit(tmp_path: Path) -> None:
    raw = policy().model_dump()
    raw["mcp_connections"]["lab"]["tool_policies"]["read"]["argument_values"] = {
        "project": ["é" * 90000]
    }
    approved = parse_assessment_policy(raw)
    assert approved is not None
    bind_assessment_policy(tmp_path, "scan", approved, resuming=False)
    assert bind_assessment_policy(tmp_path, "scan", None, resuming=True) == approved


def test_legacy_resume_cannot_gain_a_manifest(tmp_path: Path) -> None:
    assert bind_assessment_policy(tmp_path, "scan", None, resuming=True) is None
    with pytest.raises(ValueError, match="Missing"):
        bind_assessment_policy(tmp_path, "scan", policy(), resuming=True)
    bind_assessment_policy(tmp_path, "scan", None, resuming=False)
    with pytest.raises(ValueError, match="cannot change"):
        bind_assessment_policy(tmp_path, "scan", policy(), resuming=False)


@pytest.mark.parametrize("payload", ["null", "{}", "[]", '{"version":1,"version":1}', "x" * 270000])
def test_invalid_saved_binding_fails_closed(tmp_path: Path, payload: str) -> None:
    (tmp_path / "assessment-policy.json").write_text(payload)
    with pytest.raises(ValueError):
        bind_assessment_policy(tmp_path, "scan", None, resuming=True)


def test_symlink_binding_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "assessment-policy.json").symlink_to(tmp_path / "absent")
    with pytest.raises(ValueError, match="symlink"):
        bind_assessment_policy(tmp_path, "scan", None, resuming=True)


def test_null_and_duplicate_input_rejected(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    for raw in ["null", '{"version":1,"version":1}']:
        path.write_text(raw)
        with pytest.raises(ValueError):
            read_assessment_policy(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"targets": []},
        {
            "targets": [
                {
                    "type": "web_application",
                    "original": "https://192.0.2.10",
                    "details": {"target_url": "https://other.invalid"},
                }
            ]
        },
        {"network_policy": {}},
        {"local_sources": [{"source_path": "/workspace/source"}]},
        {"workspace_mount": "/workspace/source"},
    ],
)
def test_scope_uses_resolved_targets_and_cannot_conflict(overrides: dict[str, Any]) -> None:
    validate_assessment_scope(policy(), config())
    with pytest.raises(ValueError):
        validate_assessment_scope(policy(), config(**overrides))


def test_mcp_grants_filter_and_snapshot_without_widening_connection_allowlist() -> None:
    source = request(allowed_tools=[])
    selected = assessment_mcp_requests(policy(), [source, request(name="unapproved")])
    assert len(selected) == 1
    assert selected[0].config.allowed_tools == []
    source.config.url = "https://other.invalid"
    assert selected[0].config.url == policy().mcp_connections["lab"].url
    assert source.config.tool_policies is None
    assert assessment_mcp_requests(policy(mcp_connections={}), [source]) == []


@pytest.mark.parametrize(
    "requests",
    [
        [],
        [request(), request()],
        [request(url="https://other.invalid")],
        [request(transport="stdio", command="echo")],
        [request(tool_policies={})],
    ],
)
def test_mcp_mismatch_is_fatal(requests: list[McpConnectionRequest]) -> None:
    with pytest.raises(ValueError):
        assessment_mcp_requests(policy(), requests)


@pytest.mark.asyncio
async def test_assessment_dispatch_only_authorized_calls_reach_provider() -> None:
    helpers = importlib.import_module("tests.test_mcp_client")
    server = helpers.FakeMCPServer("lab", [helpers._mcp_tool("read"), helpers._mcp_tool("delete")])
    approved = assessment_mcp_requests(policy(), [request()])[0]
    session = SupervisedMcpSession.adopt(server, name="lab", config=approved.config)
    for tool, args in [("delete", {}), ("read", {}), ("read", {"project": "other"})]:
        result = await session.dispatch(tool, args, label="assessment")
        assert result["error"] == "mcp_policy_denied"
    assert server.calls == []
    await session.dispatch("read", {"project": "lab-42"}, label="assessment")
    assert len(server.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,requests",
    [
        ({"targets": []}, [request()]),
        ({}, []),
        ({}, [request(url="https://other.invalid")]),
    ],
)
async def test_runner_denies_before_sandbox_or_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    requests: list[McpConnectionRequest],
) -> None:
    monkeypatch.setattr(runner, "run_dir_for", lambda _: tmp_path)
    sandbox, model = AsyncMock(), AsyncMock()
    monkeypatch.setattr(session_manager, "create_or_reuse", sandbox)
    monkeypatch.setattr(runner, "run_agent_loop", model)
    with pytest.raises(ValueError):
        await runner.run_strix_scan(
            scan_config=config(**overrides),
            scan_id="scan",
            image="unused",
            mcp_connection_requests=requests,
        )
    sandbox.assert_not_called()
    model.assert_not_called()


def test_cli_resume_restores_manifest_and_denies_missing_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "policy.json"
    path.write_text(policy().model_dump_json())
    monkeypatch.setattr(
        sys, "argv", ["strix", "-n", "-t", "https://192.0.2.10", "--assessment-policy", str(path)]
    )
    args = parse_arguments()
    assert args.assessment_policy == policy().model_dump()
    assert args.network_policy == policy().network_policy.model_dump()
    run = tmp_path / "strix_runs" / "scan"
    state = run / ".state"
    state.mkdir(parents=True)
    bind_assessment_policy(state, "scan", policy(), resuming=False)
    (state / "agents.json").write_text("{}")
    (run / "run.json").write_text(
        json.dumps(
            {
                "assessment": policy().summary(),
                "targets_info": args.targets_info,
                "network_policy": args.network_policy,
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["strix", "-n", "--resume", "scan"])
    assert parse_arguments().assessment_policy == policy().model_dump()
    (state / "assessment-policy.json").unlink()
    with pytest.raises(SystemExit):
        parse_arguments()


def test_report_only_contains_summary_and_requires_restored_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    report = ReportState("scan")
    report.set_scan_config(config())
    assert report.run_record["assessment"] == policy().summary()
    assert "mcp.example.invalid" not in json.dumps(report.run_record)
    with pytest.raises(ValueError, match="must be restored"):
        report.set_scan_config({})


@pytest.mark.asyncio
async def test_pinned_http_endpoint_blocks_redirects_and_alternate_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    params: dict[str, Any] = {}

    def capture_server(**kwargs: Any) -> object:
        params.update(kwargs["params"])
        return object()

    async def respond(req: httpx.Request) -> httpx.Response:
        sent.append(str(req.url))
        return httpx.Response(307, headers={"Location": "https://other.invalid/mcp"})

    monkeypatch.setattr(mcp_client, "MCPServerStreamableHttp", capture_server)
    monkeypatch.setattr(
        mcp_client,
        "create_mcp_http_client",
        lambda **_: httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            follow_redirects=True,
        ),
    )
    approved = assessment_mcp_requests(policy(), [request()])[0].config
    mcp_client._build_server(approved)
    async with params["httpx_client_factory"]() as client:
        response = await client.post(approved.url)
        assert response.status_code == 307
        for destination in ["https://other.invalid/mcp", "https://mcp.example.invalid/other"]:
            with pytest.raises(ValueError, match="approved endpoint"):
                await client.post(destination)
        # Even a caller explicitly enabling redirects cannot escape the request hook.
        with pytest.raises(ValueError, match="approved endpoint"):
            await client.post(approved.url, follow_redirects=True)
    assert sent == [approved.url, approved.url]


@pytest.mark.asyncio
async def test_runner_restores_omitted_assessment_and_snapshots_requests_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helpers = importlib.import_module("tests.test_runner_teardown")
    helpers._wire_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: None)
    monkeypatch.setattr(runner, "run_agent_loop", AsyncMock())
    monkeypatch.setattr(mcp_pkg.McpRegistry, "start_warmup", lambda *_, **__: None)
    bind_assessment_policy(tmp_path, "scan", policy(), resuming=False)
    source = request()
    captured: list[McpConnectionRequest] = []
    original_register = mcp_pkg.McpRegistry.register

    def register(registry: Any, approved: McpConnectionRequest) -> Any:
        captured.append(approved)
        return original_register(registry, approved)

    async def start(*_: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["network_policy"] == policy().network_policy
        assert kwargs["run_dir"] == tmp_path
        source.config.url = "https://substituted.invalid/mcp"
        return {"client": SimpleNamespace(), "session": object(), "caido_client": None}

    monkeypatch.setattr(mcp_pkg.McpRegistry, "register", register)
    monkeypatch.setattr(session_manager, "create_or_reuse", start)
    scan_config = config(assessment_policy=None)
    await runner.run_strix_scan(
        scan_config=scan_config, scan_id="scan", image="unused", mcp_connection_requests=[source]
    )
    assert scan_config["assessment_policy"] == policy().model_dump()
    assert captured[0].config.url == "https://mcp.example.invalid/mcp"
    assert captured[0].config.pin_http_endpoint is True
    assert captured[0].config.tool_policies == policy().mcp_connections["lab"].tool_policies


def test_cli_rejects_target_and_network_mismatch_before_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "policy.json"
    path.write_text(policy().model_dump_json())
    for target in ["https://other.invalid", "git@example.invalid:repo.git", "postman://fixture"]:
        monkeypatch.setattr(
            sys, "argv", ["strix", "-n", "-t", target, "--assessment-policy", str(path)]
        )
        with pytest.raises(SystemExit):
            parse_arguments()
    network = tmp_path / "network.json"
    network.write_text("{}")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "strix",
            "-n",
            "-t",
            "https://192.0.2.10",
            "--assessment-policy",
            str(path),
            "--network-policy",
            str(network),
        ],
    )
    with pytest.raises(SystemExit):
        parse_arguments()
