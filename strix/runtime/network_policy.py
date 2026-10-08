"""Explicit packet destinations and an immutable per-run policy binding."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator


if TYPE_CHECKING:
    from pathlib import Path


Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
_MAX_POLICY_BYTES = 65536


class NetworkDestination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    address: str
    protocol: Literal["tcp", "udp"]
    ports: Annotated[list[Port], Field(min_length=1, max_length=64)]

    @field_validator("address")
    @classmethod
    def canonical_address(cls, value: str) -> str:
        if "%" in value:
            raise ValueError("IPv6 zone identifiers are not supported")
        return str(ipaddress.ip_network(value, strict=True))


class NetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    version: Literal[1] = 1
    destinations: Annotated[list[NetworkDestination], Field(max_length=256)] = Field(
        default_factory=list[NetworkDestination]
    )
    hosts: dict[str, str] = Field(default_factory=dict)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported network policy version")
        return value

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 256:
            raise ValueError("At most 256 static host aliases are supported")
        normalized: dict[str, str] = {}
        for name, address in value.items():
            if (
                not name.isascii()
                or len(name) > 253
                or any(
                    re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) is None
                    for label in name.split(".")
                )
            ):
                raise ValueError("Host aliases must be ASCII DNS names")
            if name.lower() in {"localhost", "ip6-localhost", "ip6-loopback"}:
                raise ValueError("Loopback host aliases are reserved")
            if "%" in address:
                raise ValueError("IPv6 zone identifiers are not supported")
            if name.lower() in normalized:
                raise ValueError("Duplicate normalized host alias")
            normalized[name.lower()] = str(ipaddress.ip_address(address))
        return normalized

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def parse_network_policy(value: Any) -> NetworkPolicy | None:
    if value is None:
        return None
    policy = NetworkPolicy.model_validate(value).model_copy(deep=True)
    if len(policy.model_dump_json().encode()) > _MAX_POLICY_BYTES:
        raise ValueError("Network policy exceeds 64 KiB")
    return policy


def read_network_policy(path: Path) -> NetworkPolicy:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_POLICY_BYTES + 1)
    if len(raw) > _MAX_POLICY_BYTES:
        raise ValueError("Network policy exceeds 64 KiB")
    return NetworkPolicy.model_validate_json(raw)


def bind_network_policy(state_dir: Path, requested: Any, *, resuming: bool) -> NetworkPolicy | None:
    """Restore the saved policy, reject changes, and pin new runs before execution."""
    policy = parse_network_policy(requested)
    path = state_dir / "network-policy.json"
    if path.is_symlink():
        raise ValueError("Network policy binding must not be a symlink")
    if path.exists():
        with path.open("rb") as stream:
            raw = stream.read(_MAX_POLICY_BYTES * 2 + 1)
        if len(raw) > _MAX_POLICY_BYTES * 2:
            raise ValueError("Saved network policy binding exceeds 128 KiB")
        raw_payload = json.loads(raw)
        if not isinstance(raw_payload, dict):
            raise ValueError("Invalid saved network policy binding")
        payload = cast("dict[str, Any]", raw_payload)
        if set(payload) != {"policy", "version"}:
            raise ValueError("Invalid saved network policy binding")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError("Unsupported network policy binding version")
        saved = parse_network_policy(payload["policy"])
        if policy is not None and policy != saved:
            raise ValueError("Network policy cannot change on resume; start a new run")
        return saved
    if resuming and policy is not None:
        raise ValueError("Missing network policy binding; start a new run")
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "policy": policy.model_dump() if policy is not None else None}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bind_network_policy(state_dir, requested, resuming=resuming)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return policy


def firewall_rules(
    policy: NetworkPolicy, *, family: Literal[4, 6], gateway: str, exposed_ports: tuple[int, ...]
) -> str:
    """Default-deny rules for the shared namespace, including all forwarded traffic."""
    lines = ["*filter", ":INPUT DROP [0:0]", ":FORWARD DROP [0:0]", ":OUTPUT DROP [0:0]"]
    # Docker can forward its embedded DNS outside this namespace. Never allow
    # that loopback address through the general local-service exception.
    if family == 4:
        lines.append("-A OUTPUT -d 127.0.0.11/32 -j DROP")
    else:
        # Link-local neighbor discovery is required when the Docker bridge has
        # IPv6 enabled. Raw sockets are unavailable to the sandbox, so agents
        # cannot forge these kernel control packets.
        for chain in ("INPUT", "OUTPUT"):
            lines.extend(
                f"-A {chain} -p ipv6-icmp --icmpv6-type {kind} -m hl --hl-eq 255 -j ACCEPT"
                for kind in (135, 136)
            )
    lines.extend(
        [
            "-A INPUT -i lo -j ACCEPT",
            "-A OUTPUT -o lo -j ACCEPT",
            "-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        ]
    )
    if family == 4:
        # Only the Docker host may initiate connections to the published proxy.
        host = str(ipaddress.IPv4Address(gateway))
        lines.extend(
            f"-A INPUT -s {host}/32 -p tcp -m tcp --dport {port} -j ACCEPT"
            for port in exposed_ports
        )
    for destination in policy.destinations:
        if ipaddress.ip_network(destination.address).version != family:
            continue
        for port in sorted(set(destination.ports)):
            proto = destination.protocol
            lines.append(
                f"-A OUTPUT -d {destination.address} -p {proto} -m {proto} --dport {port} -j ACCEPT"
            )
    return "\n".join([*lines, "COMMIT", ""])
