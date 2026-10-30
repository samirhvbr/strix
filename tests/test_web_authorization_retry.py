"""A WEB that does not answer is not a WEB that refused (PT-37, 30/10/2026).

The engine verifies the authorization before every model call. Until 1.2.45 one failed attempt
(10 s timeout, no repeat) ended the run as ``failed`` whatever the cause, so the thirty seconds of
a WEB deploy, a network blip or a laptop waking from sleep could end a run of hours. Now only a
refusal ends it at once; a WEB that does not answer is asked again for five minutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from strix.core import web_authorization
from strix.core.hooks import ReportUsageHooks
from strix.core.web_authorization import (
    WebAuthorization,
    WebAuthorizationError,
    WebAuthorizationUnavailableError,
    verify_patiently,
)
from tests.test_controlled_assessment import TOKEN
from tests.test_open_scan_authorization import open_approval


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class Web:
    """A WEB that answers from a script: an exception to raise, a status, or a body."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.requests = 0

    def handler(self, _request: httpx.Request) -> httpx.Response:
        self.requests += 1
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, json={"message": "no"})
        return httpx.Response(200, json=step)


def gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[Any]
) -> tuple[WebAuthorization, Web]:
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps({"authority": "https://web.example.invalid", "token": TOKEN}))
    path.chmod(0o600)
    web = Web(steps)
    original = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return original(transport=httpx.MockTransport(web.handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return WebAuthorization(path), web


class Time:
    """A clock the fake sleep moves, so a five-minute window costs nothing."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


UNREACHABLE = [
    pytest.param(httpx.ConnectError("refused"), id="connection refused"),
    pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
    pytest.param(502, id="502"),
    pytest.param(503, id="503 maintenance"),
    pytest.param(408, id="408"),
    pytest.param(429, id="429"),
]
ANSWERS = [pytest.param(code, id=str(code)) for code in (400, 401, 403, 404, 409, 410, 422)]


@pytest.mark.parametrize("step", UNREACHABLE)
def test_a_web_that_does_not_answer_is_unavailable_not_refused(
    step: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web_gate, _web = gate(tmp_path, monkeypatch, [step])
    with pytest.raises(WebAuthorizationUnavailableError):
        web_gate.fetch()


@pytest.mark.parametrize("code", ANSWERS)
def test_an_answer_from_the_web_is_a_refusal_that_is_never_repeated(
    code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web_gate, _web = gate(tmp_path, monkeypatch, [code])
    with pytest.raises(WebAuthorizationError) as raised:
        web_gate.fetch()
    assert not isinstance(raised.value, WebAuthorizationUnavailableError)


def test_a_garbled_answer_is_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web_gate, _web = gate(tmp_path, monkeypatch, [{"policy": "nonsense"}])
    with pytest.raises(WebAuthorizationError) as raised:
        web_gate.fetch()
    assert not isinstance(raised.value, WebAuthorizationUnavailableError)


@pytest.mark.asyncio
async def test_the_check_is_repeated_until_the_web_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = open_approval()
    web_gate, web = gate(tmp_path, monkeypatch, [approved])
    policy, _context, budget = web_gate.fetch()
    web.steps = [httpx.ConnectError("down"), 503, approved]
    web.requests = 0
    time_ = Time()
    await verify_patiently(
        lambda: web_gate.check_scope_only("scan", policy, budget),
        sleep=time_.sleep,
        clock=time_.clock,
    )
    assert web.requests == 3
    assert time_.waits == [1.0, 2.0]


@pytest.mark.asyncio
async def test_the_check_gives_up_after_the_window_and_ends_closed() -> None:
    attempts = 0

    def down() -> None:
        nonlocal attempts
        attempts += 1
        raise WebAuthorizationUnavailableError("WEB authorization expired, revoked or unavailable")

    time_ = Time()
    with pytest.raises(WebAuthorizationUnavailableError):
        await verify_patiently(down, sleep=time_.sleep, clock=time_.clock)
    assert time_.waits[:7] == [1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0]
    assert sum(time_.waits) == web_authorization.RETRY_WINDOW_SECONDS == 300.0
    assert attempts == len(time_.waits) + 1


@pytest.mark.asyncio
async def test_a_refusal_is_not_repeated() -> None:
    attempts = 0

    def refused() -> None:
        nonlocal attempts
        attempts += 1
        raise WebAuthorizationError("Execution differs from the WEB authorization")

    time_ = Time()
    with pytest.raises(WebAuthorizationError) as raised:
        await verify_patiently(refused, sleep=time_.sleep, clock=time_.clock)
    assert not isinstance(raised.value, WebAuthorizationUnavailableError)
    assert attempts == 1
    assert time_.waits == []


@pytest.mark.asyncio
async def test_neither_the_error_nor_the_log_carries_the_token_or_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    web_gate, _web = gate(tmp_path, monkeypatch, [httpx.ConnectError(f"cannot reach {TOKEN}")])
    time_ = Time()
    caplog.set_level(logging.DEBUG, logger="strix.core.web_authorization")
    with pytest.raises(WebAuthorizationUnavailableError) as raised:
        await verify_patiently(web_gate.fetch, sleep=time_.sleep, clock=time_.clock)
    everything = str(raised.value) + caplog.text + repr(raised.value.__cause__)
    assert caplog.records, "the repeats must leave a trace"
    assert TOKEN not in everything
    assert "web.example.invalid" not in everything


@pytest.mark.asyncio
async def test_stopping_the_run_ends_the_wait_at_once() -> None:
    attempts = 0

    def down() -> None:
        nonlocal attempts
        attempts += 1
        raise WebAuthorizationUnavailableError("WEB authorization expired, revoked or unavailable")

    task = asyncio.create_task(verify_patiently(down))
    while attempts == 0:
        await asyncio.sleep(0.01)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1.0
    assert attempts == 1


def test_stopping_the_run_does_not_hold_the_process_open() -> None:
    """The wait must not be a sleeping worker thread: the interpreter joins those on exit.

    The child waits twenty seconds between checks, is cancelled while it waits, and has to be
    gone long before the first wait would have ended. A worker thread sleeping through the wait
    would keep it alive for the whole twenty seconds.
    """
    child = textwrap.dedent(
        """
        import asyncio
        from strix.core import web_authorization as w

        w.RETRY_BACKOFF_SECONDS = (20.0,)
        attempts = 0

        def down():
            global attempts
            attempts += 1
            raise w.WebAuthorizationUnavailableError("down")

        async def main():
            task = asyncio.create_task(w.verify_patiently(down))
            while attempts == 0:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(main())
        """
    )
    started = time.monotonic()
    done = subprocess.run(  # noqa: S603 -- our own interpreter, a fixed script
        [sys.executable, "-c", child], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr[-500:]
    assert time.monotonic() - started < 10.0


def _hooks_context(authorize: Callable[[], None]) -> MagicMock:
    wrapper = MagicMock()
    wrapper.context = {"agent_id": "x", "parent_id": None, "authorize_assessment": authorize}
    return wrapper


@pytest.mark.asyncio
async def test_a_model_turn_waits_out_a_short_outage_and_a_refusal_still_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_authorization, "RETRY_BACKOFF_SECONDS", (0.01,))
    ledger = SimpleNamespace(get_total_llm_cost=lambda: 0.0)
    hooks = ReportUsageHooks(model="m", max_budget_usd=10.0)
    answers: list[Exception | None] = [WebAuthorizationUnavailableError("down"), None]

    def authorize() -> None:
        outcome = answers.pop(0)
        if outcome is not None:
            raise outcome

    with patch("strix.core.hooks.get_global_report_state", return_value=ledger):
        ctx = _hooks_context(authorize)
        await hooks.on_llm_start(ctx, MagicMock(), None, [])
        assert answers == []
        assert ctx.context["llm_turn"] == 1

        def refused() -> None:
            raise WebAuthorizationError("WEB authorization expired, revoked or unavailable")

        with pytest.raises(WebAuthorizationError):
            await hooks.on_llm_start(_hooks_context(refused), MagicMock(), None, [])
