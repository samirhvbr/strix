# ruff: noqa: TRY301 -- Rejected handoff/response data is sanitized at one exception boundary.
"""Online, revocable WEB authorization supplied through a private native handoff.

A version-2 policy is a controlled assessment: it always carries a context (identities,
fixed operations, cases) and the runner narrows the agent to the one adapter the context
approves. A version-1 policy carries no context: it authorizes a network destination only
(the policy's own ``targets`` and ``network_policy``), and the full scanning agent runs
against it exactly as it does without any WEB authorization at all, confined by that network
grant. The two never mix: a version-1 authorization with a context, or a version-2 one
without, is rejected before anything starts.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import httpx

from strix.core.assessment import AssessmentPolicy, parse_assessment_policy
from strix.core.assessment_context import AssessmentContext, parse_context
from strix.core.inference_source import InferenceChoice, parse_inference


if TYPE_CHECKING:
    from pathlib import Path


class WebAuthorizationError(ValueError):
    """A safe error that never contains a grant token or a remote response."""


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
        except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError):
            raise WebAuthorizationError(
                "WEB authorization expired, revoked or unavailable"
            ) from None

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
