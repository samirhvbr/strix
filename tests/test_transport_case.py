"""Real local TLS and SSH controls for versioned transport inspection."""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import paramiko
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from strix.core.assessment_context import FileCredentials, bind_context
from strix.core.evidence_ledger import EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.core.transport_case import classify_ssh
from tests.test_assessment_context import config


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tls_server(tmp_path: Path) -> Any:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local fixture")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "cert.pem").write_bytes(pem)
    (tmp_path / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    stopping = threading.Event()

    def run() -> None:
        while not stopping.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn, suppress(ssl.SSLError, OSError), context.wrap_socket(conn, server_side=True):
                pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], pem.decode()
    finally:
        stopping.set()
        listener.close()
        thread.join(timeout=2)


@pytest.fixture
def ssh_server() -> Any:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    stopping = threading.Event()
    state = {"weak": False}
    key = paramiko.RSAKey.generate(3072)
    active: list[paramiko.Transport] = []

    def handle(conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        active.append(transport)
        options = transport.get_security_options()
        options.kex = (
            ("curve25519-sha256@libssh.org", "diffie-hellman-group14-sha1")
            if state["weak"]
            else ("curve25519-sha256@libssh.org",)
        )
        options.ciphers = (
            ("aes256-gcm@openssh.com", "aes128-cbc")
            if state["weak"]
            else ("aes256-gcm@openssh.com",)
        )
        options.digests = (
            ("hmac-sha2-256-etm@openssh.com", "hmac-sha1")
            if state["weak"]
            else ("hmac-sha2-256-etm@openssh.com",)
        )
        options.key_types = ("rsa-sha2-512", "ssh-rsa") if state["weak"] else ("rsa-sha2-512",)
        transport.add_server_key(key)
        with suppress(paramiko.SSHException, EOFError, OSError):
            transport.start_server(server=paramiko.ServerInterface())
        transport.close()

    def run() -> None:
        while not stopping.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], state
    finally:
        stopping.set()
        listener.close()
        for transport in active:
            transport.close()
        thread.join(timeout=2)


def executor_at(path: Path, port: int, adapter: str, ca: str | None = None) -> IdentityExecutor:
    policy, raw = config(port)
    protocol = "tls" if adapter == "openssl.tls" else "ssh"
    raw.update(
        version=2,
        identities={"anonymous": {"tenant_ref": "lab", "role_ref": "observer", "secret_ref": None}},
        operations={
            "inspect": {"method": protocol.upper(), "url": f"{protocol}://127.0.0.1:{port}"}
        },
        cases={
            "transport": {
                "version": 1,
                "identities": ["anonymous"],
                "operations": ["inspect"],
                "transport": {"adapter": adapter, "version": 1, "ca_certificate": ca},
            }
        },
    )
    context = bind_context(path, "scan", policy, raw, resuming=False)
    assert context is not None
    ledger = EvidenceLedger(
        path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda _: True,
    )
    return IdentityExecutor(context, FileCredentials(None, "assessment"), ledger)


@pytest.mark.asyncio
async def test_tls_trust_failure_and_valid_control(tmp_path: Path, tls_server: Any) -> None:
    port, ca = tls_server
    for name, trust, verdict in [("trusted", ca, "compliant"), ("untrusted", None, "vulnerable")]:
        executor = executor_at(tmp_path / name, port, "openssl.tls", trust)
        result = await executor.run_case(agent_ref="agent", case_ref="transport")
        assert result["verdict"] == verdict
        assert result["tool_version"].startswith("OpenSSL")
        assert result["evidence"][0]["source"] == "runtime_transport_observation"
        assert "BEGIN CERTIFICATE" not in json.dumps(result)
        await executor.close()


@pytest.mark.asyncio
async def test_ssh_algorithms_real_safe_and_weak_controls(tmp_path: Path, ssh_server: Any) -> None:
    port, target = ssh_server
    for name, weak, expected in [("safe", False, "compliant"), ("weak", True, "vulnerable")]:
        target["weak"] = weak
        executor = executor_at(tmp_path / name, port, "ssh-audit")
        result = await executor.run_case(agent_ref="agent", case_ref="transport")
        assert result["verdict"] == expected, result
        assert result["tool_version"] == "ssh-audit 3.3.0"
        assert "private_output" not in json.dumps(result)
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["openssl.tls", "ssh-audit"])
async def test_unavailable_transport_is_inconclusive(tmp_path: Path, adapter: str) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        executor = executor_at(tmp_path, port, adapter)
        result = await executor.run_case(agent_ref="agent", case_ref="transport")
        assert result["verdict"] == "inconclusive"
        assert executor.ledger.summary()["obligations"]["essential_unfulfilled"] == 1
        await executor.close()


def test_incomplete_or_cross_target_scanner_output_cannot_be_clean() -> None:
    assert classify_ssh({}, 0, "127.0.0.1")["verdict"] == "inconclusive"
    assert classify_ssh({"target": "192.0.2.1"}, 0, "127.0.0.1")["verdict"] == "inconclusive"


def test_connection_error_or_unclassified_failure_cannot_be_compliant() -> None:
    data = {
        "target": "127.0.0.1:22",
        **{
            group: [{"algorithm": "example", "notes": {}}] for group in ["kex", "key", "enc", "mac"]
        },
    }
    for code in [1, 3, -1]:
        assert classify_ssh(data, code, "127.0.0.1:22")["verdict"] == "inconclusive"
