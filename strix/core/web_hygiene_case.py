"""Fixed GET/HEAD web-hygiene probe: redirect, CORS, cookie attributes, response disclosure.

One approved case, one real request, four independent sub-verdicts (PENTEST-15, PT-23). The
generic ``IdentityExecutor._request`` blocks redirects and never records a response header, so
this adapter makes its own single bounded request and keeps the receipt restricted to a fixed
header-name allowlist — the same host-side guarantee the other adapters give, extended to
headers instead of bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

from strix.core.assessment_context import IdentityUnavailableError
from strix.core.evidence_ledger import EvidenceError
from strix.core.server_name import request_presentation
from strix.core.web_authorization import WebAuthorizationError


if TYPE_CHECKING:
    from strix.core.identity_executor import IdentityExecutor


#: Only these response header NAMES ever reach a receipt; every other header the target sends is
#: discarded before it is read. Values still pass through the executor's secret redaction.
ALLOWED_RESPONSE_HEADERS = frozenset(
    {
        "location",
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-methods",
        "set-cookie",
        "server",
        "x-powered-by",
        "content-type",
        "www-authenticate",
    }
)
_DISCLOSURE_HEADERS = ("server", "x-powered-by")


def _cookie_check(headers: httpx.Headers) -> dict[str, str]:
    cookies = headers.get_list("set-cookie")
    if not cookies:
        return {"verdict": "compliant", "reason": "no_cookie_observed"}
    for raw in cookies:
        attrs = [part.strip() for part in raw.split(";")[1:]]
        names = {part.split("=", 1)[0].lower() for part in attrs}
        samesite = next(
            (
                part.split("=", 1)[1].strip().lower()
                for part in attrs
                if "=" in part and part.split("=", 1)[0].lower() == "samesite"
            ),
            "",
        )
        # Checked first and separately: SameSite=None without Secure is the one RFC 6265bis
        # violation with its own reason; checking the generic case first would always win (every
        # SameSite=None-without-Secure cookie is also "missing secure"), making this unreachable.
        if samesite == "none" and "secure" not in names:
            return {"verdict": "vulnerable", "reason": "samesite_none_without_secure"}
        if "secure" not in names or "samesite" not in names:
            return {"verdict": "vulnerable", "reason": "cookie_missing_secure_or_samesite"}
    return {"verdict": "compliant", "reason": "cookie_attributes_present"}


def _cors_check(headers: httpx.Headers) -> dict[str, str]:
    origin = headers.get("access-control-allow-origin")
    if origin is None:
        return {"verdict": "compliant", "reason": "no_cors_header_observed"}
    credentials = (headers.get("access-control-allow-credentials") or "").strip().lower() == "true"
    if origin == "*" and credentials:
        return {"verdict": "vulnerable", "reason": "wildcard_origin_with_credentials"}
    return {"verdict": "compliant", "reason": "cors_header_present_without_risk"}


def _disclosure_check(headers: httpx.Headers) -> dict[str, Any]:
    present = sorted(name for name in _DISCLOSURE_HEADERS if headers.get(name))
    if present:
        return {"verdict": "vulnerable", "reason": "version_header_disclosed", "headers": present}
    return {"verdict": "compliant", "reason": "no_version_header_observed", "headers": []}


def _redirect_check(location: str | None, canary_host: str | None) -> dict[str, str]:
    if canary_host is None:
        return {"verdict": "inconclusive", "reason": "redirect_probe_not_declared"}
    if location is None:
        return {"verdict": "compliant", "reason": "probe_did_not_redirect"}
    try:
        redirected_host = httpx.URL(location).host
    except httpx.InvalidURL:
        return {"verdict": "inconclusive", "reason": "redirect_location_unparseable"}
    if redirected_host == canary_host:
        return {"verdict": "vulnerable", "reason": "redirect_followed_declared_canary"}
    return {"verdict": "compliant", "reason": "redirect_did_not_follow_declared_canary"}


def _record(
    executor: IdentityExecutor,
    *,
    case_ref: str,
    case_version: int,
    spec: Any,
    receipt: dict[str, Any] | None,
    verdict: str,
    reason: str,
    checks: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    result = executor.ledger.record_case_result(
        case_ref,
        {
            "schema_version": 1,
            "adapter": spec.adapter,
            "adapter_version": spec.version,
            "case_version": case_version,
            "verdict": verdict,
            "reason": reason,
            "checks": checks or {},
            "evidence": (
                [{key: value for key, value in receipt.items() if key != "content"}]
                if receipt is not None
                else []
            ),
        },
    )
    if executor.on_case_result is not None:
        executor.on_case_result(result)
    return result


async def run_web_hygiene_case(
    executor: IdentityExecutor, *, agent_ref: str, case_ref: str
) -> dict[str, Any]:
    case = executor.context.cases[case_ref]
    spec = case.web_hygiene
    if spec is None:
        raise EvidenceError("This case has no approved web-hygiene adapter")
    identity_ref = case.identities[0]
    operation = executor.context.operations[spec.probe_operation]
    url, canary_host = operation.url, None
    if spec.redirect_param is not None and spec.redirect_target is not None:
        canary_host = httpx.URL(spec.redirect_target).host
        url = f"{url}?{urlencode({spec.redirect_param: spec.redirect_target})}"
    attempt = executor.ledger.begin(
        agent_ref=agent_ref,
        case_ref=case_ref,
        case_version=case.version,
        identity_ref=identity_ref,
        operation_ref=spec.probe_operation,
    )
    identity = executor.context.identities[identity_ref]
    headers: dict[str, str] = {}
    try:
        if executor._authorize is not None:
            await asyncio.to_thread(executor._authorize)
        if identity.secret_ref is not None:
            credential = executor.credentials.resolve(identity.secret_ref, identity_ref)
            headers = {key: value.get_secret_value() for key, value in credential.headers.items()}
            executor._remember(headers)
            fingerprint = hashlib.sha256(json.dumps(headers, sort_keys=True).encode()).hexdigest()
            executor.ledger.bind_credential(attempt, credential.revision, fingerprint)
    except WebAuthorizationError:
        executor.ledger.record_denial(agent_ref, "authorization_rejected")
        artifact = executor.ledger.finish(
            attempt, status="blocked", content={"reason": "authorization_unavailable"}
        )
        receipt = executor.ledger.read(artifact)
        return _record(
            executor,
            case_ref=case_ref,
            case_version=case.version,
            spec=spec,
            receipt=receipt,
            verdict="inconclusive",
            reason="authorization_unavailable",
            checks=None,
        )
    except IdentityUnavailableError as exc:
        artifact = executor.ledger.finish(attempt, status="blocked", content={"reason": str(exc)})
        receipt = executor.ledger.read(artifact)
        return _record(
            executor,
            case_ref=case_ref,
            case_version=case.version,
            spec=spec,
            receipt=receipt,
            verdict="inconclusive",
            reason=str(exc),
            checks=None,
        )
    except (ValueError, EvidenceError):
        artifact = executor.ledger.finish(
            attempt, status="blocked", content={"reason": "identity_unavailable"}
        )
        receipt = executor.ledger.read(artifact)
        return _record(
            executor,
            case_ref=case_ref,
            case_version=case.version,
            spec=spec,
            receipt=receipt,
            verdict="inconclusive",
            reason="identity_unavailable",
            checks=None,
        )
    try:
        name_headers, name_extensions = request_presentation(operation)
        async with (
            httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=20) as client,
            client.stream(
                operation.method,
                url,
                headers={**headers, **name_headers},
                extensions=name_extensions,
            ) as response,
        ):
            # Headers are already available; the body is never read, only bounded and
            # discarded, so a hostile or misconfigured target cannot exhaust memory here.
            drained = 0
            async for chunk in response.aiter_bytes():
                drained += len(chunk)
                if drained > 131072:
                    break
    except httpx.HTTPError:
        artifact = executor.ledger.finish(
            attempt, status="uncertain", content={"reason": "transport_outcome_unknown"}
        )
        receipt = executor.ledger.read(artifact)
        return _record(
            executor,
            case_ref=case_ref,
            case_version=case.version,
            spec=spec,
            receipt=receipt,
            verdict="inconclusive",
            reason="transport_outcome_unknown",
            checks=None,
        )
    recorded = {
        name: executor._redact(value)
        for name, value in response.headers.items()
        if name.lower() in ALLOWED_RESPONSE_HEADERS
    }
    artifact = executor.ledger.finish(
        attempt,
        status="observed",
        content={"status_code": response.status_code, "headers": recorded},
    )
    receipt = executor.ledger.read(artifact)
    checks = {
        "redirect": _redirect_check(response.headers.get("location"), canary_host),
        "cors": _cors_check(response.headers),
        "cookies": _cookie_check(response.headers),
        "disclosure": _disclosure_check(response.headers),
    }
    overall = (
        "vulnerable" if any(c["verdict"] == "vulnerable" for c in checks.values()) else "compliant"
    )
    return _record(
        executor,
        case_ref=case_ref,
        case_version=case.version,
        spec=spec,
        receipt=receipt,
        verdict=overall,
        reason="see_checks",
        checks=checks,
    )
