"""Tests for strix/report/result.py (ENG-01/ENG-02, .continue/pentest/
PENTEST-11-DOCUMENTACAO-PRODUTO-CONSOLIDADA.md §7.5).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from strix.report.result import (
    EXIT_FINDINGS_POLICY_FAILED,
    EXIT_INCOMPLETE,
    EXIT_SUCCESS,
    compose_evaluation_result,
    write_result,
)


if TYPE_CHECKING:
    from pathlib import Path


def _coverage_document(
    *,
    complete: bool,
    scan_status: str = "completed",
    exit_reason: str | None = "finished_by_tool",
    caveats: list[str] | None = None,
    gaps: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "completeness": {
            "complete": complete,
            "scan_status": scan_status,
            "exit_reason": exit_reason,
            "caveats": caveats or [],
        },
        "summary": {"gaps": gaps},
    }


# --- precedence (§7.5: fatal error > findings-policy failure > incompleteness > success) ---


def test_complete_scan_with_no_findings_failure_succeeds() -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=True),
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.operationally_complete is True


def test_incomplete_scan_with_no_findings_failure_is_incomplete() -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=False, scan_status="stopped"),
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.exit_code == EXIT_INCOMPLETE


def test_complete_scan_with_findings_failure_fails_the_build() -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=True),
        findings_count=3,
        findings_policy_failed=True,
    )
    assert result.exit_code == EXIT_FINDINGS_POLICY_FAILED


def test_findings_failure_takes_precedence_over_incompleteness() -> None:
    """§7.5: 'a precedência sugerida é erro fatal, reprovação por achados,
    incompletude e sucesso' -- a scan that is BOTH incomplete and breaches
    --fail-on exits 2, not 3."""
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=False, scan_status="failed"),
        findings_count=1,
        findings_policy_failed=True,
    )
    assert result.exit_code == EXIT_FINDINGS_POLICY_FAILED


# --- missing/unreadable coverage never silently succeeds ---


def test_missing_coverage_document_is_incomplete_not_success() -> None:
    """A scan this module cannot attest as complete must never exit 0 --
    the bug §7.5 exists to fix ('0 não oferece hoje a garantia proposta')."""
    result = compose_evaluation_result(
        coverage_document=None,
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.exit_code == EXIT_INCOMPLETE
    assert result.operationally_complete is False
    assert result.scan_status == "unknown"


def test_coverage_document_missing_completeness_key_is_incomplete() -> None:
    result = compose_evaluation_result(
        coverage_document={"schema_version": 1},
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.exit_code == EXIT_INCOMPLETE


# --- fields carried through for the UI/dashboard (Spec 09/D11) to read ---


def test_caveats_and_exit_reason_are_carried_through() -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(
            complete=False,
            scan_status="stopped",
            exit_reason="budget_exceeded",
            caveats=["2 agent(s) did not finish cleanly"],
        ),
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.exit_reason == "budget_exceeded"
    assert result.caveats == ("2 agent(s) did not finish cleanly",)
    assert result.scan_status == "stopped"


def test_coverage_gaps_count_is_carried_through_but_does_not_gate() -> None:
    """ENG-06 (not this module) is where a gap starts blocking success --
    see the module docstring. A complete run with open gaps still succeeds."""
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=True, gaps=4),
        findings_count=0,
        findings_policy_failed=False,
    )
    assert result.coverage_gaps == 4
    assert result.exit_code == EXIT_SUCCESS


# --- to_dict / persistence ---


def test_to_dict_round_trips_through_json() -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=True, gaps=1),
        findings_count=2,
        findings_policy_failed=False,
    )
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["exit_code"] == EXIT_SUCCESS
    assert payload["findings_count"] == 2
    assert payload["coverage_gaps"] == 1
    assert payload["caveats"] == []


def test_write_result_emits_a_top_level_artifact(tmp_path: Path) -> None:
    result = compose_evaluation_result(
        coverage_document=_coverage_document(complete=True),
        findings_count=0,
        findings_policy_failed=False,
    )

    path = write_result(tmp_path, result)

    assert path == tmp_path / "result.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["exit_code"] == EXIT_SUCCESS
    assert on_disk["schema_version"] == 1
