"""One real request, four independent sub-verdicts, a fixed response-header allowlist."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.assessment_context import FileCredentials, bind_context, parse_context
from strix.core.evidence_ledger import EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.core.web_hygiene_case import ALLOWED_RESPONSE_HEADERS
from tests.test_assessment_context import config, write_credentials


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def hygiene_server() -> Any:
    state: dict[str, Any] = {
        "redirect_to": None,
        "cors": "none",
        "cookie": "none",
        "disclosure": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _respond(self) -> None:
            # send_response_only skips Python's own automatic Server/Date headers, so the
            # "disclosure" fixture state is the only source of a Server header below.
            self.send_response_only(302 if state["redirect_to"] else 200)
            self.send_header("Date", self.date_time_string())
            if state["redirect_to"]:
                self.send_header("Location", state["redirect_to"])
            if state["cors"] in {"wildcard_with_credentials", "origin_with_credentials"}:
                origin = "*" if state["cors"] == "wildcard_with_credentials" else "https://app.test"
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
            if state["cookie"] == "safe":
                self.send_header("Set-Cookie", "session=abc; Secure; SameSite=Lax; HttpOnly")
            elif state["cookie"] == "missing_secure":
                self.send_header("Set-Cookie", "session=abc; SameSite=Lax")
            elif state["cookie"] == "samesite_none_no_secure":
                self.send_header("Set-Cookie", "session=abc; SameSite=None")
            if state["disclosure"]:
                self.send_header("Server", "nginx/1.19.0")
                self.send_header("X-Powered-By", "PHP/8.1.2")
            # Never allow-listed: must never reach a stored receipt or a check verdict.
            self.send_header("Via", "1.1 test-proxy")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            self._respond()

        def do_HEAD(self) -> None:
            self._respond()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def context_at(
    port: int,
    *,
    redirect: bool = True,
    credential: bool = True,
    canary_host: str = "canary.invalid",
) -> tuple[Any, dict[str, Any]]:
    policy, raw = config(port)
    raw["version"] = 2
    raw["operations"] = {"probe": {"method": "GET", "url": f"http://127.0.0.1:{port}/probe"}}
    web_hygiene = {"adapter": "http.web-hygiene", "version": 1, "probe_operation": "probe"}
    if redirect:
        web_hygiene |= {
            "redirect_param": "next",
            "redirect_target": f"http://{canary_host}/landing",
        }
    if not credential:
        raw["identities"]["a"]["secret_ref"] = None
    raw["cases"] = {
        "hygiene": {
            "version": 1,
            "identities": ["a"],
            "operations": ["probe"],
            "web_hygiene": web_hygiene,
        }
    }
    return policy, raw


def executor_at(
    path: Path,
    port: int,
    *,
    redirect: bool = True,
    credential: bool = True,
    canary_host: str = "canary.invalid",
) -> IdentityExecutor:
    policy, raw = context_at(
        port, redirect=redirect, credential=credential, canary_host=canary_host
    )
    context = bind_context(path, "scan", policy, raw, resuming=False)
    assert context is not None
    ledger = EvidenceLedger(
        path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda _: True,
    )
    creds_path = None
    if credential:
        creds_path = path / "creds.json"
        write_credentials(creds_path)
    return IdentityExecutor(context, FileCredentials(creds_path, "assessment"), ledger)


@pytest.mark.asyncio
async def test_clean_response_closes_all_four_checks_compliant(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, _state = hygiene_server
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["verdict"] == "compliant"
    assert result["checks"]["redirect"] == {
        "verdict": "inconclusive",
        "reason": "redirect_probe_not_declared",
    }
    assert result["checks"]["cors"]["reason"] == "no_cors_header_observed"
    assert result["checks"]["cookies"]["reason"] == "no_cookie_observed"
    assert result["checks"]["disclosure"] == {
        "verdict": "compliant",
        "reason": "no_version_header_observed",
        "headers": [],
    }
    await executor.close()


@pytest.mark.asyncio
async def test_redirect_to_the_declared_canary_is_vulnerable(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["redirect_to"] = "http://canary.invalid/landing"
    executor = executor_at(tmp_path, port, canary_host="canary.invalid")
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["verdict"] == "vulnerable"
    assert result["checks"]["redirect"] == {
        "verdict": "vulnerable",
        "reason": "redirect_followed_declared_canary",
    }
    await executor.close()


@pytest.mark.asyncio
async def test_redirect_elsewhere_or_nowhere_is_compliant(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    for name, location in [("nowhere", None), ("elsewhere", "http://app.test/home")]:
        state["redirect_to"] = location
        executor = executor_at(tmp_path / name, port)
        result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
        expected = (
            "probe_did_not_redirect"
            if location is None
            else "redirect_did_not_follow_declared_canary"
        )
        assert result["checks"]["redirect"] == {"verdict": "compliant", "reason": expected}
        await executor.close()


@pytest.mark.asyncio
async def test_cors_wildcard_with_credentials_is_vulnerable(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["cors"] = "wildcard_with_credentials"
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["checks"]["cors"] == {
        "verdict": "vulnerable",
        "reason": "wildcard_origin_with_credentials",
    }
    await executor.close()


@pytest.mark.asyncio
async def test_cors_explicit_origin_with_credentials_is_compliant(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["cors"] = "origin_with_credentials"
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["checks"]["cors"] == {
        "verdict": "compliant",
        "reason": "cors_header_present_without_risk",
    }
    await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cookie", "expected"),
    [
        ("missing_secure", "cookie_missing_secure_or_samesite"),
        ("samesite_none_no_secure", "samesite_none_without_secure"),
    ],
)
async def test_cookie_without_required_attributes_is_vulnerable(
    tmp_path: Path, hygiene_server: Any, cookie: str, expected: str
) -> None:
    port, state = hygiene_server
    state["cookie"] = cookie
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["checks"]["cookies"] == {"verdict": "vulnerable", "reason": expected}
    await executor.close()


@pytest.mark.asyncio
async def test_cookie_with_secure_and_samesite_is_compliant(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["cookie"] = "safe"
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["checks"]["cookies"] == {
        "verdict": "compliant",
        "reason": "cookie_attributes_present",
    }
    await executor.close()


@pytest.mark.asyncio
async def test_disclosure_header_present_is_vulnerable_and_names_only_the_closed_set(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["disclosure"] = True
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["checks"]["disclosure"] == {
        "verdict": "vulnerable",
        "reason": "version_header_disclosed",
        "headers": ["server", "x-powered-by"],
    }
    await executor.close()


@pytest.mark.asyncio
async def test_overall_verdict_is_vulnerable_if_any_single_check_is(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["cookie"] = "missing_secure"
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["verdict"] == "vulnerable"
    assert result["checks"]["cors"]["verdict"] == "compliant"
    assert result["checks"]["disclosure"]["verdict"] == "compliant"
    await executor.close()


@pytest.mark.asyncio
async def test_only_allowlisted_headers_reach_the_durable_receipt(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["disclosure"] = True
    state["cookie"] = "safe"
    executor = executor_at(tmp_path, port, redirect=False)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    ref = result["evidence"][0]["evidence_ref"]
    private = executor.ledger.read_private(ref, case_ref="hygiene")
    recorded = private["content"]["headers"]
    assert set(recorded) <= ALLOWED_RESPONSE_HEADERS
    assert "via" not in recorded
    assert recorded["server"] == "nginx/1.19.0"
    # The agent-facing view never carries response headers at all, regardless of the allowlist.
    assert "headers" not in executor.ledger.read(ref)["content"]
    await executor.close()


@pytest.mark.asyncio
async def test_credential_header_is_sent_and_redacted_if_ever_echoed(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, state = hygiene_server
    state["cookie"] = "safe"
    executor = executor_at(tmp_path, port, redirect=False, credential=True)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["verdict"] == "compliant"
    await executor.close()


@pytest.mark.asyncio
async def test_expired_credential_blocks_before_any_request(
    tmp_path: Path, hygiene_server: Any
) -> None:
    port, _state = hygiene_server
    policy, raw = context_at(port, redirect=False)
    context = bind_context(tmp_path, "scan", policy, raw, resuming=False)
    assert context is not None
    ledger = EvidenceLedger(
        tmp_path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda _: True,
    )
    creds_path = tmp_path / "creds.json"
    write_credentials(creds_path, expired=True)
    executor = IdentityExecutor(context, FileCredentials(creds_path, "assessment"), ledger)
    result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
    assert result["verdict"] == "inconclusive"
    assert result["checks"] == {}
    assert result["evidence"][0]["status"] == "blocked"
    await executor.close()


@pytest.mark.asyncio
async def test_unreachable_target_is_inconclusive_not_a_crash(tmp_path: Path) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        executor = executor_at(tmp_path, port, redirect=False)
        result = await executor.run_case(agent_ref="agent", case_ref="hygiene")
        assert result["verdict"] == "inconclusive"
        assert executor.ledger.summary()["obligations"]["essential_unfulfilled"] == 1
        await executor.close()


@pytest.mark.parametrize(
    ("label", "change"),
    [
        (
            "duplicate_operation",
            lambda raw: raw["cases"]["hygiene"].update(operations=["probe", "probe"]),
        ),
        ("two_identities", lambda raw: raw["cases"]["hygiene"].update(identities=["a", "b"])),
        ("post_method", lambda raw: raw["operations"]["probe"].update(method="POST")),
        (
            "only_param_set",
            lambda raw: raw["cases"]["hygiene"]["web_hygiene"].pop("redirect_target"),
        ),
        (
            "canary_inside_scope",
            lambda raw: raw["cases"]["hygiene"]["web_hygiene"].update(
                redirect_target="http://127.0.0.1:1/x"
            ),
        ),
        ("version_1", lambda raw: raw.update(version=1)),
    ],
)
def test_invalid_web_hygiene_scope_is_rejected(label: str, change: Any) -> None:
    policy, raw = context_at(1)
    change(raw)
    context = parse_context(raw)
    assert context is not None, label
    with pytest.raises(ValueError, match="web-hygiene"):
        context.validate_scope(policy)
