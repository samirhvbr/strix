"""Bounded single-credit fixture case with durable effects and read-only recovery."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from strix.core.evidence_ledger import EvidenceError


if TYPE_CHECKING:
    from strix.core.identity_executor import IdentityExecutor


async def run_single_credit_case(
    executor: IdentityExecutor, *, agent_ref: str, case_ref: str
) -> dict[str, Any]:
    case = executor.context.cases[case_ref]
    spec = case.business
    if spec is None:
        raise EvidenceError("This case has no approved business adapter")
    identities = list(dict.fromkeys(case.identities))
    evidence: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    async def state(identity: str) -> dict[str, Any] | None:
        receipt = await executor._request(
            agent_ref=agent_ref,
            case_ref=case_ref,
            identity_ref=identity,
            operation_ref=spec.state_operation,
        )
        try:
            private = executor.ledger.read_private(
                receipt["evidence_ref"], case_ref=case_ref, require_complete=True
            )
            data = json.loads(private["content"].get("body", ""))
            if (
                private["content"].get("status_code") != 200
                or not isinstance(data, dict)
                or data.get("resource_ref") != spec.resource_ref
                or type(data.get("balance")) is not int
                or type(data.get("applications")) is not int
                or data["applications"] < 0
                or not isinstance(data.get("effects"), dict)
            ):
                return None
            evidence.append({key: value for key, value in receipt.items() if key != "content"})
            executor.ledger.reconcile_effects(
                case_ref, spec.state_operation, receipt["evidence_ref"]
            )
        except (EvidenceError, ValueError, TypeError):
            return None
        else:
            return data

    async def effect(identity: str, operation: str) -> None:
        receipt = await executor._request(
            agent_ref=agent_ref,
            case_ref=case_ref,
            identity_ref=identity,
            operation_ref=operation,
            allow_effects=True,
        )
        actions.append({key: value for key, value in receipt.items() if key != "content"})

    def finish(verdict: str, reason: str, *, cleanup_complete: bool) -> dict[str, Any]:
        result = executor.ledger.record_case_result(
            case_ref,
            {
                "schema_version": 1,
                "adapter": spec.adapter,
                "adapter_version": spec.version,
                "case_version": case.version,
                "verdict": verdict,
                "reason": reason,
                "cleanup_complete": cleanup_complete,
                "evidence": evidence,
                "actions": actions,
                "effects": executor.ledger.effect_history(case_ref),
            },
        )
        if executor.on_case_result is not None:
            executor.on_case_result(result)
        return result

    checkpoint = executor.ledger.business_checkpoint(case_ref)
    before = await state(identities[0])
    control = await state(identities[1])
    if checkpoint is not None:
        # A durable reservation might precede an interrupted dispatch. Never infer that
        # absence of a response or effect entry means it is safe to repeat a mutation.
        return finish(
            "inconclusive", "reconciled_without_replaying_effects", cleanup_complete=False
        )
    if before is None or control is None or before != control or before["applications"] != 0:
        return finish("inconclusive", "initial_state_unavailable", cleanup_complete=False)
    claimed, _ = executor.ledger.claim_business_run(case_ref, evidence[0]["evidence_ref"])
    if not claimed:
        return finish("inconclusive", "case_already_reserved", cleanup_complete=False)
    await asyncio.gather(*(effect(actor, spec.redeem_operation) for actor in identities))
    after = await state(identities[0])
    after_control = await state(identities[1])
    history = executor.ledger.effect_history(case_ref)
    writes = [item for item in history if item["operation_ref"] == spec.redeem_operation]
    applied = sum(item["outcome"] == "applied" for item in writes)
    settled = len(writes) == 2 and all(item["outcome"] != "pending" for item in writes)
    verdict, reason = "inconclusive", "persistent_effects_unavailable"
    if (
        after is not None
        and after_control == after
        and settled
        and applied >= 1
        and after["applications"] == applied
        and after["balance"] == before["balance"] + applied * spec.credit
    ):
        verdict = "vulnerable" if applied > 1 else "compliant"
        reason = "single_credit_duplicated" if applied > 1 else "single_credit_preserved"
    cleanup_complete = False
    if after is not None:
        await asyncio.gather(*(effect(actor, spec.cleanup_operation) for actor in identities))
        restored = await state(identities[0])
        restored_control = await state(identities[1])
        cleanup_complete = bool(
            restored is not None
            and restored_control == restored
            and restored["applications"] == 0
            and restored["balance"] == before["balance"]
            and all(
                item["outcome"] != "pending" for item in executor.ledger.effect_history(case_ref)
            )
        )
    return finish(verdict, reason, cleanup_complete=cleanup_complete)
