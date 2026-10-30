# ruff: noqa: TRY301 -- Rejected handoff/response data is sanitized at one exception boundary.
"""Online, revocable WEB authorization supplied through a private native handoff.

A version-2 policy is a controlled assessment: it always carries a context (identities,
fixed operations, cases) and the runner narrows the agent to the one adapter the context
approves. A version-1 policy carries no context: it authorizes a network destination only
(the policy's own ``targets`` and ``network_policy``), and the full scanning agent runs
against it exactly as it does without any WEB authorization at all, confined by that network
grant. The two never mix: a version-1 authorization with a context, or a version-2 one
without, is rejected before anything starts.

The engine asks the WEB again before every model call and every controlled tool call. A
refusal (expired, revoked, defunded, a different grant) ends the run at once. A WEB that
simply does not answer (a deploy, a network blip, a 5xx, a laptop waking from sleep) is
different: it is :class:`WebAuthorizationUnavailableError`, and :func:`verify_patiently` repeats
that one for a bounded window before treating it as a refusal. Until 1.2.45 both ended the
run the same way, so thirty seconds of maintenance on the WEB could end a run of hours.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import stat
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import httpx

from strix.core.assessment import AssessmentPolicy, parse_assessment_policy
from strix.core.assessment_context import AssessmentContext, parse_context
from strix.core.inference_source import InferenceChoice, parse_inference


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path


logger = logging.getLogger(__name__)

_UNAVAILABLE = "WEB authorization expired, revoked or unavailable"

#: How long :func:`verify_patiently` keeps repeating a verification the WEB did not answer,
#: counted from the first attempt. The owner chose five minutes (PT-37, 30/10/2026): it covers
#: a deploy and a short outage, and it is also how long the engine goes without being able to
#: honor a revocation. It is a constant on purpose, not an environment variable: it is the
#: bound of a safety check, and a value nobody can raise by accident is the safer default.
RETRY_WINDOW_SECONDS = 300.0

#: Waits between repeated attempts; the last one repeats until the window is used up.
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

#: HTTP statuses that mean "try again", besides every 5xx. Everything else in 4xx is an answer.
_RETRYABLE_STATUS = frozenset({408, 429})


class WebAuthorizationError(ValueError):
    """A safe error that never contains a grant token or a remote response."""


class WebAuthorizationUnavailableError(WebAuthorizationError):
    """The WEB did not answer (transport error, timeout, 5xx, 408 or 429): not a refusal.

    Still a :class:`WebAuthorizationError`, so every caller that treats the check as failed
    keeps doing so. Only :func:`verify_patiently` tells the two apart.
    """


class WebAuthorization:
    #: The model the last successful ``fetch`` approved, or None when the WEB approved none.
    inference: InferenceChoice | None

    def __init__(self, path: Path) -> None:
        self.inference = None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                mode = os.fstat(stream.fileno())
                if not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077:
                    raise ValueError("Unsafe handoff")
                raw = stream.read(8193)
            data = json.loads(raw)
            if len(raw) > 8192 or set(data) != {"authority", "token"}:
                raise ValueError("Invalid handoff")
            url = httpx.URL(data["authority"])
            if (
                url.scheme != "https"
                or not url.host
                or url.userinfo
                or url.query
                or url.fragment
                or url.path != "/"
                or str(url).rstrip("/") != data["authority"].rstrip("/")
            ):
                raise ValueError("Invalid authority")
            if not isinstance(data["token"], str) or not re.fullmatch(
                r"[a-f0-9]{64}", data["token"]
            ):
                raise ValueError("Invalid token")
            self._url = str(url).rstrip("/") + "/api/v1/pentest/authorizations/verify"
            self._token = data["token"]
        except (OSError, ValueError, TypeError, KeyError, httpx.InvalidURL):
            raise WebAuthorizationError("Invalid or unsafe WEB authorization handoff") from None

    def fetch(
        self, scan_id: str | None = None
    ) -> tuple[AssessmentPolicy, AssessmentContext | None, float]:
        try:
            with (
                httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client,
                client.stream(
                    "POST",
                    self._url,
                    headers={
                        "Authorization": "Bearer " + self._token,
                        "Accept": "application/json",
                    },
                    json={"scan_id": scan_id},
                ) as response,
            ):
                response.raise_for_status()
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 524288:
                        raise ValueError("Oversized authorization")
            data = cast("dict[str, Any]", json.loads(raw))
            policy = parse_assessment_policy(data["policy"])
            context = parse_context(data["context"])
            if (
                policy is None
                or policy.version not in (1, 2)
                or (policy.version == 2) != (context is not None)
            ):
                raise ValueError("Invalid authorization contract")
            if context is not None:
                context.validate_scope(policy)
            inference = parse_inference(data.get("inference"))
            expiry = datetime.fromisoformat(data["expires_at"])
            budget = float(data["max_budget_usd"])
            if (
                expiry.tzinfo is None
                or expiry <= datetime.now(UTC)
                or not math.isfinite(budget)
                or budget <= 0
                or budget > 10000
            ):
                raise ValueError("Expired or invalid authorization")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500 or status in _RETRYABLE_STATUS:
                raise WebAuthorizationUnavailableError(_UNAVAILABLE) from None
            raise WebAuthorizationError(_UNAVAILABLE) from None
        except (httpx.TransportError, OSError):
            raise WebAuthorizationUnavailableError(_UNAVAILABLE) from None
        except (ValueError, TypeError, KeyError, httpx.HTTPError):
            raise WebAuthorizationError(_UNAVAILABLE) from None

        self.inference = inference
        return policy, context, budget

    def check(
        self, scan_id: str, policy: AssessmentPolicy, context: AssessmentContext, budget: float
    ) -> None:
        approved_policy, approved_context, approved_budget = self.fetch(scan_id)
        if (
            approved_policy.digest != policy.digest
            or approved_context is None
            or approved_context.digest != context.digest
            or not math.isfinite(budget)
            or budget <= 0
            or budget > approved_budget
        ):
            raise WebAuthorizationError("Execution differs from the WEB authorization")

    def check_scope_only(self, scan_id: str, policy: AssessmentPolicy, budget: float) -> None:
        """Re-verify a context-less (version-1) authorization: live, unrevoked, still funded.

        Separate from :meth:`check` on purpose. A controlled (version-2) assessment is always
        re-verified against its exact identities and operations; a scope-only authorization has
        none to compare, and this never accepts one that, on re-fetch, suddenly carries a
        context — that would be a different authorization, not the one approved at start.
        """
        approved_policy, approved_context, approved_budget = self.fetch(scan_id)
        if (
            approved_policy.digest != policy.digest
            or approved_context is not None
            or not math.isfinite(budget)
            or budget <= 0
            or budget > approved_budget
        ):
            raise WebAuthorizationError("Execution differs from the WEB authorization")


async def verify_patiently(
    check: Callable[[], None],
    *,
    window: float | None = None,
    backoff: tuple[float, ...] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Run a blocking authorization check, repeating it while the WEB does not answer.

    Only :class:`WebAuthorizationUnavailableError` is repeated, with growing waits, until ``window``
    seconds have passed since the first attempt; then it is raised as it is, so the run still
    ends closed. A refusal (any other :class:`WebAuthorizationError`) is raised at once.

    The waits are ``asyncio.sleep``, not a sleeping worker thread: stopping the run cancels the
    task and the wait ends in the same instant. A sleeping thread would be joined when the
    interpreter exits and would hold the stop for up to the whole window. At most one attempt
    is in flight, and it is bounded by the client timeout.

    ``window``, ``backoff``, ``sleep`` and ``clock`` default to the module constants, the event
    loop's ``asyncio.sleep`` and ``time.monotonic``, read at call time; they are parameters so a
    test can run a five-minute window in no time.

    The log lines say that the check was repeated, how many times and how long it has been,
    never the token, the address or anything the WEB answered.
    """
    window = RETRY_WINDOW_SECONDS if window is None else window
    backoff = RETRY_BACKOFF_SECONDS if backoff is None else backoff
    sleep = sleep or asyncio.sleep
    clock = clock or time.monotonic
    started = clock()
    retries = 0
    while True:
        try:
            await asyncio.to_thread(check)
        except WebAuthorizationUnavailableError:
            used = clock() - started
            if used >= window:
                logger.warning(
                    "WEB authorization still unreachable after %d repeated checks over %.0fs; "
                    "treating it as refused",
                    retries,
                    used,
                )
                raise
            delay = min(backoff[min(retries, len(backoff) - 1)], window - used)
            retries += 1
            logger.warning(
                "WEB authorization unreachable; checking again in %.0fs (repeat %d, %.0f of %.0fs)",
                delay,
                retries,
                used,
                window,
            )
            await sleep(delay)
        else:
            if retries:
                logger.info("WEB authorization verified again after %d repeated checks", retries)
            return
