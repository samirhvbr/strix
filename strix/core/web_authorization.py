# ruff: noqa: TRY301 -- Rejected handoff/response data is sanitized at one exception boundary.
"""Online, revocable WEB authorization supplied through a private native handoff."""

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


if TYPE_CHECKING:
    from pathlib import Path


class WebAuthorizationError(ValueError):
    """A safe error that never contains a grant token or a remote response."""


class WebAuthorization:
    def __init__(self, path: Path) -> None:
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
    ) -> tuple[AssessmentPolicy, AssessmentContext, float]:
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
            if policy is None or policy.version != 2 or context is None:
                raise ValueError("Invalid authorization contract")
            context.validate_scope(policy)
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

        return policy, context, budget

    def check(
        self, scan_id: str, policy: AssessmentPolicy, context: AssessmentContext, budget: float
    ) -> None:
        approved_policy, approved_context, approved_budget = self.fetch(scan_id)
        if (
            approved_policy.digest != policy.digest
            or approved_context.digest != context.digest
            or not math.isfinite(budget)
            or budget <= 0
            or budget > approved_budget
        ):
            raise WebAuthorizationError("Execution differs from the WEB authorization")
