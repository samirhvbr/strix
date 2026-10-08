"""Agents select approved references; they never supply credentials or proof blobs."""

from __future__ import annotations

from typing import Any

from agents import RunContextWrapper, function_tool

from strix.core.identity_executor import IdentityExecutor


def _executor(ctx: RunContextWrapper[dict[str, Any]]) -> IdentityExecutor:
    executor = ctx.context.get("identity_executor")
    if not isinstance(executor, IdentityExecutor):
        raise TypeError("This run has no approved assessment identity context")
    return executor


@function_tool
async def list_assessment_cases(ctx: RunContextWrapper[dict[str, Any]]) -> dict[str, Any]:
    """List host-approved cases, identity/tenant/role references and fixed HTTP operations.

    Credentials are never returned. Use execute_assessment_operation to preserve
    isolated sessions and collect evidence before making a security claim.
    """
    return _executor(ctx).catalog()


@function_tool
async def execute_assessment_operation(
    ctx: RunContextWrapper[dict[str, Any]], case_ref: str, identity_ref: str, operation_ref: str
) -> dict[str, Any]:
    """Execute one approved operation with the selected identity and capture durable evidence.

    Blocked, uncertain and truncated observations do not establish a completed
    test. Each invocation is a new attempt; no remote operation is automatically
    retried. Only references from list_assessment_cases are accepted.
    Target response bodies remain private; returned content is sanitized metadata.

    Args:
        case_ref: Approved case identifier from list_assessment_cases.
        identity_ref: Approved identity for this case, with its own session.
        operation_ref: Approved fixed HTTP operation for this case.
    """
    return await _executor(ctx).request(
        agent_ref=ctx.context["agent_id"],
        case_ref=case_ref,
        identity_ref=identity_ref,
        operation_ref=operation_ref,
    )


@function_tool
async def read_assessment_evidence(
    ctx: RunContextWrapper[dict[str, Any]], evidence_ref: str, case_ref: str
) -> dict[str, Any]:
    """Read a durable runtime observation after verifying its digest and case ownership.

    Evidence from another assessment, an invented reference or an altered blob
    is rejected. A genuine observation still needs a justified security interpretation.

    Args:
        evidence_ref: Runtime artifact identifier returned by execute_assessment_operation.
        case_ref: Case identifier that must own the referenced artifact.
    """
    return _executor(ctx).ledger.read(evidence_ref, case_ref=case_ref)
