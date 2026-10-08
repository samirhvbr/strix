from __future__ import annotations

import asyncio
import sqlite3
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agents import ModelSettings

import strix.tools.notes.tools as notes_tools
import strix.tools.todo.tools as todo_tools
from strix.core import runner
from strix.core.agents import AgentCoordinator
from strix.llm import request_log
from strix.runtime import session_manager
from strix.runtime.network_policy import NetworkPolicy, bind_network_policy
from strix.telemetry.test_ledger import TestLedger


def _wire_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(runner, "run_dir_for", lambda _scan_id: tmp_path)
    monkeypatch.setattr(runner, "runtime_state_dir", lambda _run_dir: tmp_path)
    monkeypatch.setattr(runner, "setup_scan_logging", lambda _run_dir: lambda: None)
    monkeypatch.setattr(runner, "set_scan_id", lambda _scan_id: None)

    settings = _settings()
    monkeypatch.setattr(runner, "load_settings", lambda: settings)
    monkeypatch.setattr(runner, "configure_sdk_model_defaults", lambda _s: None)
    monkeypatch.setattr(runner, "uses_chat_completions_tool_schema", lambda _m, _s: False)
    monkeypatch.setattr(todo_tools, "hydrate_todos_from_disk", lambda _d: None)
    monkeypatch.setattr(notes_tools, "hydrate_notes_from_disk", lambda _d: None)

    async def _create_or_reuse(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"client": object(), "session": object(), "caido_client": None}

    async def _cleanup(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(session_manager, "create_or_reuse", _create_or_reuse)
    monkeypatch.setattr(session_manager, "cleanup", _cleanup)
    monkeypatch.setattr(runner, "build_root_task", lambda _c: "task")
    monkeypatch.setattr(runner, "build_scope_context", lambda _c: {"authorized_targets": []})
    monkeypatch.setattr(runner, "make_model_settings", lambda *_a, **_k: ModelSettings())
    monkeypatch.setattr(runner, "build_strix_agent", lambda **_k: object())
    monkeypatch.setattr(runner, "make_child_factory", lambda **_k: lambda **_kk: object())
    monkeypatch.setattr(runner, "open_agent_session", lambda _root_id, _db: object())


def _settings() -> Any:
    return types.SimpleNamespace(
        llm=types.SimpleNamespace(
            model="openai/gpt-4o",
            reasoning_effort="high",
            force_required_tool_choice=False,
            timeout=300,
            prompt_cache=True,
            extra_headers=None,
        ),
        runtime=types.SimpleNamespace(max_context_images=3),
    )


@pytest.mark.asyncio
async def test_network_policy_change_fails_before_sandbox_or_agent_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _wire_runner(monkeypatch, tmp_path)
    bind_network_policy(tmp_path, NetworkPolicy(), resuming=False)
    sandbox = AsyncMock()
    agent = AsyncMock()
    monkeypatch.setattr(session_manager, "create_or_reuse", sandbox)
    monkeypatch.setattr(runner, "run_agent_loop", agent)
    with pytest.raises(ValueError, match="cannot change"):
        await runner.run_strix_scan(
            scan_config={
                "targets": [],
                "network_policy": {
                    "destinations": [{"address": "192.0.2.10", "protocol": "tcp", "ports": [443]}],
                },
            },
            scan_id="guarded",
            image="unused",
        )
    sandbox.assert_not_called()
    agent.assert_not_called()


@pytest.mark.asyncio
async def test_runner_restores_omitted_policy_and_passes_protected_run_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _wire_runner(monkeypatch, tmp_path)
    saved = NetworkPolicy()
    bind_network_policy(tmp_path, saved, resuming=False)
    sandbox = AsyncMock(
        return_value={
            "client": types.SimpleNamespace(
                network_guard=types.SimpleNamespace(image_id="sha256:fixture")
            ),
            "session": object(),
            "caido_client": None,
        }
    )
    monkeypatch.setattr(session_manager, "create_or_reuse", sandbox)
    monkeypatch.setattr(runner, "get_global_report_state", lambda: None)
    monkeypatch.setattr(runner, "run_agent_loop", AsyncMock())
    await runner.run_strix_scan(scan_config={"targets": []}, scan_id="guarded", image="unused")
    assert sandbox.call_args.kwargs["network_policy"] == saved
    assert sandbox.call_args.kwargs["run_dir"] == tmp_path


@pytest.mark.parametrize("ending", ["success", "failure", "cancelled"])
@pytest.mark.asyncio
async def test_telemetry_records_root_and_cancelled_children_before_detaching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    ending: str,
) -> None:
    _wire_runner(monkeypatch, tmp_path)
    coordinator = AgentCoordinator()
    started = asyncio.Event()

    def emit(agent_id: str) -> None:
        now = datetime.now(UTC)
        request_log.emit(
            request_log.LlmRequestEvent(
                call_id=agent_id,
                agent_id=agent_id,
                route="openai",
                provider="openai",
                model="test-model",
                api_host=None,
                streaming=False,
                outcome="success",
                status_code=200,
                provider_request_id=None,
                response_id=None,
                error_type=None,
                error_message=None,
                started_at=now,
                finished_at=now,
                duration_ms=1,
                cost_usd=0.02,
                input_tokens=1,
                output_tokens=2,
                total_tokens=3,
            )
        )

    async def run(**kwargs: Any) -> None:
        root = kwargs["agent_id"]
        emit(root)
        await coordinator.register("child", "Child", parent_id=root)
        kwargs["context"]["test_catalog"].register(agent_id="child", name="Child")

        async def child() -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                emit("child")

        task = asyncio.create_task(child())
        await coordinator.attach_runtime("child", task=task)
        await started.wait()
        if ending == "failure":
            raise RuntimeError("simulated agent failure")
        if ending == "cancelled":
            raise asyncio.CancelledError

    monkeypatch.setattr(runner, "run_agent_loop", run)
    invocation = runner.run_strix_scan(
        scan_config={"targets": []},
        scan_id="scan-test",
        image="img",
        coordinator=coordinator,
    )
    if ending == "success":
        await invocation
    else:
        with pytest.raises(RuntimeError if ending == "failure" else asyncio.CancelledError):
            await invocation
    with sqlite3.connect(tmp_path / "test_telemetry.db") as db:
        assert db.execute("SELECT COUNT(*) FROM test_events").fetchone()[0] == 2
        assert db.execute("SELECT SUM(cost_usd) FROM test_events").fetchone()[0] == 0.04
        assert db.execute("SELECT ingestion_status FROM ledger_metadata").fetchone()[0] == "closed"
    assert not any(
        isinstance(getattr(sink, "__self__", None), TestLedger) for sink in request_log._sinks
    )


@pytest.mark.asyncio
async def test_a_live_child_is_settled_before_sessions_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _wire_runner(monkeypatch, tmp_path)
    coordinator = AgentCoordinator()
    child_started = asyncio.Event()
    child_task: dict[str, asyncio.Task[None]] = {}

    async def _root_finishes(**kwargs: Any) -> None:
        root_id = kwargs["agent_id"]

        async def _child_mid_turn() -> None:
            child_started.set()
            await asyncio.sleep(3600)

        await coordinator.register("child", "Child", parent_id=root_id)
        task = asyncio.create_task(_child_mid_turn())
        child_task["t"] = task
        await coordinator.attach_runtime("child", task=task)
        await child_started.wait()

    monkeypatch.setattr(runner, "run_agent_loop", _root_finishes)

    await runner.run_strix_scan(
        scan_config={"targets": [], "scan_mode": "deep"},
        scan_id="scan-test",
        image="img",
        coordinator=coordinator,
    )

    task = child_task["t"]
    assert task.done(), "the child task was left running past scan teardown"
    assert task.cancelled(), "the child was not cancelled cleanly on a finish"


@pytest.mark.parametrize(("root_status", "logged"), [("completed", False), ("stopped", True)])
@pytest.mark.asyncio
async def test_missing_finish_is_logged_only_when_root_did_not_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
    root_status: str,
    logged: bool,
) -> None:
    _wire_runner(monkeypatch, tmp_path)
    coordinator = AgentCoordinator()

    async def _root_ends_with_text(**kwargs: Any) -> Any:
        await coordinator.set_status(kwargs["agent_id"], root_status)
        return types.SimpleNamespace(final_output="The review is complete.")

    monkeypatch.setattr(runner, "run_agent_loop", _root_ends_with_text)

    await runner.run_strix_scan(
        scan_config={"targets": [], "scan_mode": "deep"},
        scan_id="scan-test",
        image="img",
        coordinator=coordinator,
    )

    assert ("ended without calling finish_scan" in caplog.text) is logged
