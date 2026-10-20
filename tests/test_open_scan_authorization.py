"""A WEB authorization with no context: network scope only, the full agent, no narrowing.

Until this was added, every ``--web-authorization`` run had to be a controlled (version-2,
identity-bound) assessment: :meth:`WebAuthorization.fetch` rejected any authorization without
a context, and the runner's own gate required an identity context whenever the policy was
version 2. There was no way to authorize "just this network destination, run the ordinary
scanning agent" through the WEB/Desktop integration — only through a hand-built
``--assessment-policy`` file passed directly on the command line, which the integration never
produces. See ``docs/PENTEST/assessment-policy.md`` for that pre-existing, context-less path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from strix.config import loader
from strix.core.web_authorization import WebAuthorizationError
from strix.interface.cli_args import parse_arguments
from tests.test_assessment_context import config
from tests.test_controlled_assessment import web_gate


if TYPE_CHECKING:
    from pathlib import Path


def open_approval(port: int = 1234) -> dict[str, Any]:
    policy, _context = config(port)
    return {
        "policy": policy.model_dump(),
        "context": None,
        "max_budget_usd": 5,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }


def test_fetch_accepts_a_version_one_policy_with_no_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = web_gate(tmp_path, monkeypatch, {"body": open_approval()})
    policy, context, budget = gate.fetch()
    assert policy.version == 1
    assert context is None
    assert budget == 5
    gate.check_scope_only("scan", policy, budget)


def test_fetch_rejects_a_version_one_policy_that_carries_a_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, context = config(1234)
    body = {
        "policy": policy.model_dump(),
        "context": context,
        "max_budget_usd": 5,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }
    gate = web_gate(tmp_path, monkeypatch, {"body": body})
    with pytest.raises(WebAuthorizationError):
        gate.fetch()


def test_fetch_rejects_a_version_two_policy_with_no_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = open_approval() | {"policy": (config(1234)[0].model_dump() | {"version": 2})}
    gate = web_gate(tmp_path, monkeypatch, {"body": body})
    with pytest.raises(WebAuthorizationError):
        gate.fetch()


def test_check_scope_only_rejects_a_context_that_appears_on_refetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {"body": open_approval()}
    gate = web_gate(tmp_path, monkeypatch, state)
    policy, _context, budget = gate.fetch()
    _policy, context = config(1234)
    state["body"] = state["body"] | {"context": context}
    with pytest.raises(WebAuthorizationError):
        gate.check_scope_only("scan", policy, budget)


def test_check_scope_only_rejects_a_revoked_or_defunded_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {"body": open_approval()}
    gate = web_gate(tmp_path, monkeypatch, state)
    policy, _context, budget = gate.fetch()
    state["status"] = 403
    with pytest.raises(WebAuthorizationError):
        gate.check_scope_only("scan", policy, budget)
    state["status"] = 200
    with pytest.raises(WebAuthorizationError):
        gate.check_scope_only("scan", policy, budget + 1)


def test_cli_resolves_targets_from_the_policy_with_no_assessment_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STRIX_LLM", "openai/gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setattr(loader, "_cached", None)
    raw = json.loads(json.dumps(open_approval()).replace("127.0.0.1", "192.0.2.10"))
    web_gate(tmp_path, monkeypatch, {"body": raw})
    monkeypatch.setattr(
        "sys.argv", ["strix", "--web-authorization", str(tmp_path / "authorization.json")]
    )
    args = parse_arguments()
    assert args.non_interactive is True
    assert args.assessment_policy["version"] == 1
    assert args.assessment_context is None
    assert args.target == ["192.0.2.10"]
