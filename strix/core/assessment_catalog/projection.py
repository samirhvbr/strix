"""Read-only view of the catalog plan against the runtime receipts (informational).

Dict in, dict out, so the report layer can import it without the approved-context models. It never
changes ``completeness`` or the exit code: the version-1 obligations still decide those, and an
agent's own words never close an essential item here (ENG-06).
"""

from __future__ import annotations

from collections import Counter
from typing import Any


OBLIGATION_CLOSED = frozenset({"observed", "reconciled"})
VERDICT_CLOSED = frozenset({"compliant", "vulnerable"})


def _essential_status(item: dict[str, Any], evidence: Any) -> tuple[str, str | None]:
    if not isinstance(evidence, dict) or not isinstance(evidence.get("obligations"), dict):
        return "open", "receipts_unavailable"
    refs = set(item.get("case_refs") or [])
    rows = [r for r in evidence["obligations"].get("items", []) if r.get("case_ref") in refs]
    evaluations = [e for e in evidence.get("case_evaluations", []) if e.get("case_ref") in refs]
    if not refs or not rows or len(evaluations) < len(refs):
        return "open", "receipts_unavailable"
    unclosed = {r.get("status") for r in rows} - OBLIGATION_CLOSED
    unclosed |= {e.get("verdict") for e in evaluations} - VERDICT_CLOSED
    if unclosed:
        return "open", ",".join(sorted(str(value) for value in unclosed))
    return ("covered" if item.get("scope") == "full" else "partially_covered"), None


def project_catalog(run_record: dict[str, Any]) -> dict[str, Any] | None:
    """The catalog status of one run, or ``None`` when the run recorded no catalog plan."""
    plan = run_record.get("assessment_catalog")
    if not isinstance(plan, dict):
        return None
    base = {"version": plan.get("version"), "informational": True}
    if plan.get("status") != "ready":
        return {**base, "status": "unavailable", "reason": str(plan.get("reason", "unknown"))}
    evidence = run_record.get("evidence_ledger")
    rows: list[dict[str, Any]] = []
    for item in plan.get("items", []):
        row = {
            key: item[key]
            for key in ("id", "weight", "label", "text", "coverage_class")
            if key in item
        }
        if item.get("weight") == "essential":
            status, reason = _essential_status(item, evidence)
            row |= {"status": status, "scope": item.get("scope"), "limits": item.get("limits")}
            row["limits_pt"] = item.get("limits_pt")
            row |= {"case_refs": item.get("case_refs", [])}
            if reason:
                row["reason"] = reason
        else:
            row["status"] = "consultative"
        rows.append(row)
    states = Counter(row["status"] for row in rows)
    return {
        **base,
        "status": "ready",
        "catalog_version": plan.get("catalog_version"),
        "selection_version": plan.get("selection_version"),
        "catalog_sha256": plan.get("catalog_sha256"),
        "context_sha256": plan.get("context_sha256"),
        "totals": {
            "catalog": plan.get("catalog_items"),
            "selected": len(rows),
            "essential": sum(row["weight"] == "essential" for row in rows),
            "covered": states["covered"],
            "partially_covered": states["partially_covered"],
            "open": states["open"],
            "consultative": states["consultative"],
            "unmet_facts": len(plan.get("unmet", [])),
            "not_selected": plan.get("not_selected"),
        },
        "items": rows,
        "unmet": plan.get("unmet", []),
    }
