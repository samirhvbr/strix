"""Top-level Strix scan runner."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import RunConfig
from agents.sandbox import SandboxRunConfig
from openai import RateLimitError

from strix.agents.factory import build_strix_agent, make_child_factory
from strix.agents.prompt import render_scope_prompt, render_system_prompt
from strix.config import codex, load_settings
from strix.config.models import (
    StrixProvider,
    configure_sdk_api_route,
    configure_sdk_model_defaults,
    model_supports_images,
    supports_strict_tool_schemas,
    uses_chat_completions_tool_schema,
)
from strix.config.settings import DEFAULT_MAX_TURNS
from strix.core.agents import AgentCoordinator, BudgetPolicy
from strix.core.assessment import (
    assessment_mcp_requests,
    bind_assessment_policy,
    validate_assessment_scope,
)
from strix.core.assessment_context import FileCredentials, bind_context
from strix.core.authorization_audit import AuthorizationAudit
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.execution import (
    respawn_subagents,
    run_agent_loop,
)
from strix.core.execution import (
    spawn_child_agent as start_child_agent,
)
from strix.core.hooks import BudgetExceededError, ReportUsageHooks, recomputed_budget_flags
from strix.core.identity_executor import IdentityExecutor
from strix.core.inputs import (
    build_root_task,
    build_scan_targets,
    build_scope_context,
    make_model_settings,
)
from strix.core.paths import run_dir_for, runtime_state_dir
from strix.core.run_lease import exclusive_scan
from strix.core.sessions import open_agent_session
from strix.core.targets import is_whitebox_scan
from strix.core.test_catalog import TestCatalog, TestUnit
from strix.core.test_pause import TestPauseController, validate_interval
from strix.core.web_authorization import WebAuthorization
from strix.llm import request_log
from strix.report.state import get_global_report_state
from strix.runtime import session_manager
from strix.runtime.network_policy import bind_network_policy
from strix.telemetry import set_scan_phase
from strix.telemetry.logging import set_scan_id, setup_scan_logging
from strix.telemetry.test_ledger import TestLedger
from strix.tools.finish.tool import finish_scan
from strix.tools.output_store import (
    WORKSPACE_SPILL_DIR,
    configure_spill_writer,
)


if TYPE_CHECKING:
    from agents.memory import SQLiteSession
    from agents.result import RunResultBase
    from agents.tool import Tool

    from strix.runtime.status import StatusSink
    from strix.tools.mcp import (
        McpConnectionRequest,
        McpRegistry,
    )


logger = logging.getLogger(__name__)

_MCP_PROMPT_WARMUP_TIMEOUT_SECONDS = 30.0

StreamEventSink = Callable[[str, Any], None]

# Receives the run's MCP connection roster as a list of non-secret status dicts
# ({"name", "provider", "tool_count", "dead", "state"}), once when the connections
# are registered and again on lifecycle transitions. An interface
# can persist it, render it, or forward it on as connection status. Kept as a
# snapshot of the whole roster (not a per-
# connection delta) so every call carries a consistent, current picture.
McpStatusSink = Callable[[list[dict[str, Any]]], None]


def _mcp_roster_payload(registry: McpRegistry) -> list[dict[str, Any]]:
    """The run's MCP roster as non-secret lifecycle status dicts."""
    return [
        {
            "name": status.name,
            "provider": status.provider,
            "tool_count": status.tool_count,
            "dead": status.dead,
            "state": status.state,
        }
        for status in registry.statuses()
    ]


def _mcp_prompt_roster(registry: McpRegistry) -> list[dict[str, Any]]:
    """The MCP roster rendered in agent prompts, with only verified counts."""
    return [
        {
            "name": summary.name,
            "purpose": summary.purpose,
            "tool_count": summary.tool_count if summary.state == "catalog_ready" else None,
            "state": summary.state,
        }
        for summary in registry.summaries()
    ]


def _record_mcp_connections(connection_names: list[str]) -> None:
    """Record which MCP servers this run configured, for the interfaces.

    A server's tools are offered to the model under a name built from the
    connection name and the tool's own name, which cannot be split back apart, so
    the TUI and the run viewer need the names to match a tool call against before
    they can show which server it went out to. Kept on the run record because the
    viewer reads a finished run from disk.
    """
    report_state = get_global_report_state()
    if report_state is None:
        return
    report_state.record_mcp_connections(connection_names)


def _note_exit_reason(reason: str) -> None:
    """Record why the scan stopped so the end-of-scan beacon reports it."""
    report_state = get_global_report_state()
    if report_state is not None and report_state.scan_ended_exit_reason is None:
        report_state.scan_ended_exit_reason = reason


def _persist_mcp_status(roster: list[dict[str, Any]]) -> None:
    """Write the run's non-secret MCP connection status roster to run.json.

    The viewer rebuilds its display by re-reading the run's files from disk, so
    it cannot see the in-memory ``mcp_status_sink`` the TUI consumes. Persisting
    the same non-secret roster (name / provider / tool_count / dead) gives the
    viewer a source it can poll. Runs regardless of whether an interface sink is
    attached, so the standalone / non-TUI CLI path records health too.
    """
    report_state = get_global_report_state()
    if report_state is None:
        return
    report_state.record_mcp_connection_status(roster)


def _merge_root_prompt_context(
    scope_context: dict[str, Any],
    extra_system_prompt_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not extra_system_prompt_context:
        return scope_context
    reserved_keys = scope_context.keys() & extra_system_prompt_context.keys()
    if reserved_keys:
        raise ValueError(
            "extra_system_prompt_context cannot override built-in scope keys: "
            f"{sorted(reserved_keys)}",
        )
    return {**scope_context, **extra_system_prompt_context}


def _compose_root_instructions_override(
    root_instructions_override: str | None,
    *,
    skills: list[str],
    scan_mode: str,
    is_whitebox: bool,
    is_diff_scoped: bool,
    interactive: bool,
    system_prompt_context: dict[str, Any],
    supports_images: bool,
) -> str | None:
    if root_instructions_override is None:
        return None

    base_instructions = render_system_prompt(
        skills=skills,
        scan_mode=scan_mode,
        is_whitebox=is_whitebox,
        is_root=True,
        is_diff_scoped=is_diff_scoped,
        interactive=interactive,
        system_prompt_context=system_prompt_context,
        include_scope=False,
        supports_images=supports_images,
    )
    return (
        f"{base_instructions}\n\n"
        "<root_scan_instructions_override>\n"
        "The following root scan instructions describe the task configuration.\n\n"
        f"{root_instructions_override}\n"
        "</root_scan_instructions_override>\n\n"
        f"{render_scope_prompt(system_prompt_context)}"
    )


@exclusive_scan(lambda scan_id: runtime_state_dir(run_dir_for(scan_id)))
async def run_strix_scan(
    *,
    scan_config: dict[str, Any],
    scan_id: str | None = None,
    image: str,
    local_sources: list[dict[str, Any]] | None = None,
    extra_files: list[dict[str, Any]] | None = None,
    coordinator: AgentCoordinator | None = None,
    interactive: bool = False,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_budget_usd: float | None = None,
    budget_policy: BudgetPolicy = "stop",
    pause_every_n_tests: int | None = None,
    model: str | None = None,
    cleanup_on_exit: bool = True,
    event_sink: StreamEventSink | None = None,
    root_instructions_override: str | None = None,
    extra_system_prompt_context: dict[str, Any] | None = None,
    status_sink: StatusSink | None = None,
    mcp_connection_requests: list[McpConnectionRequest] | None = None,
    mcp_status_sink: McpStatusSink | None = None,
    root_finish_tool: Tool = finish_scan,
) -> RunResultBase | None:
    """Run or resume one Strix scan against a sandbox.

    ``root_instructions_override`` adds root scan instructions to the rendered
    root prompt without replacing the system-verified scope block.
    ``extra_files`` entries (``{"workspace_path", "content"}``) are placed into
    the sandbox workspace at session bring-up; see
    :func:`strix.runtime.session_manager.create_or_reuse`.
    ``extra_system_prompt_context`` is merged into the root agent's scan
    context before prompt rendering. Child agents keep the standard scan prompt
    and context.
    ``budget_policy`` decides what happens when the LLM spend reaches
    ``max_budget_usd``: ``"stop"`` warns the agents as the limit approaches and
    ends the scan at it; ``"pause"`` tells the agents nothing and parks every
    agent before its next LLM call until the caller resumes the scan through
    ``coordinator.resume_budget()`` (optionally with a higher limit) or cancels
    it. ``coordinator.pause_budget()`` parks a running scan the same way.
    ``pause_every_n_tests`` requests an independent durable pause after N unique
    terminal catalog tests. An omitted interval retains any saved interval.
    ``resume_budget(reason="test")`` releases only that reason, without spending
    reset or budget extension. Operator pauses work under either budget policy.
    ``mcp_connection_requests`` supplies the run's MCP connections from any
    source: when given, the engine connects those requests; when ``None`` (the
    command-line default) it reads ``~/.strix/mcp-servers.json`` itself. Either
    way the engine does the connecting, so the caller passes inert configs plus
    metadata and never live sessions.
    ``root_finish_tool`` is the tool the root agent ends the run with.
    """

    def report(phase: str) -> None:
        if status_sink is not None:
            status_sink(phase)

    if scan_id is None:
        scan_id = f"scan-{uuid.uuid4().hex[:8]}"

    run_dir = run_dir_for(scan_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state_dir = runtime_state_dir(run_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    assessment_policy = bind_assessment_policy(
        state_dir,
        scan_id,
        scan_config.get("assessment_policy"),
        resuming=(state_dir / "agents.json").exists(),
    )
    authorization_audit: AuthorizationAudit | None = None
    if assessment_policy is not None:
        audit_report = get_global_report_state()

        def publish_authorization_audit(summary: dict[str, Any]) -> None:
            if audit_report is not None and audit_report.run_id == scan_id:
                audit_report.run_record["authorization_audit"] = summary
                audit_report.save_run_data()

        authorization_audit = AuthorizationAudit(
            state_dir / "authorization_audit.db",
            scan_id=scan_id,
            assessment_id=assessment_policy.assessment_id,
            policy_sha256=assessment_policy.digest,
            authorization_ref=assessment_policy.authorization_ref,
            operator_ref=assessment_policy.operator_ref,
            approved_tools={
                name: frozenset(grant.tool_policies)
                for name, grant in assessment_policy.mcp_connections.items()
            },
            on_change=publish_authorization_audit,
            resuming=(state_dir / "agents.json").exists(),
        )
        try:
            validate_assessment_scope(
                assessment_policy,
                {
                    **scan_config,
                    "local_sources": local_sources or scan_config.get("local_sources"),
                },
            )
        except ValueError:
            authorization_audit.record_denial("startup", "scope_rejected")
            authorization_audit.close()
            raise
        scan_config["network_policy"] = assessment_policy.network_policy.model_dump()
        scan_config["assessment_policy"] = assessment_policy.model_dump()
        from strix.tools.mcp import McpConnectionRequest, load_user_mcp_configs

        requests = mcp_connection_requests
        if requests is None:
            requests = [McpConnectionRequest(config=item) for item in load_user_mcp_configs()]
        # Validate and snapshot before sandbox startup and outside the legacy
        # best-effort MCP registration block. No warm-up can precede this gate.
        try:
            mcp_connection_requests = assessment_mcp_requests(assessment_policy, requests)
        except ValueError:
            authorization_audit.record_denial("startup", "mcp_configuration_rejected")
            authorization_audit.close()
            raise
    try:
        network_policy = bind_network_policy(
            state_dir,
            scan_config.get("network_policy"),
            resuming=(state_dir / "agents.json").exists(),
        )
    except ValueError:
        if authorization_audit is not None:
            authorization_audit.record_denial("startup", "network_binding_rejected")
            authorization_audit.close()
        raise
    scan_config["network_policy"] = (
        network_policy.model_dump() if network_policy is not None else None
    )
    identity_context = bind_context(
        state_dir,
        scan_id,
        assessment_policy,
        scan_config.get("assessment_context"),
        resuming=(state_dir / "agents.json").exists(),
    )
    context_report = get_global_report_state()
    saved_context = (
        context_report.run_record.get("assessment_context")
        if context_report is not None and context_report.run_id == scan_id
        else None
    )
    if saved_context is not None and (
        identity_context is None or saved_context.get("context_sha256") != identity_context.digest
    ):
        raise ValueError("Assessment context report and binding disagree")
    credential_path = scan_config.get("identity_credentials")
    if credential_path and Path(credential_path).resolve().is_relative_to(run_dir.resolve()):
        raise ValueError("Identity credential files must stay outside run artifacts")
    authorization_check: Callable[[], None] | None = None
    if assessment_policy is not None and assessment_policy.version == 2:
        if identity_context is None or not scan_config.get("web_authorization"):
            raise ValueError(
                "Controlled assessment requires WEB authorization and identity context"
            )
        handoff = Path(scan_config["web_authorization"])
        if handoff.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("WEB authorization must stay outside run artifacts")
        web_authorization = WebAuthorization(handoff)
        if interactive or budget_policy != "stop":
            raise ValueError("Controlled assessment requires headless execution and a fixed budget")
        authorization_check = partial(
            web_authorization.check,
            scan_id,
            assessment_policy,
            identity_context,
            max_budget_usd or 0,
        )
        authorization_check()
    teardown_logging = setup_scan_logging(run_dir)
    set_scan_id(scan_id)

    agents_path = state_dir / "agents.json"
    agents_db = state_dir / "agents.db"
    test_catalog_path = state_dir / "test_catalog.json"
    is_resume = agents_path.exists()

    logger.info(
        "%s Strix scan %s (image=%s, max_turns=%d, interactive=%s, run_dir=%s)",
        "Resuming" if is_resume else "Starting",
        scan_id,
        image,
        max_turns,
        interactive,
        run_dir,
    )

    settings = load_settings()
    configure_sdk_model_defaults(settings)
    resolved_model = (model or settings.llm.model or "").strip()
    if not resolved_model:
        raise RuntimeError(
            "No LLM model configured. Set STRIX_LLM env or pass model= to run_strix_scan().",
        )
    if resolved_model != (settings.llm.model or "").strip() and not codex.subscription_model(
        resolved_model
    ):
        configure_sdk_api_route(resolved_model, settings)
    logger.info("LLM model resolved: %s", resolved_model)
    chat_completions_tools = uses_chat_completions_tool_schema(resolved_model, settings)
    strict_tool_schemas = supports_strict_tool_schemas(resolved_model)
    if not strict_tool_schemas:
        logger.info("Sending non-strict tool schemas: %s caps strict tools", resolved_model)
    supports_images = model_supports_images(resolved_model)
    if not supports_images:
        logger.info("Leaving out image tools: %s does not accept images", resolved_model)

    validate_interval(pause_every_n_tests)
    if budget_policy not in ("stop", "pause"):
        raise ValueError(f"unknown budget_policy: {budget_policy!r}")
    if coordinator is None:
        coordinator = AgentCoordinator()
    coordinator.set_snapshot_path(agents_path)
    coordinator.set_budget_policy(budget_policy)
    coordinator.root_finish_tool = root_finish_tool.name

    # Spec 01 (.continue/pentest): every create_agent call is catalogued as a
    # "test" (D6 -- emergent decomposition, catalogued identity). A sibling of
    # the coordinator, never a part of it: wired only through load()/the
    # status-change callback below, so a run with no interest in test
    # cataloguing pays nothing beyond one small JSON file.
    test_catalog = TestCatalog()
    test_catalog.set_snapshot_path(test_catalog_path)
    test_catalog.load()  # no-op when test_catalog.json does not exist yet (first run)
    coordinator.set_status_change_callback(test_catalog.mark_status)

    from strix.tools.coverage.tools import hydrate_coverage_from_disk
    from strix.tools.notes.tools import hydrate_notes_from_disk
    from strix.tools.threat_model.tools import hydrate_threat_models_from_disk
    from strix.tools.todo.tools import hydrate_todos_from_disk

    hydrate_todos_from_disk(state_dir)
    hydrate_notes_from_disk(state_dir)
    hydrate_coverage_from_disk(state_dir)
    hydrate_threat_models_from_disk(state_dir)

    root_id: str | None = None
    if is_resume:
        try:
            snap = json.loads(agents_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot resume scan {scan_id}: agents.json is unreadable: {exc}",
            ) from exc
        if not agents_db.exists():
            raise RuntimeError(
                f"Cannot resume scan {scan_id}: missing SDK session database at {agents_db}",
            )
        await coordinator.restore(snap)
        report_state = get_global_report_state()
        if report_state is not None:
            budget_stopped, reserve_stopped = recomputed_budget_flags(
                report_state.get_total_llm_cost(),
                max_budget_usd,
                interactive=interactive,
                budget_policy=budget_policy,
            )
            # Under the pause policy the hooks re-park at the first call if the
            # spend is still at the limit, so a restored pause flag would only
            # hold agents back after the limit was raised.
            await coordinator.reset_budget_stops(
                budget_stopped=budget_stopped,
                reserve_stopped=reserve_stopped,
                budget_paused=(
                    interactive and budget_policy != "pause" and coordinator.budget_paused
                ),
            )
        for aid, parent in coordinator.parent_of.items():
            if parent is None:
                root_id = aid
                break
        if root_id is None:
            raise RuntimeError(
                f"Cannot resume scan {scan_id}: agents.json has no root agent (parent=None)",
            )
        logger.info(
            "Resume: restored coordinator with %d agent(s); root=%s",
            len(coordinator.statuses),
            root_id,
        )
    else:
        root_id = uuid.uuid4().hex[:8]

    test_pause = TestPauseController(
        coordinator, state_dir / "test_pause.json", pause_every_n_tests=pause_every_n_tests
    )
    test_catalog.set_change_callback(test_pause.observe)
    report_state = get_global_report_state()
    if report_state is not None and test_pause.interval is not None:
        report_state.run_record["pause_every_n_tests"] = test_pause.interval
        report_state.save_run_data()

    logger.info("Bringing up sandbox session for scan %s", scan_id)
    set_scan_phase("sandbox_init")
    bundle = await session_manager.create_or_reuse(
        scan_id,
        image=image,
        local_sources=local_sources or [],
        extra_files=extra_files,
        status_sink=status_sink,
        authorized_targets=build_scope_context(scan_config).get("authorized_targets", []),
        network_policy=network_policy,
        run_dir=run_dir,
    )
    report("Waiting for the first model response")
    logger.info("Sandbox ready for scan %s", scan_id)
    set_scan_phase("agent_setup")

    sandbox_session = bundle["session"]

    async def _spill_to_workspace(output_id: str, text: str) -> str | None:
        """Write an oversized tool result into the sandbox; return its path or None."""
        path = f"{WORKSPACE_SPILL_DIR}/{output_id}.txt"
        try:
            await sandbox_session.write(Path(path), io.BytesIO(text.encode("utf-8")))
        except Exception:
            logger.exception("failed to spill tool output to sandbox workspace")
            return None
        return path

    configure_spill_writer(_spill_to_workspace)

    sessions_to_close: list[SQLiteSession] = []
    mcp_registry: McpRegistry | None = None
    test_ledger: TestLedger | None = None
    identity_executor: IdentityExecutor | None = None

    try:
        if identity_context is not None and assessment_policy is not None:
            if state_dir.is_symlink():
                raise EvidenceError(  # noqa: TRY301 -- Refuse before tightening state permissions.
                    "Assessment state directory must not be a symlink"
                )
            state_dir.chmod(0o700)
            identity_report = get_global_report_state()

            def publish_evidence(summary: dict[str, Any]) -> None:
                if identity_report is not None and identity_report.run_id == scan_id:
                    identity_report.run_record["evidence_ledger"] = summary
                    identity_report.run_record["assessment_context"] = {
                        "project_ref": identity_context.project_ref,
                        "environment_ref": identity_context.environment_ref,
                        "context_sha256": identity_context.digest,
                    }
                    identity_report.save_run_data()

            ledger = EvidenceLedger(
                state_dir / "evidence.db",
                scan_id=scan_id,
                assessment_id=assessment_policy.assessment_id,
                context_sha256=identity_context.digest,
                owns_agent=lambda agent_ref: agent_ref in coordinator.statuses,
                on_change=publish_evidence,
                resuming=is_resume,
            )
            identity_executor = IdentityExecutor(
                identity_context,
                FileCredentials(
                    Path(credential_path) if credential_path else None,
                    assessment_policy.assessment_id,
                ),
                ledger,
                authorize=authorization_check,
            )

            def publish_case_result(result: dict[str, Any]) -> None:
                if identity_report is None or result["verdict"] != "vulnerable":
                    return
                business = result["adapter"] == "http.single-credit"
                transport = result["adapter"] in {"openssl.tls", "ssh-audit"}
                title = (
                    f"Single-credit invariant violated ({result['case_ref']})"
                    if business
                    else f"Cross-tenant private resource access ({result['case_ref']})"
                )
                if transport:
                    title = f"Transport inspection failed ({result['case_ref']})"
                if any(
                    report.get("title") == title for report in identity_report.vulnerability_reports
                ):
                    return
                identity_report.add_vulnerability_report(
                    title=title,
                    severity="high",
                    cwe=(
                        "CWE-295"
                        if result["adapter"] == "openssl.tls"
                        else "CWE-326"
                        if transport
                        else "CWE-841"
                        if business
                        else "CWE-639"
                    ),
                    confidence="high",
                    description=(
                        "The approved transport inspection failed its versioned checks. "
                        "See the private runtime evidence and recorded adapter version."
                        if transport
                        else "Concurrent requests applied the approved single-use credit "
                        "more than once. "
                        "Persistent balance and settled effects confirm the invariant violation."
                        if business
                        else "A second authenticated tenant received the approved private resource "
                        "and data. Both identity controls and legitimate owner access passed."
                    ),
                    remediation_steps=(
                        "Correct certificate trust or failed SSH algorithms and repeat "
                        "the approved inspection."
                        if transport
                        else "Enforce single-use redemption atomically and retest "
                        "with a fresh fixture."
                        if business
                        else "Enforce resource ownership and retest with valid identities."
                    ),
                    assessment_evidence=result["evidence"],
                )

            identity_executor.on_case_result = publish_case_result
            if authorization_check is not None:
                try:
                    counters = await asyncio.to_thread(
                        bundle["client"].network_guard.denied_packets
                    )
                except Exception:  # noqa: BLE001 -- Persist a gap without exposing transport errors.
                    ledger.record_network_snapshot(None)
                    raise RuntimeError(
                        "Controlled assessment cannot observe network denials"
                    ) from None
                ledger.record_network_snapshot(counters)
        if assessment_policy is not None:
            report_state = get_global_report_state()
            if report_state is not None:
                report_state.run_record["assessment"] = assessment_policy.summary()
                report_state.save_run_data()
        if network_policy is not None:
            report_state = get_global_report_state()
            if report_state is not None:
                guard = bundle["client"].network_guard
                report_state.run_record["network_policy"] = network_policy.model_dump()
                report_state.run_record["network_enforcement"] = {
                    "kind": "docker_namespace_firewall",
                    "policy_sha256": network_policy.digest,
                    "guard_image_id": guard.image_id,
                }
                report_state.save_run_data()
        test_ledger = TestLedger(
            state_dir / "test_telemetry.db",
            scan_id=scan_id,
            catalog=test_catalog,
            owns_agent=lambda agent_id: agent_id in coordinator.statuses,
            subscription=codex.auth_mode(resolved_model) == "subscription",
            resuming=is_resume,
        )

        def record_test_change(unit: TestUnit) -> None:
            test_pause.observe(unit)
            test_ledger.sync_test(unit)

        test_catalog.set_change_callback(record_test_change)

        def record_test_status(agent_id: str, status: str) -> None:
            test_catalog.mark_status(agent_id, status)
            test_ledger.record_status(agent_id, status)

        coordinator.set_status_change_callback(record_test_status)
        request_log.register_sink(test_ledger.record_llm_event)
        targets = scan_config.get("targets") or []
        scan_mode = str(scan_config.get("scan_mode") or "deep")
        is_whitebox = is_whitebox_scan(targets)
        diff_scope = scan_config.get("diff_scope")
        is_diff_scoped = bool(isinstance(diff_scope, dict) and diff_scope.get("active"))
        skills = list(scan_config.get("skills") or [])
        root_task = build_root_task(scan_config)
        model_settings = make_model_settings(
            settings.llm.reasoning_effort,
            model_name=resolved_model,
            force_required_tool_choice=settings.llm.force_required_tool_choice,
            request_timeout=settings.llm.timeout,
            prompt_cache=settings.llm.prompt_cache,
            extra_headers=settings.llm.extra_headers,
        )
        run_config = RunConfig(
            model=resolved_model,
            model_provider=StrixProvider(),
            model_settings=model_settings,
            sandbox=SandboxRunConfig(client=bundle["client"], session=bundle["session"]),
            trace_include_sensitive_data=False,
            # A hallucinated tool name is a recoverable model mistake, not a scan-ending
            # error: hand it back as a tool result so the agent can correct itself.
            tool_not_found_behavior="return_error_to_model",
        )
        hooks = ReportUsageHooks(
            model=resolved_model,
            max_budget_usd=max_budget_usd,
            max_turns=max_turns,
            interactive=interactive,
            budget_policy=budget_policy,
        )
        coordinator.set_budget_limit_setter(hooks.set_max_budget_usd)
        if interactive and budget_policy != "pause":
            coordinator.set_budget_extender(hooks.extend_budget)

        scope_context = build_scope_context(scan_config)
        if identity_executor is not None:
            scope_context["assessment_context"] = identity_executor.catalog()
            scope_context["controlled_assessment"] = (
                assessment_policy is not None and assessment_policy.version == 2
            )

        # Attach the run's MCP connections and hold their live sessions in a
        # per-run registry. The connections are source-agnostic: a caller
        # (the SaaS/pro product) can supply them as mcp_connection_requests, and
        # when it does not the command-line path reads them from
        # ~/.strix/mcp-servers.json here. Either way one shared engine routine
        # does the connecting and populating. Nothing is registered as an agent
        # tool: every agent reaches these connections on demand through the
        # list_mcps / describe_mcp / call_mcp tools, guided by brief static prompt
        # guidance when any connection exists. Fail-open: a missing config, or a
        # server that will not connect, must never break a run.
        from strix.tools.mcp import (
            McpConnectionRequest,
            McpRegistry,
            load_user_mcp_configs,
        )

        mcp_registry = McpRegistry(authorization_audit=authorization_audit)
        try:
            if mcp_connection_requests is None:
                # Command-line default: read the user's file and wrap each config
                # in a bare request (no provider or transform), so this path is
                # exactly the old behavior.
                mcp_requests = [
                    McpConnectionRequest(config=config) for config in load_user_mcp_configs()
                ]
            else:
                mcp_requests = mcp_connection_requests
            if mcp_requests:
                for request in mcp_requests:
                    mcp_registry.register(request)
                _record_mcp_connections(mcp_registry.names())
                report(
                    f"MCP: configured {len(mcp_registry)} connection(s); "
                    "connecting and listing tools"
                )
                scope_context["mcp_available"] = True
                scope_context["mcp_connections"] = _mcp_prompt_roster(mcp_registry)

                def _emit_mcp_status() -> None:
                    scope_context["mcp_connections"] = _mcp_prompt_roster(mcp_registry)
                    roster = _mcp_roster_payload(mcp_registry)
                    _persist_mcp_status(roster)
                    if mcp_status_sink is not None:
                        try:
                            mcp_status_sink(roster)
                        except Exception:
                            logger.exception("MCP status sink failed")

                mcp_registry.set_status_sink(_emit_mcp_status)
                _emit_mcp_status()
                warmup_task = mcp_registry.start_warmup(max_concurrency=6)
                await asyncio.wait(
                    {warmup_task},
                    timeout=_MCP_PROMPT_WARMUP_TIMEOUT_SECONDS,
                )
        except Exception:
            if assessment_policy is not None:
                raise
            logger.exception("Failed to configure user MCP servers; continuing without them")

        root_context = _merge_root_prompt_context(scope_context, extra_system_prompt_context)
        root_instructions = _compose_root_instructions_override(
            root_instructions_override,
            skills=skills,
            scan_mode=scan_mode,
            is_whitebox=is_whitebox,
            is_diff_scoped=is_diff_scoped,
            interactive=interactive,
            system_prompt_context=root_context,
            supports_images=supports_images,
        )

        root_agent = build_strix_agent(
            name="Root Agent",
            skills=skills,
            is_root=True,
            scan_mode=scan_mode,
            is_whitebox=is_whitebox,
            is_diff_scoped=is_diff_scoped,
            interactive=interactive,
            chat_completions_tools=chat_completions_tools,
            strict_tool_schemas=strict_tool_schemas,
            system_prompt_context=root_context,
            instructions_override=root_instructions,
            supports_images=supports_images,
            finish_tool=root_finish_tool,
        )

        if not is_resume:
            await coordinator.register(
                root_id,
                "Root Agent",
                parent_id=None,
                task=root_task,
                skills=skills,
            )

        child_agent_builder = make_child_factory(
            scan_mode=scan_mode,
            is_whitebox=is_whitebox,
            is_diff_scoped=is_diff_scoped,
            interactive=interactive,
            chat_completions_tools=chat_completions_tools,
            strict_tool_schemas=strict_tool_schemas,
            system_prompt_context=scope_context,
            supports_images=supports_images,
        )

        async def spawn_child_agent(**kwargs: Any) -> dict[str, Any]:
            return await start_child_agent(
                coordinator=coordinator,
                test_catalog=test_catalog,
                factory=child_agent_builder,
                agents_db_path=agents_db,
                sessions_to_close=sessions_to_close,
                run_config=run_config,
                max_turns=max_turns,
                interactive=interactive,
                event_sink=event_sink,
                hooks=hooks,
                **kwargs,
            )

        context: dict[str, Any] = {
            "coordinator": coordinator,
            "test_catalog": test_catalog,
            "sandbox_session": bundle["session"],
            "caido_client": bundle["caido_client"],
            "mcp_registry": mcp_registry,
            "identity_executor": identity_executor,
            "authorize_assessment": authorization_check,
            "agent_id": root_id,
            "parent_id": None,
            "interactive": interactive,
            "spawn_child_agent": spawn_child_agent,
            "scan_targets": build_scan_targets(scan_config),
            "max_context_images": settings.runtime.max_context_images,
            "supports_images": supports_images,
        }

        root_session = open_agent_session(root_id, agents_db)
        sessions_to_close.append(root_session)
        await coordinator.attach_runtime(root_id, session=root_session)

        if is_resume:
            await respawn_subagents(
                coordinator=coordinator,
                factory=child_agent_builder,
                agents_db_path=agents_db,
                sessions_to_close=sessions_to_close,
                run_config=run_config,
                max_turns=max_turns,
                interactive=interactive,
                parent_ctx=context,
                root_id=root_id,
                event_sink=event_sink,
                hooks=hooks,
            )

        initial_input: Any = [] if is_resume else root_task

        # Resume + new ``--instruction``: SDK replay drives root from
        # agents.db with ``initial_input=[]``, so a brand-new instruction
        # passed on the resume CLI would otherwise be silently ignored.
        # Inject it as a fresh user message in root's SDK session; the
        # next run cycle will replay it with the rest of the session.
        resume_instruction = str(scan_config.get("resume_instruction") or "").strip()
        if is_resume and resume_instruction:
            await coordinator.send(
                root_id,
                {
                    "from": "user",
                    "type": "instruction",
                    "priority": "high",
                    "content": resume_instruction,
                },
            )
            logger.info(
                "Resume: injected new instruction into root SDK session (len=%d)",
                len(resume_instruction),
            )

        async with coordinator._lock:
            root_status = coordinator.statuses.get(root_id)

        set_scan_phase("agent_loop")
        result = await run_agent_loop(
            agent=root_agent,
            initial_input=initial_input,
            run_config=run_config,
            context=context,
            max_turns=max_turns,
            coordinator=coordinator,
            agent_id=root_id,
            interactive=interactive,
            session=root_session,
            start_parked=bool(interactive and is_resume and root_status != "running"),
            event_sink=event_sink,
            hooks=hooks,
        )
        if not interactive and result is not None:
            final = getattr(result, "final_output", None)
            # Lifecycle tools mark the root completed.
            async with coordinator._lock:
                root_completed = coordinator.statuses.get(root_id) == "completed"
            if not root_completed:
                logger.error(
                    "Scan %s ended without calling finish_scan. The agent "
                    "emitted a text-only turn instead of a lifecycle tool call, "
                    "so no executive report was written. Final output (first "
                    "300 chars): %r",
                    scan_id,
                    str(final)[:300],
                )
        return result  # noqa: TRY300
    except BudgetExceededError as exc:
        logger.info("Scan %s stopped: %s", scan_id, exc)
        _note_exit_reason("budget_exceeded")
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.set_status(root_id, "stopped")
        return None
    except RateLimitError as exc:
        logger.warning(
            "Scan %s stopped: persistent rate limit from the LLM provider (%s). "
            "Resume with 'strix --resume %s' once the limit clears.",
            scan_id,
            exc,
            scan_id,
        )
        _note_exit_reason("rate_limited")
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.set_status(root_id, "stopped")
        return None
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Scan %s interrupted by the user", scan_id)
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.set_status(root_id, "running")
        raise
    except Exception as exc:
        # A LiteLLM-routed provider can surface an exhausted usage window as a
        # non-RateLimitError type (OpenAI's RateLimitError is handled above).
        # Both retry layers already stop retrying it (see
        # codex.is_usage_limit_error), so route it to the same resumable stop
        # instead of the generic failure path.
        if codex.is_usage_limit_error(exc):
            logger.warning(
                "Scan %s stopped: persistent usage limit from the LLM provider (%s). "
                "Resume with 'strix --resume %s' once the limit clears.",
                scan_id,
                exc,
                scan_id,
            )
            _note_exit_reason("rate_limited")
            if root_id is not None:
                with contextlib.suppress(Exception):
                    await coordinator.set_status(root_id, "stopped")
            return None
        logger.exception("Strix scan %s failed", scan_id)
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.set_status(root_id, "failed")
        raise
    except BaseException:
        logger.exception("Strix scan %s failed", scan_id)
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.set_status(root_id, "failed")
        raise
    finally:
        configure_spill_writer(None)
        # Settle descendants before closing sessions: on a clean finish a child
        # can still be mid-turn, and closing its session underneath it crashes it.
        if root_id is not None:
            with contextlib.suppress(Exception):
                await coordinator.cancel_descendants(root_id)
        if test_ledger is not None:
            request_log.unregister_sink(test_ledger.record_llm_event)
            test_catalog.set_change_callback(None)
            coordinator.set_status_change_callback(test_catalog.mark_status)
            test_ledger.close()
        for s in sessions_to_close:
            with contextlib.suppress(Exception):
                s.close()
        if mcp_registry is not None:
            with contextlib.suppress(Exception):
                await mcp_registry.close()
        if authorization_audit is not None:
            authorization_audit.close()
        if identity_executor is not None:
            if authorization_check is not None:
                try:
                    counters = await asyncio.to_thread(
                        bundle["client"].network_guard.denied_packets
                    )
                except Exception:  # noqa: BLE001 -- Cleanup retains an explicit observation gap.
                    counters = None
                with contextlib.suppress(Exception):
                    identity_executor.ledger.record_network_snapshot(counters)
            with contextlib.suppress(Exception):
                await identity_executor.close()
        with contextlib.suppress(Exception):
            await coordinator._maybe_snapshot()
        if cleanup_on_exit:
            logger.info("Tearing down sandbox session for scan %s", scan_id)
            await session_manager.cleanup(scan_id)
        logger.info("Strix scan %s done", scan_id)
        teardown_logging()
