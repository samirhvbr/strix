"""Policy validation, durable resume binding, and source-mount protection."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from strix.interface.cli_args import parse_arguments
from strix.report.state import ReportState
from strix.runtime import session_manager
from strix.runtime.network_policy import (
    NetworkPolicy,
    bind_network_policy,
    firewall_rules,
    parse_network_policy,
    read_network_policy,
)
from strix.runtime.session_manager import protect_run_mounts


if TYPE_CHECKING:
    from pathlib import Path


def policy(address: str = "192.0.2.10", port: int = 443) -> NetworkPolicy:
    return NetworkPolicy.model_validate(
        {
            "destinations": [{"address": address, "protocol": "tcp", "ports": [port]}],
        }
    )


@pytest.mark.parametrize(
    "change",
    [
        {"address": "example.com"},
        {"address": "192.0.2.1/24"},
        {"address": "fe80::1%eth0"},
        {"address": "192.0.2.1\nCOMMIT"},
        {"ports": []},
        {"ports": [0]},
        {"ports": [65536]},
        {"ports": [True]},
        {"ports": ["443"]},
        {"protocol": "icmp"},
        {"unexpected": True},
    ],
)
def test_invalid_destination_is_rejected(change: dict[str, Any]) -> None:
    value = {"address": "192.0.2.10", "protocol": "tcp", "ports": [443], **change}
    with pytest.raises(ValidationError):
        NetworkPolicy.model_validate({"destinations": [value]})


@pytest.mark.parametrize(
    "hosts",
    [
        {"localhost": "192.0.2.10"},
        {"a\nb": "192.0.2.10"},
        {"-a.test": "192.0.2.10"},
        {"a.test": "name.test"},
        {"a.test": "fe80::1%eth0"},
        {"a.test": "192.0.2.10", "A.TEST": "192.0.2.11"},
    ],
)
def test_invalid_static_hosts_are_rejected(hosts: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        NetworkPolicy(hosts=hosts)


def test_policy_copy_and_canonical_digest() -> None:
    original = policy()
    copied = parse_network_policy(original)
    assert copied is not None and copied is not original
    assert copied.digest == original.digest
    original.destinations[0].ports.append(80)
    assert copied.destinations[0].ports == [443]
    assert copied.digest != original.digest
    assert policy("2001:db8::1").destinations[0].address == "2001:db8::1/128"
    assert NetworkPolicy(hosts={"APP.TEST": "192.0.2.10"}).hosts == {"app.test": "192.0.2.10"}


def test_mutated_policy_objects_are_revalidated() -> None:
    value = policy()
    value.destinations[0].ports.append("443\n-A OUTPUT -j ACCEPT")
    with pytest.raises(ValidationError):
        parse_network_policy(value)


@pytest.mark.parametrize("version", [True, "1", 1.0, 2])
def test_policy_version_requires_the_exact_integer(version: Any) -> None:
    with pytest.raises(ValidationError):
        NetworkPolicy.model_validate({"version": version})


def test_rules_separate_families_and_deny_embedded_dns_before_loopback() -> None:
    value = NetworkPolicy(destinations=policy().destinations + policy("2001:db8::1").destinations)
    ipv4 = firewall_rules(value, family=4, gateway="172.17.0.1", exposed_ports=(48080,))
    ipv6 = firewall_rules(value, family=6, gateway="172.17.0.1", exposed_ports=(48080,))
    assert ipv4.index("127.0.0.11/32 -j DROP") < ipv4.index("-A OUTPUT -o lo -j ACCEPT")
    assert "192.0.2.10/32" in ipv4 and "2001:db8::1/128" not in ipv4
    assert "2001:db8::1/128" in ipv6 and "192.0.2.10/32" not in ipv6
    assert "-A INPUT -s 172.17.0.1/32 -p tcp -m tcp --dport 48080 -j ACCEPT" in ipv4
    assert "48080" not in ipv6
    for rules in (ipv4, ipv6):
        for chain in ("INPUT", "FORWARD", "OUTPUT"):
            assert f":{chain} DROP [0:0]" in rules


def test_policy_file_size_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        read_network_policy(path)
    path.write_text("{}")
    assert read_network_policy(path) == NetworkPolicy()


def test_binding_survives_omitted_resume_policy_and_rejects_changes(tmp_path: Path) -> None:
    saved = policy()
    assert bind_network_policy(tmp_path, saved, resuming=False) == saved
    assert (tmp_path / "network-policy.json").stat().st_mode & 0o777 == 0o600
    assert bind_network_policy(tmp_path, None, resuming=True) == saved
    assert bind_network_policy(tmp_path, saved.model_dump(), resuming=True) == saved
    with pytest.raises(ValueError, match="cannot change"):
        bind_network_policy(tmp_path, policy(port=80), resuming=True)
    with pytest.raises(ValueError, match="cannot change"):
        bind_network_policy(tmp_path, NetworkPolicy(), resuming=True)
    assert bind_network_policy(tmp_path, None, resuming=True) == saved


def test_legacy_run_cannot_acquire_policy_on_resume(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing"):
        bind_network_policy(tmp_path, policy(), resuming=True)
    assert bind_network_policy(tmp_path, None, resuming=True) is None
    with pytest.raises(ValueError, match="cannot change"):
        bind_network_policy(tmp_path, policy(), resuming=True)


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        '{"version": 2, "policy": null}',
        '{"version": true, "policy": null}',
        '{"version": 1}',
        '{"version": 1, "policy": {"unknown": 1}}',
    ],
)
def test_corrupt_binding_never_downgrades_to_unrestricted(tmp_path: Path, payload: str) -> None:
    (tmp_path / "network-policy.json").write_text(payload)
    with pytest.raises(ValueError):
        bind_network_policy(tmp_path, None, resuming=True)


def test_binding_rejects_symlink(tmp_path: Path) -> None:
    (tmp_path / "network-policy.json").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        bind_network_policy(tmp_path, None, resuming=True)


def test_all_source_aliases_protect_the_run_directory(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "run1"
    run.mkdir(parents=True)
    mounts = [
        {"source": str(tmp_path), "target": "/workspace/repo"},
        {"source": str(tmp_path), "target": "/workspace/alias"},
        {"source": str(run / ".state"), "target": "/workspace/state"},
    ]
    protect_run_mounts(mounts, run)
    assert mounts[2]["read_only"] is True
    assert {entry["target"] for entry in mounts if entry.get("read_only")} == {
        "/workspace/repo",
        "/workspace/alias",
        "/workspace/state",
    }


@pytest.mark.asyncio
async def test_cached_policy_cannot_be_changed_or_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = policy()
    guard = MagicMock()
    bundle = {"network_policy": saved, "client": SimpleNamespace(network_guard=guard)}
    monkeypatch.setattr(session_manager, "_SESSION_CACHE", {"guarded": bundle})
    for requested in (None, NetworkPolicy(), policy(port=80)):
        with pytest.raises(ValueError, match="cannot change"):
            await session_manager.create_or_reuse(
                "guarded",
                image="unused",
                local_sources=[],
                network_policy=requested,
            )
    assert (
        await session_manager.create_or_reuse(
            "guarded",
            image="unused",
            local_sources=[],
            network_policy=saved,
        )
        is bundle
    )
    guard.verify.side_effect = RuntimeError("stopped or restarted")
    with pytest.raises(RuntimeError, match="stopped or restarted"):
        await session_manager.create_or_reuse(
            "guarded",
            image="unused",
            local_sources=[],
            network_policy=saved,
        )


@pytest.mark.asyncio
async def test_policy_rejects_unsupported_backend_before_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_manager, "_SESSION_CACHE", {})
    monkeypatch.setattr(
        session_manager,
        "load_settings",
        lambda: SimpleNamespace(runtime=SimpleNamespace(backend="remote")),
    )
    backend = AsyncMock()
    monkeypatch.setattr(session_manager, "get_backend", backend)
    with pytest.raises(ValueError, match="Docker backend"):
        await session_manager.create_or_reuse(
            "new", image="unused", local_sources=[], network_policy={}
        )
    backend.assert_not_called()


def test_cli_reads_file_and_resume_preserves_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "policy.json"
    path.write_text(policy().model_dump_json())
    monkeypatch.setattr(
        sys, "argv", ["strix", "-n", "-t", "http://192.0.2.10", "--network-policy", str(path)]
    )
    args = parse_arguments()
    assert args.network_policy == policy().model_dump()
    run = tmp_path / "strix_runs" / "guarded"
    (run / ".state").mkdir(parents=True)
    (run / ".state" / "agents.json").write_text("{}")
    (run / "run.json").write_text(
        json.dumps({"network_policy": args.network_policy, "targets_info": args.targets_info})
    )
    monkeypatch.setattr(sys, "argv", ["strix", "-n", "--resume", "guarded"])
    assert parse_arguments().network_policy == policy().model_dump()
    path.write_text(policy(port=80).model_dump_json())
    monkeypatch.setattr(
        sys, "argv", ["strix", "-n", "--resume", "guarded", "--network-policy", str(path)]
    )
    with pytest.raises(SystemExit):
        parse_arguments()


def test_report_config_preserves_policy_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = ReportState(run_name="guarded")
    saved = policy().model_dump()
    state.set_scan_config({"network_policy": saved})
    state.set_scan_config({})
    assert state.run_record["network_policy"] == saved
    assert state.scan_config["network_policy"] == saved
    with pytest.raises(ValueError, match="cannot change"):
        state.set_scan_config({"network_policy": policy(port=80).model_dump()})
    assert state.run_record["network_policy"] == saved
