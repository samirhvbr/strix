"""Shared bounded metadata queries, independent of provider and agent initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    import sqlite3


def ledger_snapshot(
    db: sqlite3.Connection, *, parents: dict[str, Any], limit: int = 256
) -> dict[str, Any]:
    """Up to ``limit`` rows per group, with ``truncated`` set when any group had more.

    ``events`` and ``incidents`` grow for as long as a run lasts, so when they are cut they
    keep the most recent rows, still in chronological order. Until fork 1.2.47 they kept the
    oldest: past 256 events the list never changed again, and a consumer asking when the last
    model call happened got the answer of the first minutes of the run. ``tests`` and
    ``models`` are few (models are aggregates) and keep their order.
    """
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
    # The model rows are summed here from the attempts, not read from the ``model_usage`` view:
    # a ledger keeps the view it was created with, and the view never summed the cached input
    # tokens. Since fork 1.2.53 a row carries them with ``unknown_cache_attempts`` (the calls that
    # reported no cache figure), so a reader can tell a measured sum from a partial one; the same
    # rule as ``unknown_token_attempts`` for the token counts.
    models = rows(
        "SELECT test_id, agent_id, route, provider, model, cost_basis, "
        "COUNT(*) AS attempt_count, SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, SUM(cached_input_tokens) AS cached_input_tokens, "
        "SUM(total_tokens) AS total_tokens, SUM(cost_usd) AS total_cost_usd, "
        "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_cost_attempts, "
        "SUM(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_token_attempts, "
        "SUM(CASE WHEN cached_input_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_cache_attempts, "
        "SUM(duration_ms) AS provider_duration_ms FROM test_events "
        "GROUP BY test_id, agent_id, route, provider, model, cost_basis "
        "ORDER BY agent_id, model, route, provider, cost_basis LIMIT ?"
    )
    events = rows(
        "SELECT event_id, test_id, agent_id, route, provider, model, cost_basis, "
        "outcome, retry_attempt, status_code, input_tokens, output_tokens, "
        "total_tokens, cost_usd, started_at, finished_at, duration_ms "
        "FROM test_events ORDER BY started_at DESC, event_id DESC LIMIT ?"
    )
    incidents = rows(
        "SELECT incident_id, test_id, agent_id, severity, source, occurred_at "
        "FROM scan_incidents ORDER BY occurred_at DESC, incident_id DESC LIMIT ?"
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
        # Newest first from SQL, so the first ``limit`` are the most recent rows;
        # reversed, they are chronological again, as consumers have always read them.
        "events": events[:limit][::-1],
        "incidents": incidents[:limit][::-1],
        "truncated": any(len(group) > limit for group in (tests, models, events, incidents)),
    }
