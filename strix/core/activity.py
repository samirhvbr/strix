"""A bounded, redacted window onto what a run's agents are doing: said, called and got back.

The native viewer reads the whole transcript with no redaction, because it runs on the operator's
own machine and shows it to the operator. A page that embeds the run (the SHVIA's Pentest screen)
needs something smaller and safer: a short window of recent rows, each clipped, with the credentials
that tool output commonly carries masked, and nothing the page could not safely render (control
bytes, images).

Read-only. It opens ``agents.db`` the way the TUI history does (``mode=ro``, so it never locks the
WAL file the running scan is writing) and touches nothing else but the small ``agents.json`` for
names.

Masking is pattern based and best effort: it cannot know that an arbitrary string from a target is
a secret. That is why the window is clipped, never stored by this module, and meant to be shown to
the person who owns the run and to nobody else.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from strix.core.paths import runtime_state_dir
from strix.interface.terminal_text import sanitize_terminal_text
from strix.interface.tui.live_view import _is_internal_agent_turn


if TYPE_CHECKING:
    from pathlib import Path


SCHEMA = 1
DEFAULT_LIMIT = 80
MAX_LIMIT = 200
# Redaction runs on this many characters BEFORE the final clip, so a secret that straddles the clip
# is masked whole instead of leaving its first half on screen.
_REDACT_WINDOW = 4_000
_CLIP = {"say": 700, "user": 700, "call": 400, "result": 600}
_MAX_AGENTS = 64
_MASK = "[REDACTED_SECRET]"

_SECRETS = re.compile(
    # Header-style lines: the value goes, up to the end of the line or the quote that closes it,
    # so `curl -H 'Authorization: ...' https://target/` keeps what follows readable.
    r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*)"
    r"[^\r\n'\"]+"
    # name=value / name: value pairs with a name that says it is a secret.
    r"|(\b[\w.-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd|"
    r"secret|private[_-]?key)[\w.-]*\s*[=:]\s*[\"']?)[^\s\"',;&]+"
)
_TOKENS = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
_INLINE_IMAGE = re.compile(r"data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=]{64,}")


def _mask(text: str) -> str:
    text = _INLINE_IMAGE.sub("[image]", text)
    text = _SECRETS.sub(lambda match: (match[1] or match[2]) + _MASK, text)
    return _TOKENS.sub(_MASK, text)


def _clip(text: str, kind: str) -> tuple[str, bool]:
    """Mask on a wider window, make control bytes visible, then cut to the row's own limit."""
    wide = text[:_REDACT_WINDOW]
    cut = len(text) > _REDACT_WINDOW
    cleaned = sanitize_terminal_text(_mask(wide)).replace("\\x0a", "\n").replace("\\x09", " ")
    limit = _CLIP[kind]
    if len(cleaned) > limit:
        return cleaned[:limit].rstrip() + "…", True
    return cleaned, cut


def _text_of(content: Any) -> str:
    """The text of a message or tool output; anything that is not text is named, never embedded."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part for part in (_text_of(item) for item in content) if part)
    if isinstance(content, dict):
        return _object_text(content)
    return "" if content is None else str(content)


def _object_text(content: dict[str, Any]) -> str:
    text = content.get("text")
    if "image" in str(content.get("type") or ""):
        return "[image]"
    if isinstance(text, str):
        return text
    if "output" in content:
        return _text_of(content["output"])
    return json.dumps(content, default=str, ensure_ascii=False)[:_REDACT_WINDOW]


def _call_text(item: dict[str, Any]) -> str:
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, default=str, ensure_ascii=False)[:_REDACT_WINDOW]


def _project(row_id: int, agent_id: str, item: dict[str, Any], at: str) -> dict[str, Any] | None:
    kind: str
    tool: str | None = None
    kind_of = item.get("type")
    role = item.get("role")
    if role in {"user", "assistant"} and kind_of in {None, "message"}:
        kind, text = ("say" if role == "assistant" else "user"), _text_of(item.get("content"))
    elif kind_of == "function_call":
        kind, text, tool = "call", _call_text(item), str(item.get("name") or "tool")
    elif kind_of == "function_call_output":
        kind, text = "result", _text_of(item.get("output"))
    else:
        return None
    if not text.strip():
        return None
    clipped, truncated = _clip(text, kind)
    row: dict[str, Any] = {"id": row_id, "at": at, "agent": agent_id, "kind": kind, "text": clipped}
    if tool:
        row["tool"] = tool
    if truncated:
        row["truncated"] = True
    return row


def _agents(run_dir: Path) -> list[dict[str, str]]:
    try:
        data = json.loads((runtime_state_dir(run_dir) / "agents.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("statuses"), dict):
        return []
    raw_names = data.get("names")
    names: dict[str, Any] = raw_names if isinstance(raw_names, dict) else {}
    listed = []
    for agent_id, status in list(data["statuses"].items())[:_MAX_AGENTS]:
        if not isinstance(agent_id, str):
            continue
        name = names.get(agent_id, agent_id)
        listed.append(
            {
                "id": agent_id,
                "name": sanitize_terminal_text(str(name))[:80],
                "status": sanitize_terminal_text(str(status))[:24],
            }
        )
    return listed


def _timestamp(value: Any) -> str:
    # SQLite stores "YYYY-MM-DD HH:MM:SS" in UTC; the page wants an unambiguous instant.
    text = str(value or "").strip()
    return text.replace(" ", "T", 1) + "Z" if text and "T" not in text and "Z" not in text else text


def read_activity(run_dir: Path, *, after: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Up to ``limit`` stored rows (the newest, or the ones after ``after``), oldest first.

    The limit counts stored rows, so a window can show fewer events than ``limit`` when some rows
    are guidance or empty. ``last_id`` is the cursor to send back as ``after``; it advances past
    rows that were skipped (system guidance, an agent's opening task, empty rows), so the next call
    never rescans them. ``more`` says rows beyond this window already exist.
    """
    if not 1 <= limit <= MAX_LIMIT or after < 0:
        raise ValueError("invalid activity window")
    database = runtime_state_dir(run_dir) / "agents.db"
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "agents": _agents(run_dir),
        "events": [],
        "last_id": after,
        "more": False,
    }
    if not database.is_file():
        return result
    # Read-only: the scan may be writing this WAL database from another process right now.
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, check_same_thread=False)
    try:
        if after:
            rows = connection.execute(
                "select id, session_id, message_data, created_at from agent_messages "
                "where id > ? order by id limit ?",
                (after, limit + 1),
            ).fetchall()
        else:
            rows = connection.execute(
                "select id, session_id, message_data, created_at from agent_messages "
                "order by id desc limit ?",
                (limit + 1,),
            ).fetchall()[::-1]
        # Only for the agents that appear in this window, so the cost does not grow with the run.
        firsts = {
            agent: connection.execute(
                "select min(id) from agent_messages where session_id = ?", (agent,)
            ).fetchone()[0]
            for agent in {row[1] for row in rows}
        }
    finally:
        connection.close()
    if len(rows) > limit:
        result["more"] = True
        # Newest window: drop the oldest extra row; incremental one: drop the newest extra row.
        rows = rows[1:] if not after else rows[:limit]
    for row_id, agent_id, data, created_at in rows:
        result["last_id"] = max(result["last_id"], int(row_id))
        try:
            item = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        if item.get("role") == "user" and row_id == firsts.get(agent_id):
            continue  # the agent's own opening task, not something the operator typed
        if item.get("role") == "user" and _is_internal_agent_turn(_text_of(item.get("content"))):
            continue
        row = _project(int(row_id), str(agent_id), item, _timestamp(created_at))
        if row is not None:
            result["events"].append(row)
    return result
