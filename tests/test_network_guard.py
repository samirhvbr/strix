"""Fail-closed creation and lifecycle checks for the Docker namespace owner."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents.sandbox.manifest import Manifest
from agents.sandbox.sandboxes.docker import DockerSandboxClient
from docker.errors import ImageNotFound, NotFound

from strix.runtime import docker_client
from strix.runtime.docker_client import StrixDockerSandboxClient
from strix.runtime.network_guard import NetworkGuard
from strix.runtime.network_policy import NetworkPolicy


def _docker() -> Any:
    client = MagicMock()
    container = client.containers.create.return_value
    container.id = "owned-guard"
    container.attrs = {
        "State": {"Running": True, "StartedAt": "first-start"},
        "NetworkSettings": {
            "Networks": {"bridge": {"Gateway": "172.17.0.1"}},
            "Ports": {"48080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32000"}]},
        },
    }
    container.exec_run.return_value = SimpleNamespace(exit_code=0)
    return client


@pytest.mark.parametrize("failure", ["create", "start", "ipv4", "ipv6"])
def test_setup_failure_never_leaves_a_usable_guard(failure: str) -> None:
    client = _docker()
    container = client.containers.create.return_value
    if failure == "create":
        client.containers.create.side_effect = RuntimeError("create failed")
    elif failure == "start":
        container.start.side_effect = RuntimeError("start failed")
    else:
        container.exec_run.side_effect = [
            SimpleNamespace(exit_code=1 if failure == "ipv4" else 0),
            SimpleNamespace(exit_code=1),
        ]
    guard = NetworkGuard(client, NetworkPolicy(), (48080,))
    with pytest.raises(RuntimeError):
        guard.start()
    assert guard.container is None
    if failure != "create":
        container.remove.assert_called_once_with(force=True)


def test_missing_image_is_actionable_and_never_pulled() -> None:
    client = _docker()
    client.images.get.side_effect = ImageNotFound("missing")
    with pytest.raises(RuntimeError, match=r"docker build -f containers/network-guard\.Dockerfile"):
        NetworkGuard(client, NetworkPolicy(), ()).start()
    client.images.pull.assert_not_called()
    client.containers.create.assert_not_called()


def test_guard_uses_immutable_image_and_private_authority() -> None:
    client = _docker()
    guard = NetworkGuard(client, NetworkPolicy(), (48080,))
    guard.start()
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["image"] == str(client.images.get.return_value.id)
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["cap_add"] == ["NET_ADMIN"]
    assert kwargs["read_only"] is True
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["ports"] == {"48080/tcp": ("127.0.0.1", None)}
    assert "pid_mode" not in kwargs and "mounts" not in kwargs
    assert guard.endpoint(48080).port == 32000
    with pytest.raises(RuntimeError, match="not published"):
        guard.endpoint(48081)
    guard.container.attrs["NetworkSettings"]["Ports"]["48080/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(RuntimeError, match="only on loopback"):
        guard.endpoint(48080)
    guard.container.attrs["State"]["StartedAt"] = "second-start"
    with pytest.raises(RuntimeError, match="stopped or restarted"):
        guard.verify()


def test_close_is_idempotent_when_container_was_removed() -> None:
    client = _docker()
    guard = NetworkGuard(client, NetworkPolicy(), ())
    guard.start()
    client.containers.create.return_value.remove.side_effect = NotFound("gone")
    guard.close()
    guard.close()
    assert guard.container is None


@pytest.mark.asyncio
async def test_guard_failure_prevents_sandbox_creation() -> None:
    client = StrixDockerSandboxClient.__new__(StrixDockerSandboxClient)
    client.docker_client = _docker()
    client.strix_network_policy = NetworkPolicy()
    with (
        patch.object(client, "image_exists", return_value=True),
        patch.object(NetworkGuard, "start", side_effect=RuntimeError("cannot install")),
        pytest.raises(RuntimeError, match="cannot install"),
    ):
        await client._create_container("fixture")
    client.docker_client.containers.create.assert_not_called()


@pytest.mark.asyncio
async def test_creation_cleanup_attempts_both_containers_and_keeps_original_error() -> None:
    client = StrixDockerSandboxClient.__new__(StrixDockerSandboxClient)
    client._guarded_container = MagicMock()
    client._guarded_container.remove.side_effect = RuntimeError("remove failed")
    client.network_guard = MagicMock()
    with (
        patch.object(
            DockerSandboxClient, "create", new=AsyncMock(side_effect=ValueError("original"))
        ),
        pytest.raises(ValueError, match="original"),
    ):
        await client.create()
    client.network_guard.close.assert_called_once()


@pytest.mark.asyncio
async def test_delete_still_removes_guard_when_sdk_delete_fails() -> None:
    client = StrixDockerSandboxClient.__new__(StrixDockerSandboxClient)
    client.network_guard = MagicMock()
    session = SimpleNamespace(_inner=SimpleNamespace(state=SimpleNamespace(container_id=None)))
    with (
        patch.object(
            DockerSandboxClient, "delete", new=AsyncMock(side_effect=RuntimeError("failed"))
        ),
        pytest.raises(RuntimeError, match="failed"),
    ):
        await client.delete(session)
    client.network_guard.close.assert_called_once()


@pytest.mark.asyncio
async def test_guarded_sdk_resume_is_rejected() -> None:
    client = StrixDockerSandboxClient.__new__(StrixDockerSandboxClient)
    client.strix_network_policy = NetworkPolicy()
    with pytest.raises(ValueError, match="cannot be resumed"):
        await client.resume({})


def test_guarded_manifest_rejects_custom_network_and_sys_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrixDockerSandboxClient.__new__(StrixDockerSandboxClient)
    client.strix_network_policy = NetworkPolicy()
    monkeypatch.setenv("STRIX_DOCKER_SANDBOX_NETWORK", "custom")
    with pytest.raises(ValueError, match="STRIX_DOCKER_SANDBOX_NETWORK"):
        client._validate_guarded_manifest(None)
    monkeypatch.delenv("STRIX_DOCKER_SANDBOX_NETWORK")
    monkeypatch.setattr(docker_client, "_manifest_requires_sys_admin", lambda _manifest: True)
    with pytest.raises(ValueError, match="SYS_ADMIN"):
        client._validate_guarded_manifest(Manifest())
