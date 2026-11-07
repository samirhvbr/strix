"""A bounded, redacted window onto what a run's agents are doing: said, called and got back.

The native viewer reads the whole transcript with no redaction, because it runs on the operator's
own machine and shows it to the operator. A page that embeds the run (the SHVIA's Pentest screen)
needs something smaller and safer: a short window of recent rows, each clipped, with the credentials
that tool output commonly carries masked, and nothing the page could not safely render (control
bytes, images).

Read-only. It opens ``agents.db`` the way the TUI history does (``mode=ro``, so it never writes the
database or locks the WAL file the running scan is writing) and touches nothing else but the small
``agents.json`` for names. On a database whose run has ended, SQLite itself may create the empty
``-shm`` and ``-wal`` files beside it; this module never writes into them.

Masking is pattern based and best effort: it cannot know that an arbitrary string from a target is
a secret. That is why the window is clipped, never stored by this module, and meant to be shown to
the person who owns the run and to nobody else.

Every field is also normalised to what the Desktop accepts (``activity.rs``: ids up to 128
characters, names up to 80, no empty field, no control bytes, no lone surrogates). The Desktop
refuses the whole answer when one row is outside those bounds, and then the cursor never advances,
so a single odd row would freeze the window until the run ends.
"""

from __future__ import annotations

import hashlib
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
# What the Desktop accepts for each short field (`short` in activity.rs). One value outside these
# makes it refuse the whole answer, and a refused answer never advances the cursor.
_MAX_ID = 128
_MAX_TOOL = 128
_MAX_NAME = 80
_MAX_STATUS = 24
_MAX_AT = 40
_EPOCH = "1970-01-01T00:00:00Z"
_MASK = "[REDACTED_SECRET]"

# A name that says it is a secret has at most 32 characters either side of the word. Both sides are
# bounded on purpose: with `[\w.-]*` a line of `secret.secret.…` (4,000 characters) took 4.5 s, and
# four of them overran the Desktop's 15 s deadline.
_NAME = r"[\w.-]{0,32}"
_WORD = (
    r"(?:api[_-]?key|access[_-]?key|account[_-]?key|secret[_-]?key|private[_-]?key"
    # `max_tokens`, `total_tokens` and `tokenizer` are not credentials; `API_TOKEN` is.
    r"|token(?-i:(?![a-z]))"
    # `pass=` and `PASS:` are; `bypass:` and `passed:` are not.
    r"|(?<![a-z])pass(?-i:(?![a-z]))|passw(?:or)?d|secret|credentials?"
    r"|session[_-]?id|phpsessid|connect\.sid|amz[_-]?signature)"
)
# A quote, or the escaped quote of JSON-encoded tool arguments.
_QUOTE = r"\\?[\"']"
_SECRETS = re.compile(
    # 1. Header lines: the value goes, up to the end of the line or the quote that closes it, so
    #    `curl -H 'Authorization: ...' https://target/` keeps what follows readable. The name may
    #    follow a literal `\n` (JSON-encoded arguments, where `\b` finds no boundary) and may
    #    itself be quoted (`"Authorization": "..."`).
    r"(?P<header>(?:(?<![A-Za-z0-9])|(?<=\\n)|(?<=\\r))"
    r"(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)"
    r"\\?[\"']?\s{0,8}:\s{0,8}\\?[\"']?)[^\r\n'\"\\]+"
    # 2. name=value, name: value and "name":"value" pairs whose name says it is a secret. A quoted
    #    value runs to its closing quote (a password may hold spaces, `&` or `;`), an unquoted one
    #    to the next separator, and an object or an array is never a value.
    r"|(?P<pair>" + _NAME + _WORD + _NAME + r"\\?[\"']?\s{0,8}[=:]\s{0,8}(?P<q>" + _QUOTE + r")?)"
    r"(?(q)(?:(?!(?P=q))[^\r\n])+|(?![{\[])[^\s\"',;&}\\]+)"
    # 3. user:password@ in a URL (postgres://, redis://, mongodb+srv://).
    r"|(?P<url>\b[a-z][a-z0-9+.-]{1,15}://[^\s/@:]{0,64}:)[^\s/@]{1,128}(?=@)"
    # 4. Command-line forms that carry a secret with no name before it. `-u` stays out of the way
    #    of a URL (`sqlmap -u http://...`) and a bare `-p` is never matched (nmap's is a port).
    r"|(?P<curl_user>\s(?:-u|--user)[ =]\s{0,4}[\"']?[^\s:/\"']{1,64}:)(?!//)[^\s\"']+"
    r"|(?P<cookie_flag>\s(?:-b|--cookie)(?:\s{1,4}|=)\s{0,4}(?P<cq>" + _QUOTE + r")?)"
    r"(?(cq)(?:(?!(?P=cq))[^\r\n]){1,512}|[^\s\"'=]{1,64}=\S{1,256})"
    r"|(?P<secret_flag>(?<![\w-])--(?:[a-z]{1,12}-)?"
    r"(?:password|passwd|pass|token|secret|api-?key|auth-?token)\s+[\"']?)(?!-)[^\s\"']+"
    r"|(?P<pass_flag>\bsshpass\s+-p\s*[\"']?|\s-l\s+\S{1,64}\s+-p\s+[\"']?)[^\s\"']+",
    re.IGNORECASE,
)
_PREFIX_GROUPS = (
    "header",
    "pair",
    "url",
    "curl_user",
    "cookie_flag",
    "secret_flag",
    "pass_flag",
)
# Credentials that carry their own prefix, wherever they appear.
_TOKENS = re.compile(
    r"\b(?i:Bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(?i:Basic)\s+[A-Za-z0-9+/]{16,}={0,2}"
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"
    r"|\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{22,}"
    r"|\bgl(?:pat|dt|rt|ptt|ft|soat|cbt|imt|agent|oas)-[A-Za-z0-9_-]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"|\bnpm_[A-Za-z0-9]{30,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
_INLINE_IMAGE = re.compile(r"data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=]{64,}")


def _redact(match: re.Match[str]) -> str:
    """Keep what says what it is (the header, the name, the flag) and drop the value."""
    for group in _PREFIX_GROUPS:
        prefix = match.group(group)
        if prefix is not None:
            return prefix + _MASK
    return _MASK


def _mask(text: str) -> str:
    text = _INLINE_IMAGE.sub("[image]", text)
    text = _SECRETS.sub(_redact, text)
    return _TOKENS.sub(_MASK, text)


def _visible(value: Any) -> str:
    """Control bytes made visible, lone surrogates replaced: text a JSON consumer can parse."""
    return sanitize_terminal_text(value).encode("utf-8", "replace").decode("utf-8")


def _short(value: Any, limit: int, fallback: str) -> str:
    """A short field the Desktop accepts: visible, bounded and never empty."""
    text = _visible(value)
    return (text if text.strip() else fallback)[:limit]


def _identifier(value: Any) -> str:
    """An agent id as the Desktop accepts it, the same in the agent list and in the rows."""
    text = _visible(value)
    if not text.strip():
        return "agent"
    if len(text) <= _MAX_ID:
        return text
    # Cut, but keep two long ids that share their first characters apart.
    digest = hashlib.blake2b(text.encode(), digest_size=4).hexdigest()
    return f"{text[: _MAX_ID - 9]}~{digest}"


def _clip(text: str, kind: str) -> tuple[str, bool]:
    """Mask on a wider window, make control bytes visible, then cut to the row's own limit."""
    wide = text[:_REDACT_WINDOW]
    cut = len(text) > _REDACT_WINDOW
    cleaned = _visible(_mask(wide)).replace("\\x0a", "\n").replace("\\x09", " ")
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


def _project(row_id: int, agent: str, item: dict[str, Any], at: str) -> dict[str, Any] | None:
    kind: str
    tool: str | None = None
    kind_of = item.get("type")
    role = item.get("role")
    if role in {"user", "assistant"} and kind_of in {None, "message"}:
        kind, text = ("say" if role == "assistant" else "user"), _text_of(item.get("content"))
    elif kind_of == "function_call":
        kind, text = "call", _call_text(item)
        tool = _short(item.get("name") or "tool", _MAX_TOOL, "tool")
    elif kind_of == "function_call_output":
        kind, text = "result", _text_of(item.get("output"))
    else:
        return None
    if not text.strip():
        return None
    clipped, truncated = _clip(text, kind)
    row: dict[str, Any] = {"id": row_id, "at": at, "agent": agent, "kind": kind, "text": clipped}
    if tool:
        row["tool"] = tool
    if truncated:
        row["truncated"] = True
    return row


def _agents(run_dir: Path) -> list[dict[str, str]]:
    try:
        data = json.loads((runtime_state_dir(run_dir) / "agents.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Names are a convenience: an unreadable file is an empty list, never a refused answer.
        return []
    if not isinstance(data, dict) or not isinstance(data.get("statuses"), dict):
        return []
    raw_names = data.get("names")
    names: dict[str, Any] = raw_names if isinstance(raw_names, dict) else {}
    listed = []
    for agent_id, status in list(data["statuses"].items())[:_MAX_AGENTS]:
        if not isinstance(agent_id, str):
            continue
        identifier = _identifier(agent_id)
        # A name is text an agent wrote, so it can carry a credential too; an empty one is the id.
        name = _mask(_visible(names.get(agent_id, agent_id))[:_REDACT_WINDOW])
        listed.append(
            {
                "id": identifier,
                "name": _short(name, _MAX_NAME, identifier),
                "status": _short(status, _MAX_STATUS, "unknown"),
            }
        )
    return listed


def _timestamp(value: Any, previous: str) -> str:
    # SQLite stores "YYYY-MM-DD HH:MM:SS" in UTC; the page wants an unambiguous instant.
    text = _visible(value).strip()
    if text and "T" not in text and "Z" not in text:
        text = text.replace(" ", "T", 1) + "Z"
    return text[:_MAX_AT] or previous


def read_activity(run_dir: Path, *, after: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Up to ``limit`` stored rows (the newest, or the ones after ``after``), oldest first.

    The limit counts stored rows, so a window can show fewer events than ``limit`` when some rows
    are guidance or empty. ``last_id`` is the cursor to send back as ``after``; it advances past
    rows that were skipped (system guidance, an agent's opening task, empty rows), so the next call
    never rescans them. ``more`` says rows beyond this window already exist.

    The cursor is the stored row id, which assumes rows are only ever appended. A compaction of an
    agent's context is the one writer that is not: it deletes the agent's rows and inserts the
    summary and the recent rows again with new ids, so after one the same recent rows can come once
    more. A consumer that keeps the rows it has shown must tolerate that repeat.
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
    # A row that is not valid UTF-8 would otherwise make every read of this window fail.
    connection.text_factory = lambda raw: raw.decode("utf-8", "replace")
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
    previous_at = _EPOCH
    for row_id, agent_id, data, created_at in rows:
        result["last_id"] = max(result["last_id"], int(row_id))
        try:
            item = json.loads(data)
        except (TypeError, ValueError):  # not JSON, or a BLOB that is not UTF-8
            continue
        if not isinstance(item, dict):
            continue
        if item.get("role") == "user" and row_id == firsts.get(agent_id):
            continue  # the agent's own opening task, not something the operator typed
        if item.get("role") == "user" and _is_internal_agent_turn(_text_of(item.get("content"))):
            continue
        at = _timestamp(created_at, previous_at)
        row = _project(int(row_id), _identifier(agent_id), item, at)
        if row is not None:
            previous_at = at
            result["events"].append(row)
    return result
