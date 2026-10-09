"""Fixed TLS and pinned SSH inspections with private tool output and bounded execution."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import socket
import ssl
import sys
from typing import TYPE_CHECKING, Any

import httpx

from strix.core.evidence_ledger import EvidenceError
from strix.core.web_authorization import WebAuthorizationError


if TYPE_CHECKING:
    from strix.core.identity_executor import IdentityExecutor


def inspect_tls(host: str, port: int, ca: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"tool_version": ssl.OPENSSL_VERSION}
    try:
        context = ssl.create_default_context(cadata=ca)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with (
            socket.create_connection((host, port), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname=host) as secure,
        ):
            result.update(
                verdict="compliant",
                reason="certificate_and_tls12_verified",
                protocol=secure.version(),
                certificate_sha256=hashlib.sha256(
                    secure.getpeercert(binary_form=True) or b""
                ).hexdigest(),
            )
    except ssl.SSLCertVerificationError:
        result.update(verdict="vulnerable", reason="certificate_verification_failed")
    except (OSError, ValueError):
        result.update(verdict="inconclusive", reason="tls_inspection_unavailable")
    return result


def classify_ssh(data: Any, code: int, target: str) -> dict[str, Any]:
    result: dict[str, Any] = {"verdict": "inconclusive", "reason": "ssh_output_invalid"}
    if not isinstance(data, dict) or data.get("target") != target or code not in {0, 2, 3}:
        return result
    failures, warnings, unknown = 0, 0, False
    for group in ["kex", "key", "enc", "mac"]:
        entries = data.get(group)
        if not isinstance(entries, list) or not entries:
            return result
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("notes"), dict):
                return result
            notes = entry["notes"]
            failed, warned = notes.get("fail", []), notes.get("warn", [])
            if not isinstance(failed, list) or not isinstance(warned, list):
                return result
            unknown |= any("unknown" in str(note).lower() for note in failed)
            failures += len(failed)
            warnings += len(warned)
    result.update(failed_checks=failures, warning_checks=warnings)
    if unknown:
        result["reason"] = "ssh_algorithm_unknown"
    elif failures:
        result.update(verdict="vulnerable", reason="ssh_failed_algorithm_checks")
    elif code == 3:
        result["reason"] = "ssh_failure_outside_algorithm_profile"
    else:
        result.update(verdict="compliant", reason="no_failed_ssh_algorithm_checks")
    return result


async def inspect_ssh(host: str, port: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool_version": "ssh-audit 3.3.0",
        "verdict": "inconclusive",
        "reason": "ssh_tool_unavailable",
    }
    try:
        if importlib.metadata.version("ssh-audit") != "3.3.0":
            return result
    except importlib.metadata.PackageNotFoundError:
        return result
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ssh_audit.ssh_audit",
        "-j",
        "--skip-rate-test",
        "-t",
        "5",
        "-p",
        str(port),
        host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(20):
            assert process.stdout is not None
            raw = b""
            while chunk := await process.stdout.read(65536):
                raw += chunk
                if len(raw) > 1048576:
                    return result | {"reason": "ssh_output_too_large"}
            code = await process.wait()
            data = json.loads(raw)
            target = f"{host}:{port}"
            return result | classify_ssh(data, code, target) | {"private_output": data}
    except (TimeoutError, ValueError):
        return result | {"reason": "ssh_inspection_inconclusive"}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def run_transport_case(
    executor: IdentityExecutor, *, agent_ref: str, case_ref: str
) -> dict[str, Any]:
    case = executor.context.cases[case_ref]
    spec = case.transport
    if spec is None:
        raise EvidenceError("This case has no approved transport adapter")
    operation_ref, identity_ref = case.operations[0], case.identities[0]
    operation = executor.context.operations[operation_ref]
    url = httpx.URL(operation.url)
    attempt = executor.ledger.begin(
        agent_ref=agent_ref,
        case_ref=case_ref,
        case_version=case.version,
        identity_ref=identity_ref,
        operation_ref=operation_ref,
    )
    try:
        if executor._authorize is not None:
            await asyncio.to_thread(executor._authorize)
        observation = (
            await asyncio.to_thread(inspect_tls, url.host, url.port or 443, spec.ca_certificate)
            if spec.adapter == "openssl.tls"
            else await inspect_ssh(url.host, url.port or 22)
        )
    except WebAuthorizationError:
        executor.ledger.record_denial(agent_ref, "authorization_rejected")
        observation = {"verdict": "inconclusive", "reason": "authorization_unavailable"}
    observation["adapter"] = spec.adapter
    status = "blocked" if observation["verdict"] == "inconclusive" else "observed"
    ref = executor.ledger.finish(attempt, status=status, content=observation)
    receipt = executor.ledger.read(ref)
    result = executor.ledger.record_case_result(
        case_ref,
        {
            "schema_version": 1,
            "case_version": case.version,
            "adapter": spec.adapter,
            "adapter_version": spec.version,
            "tool_version": observation.get("tool_version"),
            "verdict": observation["verdict"],
            "reason": observation["reason"],
            "failed_checks": observation.get("failed_checks"),
            "warning_checks": observation.get("warning_checks"),
            "evidence": [{key: value for key, value in receipt.items() if key != "content"}],
        },
    )
    if executor.on_case_result is not None:
        executor.on_case_result(result)
    return result
