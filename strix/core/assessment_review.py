"""Offline, metadata-only review of context-bound runtime evidence."""

from __future__ import annotations

import re
import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from strix.core.assessment import parse_assessment_policy, read_assessment_json
from strix.core.assessment_context import parse_context
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger


if TYPE_CHECKING:
    from pathlib import Path

    from strix.core.assessment import AssessmentPolicy
    from strix.core.assessment_context import AssessmentContext


_VERDICTS = {
    "missing",
    "needs_retest",
    "invalid_evidence",
    "inconclusive",
    "compliant",
    "vulnerable",
}
_RETEST = {"not_requested", "no_vulnerable_baseline", "fixed", "not_fixed", "inconclusive"}
_ADAPTERS = {"http.private-read", "http.single-credit", "openssl.tls", "ssh-audit"}


def _ref(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else None


def _case(item: dict[str, Any], ledger: EvidenceLedger, approved: set[str]) -> dict[str, Any]:
    name = item.get("case_ref")
    if name not in approved:
        raise EvidenceError("Unapproved review case")
    verdict = item.get("verdict")
    result: dict[str, Any] = {
        "case_ref": name,
        "verdict": verdict if verdict in _VERDICTS else "invalid_evidence",
        "case_result_ref": _ref(item.get("case_result_ref")),
    }
    if result["verdict"] in {"missing", "needs_retest", "invalid_evidence"}:
        return result
    adapter = item.get("adapter")
    if adapter not in _ADAPTERS or item.get("schema_version") != 1:
        return {**result, "verdict": "invalid_evidence"}
    result.update(adapter=adapter)
    for key in ("case_version", "adapter_version"):
        value = item.get(key)
        if type(value) is not int or value < 1:
            raise EvidenceError("Invalid case version")
        result[key] = value
    for key in ("controls_valid", "legitimate_owner", "cleanup_complete"):
        if type(item.get(key)) is bool:
            result[key] = item[key]
    for key in ("failed_checks", "warning_checks"):
        if type(item.get(key)) is int and 0 <= item[key] <= 100000:
            result[key] = item[key]
    retest = item.get("retest_status")
    result["retest_status"] = retest if retest in _RETEST else "not_requested"
    result["baseline_ref"] = _ref(item.get("baseline_ref"))
    # Never forward stored narrative, reason text, target content or arbitrary fields.
    receipts = []
    for evidence in item.get("evidence", []):
        receipt = ledger.read(evidence["evidence_ref"], case_ref=name)
        receipts.append(
            {
                key: receipt[key]
                for key in (
                    "evidence_ref",
                    "sha256",
                    "case_ref",
                    "case_version",
                    "identity_ref",
                    "operation_ref",
                    "status",
                    "truncated",
                    "content",
                    "sanitization",
                )
            }
        )
    result["evidence"] = receipts
    return result


def _regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise EvidenceError("Review requires regular local records")


def load_review_scope(directory: Path) -> tuple[AssessmentPolicy, AssessmentContext]:
    """Read and validate the existing immutable scope without creating state."""
    directory = directory.absolute()
    # Reject directory aliases as well as linked files; the OS account is the trust boundary.
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise EvidenceError("Review directory must not use symbolic links")
    state = directory / ".state"
    if not stat.S_ISDIR(state.lstat().st_mode) or state.stat().st_mode & 0o077:
        raise EvidenceError("Review state must be private")
    for name in ("assessment-policy.json", "assessment-context.json"):
        _regular(state / name)
    policy_record = read_assessment_json(state / "assessment-policy.json")
    context_record = read_assessment_json(state / "assessment-context.json")
    if (
        not isinstance(policy_record, dict)
        or set(policy_record) != {"version", "scan_id", "policy"}
        or policy_record.get("version") != 1
        or policy_record.get("scan_id") != directory.name
        or not isinstance(context_record, dict)
        or set(context_record) != {"scan_id", "policy_sha256", "context"}
        or context_record.get("scan_id") != directory.name
    ):
        raise EvidenceError("Invalid review scope binding")
    policy = parse_assessment_policy(policy_record["policy"])
    context = parse_context(context_record["context"])
    if policy is None or context is None or context_record["policy_sha256"] != policy.digest:
        raise EvidenceError("Controlled assessment context required for review")
    context.validate_scope(policy)
    return policy, context


def review_assessment(directory: Path) -> dict[str, Any]:
    """Read only immutable scope plus a consistent SQLite snapshot; never dispatch."""
    directory = directory.absolute()
    policy, context = load_review_scope(directory)
    state = directory / ".state"
    with EvidenceLedger.snapshot(
        state / "evidence.db",
        scan_id=directory.name,
        assessment_id=policy.assessment_id,
        context_sha256=context.digest,
    ) as ledger:
        data = ledger.review(context)
        approved = set(context.cases)
        return {
            "version": 1,
            "source": "verified_runtime_evidence_snapshot",
            "reviewed_at": datetime.now(UTC).isoformat(),
            "scan_id": directory.name,
            "assessment_id": policy.assessment_id,
            "context_sha256": context.digest,
            "sanitization": {"version": 1, "policy": "metadata_only"},
            "attempts": data["attempts"],
            "unresolved_attempts": data["unresolved_attempts"],
            "pending_effects": data["pending_effects"],
            "authorization_denials": data["authorization_denials"],
            "network_observation_gaps": data["network_observation_gaps"],
            "obligations": data["obligations"],
            "cases": [_case(item, ledger, approved) for item in data["case_evaluations"]],
            "history": [_case(item, ledger, approved) for item in data["history"]],
            "history_truncated": data["history_truncated"],
        }
