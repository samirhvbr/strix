"""Read real SQLite/WAL after process death, without dispatch or source mutations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from strix.core.history_export import export_history
from strix.core.run_lease import controller_lease
from strix.core.test_catalog import TestCatalog
from strix.interface.history_export import run_history_export
from strix.telemetry.test_ledger import TestLedger
from tests.test_assessment_review import fixture_run, snapshot_files


pytestmark = pytest.mark.skipif(os.name != "posix", reason="private POSIX history")


def record(run):
    path = run / "run.json"
    path.write_text(
        json.dumps(
            {
                "run_id": run.name,
                "start_time": "2026-10-10T00:00:00+00:00",
                "end_time": None,
                "status": "running",
                "scan_mode": "standard",
                "strix_version": "1.2.26",
                "instruction": "PRIVATE TASK",
                "targets_info": ["PRIVATE TARGET"],
            }
        )
    )
    path.chmod(0o600)


@pytest.mark.asyncio
async def test_closed_history_reuses_live_projection_and_excludes_private_fields(tmp_path, capsys):
    run, executor = fixture_run(tmp_path)
    record(run)
    state = run / ".state"
    catalog = TestCatalog()
    catalog.set_snapshot_path(state / "test_catalog.json")
    with controller_lease(state):
        ledger = TestLedger(
            state / "test_telemetry.db", scan_id="scan", catalog=catalog, owns_agent=lambda _: True
        )
        unit = catalog.register(agent_id="child", name="PRIVATE TASK")
        ledger.sync_test(unit)
        live = ledger.snapshot()
        ledger.close()
    before = snapshot_files(run)
    data = export_history(run)
    assert data["telemetry"]["tests"] == live["tests"]
    assert data["telemetry"]["metadata"]["ingestion_status"] == "closed"
    assert data["runtime"]["status"] == "running", "history does not invent successful completion"
    assert snapshot_files(run) == before
    assert run_history_export([str(run)]) == 0
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert data["assessment_id"] == json.loads(output)["assessment_id"]
    with controller_lease(state):
        assert run_history_export([str(run)]) == 1
    error = capsys.readouterr().err
    assert error == '{"error":"assessment_history_unavailable"}\n'
    await executor.close()


@pytest.mark.asyncio
async def test_killed_writer_preserves_committed_wal_rows_without_recovery_writes(tmp_path):
    run, executor = fixture_run(tmp_path)
    record(run)
    code = """
import sys, time
from pathlib import Path
from strix.core.run_lease import controller_lease
from strix.core.test_catalog import TestCatalog
from strix.telemetry.test_ledger import TestLedger
state = Path(sys.argv[1]) / ".state"
with controller_lease(state):
    catalog = TestCatalog()
    catalog.set_snapshot_path(state / "test_catalog.json")
    ledger = TestLedger(
        state / "test_telemetry.db", scan_id="scan", catalog=catalog, owns_agent=lambda _: True
    )
    unit = catalog.register(agent_id="child", name="private task")
    ledger.sync_test(unit)
    (state / "ready").touch()
    while True: time.sleep(1)
"""
    with subprocess.Popen(  # noqa: S603 -- Fixed fixture code and an owned temporary path.
        [sys.executable, "-c", code, str(run)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    ) as child:
        try:
            deadline = time.monotonic() + 20
            while not (run / ".state/ready").exists():
                assert child.poll() is None, (
                    child.stderr.read().decode() if child.poll() is not None else ""
                )
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert (run / ".state/test_telemetry.db-wal").stat().st_size > 0
            with pytest.raises(OSError):
                export_history(run)
        finally:
            child.kill()
            child.wait(timeout=5)
    before = snapshot_files(run)
    result = export_history(run)
    assert result["telemetry"]["tests"][0]["agent_id"] == "child"
    assert result["telemetry"]["metadata"]["ingestion_status"] == "collecting"
    assert snapshot_files(run) == before
    assert export_history(run) == result, "repeated offline export is stable"
    await executor.close()


@pytest.mark.asyncio
async def test_history_rejects_linked_or_cross_run_records(tmp_path):
    run, executor = fixture_run(tmp_path)
    record(run)
    state = run / ".state"
    with controller_lease(state):
        catalog = TestCatalog()
        catalog.set_snapshot_path(state / "test_catalog.json")
        catalog.register(agent_id="child", name="safe")
        ledger = TestLedger(
            state / "test_telemetry.db",
            scan_id="wrong-scan",
            catalog=catalog,
            owns_agent=lambda _: True,
        )
        ledger.close()
    with pytest.raises(ValueError, match="different run"):
        export_history(run)
    path = state / "test_telemetry.db"
    path.rename(state / "original.db")
    path.symlink_to(state / "original.db")
    assert run_history_export([str(run)]) == 1
    await executor.close()
