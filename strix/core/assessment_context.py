"""Immutable case/identity metadata; rotating secrets stay outside run artifacts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from strix.core.assessment import AssessmentPolicy, Reference, read_assessment_json


if TYPE_CHECKING:
    from pathlib import Path


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_ref: Reference
    role_ref: Reference
    secret_ref: Reference | None = None


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    method: Literal["GET", "HEAD"]
    url: str


class AuthorizationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    adapter: Literal["http.private-read"]
    version: Literal[1]
    owner_ref: Reference
    other_ref: Reference
    control_operation: Reference
    resource_operation: Reference
    resource_ref: Reference


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Annotated[int, Field(strict=True, ge=1)]
    identities: Annotated[list[Reference], Field(min_length=1, max_length=64)]
    operations: Annotated[list[Reference], Field(min_length=1, max_length=64)]
    authorization: AuthorizationCase | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.authorization is None:
            data.pop("authorization", None)
        return data


class AssessmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1, 2]
    project_ref: Reference
    environment_ref: Reference
    identities: dict[Reference, Identity]
    operations: dict[Reference, Operation]
    cases: dict[Reference, Case]

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def validate_scope(self, policy: AssessmentPolicy) -> None:
        if not self.identities or not self.operations or not self.cases:
            raise ValueError("Assessment context needs identities, operations and cases")
        if max(len(self.identities), len(self.operations), len(self.cases)) > 256:
            raise ValueError("Assessment context exceeds the supported size")
        if len(self.model_dump_json().encode()) > 262144:
            raise ValueError("Assessment context exceeds the supported size")
        for case in self.cases.values():
            if (
                set(case.identities) - self.identities.keys()
                or set(case.operations) - self.operations.keys()
            ):
                raise ValueError("Case contains unknown identity or operation references")
            self._validate_case_adapter(case)
        for operation in self.operations.values():
            try:
                url = httpx.URL(operation.url)
            except httpx.InvalidURL:
                raise ValueError("Invalid assessment operation URL") from None
            if url.scheme not in {"http", "https"} or url.userinfo or url.query or url.fragment:
                raise ValueError(
                    "Operations need fixed HTTP URLs without credentials or query strings"
                )
            try:
                address = ipaddress.ip_address(url.host)
            except ValueError:
                raise ValueError(
                    "Identity HTTP operations currently require literal IP addresses"
                ) from None
            if str(url) != operation.url or "%" in operation.url or "\\" in operation.url:
                raise ValueError("Operations need canonical, unencoded URLs")
            # URL targets authorize their exact path; IP targets authorize that address.
            if not any(
                target.value
                == (operation.url if target.type == "web_application" else str(address))
                for target in policy.targets
            ):
                raise ValueError("Identity operation is outside the assessment targets")
            port = url.port or (443 if url.scheme == "https" else 80)
            if not any(
                rule.protocol == "tcp"
                and port in rule.ports
                and address in ipaddress.ip_network(rule.address)
                for rule in policy.network_policy.destinations
            ):
                raise ValueError("Identity operation is outside the assessment network grants")

    def _validate_case_adapter(self, case: Case) -> None:
        spec = case.authorization
        if spec is not None:
            if (
                self.version != 2
                or spec.owner_ref == spec.other_ref
                or spec.control_operation == spec.resource_operation
                or set(case.identities) != {spec.owner_ref, spec.other_ref}
                or set(case.operations) != {spec.control_operation, spec.resource_operation}
            ):
                raise ValueError("Invalid authorization case scope")
            owner, other = self.identities[spec.owner_ref], self.identities[spec.other_ref]
            if (
                not owner.secret_ref
                or not other.secret_ref
                or owner.tenant_ref == other.tenant_ref
                or any(self.operations[op].method != "GET" for op in case.operations)
            ):
                raise ValueError(
                    "Authorization cases require authenticated distinct tenants and GET"
                )


def parse_context(value: Any) -> AssessmentContext | None:
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise TypeError("Invalid context")  # noqa: TRY301 -- Sanitize rejected input.
        value = cast("dict[str, Any]", value)
        if type(value.get("version")) is not int:
            raise TypeError("Invalid context")  # noqa: TRY301 -- Sanitize rejected input below.
        return AssessmentContext.model_validate(value).model_copy(deep=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid assessment identity context") from None


def bind_context(  # noqa: PLR0912 -- Validate both initial and resumed immutable bindings.
    state_dir: Path,
    scan_id: str,
    policy: AssessmentPolicy | None,
    requested: Any,
    *,
    resuming: bool,
) -> AssessmentContext | None:
    context = parse_context(requested)
    path = state_dir / "assessment-context.json"
    if path.is_symlink():
        raise ValueError("Assessment context must not be a symlink")
    if path.exists():
        raw_data = read_assessment_json(path)
        if not isinstance(raw_data, dict):
            raise ValueError("Invalid assessment context binding")
        data = cast("dict[str, Any]", raw_data)
        if (
            set(data) != {"scan_id", "policy_sha256", "context"}
            or data.get("scan_id") != scan_id
            or data.get("policy_sha256") != (policy.digest if policy else None)
        ):
            raise ValueError("Invalid or cross-assessment identity context")
        saved = parse_context(data.get("context"))
        if context is not None and (saved is None or saved.digest != context.digest):
            raise ValueError("Identity context cannot change on resume")
        context = saved
        if context is None and (state_dir / "evidence.db").exists():
            raise ValueError("Required assessment identity context is missing")
    else:
        if (state_dir / "evidence.db").exists():
            raise ValueError("Required assessment identity context is missing")
        if resuming and context is not None:
            raise ValueError("Cannot add identity context to an existing run")
        if context is not None:
            if policy is None:
                raise ValueError("Identity context requires an assessment policy")
            context.validate_scope(policy)
        data = {
            "scan_id": scan_id,
            "policy_sha256": policy.digest if policy else None,
            "context": context.model_dump() if context else None,
        }
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
    if context is not None:
        if policy is None:
            raise ValueError("Identity context requires an assessment policy")
        context.validate_scope(policy)
    return context


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assessment_id: Reference
    identity_ref: Reference
    revision: Annotated[int, Field(strict=True, ge=1)]
    expires_at: datetime
    headers: dict[Literal["Authorization", "Cookie"], SecretStr]


class FileCredentials:
    """Operator-controlled private file, reread for expiration and rotation on each request."""

    def __init__(self, path: Path | None, assessment_id: str) -> None:
        self.path = path
        self.assessment_id = assessment_id

    def resolve(self, reference: str, identity_ref: str) -> Credential:
        try:
            return self._resolve_checked(reference, identity_ref)
        except IdentityUnavailableError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise IdentityUnavailableError("identity_unavailable") from None

    def _resolve_checked(self, reference: str, identity_ref: str) -> Credential:
        if self.path is None:
            raise ValueError("credential_unavailable")
        # Do not follow a substituted symlink or read a world/group-readable secret.
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            mode = os.fstat(stream.fileno())
            if not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077:
                raise ValueError("credential_unavailable")
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ValueError("credential_unavailable")
        data = json.loads(raw)
        credential = Credential.model_validate(data[reference])
        if (
            credential.assessment_id != self.assessment_id
            or credential.identity_ref != identity_ref
        ):
            raise ValueError("credential_unavailable")
        if not credential.headers or credential.expires_at.tzinfo is None:
            raise ValueError("credential_unavailable")
        for value in credential.headers.values():
            text = value.get_secret_value()
            if not text or len(text) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in text):
                raise ValueError("credential_unavailable")
        if credential.expires_at <= datetime.now(UTC):
            raise IdentityUnavailableError("identity_expired")
        return credential


class IdentityUnavailableError(ValueError):
    """A safe, stable identity limitation code without rejected credential values."""
