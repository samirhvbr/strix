"""The activity window: what a run's agents did, bounded and with credentials masked."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.activity import MAX_LIMIT, read_activity
from strix.core.paths import runtime_state_dir
from strix.interface.activity import run_activity


if TYPE_CHECKING:
    from pathlib import Path


def _run(tmp_path: Path, rows: list[tuple[str, dict[str, Any]]]) -> Path:
    run_dir = tmp_path / "run"
    state = runtime_state_dir(run_dir)
    state.mkdir(parents=True)
    (state / "agents.json").write_text(
        json.dumps(
            {
                "statuses": {"root": "running", "scout": "waiting"},
                "names": {"root": "Root agent", "scout": "Scout\x1b[31m"},
            }
        ),
        encoding="utf-8",
    )
    connection = sqlite3.connect(state / "agents.db")
    try:
        connection.execute(
            "create table agent_messages (id integer primary key, session_id text, "
            "message_data text, created_at text)"
        )
        for index, (agent, item) in enumerate(rows, start=1):
            connection.execute(
                "insert into agent_messages (id, session_id, message_data, created_at) "
                "values (?, ?, ?, ?)",
                (index, agent, json.dumps(item), f"2026-11-05 10:00:{index % 60:02d}"),
            )
        connection.commit()
    finally:
        connection.close()
    return run_dir


def _session() -> list[tuple[str, dict[str, Any]]]:
    command = {
        "command": "curl -H 'Authorization: Bearer abc.def.ghi-123456' https://t.example/login"
    }
    return [
        ("root", {"role": "user", "content": "Test https://target.example thoroughly."}),
        ("root", {"role": "assistant", "content": "Starting with the login form."}),
        (
            "root",
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "terminal_execute",
                "arguments": json.dumps(command),
            },
        ),
        (
            "root",
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "HTTP/1.1 200 OK\nSet-Cookie: sid=hunter2; Path=/\n\x1b[2Jbody",
            },
        ),
        ("root", {"role": "user", "content": "[Message from scout]: found /admin"}),
        ("root", {"role": "user", "content": "also look at password reset"}),
    ]


def test_shows_what_was_said_and_called_but_not_the_task_or_system_guidance(tmp_path: Path) -> None:
    window = read_activity(_run(tmp_path, _session()))

    assert [(row["kind"], row.get("tool")) for row in window["events"]] == [
        ("say", None),
        ("call", "terminal_execute"),
        ("result", None),
        ("user", None),
    ]
    assert window["events"][0]["at"] == "2026-11-05T10:00:02Z"
    assert window["events"][-1]["text"] == "also look at password reset"
    # The opening task and the coordinator's guidance are not the operator's words.
    shown = " ".join(row["text"] for row in window["events"])
    assert "thoroughly" not in shown and "[Message from" not in shown
    assert window["last_id"] == 6
    assert [agent["name"] for agent in window["agents"]] == ["Root agent", "Scout\\x1b[31m"]


def test_masks_credentials_and_makes_control_bytes_harmless(tmp_path: Path) -> None:
    window = read_activity(_run(tmp_path, _session()))
    text = json.dumps(window)

    for secret in ("abc.def.ghi-123456", "sid=hunter2"):
        assert secret not in text
    assert "[REDACTED_SECRET]" in text
    # Look at the rows, not at their JSON: `json.dumps` writes ESC as `\u001b`, so a check on the
    # dump passes with or without the sanitizing.
    rows = [row["text"] for row in window["events"]]
    assert not any("\x1b" in row for row in rows)
    assert "\\x1b[2Jbody" in rows[2]  # shown, not dropped: the person sees the target sent it
    # The header's value goes, not the rest of the command line: the request stays readable.
    assert "https://t.example/login" in text


def test_a_secret_that_straddles_the_clip_is_masked_whole(tmp_path: Path) -> None:
    key = "sk-" + "A1b2C3d4" * 6
    # The key starts a few characters before the 600-character result clip and runs past it.
    output = "x" * 594 + " " + key + " tail"
    rows = [("root", {"type": "function_call_output", "output": output})]
    window = read_activity(_run(tmp_path, rows))

    row = window["events"][0]
    assert row["truncated"] is True
    assert "sk-" not in row["text"]
    assert len(row["text"]) <= 601


def test_asks_only_for_what_is_new_and_for_a_bounded_window(tmp_path: Path) -> None:
    rows: list[tuple[str, dict[str, Any]]] = [("root", {"role": "user", "content": "task"})]
    rows += [("root", {"role": "assistant", "content": f"step {n}"}) for n in range(1, 31)]
    run_dir = _run(tmp_path, rows)

    newest = read_activity(run_dir, limit=10)
    assert [row["text"] for row in newest["events"]] == [f"step {n}" for n in range(21, 31)]
    assert newest["more"] is True
    assert newest["last_id"] == 31

    nothing = read_activity(run_dir, after=newest["last_id"])
    assert nothing["events"] == [] and nothing["last_id"] == 31 and nothing["more"] is False

    first_page = read_activity(run_dir, after=1, limit=10)
    assert [row["text"] for row in first_page["events"]][:2] == ["step 1", "step 2"]
    assert first_page["more"] is True
    assert first_page["last_id"] == 11

    with pytest.raises(ValueError, match="invalid activity window"):
        read_activity(run_dir, limit=MAX_LIMIT + 1)


def test_a_run_with_no_database_yet_is_an_empty_window(tmp_path: Path) -> None:
    window = read_activity(tmp_path)

    assert window["events"] == [] and window["agents"] == [] and window["last_id"] == 0


def test_the_command_prints_one_json_line_and_says_nothing_private_when_it_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run(tmp_path, _session())

    # The limit counts stored rows, skipped ones included: rows 4, 5 (guidance, skipped) and 6.
    assert run_activity([str(run_dir), "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
    assert [row["kind"] for row in json.loads(out)["events"]] == ["result", "user"]

    (runtime_state_dir(run_dir) / "agents.db").write_bytes(b"not a database")
    assert run_activity([str(run_dir)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == '{"error":"activity_unavailable"}'


def test_an_inline_image_is_named_and_never_embedded(tmp_path: Path) -> None:
    blob = "data:image/png;base64," + "A" * 200
    rows: list[tuple[str, dict[str, Any]]] = [
        ("root", {"type": "function_call_output", "output": f"screenshot {blob} done"}),
        ("root", {"type": "function_call_output", "output": [{"type": "input_image", "x": blob}]}),
    ]

    shown = [row["text"] for row in read_activity(_run(tmp_path, rows))["events"]]

    assert shown == ["screenshot [image] done", "[image]"]


def test_the_database_is_opened_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = _run(tmp_path, _session())
    opened: list[tuple[str, Any]] = []
    real_connect = sqlite3.connect

    def spy(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append((str(database), kwargs.get("uri")))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    read_activity(run_dir)

    assert len(opened) == 1
    database, uri = opened[0]
    assert uri is True and database.endswith("agents.db?mode=ro")


def test_a_scan_keeps_writing_while_the_window_is_read(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, _session())
    insert = (
        "insert into agent_messages (id, session_id, message_data, created_at) "
        "values (?, 'root', ?, '2026-11-05 10:01:00')"
    )
    writer = sqlite3.connect(runtime_state_dir(run_dir) / "agents.db")
    try:
        writer.execute("pragma journal_mode=wal")
        writer.execute(insert, (100, json.dumps({"role": "assistant", "content": "written live"})))
        writer.commit()

        assert read_activity(run_dir)["events"][-1]["text"] == "written live"

        # The reader took nothing the writer needs: it commits again right after a read.
        writer.execute(insert, (101, json.dumps({"role": "assistant", "content": "and again"})))
        writer.commit()
        assert read_activity(run_dir, after=100)["events"][-1]["text"] == "and again"
    finally:
        writer.close()


def test_after_a_compaction_the_recent_rows_come_again_under_new_ids(tmp_path: Path) -> None:
    """The cursor is the stored row id and a compaction is the one writer that is not append-only.

    `replace_session_items` (strix/core/sessions.py) deletes an agent's rows and inserts the
    checkpoint and the recent rows again, with new ids, so a consumer that follows the cursor sees
    the recent rows twice. The window states that limit (docs/PENTEST/activity-window.md); this
    test pins it, so lifting it (a stable key and a consumer that drops repeats) changes it on
    purpose.
    """
    task: tuple[str, dict[str, Any]] = ("root", {"role": "user", "content": "task"})
    steps = [("root", {"role": "assistant", "content": f"step {n}"}) for n in range(1, 7)]
    run_dir = _run(tmp_path, [task, *steps])
    cursor = read_activity(run_dir)["last_id"]

    checkpoint = {"role": "user", "content": "<conversation-checkpoint>steps 1 to 4</conversation>"}
    recent = [item for _, item in steps[-2:]]
    database = sqlite3.connect(runtime_state_dir(run_dir) / "agents.db")
    try:
        database.execute("delete from agent_messages where session_id = 'root'")
        for new_id, item in enumerate([checkpoint, *recent], start=cursor + 1):
            database.execute(
                "insert into agent_messages (id, session_id, message_data, created_at) "
                "values (?, 'root', ?, '2026-11-05 10:02:00')",
                (new_id, json.dumps(item)),
            )
        database.commit()
    finally:
        database.close()

    again = read_activity(run_dir, after=cursor)

    # The checkpoint is the agent's first stored row now, so it is skipped like an opening task;
    # the two recent rows were already shown and come with ids past the old cursor.
    assert [row["text"] for row in again["events"]] == ["step 5", "step 6"]
    assert all(row["id"] > cursor for row in again["events"])
    assert again["last_id"] == cursor + 3
