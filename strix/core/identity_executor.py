"""Fixed HTTP operations with isolated identities and durable runtime evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import httpx

from strix.core.assessment_context import IdentityUnavailableError
from strix.core.authorization_case import run_private_read_case
from strix.core.evidence_ledger import EvidenceError
from strix.core.web_authorization import WebAuthorizationError


if TYPE_CHECKING:
    from collections.abc import Callable

    from strix.core.assessment_context import AssessmentContext, FileCredentials
    from strix.core.evidence_ledger import EvidenceLedger


class IdentityExecutor:
    def __init__(
        self,
        context: AssessmentContext,
        credentials: FileCredentials,
        ledger: EvidenceLedger,
        authorize: Callable[[], None] | None = None,
    ) -> None:
        self.context = context.model_copy(deep=True)
        self.credentials = credentials
        self.ledger = ledger
        self.ledger.bind_obligations(self.context)
        self._authorize = authorize
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._revisions: dict[str, int] = {}
        self._locks = {name: asyncio.Lock() for name in context.identities}
        self._sensitive: set[str] = set()
        self._case_locks = {name: asyncio.Lock() for name in context.cases}
        self.on_case_result: Callable[[dict[str, Any]], None] | None = None

    def catalog(self) -> dict[str, Any]:
        return {
            "obligations": self.ledger.summary()["obligations"],
            "project_ref": self.context.project_ref,
            "environment_ref": self.context.environment_ref,
            "identities": {
                key: {"tenant_ref": identity.tenant_ref, "role_ref": identity.role_ref}
                for key, identity in self.context.identities.items()
            },
            "cases": {key: case.model_dump() for key, case in self.context.cases.items()},
            "operations": {key: op.model_dump() for key, op in self.context.operations.items()},
        }

    def _remember(self, headers: dict[str, str]) -> None:
        for name, value in headers.items():
            self._sensitive.add(value)
            if name.lower() == "authorization" and " " in value:
                self._sensitive.add(value.split(" ", 1)[1])
            if name.lower() == "cookie":
                for part in value.split(";"):
                    if "=" in part:
                        self._sensitive.add(part.split("=", 1)[1].strip())

    def _redact(self, text: str) -> str:
        for secret in sorted(self._sensitive, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    async def run_case(
        self, *, agent_ref: str, case_ref: str, baseline_ref: str | None = None
    ) -> dict[str, Any]:
        if case_ref not in self._case_locks:
            self.ledger.record_denial(agent_ref, "scope_rejected")
            raise EvidenceError("Unknown approved case")
        async with self._case_locks[case_ref]:
            return await run_private_read_case(
                self, agent_ref=agent_ref, case_ref=case_ref, baseline_ref=baseline_ref
            )

    async def request(
        self, *, agent_ref: str, case_ref: str, identity_ref: str, operation_ref: str
    ) -> dict[str, Any]:
        if case_ref not in self._case_locks:
            self.ledger.record_denial(agent_ref, "scope_rejected")
            raise EvidenceError("Operation or identity is outside the approved case")
        async with self._case_locks[case_ref]:
            return await self._request(
                agent_ref=agent_ref,
                case_ref=case_ref,
                identity_ref=identity_ref,
                operation_ref=operation_ref,
            )

    async def _request(  # noqa: PLR0912, PLR0915 -- One serialized request/receipt transaction.
        self, *, agent_ref: str, case_ref: str, identity_ref: str, operation_ref: str
    ) -> dict[str, Any]:
        case = self.context.cases.get(case_ref)
        if (
            case is None
            or identity_ref not in case.identities
            or operation_ref not in case.operations
        ):
            self.ledger.record_denial(agent_ref, "scope_rejected")
            raise EvidenceError("Operation or identity is outside the approved case")
        async with self._locks[identity_ref]:
            attempt = self.ledger.begin(
                agent_ref=agent_ref,
                case_ref=case_ref,
                case_version=case.version,
                identity_ref=identity_ref,
                operation_ref=operation_ref,
            )
            identity = self.context.identities[identity_ref]
            headers: dict[str, str] = {}
            revision = 0
            try:
                if self._authorize is not None:
                    await asyncio.to_thread(self._authorize)
                if identity.secret_ref is not None:
                    credential = self.credentials.resolve(identity.secret_ref, identity_ref)
                    headers = {
                        key: value.get_secret_value() for key, value in credential.headers.items()
                    }
                    revision = credential.revision
                    self._remember(headers)
                    fingerprint = hashlib.sha256(
                        json.dumps(headers, sort_keys=True).encode()
                    ).hexdigest()
                    self.ledger.bind_credential(attempt, revision, fingerprint)
            except WebAuthorizationError:
                self.ledger.record_denial(agent_ref, "authorization_rejected")
                artifact = self.ledger.finish(
                    attempt, status="blocked", content={"reason": "authorization_unavailable"}
                )
                return self.ledger.read(artifact)
            except IdentityUnavailableError as exc:
                artifact = self.ledger.finish(
                    attempt, status="blocked", content={"reason": str(exc)}
                )
                return self.ledger.read(artifact)
            except (ValueError, EvidenceError):
                artifact = self.ledger.finish(
                    attempt, status="blocked", content={"reason": "identity_unavailable"}
                )
                return self.ledger.read(artifact)
            if self._revisions.get(identity_ref) != revision:
                previous = self._clients.pop(identity_ref, None)
                if previous is not None:
                    await previous.aclose()
                self._revisions[identity_ref] = revision
            client = self._clients.get(identity_ref)
            if client is None:
                client = httpx.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                    timeout=20,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                )
                self._clients[identity_ref] = client
            operation = self.context.operations[operation_ref]
            try:
                async with client.stream(
                    operation.method, operation.url, headers=headers
                ) as response:
                    for cookie in client.cookies.jar:
                        if cookie.value:
                            self._sensitive.add(cookie.value)
                    if response.is_redirect:
                        content: dict[str, Any] = {
                            "status_code": response.status_code,
                            "reason": "redirect_blocked",
                        }
                        status, truncated = "blocked", False
                    elif response.status_code in {401, 407}:
                        content = {
                            "status_code": response.status_code,
                            "reason": "identity_rejected",
                        }
                        status, truncated = "blocked", False
                        client.cookies.clear()
                    else:
                        chunks = bytearray()
                        truncated = False
                        async for chunk in response.aiter_bytes():
                            remaining = 131072 - len(chunks)
                            chunks.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                truncated = True
                                break
                        try:
                            body = chunks.decode("utf-8")
                        except UnicodeDecodeError:
                            body = chunks.decode("utf-8", errors="replace")
                            truncated = True
                        content = {
                            "status_code": response.status_code,
                            "body": self._redact(body),
                        }
                        status = "observed"
            except httpx.HTTPError:
                # A timeout does not establish whether the target processed the request.
                content, status, truncated = (
                    {"reason": "transport_outcome_unknown"},
                    "uncertain",
                    False,
                )
            artifact = self.ledger.finish(
                attempt, status=status, content=content, truncated=truncated
            )
            return self.ledger.read(artifact)

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._sensitive.clear()
        self.ledger.close()
