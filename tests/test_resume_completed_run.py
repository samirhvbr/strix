"""Resuming a run keeps a completed result until a new finish_scan replaces it (fork 1.2.58)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from strix.report.state import ReportState


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


RESULTS = {
    "scan_completed": True,
    "executive_summary": "No confirmed vulnerability.",
    "methodology": "Black box.",
    "technical_analysis": "Edge and API.",
    "recommendations": "Retest with accounts.",
    "success": True,
}


def record(tmp_path: Path, name: str) -> dict[str, Any]:
    return json.loads((tmp_path / "strix_runs" / name / "run.json").read_text())


def resumed(name: str) -> ReportState:
    state = ReportState(run_name=name)
    state.hydrate_from_run_dir()
    state.set_scan_config({})
    state.save_run_data()
    return state


def test_resuming_a_completed_run_keeps_its_status_end_and_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-24: a resume that ended at once left a finished run RUNNING with no result."""
    monkeypatch.chdir(tmp_path)
    first = ReportState(run_name="done")
    first.scan_results = dict(RESULTS)
    first.run_record["scan_results"] = first.scan_results
    first.save_run_data(mark_complete=True)
    finished = record(tmp_path, "done")
    assert finished["status"] == "completed"

    state = resumed("done")
    after = record(tmp_path, "done")
    assert after["status"] == "completed"
    assert after["end_time"] == finished["end_time"]
    assert after["scan_results"] == RESULTS
    assert state.final_scan_result, "the PDF and the report still have the summary"

    # The resume ends with no new work (Ctrl-C, a closed terminal): still completed.
    state.cleanup(status="stopped")
    assert record(tmp_path, "done")["status"] == "completed"
    assert record(tmp_path, "done")["end_time"] == finished["end_time"]


def test_resuming_an_unfinished_run_still_reopens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ReportState(run_name="halted").save_run_data(status="stopped")
    assert record(tmp_path, "halted")["status"] == "stopped"

    resumed("halted")
    after = record(tmp_path, "halted")
    assert after["status"] == "running"
    assert after["end_time"] is None
