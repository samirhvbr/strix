"""Composes the scan's final result into one explicit, versioned contract.

(.continue/pentest/PENTEST-11-DOCUMENTACAO-PRODUTO-CONSOLIDADA.md §7.5,
§9.2.1 -- ENG-01/ENG-02.) The three signals this composes from already
exist, scattered: ``coverage.json``'s ``completeness`` (itself already
derived from ``run_record["status"]`` and the agent graph, by
``strix.report.coverage._completeness``) carries the operational-state
dimension; ``strix.interface.main.findings_fail_build`` already computes
the findings-policy dimension. What was missing was composing the two
into one artifact and letting it -- not just the findings check alone --
decide the headless exit code.

Four states from §7.5, in the precedence order it specifies: fatal
error, findings-policy failure, incompleteness, success. A fatal
operational error is NOT representable here -- ``strix.interface.main``
already calls ``sys.exit(1)`` directly for those (Docker missing, model
unreachable, an unhandled exception), before or without ever reaching a
composed result. This module only distinguishes the three states a scan
that DID start running can end in.

The controlled assessment profile also makes missing essential runtime
obligations incomplete (ENG-06). General agent-reported or skill-derived gaps
remain informational; they cannot satisfy or waive an essential obligation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from strix.report.writer import atomic_write_text


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)

RESULT_SCHEMA_VERSION = 1
RESULT_FILENAME = "result.json"

#: §7.5's convention, minus the fatal-error code (1) this module never emits --
#: see the module docstring.
EXIT_SUCCESS = 0
EXIT_FINDINGS_POLICY_FAILED = 2
EXIT_INCOMPLETE = 3


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """The scan's composed result -- what the headless exit code and
    ``result.json`` both come from, so the two can never disagree."""

    schema_version: int
    scan_status: str
    operationally_complete: bool
    exit_reason: str | None
    caveats: tuple[str, ...]
    coverage_gaps: int
    findings_count: int
    findings_policy_failed: bool
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scan_status": self.scan_status,
            "operationally_complete": self.operationally_complete,
            "exit_reason": self.exit_reason,
            "caveats": list(self.caveats),
            "coverage_gaps": self.coverage_gaps,
            "findings_count": self.findings_count,
            "findings_policy_failed": self.findings_policy_failed,
            "exit_code": self.exit_code,
        }


def compose_evaluation_result(
    *,
    coverage_document: dict[str, Any] | None,
    findings_count: int,
    findings_policy_failed: bool,
) -> EvaluationResult:
    """Compose the result. ``coverage_document`` is ``coverage.json`` as
    ``strix.report.coverage.read_coverage``/``build_coverage_document``
    shape it; ``None`` when it could not be read (treated as incomplete
    -- a scan this module cannot attest as complete must never exit 0).
    """
    completeness = (coverage_document or {}).get("completeness") or {}
    scan_status = str(completeness.get("scan_status") or "unknown")
    operationally_complete = bool(completeness.get("complete", False))
    exit_reason = completeness.get("exit_reason")
    caveats = tuple(str(c) for c in (completeness.get("caveats") or ()))
    summary = (coverage_document or {}).get("summary") or {}
    coverage_gaps = int(summary.get("gaps", 0) or 0)

    if findings_policy_failed:
        exit_code = EXIT_FINDINGS_POLICY_FAILED
    elif not operationally_complete:
        exit_code = EXIT_INCOMPLETE
    else:
        exit_code = EXIT_SUCCESS

    return EvaluationResult(
        schema_version=RESULT_SCHEMA_VERSION,
        scan_status=scan_status,
        operationally_complete=operationally_complete,
        exit_reason=exit_reason,
        caveats=caveats,
        coverage_gaps=coverage_gaps,
        findings_count=findings_count,
        findings_policy_failed=findings_policy_failed,
        exit_code=exit_code,
    )


def write_result(run_dir: Path, result: EvaluationResult) -> Path:
    """Persist ``result.json`` next to ``coverage.json``/``run.json``,
    atomically (same ``atomic_write_text`` the rest of ``strix/report/``
    already uses). Raises on failure -- the caller decides whether a
    write error should be fatal; see its call site in
    ``strix.interface.main``.
    """
    path = run_dir / RESULT_FILENAME
    atomic_write_text(path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    logger.info("Saved evaluation result to: %s (exit_code=%d)", path, result.exit_code)
    return path
