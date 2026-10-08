"""Versioned private-resource comparison; credentials and bodies never leave the host."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from strix.core.evidence_ledger import EvidenceError


if TYPE_CHECKING:
    from strix.core.identity_executor import IdentityExecutor


def _body(receipt: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(receipt["content"].get("body", ""))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


async def run_private_read_case(
    executor: IdentityExecutor, *, agent_ref: str, case_ref: str, baseline_ref: str | None
) -> dict[str, Any]:
    case = executor.context.cases[case_ref]
    spec = case.authorization
    if spec is None:
        raise EvidenceError("This approved case has no executable adapter")
    baseline = executor.ledger.read_case_result(baseline_ref, case_ref) if baseline_ref else None
    receipts: list[dict[str, Any]] = []
    observations: dict[tuple[str, str], dict[str, Any]] = {}
    controls_valid = True
    revisions: dict[str, set[int | None]] = {}
    reason, verdict = "identity_controls_unavailable", "inconclusive"
    # Controls before and after the comparison prevent logout/expiry from passing a retest.
    sequence = [
        (spec.owner_ref, spec.control_operation),
        (spec.other_ref, spec.control_operation),
        (spec.owner_ref, spec.resource_operation),
        (spec.other_ref, spec.resource_operation),
        (spec.owner_ref, spec.control_operation),
        (spec.other_ref, spec.control_operation),
    ]
    for identity, operation in sequence:
        receipt = await executor._request(
            agent_ref=agent_ref, case_ref=case_ref, identity_ref=identity, operation_ref=operation
        )
        receipts.append({key: value for key, value in receipt.items() if key != "content"})
        try:
            private = executor.ledger.read_private(
                receipt["evidence_ref"], case_ref=case_ref, require_complete=True
            )
        except EvidenceError:
            controls_valid = False
            continue
        observations[(identity, operation)] = private
        revisions.setdefault(identity, set()).add(private["credential_revision"])
        if operation == spec.control_operation:
            actor = executor.context.identities[identity]
            data = _body(private)
            controls_valid &= (
                private["content"].get("status_code") == 200
                and data.get("authenticated") is True
                and data.get("identity_ref") == identity
                and data.get("tenant_ref") == actor.tenant_ref
            )
    controls_valid &= all(len(values) == 1 and None not in values for values in revisions.values())
    owner = observations.get((spec.owner_ref, spec.resource_operation), {})
    other = observations.get((spec.other_ref, spec.resource_operation), {})
    owner_body, other_body = _body(owner) if owner else {}, _body(other) if other else {}
    expected = {
        "resource_ref": spec.resource_ref,
        "owner_ref": spec.owner_ref,
        "tenant_ref": executor.context.identities[spec.owner_ref].tenant_ref,
        "private": True,
    }
    legitimate = (
        owner.get("content", {}).get("status_code") == 200
        and all(owner_body.get(key) == value for key, value in expected.items())
        and bool(owner_body.get("data"))
    )
    if controls_valid and legitimate:
        status = other.get("content", {}).get("status_code")
        if status in {403, 404}:
            reason, verdict = "private_resource_denied", "compliant"
        elif (
            status == 200
            and all(other_body.get(key) == value for key, value in expected.items())
            and other_body.get("data") == owner_body["data"]
        ):
            reason, verdict = "cross_tenant_private_resource", "vulnerable"
        else:
            reason = "resource_comparison_inconclusive"
    elif controls_valid:
        reason = "legitimate_owner_control_failed"
    retest = "not_requested"
    if baseline is not None:
        retest = (
            "no_vulnerable_baseline"
            if baseline.get("verdict") != "vulnerable"
            else "fixed"
            if verdict == "compliant"
            else "not_fixed"
            if verdict == "vulnerable"
            else "inconclusive"
        )
    result = executor.ledger.record_case_result(
        case_ref,
        {
            "schema_version": 1,
            "adapter": spec.adapter,
            "adapter_version": spec.version,
            "case_version": case.version,
            "verdict": verdict,
            "reason": reason,
            "controls_valid": controls_valid,
            "legitimate_owner": legitimate,
            "baseline_ref": baseline_ref,
            "retest_status": retest,
            "evidence": receipts,
        },
    )
    if executor.on_case_result is not None:
        executor.on_case_result(result)
    return result
