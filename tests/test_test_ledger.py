from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.agents import AgentCoordinator
from strix.core.test_catalog import TestCatalog
from strix.llm.request_log import LlmRequestEvent
from strix.telemetry import test_ledger
from strix.telemetry.test_ledger import TestLedger


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def event(**overrides: Any) -> LlmRequestEvent:
    started = datetime(2026, 10, 8, tzinfo=UTC)
    base = LlmRequestEvent(
        call_id="attempt-1",
        route="openai",
        provider="openai",
        model="model-a",
        api_host="provider.invalid",
        streaming=True,
        outcome="success",
        status_code=200,
        provider_request_id=None,
        response_id=None,
        error_type=None,
        error_message=None,
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        duration_ms=2000,
        agent_id="child",
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=50,
        total_tokens=120,
        cost_usd=0.01,
    )
    return replace(base, **overrides)


def rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]  # noqa: S608


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[TestLedger]:
    catalog = TestCatalog()
    result = TestLedger(
        tmp_path / "test_telemetry.db",
        scan_id="scan-1",
        catalog=catalog,
        owns_agent=lambda aid: aid in {"root", "child"},
    )
    catalog.set_change_callback(result.sync_test)
    catalog.register(agent_id="child", name="Specialist", skills=["sql_injection"])
    yield result
    result.close()


def test_multi_model_accounting_preserves_root_cost_and_replays(
    ledger: TestLedger,
    tmp_path: Path,
) -> None:
    first = event()
    ledger.record_llm_event(first)
    ledger.record_llm_event(first)
    ledger.record_llm_event(
        event(call_id="attempt-2", model="model-b", cost_usd=0.02, retry_attempt=1)
    )
    ledger.record_llm_event(event(call_id="root-1", agent_id="root", cost_usd=0.04))
    db = tmp_path / "test_telemetry.db"
    attempts = rows(db, "test_events")
    assert len(attempts) == 3
    assert next(e for e in attempts if e["agent_id"] == "root")["test_id"] is None
    summary = rows(db, "test_summary")[0]
    assert summary["attempt_count"] == 2
    assert summary["replay_attempt_count"] == 1
    assert summary["total_tokens"] == 240
    assert summary["total_cost_usd"] == pytest.approx(0.03)
    assert summary["provider_duration_ms"] == 4000
    assert len(rows(db, "model_usage")) == 3
    assert sum(r["total_cost_usd"] for r in rows(db, "agent_usage")) == pytest.approx(0.07)


def test_resume_preserves_ids_and_deduplicates_existing_attempts(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.db"
    snapshot = tmp_path / "catalog.json"
    catalog = TestCatalog()
    catalog.set_snapshot_path(snapshot)
    unit = catalog.register(agent_id="child", name="Specialist")
    for index in range(2):
        restored = TestCatalog()
        restored.load(snapshot)
        writer = TestLedger(
            path,
            scan_id="scan-1",
            catalog=restored,
            owns_agent=lambda _aid: True,
            resuming=index > 0,
        )
        restored.set_change_callback(writer.sync_test)
        writer.record_llm_event(event())
        writer.close()
    assert len(rows(path, "test_events")) == 1
    assert rows(path, "test_summary")[0]["test_id"] == unit.test_id
    metadata = rows(path, "ledger_metadata")[0]
    assert metadata["history_scope"] == "from_scan_start"
    assert metadata["interrupted_writers"] == 0
    assert metadata["ingestion_status"] == "closed"


def test_legacy_resume_explicitly_marks_partial_history(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.db"
    writer = TestLedger(
        path, scan_id="old-run", catalog=TestCatalog(), owns_agent=lambda _aid: True, resuming=True
    )
    writer.close()
    assert rows(path, "ledger_metadata")[0]["history_scope"] == "from_resume"


def test_ungraceful_writer_restart_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.db"
    writer = TestLedger(path, scan_id="scan-1", catalog=TestCatalog(), owns_agent=lambda _aid: True)
    writer.record_llm_event(event(agent_id="root"))
    writer._db.close()  # A process exit does not call the ledger's finalizer.
    resumed = TestLedger(
        path, scan_id="scan-1", catalog=TestCatalog(), owns_agent=lambda _aid: True, resuming=True
    )
    resumed.record_llm_event(event(agent_id="root"))
    resumed.close()
    assert len(rows(path, "test_events")) == 1
    assert rows(path, "ledger_metadata")[0]["interrupted_writers"] == 1


def test_zero_call_test_and_resumed_status_are_visible(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.db"
    catalog = TestCatalog()
    writer = TestLedger(path, scan_id="scan-1", catalog=catalog, owns_agent=lambda _aid: True)
    catalog.set_change_callback(writer.sync_test)
    unit = catalog.register(agent_id="child", name="No provider call")
    catalog.mark_status("child", "failed")
    summary = rows(path, "test_summary")[0]
    assert summary["attempt_count"] == 0
    assert summary["status"] == "failed"
    assert summary["ended_at"] is not None
    assert summary["duration_s"] >= 0
    catalog.mark_status("child", "running")
    resumed = rows(path, "test_summary")[0]
    assert resumed["test_id"] == unit.test_id
    assert resumed["status"] == "running"
    assert resumed["ended_at"] is None
    assert resumed["duration_s"] is None
    writer.close()


@pytest.mark.asyncio
async def test_coordinator_resume_notifies_catalog() -> None:
    coordinator, catalog = AgentCoordinator(), TestCatalog()
    await coordinator.register("child", "Child", parent_id="root")
    catalog.register(agent_id="child", name="Child")
    coordinator.set_status_change_callback(catalog.mark_status)
    await coordinator.set_status("child", "failed")
    await coordinator.mark_running("child")
    unit = catalog.get("child")
    assert unit is not None and unit.status == "running" and unit.ended_at is None


def test_retry_and_failure_incidents_exclude_provider_secrets(
    ledger: TestLedger,
    tmp_path: Path,
) -> None:
    secret = "synthetic-private-token-and-request-body"  # noqa: S105 - exclusion fixture
    failed = event(
        outcome="error",
        status_code=429,
        cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        retry_attempt=1,
        error_message=secret,
        response_headers={"authorization": secret},
        details={"body": secret},
    )
    ledger.record_llm_event(failed)
    ledger.record_llm_event(failed)
    ledger.record_status("root", "failed")
    ledger.record_status("root", "failed")
    db = tmp_path / "test_telemetry.db"
    incidents = rows(db, "scan_incidents")
    assert {i["source"] for i in incidents} == {"llm_retry", "rate_limited", "agent_failed"}
    assert len(incidents) == 3
    ledger.close()
    assert secret.encode() not in db.read_bytes()


def test_unknown_usage_is_distinct_from_zero(ledger: TestLedger, tmp_path: Path) -> None:
    ledger.record_llm_event(
        event(cost_usd=None, input_tokens=None, output_tokens=None, total_tokens=None)
    )
    summary = rows(tmp_path / "test_telemetry.db", "test_summary")[0]
    assert summary["total_cost_usd"] is None and summary["total_tokens"] is None
    assert summary["unknown_cost_attempts"] == 1
    assert summary["unknown_token_attempts"] == 1


@pytest.mark.parametrize("subscription", [False, True])
def test_subscription_and_reported_zero_cost(tmp_path: Path, subscription: bool) -> None:
    path = tmp_path / "telemetry.db"
    writer = TestLedger(
        path,
        scan_id="scan",
        catalog=TestCatalog(),
        owns_agent=lambda _aid: True,
        subscription=subscription,
    )
    writer.record_llm_event(event(agent_id="root", cost_usd=10 if subscription else 0))
    writer.close()
    attempt = rows(path, "test_events")[0]
    assert attempt["cost_usd"] == 0
    assert attempt["cost_basis"] == ("subscription" if subscription else "reported")


def test_estimation_reuses_report_pricing_and_cache_tokens(
    ledger: TestLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def estimate(usage: Any, model: str) -> float:
        captured.append(
            (
                usage.input_tokens,
                usage.output_tokens,
                usage.input_tokens_details.cached_tokens,
                model,
            )
        )
        return 0.012

    monkeypatch.setattr(test_ledger, "estimate_usage_cost", estimate)
    ledger.record_llm_event(event(cost_usd=None))
    assert captured == [(100, 20, 50, "model-a")]
    attempt = rows(tmp_path / "test_telemetry.db", "test_events")[0]
    assert attempt["cost_basis"] == "estimated" and attempt["cost_usd"] == 0.012


def test_process_wide_sink_cannot_mix_agents_from_different_runs(
    ledger: TestLedger,
    tmp_path: Path,
) -> None:
    ledger.record_llm_event(event(agent_id="someone-elses-root"))
    ledger.record_llm_event(event(agent_id=None))
    assert not rows(tmp_path / "test_telemetry.db", "test_events")


def test_concurrent_delivery_is_idempotent(ledger: TestLedger, tmp_path: Path) -> None:
    attempts = [event(call_id=f"attempt-{i}") for i in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(ledger.record_llm_event, attempts * 3))
    assert rows(tmp_path / "test_telemetry.db", "test_summary")[0]["attempt_count"] == 20


def test_partial_write_rolls_back_and_persists_degraded_health(
    ledger: TestLedger,
    tmp_path: Path,
) -> None:
    ledger._db.execute(
        "CREATE TRIGGER reject_incident BEFORE INSERT ON scan_incidents "
        "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END"
    )
    ledger.record_llm_event(event(retry_attempt=1))
    assert not rows(tmp_path / "test_telemetry.db", "test_events")
    ledger.close()
    metadata = rows(tmp_path / "test_telemetry.db", "ledger_metadata")[0]
    assert metadata["ingestion_status"] == "degraded"
    assert metadata["write_errors"] == 1


def test_different_scan_and_unknown_schema_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.db"
    kwargs = {"catalog": TestCatalog(), "owns_agent": lambda _aid: True}
    first = TestLedger(path, scan_id="first", **kwargs)
    first.close()
    with pytest.raises(ValueError, match="different scan"):
        TestLedger(path, scan_id="second", **kwargs)
    assert rows(path, "ledger_metadata")[0]["ingestion_status"] == "closed"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="Unsupported"):
        TestLedger(path, scan_id="first", **kwargs)


def test_estimator_failure_marks_accounting_incomplete(
    ledger: TestLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_estimator(*_args: Any) -> float:
        raise RuntimeError("synthetic price adapter failure")

    monkeypatch.setattr(test_ledger, "estimate_usage_cost", broken_estimator)
    ledger.record_llm_event(event(cost_usd=None))
    ledger.close()
    metadata = rows(tmp_path / "test_telemetry.db", "ledger_metadata")[0]
    assert metadata["ingestion_status"] == "degraded"
    assert metadata["write_errors"] == 1


def test_close_waits_for_an_inflight_callback(
    ledger: TestLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing, release = threading.Event(), threading.Event()

    def slow_estimator(*_args: Any) -> float:
        pricing.set()
        assert release.wait(timeout=5)
        return 0.02

    monkeypatch.setattr(test_ledger, "estimate_usage_cost", slow_estimator)
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback = pool.submit(ledger.record_llm_event, event(cost_usd=None))
        assert pricing.wait(timeout=5)
        closing = pool.submit(ledger.close)
        try:
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.05)
        finally:
            release.set()
        callback.result(timeout=5)
        closing.result(timeout=5)
    assert rows(tmp_path / "test_telemetry.db", "test_summary")[0]["total_cost_usd"] == 0.02
