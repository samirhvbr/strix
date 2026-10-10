"""Directed private-read retest, under the original run's ownership and authorization."""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

from strix.core.assessment_context import FileCredentials
from strix.core.assessment_review import load_review_scope
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.core.run_lease import controller_lease
from strix.core.web_authorization import WebAuthorization, WebAuthorizationError


if TYPE_CHECKING:
    from pathlib import Path

    from strix.core.assessment_context import AssessmentContext


def _baseline(
    ledger: EvidenceLedger, context: AssessmentContext, case_ref: str, baseline_ref: str
) -> None:
    case = context.cases.get(case_ref)
    spec = case.authorization if case is not None else None
    if spec is None or case is None or case.business is not None or case.transport is not None:
        raise EvidenceError("Directed retest requires an approved private-read case")
    result = ledger.read_case_result(baseline_ref, case_ref)
    expected = [
        (spec.owner_ref, spec.control_operation),
        (spec.other_ref, spec.control_operation),
        (spec.owner_ref, spec.resource_operation),
        (spec.other_ref, spec.resource_operation),
        (spec.owner_ref, spec.control_operation),
        (spec.other_ref, spec.control_operation),
    ]
    receipts = result.get("evidence", [])
    if (
        result.get("case_result_ref") != baseline_ref
        or result.get("case_ref") != case_ref
        or result.get("schema_version") != 1
        or result.get("adapter") != "http.private-read"
        or result.get("adapter_version") != spec.version
        or result.get("case_version") != case.version
        or result.get("verdict") not in {"vulnerable", "compliant", "inconclusive"}
        or len(receipts) != len(expected)
    ):
        raise EvidenceError("Baseline does not match the approved private-read case")
    for receipt, (identity, operation) in zip(receipts, expected, strict=True):
        actual = ledger.read_private(receipt["evidence_ref"], case_ref=case_ref)
        if (
            actual["identity_ref"] != identity
            or actual["operation_ref"] != operation
            or actual["case_version"] != case.version
        ):
            raise EvidenceError("Baseline evidence differs from the approved case sequence")


async def retest_assessment(
    directory: Path,
    *,
    case_ref: str,
    baseline_ref: str,
    authorization_path: Path,
    credentials_path: Path,
) -> dict[str, Any]:
    """Append one deterministic result; never resume models, reset usage or replay effects."""
    if os.name != "posix" or not re.fullmatch(r"[0-9a-f]{32}", baseline_ref):
        raise EvidenceError("Directed retest requires POSIX and a valid baseline reference")
    directory = directory.absolute()
    # Validate before lease acquisition, which may create a lock in an existing run.
    load_review_scope(directory)
    for path in (authorization_path, credentials_path):
        if path.resolve().is_relative_to(directory.resolve()):
            raise EvidenceError("Private handoffs must remain outside the run directory")
    state = directory / ".state"
    with controller_lease(state):
        policy, context = load_review_scope(directory)
        if policy.version != 2 or context.version != 2:
            raise EvidenceError("Directed retest requires the controlled approval profile")
        binding = {
            "scan_id": directory.name,
            "assessment_id": policy.assessment_id,
            "context_sha256": context.digest,
        }
        with EvidenceLedger.snapshot(state / "evidence.db", **binding) as snapshot:
            summary = snapshot.review(context)
            if summary["unresolved_attempts"] or summary["pending_effects"]:
                raise EvidenceError("Uncertain attempts or effects require recovery before retest")
            _baseline(snapshot, context, case_ref, baseline_ref)

        authorization = WebAuthorization(authorization_path)
        approved_policy, approved_context, budget = await asyncio.to_thread(
            authorization.fetch, directory.name
        )
        if approved_policy.digest != policy.digest or approved_context.digest != context.digest:
            raise WebAuthorizationError("Retest differs from the original WEB authorization")

        # No model or billable provider runs here. The grant ceiling and all recorded
        # usage are retained; there is no fresh budget or run.json rewrite.
        def authorize() -> None:
            authorization.check(directory.name, policy, context, budget)

        ledger = EvidenceLedger(
            state / "evidence.db",
            scan_id=directory.name,
            assessment_id=policy.assessment_id,
            context_sha256=context.digest,
            owns_agent=lambda agent: agent == "operator-retest",
            resuming=True,
        )
        executor = IdentityExecutor(
            context, FileCredentials(credentials_path, policy.assessment_id), ledger, authorize
        )
        try:
            _baseline(ledger, context, case_ref, baseline_ref)
            async with asyncio.timeout(240):
                result = await executor.run_case(
                    agent_ref="operator-retest", case_ref=case_ref, baseline_ref=baseline_ref
                )
            return {
                "version": 1,
                "source": "directed_private_read_retest",
                "scan_id": directory.name,
                **{
                    key: result[key]
                    for key in (
                        "case_ref",
                        "case_result_ref",
                        "baseline_ref",
                        "verdict",
                        "retest_status",
                    )
                },
            }
        finally:
            await executor.close()
