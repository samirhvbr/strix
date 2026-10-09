"""Offline review must verify evidence without changing or disclosing private history."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.assessment import bind_assessment_policy
from strix.core.assessment_review import review_assessment
from strix.core.evidence_ledger import EvidenceLedger
from strix.interface.assessment_review import run_review
from tests.test_assessment_context import config
from tests.test_authorization_case import executor_at
from tests.test_authorization_case import (
    private_server as private_server,  # noqa: PLC0414 -- Fixture export.
)


if TYPE_CHECKING:
    from pathlib import Path


def fixture_run(tmp_path: Path, port: int = 8080) -> Any:
    run = tmp_path / "scan"
    state = run / ".state"
    state.mkdir(parents=True, mode=0o700)
    policy, _ = config(port)
    bind_assessment_policy(state, "scan", policy, resuming=False)
    return run, executor_at(state, port)


def snapshot_files(run: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        str(path.relative_to(run)): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
            path.stat().st_mode,
        )
        for path in run.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_empty_review_has_gaps_and_changes_no_files(tmp_path: Path) -> None:
    run, executor = fixture_run(tmp_path)
    before = snapshot_files(run)
    report = review_assessment(run)
    assert snapshot_files(run) == before
    assert report["source"] == "verified_runtime_evidence_snapshot"
    assert report["obligations"]["essential_unfulfilled"] == 4
    assert report["cases"][0]["verdict"] == "missing"
    assert report["history"] == []
    assert "success" not in report
    await executor.close()


@pytest.mark.asyncio
async def test_real_baseline_fixed_retest_and_stale_review(
    tmp_path: Path, private_server: Any
) -> None:
    port, target = private_server
    run, executor = fixture_run(tmp_path, port)
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    target["mode"] = "fixed"
    fixed = await executor.run_case(
        agent_ref="agent", case_ref="cross-tenant", baseline_ref=first["case_result_ref"]
    )
    before, calls = snapshot_files(run), target["calls"]
    report = review_assessment(run)
    assert snapshot_files(run) == before
    assert target["calls"] == calls
    assert report["cases"][0]["retest_status"] == "fixed"
    assert report["cases"][0]["baseline_ref"] == first["case_result_ref"]
    assert [item["verdict"] for item in report["history"]] == ["compliant", "vulnerable"]
    assert report["history"][0]["case_result_ref"] == fixed["case_result_ref"]
    assert report["obligations"]["essential_unfulfilled"] == 0
    public = json.dumps(report)
    for secret in ["SYNTHETIC-PRIVATE-CONTENT-42", "tenant-a-token", "credentials.json", "body"]:
        assert secret not in public
    executor.ledger.begin(
        agent_ref="agent",
        case_ref="cross-tenant",
        case_version=1,
        identity_ref="a",
        operation_ref="private",
    )
    stale = review_assessment(run)
    assert stale["cases"][0]["verdict"] == "needs_retest"
    assert stale["unresolved_attempts"] == 1
    assert stale["obligations"]["essential_unfulfilled"] == 1
    assert len(stale["history"]) == 2
    await executor.close()


@pytest.mark.asyncio
async def test_corruption_is_visible_and_arbitrary_result_fields_do_not_leak(
    tmp_path: Path,
    private_server: Any,
) -> None:
    port, _ = private_server
    run, executor = fixture_run(tmp_path, port)
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    with sqlite3.connect(executor.ledger.path) as db:
        raw = db.execute("SELECT content FROM case_results").fetchone()[0]
        data = json.loads(raw)
        data.update(reason="SYNTHETIC-SECRET", narrative="SYNTHETIC-SECRET")
        encoded = json.dumps(data).encode()
        db.execute(
            "UPDATE case_results SET content=?,sha256=?",
            (encoded, hashlib.sha256(encoded).hexdigest()),
        )
    assert "SYNTHETIC-SECRET" not in json.dumps(review_assessment(run))
    with sqlite3.connect(executor.ledger.path) as db:
        db.execute(
            "UPDATE artifacts SET content=? WHERE id=?",
            (b"{}", first["evidence"][0]["evidence_ref"]),
        )
    report = review_assessment(run)
    assert report["cases"][0]["verdict"] == "invalid_evidence"
    assert report["history"][0]["verdict"] == "invalid_evidence"
    await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["plan", "binding", "missing", "permissions", "symlink", "context"]
)
async def test_unsafe_or_crossed_history_fails_without_mutation(
    tmp_path: Path,
    damage: str,
    capsys: Any,
) -> None:
    run, executor = fixture_run(tmp_path)
    path = executor.ledger.path
    if damage in {"plan", "binding"}:
        with sqlite3.connect(path) as db:
            db.execute(
                "DELETE FROM obligations"
                if damage == "plan"
                else "UPDATE binding SET scan_id='other'"
            )
    elif damage == "missing":
        path.unlink()
    elif damage == "permissions":
        path.chmod(0o644)
    elif damage == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    else:
        path = run / ".state" / "assessment-context.json"
        data = json.loads(path.read_text())
        data["scan_id"] = "different"
        path.write_text(json.dumps(data))
    before = snapshot_files(run)
    assert run_review([str(run)]) == 1
    assert snapshot_files(run) == before
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err) == {"error": "assessment_review_unavailable"}


@pytest.mark.asyncio
async def test_snapshot_stays_consistent_and_refuses_writes(tmp_path: Path) -> None:
    run, executor = fixture_run(tmp_path)
    context = executor.context
    with EvidenceLedger.snapshot(
        executor.ledger.path,
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
    ) as review:
        executor.ledger.begin(
            agent_ref="agent",
            case_ref="cross-tenant",
            case_version=1,
            identity_ref="a",
            operation_ref="private",
        )
        assert review.review(context)["attempts"] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            review.record_case_result("cross-tenant", {})
    assert review_assessment(run)["attempts"] == 1
    await executor.close()


def test_missing_run_does_not_create_directory(tmp_path: Path, capsys: Any) -> None:
    missing = tmp_path / "absent"
    assert run_review([str(missing)]) == 1
    assert not missing.exists()
    assert "absent" not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("journal", ["wal", "pending", "oversized"])
async def test_review_refuses_unsupported_storage_without_repair(
    tmp_path: Path,
    journal: str,
    capsys: Any,
) -> None:
    run, executor = fixture_run(tmp_path)
    path = executor.ledger.path
    if journal == "wal":
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
        db.close()
    elif journal == "pending":
        path.with_name(path.name + "-journal").write_bytes(b"unsettled")
    else:
        with path.open("ab") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
    before = snapshot_files(run)
    assert run_review([str(run)]) == 1
    assert snapshot_files(run) == before
    assert not capsys.readouterr().out


@pytest.mark.asyncio
async def test_history_limit_is_explicit(tmp_path: Path, private_server: Any) -> None:
    port, _ = private_server
    run, executor = fixture_run(tmp_path, port)
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    for _ in range(100):
        executor.ledger.record_case_result("cross-tenant", first)
    report = review_assessment(run)
    assert len(report["history"]) == 100
    assert report["history_truncated"] is True
    await executor.close()


def test_main_routes_review_before_scan_setup(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    main = importlib.import_module("strix.interface.main")
    monkeypatch.setattr("sys.argv", ["strix", "review", str(tmp_path)])
    monkeypatch.setattr(main, "parse_arguments", lambda: pytest.fail("scan setup was invoked"))
    with pytest.raises(SystemExit) as stopped:
        main.main()
    assert stopped.value.code == 1
    assert not capsys.readouterr().out
