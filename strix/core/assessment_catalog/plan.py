"""Catalog plan v2: which catalog items an approved assessment context makes applicable.

The plan is recorded beside the version-1 obligation plan (``evidence_ledger.obligations``), never
in place of it. Version 1 still decides the exit code; this plan adds traceability from each
essential item to the adapter that can close it, so a report can say "5.12.1 partially covered
(read only)" instead of only "case X observed".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

from strix.core.assessment_catalog.catalog import ADAPTER_FACTS, CatalogError, load_catalog


if TYPE_CHECKING:
    from strix.core.assessment_context import AssessmentContext


logger = logging.getLogger(__name__)

PLAN_VERSION = 2
HTTP_METHODS = frozenset({"GET", "HEAD", "POST"})


def _adapter(case: Any) -> str | None:
    for spec in (case.authorization, case.business, case.transport):
        if spec is not None:
            return str(spec.adapter)
    return None


def context_facts(context: AssessmentContext) -> dict[str, list[str]]:
    """Facts the approved context establishes, each with the cases that establish it."""
    facts: dict[str, list[str]] = {}
    for name, case in sorted(context.cases.items()):
        adapter = _adapter(case)
        if adapter in ADAPTER_FACTS:
            facts.setdefault(ADAPTER_FACTS[adapter], []).append(name)
    if any(operation.method in HTTP_METHODS for operation in context.operations.values()):
        facts["operation.http"] = []
    return facts


def build_catalog_plan(context: AssessmentContext) -> dict[str, Any]:
    """The applicable curated items, the curated ones whose facts are missing, and a count."""
    catalog = load_catalog()
    facts = context_facts(context)
    selected: list[dict[str, Any]] = []
    unmet: list[dict[str, Any]] = []
    for item in catalog.items.values():
        if item.label is None:
            continue
        missing = [fact for fact in item.applies_when if fact not in facts]
        if missing:
            unmet.append({"id": item.id, "weight": item.weight, "missing_facts": missing})
            continue
        row: dict[str, Any] = {
            "id": item.id,
            "family": item.family_id,
            "weight": item.weight,
            "label": item.label,
            "text": item.text,
            "coverage_class": item.coverage_class,
        }
        if item.weight == "essential":
            row |= {
                "adapter": item.adapter,
                "scope": item.scope,
                "limits": item.limits,
                "limits_pt": item.limits_pt,
                "case_refs": facts[item.applies_when[0]],
            }
        else:
            row["facts"] = list(item.applies_when)
        selected.append(row)
    return {
        "version": PLAN_VERSION,
        "status": "ready",
        "catalog_version": catalog.catalog_version,
        "selection_version": catalog.selection_version,
        "catalog_sha256": catalog.sha256,
        "context_sha256": context.digest,
        "catalog_items": len(catalog.items),
        "families": catalog.families,
        "essential_limit": catalog.essential_limit,
        "items": selected,
        "unmet": unmet,
        "not_selected": len(catalog.items) - len(selected) - len(unmet),
    }


def catalog_plan_for(context: AssessmentContext) -> dict[str, Any]:
    """Like :func:`build_catalog_plan`, but a broken catalog never stops a paid scan.

    The catalog is informational, so a failure is recorded as ``unavailable`` in the run record
    and logged, and the version-1 obligations carry on as before.
    """
    try:
        return build_catalog_plan(context)
    except (CatalogError, OSError, yaml.YAMLError) as error:
        logger.warning("Assessment catalog unavailable: %s", error, exc_info=True)
        return {"version": PLAN_VERSION, "status": "unavailable", "reason": type(error).__name__}
