"""Own a sandbox network namespace without exposing firewall authority to agents."""

from __future__ import annotations

import base64
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any, Literal

from agents.sandbox.types import ExposedPortEndpoint
from docker.errors import ImageNotFound, NotFound  # type: ignore[import-untyped, unused-ignore]

from strix.runtime.network_policy import NetworkPolicy, firewall_rules


if TYPE_CHECKING:
    from docker.models.containers import Container  # type: ignore[import-untyped, unused-ignore]


GUARD_IMAGE = "shvia-strix-network-guard:1"
logger = logging.getLogger(__name__)


class NetworkGuard:
    """A separate filesystem and PID namespace, sharing only networking with the sandbox."""

    def __init__(self, client: Any, policy: NetworkPolicy, ports: tuple[int, ...]) -> None:
        self.client = client
        self.policy = policy.model_copy(deep=True)
        self.ports = ports
        self.container: Container | None = None
        self.started_at = ""
        self.image_id = ""

    def start(self) -> None:
        # Resolve locally to immutable content. Never pull an unexpected helper
        # image or fall back to unguarded networking when the build is missing.
        try:
            image = self.client.images.get(GUARD_IMAGE)
        except ImageNotFound as exc:
            raise RuntimeError(
                "Network guard image is missing. Build it from the fork checkout: "
                f"docker build -f containers/network-guard.Dockerfile -t {GUARD_IMAGE} ."
            ) from exc
        self.image_id = str(image.id)
        container: Container = self.client.containers.create(
            image=self.image_id,
            entrypoint=["sleep"],
            command=["infinity"],
            user="0:0",
            detach=True,
            network_mode="bridge",
            cap_drop=["ALL"],
            cap_add=["NET_ADMIN"],
            read_only=True,
            security_opt=["no-new-privileges:true"],
            tmpfs={"/run": "rw,noexec,nosuid,size=1m"},
            dns=["127.0.0.1"],
            extra_hosts=self.policy.hosts,
            ports={f"{port}/tcp": ("127.0.0.1", None) for port in self.ports},
            labels={"shvia.network-guard": "1", "shvia.network-policy": self.policy.digest},
            mem_limit="64m",
            pids_limit=32,
        )
        self.container = container
        try:
            container.start()
            container.reload()
            self.started_at = container.attrs["State"]["StartedAt"]
            gateway = container.attrs["NetworkSettings"]["Networks"]["bridge"]["Gateway"]
            family: Literal[4, 6]
            for family in (4, 6):
                self._install(family, gateway)
            self.verify()
        except BaseException:
            try:
                self.close()
            except Exception:
                logger.exception("Failed to remove a network guard after setup failed")
            raise

    def _install(self, family: Literal[4, 6], gateway: str) -> None:
        assert self.container is not None
        content = firewall_rules(
            self.policy, family=family, gateway=gateway, exposed_ports=self.ports
        ).encode()
        if len(content) > 65536:
            raise ValueError("Compiled network rules exceed 64 KiB")
        executable = "iptables-restore" if family == 4 else "ip6tables-restore"
        # Fixed command: the policy travels as a positional base64 argument,
        # never as shell source. No writable helper filesystem is required.
        result = self.container.exec_run(
            [
                "sh",
                "-c",
                f'printf "%s" "$1" | base64 -d | {executable} --wait 5',
                "sh",
                base64.b64encode(content).decode("ascii"),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Could not install IPv{family} sandbox network rules")

    @property
    def network_mode(self) -> str:
        if self.container is None:
            raise RuntimeError("Network guard is not running")
        return f"container:{self.container.id}"

    def verify(self) -> None:
        if self.container is None:
            raise RuntimeError("Network guard is unavailable")
        self.container.reload()
        state = self.container.attrs["State"]
        if not state.get("Running") or state.get("StartedAt") != self.started_at:
            raise RuntimeError("Network guard stopped or restarted; start a new sandbox")

    def endpoint(self, port: int) -> ExposedPortEndpoint:
        self.verify()
        if self.container is None or port not in self.ports:
            raise RuntimeError("Network guard port is not published")
        bindings: list[dict[str, Any]] = (
            self.container.attrs["NetworkSettings"]["Ports"].get(f"{port}/tcp") or []
        )
        if not bindings or bindings[0].get("HostIp") != "127.0.0.1":
            raise RuntimeError("Network guard port must be published only on loopback")
        return ExposedPortEndpoint(host="127.0.0.1", port=int(bindings[0]["HostPort"]), tls=False)

    def denied_packets(self) -> dict[str, dict[str, int]]:
        """Read aggregate DROP counters from the protected namespace, without packet contents."""
        self.verify()
        assert self.container is not None
        counts: dict[str, dict[str, int]] = {}
        for family, command in (("ipv4", "iptables-save"), ("ipv6", "ip6tables-save")):
            result = self.container.exec_run([command, "-c", "-t", "filter"])
            if result.exit_code != 0:
                raise RuntimeError("Cannot observe sandbox packet denials")
            rules = result.output.decode("ascii", errors="strict")
            default = re.search(r"^:OUTPUT DROP \[(\d+):(\d+)\]$", rules, re.MULTILINE)
            if default is None:
                raise RuntimeError("Sandbox OUTPUT policy is not default-deny")
            packets, octets = map(int, default.groups())
            # Docker's embedded DNS has an explicit DROP before the loopback exception.
            for match in re.finditer(
                r"^\[(\d+):(\d+)\] -A OUTPUT .* -j DROP$", rules, re.MULTILINE
            ):
                packets += int(match[1])
                octets += int(match[2])
            counts[family] = {"packets": packets, "bytes": octets}
        return counts

    def close(self) -> None:
        if self.container is not None:
            with contextlib.suppress(NotFound):
                self.container.remove(force=True)
            self.container = None
