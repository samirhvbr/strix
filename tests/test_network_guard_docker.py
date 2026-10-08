"""Real packet and privilege checks against disposable, locally owned containers."""

from __future__ import annotations

import os
import time
import urllib.request
from typing import Any

import pytest
from agents.sandbox.manifest import Manifest

from strix.runtime.backends import _docker_backend
from strix.runtime.docker_connection import connect_docker
from strix.runtime.network_policy import NetworkPolicy
from strix.runtime.session_manager import protect_run_mounts


pytestmark = pytest.mark.skipif(
    os.getenv("STRIX_NETWORK_GUARD_TESTS") != "1", reason="requires local Docker fixture images"
)
IMAGE = "shvia-strix-network-fixture:1"

SERVER = """
import http.server, socket, threading, time
for port in (18080, 18081, 38080):
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
def echo(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', port))
    while True:
        data, address = sock.recvfrom(1024)
        sock.sendto(data, address)
for port in (18082, 18083, 53):
    threading.Thread(target=echo, args=(port,), daemon=True).start()
print('READY', flush=True)
while True:
    time.sleep(60)
"""


@pytest.fixture
def targets() -> Any:
    docker = connect_docker()
    containers = []
    try:
        for _ in range(2):
            container = docker.containers.run(
                IMAGE, ["python3", "-u", "-c", SERVER], detach=True, network_mode="bridge"
            )
            containers.append(container)
            deadline = time.monotonic() + 20
            while b"READY" not in container.logs():
                if time.monotonic() > deadline:
                    pytest.fail("Local target failed to start")
                time.sleep(0.1)
            container.reload()
        yield docker, containers
    finally:
        for container in reversed(containers):
            container.remove(force=True)
        docker.close()


def _ip(container: Any) -> str:
    return str(container.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"])


def _python(container: Any, code: str) -> str:
    result = container.exec_run(["python3", "-c", code], user="0")
    assert result.exit_code == 0, result.output.decode()
    return str(result.output.decode().strip())


def _connect(container: Any, address: str, port: int, *, udp: bool = False) -> bool:
    code = f"""
import socket
sock = socket.socket({"socket.AF_INET6" if ":" in address else "socket.AF_INET"},
                     {"socket.SOCK_DGRAM" if udp else "socket.SOCK_STREAM"})
sock.settimeout(0.7)
try:
    sock.connect(({address!r}, {port}))
    if {udp!r}:
        sock.send(b'owned-fixture')
        assert sock.recv(1024) == b'owned-fixture'
    print('connected')
except (OSError, AssertionError):
    print('blocked')
finally:
    sock.close()
"""
    return _python(container, code) == "connected"


@pytest.mark.asyncio
async def test_real_packets_root_tampering_control_port_and_cleanup(  # noqa: PLR0915
    targets: Any, tmp_path: Any
) -> None:
    docker, (allowed, denied) = targets
    allowed_ip, denied_ip = _ip(allowed), _ip(denied)
    policy = NetworkPolicy.model_validate(
        {
            "destinations": [
                {"address": allowed_ip, "protocol": "tcp", "ports": [18080]},
                {"address": allowed_ip, "protocol": "udp", "ports": [18082]},
            ],
            "hosts": {"approved.lab": allowed_ip},
        }
    )
    run_dir = tmp_path / "runs" / "protected"
    run_dir.mkdir(parents=True)
    (run_dir / "policy.json").write_text("protected")
    mounts = [{"source": str(tmp_path), "target": "/workspace/repo"}]
    protect_run_mounts(mounts, run_dir)
    client, session = await _docker_backend(
        image=IMAGE,
        manifest=Manifest(),
        exposed_ports=(38080,),
        network_policy=policy,
        bind_mounts=mounts,
    )
    sandbox = docker.containers.get(session._inner.state.container_id)
    guard = client.network_guard.container
    sandbox_id, guard_id = sandbox.id, guard.id
    try:
        assert _connect(denied, allowed_ip, 18081), "Negative-control service must be reachable"
        assert _connect(denied, allowed_ip, 18083, udp=True)
        assert _connect(denied, allowed_ip, 53, udp=True)
        assert _connect(allowed, denied_ip, 18080)
        assert _connect(sandbox, allowed_ip, 18080)
        assert _connect(sandbox, "approved.lab", 18080)
        assert _connect(sandbox, allowed_ip, 18082, udp=True)
        assert not _connect(sandbox, allowed_ip, 18081)
        assert not _connect(sandbox, denied_ip, 18080)
        assert not _connect(sandbox, allowed_ip, 18083, udp=True)
        assert not _connect(sandbox, allowed_ip, 53, udp=True)
        assert not _connect(sandbox, "::ffff:" + denied_ip, 18080)

        # Both filter families must be installed even on an IPv4-only bridge.
        for command in ("iptables-save", "ip6tables-save"):
            rules = guard.exec_run([command]).output.decode()
            assert ":OUTPUT DROP" in rules and ":FORWARD DROP" in rules
        for command in (
            ["iptables", "-F"],
            ["ip6tables", "-F"],
            ["ip", "route", "add", "198.18.0.0/15", "dev", "eth0"],
            ["mount", "-o", "remount,rw", "/workspace/repo"],
            ["mv", "/workspace/repo/runs", "/workspace/repo/renamed"],
        ):
            assert sandbox.exec_run(command, user="0").exit_code != 0
        assert (
            _python(
                sandbox,
                """
import socket
for family, kind, protocol in [(socket.AF_PACKET, socket.SOCK_RAW, 0),
                               (socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)]:
    try:
        socket.socket(family, kind, protocol)
    except PermissionError:
        continue
    raise AssertionError('Root opened a raw socket')
print('blocked')
""",
            )
            == "blocked"
        )
        result = sandbox.exec_run(
            ["sh", "-c", "echo changed > /workspace/repo/runs/protected/policy.json"], user="0"
        )
        assert result.exit_code != 0
        assert (run_dir / "policy.json").read_text() == "protected"
        assert not _connect(sandbox, denied_ip, 18080)
        assert _connect(sandbox, allowed_ip, 18080)

        # A valid DNS query must not use Docker's embedded forwarding resolver.
        assert (
            _python(
                sandbox,
                """
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(0.7)
try:
    s.sendto(bytes.fromhex('123401000001000000000000076578616d706c6503636f6d0000010001'),
             ('127.0.0.11', 53))
    s.recv(512)
    raise AssertionError('Embedded DNS answered')
except OSError:
    print('blocked')
""",
            )
            == "blocked"
        )

        sandbox.exec_run(["python3", "-m", "http.server", "38080"], detach=True)
        endpoint = await session.resolve_exposed_port(38080)
        assert endpoint.host == "127.0.0.1"
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(
                    f"http://{endpoint.host}:{endpoint.port}", timeout=1
                ) as response:
                    assert response.status == 200
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        assert not _connect(denied, _ip(guard), 38080), "Peer must not reach the control service"
        assert _connect(sandbox, "127.0.0.1", 38080)

        guard.restart(timeout=0)
        assert not _connect(sandbox, denied_ip, 18080)
        with pytest.raises(RuntimeError, match="stopped or restarted"):
            client.network_guard.verify()
    finally:
        await client.delete(session)
        client.docker_client.close()
    remaining = {item.id for item in docker.containers.list(all=True)}
    assert sandbox_id not in remaining
    assert guard_id not in remaining


@pytest.mark.asyncio
async def test_empty_policy_blocks_reachable_target(targets: Any) -> None:
    docker, (target, control) = targets
    assert _connect(control, _ip(target), 18080)
    client, session = await _docker_backend(
        image=IMAGE, manifest=Manifest(), exposed_ports=(), network_policy=NetworkPolicy()
    )
    try:
        sandbox = docker.containers.get(session._inner.state.container_id)
        assert not _connect(sandbox, _ip(target), 18080)
        resolver = _python(sandbox, "print(open('/etc/resolv.conf').read())")
        assert "nameserver 127.0.0.1" in resolver
    finally:
        await client.delete(session)
        client.docker_client.close()
