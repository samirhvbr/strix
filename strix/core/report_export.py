"""Opt-in report content export for a verified stopped assessment.

This is broader than accounting metadata. Consumers must obtain explicit consent
before transferring it. Redaction is defense in depth, not a confidentiality proof.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from strix.core.history_export import _read, stopped_assessment


if TYPE_CHECKING:
    from pathlib import Path


NARRATIVE_FIELDS = (
    "title",
    "description",
    "impact",
    "counterevidence",
    "confidence_rationale",
    "severity_change_conditions",
    "technical_analysis",
    "poc_description",
    "remediation_steps",
    "fix_verification",
    "assumptions",
)
TECHNICAL_FIELDS = (
    "id",
    "severity",
    "timestamp",
    "target",
    "endpoint",
    "method",
    "cve",
    "cwe",
    "cvss",
    "cvss_vector",
    "confidence",
    "fix_effort",
    "evidence",
    "poc_script_code",
    "finding_class",
    "assessment_evidence",
    "dependency_metadata",
    "code_locations",
    "update_history",
)
SECTIONS = ("executive_summary", "methodology", "technical_analysis", "recommendations")
_SECRETS = re.compile(
    r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+"
    r"|(\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[\"']?)[^\s\"',;]+"
)
_PRIVATE_PATH = re.compile(r"/(?:home|Users|tmp|workspace)/[^\s`\"'<>]+")
_CATALOG_TOTALS = (
    "catalog",
    "selected",
    "essential",
    "covered",
    "partially_covered",
    "open",
    "consultative",
    "unmet_facts",
    "not_selected",
)
_CATALOG_ITEM_TEXT = ("label", "text", "coverage_class", "limits", "limits_pt", "reason")
_CATALOG_ITEM_ID = re.compile(r"^\d{1,2}(?:\.\d{1,3}){1,3}$")
_CATALOG_STATUSES = frozenset({"covered", "partially_covered", "open", "consultative"})
_CATALOG_MAX_ITEMS = 64


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        raise ValueError("Report structure exceeds limit")
    if isinstance(value, str):
        if len(value) > 100_000:
            raise ValueError("Report field exceeds limit")
        text = _SECRETS.sub(lambda match: (match[1] or match[2]) + "[REDACTED_SECRET]", value)
        return _PRIVATE_PATH.sub("[REDACTED_LOCAL_PATH]", text)
    if isinstance(value, list):
        if len(value) > 512:
            raise ValueError("Report list exceeds limit")
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 128 or any(not isinstance(key, str) or len(key) > 128 for key in value):
            raise ValueError("Report object exceeds limit")
        return {key: redact(item, depth + 1) for key, item in value.items()}
    if value is None or isinstance(value, bool | int | float):
        return value
    raise ValueError("Unsupported report value")


def _catalog_item(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    if not isinstance(row.get("id"), str) or not _CATALOG_ITEM_ID.match(row["id"]):
        return None
    if row.get("weight") not in {"essential", "consultative"}:
        return None
    if row.get("status") not in _CATALOG_STATUSES or row.get("scope") not in {
        "full",
        "partial",
        None,
    }:
        return None
    item: dict[str, Any] = {key: row[key] for key in ("id", "weight", "status")}
    item["scope"] = row.get("scope")
    for key in _CATALOG_ITEM_TEXT:
        value = row.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 600):
            return None
        item[key] = value
    return item if item["label"] else None


def _catalog_section(value: Any) -> dict[str, Any] | None:
    """The informational catalog projection, narrowed to what a reader needs.

    It carries no case references, digests or agent text, only the plan's own labels and statuses.
    A malformed projection is left out: informational data must never block the report.
    """
    if not isinstance(value, dict) or not isinstance(value.get("version"), int):
        return None
    if value.get("status") == "unavailable":
        reason = value.get("reason")
        if not isinstance(reason, str) or len(reason) > 200:
            return None
        return {"version": value["version"], "status": "unavailable", "reason": reason}
    totals = value.get("totals")
    rows = value.get("items")
    if (
        value.get("status") != "ready"
        or not isinstance(totals, dict)
        or not isinstance(rows, list)
        or len(rows) > _CATALOG_MAX_ITEMS
        or not all(isinstance(value.get(k), int) for k in ("catalog_version", "selection_version"))
        or not all(
            isinstance(totals.get(key), int) and not isinstance(totals.get(key), bool)
            for key in _CATALOG_TOTALS
        )
    ):
        return None
    items = [_catalog_item(row) for row in rows]
    if any(item is None for item in items):
        return None
    return {
        "version": value["version"],
        "status": "ready",
        "informational": True,
        "catalog_version": value["catalog_version"],
        "selection_version": value["selection_version"],
        "totals": {key: totals[key] for key in _CATALOG_TOTALS},
        "items": items,
    }


def export_report(run_directory: Path) -> dict[str, Any]:
    with stopped_assessment(run_directory) as (directory, policy, context):
        record = json.loads(_read(directory / "run.json", 2 * 1024 * 1024))
        if record.get("run_id") != directory.name:
            raise ValueError("Report belongs to another run")
        findings_path = directory / "vulnerabilities.json"
        findings = (
            json.loads(_read(findings_path, 2 * 1024 * 1024)) if findings_path.exists() else None
        )
        if findings is not None and (not isinstance(findings, list) or len(findings) > 128):
            raise ValueError("Invalid findings")
        results = record.get("scan_results") or {}
        if not isinstance(results, dict):
            raise TypeError("Invalid narrative")
        coverage_path = directory / "coverage.json"
        coverage = (
            json.loads(_read(coverage_path, 2 * 1024 * 1024)) if coverage_path.exists() else None
        )
        if coverage is not None and (
            not isinstance(coverage, dict)
            or coverage.get("schema_version") != 1
            or coverage.get("run_id") != directory.name
        ):
            raise ValueError("Invalid coverage")
        allowed = set(NARRATIVE_FIELDS + TECHNICAL_FIELDS)
        selected = []
        omitted = set()
        for finding in findings or []:
            if not isinstance(finding, dict):
                raise TypeError("Invalid finding")
            omitted.update(set(finding) - allowed)
            selected.append({key: value for key, value in finding.items() if key in allowed})
        if len(omitted) > 64:
            raise ValueError("Unsupported finding fields")
        result: dict[str, Any] = {
            "version": 1,
            "source": "native_report_v1",
            "assessment_id": policy.assessment_id,
            "context_sha256": context.digest,
            "scan_id": directory.name,
            "scan_results": {key: results.get(key) for key in SECTIONS},
            "findings": selected if findings is not None else None,
            "coverage": (
                {
                    key: coverage.get(key)
                    for key in ("entries", "gaps", "completeness", "machine_observed")
                }
                if coverage
                else None
            ),
            "omitted_fields": sorted(omitted),
        }
        catalog = _catalog_section(coverage.get("catalog")) if coverage else None
        if catalog is not None:
            result["catalog"] = catalog
        # Machine context must not transfer the private agent graph or original task.
        if result["coverage"]:
            machine = result["coverage"].get("machine_observed") or {}
            result["coverage"]["machine_observed"] = {
                key: machine.get(key)
                for key in ("source", "assessment_obligations", "case_evaluations")
            }
        clean = redact(result)
        if len(json.dumps(clean, allow_nan=False).encode()) > 2 * 1024 * 1024:
            raise ValueError("Report exceeds limit")
        return cast("dict[str, Any]", clean)
