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
    text = json.dumps(read_activity(_run(tmp_path, _session())))

    for secret in ("abc.def.ghi-123456", "sid=hunter2"):
        assert secret not in text
    assert "[REDACTED_SECRET]" in text
    assert "\x1b" not in text
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
