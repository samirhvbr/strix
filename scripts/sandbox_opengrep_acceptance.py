#!/usr/bin/env python3
"""Scan synthetic fixtures with every bundled Opengrep rule on native Linux amd64.

Pull IMAGE explicitly first; this harness never pulls an image or contacts a target.
Run with Python 3.12 and an isolated HOME/environment::

    python scripts/sandbox_opengrep_acceptance.py --report /tmp/evidence/opengrep-result.json

Evidence files named opengrep-* are written beside --report (or in --evidence-dir).
The scanner container has no network, host mounts or published ports. Its entrypoint
is overridden with sleep for this SAST check; this does not replace runtime acceptance.
A 600-second scanner deadline allows headroom over the earlier 169.247-second arm64
observation. That observation is not a performance baseline for native amd64.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import signal
import subprocess  # nosec B404
import sys
import time
import uuid
from pathlib import Path
from typing import Any


IMAGE = (
    "ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca"
    "@sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30"
)
AMD64_CONFIG = "sha256:e72efc9fe87c6fa1d1dc89786c418d8c7703a120f5b6e3ec42e8e7c6a34d51e1"
AMD64_MANIFEST = "sha256:1465e11e35945996c729f9b731f52fb39280d1a9a299260bc63ab5a824af7604"
RULES = "/opt/opengrep-rules"
# This path exists only inside the owned, disposable scanner container.
WORK = "/tmp/strix-opengrep-fixture"  # noqa: S108  # nosec B108
SCAN_TIMEOUT = 600
REPOSITORY = Path(__file__).resolve().parents[1]
LABEL = "strix-owned-opengrep-acceptance"
RULE_PREFIX = "opt.opengrep-rules."
EXPECTED_RESULTS = {
    (RULE_PREFIX + "python.lang.security.audit.eval-detected", "src/positive.py", 1),
    (RULE_PREFIX + "strix.github-owned-action-not-pinned-to-commit-sha", "workflows/tag.yml", 7),
    (RULE_PREFIX + "strix.github-owned-action-not-pinned-to-commit-sha", "workflows/branch.yml", 7),
    (
        RULE_PREFIX + "yaml.github-actions.security.third-party-action-not-pinned-to-commit-sha",
        "workflows/other-owner.yml",
        7,
    ),
}
CATALOG_CODE = """
import hashlib, json
from pathlib import Path
root = Path('/opt/opengrep-rules')
files = []
for path in sorted(root.rglob('*')):
    if path.is_symlink():
        raise RuntimeError('Unexpected symlink in the pinned rule bundle: ' + str(path))
    if path.is_file():
        content = path.read_bytes()
        files.append({'path': str(path.relative_to(root)), 'bytes': len(content),
                      'sha256': hashlib.sha256(content).hexdigest()})
encoded = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
yaml_files = [f for f in files if Path(f['path']).suffix in {'.yaml', '.yml'}]
print(json.dumps({'root': str(root), 'files': files, 'total_files': len(files),
                  'yaml_files': len(yaml_files), 'yaml_bytes': sum(f['bytes'] for f in yaml_files),
                  'catalog_sha256': hashlib.sha256(encoded).hexdigest()}))
"""
SETUP_CODE = """
import hashlib, json, sys
from pathlib import Path
root = Path('/tmp/strix-opengrep-fixture')
root.mkdir()
files = json.load(sys.stdin)
result = []
for relative, content in sorted(files.items()):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    result.append({'path': relative, 'bytes': path.stat().st_size,
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
print(json.dumps(result))
"""


ELF_CODE = """
import hashlib, json
from pathlib import Path
path = Path('/usr/local/bin/opengrep')
sha = hashlib.sha256()
with path.open('rb') as stream:
    header = stream.read(64)
    if len(header) != 64 or header[:4] != b'\\x7fELF':
        raise RuntimeError('Opengrep launcher is not an ELF file')
    sha.update(header)
    while block := stream.read(1024 * 1024):
        sha.update(block)
print(json.dumps({'path': str(path), 'elf_class': header[4], 'endianness': header[5],
                  'e_machine': int.from_bytes(header[18:20], 'little'),
                  'bytes': path.stat().st_size, 'sha256': sha.hexdigest()}))
"""


def require(condition: bool, message: str) -> None:
    """Acceptance remains active under python -O."""
    if not condition:
        raise RuntimeError(message)


def fixtures() -> dict[str, str]:
    """These source files are scanned as text, never executed."""
    files = {
        "src/positive.py": 'value = eval(input("expression: "))\n',
        "src/clean.py": 'value = int("123")\n',
    }
    actions = {
        "tag": "actions/checkout@v4",
        "branch": "github/codeql-action/init@main",
        "sha": "actions/checkout@" + "a" * 40,
        "local": "./local-action",
        "other-owner": "synthetic-owner/action@v1",
    }
    for name, action in actions.items():
        files[f"workflows/{name}.yml"] = (
            "name: Offline fixture\non: push\njobs:\n  fixture:\n"
            f"    runs-on: ubuntu-latest\n    steps:\n      - uses: {action}\n"
        )
    return files


def command(
    args: list[str], *, stdin: bytes | None = None, timeout: int = 30, success: bool = True
) -> subprocess.CompletedProcess[bytes]:
    # All callers build argument lists from fixed commands and owned container IDs.
    result = subprocess.run(  # noqa: S603  # nosec B603
        args, input=stdin, capture_output=True, timeout=timeout, check=False
    )
    if success:
        require(result.returncode == 0, f"Command failed: {args[:3]}: {result.stderr[-1500:]!r}")
    return result


def scan_summary(stderr: str) -> dict[str, int]:
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stderr)
    loaded = re.findall(r"Scanning\s+([\d,]+)\s+files?\s+with\s+([\d,]+)\s+Code\s+rules?", clean)
    applied = re.findall(
        r"Ran\s+([\d,]+)\s+rules?\s+on\s+([\d,]+)\s+files?:\s+([\d,]+)\s+findings?", clean
    )
    require(len(loaded) == 1 and len(applied) == 1, "Missing or ambiguous scanner rule counts")
    numbers = [int(value.replace(",", "")) for value in (*loaded[0], *applied[0])]
    return dict(
        zip(
            ("files_selected", "rules_loaded", "rules_applied", "files_scanned", "findings"),
            numbers,
            strict=True,
        )
    )


class Acceptance:
    def __init__(self, report: Path, evidence: Path) -> None:
        self.report, self.evidence = report.resolve(), evidence.resolve()
        self.run_id = "strix-opengrep-acceptance-" + uuid.uuid4().hex[:12]
        self.container: str | None = None
        self.create_attempted = False
        self.data: dict[str, Any] = {
            "run_id": self.run_id,
            "image": IMAGE,
            "expected_amd64_manifest": AMD64_MANIFEST,
            "expected_amd64_config": AMD64_CONFIG,
            "checks": {},
            "passed": False,
            "scope": "Complete bundled rules on seven synthetic files; offline SAST only",
            "limits": [
                "Entrypoint overridden for SAST; startup/restart/network gates are separate.",
                "All bundled rules are loaded; only applicable Python/YAML rules are applied.",
                "--taint-intrafile is enabled; seven fixtures do not validate every rule or flow.",
                "Elapsed time is an observation, without a cross-architecture comparison.",
            ],
        }
        self.outputs = {
            "scan_json": self.evidence / "opengrep-scan.json",
            "scan_stderr": self.evidence / "opengrep-scan.stderr.log",
            "rules_catalog": self.evidence / "opengrep-rules-catalog.json",
            "fixtures": self.evidence / "opengrep-fixtures.json",
        }

    def save(self) -> None:
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def record(self, key: str, value: Any = True) -> None:
        self.data["checks"][key] = value
        self.save()
        sys.stdout.write(json.dumps({"check": key, "value": value}) + "\n")
        sys.stdout.flush()

    def execute(
        self, args: list[str], *, stdin: bytes | None = None, timeout: int = 30
    ) -> subprocess.CompletedProcess[bytes]:
        if self.container is None:
            raise RuntimeError("Scanner container is not available")
        return command(
            ["docker", "exec", "-i", self.container, "timeout", str(timeout - 5), *args],
            stdin=stdin,
            timeout=timeout,
        )

    def preflight(self) -> None:
        self.data["host"] = {"system": platform.system(), "machine": platform.machine()}
        require(
            platform.system() == "Linux" and platform.machine() in {"amd64", "x86_64"},
            "This acceptance requires a native Linux amd64 host",
        )
        info = json.loads(command(["docker", "info", "--format", "{{json .}}"]).stdout)
        require(
            info["OSType"] == "linux" and info["Architecture"] in {"amd64", "x86_64"},
            "Docker daemon is not native Linux amd64",
        )
        self.record("docker_architecture", info["Architecture"])
        image = json.loads(command(["docker", "image", "inspect", IMAGE]).stdout)[0]
        self.data["image_id"] = image["Id"]
        self.data["image_repo_digests"] = image.get("RepoDigests", [])
        require(
            image["Architecture"] == "amd64" and image["Id"] == AMD64_CONFIG,
            "Selected image does not match the published amd64 config",
        )
        require(
            any(
                value.endswith("@" + IMAGE.rsplit("@", 1)[1])
                for value in image.get("RepoDigests", [])
            ),
            "Selected image does not carry the pinned index digest",
        )
        self.record("image_architecture", image["Architecture"])
        source = (
            command(["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"]).stdout.decode().strip()
        )
        self.record("source_sha", source)

    def start(self) -> None:
        self.create_attempted = True
        result = command(
            [
                "docker",
                "create",
                "--pull=never",
                "--name",
                self.run_id,
                "--label",
                LABEL + "=" + self.run_id,
                "--network",
                "none",
                "--log-opt",
                "max-size=1m",
                "--log-opt",
                "max-file=1",
                "--entrypoint",
                "sleep",
                IMAGE,
                "infinity",
            ]
        )
        self.container = result.stdout.decode().strip()
        self.data["container_id"] = self.container
        self.save()
        command(["docker", "start", self.container])
        info = json.loads(command(["docker", "inspect", self.container]).stdout)[0]
        require(info["State"]["Running"], "Scanner container did not start")
        require(
            info["HostConfig"]["NetworkMode"] == "none"
            and not info["Mounts"]
            and not info["HostConfig"]["PortBindings"],
            "Scanner isolation differs",
        )
        require(info["Config"]["Entrypoint"] == ["sleep"], "Unexpected SAST entrypoint")
        self.record(
            "isolation", {"network": "none", "mounts": [], "ports": {}, "entrypoint": ["sleep"]}
        )
        machine = self.execute(["uname", "-m"]).stdout.decode().strip()
        require(machine in {"amd64", "x86_64"}, "Scanner container is not amd64")
        self.record("container_uname", machine)
        binary = json.loads(self.execute(["python3", "-c", ELF_CODE]).stdout)
        require(
            binary["elf_class"] == 2 and binary["endianness"] == 1 and binary["e_machine"] == 62,
            "Opengrep launcher is not ELF64 x86-64",
        )
        self.record("opengrep_binary", binary)
        version = self.execute(["opengrep", "--version"]).stdout.decode().strip()
        require(version == "1.31.0", "Opengrep version differs from the pinned image")
        self.record("opengrep_version", version)
        result = self.execute(["python3", "-c", SETUP_CODE], stdin=json.dumps(fixtures()).encode())
        self.outputs["fixtures"].write_bytes(result.stdout)
        manifest = json.loads(result.stdout)
        require(
            {entry["path"] for entry in manifest} == set(fixtures()), "Fixture file set differs"
        )
        self.record("synthetic_files", manifest)

    def catalog(self, *, initial: bool) -> dict[str, Any]:
        result = self.execute(["python3", "-c", CATALOG_CODE])
        catalog: dict[str, Any] = json.loads(result.stdout)
        require(catalog["yaml_files"] == 1933, "Pinned bundle YAML file inventory differs")
        if initial:
            self.outputs["rules_catalog"].write_bytes(result.stdout)
            self.record(
                "rules_catalog", {key: value for key, value in catalog.items() if key != "files"}
            )
        return catalog

    def scan(self) -> None:
        if self.container is None:
            raise RuntimeError("Scanner container is not available")
        args = [
            "docker",
            "exec",
            "-w",
            WORK,
            self.container,
            "timeout",
            "--signal=TERM",
            "--kill-after=10s",
            str(SCAN_TIMEOUT),
            "opengrep",
            "--json",
            "--disable-version-check",
            "--no-git-ignore",
            "--strict",
            "--config",
            RULES,
            "--taint-intrafile",
            "src",
            "workflows",
        ]
        started = time.monotonic()
        self.data["scan"] = {"command": args, "timeout_seconds": SCAN_TIMEOUT}
        self.save()
        try:
            with (
                self.outputs["scan_json"].open("wb") as stdout,
                self.outputs["scan_stderr"].open("wb") as stderr,
            ):
                result = subprocess.run(  # noqa: S603  # nosec B603
                    args, stdout=stdout, stderr=stderr, timeout=SCAN_TIMEOUT + 30, check=False
                )
            self.data["scan"]["exit_code"] = result.returncode
            require(result.returncode == 0, f"Complete rule bundle scan exited {result.returncode}")
        finally:
            self.data["scan"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.save()
        payload = json.loads(self.outputs["scan_json"].read_text(encoding="utf-8"))
        require(
            payload.get("errors") == [], "Opengrep returned errors; inspect complete JSON evidence"
        )
        require(payload.get("version") == "1.31.0", "JSON engine version differs")
        scanned = payload.get("paths", {}).get("scanned", [])
        require(
            len(scanned) == len(fixtures()) and set(scanned) == set(fixtures()),
            "Scanned paths differ from all seven fixtures",
        )
        findings = payload.get("results", [])
        actual = {(entry["check_id"], entry["path"], entry["start"]["line"]) for entry in findings}
        require(
            len(findings) == len(EXPECTED_RESULTS) and actual == EXPECTED_RESULTS,
            f"Unexpected complete-bundle findings: {sorted(actual)}",
        )
        summary = scan_summary(self.outputs["scan_stderr"].read_text(encoding="utf-8"))
        require(
            summary
            == {
                "files_selected": 7,
                "rules_loaded": 2006,
                "rules_applied": 706,
                "files_scanned": 7,
                "findings": len(EXPECTED_RESULTS),
            },
            f"Complete-bundle rule counts differ: {summary}",
        )
        self.record(
            "full_bundle",
            {
                "errors": [],
                "scanned_paths": sorted(scanned),
                "findings": [
                    {"check_id": rule, "path": path, "line": line}
                    for rule, path, line in sorted(actual)
                ],
                **summary,
            },
        )

    def cleanup(self) -> None:
        if not self.create_attempted:
            self.data.update(
                cleanup_remaining_owned_ids=[], cleanup_errors=[], cleanup_fixture_removed=True
            )
            return
        errors = []
        owned = {self.container} if self.container else set()
        try:
            found = command(
                ["docker", "ps", "-aq", "--filter", "label=" + LABEL + "=" + self.run_id]
            )
            owned.update(found.stdout.decode().split())
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            errors.append(str(exc))
        for container_id in owned:
            try:
                command(["docker", "rm", "-f", container_id], timeout=30)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                errors.append(str(exc))
        try:
            found = command(
                ["docker", "ps", "-aq", "--filter", "label=" + LABEL + "=" + self.run_id]
            )
            remaining = found.stdout.decode().split()
            self.data["cleanup_remaining_owned_ids"] = remaining
            require(not remaining, "Owned scanner containers remain")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            errors.append(str(exc))
        self.data["cleanup_errors"] = errors
        self.data["cleanup_fixture_removed"] = not errors
        if errors:
            self.data["passed"] = False

    def run(self) -> int:
        require(not self.report.exists(), "Refusing to overwrite an earlier report")
        require(
            all(not path.exists() for path in self.outputs.values()), "Evidence files already exist"
        )
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.save()
        try:
            self.preflight()
            self.start()
            before = self.catalog(initial=True)
            self.scan()
            after = self.catalog(initial=False)
            require(before == after, "Rule bundle contents changed during the scan")
            self.record("rules_catalog_unchanged")
            self.data["passed"] = True
        except BaseException as exc:  # noqa: BLE001 - preserve interrupts and always clean up
            self.data["error"] = {"type": type(exc).__name__, "message": str(exc)[:3000]}
        finally:
            self.cleanup()
            self.data["evidence"] = [
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in self.outputs.values()
                if path.exists()
            ]
            self.save()
        sys.stdout.write(
            json.dumps({"report": str(self.report), "passed": self.data["passed"]}) + "\n"
        )
        return 0 if self.data["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()

    def interrupted(signum: int, _frame: Any) -> None:
        raise RuntimeError(f"Acceptance interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    return Acceptance(args.report, args.evidence_dir or args.report.parent).run()


if __name__ == "__main__":
    raise SystemExit(main())
