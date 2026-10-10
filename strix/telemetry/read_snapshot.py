"""Shared bounded metadata queries, independent of provider and agent initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    import sqlite3


def ledger_snapshot(
    db: sqlite3.Connection, *, parents: dict[str, Any], limit: int = 256
) -> dict[str, Any]:
    if not 1 <= limit <= 256:
        raise ValueError("invalid telemetry snapshot limit")

    def rows(query: str) -> list[dict[str, Any]]:
        cursor = db.execute(query, (limit + 1,))
        keys = [column[0] for column in cursor.description]
        return [dict(zip(keys, row, strict=True)) for row in cursor.fetchall()]

    tests = rows(
        "SELECT test_id, agent_id, vuln_class, status, started_at, ended_at, duration_s, "
        "attempt_count, replay_attempt_count, error_count, input_tokens, output_tokens, "
        "cached_input_tokens, total_tokens, total_cost_usd, unknown_token_attempts, "
        "unknown_cost_attempts, provider_duration_ms FROM test_summary ORDER BY test_id LIMIT ?"
    )
    for item in tests:
        item["parent_test_id"] = parents.get(item["agent_id"])
    models = rows(
        "SELECT test_id, agent_id, route, provider, model, cost_basis, attempt_count, "
        "input_tokens, output_tokens, total_tokens, total_cost_usd, unknown_cost_attempts, "
        "unknown_token_attempts, provider_duration_ms FROM model_usage "
        "ORDER BY agent_id, model, route, provider, cost_basis LIMIT ?"
    )
    events = rows(
        "SELECT event_id, test_id, agent_id, route, provider, model, cost_basis, "
        "outcome, retry_attempt, status_code, input_tokens, output_tokens, "
        "total_tokens, cost_usd, started_at, finished_at, duration_ms "
        "FROM test_events ORDER BY started_at, event_id LIMIT ?"
    )
    incidents = rows(
        "SELECT incident_id, test_id, agent_id, severity, source, occurred_at "
        "FROM scan_incidents ORDER BY occurred_at, incident_id LIMIT ?"
    )
    cursor = db.execute(
        "SELECT scan_id, ingestion_status, write_errors, history_started_at, history_scope, "
        "interrupted_writers FROM ledger_metadata WHERE singleton = 1"
    )
    metadata = dict(
        zip([column[0] for column in cursor.description], cursor.fetchone(), strict=True)
    )
    return {
        "metadata": metadata,
        "tests": tests[:limit],
        "models": models[:limit],
        "events": events[:limit],
        "incidents": incidents[:limit],
        "truncated": any(len(group) > limit for group in (tests, models, events, incidents)),
    }
