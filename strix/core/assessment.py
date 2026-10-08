"""Host-authored assessment scope, pinned before executors are started."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from strix.core.inputs import build_scope_context
from strix.runtime.network_policy import NetworkPolicy, parse_network_policy
from strix.tools.mcp.config import (
    McpToolPolicy,  # noqa: TC001 -- Pydantic resolves this at runtime.
)


if TYPE_CHECKING:
    from pathlib import Path

    from strix.tools.mcp.registry import McpConnectionRequest


_MAX_BYTES = 262144
Reference = Annotated[
    str, Field(strict=True, min_length=1, max_length=256, pattern=r"^[\w./:@-]+$")
]


class ScopeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["web_application", "ip_address"]
    value: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class AssessmentMcpGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    tool_policies: dict[Reference, McpToolPolicy]

    @field_validator("url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            raise ValueError("MCP endpoints must be HTTP URLs without credentials or query strings")
        return value


class AssessmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    version: Literal[1]
    assessment_id: Reference
    authorization_ref: Reference
    operator_ref: Reference
    targets: Annotated[list[ScopeTarget], Field(min_length=1, max_length=256)]
    network_policy: NetworkPolicy
    mcp_connections: dict[Reference, AssessmentMcpGrant]

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported assessment policy version")
        return value

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def summary(self) -> dict[str, Any]:
        """Keep grant arguments and connection endpoints out of the public report."""
        return {
            "version": self.version,
            "assessment_id": self.assessment_id,
            "authorization_ref": self.authorization_ref,
            "operator_ref": self.operator_ref,
            "policy_sha256": self.digest,
        }


def parse_assessment_policy(value: Any) -> AssessmentPolicy | None:
    if value is None:
        return None
    try:
        raw = value.model_dump() if isinstance(value, AssessmentPolicy) else value
        policy = AssessmentPolicy.model_validate(raw).model_copy(deep=True)
    except (ValueError, TypeError):
        # Pydantic's full diagnostic can contain rejected credentials or grants.
        raise ValueError("Invalid assessment policy (version 1, at most 256 KiB)") from None
    if len(policy.model_dump_json().encode()) > _MAX_BYTES:
        raise ValueError("Assessment policy exceeds 256 KiB")
    return policy


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate assessment policy field")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 4097)
    if len(raw) > _MAX_BYTES + 4096:
        raise ValueError("Assessment policy file exceeds size limit")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid assessment policy JSON") from None


def read_assessment_policy(path: Path) -> AssessmentPolicy:
    policy = parse_assessment_policy(_read_json(path))
    if policy is None:
        raise ValueError("Assessment policy must be an object")
    return policy


def validate_assessment_scope(policy: AssessmentPolicy, scan_config: dict[str, Any]) -> None:
    """Match resolved target identities, without claiming HTTP path enforcement."""
    targets = build_scope_context(scan_config)["authorized_targets"]
    actual = [{"type": target["type"], "value": target["value"]} for target in targets]
    expected = [target.model_dump() for target in policy.targets]
    if actual != expected:
        raise ValueError("Assessment targets differ from the approved policy")
    if scan_config.get("workspace_mount") or scan_config.get("local_sources"):
        raise ValueError(
            "Assessment policy version 1 supports network targets without source mounts"
        )
    network = parse_network_policy(scan_config.get("network_policy"))
    if network is not None and network != policy.network_policy:
        raise ValueError("Network policy differs from the assessment policy")


def bind_assessment_policy(
    state_dir: Path, scan_id: str, requested: Any, *, resuming: bool
) -> AssessmentPolicy | None:
    """Restore restrictions even when the caller omits the original manifest."""
    policy = parse_assessment_policy(requested)
    path = state_dir / "assessment-policy.json"
    if path.is_symlink():
        raise ValueError("Assessment binding must not be a symlink")
    if path.exists():
        payload = _read_json(path)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "scan_id", "policy"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["scan_id"] != scan_id
        ):
            raise ValueError("Invalid or cross-run assessment binding")
        saved = parse_assessment_policy(payload["policy"])
        if policy is not None and (saved is None or policy.digest != saved.digest):
            raise ValueError("Assessment policy cannot change on resume; start a new run")
        return saved
    if resuming:
        if policy is not None:
            raise ValueError("Missing assessment binding; start a new run")
        return None
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "scan_id": scan_id, "policy": policy.model_dump() if policy else None}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bind_assessment_policy(state_dir, scan_id, requested, resuming=resuming)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return policy


def assessment_mcp_requests(
    policy: AssessmentPolicy, requests: list[McpConnectionRequest]
) -> list[McpConnectionRequest]:
    """Only approved HTTP connections reach discovery or provider dispatch.

    Provider behavior remains a trust boundary. Stdio subprocesses cannot be
    constrained by tool grants and are deliberately unsupported in this version.
    """
    selected: dict[str, McpConnectionRequest] = {}
    for request in requests:
        config = request.config.model_copy(deep=True)
        grant = policy.mcp_connections.get(config.name)
        if grant is None:
            continue
        if config.name in selected:
            raise ValueError("Duplicate assessment MCP connection")
        if config.transport != "http" or config.url != grant.url:
            raise ValueError("MCP endpoint differs from the assessment policy")
        if config.tool_policies is not None:
            configured = {name: item.model_dump() for name, item in config.tool_policies.items()}
            approved = {name: item.model_dump() for name, item in grant.tool_policies.items()}
            # JSON preserves bool/int/float distinctions that Python equality loses.
            if json.dumps(configured, sort_keys=True) != json.dumps(approved, sort_keys=True):
                raise ValueError("Existing MCP argument policy differs from the assessment policy")
        config.tool_policies = {
            name: item.model_copy(deep=True) for name, item in grant.tool_policies.items()
        }
        config.pin_http_endpoint = True
        selected[config.name] = dataclasses.replace(request, config=config)
    if selected.keys() != policy.mcp_connections.keys():
        raise ValueError("An approved assessment MCP connection is unavailable")
    return list(selected.values())
