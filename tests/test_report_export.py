"""Report sharing is bounded, scope-bound and distinct from metadata export."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strix.core.history_export import stopped_assessment
from strix.core.report_export import export_report
from strix.core.run_lease import controller_lease
from strix.interface.report_export import run_report_export
from tests.test_assessment_review import fixture_run, snapshot_files


pytestmark = pytest.mark.skipif(os.name != "posix", reason="private POSIX report")


def write(path: Path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@pytest.mark.asyncio
async def test_report_uses_actual_finish_and_coverage_shape_without_private_runtime(
    tmp_path, capsys
):
    run, executor = fixture_run(tmp_path)
    with controller_lease(run / ".state"):
        pass
    write(
        run / "run.json",
        {
            "run_id": run.name,
            "instruction": "PRIVATE TASK",
            "scan_results": {
                "executive_summary": "Two findings require follow-up.",
                "methodology": "Controlled fixture.",
                "technical_analysis": "Do not infer a clean target from missing coverage.",
                "recommendations": "Retest after remediation.",
            },
        },
    )
    write(
        run / "vulnerabilities.json",
        [
            {
                "id": "finding-1",
                "severity": "high",
                "title": "Tenant boundary",
                "description": "Owner isolation failed.",
                "evidence": "Authorization: Bearer synthetic-private\nGET /account",
                "poc_script_code": "assert response.status_code == 403",
                "cwe": "CWE-862",
                "endpoint": "/account",
                "code_locations": [
                    {"file": "src/handler.py", "start_line": 2, "fix_after": "require_owner()"}
                ],
                "dependency_metadata": {"package_name": "example", "installed_version": "1.0.0"},
            }
        ],
    )
    write(
        run / "coverage.json",
        {
            "schema_version": 1,
            "run_id": run.name,
            "entries": [
                {
                    "surface": "/account",
                    "risk_area": "idor",
                    "outcome": "reported",
                    "source": "agent_reported",
                }
            ],
            "gaps": [{"source": "runtime", "detail": "Required case incomplete"}],
            "completeness": {"complete": False},
            "machine_observed": {
                "source": "runtime",
                "agents": [{"task": "PRIVATE TASK"}],
                "assessment_obligations": {"essential_unfulfilled": 1},
            },
        },
    )
    before = snapshot_files(run)
    data = export_report(run)
    assert snapshot_files(run) == before
    assert data["coverage"]["completeness"]["complete"] is False
    assert data["findings"][0]["cwe"] == "CWE-862"
    assert data["findings"][0]["code_locations"][0]["file"] == "src/handler.py"
    assert "synthetic-private" not in json.dumps(data)
    assert "PRIVATE TASK" not in json.dumps(data)
    assert "[REDACTED_SECRET]" in json.dumps(data)
    assert run_report_export([str(run)]) == 0
    assert json.loads(capsys.readouterr().out) == data
    fixture = os.environ.get("SHVIA_REPORT_FIXTURE")
    if fixture:
        Path(fixture).write_text(json.dumps(data, indent=2) + "\n")
    with stopped_assessment(run):
        assert run_report_export([str(run)]) == 1
    assert capsys.readouterr().err == '{"error":"assessment_report_unavailable"}\n'
    await executor.close()


@pytest.mark.asyncio
async def test_report_refuses_cross_scan_linked_or_oversized_artifacts(tmp_path):
    run, executor = fixture_run(tmp_path)
    with controller_lease(run / ".state"):
        pass
    write(run / "run.json", {"run_id": "other"})
    write(run / "vulnerabilities.json", [])
    with pytest.raises(ValueError, match="another run"):
        export_report(run)
    write(run / "run.json", {"run_id": run.name})
    write(run / "vulnerabilities.json", [{"title": "x" * 100_001}])
    with pytest.raises(ValueError, match="limit"):
        export_report(run)
    (run / "vulnerabilities.json").unlink()
    assert export_report(run)["findings"] is None, "absent evidence cannot claim zero findings"
    write(run / "vulnerabilities.json", [])
    source = run / "vulnerabilities.json"
    source.rename(run / "saved.json")
    source.symlink_to(run / "saved.json")
    with pytest.raises(OSError):
        export_report(run)
    await executor.close()
