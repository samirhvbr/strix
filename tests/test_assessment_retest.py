"""Directed retest preserves ownership, live grants, evidence history and spending."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.assessment import bind_assessment_policy
from strix.core.assessment_context import FileCredentials, bind_context
from strix.core.assessment_retest import retest_assessment
from strix.core.assessment_review import review_assessment
from strix.core.evidence_ledger import EvidenceError, EvidenceLedger
from strix.core.identity_executor import IdentityExecutor
from strix.core.run_lease import RunAlreadyOwnedError, controller_lease
from strix.core.web_authorization import WebAuthorization, WebAuthorizationError
from strix.interface.assessment_retest import run_retest
from tests.test_assessment_context import config, write_credentials
from tests.test_authorization_case import executor_at
from tests.test_authorization_case import (
    private_server as private_server,  # noqa: PLC0414 -- Local fixture export.
)


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Controlled profile requires POSIX")


async def prepared(tmp_path: Path, port: int, monkeypatch: Any) -> dict[str, Any]:
    template = executor_at(tmp_path / "template", port)
    raw = template.context.model_dump()
    await template.close()
    policy, _ = config(port)
    policy = policy.model_copy(update={"version": 2})
    run = tmp_path / "scan"
    state = run / ".state"
    bind_assessment_policy(state, "scan", policy, resuming=False)
    state.chmod(0o700)
    context = bind_context(state, "scan", policy, raw, resuming=False)
    assert context is not None
    credentials = tmp_path / "credentials.json"
    write_credentials(credentials)
    ledger = EvidenceLedger(
        state / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=context.digest,
        owns_agent=lambda _: True,
    )
    executor = IdentityExecutor(context, FileCredentials(credentials, "assessment"), ledger)
    first = await executor.run_case(agent_ref="agent", case_ref="cross-tenant")
    await executor.close()
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps({"authority": "https://approval.invalid", "token": "a" * 64}))
    handoff.chmod(0o600)
    (run / "run.json").write_text(json.dumps({"llm_usage": {"cost": 9.8}, "status": "stopped"}))
    approvals = []

    def fetch(_self: Any, scan_id: str) -> Any:
        approvals.append(scan_id)
        return policy, context, 10.0

    monkeypatch.setattr(WebAuthorization, "fetch", fetch)
    return {
        "directory": run,
        "case_ref": "cross-tenant",
        "baseline_ref": first["case_result_ref"],
        "authorization_path": handoff,
        "credentials_path": credentials,
        "approvals": approvals,
    }


def arguments(fixture: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fixture.items() if key != "approvals"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("fixed", "fixed"),
        ("vulnerable", "not_fixed"),
        ("logged-out", "inconclusive"),
        ("broken-owner", "inconclusive"),
        ("expired", "inconclusive"),
    ],
)
async def test_real_target_retest_preserves_baseline_usage_and_private_data(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    mode: str,
    expected: str,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    run = fixture["directory"]
    before = (run / "run.json").read_bytes()
    baseline = review_assessment(run)["history"][0]
    target["mode"] = mode
    if mode == "expired":
        write_credentials(fixture["credentials_path"], expired=True)
    result = await retest_assessment(**arguments(fixture))
    assert result["retest_status"] == expected
    assert result["baseline_ref"] == baseline["case_result_ref"]
    assert (run / "run.json").read_bytes() == before
    report = review_assessment(run)
    assert report["history"][1] == baseline
    assert report["history"][0]["case_result_ref"] == result["case_result_ref"]
    assert fixture["approvals"] == ["scan"] * 7
    assert target["calls"] == (6 if mode == "expired" else 12)
    public = json.dumps(result) + json.dumps(report)
    for private in ["SYNTHETIC-PRIVATE", "tenant-a-token", "handoff.json", "credentials.json"]:
        assert private not in public


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["result", "artifact", "context", "missing", "case", "scope"])
async def test_rejected_baseline_or_scope_cannot_dispatch(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    damage: str,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    state = fixture["directory"] / ".state"
    if damage in {"result", "artifact"}:
        with sqlite3.connect(state / "evidence.db") as db:
            query = (
                "UPDATE case_results SET content=?"
                if damage == "result"
                else "UPDATE artifacts SET content=?"
            )
            db.execute(query, (b"{}",))
    elif damage == "missing":
        fixture["baseline_ref"] = "0" * 32
    elif damage == "case":
        fixture["case_ref"] = "other-case"
    elif damage == "scope":
        with sqlite3.connect(state / "evidence.db") as db:
            db.execute("DELETE FROM obligations")
    else:
        path = state / "assessment-context.json"
        data = json.loads(path.read_text())
        data["scan_id"] = "other-run"
        path.write_text(json.dumps(data))
    with pytest.raises((EvidenceError, ValueError)):
        await retest_assessment(**arguments(fixture))
    assert target["calls"] == 6
    assert fixture["approvals"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("after_claim", [False, True])
async def test_revocation_before_dispatch_produces_zero_new_target_requests(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    after_claim: bool,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    original = WebAuthorization.fetch
    count = 0

    def revoked(self: Any, scan_id: str) -> Any:
        nonlocal count
        count += 1
        if after_claim and count == 1:
            return original(self, scan_id)
        raise WebAuthorizationError("revoked")

    monkeypatch.setattr(WebAuthorization, "fetch", revoked)
    if after_claim:
        result = await retest_assessment(**arguments(fixture))
        assert result["retest_status"] == "inconclusive"
        assert review_assessment(fixture["directory"])["authorization_denials"] == 6
    else:
        with pytest.raises(WebAuthorizationError):
            await retest_assessment(**arguments(fixture))
    assert target["calls"] == 6


@pytest.mark.asyncio
async def test_exclusive_owner_rejects_retest_before_any_approval_or_request(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    with controller_lease(fixture["directory"] / ".state"), pytest.raises(RunAlreadyOwnedError):
        await retest_assessment(**arguments(fixture))
    assert fixture["approvals"] == []
    assert target["calls"] == 6


def test_cli_routes_without_generic_scan_setup_and_sanitizes_failure(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    main = importlib.import_module("strix.interface.main")
    args = [
        str(tmp_path / "missing"),
        "--case=cross-tenant",
        "--baseline=" + "0" * 32,
        "--web-authorization=" + str(tmp_path / "secret-handoff"),
        "--identity-credentials=" + str(tmp_path / "secret-credentials"),
    ]
    monkeypatch.setattr("sys.argv", ["strix", "retest", *args])
    monkeypatch.setattr(main, "parse_arguments", lambda: pytest.fail("generic scan setup"))
    with pytest.raises(SystemExit) as stopped:
        main.main()
    assert stopped.value.code == 1
    assert not (tmp_path / "missing").exists()
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err) == {"error": "assessment_retest_unavailable"}


def test_cli_success_outputs_only_metadata(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    port, target = private_server
    fixture = asyncio.run(prepared(tmp_path, port, monkeypatch))
    target["mode"] = "fixed"
    assert (
        run_retest(
            [
                str(fixture["directory"]),
                "--case=cross-tenant",
                "--baseline=" + fixture["baseline_ref"],
                "--web-authorization=" + str(fixture["authorization_path"]),
                "--identity-credentials=" + str(fixture["credentials_path"]),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert not output.err
    assert json.loads(output.out)["retest_status"] == "fixed"
    assert "PRIVATE" not in output.out


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["policy", "context", "budget"])
async def test_live_authority_cannot_change_scope_or_raise_a_reduced_ceiling(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    change: str,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    original = WebAuthorization.fetch
    count = 0

    def changed(self: Any, scan_id: str) -> Any:
        nonlocal count
        count += 1
        policy, context, budget = original(self, scan_id)
        if change == "policy":
            policy = policy.model_copy(update={"authorization_ref": "other-grant"})
        elif change == "context":
            context = context.model_copy(update={"environment_ref": "other-environment"})
        elif count > 1:
            budget = 5.0
        return policy, context, budget

    monkeypatch.setattr(WebAuthorization, "fetch", changed)
    if change == "budget":
        assert (await retest_assessment(**arguments(fixture)))["retest_status"] == "inconclusive"
    else:
        with pytest.raises(WebAuthorizationError):
            await retest_assessment(**arguments(fixture))
    assert target["calls"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["adapter", "sequence", "version", "missing-receipt"])
async def test_rehashed_baseline_must_still_match_approved_adapter_and_receipts(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    change: str,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    with sqlite3.connect(fixture["directory"] / ".state" / "evidence.db") as db:
        result = json.loads(db.execute("SELECT content FROM case_results").fetchone()[0])
        if change == "adapter":
            result["adapter"] = "http.single-credit"
        elif change == "sequence":
            result["evidence"][0], result["evidence"][2] = (
                result["evidence"][2],
                result["evidence"][0],
            )
        elif change == "version":
            result["case_version"] = 99
        else:
            result["evidence"].pop()
        encoded = json.dumps(result).encode()
        db.execute(
            "UPDATE case_results SET content=?,sha256=?",
            (encoded, hashlib.sha256(encoded).hexdigest()),
        )
    with pytest.raises(EvidenceError):
        await retest_assessment(**arguments(fixture))
    assert fixture["approvals"] == []
    assert target["calls"] == 6


@pytest.mark.asyncio
async def test_interrupted_attempt_requires_recovery_and_never_replays(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    report = review_assessment(fixture["directory"])
    ledger = EvidenceLedger(
        fixture["directory"] / ".state" / "evidence.db",
        scan_id="scan",
        assessment_id="assessment",
        context_sha256=report["context_sha256"],
        owns_agent=lambda _: True,
        resuming=True,
    )
    ledger.begin(
        agent_ref="interrupted",
        case_ref="cross-tenant",
        case_version=1,
        identity_ref="a",
        operation_ref="private",
    )
    ledger.close()
    with pytest.raises(EvidenceError, match="recovery"):
        await retest_assessment(**arguments(fixture))
    assert target["calls"] == 6
    assert fixture["approvals"] == []
    assert review_assessment(fixture["directory"])["unresolved_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["authorization_path", "credentials_path"])
async def test_handoffs_inside_run_are_refused(
    tmp_path: Path,
    private_server: Any,
    monkeypatch: Any,
    kind: str,
) -> None:
    port, target = private_server
    fixture = await prepared(tmp_path, port, monkeypatch)
    fixture[kind] = fixture["directory"] / "private.json"
    with pytest.raises(EvidenceError, match="outside"):
        await retest_assessment(**arguments(fixture))
    assert target["calls"] == 6
    assert fixture["approvals"] == []
