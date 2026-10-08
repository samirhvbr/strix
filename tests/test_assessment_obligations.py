"""Essential scope cannot be satisfied by prose, stale attempts or incomplete receipts."""

from __future__ import annotations

import importlib
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from strix.core.assessment_context import parse_context
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.report.result import EXIT_INCOMPLETE, EXIT_SUCCESS, compose_evaluation_result
from tests.test_assessment_context import config


if TYPE_CHECKING:
    from pathlib import Path


def ledger_at(path: Path) -> EvidenceLedger:
    _, raw = config(8080)
    raw["operations"] = {"private": raw["operations"]["private"]}
    raw["cases"]["cross-tenant"]["operations"] = ["private"]
    context = parse_context(raw)
    assert context is not None
    ledger = EvidenceLedger(
        path,
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda agent: agent == "agent",
    )
    ledger.bind_obligations(context)
    return ledger


def observe(
    ledger: EvidenceLedger, identity: str, status: str = "observed", *, truncated: bool = False
) -> str:
    attempt = ledger.begin(
        agent_ref="agent",
        case_ref="cross-tenant",
        case_version=1,
        identity_ref=identity,
        operation_ref="private",
    )
    return ledger.finish(attempt, status=status, content={"status_code": 200}, truncated=truncated)


def result(ledger: EvidenceLedger) -> int:
    summary = ledger.summary() | {"status": "closed"}
    document = importlib.import_module("tests.test_report_coverage")._document(
        run_record={"status": "completed", "evidence_ledger": summary},
        entries=[{"agent_id": "agent", "outcome": "no_issue_found", "surface": "all cases tested"}],
    )
    assert document["machine_observed"]["assessment_obligations"] == summary["obligations"]
    outcome = compose_evaluation_result(
        coverage_document=document, findings_count=0, findings_policy_failed=False
    )
    assert outcome.coverage_gaps >= summary["obligations"]["essential_unfulfilled"]
    return outcome.exit_code


def test_missing_identity_blocks_full_result_despite_clean_agent_prose(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path / "evidence.db")
    assert ledger.summary()["obligations"]["essential_total"] == 2
    assert result(ledger) == EXIT_INCOMPLETE
    observe(ledger, "a")
    assert result(ledger) == EXIT_INCOMPLETE
    observe(ledger, "b")
    assert result(ledger) == EXIT_SUCCESS
    ledger.close()


@pytest.mark.parametrize(
    "status,truncated", [("blocked", False), ("uncertain", False), ("observed", True)]
)
def test_latest_incomplete_attempt_invalidates_previous_complete_coverage(
    tmp_path: Path, status: str, truncated: bool
) -> None:
    ledger = ledger_at(tmp_path / "evidence.db")
    observe(ledger, "a")
    observe(ledger, "b")
    assert result(ledger) == EXIT_SUCCESS
    observe(ledger, "b", status, truncated=truncated)
    assert result(ledger) == EXIT_INCOMPLETE
    ledger.close()
    restored = ledger_at(tmp_path / "evidence.db")
    assert result(restored) == EXIT_INCOMPLETE
    observe(restored, "b")
    # A later HTTP response cannot reconcile the effects of an earlier uncertain request.
    assert result(restored) == (EXIT_INCOMPLETE if status == "uncertain" else EXIT_SUCCESS)
    restored.close()


def test_started_or_corrupt_receipt_does_not_satisfy_obligation(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path / "evidence.db")
    observe(ledger, "a")
    ref = observe(ledger, "b")
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE artifacts SET content=? WHERE id=?", (b"corrupt", ref))
    assert ledger.summary()["obligations"]["items"][1]["status"] == "invalid_evidence"
    assert result(ledger) == EXIT_INCOMPLETE
    ledger.begin(
        agent_ref="agent",
        case_ref="cross-tenant",
        case_version=1,
        identity_ref="b",
        operation_ref="private",
    )
    assert ledger.summary()["obligations"]["items"][1]["status"] == "started"
    ledger.close()
    restored = ledger_at(tmp_path / "evidence.db")
    assert result(restored) == EXIT_INCOMPLETE
    restored.close()


def test_wrong_case_version_and_tampered_plan_fail_closed(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path / "evidence.db")
    with pytest.raises(EvidenceError, match="outside"):
        ledger.begin(
            agent_ref="agent",
            case_ref="cross-tenant",
            case_version=2,
            identity_ref="a",
            operation_ref="private",
        )
    ledger.close()
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("DELETE FROM obligations WHERE identity_ref='b'")
    with pytest.raises(EvidenceError, match="changed"):
        ledger_at(tmp_path / "evidence.db")


def test_missing_plan_cannot_become_complete_or_finish_as_successful() -> None:
    record = {"status": "completed", "assessment_context": {"version": 1}}
    document = importlib.import_module("tests.test_report_coverage")._document(run_record=record)
    outcome = compose_evaluation_result(
        coverage_document=document, findings_count=0, findings_policy_failed=False
    )
    assert outcome.exit_code == EXIT_INCOMPLETE
    assert outcome.coverage_gaps >= 1
    state = MagicMock(run_record=record, vulnerability_reports=[])
    with patch("strix.report.state.get_global_report_state", return_value=state):
        finished = importlib.import_module("strix.tools.finish.tool")._do_finish(
            parent_id=None,
            executive_summary="No issues found",
            methodology="Review",
            technical_analysis="Missing runtime receipts",
            recommendations="Complete scope",
            agent_graph={},
        )
    assert finished["scan_completed"] is True
    assert finished["essential_coverage_complete"] is False
    assert "coverage gaps" in finished["message"]
