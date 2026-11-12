#!/usr/bin/env python3
"""Accept the pinned sandbox on a native Linux amd64 Docker host, without a scan.

Prepare Python 3.12 with ``uv sync --frozen`` and pull IMAGE and PARENT below.
Build ``containers/network-guard.Dockerfile`` as ``shvia-strix-network-guard:1``
and ``tests/fixtures/network-sandbox.Dockerfile`` as ``shvia-strix-network-fixture:1``.
Run with an isolated HOME and environment, using the checkout on PYTHONPATH::

    uv run --frozen python scripts/sandbox_runtime_acceptance.py \
        --report acceptance.json --expected-architecture amd64

Only disposable, locally owned containers and files are used. No model or target
scan runs. A workflow timeout must bound Docker daemon stalls in addition to the
API, command and asynchronous deadlines here. Cleanup failures fail acceptance.
"""

# Application imports wait until main disables dotenv loading.
# ruff: noqa: PLC0415
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


IMAGE = (
    "ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca"
    "@sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30"
)
PARENT = (
    "ghcr.io/usestrix/strix-sandbox"
    "@sha256:c8e70302ba4a69b678b4001c909b134d8d0b84625d7b23feed59ece02672c1d2"
)
AMD64_CONFIG = "sha256:e72efc9fe87c6fa1d1dc89786c418d8c7703a120f5b6e3ec42e8e7c6a34d51e1"
AMD64_MANIFEST = "sha256:1465e11e35945996c729f9b731f52fb39280d1a9a299260bc63ab5a824af7604"
ENTRYPOINT_SHA256 = "cbdb5ca54544c2f7989a4981133e5faa5bfe4f65ee15d67e106082e95029b818"
FIXTURE_IMAGE = "shvia-strix-network-fixture:1"
GUARD_IMAGE = "shvia-strix-network-guard:1"
NSS_DB = "sql:/home/pentester/.pki/nssdb"
SENTINEL = "Strix Runtime Acceptance Sentinel"
REPOSITORY = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Keep acceptance gates active even if Python is invoked with optimization."""
    if not condition:
        raise RuntimeError(message)


def architecture(value: str) -> str:
    return "amd64" if value.lower() in {"amd64", "x86_64"} else value.lower()


class Acceptance:
    def __init__(self, report: Path, expected_architecture: str) -> None:
        self.report = report.resolve()
        self.expected_architecture = expected_architecture
        self.run_id = "strix-runtime-acceptance-" + uuid.uuid4().hex[:12]
        self.data: dict[str, Any] = {
            "run_id": self.run_id,
            "image": IMAGE,
            "expected_amd64_manifest": AMD64_MANIFEST,
            "expected_amd64_config": AMD64_CONFIG,
            "checks": {},
            "resources": {},
            "passed": False,
        }
        self.docker: Any = None
        self.client: Any = None
        self.session: Any = None
        self.caido: Any = None
        self.sandbox: Any = None
        self.guard: Any = None
        self.target: Any = None
        self.target_ip = ""
        self.policy_digest = ""
        self.fixture: Path | None = None
        self.owned_ids: set[str] = set()

    def save(self) -> None:
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def record(self, name: str, value: Any = True) -> None:
        self.data["checks"][name] = value
        self.save()
        sys.stdout.write(json.dumps({"check": name, "value": value}) + "\n")
        sys.stdout.flush()

    def own(self, name: str, container: Any) -> None:
        self.owned_ids.add(container.id)
        self.data["resources"][name] = container.id
        self.save()

    @staticmethod
    def execute(container: Any, args: list[str], *, success: bool = True, user: str = "0") -> Any:
        result = container.exec_run(["timeout", "20", *args], user=user)
        if success:
            require(
                result.exit_code == 0,
                f"Fixture command failed: {args[:2]}: {result.output[-1000:]!r}",
            )
        return result

    def connects(self, container: Any, address: str, port: int) -> bool:
        code = """
import socket, sys
with socket.socket() as sock:
    sock.settimeout(0.7)
    try:
        sock.connect((sys.argv[1], int(sys.argv[2])))
        print('connected')
    except OSError:
        print('blocked')
"""
        result = self.execute(container, ["python3", "-c", code, address, str(port)])
        return bool(result.output.strip() == b"connected")

    def preflight(self) -> None:
        from strix.config.settings import RuntimeSettings
        from strix.runtime.docker_connection import connect_docker

        self.data["host"] = {"system": platform.system(), "machine": platform.machine()}
        self.save()
        require(platform.system() == "Linux", "This acceptance requires a native Linux host")
        require(
            architecture(platform.machine()) == self.expected_architecture,
            "Host is not native amd64",
        )
        require(
            RuntimeSettings().image == IMAGE,
            "Runtime default differs from the selected immutable image",
        )
        self.docker = connect_docker()
        info = self.docker.info()
        self.record("docker_architecture", info["Architecture"])
        require(
            architecture(info["Architecture"]) == self.expected_architecture,
            "Docker daemon is not amd64",
        )
        require(info["OSType"] == "linux", "Docker daemon is not Linux")
        image = self.docker.images.get(IMAGE)  # All pulls/builds are explicit workflow steps.
        parent = self.docker.images.get(PARENT)
        self.data["image_id"] = image.id
        self.data["image_repo_digests"] = image.attrs.get("RepoDigests", [])
        self.record("image_architecture", image.attrs["Architecture"])
        require(image.attrs["Architecture"] == "amd64", "Selected image is not amd64")
        require(image.id == AMD64_CONFIG, "Selected image config does not match the amd64 witness")
        require(parent.attrs["Architecture"] == "amd64", "Parent image is not amd64")
        require(
            image.attrs["RootFS"]["Layers"][:-1] == parent.attrs["RootFS"]["Layers"],
            "Derived rootfs must retain all parent layers and add exactly one",
        )
        self.record("official_parent_layers_plus_one")
        for name in (FIXTURE_IMAGE, GUARD_IMAGE):
            fixture = self.docker.images.get(name)
            require(
                fixture.attrs["Architecture"] == "amd64",
                f"Fixture image is not native amd64: {name}",
            )
        git = shutil.which("git")
        require(git is not None, "Git is required to record checkout provenance")
        result = subprocess.run(  # noqa: S603 -- Fixed read-only command in this checkout.
            [str(git), "rev-parse", "HEAD"],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.record("source_sha", result.stdout.strip())

    async def start(self) -> None:
        from agents.sandbox.manifest import Environment, Manifest

        from strix.runtime.backends import _docker_backend
        from strix.runtime.network_policy import NetworkPolicy
        from strix.runtime.session_manager import (
            _host_identity_env,
            build_bind_mounts,
            build_sandbox_environment,
            protect_run_mounts,
        )

        server = """
import socket, time
sock = socket.socket()
sock.bind(('0.0.0.0', 18080))
sock.listen(20)
print('READY', flush=True)
while True:
    connection, _ = sock.accept()
    connection.close()
"""
        self.target = self.docker.containers.create(
            FIXTURE_IMAGE,
            ["python3", "-u", "-c", server],
            name=self.run_id + "-target",
            network_mode="bridge",
            labels={"strix-owned-acceptance": self.run_id},
        )
        self.own("target", self.target)
        self.target.start()
        deadline = time.monotonic() + 15
        while b"READY" not in self.target.logs(tail=5):
            require(time.monotonic() < deadline, "Owned TCP fixture did not start")
            await asyncio.sleep(0.1)
        self.target.reload()
        self.target_ip = self.target.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        require(
            self.connects(self.target, self.target_ip, 18080), "Owned TCP positive control failed"
        )
        self.record("owned_tcp_positive_control")
        policy = NetworkPolicy(hosts={self.run_id + ".invalid": self.target_ip})
        self.policy_digest = policy.digest
        self.data["policy_digest"] = policy.digest
        self.fixture = Path(tempfile.mkdtemp(prefix=self.run_id + "-"))
        self.data["fixture_directory"] = str(self.fixture)
        source = self.fixture / "readonly"
        metadata = self.fixture / "metadata"
        run_dir = self.fixture / "run-source" / "runs" / "current"
        for path in (source, metadata / ".git", run_dir):
            path.mkdir(parents=True)
        for path, value in (
            (source / "source.txt", "owned-source"),
            (metadata / ".git" / "config", "owned-git"),
            (run_dir / "policy.json", "owned-policy"),
        ):
            path.write_text(value, encoding="utf-8")
        mounts = build_bind_mounts(
            [
                {"source_path": str(source), "workspace_subdir": "readonly", "read_only": True},
                {
                    "source_path": str(metadata),
                    "workspace_subdir": "metadata",
                    "protect_metadata": True,
                },
                {"source_path": str(self.fixture / "run-source"), "workspace_subdir": "run-source"},
            ]
        )
        protect_run_mounts(mounts, run_dir)
        self.save()
        started = time.monotonic()
        self.client, self.session = await asyncio.wait_for(
            _docker_backend(
                image=IMAGE,
                manifest=Manifest(
                    environment=Environment(
                        value={**build_sandbox_environment(), **_host_identity_env()}
                    )
                ),
                exposed_ports=(48080,),
                bind_mounts=mounts,
                network_policy=policy,
            ),
            timeout=120,
        )
        self.sandbox = self.docker.containers.get(self.session._inner.state.container_id)
        self.guard = self.client.network_guard.container
        self.own("sandbox", self.sandbox)
        self.own("guard", self.guard)
        self.sandbox.rename(self.run_id + "-sandbox")
        self.guard.rename(self.run_id + "-guard")
        self.record("backend_started_seconds", round(time.monotonic() - started, 3))
        await self.ready(1)
        self.check_runtime()

    def check_runtime(self) -> None:
        machine = self.execute(self.sandbox, ["uname", "-m"]).output.decode().strip()
        self.record("container_uname", machine)
        require(architecture(machine) == "amd64", "Container uname is not amd64")
        entrypoint = self.execute(
            self.sandbox, ["cat", "/usr/local/bin/docker-entrypoint.sh"]
        ).output
        digest = hashlib.sha256(entrypoint).hexdigest()
        self.record("entrypoint_sha256", digest)
        require(
            digest == ENTRYPOINT_SHA256,
            "Published entrypoint differs from the reviewed fork source",
        )
        version = (
            self.execute(self.sandbox, ["timeout", "15", "opengrep", "--version"], user="")
            .output.decode()
            .strip()
        )
        require(version == "1.31.0", "Unexpected Opengrep version")
        self.record("opengrep_version", version)
        identity = self.execute(
            self.sandbox,
            [
                "python3",
                "-c",
                "import json,os,pwd; print(json.dumps({'uid':os.getuid(),"
                "'name':pwd.getpwuid(os.getuid()).pw_name,'home':os.environ.get('HOME')}))",
            ],
            user="",
        )
        user = json.loads(identity.output)
        require(
            user["uid"] == os.getuid() and user["name"] == "pentester",
            "Sandbox user was not mapped to the Linux host UID",
        )
        self.record("sandbox_effective_user", user)

    async def ready(self, expected: int) -> None:
        deadline = time.monotonic() + 40
        while True:
            self.sandbox.reload()
            logs = self.sandbox.logs(tail=150)
            require(self.sandbox.status == "running", "Sandbox exited during entrypoint")
            require(
                b"Invalid password.  Try again." not in logs,
                "NSS initialization entered its password loop",
            )
            if logs.count(b"Container ready") >= expected:
                self.record(f"entrypoint_ready_{expected}", self.sandbox.id)
                return
            require(time.monotonic() < deadline, "Entrypoint readiness deadline expired")
            await asyncio.sleep(0.2)

    def mounts(self, phase: str) -> None:
        require(self.fixture is not None, "Fixture directory is missing")
        assert self.fixture is not None
        code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('changed')"
        for path in (
            "/workspace/readonly/source.txt",
            "/workspace/metadata/.git/config",
            "/workspace/run-source/runs/current/policy.json",
        ):
            result = self.execute(self.sandbox, ["python3", "-c", code, path], success=False)
            require(
                result.exit_code != 0 and b"Read-only file system" in result.output,
                f"Read-only mount failed: {path}",
            )
        self.execute(
            self.sandbox, ["python3", "-c", code, "/workspace/metadata/writable-control.txt"]
        )
        for relative, expected in (
            ("readonly/source.txt", "owned-source"),
            ("metadata/.git/config", "owned-git"),
            ("run-source/runs/current/policy.json", "owned-policy"),
            ("metadata/writable-control.txt", "changed"),
        ):
            require(
                (self.fixture / relative).read_text(encoding="utf-8") == expected,
                f"Host fixture content differs: {relative}",
            )
        self.record(phase + "_root_readonly_mounts_and_writable_control")

    def network(self, phase: str) -> None:
        require(
            self.connects(self.target, self.target_ip, 18080), "TCP control stopped being reachable"
        )
        require(
            not self.connects(self.sandbox, self.target_ip, 18080),
            "Default-deny allowed the owned target",
        )
        for executable in ("iptables-save", "ip6tables-save"):
            rules = self.execute(self.guard, [executable]).output.decode()
            require(
                ":OUTPUT DROP" in rules and ":FORWARD DROP" in rules,
                f"Missing firewall defaults: {executable}",
            )
        self.sandbox.reload()
        caps = self.sandbox.attrs["HostConfig"]["CapDrop"]
        require(
            {"NET_ADMIN", "NET_RAW", "SYS_ADMIN"} <= set(caps), "Fork capability drops are missing"
        )
        self.guard.reload()
        ip = self.guard.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        require(not self.connects(self.target, ip, 48080), "Peer reached the control port")
        counters = self.client.network_guard.denied_packets()
        require(counters["ipv4"]["packets"] >= 1, "No denied packets were counted")
        self.record(
            phase + "_network", {"counters": counters, "cap_drop": caps, "peer_blocked": True}
        )

    async def services(self, phase: str) -> None:
        from strix.runtime.caido_bootstrap import bootstrap_caido

        endpoint = await self.session.resolve_exposed_port(48080)
        require(endpoint.host == "127.0.0.1", "Control endpoint is not loopback")
        self.caido = await asyncio.wait_for(
            bootstrap_caido(
                self.session,
                host_url=f"http://{endpoint.host}:{endpoint.port}",
                container_url="http://127.0.0.1:48080",
                scan_id=self.run_id,
                authorized_targets=[],
                wait_timeout_s=35,
            ),
            timeout=50,
        )
        self.record(phase + "_caido_guest_bootstrap")
        require(self.session.supports_pty(), "Real SDK session does not support PTY")
        update = await self.session.pty_exec_start(
            "python3",
            "-u",
            "-c",
            "print('PTY_READY',flush=True); value=input(); print('PTY_ECHO:'+value,flush=True)",
            shell=False,
            tty=True,
            timeout=15,
            yield_time_s=0.2,
        )
        require(update.process_id is not None, "PTY exited before receiving stdin")
        process_id = update.process_id
        output = update.output
        update = await self.session.pty_write_stdin(
            session_id=process_id, chars="owned-fixture\n", yield_time_s=0.2
        )
        output += update.output
        deadline = time.monotonic() + 10
        while update.exit_code is None:
            require(time.monotonic() < deadline, "PTY did not exit")
            update = await self.session.pty_write_stdin(
                session_id=process_id, chars="", yield_time_s=0.2
            )
            output += update.output
        require(
            update.exit_code == 0
            and b"PTY_READY" in output
            and b"PTY_ECHO:owned-fixture" in output,
            "PTY stdin/poll/output failed",
        )
        self.record(phase + "_pty_start_stdin_poll_exit")
        await self.caido.aclose()
        self.caido = None

    def certificates(self, phase: str) -> None:
        for nickname in ("Testing Root CA", SENTINEL):
            result = self.execute(
                self.sandbox,
                ["certutil", "-L", "-n", nickname, "-a", "-d", NSS_DB],
                user="",
            )
            require(
                b"BEGIN CERTIFICATE" in result.output, f"NSS certificate is missing: {nickname}"
            )
            digest = hashlib.sha256(result.output).hexdigest()
            key = nickname.replace(" ", "_")
            if phase == "after_restart":
                require(
                    digest == self.data["checks"]["before_restart_cert_" + key],
                    f"NSS certificate changed on restart: {nickname}",
                )
            self.record(phase + "_cert_" + key, digest)

    async def restart(self) -> None:
        directory = (
            self.execute(
                self.sandbox,
                [
                    "python3",
                    "-c",
                    "import tempfile; print(tempfile.mkdtemp(prefix='strix-nss-acceptance-'))",
                ],
                user="",
            )
            .output.decode()
            .strip()
        )
        key, certificate = directory + "/sentinel.key", directory + "/sentinel.crt"
        self.execute(
            self.sandbox,
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                key,
                "-out",
                certificate,
                "-days",
                "1",
                "-subj",
                "/CN=Strix Runtime Acceptance Sentinel",
            ],
            user="",
        )
        try:
            self.execute(
                self.sandbox,
                [
                    "certutil",
                    "-A",
                    "-n",
                    SENTINEL,
                    "-t",
                    "C,,",
                    "-i",
                    certificate,
                    "-d",
                    NSS_DB,
                ],
                user="",
            )
        finally:
            self.execute(self.sandbox, ["rm", "-f", key], user="")
        self.certificates("before_restart")
        container_id = self.sandbox.id
        self.sandbox.reload()
        started_at = self.sandbox.attrs["State"]["StartedAt"]
        self.sandbox.stop(timeout=2)
        self.sandbox.start()
        await self.ready(2)
        self.sandbox.reload()
        require(self.sandbox.id == container_id, "Restart replaced the sandbox")
        require(
            self.sandbox.attrs["State"]["StartedAt"] != started_at, "Container was not restarted"
        )
        self.certificates("after_restart")
        self.record("same_container_restart", container_id)

    def clean_fixture(self) -> None:
        if self.fixture is None:
            return
        try:
            shutil.rmtree(self.fixture)
        except PermissionError:
            # Only this mkdtemp directory is mounted; no host checkout or parent.
            helper = self.docker.containers.create(
                FIXTURE_IMAGE,
                [
                    "python3",
                    "-c",
                    "from pathlib import Path; import shutil; p=Path('/owned-fixture'); "
                    "[(shutil.rmtree(c) if c.is_dir() and not c.is_symlink() "
                    "else c.unlink()) for c in p.iterdir()]",
                ],
                name=self.run_id + "-cleanup",
                user="0",
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                cap_add=["DAC_OVERRIDE", "FOWNER"],
                volumes={str(self.fixture): {"bind": "/owned-fixture", "mode": "rw"}},
                labels={"strix-owned-acceptance": self.run_id},
            )
            self.own("cleanup_helper", helper)
            try:
                helper.start()
                require(
                    helper.wait(timeout=20)["StatusCode"] == 0,
                    "Owned fixture cleanup helper failed",
                )
            finally:
                helper.remove(force=True)
            self.fixture.rmdir()
        self.data["cleanup_fixture_removed"] = not self.fixture.exists()
        require(not self.fixture.exists(), "Owned fixture directory remains")

    async def cleanup(self) -> None:
        errors: list[str] = []
        if self.caido is not None:
            try:
                await asyncio.wait_for(self.caido.aclose(), timeout=5)
            except Exception as exc:  # noqa: BLE001 -- Keep the original failure and try all cleanup.
                errors.append(f"Caido close: {exc}")
        if self.client is not None:
            self.client.docker_client.api.timeout = 10
            try:
                if self.session is not None:
                    await asyncio.wait_for(self.client.delete(self.session), timeout=20)
            except Exception as exc:  # noqa: BLE001 -- Exact owned IDs are removed below.
                errors.append(f"Session delete: {exc}")
            self.client.docker_client.close()
        if self.docker is not None:
            self.docker.api.timeout = 10
            labels = ["strix-run-id=" + self.run_id, "strix-owned-acceptance=" + self.run_id]
            if self.policy_digest:
                labels.append("shvia.network-policy=" + self.policy_digest)
            try:
                for label in labels:
                    self.owned_ids.update(
                        c.id
                        for c in self.docker.containers.list(all=True, filters={"label": label})
                    )
                remaining = {c.id: c for c in self.docker.containers.list(all=True)}
                for container_id in self.owned_ids & remaining.keys():
                    remaining[container_id].remove(force=True)
                self.clean_fixture()
                remaining_ids = {c.id for c in self.docker.containers.list(all=True)}
                self.data["cleanup_remaining_owned_ids"] = sorted(self.owned_ids & remaining_ids)
                require(not self.data["cleanup_remaining_owned_ids"], "Owned containers remain")
            except Exception as exc:  # noqa: BLE001 -- Report cleanup separately from the first error.
                errors.append(f"Owned resources: {exc}")
            finally:
                self.docker.close()
        self.data["cleanup_errors"] = errors
        if errors:
            self.data["passed"] = False
        self.save()

    async def run(self) -> int:
        os.environ["STRIX_RUN_ID"] = self.run_id
        os.environ["STRIX_RUN_TYPE"] = "owned-image-acceptance"
        task = asyncio.current_task()
        if task is not None and sys.platform != "win32":
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            async with asyncio.timeout(300):
                self.preflight()
                await self.start()
                for phase in ("before_restart", "after_restart"):
                    if phase == "after_restart":
                        await self.restart()
                    self.network(phase)
                    self.mounts(phase)
                    await self.services(phase)
                self.data["passed"] = True
        except BaseException as exc:  # noqa: BLE001 -- Record cancellation before owned cleanup.
            self.data["error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
            self.save()
        finally:
            await self.cleanup()
        self.record("acceptance_passed", self.data["passed"])
        return 0 if self.data["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-architecture", choices=["amd64"], default="amd64")
    args = parser.parse_args()
    # Set before importing the application: LiteLLM must not load a checkout .env.
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["STRIX_TELEMETRY"] = "false"
    os.environ["STRIX_NO_UPDATE_CHECK"] = "1"
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    return asyncio.run(Acceptance(args.report, args.expected_architecture).run())


if __name__ == "__main__":
    raise SystemExit(main())
