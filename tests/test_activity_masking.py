"""The activity window's masking and field bounds.

Masking is the only barrier between the text a target returns and a page that shows it (ADR-048
section 3 lets the page pass the text on), so each credential format has a case of its own, the
formats that must stay readable have one too, and no input may stall the window: a line that took
4.5 s to mask made four of them overrun the Desktop's 15 s deadline.

The second half pins the other thing that can freeze the window: the Desktop refuses the WHOLE
answer when one field is out of bounds, and then its cursor never advances.
"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.activity import _mask, read_activity
from strix.core.paths import runtime_state_dir
from strix.interface.activity import run_activity


if TYPE_CHECKING:
    from pathlib import Path


def _fake(*parts: str) -> str:
    """A credential put together at run time, so a secret scanner finds no whole token here."""
    return "".join(parts)


_SLACK = _fake("xoxb-", "1234567890-abcdefghijklmnop")
_GITLAB = _fake("glpat-", "abcdefghijklmnopqrst")
_STRIPE = _fake("sk_live_", "4eC39HqLyjWDarjtT1zdp7dc")
_GOOGLE = _fake("AIza", "SyA-1234567890abcdefghijklmnopqrstu")
_GITHUB_PAT = _fake("github_pat_", "11ABCDEFG0abcdefghijkl_abcdefghijklmnopqrstuvwxyz0123456789")
_NPM = _fake("npm_", "abcdefghijklmnopqrstuvwxyz0123456789")
_SENDGRID = _fake("SG.", "abcdefghijklmnop.qrstuvwxyz012345")
_AWS_KEY_ID = _fake("AKIA", "IOSFODNN7EXAMPLE")
_AWS_TEMP_KEY_ID = _fake("ASIA", "IOSFODNN7EXAMPLE")
_PEM_BEGIN = _fake("-----BEGIN ", "PRIVATE KEY-----")
_PEM_END = _fake("-----END ", "PRIVATE KEY-----")

# (id, text, fragments that must be gone, text that must stay readable)
_SECRET_FORMATS = [
    # The formats the review measured as leaking, one by one.
    (
        "json-password",
        '{"username":"admin","password":"Tr0ub4dor&3Zq w0rdPart;tail9"}',
        ("Tr0ub4dor", "3Zq", "w0rdPart", "tail9"),
        '"username":"admin"',
    ),
    (
        "query-token",
        "GET /api/items?token=9f8e7d6c5b4a&page=2 HTTP/1.1",
        ("9f8e7d6c5b4a",),
        "page=2",
    ),
    (
        "url-userinfo",
        "psql postgres://app:s3nh4-longa@db.internal:5432/app -c 'select 1'",
        ("s3nh4-longa",),
        "db.internal",
    ),
    (
        "env-api-token",
        "API_TOKEN=7f8a9b0c1d2e3f4a5b6c ./run.sh",
        ("7f8a9b0c1d2e3f4a5b6c",),
        "./run.sh",
    ),
    (
        "curl-cookie-flag",
        "curl -s -b 'session=abcdef0123456789' https://t.example/me",
        ("abcdef0123456789",),
        "https://t.example/me",
    ),
    (
        "curl-user",
        "curl -s -u admin:Sup3rSecret https://t.example/admin",
        ("Sup3rSecret",),
        "https://t.example/admin",
    ),
    (
        "slack",
        "echo " + _SLACK + " >> notes",
        (_SLACK,),
        ">> notes",
    ),
    (
        "gitlab",
        "export GL=" + _GITLAB,
        (_GITLAB,),
        "export GL=",
    ),
    (
        "stripe",
        "STRIPE=" + _STRIPE,
        (_STRIPE,),
        "STRIPE=",
    ),
    (
        "google",
        "GET https://maps.example/api?key=" + _GOOGLE + "&q=x",
        (_GOOGLE,),
        "&q=x",
    ),
    (
        # A literal backslash-n, as in JSON-encoded tool arguments: `\b` finds no boundary there.
        "basic-header-in-json-arguments",
        json.dumps(
            {
                "command": (
                    "curl -s https://t.example/ "
                    "-H 'X-Trace: 1\nAuthorization: Basic dXNlcjpwYXNzd29yZA==' -d x"
                )
            }
        ),
        ("dXNlcjpwYXNzd29yZA==",),
        "https://t.example/",
    ),
    (
        "cookie-header-in-json-arguments",
        json.dumps(
            {
                "command": (
                    "curl https://t.example/ -H 'Accept: x\nCookie: sid=c0ffee1234; theme=dark'"
                )
            }
        ),
        ("c0ffee1234",),
        "https://t.example/",
    ),
    # Other formats a pentest run meets.
    (
        "quoted-password-with-spaces",
        "export DB_PASSWORD='p@ss w0rd; rmX9'",
        ("p@ss", "w0rd", "rmX9"),
        "export DB_PASSWORD=",
    ),
    (
        "client-secret-in-a-form",
        "grant_type=client_credentials&client_id=abc&client_secret=Zx9Yw8Vv7Uu6Tt5&scope=read",
        ("Zx9Yw8Vv7Uu6Tt5",),
        "scope=read",
    ),
    ("csrf-header", "X-CSRF-Token: 5e6f7a8b9c0d1e2f", ("5e6f7a8b9c0d1e2f",), "X-CSRF-Token"),
    (
        "mongodb-srv",
        "mongodb+srv://user:p%40ssX7@cluster0.mongodb.net/db",
        ("p%40ssX7",),
        "cluster0.mongodb.net",
    ),
    (
        "redis-with-no-user",
        "redis-cli -u redis://:hunter2hunter2@cache:6379 ping",
        ("hunter2hunter2",),
        "cache:6379",
    ),
    (
        "github-fine-grained-token",
        "GITHUB_PAT=" + _GITHUB_PAT,
        (_GITHUB_PAT,),
        "GITHUB_PAT=",
    ),
    (
        "aws-temporary-key-id",
        "temporary key " + _AWS_TEMP_KEY_ID + " issued by sts",
        (_AWS_TEMP_KEY_ID,),
        "issued by sts",
    ),
    (
        "aws-secret-with-spaces-around-the-equals",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",),
        "aws_secret_access_key",
    ),
    (
        "sendgrid",
        "mail key " + _SENDGRID + " sent",
        (_SENDGRID,),
        "mail key",
    ),
    (
        "npm-token",
        "//registry.example/:_authToken=" + _NPM,
        (_NPM,),
        "//registry.example/",
    ),
    (
        "json-camel-case-token",
        '{"accessToken":"abc123.def456","expiresIn":3600}',
        ("abc123.def456",),
        '"expiresIn":3600',
    ),
    (
        "json-api-key-after-a-space",
        '{"api_key": "AbCd1234EfGh5678"}',
        ("AbCd1234EfGh5678",),
        "api_key",
    ),
    ("yaml-password", "password: hunter2hunter2", ("hunter2hunter2",), "password:"),
    (
        "cli-flag-with-a-space",
        "mysql --user=root --password hunter2hunter2 -e 'select 1'",
        ("hunter2hunter2",),
        "-e 'select 1'",
    ),
    (
        "sshpass",
        "sshpass -p 'Sup3rSecret!' ssh root@10.0.0.5 id",
        ("Sup3rSecret!",),
        "ssh root@10.0.0.5",
    ),
    (
        "hydra",
        "hydra -l admin -p letmein2024 ssh://10.0.0.5",
        ("letmein2024",),
        "ssh://10.0.0.5",
    ),
    (
        "cookie-flag-without-quotes",
        "sqlmap --cookie PHPSESSID=abc123def456 -u http://t.example/?id=1",
        ("abc123def456",),
        "http://t.example/?id=1",
    ),
    (
        "cookie-flag-with-several-cookies",
        'curl --cookie "a=1; sid=zz99yy88xx77" https://t.example/',
        ("zz99yy88xx77",),
        "https://t.example/",
    ),
    (
        "bearer-in-lower-case",
        "the log said bearer abcdefghijklmnopqrstuvwx for that call",
        ("abcdefghijklmnopqrstuvwx",),
        "for that call",
    ),
    (
        "basic-credentials-outside-a-header",
        "sent Basic dXNlcjpwYXNzd29yZHB3ZA== to the proxy",
        ("dXNlcjpwYXNzd29yZHB3ZA==",),
        "to the proxy",
    ),
    (
        "presigned-url-signature",
        "X-Amz-Credential="
        + _AWS_KEY_ID
        + "%2F20261107&X-Amz-Signature=0123456789abcdef0123456789abcdef",
        ("0123456789abcdef0123456789abcdef", _AWS_KEY_ID),
        "X-Amz-Signature",
    ),
    ("php-session-id", "PHPSESSID=abc123xyz789; path=/", ("abc123xyz789",), "path=/"),
    (
        "express-session-id",
        "connect.sid=s%3Aabcdefghijklmn.opqrstuvwxyz",
        ("s%3Aabcdefghijklmn.opqrstuvwxyz",),
        "connect.sid",
    ),
    (
        "private-key-inside-json",
        json.dumps(
            {
                "private_key": _PEM_BEGIN + "\nMIIEvQIBADANBg\n" + _PEM_END + "\n",
                "client_email": "svc@p.iam",
            }
        ),
        ("MIIEvQIBADANBg",),
        "svc@p.iam",
    ),
    ("bare-pass-field", "user=admin&pass=hunter2hunter2&submit=1", ("hunter2hunter2",), "submit=1"),
    # Controls: these were masked before and must stay masked.
    (
        "control-bearer-header",
        "curl -H 'Authorization: Bearer abc.def.ghi-123456' https://t.example/login",
        ("abc.def.ghi-123456",),
        "https://t.example/login",
    ),
    ("control-password-equals", "password=hunter2", ("hunter2",), "password="),
    ("control-api-key-colon", "api_key: abcdef123456", ("abcdef123456",), "api_key:"),
    ("control-set-cookie", "Set-Cookie: sid=hunter2; Path=/", ("hunter2",), "Set-Cookie"),
    (
        "control-jwt",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1rwW1gFWFOEjXk",
        ("dBjftJeZ4CVPmB92K27uhbUJU1p1rwW1gFWFOEjXk",),
        "",
    ),
]

# Text that is not a credential and that the mask must leave exactly as it is.
_READABLE = [
    "max_tokens=4096 temperature=0.2",
    '{"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}',
    "tokenizer=gpt2 vocab=50257",
    "Trying bypass: 403 bypass with X-Original-URL",
    "the compass: north",
    "nmap -p 80,443,8080 10.0.0.5",
    'sqlmap -u "http://t.example/item.php?id=1" -p id --batch',
    "grep -b needle /var/log/syslog",
    "PWD=/home/user/work SHELL=/bin/zsh",
    "The password field is rendered as a text input.",
    "passed: 12 failed: 0",
    "ssh://git@github.com:org/repo.git",
    "http://[::1]:8080/path",
    "session cookie flags: HttpOnly; Secure",
    "Cookies: 3 set",
    "Authorization header missing; got 401",
    # An object or an array after the name is structure, not a value to hide.
    '{"token":{"kind":"opaque"},"credentials":["a","b"]}',
]

# Inputs built to make a backtracking pattern pay: each is cut to the 4,000 characters the window
# masks, and each answered in under 20 ms when measured. The old pattern needed 4.5 s for the first
# two, so the bound below (25 times the measure) fails on it and survives a slow runner.
_HOSTILE = {
    "secret-dot": "secret." * 570,
    "api-key-dot": "api_key." * 500,
    "plain-letters": "a" * 4000,
    "token": "token" * 800,
    "password": "password" * 500,
    "scheme-separators": "://" * 1300,
    "user-flag": " -u " * 1000,
    "flag-and-spaces": "--password" + " " * 3990,
    "header-names": "authorization" * 300,
    "header-and-spaces": "Authorization:" + " " * 3980,
    "quotes": '"' * 4000,
    "escaped-newlines": "\\n" * 2000,
    "brackets": "[" * 4000,
    "unclosed-quoted-value": 'password="' + "x" * 3980,
    "names-near-the-bound": ("a" * 31 + "secret") * 100,
    "bound-and-equals": ("secret" + "a" * 40 + "=") * 80,
    "bearer-and-spaces": "Bearer" + " " * 3990 + "!",
    "pem-without-end": _PEM_BEGIN * 140,
    "cookie-flags": "-b '" * 1000,
    "hydra-flags": " -l a -p " * 500,
}
_MASK_BUDGET_SECONDS = 0.5


@pytest.mark.parametrize(
    ("text", "fragments", "readable"),
    [pytest.param(text, gone, keep, id=name) for name, text, gone, keep in _SECRET_FORMATS],
)
def test_masks_each_credential_format_and_keeps_the_rest_readable(
    text: str, fragments: tuple[str, ...], readable: str
) -> None:
    masked = _mask(text)

    for fragment in fragments:
        assert fragment not in masked, masked
    assert "[REDACTED_SECRET]" in masked
    assert readable in masked, masked


@pytest.mark.parametrize("text", _READABLE)
def test_text_that_is_not_a_credential_is_left_alone(text: str) -> None:
    assert _mask(text) == text


@pytest.mark.parametrize("text", list(_HOSTILE.values()), ids=list(_HOSTILE))
def test_no_input_can_stall_the_mask(text: str) -> None:
    text = text[:4000]
    started = time.perf_counter()
    _mask(text)

    assert time.perf_counter() - started < _MASK_BUDGET_SECONDS


# --- What the Desktop accepts (src-tauri/src/code_bridge/pentest/activity.rs, Desktop 1.37.0) ---

_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _is_control(char: str) -> bool:
    return unicodedata.category(char) == "Cc"


def _is_surrogate(char: str) -> bool:
    return 0xD800 <= ord(char) <= 0xDFFF


def _short(value: Any, maximum: int) -> bool:
    """`short`: a non-empty string of at most `maximum` characters with no control character."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not any(_is_control(c) or _is_surrogate(c) for c in value)
    )


def _body(value: Any) -> bool:
    """`body`: at most 800 characters, and a newline is the one control character allowed."""
    return (
        isinstance(value, str)
        and len(value) <= 800
        and not any((_is_control(c) and c != "\n") or _is_surrogate(c) for c in value)
    )


def _event_refusal(event: dict[str, Any]) -> str | None:
    checks = {
        "kind": event.get("kind") in {"say", "user", "call", "result"},
        "id": isinstance(event.get("id"), int) and 1 <= event["id"] <= _MAX_SAFE_INTEGER,
        "at": _short(event.get("at"), 40),
        "agent": _short(event.get("agent"), 128),
        "text": _body(event.get("text")),
        "tool": "tool" not in event or _short(event["tool"], 128),
        "truncated": "truncated" not in event or isinstance(event["truncated"], bool),
    }
    return next(
        (f"event {field}: {event.get(field)!r}" for field, ok in checks.items() if not ok), None
    )


def _agent_refusal(agent: dict[str, Any]) -> str | None:
    limits = (("id", 128), ("name", 80), ("status", 24))
    return next(
        (
            f"agent {f}: {agent.get(f)!r}"
            for f, maximum in limits
            if not _short(agent.get(f), maximum)
        ),
        None,
    )


def _refusal(window: dict[str, Any]) -> str | None:
    """The first reason the Desktop would refuse this answer, or None when it would accept it."""
    events, agents, last_id = window.get("events"), window.get("agents"), window.get("last_id")
    structure = {
        "schema": window.get("schema") == 1,
        "size": len(json.dumps(window, ensure_ascii=False).encode()) <= 512 * 1024,
        "events": isinstance(events, list) and len(events) <= 200,
        "agents": isinstance(agents, list) and len(agents) <= 64,
        "last_id": isinstance(last_id, int) and 0 <= last_id <= _MAX_SAFE_INTEGER,
        "more": isinstance(window.get("more"), bool),
    }
    for name, ok in structure.items():
        if not ok:
            return name
    reasons = [_event_refusal(event) for event in events] + [_agent_refusal(a) for a in agents]
    return next((reason for reason in reasons if reason), None)


def _build_run(
    tmp_path: Path,
    rows: list[tuple[Any, ...]],
    *,
    statuses: dict[str, Any] | None = None,
    names: dict[str, Any] | None = None,
) -> Path:
    """A run directory with the SDK's own `agent_messages` schema (ids only ever grow)."""
    run_dir = tmp_path / "run"
    state = runtime_state_dir(run_dir)
    state.mkdir(parents=True)
    (state / "agents.json").write_text(
        json.dumps({"statuses": statuses or {"root": "running"}, "names": names or {}}),
        encoding="utf-8",
    )
    connection = sqlite3.connect(state / "agents.db")
    try:
        connection.execute(
            "create table agent_messages (id integer primary key autoincrement, "
            "session_id text not null, message_data text not null, "
            "created_at timestamp default current_timestamp)"
        )
        for row in rows:
            agent, item = row[0], row[1]
            created = row[2] if len(row) > 2 else "2026-11-05 10:00:00"
            connection.execute(
                "insert into agent_messages (session_id, message_data, created_at) "
                "values (?, ?, ?)",
                (agent, json.dumps(item), created),
            )
        connection.commit()
    finally:
        connection.close()
    return run_dir


def test_one_odd_row_never_makes_the_desktop_refuse_the_answer(tmp_path: Path) -> None:
    long_id = "agent-" + "x" * 300
    same_start = "agent-" + "x" * 300 + "-two"
    call = {"type": "function_call", "arguments": "{}"}
    rows = [
        (long_id, {"role": "assistant", "content": "from an id past 128 characters"}),
        (same_start, {"role": "assistant", "content": "from another long id"}),
        ("esc\x1b[31m", {"role": "assistant", "content": "from an id with a control byte"}),
        ("root", {**call, "name": "tool\nname\x1b" + "t" * 200}),
        ("root", {**call, "name": ""}),
        ("root", {"role": "assistant", "content": "a lone \ud800 surrogate"}, None),
        ("root", {"role": "assistant", "content": "a time that is not there"}, ""),
    ]
    run_dir = _build_run(
        tmp_path,
        rows,
        statuses={long_id: "", same_start: "running", "esc\x1b[31m": "s" * 50, "root": "running"},
        names={long_id: "", "root": "   ", same_start: "N" * 200},
    )

    window = read_activity(run_dir)

    assert _refusal(window) is None
    # Two ids that share their first 128 characters stay two ids, in the list and in the rows.
    listed = [agent["id"] for agent in window["agents"]]
    assert len(set(listed)) == len(listed) == 4
    shown = {row["agent"] for row in window["events"]}
    assert shown <= set(listed) and len(shown) == 4
    names = {agent["id"]: agent["name"] for agent in window["agents"]}
    assert names["root"] == "root" and names[listed[0]] == listed[0][:80]


def test_every_control_character_is_made_visible_but_the_newline(tmp_path: Path) -> None:
    every = "".join(chr(code) for code in range(0xA0))
    run_dir = _build_run(tmp_path, [("root", {"type": "function_call_output", "output": every})])

    window = read_activity(run_dir)

    assert _refusal(window) is None
    text = window["events"][0]["text"]
    assert not any(_is_control(c) and c != "\n" for c in text)
    assert "\\x1b" in text and "\\x00" in text and "\\x9f" in text


def test_a_lone_surrogate_does_not_make_the_command_refuse_to_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [("root", {"role": "assistant", "content": "bytes a target sent: \udcff\ud800"})]
    run_dir = _build_run(tmp_path, rows, names={"root": "Root \udc80"})

    assert run_activity([str(run_dir)]) == 0

    answer = json.loads(capsys.readouterr().out)
    assert _refusal(answer) is None
    assert "bytes a target sent" in answer["events"][0]["text"]


def test_a_secret_in_an_agents_name_is_masked_like_any_other_text(tmp_path: Path) -> None:
    run_dir = _build_run(
        tmp_path,
        [("root", {"role": "assistant", "content": "hello"})],
        names={"root": "Admin tester, password=hunter2hunter2"},
    )

    name = read_activity(run_dir)["agents"][0]["name"]

    assert "hunter2hunter2" not in name
    assert name.startswith("Admin tester")


def test_a_database_with_bytes_that_are_not_utf8_still_answers(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, [("root", {"role": "assistant", "content": "first row"})])
    bad = b'{"role": "assistant", "content": "bad \xff byte"}'
    connection = sqlite3.connect(runtime_state_dir(run_dir) / "agents.db")
    try:
        # The same bytes stored once as TEXT (not valid UTF-8) and once as a BLOB.
        connection.execute(
            "insert into agent_messages (session_id, message_data, created_at) "
            "values ('root', cast(? as text), '2026-11-05 10:00:01')",
            (bad,),
        )
        connection.execute(
            "insert into agent_messages (session_id, message_data, created_at) "
            "values ('root', ?, '2026-11-05 10:00:02')",
            (bad,),
        )
        connection.commit()
    finally:
        connection.close()

    window = read_activity(run_dir)

    assert _refusal(window) is None
    # The TEXT row is read with the bad byte replaced; the BLOB row is skipped and the cursor
    # moves past it, so neither one can hold the window back.
    assert [row["text"] for row in window["events"]] == ["first row", "bad � byte"]
    assert window["last_id"] == 3
