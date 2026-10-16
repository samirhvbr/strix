"""An approved DNS name is presented over the pinned address: SNI, Host and certificate name."""

from __future__ import annotations

import asyncio
import socket
import ssl
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from strix.core.assessment_context import FileCredentials, Operation, bind_context
from strix.core.evidence_ledger import EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.core.server_name import request_presentation, validate_server_name
from tests.test_assessment_context import config
from tests.test_web_hygiene_case import context_at


if TYPE_CHECKING:
    from pathlib import Path

NAME = "adm.example.test"


@pytest.mark.parametrize(
    "value",
    ["adm.blue3.com.br", "intranet", "a-b.example.test", "xn--bcher-kva.example", "a" * 63 + ".io"],
)
def test_canonical_dns_names_are_accepted(value: str) -> None:
    assert validate_server_name(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ADM.blue3.com.br",
        "adm.blue3.com.br.",
        "-a.example",
        "a-.example",
        "a..example",
        "a_b.example",
        "https://adm.blue3.com.br",
        "adm.blue3.com.br:443",
        "1.2.3.4",
        "300.1.1.1",
        "10.1",
        "::1",
        "[::1]",
        "a" * 64 + ".io",
        "x" * 254,
    ],
)
def test_anything_that_is_not_a_canonical_dns_name_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="Server name"):
        validate_server_name(value)


def test_operations_without_a_name_serialize_exactly_as_before() -> None:
    plain = Operation(method="GET", url="https://192.0.2.10/")
    named = Operation(method="GET", url="https://192.0.2.10/", server_name=NAME)
    assert "server_name" not in plain.model_dump()
    assert named.model_dump()["server_name"] == NAME


def test_the_context_accepts_a_name_on_http_and_tls_but_not_on_ssh(tmp_path: Path) -> None:
    policy, raw = context_at(8443, redirect=False, credential=False)
    raw["operations"]["probe"]["server_name"] = NAME
    assert bind_context(tmp_path / "http", "scan", policy, raw, resuming=False) is not None

    policy, raw = config(2222)
    raw.update(
        version=2,
        identities={"anonymous": {"tenant_ref": "lab", "role_ref": "observer", "secret_ref": None}},
        operations={
            "inspect": {"method": "SSH", "url": "ssh://127.0.0.1:2222", "server_name": NAME}
        },
        cases={
            "transport": {
                "version": 1,
                "identities": ["anonymous"],
                "operations": ["inspect"],
                "transport": {"adapter": "ssh-audit", "version": 1},
            }
        },
    )
    with pytest.raises(ValueError, match="not SSH"):
        bind_context(tmp_path / "ssh", "scan", policy, raw, resuming=False)


def test_a_name_cannot_replace_the_authorized_address(tmp_path: Path) -> None:
    policy, raw = context_at(8443, redirect=False, credential=False)
    raw["operations"]["probe"].update(url=f"http://{NAME}:8443/probe", server_name=NAME)
    with pytest.raises(ValueError, match="literal IP"):
        bind_context(tmp_path, "scan", policy, raw, resuming=False)


def test_presentation_follows_the_scheme_and_a_non_default_port() -> None:
    https = Operation(method="GET", url="https://192.0.2.10:8443/", server_name=NAME)
    assert request_presentation(https) == ({"Host": f"{NAME}:8443"}, {"sni_hostname": NAME})
    default = Operation(method="GET", url="https://192.0.2.10/", server_name=NAME)
    assert request_presentation(default) == ({"Host": NAME}, {"sni_hostname": NAME})
    plain = Operation(method="GET", url="http://192.0.2.10:8080/", server_name=NAME)
    assert request_presentation(plain) == ({"Host": f"{NAME}:8080"}, {})
    assert request_presentation(Operation(method="GET", url="http://192.0.2.10/")) == ({}, {})


@pytest.fixture
def name_only_cert(tmp_path: Path) -> tuple[Path, Path, str]:
    """A self-signed CA whose only identity is a DNS name: it does not vouch for any IP."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(NAME)]), critical=False)
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
    return tmp_path / "cert.pem", tmp_path / "key.pem", pem.decode()


@pytest.fixture
def https_site(name_only_cert: tuple[Path, Path, str]) -> Any:
    """A TLS server that answers HTTP and records the SNI and Host it was addressed with."""
    cert, key, ca = name_only_cert
    seen: dict[str, Any] = {}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.sni_callback = lambda _sock, name, _ctx: seen.__setitem__("sni", name)
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
            with (
                conn,
                suppress(ssl.SSLError, OSError),
                context.wrap_socket(conn, server_side=True) as tls,
            ):
                head = tls.recv(4096).decode(errors="replace")
                lines = head.split("\r\n")
                seen["host"] = next(
                    (line[5:].strip() for line in lines if line[:5].lower() == "host:"), None
                )
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], ca, seen
    finally:
        stopping.set()
        listener.close()
        thread.join(timeout=2)


def test_https_by_address_cannot_verify_a_name_only_certificate(https_site: Any) -> None:
    port, ca, seen = https_site

    async def fetch(operation: Operation) -> int:
        headers, extensions = request_presentation(operation)
        verify = ssl.create_default_context(cadata=ca)
        async with httpx.AsyncClient(verify=verify, trust_env=False, timeout=5) as client:
            response = await client.get(operation.url, headers=headers, extensions=extensions)
            return response.status_code

    bare = Operation(method="GET", url=f"https://127.0.0.1:{port}/")
    with pytest.raises(httpx.ConnectError, match="IP address mismatch"):
        asyncio.run(fetch(bare))
    assert seen.get("sni") is None

    named = Operation(method="GET", url=f"https://127.0.0.1:{port}/", server_name=NAME)
    assert asyncio.run(fetch(named)) == 200
    assert seen == {"sni": NAME, "host": f"{NAME}:{port}"}


def tls_executor(path: Path, port: int, ca: str, server_name: str | None) -> IdentityExecutor:
    policy, raw = config(port)
    operation: dict[str, Any] = {"method": "TLS", "url": f"tls://127.0.0.1:{port}"}
    if server_name is not None:
        operation["server_name"] = server_name
    raw.update(
        version=2,
        identities={"anonymous": {"tenant_ref": "lab", "role_ref": "observer", "secret_ref": None}},
        operations={"inspect": operation},
        cases={
            "transport": {
                "version": 1,
                "identities": ["anonymous"],
                "operations": ["inspect"],
                "transport": {"adapter": "openssl.tls", "version": 1, "ca_certificate": ca},
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
async def test_tls_case_checks_the_certificate_against_the_approved_name(
    tmp_path: Path, https_site: Any
) -> None:
    port, ca, _seen = https_site
    for label, name, verdict, reason in [
        ("by-address", None, "vulnerable", "certificate_verification_failed"),
        ("by-name", NAME, "compliant", "certificate_and_tls12_verified"),
        ("wrong-name", "other.example.test", "vulnerable", "certificate_verification_failed"),
    ]:
        executor = tls_executor(tmp_path / label, port, ca, name)
        result = await executor.run_case(agent_ref="agent", case_ref="transport")
        assert (result["verdict"], result["reason"]) == (verdict, reason), label
        await executor.close()


@pytest.fixture
def host_recorder() -> Any:
    seen: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            seen.append(self.headers.get("Host"))
            self.send_response_only(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_http_requests_carry_the_approved_name_as_host(
    tmp_path: Path, host_recorder: Any
) -> None:
    port, seen = host_recorder
    for label, name, expected in [
        ("bare", None, f"127.0.0.1:{port}"),
        ("named", NAME, f"{NAME}:{port}"),
    ]:
        policy, raw = context_at(port, redirect=False, credential=False)
        if name is not None:
            raw["operations"]["probe"]["server_name"] = name
        context = bind_context(tmp_path / label, "scan", policy, raw, resuming=False)
        assert context is not None
        ledger = EvidenceLedger(
            tmp_path / label / "evidence.db",
            scan_id="scan",
            assessment_id="assessment",
            context_sha256=context.digest,
            owns_agent=lambda _: True,
        )
        executor = IdentityExecutor(context, FileCredentials(None, "assessment"), ledger)
        await executor.run_case(agent_ref="agent", case_ref="hygiene")
        await executor.close()
        assert seen[-1] == expected, label


@pytest.mark.asyncio
async def test_the_shared_request_path_also_carries_the_approved_name(
    tmp_path: Path, host_recorder: Any
) -> None:
    port, seen = host_recorder
    policy, raw = config(port)
    raw["identities"] = {"a": {"tenant_ref": "tenant-a", "role_ref": "owner", "secret_ref": None}}
    raw["operations"] = {
        "private": {"method": "GET", "url": f"http://127.0.0.1:{port}/private", "server_name": NAME}
    }
    raw["cases"] = {"read": {"version": 1, "identities": ["a"], "operations": ["private"]}}
    context = bind_context(tmp_path, "scan", policy, raw, resuming=False)
    assert context is not None
    ledger = EvidenceLedger(
        tmp_path / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda _: True,
    )
    executor = IdentityExecutor(context, FileCredentials(None, "assessment"), ledger)
    result = await executor.request(
        agent_ref="agent", case_ref="read", identity_ref="a", operation_ref="private"
    )
    await executor.close()
    assert result["content"]["status_code"] == 200
    assert seen[-1] == f"{NAME}:{port}"
