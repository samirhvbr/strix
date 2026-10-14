"""The catalog selects by declared facts; only runtime receipts close an essential item."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from strix.core.assessment_catalog import catalog as catalog_module
from strix.core.assessment_catalog import plan as plan_module
from strix.core.assessment_catalog.catalog import CatalogError, load_catalog
from strix.core.assessment_catalog.plan import build_catalog_plan, catalog_plan_for
from strix.core.assessment_catalog.projection import project_catalog
from strix.core.assessment_context import AssessmentContext, parse_context
from strix.report.result import EXIT_INCOMPLETE, compose_evaluation_result
from tests.test_assessment_context import config


if TYPE_CHECKING:
    from collections.abc import Iterator


PACKAGE = Path(catalog_module.__file__).parent
ESSENTIAL = {"5.3.1", "5.3.2", "5.3.5", "5.12.1", "5.13.5"}


@pytest.fixture(autouse=True)
def fresh_catalog() -> Iterator[None]:
    load_catalog.cache_clear()
    yield
    load_catalog.cache_clear()


def authorization_context() -> AssessmentContext:
    _, raw = config(8080)
    raw["version"] = 2
    raw["operations"] = {
        name: {"method": "GET", "url": f"http://127.0.0.1:8080/{name}"}
        for name in ["private", "control"]
    }
    raw["cases"]["cross-tenant"].update(
        operations=["private", "control"],
        authorization={
            "adapter": "http.private-read",
            "version": 1,
            "owner_ref": "a",
            "other_ref": "b",
            "control_operation": "control",
            "resource_operation": "private",
            "resource_ref": "private-42",
        },
    )
    context = parse_context(raw)
    assert context is not None
    return context


def transport_context() -> AssessmentContext:
    _, raw = config(8080)
    raw.update(
        version=2,
        identities={"anonymous": {"tenant_ref": "lab", "role_ref": "observer", "secret_ref": None}},
        operations={
            "tls": {"method": "TLS", "url": "tls://127.0.0.1:8443"},
            "ssh": {"method": "SSH", "url": "ssh://127.0.0.1:2222"},
        },
        cases={
            "tls": {
                "version": 1,
                "identities": ["anonymous"],
                "operations": ["tls"],
                "transport": {"adapter": "openssl.tls", "version": 1},
            },
            "ssh": {
                "version": 1,
                "identities": ["anonymous"],
                "operations": ["ssh"],
                "transport": {"adapter": "ssh-audit", "version": 1},
            },
        },
    )
    context = parse_context(raw)
    assert context is not None
    return context


def ids(plan: dict[str, Any], weight: str | None = None) -> set[str]:
    return {i["id"] for i in plan["items"] if weight in (None, i["weight"])}


def test_catalog_keeps_every_check_and_caps_the_essentials() -> None:
    catalog = load_catalog()
    assert (len(catalog.items), catalog.families) == (171, 29)
    assert {item.id for item in catalog.essential} == ESSENTIAL
    assert len(catalog.essential) <= catalog.essential_limit <= 15
    assert all(item.text for item in catalog.items.values())
    # Items nobody curated stay in the catalog as consultative with no fact.
    plain = catalog.items["5.1.1"]
    assert (plain.weight, plain.label, plain.applies_when) == ("consultative", None, ())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda i: i["5.12.1"].update(applies_when=["case.nothing"]), "closed vocabulary"),
        (lambda i: i["5.12.1"].update(applies_when=["case.business"]), "adapter's fact"),
        (lambda i: i["5.12.1"].pop("closes_with"), "needs closes_with"),
        (lambda i: i["5.12.1"]["closes_with"].update(scope="mostly"), "incomplete"),
        (lambda i: i["5.12.1"].update(coverage_class="made_up"), "unknown coverage class"),
        (lambda i: i["5.12.1"].update(weight="critical"), "unknown weight"),
        (lambda i: i["5.12.2"].update(closes_with=i["5.12.1"]["closes_with"]), "cannot close"),
        (lambda i: i["5.12.1"].pop("label"), "English label"),
        (lambda i: i.update({"9.9.9": dict(i["5.12.2"])}), "not a catalog item"),
    ],
)
def test_malformed_selection_fails_the_load(
    monkeypatch: pytest.MonkeyPatch, change: Any, message: str
) -> None:
    real = catalog_module._read

    def broken(name: str) -> dict[str, Any]:
        data = real(name)
        if name == "selection.yaml":
            change(data["items"])
        return data

    monkeypatch.setattr(catalog_module, "_read", broken)
    with pytest.raises(CatalogError, match=message):
        load_catalog()


def test_more_essentials_than_the_limit_fails_the_load(monkeypatch: pytest.MonkeyPatch) -> None:
    real = catalog_module._read

    def tight(name: str) -> dict[str, Any]:
        data = real(name)
        if name == "selection.yaml":
            data["essential_limit"] = 4
        return data

    monkeypatch.setattr(catalog_module, "_read", tight)
    with pytest.raises(CatalogError, match="more than 4 essential"):
        load_catalog()


def test_plan_holds_exactly_the_items_the_declared_facts_make_applicable() -> None:
    authorization = build_catalog_plan(authorization_context())
    assert ids(authorization, "essential") == {"5.12.1"}
    assert ids(authorization, "consultative") == {
        "5.12.2",
        "5.12.6",
        "5.17.4",
        "5.17.5",
        "5.17.6",
        "5.20.1",
    }
    transport = build_catalog_plan(transport_context())
    assert ids(transport, "essential") == {"5.3.1", "5.3.2", "5.3.5"}
    assert ids(transport, "consultative") == set()  # TLS and SSH operations are not HTTP


def test_curated_items_without_their_facts_are_listed_never_dropped() -> None:
    plan = build_catalog_plan(authorization_context())
    unmet = {row["id"]: row["missing_facts"] for row in plan["unmet"]}
    assert unmet["5.13.5"] == ["case.business"]
    assert unmet["5.3.1"] == ["transport.tls"]
    assert unmet["5.3.5"] == ["transport.ssh"]
    assert len(plan["items"]) + len(plan["unmet"]) + plan["not_selected"] == 171
    assert plan["catalog_items"] == 171


def test_essential_rows_name_their_adapter_scope_and_cases() -> None:
    row = next(
        i for i in build_catalog_plan(authorization_context())["items"] if i["id"] == "5.12.1"
    )
    assert (row["adapter"], row["scope"], row["case_refs"]) == (
        "http.private-read",
        "partial",
        ["cross-tenant"],
    )
    assert "read access only" in row["limits"]


def record(plan: dict[str, Any], rows: list[dict[str, str]], verdicts: list[str]) -> dict[str, Any]:
    return {
        "run_id": "r1",
        "run_name": "run-1",
        "status": "completed",
        "assessment_catalog": plan,
        "evidence_ledger": {
            "obligations": {"items": [{"case_ref": "cross-tenant", **row} for row in rows]},
            "case_evaluations": [{"case_ref": "cross-tenant", "verdict": v} for v in verdicts],
        },
    }


def status(run_record: dict[str, Any], item: str = "5.12.1") -> tuple[str, str | None]:
    projection = project_catalog(run_record)
    assert projection is not None
    row = next(i for i in projection["items"] if i["id"] == item)
    return row["status"], row.get("reason")


@pytest.mark.parametrize(
    ("rows", "verdicts", "expected"),
    [
        (
            [{"status": "observed"}, {"status": "reconciled"}],
            ["compliant"],
            ("partially_covered", None),
        ),
        ([{"status": "observed"}], ["vulnerable"], ("partially_covered", None)),
        ([{"status": "observed"}, {"status": "missing"}], ["compliant"], ("open", "missing")),
        ([{"status": "observed"}], ["inconclusive"], ("open", "inconclusive")),
        ([{"status": "observed"}], ["missing"], ("open", "missing")),
        ([{"status": "truncated"}], ["compliant"], ("open", "truncated")),
        ([], ["compliant"], ("open", "receipts_unavailable")),
        ([{"status": "observed"}], [], ("open", "receipts_unavailable")),
    ],
)
def test_an_essential_item_closes_only_with_runtime_receipts(
    rows: list[dict[str, str]], verdicts: list[str], expected: tuple[str, str | None]
) -> None:
    plan = build_catalog_plan(authorization_context())
    assert status(record(plan, rows, verdicts)) == expected


def test_a_full_scope_item_reads_covered_and_a_partial_one_does_not() -> None:
    plan = build_catalog_plan(authorization_context())
    plan["items"][0]["scope"] = "full"
    closed = record(plan, [{"status": "observed"}], ["compliant"])
    assert status(closed) == ("covered", None)
    plan["items"][0]["scope"] = "partial"
    assert status(closed) == ("partially_covered", None)


def test_no_ledger_means_open_not_covered() -> None:
    run_record = {"assessment_catalog": build_catalog_plan(authorization_context())}
    assert status(run_record) == ("open", "receipts_unavailable")


def test_totals_count_every_state() -> None:
    plan = build_catalog_plan(authorization_context())
    projection = project_catalog(record(plan, [{"status": "observed"}], ["compliant"]))
    assert projection is not None
    assert projection["informational"] is True
    assert projection["totals"] == {
        "catalog": 171,
        "selected": 7,
        "essential": 1,
        "covered": 0,
        "partially_covered": 1,
        "open": 0,
        "consultative": 6,
        "unmet_facts": 5,
        "not_selected": 159,
    }


def test_agent_attestation_cannot_close_an_item_or_change_the_exit_code() -> None:
    """ENG-06: the coverage ledger is the agent's words; only runtime receipts close an item."""
    plan = build_catalog_plan(authorization_context())
    row = {"status": "missing", "case_version": 1, "identity_ref": "a", "operation_ref": "private"}
    run_record = record(plan, [row], ["missing"])
    run_record["evidence_ledger"]["obligations"] |= {
        "essential_total": 1,
        "essential_unfulfilled": 1,
    }
    run_record["evidence_ledger"]["obligations"]["version"] = 1
    run_record["assessment_context"] = {"context_sha256": plan["context_sha256"]}
    claim = {
        "agent_id": "agent",
        "outcome": "no_issue_found",
        "surface": "5.12.1 cross-tenant read",
    }
    document = importlib.import_module("tests.test_report_coverage")._document(
        run_record=run_record, entries=[claim]
    )
    assert next(i for i in document["catalog"]["items"] if i["id"] == "5.12.1")["status"] == "open"
    outcome = compose_evaluation_result(
        coverage_document=document, findings_count=0, findings_policy_failed=False
    )
    # The version-1 gap, which only a receipt removes, is what keeps the run incomplete.
    assert any(gap["kind"] == "essential_assessment_operation" for gap in document["gaps"])
    assert outcome.exit_code == EXIT_INCOMPLETE


def test_a_run_without_a_catalog_plan_gets_no_catalog_key() -> None:
    document = importlib.import_module("tests.test_report_coverage")._document()
    assert "catalog" not in document


def test_an_unavailable_catalog_is_reported_and_does_not_touch_completeness() -> None:
    run_record = {
        "run_id": "r1",
        "status": "completed",
        "assessment_catalog": {"version": 2, "status": "unavailable", "reason": "CatalogError"},
    }
    document = importlib.import_module("tests.test_report_coverage")._document(
        run_record=run_record
    )
    assert document["catalog"]["status"] == "unavailable"
    assert document["completeness"]["complete"] is True


def test_a_broken_catalog_never_stops_a_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise CatalogError("selection.yaml: broken")

    monkeypatch.setattr(plan_module, "load_catalog", broken)
    assert catalog_plan_for(authorization_context()) == {
        "version": 2,
        "status": "unavailable",
        "reason": "CatalogError",
    }


def test_an_old_run_stays_readable_after_the_catalog_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = authorization_context()
    plan = build_catalog_plan(context)
    before = project_catalog(record(plan, [{"status": "observed"}], ["compliant"]))
    real = catalog_module._read

    def changed(name: str) -> dict[str, Any]:
        data = real(name)
        if name == "selection.yaml":
            data["items"].pop("5.12.1")
            data["selection_version"] = 2
        return data

    monkeypatch.setattr(catalog_module, "_read", changed)
    load_catalog.cache_clear()
    assert load_catalog().sha256 != plan["catalog_sha256"]
    assert ids(build_catalog_plan(context), "essential") == set()
    assert project_catalog(record(plan, [{"status": "observed"}], ["compliant"])) == before


def test_the_catalog_never_reaches_for_agent_creation() -> None:
    """Decision D6 tripwire: the catalog plans obligations; it does not create or order agents."""
    forbidden_calls = {"create_agent", "build_strix_agent", "spawn_child_agent"}
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name | ast.Attribute):
                assert getattr(node, "id", getattr(node, "attr", "")) not in forbidden_calls, path
            if isinstance(node, ast.alias):
                assert node.name not in forbidden_calls, path
            if isinstance(node, ast.ImportFrom | ast.Import):
                modules = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                assert not any(m.startswith(("strix.agents", "strix.tools")) for m in modules), path
