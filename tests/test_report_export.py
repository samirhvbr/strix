"""Report sharing is bounded, scope-bound and distinct from metadata export."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strix.core.history_export import stopped_assessment
from strix.core.report_export import export_report
from strix.core.run_lease import controller_lease
from strix.interface.report_export import run_report_export
from tests.test_assessment_review import fixture_run, snapshot_files


pytestmark = pytest.mark.skipif(os.name != "posix", reason="private POSIX report")


def write(path: Path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@pytest.mark.asyncio
async def test_report_uses_actual_finish_and_coverage_shape_without_private_runtime(
    tmp_path, capsys
):
    run, executor = fixture_run(tmp_path)
    with controller_lease(run / ".state"):
        pass
    write(
        run / "run.json",
        {
            "run_id": run.name,
            "instruction": "PRIVATE TASK",
            "scan_results": {
                "executive_summary": "Two findings require follow-up.",
                "methodology": "Controlled fixture.",
                "technical_analysis": "Do not infer a clean target from missing coverage.",
                "recommendations": "Retest after remediation.",
            },
        },
    )
    write(
        run / "vulnerabilities.json",
        [
            {
                "id": "finding-1",
                "severity": "high",
                "title": "Tenant boundary",
                "description": "Owner isolation failed.",
                "evidence": "Authorization: Bearer synthetic-private\nGET /account",
                "poc_script_code": "assert response.status_code == 403",
                "cwe": "CWE-862",
                "endpoint": "/account",
                "code_locations": [
                    {"file": "src/handler.py", "start_line": 2, "fix_after": "require_owner()"}
                ],
                "dependency_metadata": {"package_name": "example", "installed_version": "1.0.0"},
            }
        ],
    )
    write(
        run / "coverage.json",
        {
            "schema_version": 1,
            "run_id": run.name,
            "entries": [
                {
                    "surface": "/account",
                    "risk_area": "idor",
                    "outcome": "reported",
                    "source": "agent_reported",
                }
            ],
            "gaps": [{"source": "runtime", "detail": "Required case incomplete"}],
            "completeness": {"complete": False},
            "machine_observed": {
                "source": "runtime",
                "agents": [{"task": "PRIVATE TASK"}],
                "assessment_obligations": {"essential_unfulfilled": 1},
            },
        },
    )
    before = snapshot_files(run)
    data = export_report(run)
    assert snapshot_files(run) == before
    assert data["coverage"]["completeness"]["complete"] is False
    assert data["findings"][0]["cwe"] == "CWE-862"
    assert data["findings"][0]["code_locations"][0]["file"] == "src/handler.py"
    assert "synthetic-private" not in json.dumps(data)
    assert "PRIVATE TASK" not in json.dumps(data)
    assert "[REDACTED_SECRET]" in json.dumps(data)
    assert run_report_export([str(run)]) == 0
    assert json.loads(capsys.readouterr().out) == data
    fixture = os.environ.get("SHVIA_REPORT_FIXTURE")
    if fixture:
        Path(fixture).write_text(json.dumps(data, indent=2) + "\n")
    with stopped_assessment(run):
        assert run_report_export([str(run)]) == 1
    assert capsys.readouterr().err == '{"error":"assessment_report_unavailable"}\n'
    await executor.close()


@pytest.mark.asyncio
async def test_report_refuses_cross_scan_linked_or_oversized_artifacts(tmp_path):
    run, executor = fixture_run(tmp_path)
    with controller_lease(run / ".state"):
        pass
    write(run / "run.json", {"run_id": "other"})
    write(run / "vulnerabilities.json", [])
    with pytest.raises(ValueError, match="another run"):
        export_report(run)
    write(run / "run.json", {"run_id": run.name})
    write(run / "vulnerabilities.json", [{"title": "x" * 100_001}])
    with pytest.raises(ValueError, match="limit"):
        export_report(run)
    (run / "vulnerabilities.json").unlink()
    assert export_report(run)["findings"] is None, "absent evidence cannot claim zero findings"
    write(run / "vulnerabilities.json", [])
    source = run / "vulnerabilities.json"
    source.rename(run / "saved.json")
    source.symlink_to(run / "saved.json")
    with pytest.raises(OSError):
        export_report(run)
    await executor.close()


def _catalog_projection() -> dict:
    return {
        "version": 2,
        "informational": True,
        "status": "ready",
        "catalog_version": 1,
        "selection_version": 1,
        "catalog_sha256": "a" * 64,
        "context_sha256": "b" * 64,
        "totals": {
            "catalog": 171,
            "selected": 2,
            "essential": 1,
            "covered": 0,
            "partially_covered": 1,
            "open": 0,
            "consultative": 1,
            "unmet_facts": 5,
            "not_selected": 164,
        },
        "items": [
            {
                "id": "5.12.1",
                "weight": "essential",
                "label": "Cross-tenant private object read",
                "text": "Testar acesso a objetos de outro usuário ou tenant.",
                "coverage_class": "idor",
                "status": "partially_covered",
                "scope": "partial",
                "limits": "Exercises read access only.",
                "limits_pt": "Exercita apenas o acesso de leitura.",
                "case_refs": ["PRIVATE-CASE-REF"],
            },
            {
                "id": "5.17.4",
                "weight": "consultative",
                "label": "Controllable redirects",
                "text": "Verificar redirecionamentos controláveis.",
                "coverage_class": "open_redirect",
                "status": "consultative",
            },
        ],
        "unmet": [{"id": "5.3.1", "missing_facts": ["transport.tls"]}],
    }


def _stage(tmp_path: Path, catalog: object) -> Path:
    run, _ = fixture_run(tmp_path)
    with controller_lease(run / ".state"):
        pass
    write(run / "run.json", {"run_id": run.name, "scan_results": {}})
    write(run / "vulnerabilities.json", [])
    coverage = {
        "schema_version": 1,
        "run_id": run.name,
        "entries": [],
        "gaps": [],
        "completeness": {"complete": True},
        "machine_observed": {"source": "runtime"},
    }
    if catalog is not None:
        coverage["catalog"] = catalog
    write(run / "coverage.json", coverage)
    return run


@pytest.mark.asyncio
async def test_the_catalog_projection_travels_narrowed_and_informational(tmp_path):
    run = _stage(tmp_path, _catalog_projection())

    exported = export_report(run)

    catalog = exported["catalog"]
    assert catalog["informational"] is True
    assert catalog["status"] == "ready"
    assert [item["id"] for item in catalog["items"]] == ["5.12.1", "5.17.4"]
    assert catalog["items"][0]["status"] == "partially_covered"
    assert catalog["items"][0]["limits_pt"] == "Exercita apenas o acesso de leitura."
    assert catalog["totals"]["not_selected"] == 164
    serialized = json.dumps(catalog)
    for private in (
        "PRIVATE-CASE-REF",
        "catalog_sha256",
        "context_sha256",
        '"unmet":',
        "missing_facts",
    ):
        assert private not in serialized, private
    assert "catalog" not in exported["coverage"], "the coverage block stays as it was"


@pytest.mark.asyncio
async def test_a_run_without_a_catalog_exports_the_same_package_as_before(tmp_path):
    run = _stage(tmp_path, None)

    assert "catalog" not in export_report(run)


@pytest.mark.asyncio
async def test_an_unavailable_catalog_travels_as_its_reason_only(tmp_path):
    run = _stage(
        tmp_path, {"version": 2, "informational": True, "status": "unavailable", "reason": "broken"}
    )

    assert export_report(run)["catalog"] == {
        "version": 2,
        "status": "unavailable",
        "reason": "broken",
    }


def _mutations():
    def drop(key):
        return lambda c: c.pop(key)

    def row(change):
        return lambda c: change(c["items"][0])

    return [
        ("not an object", lambda _: "text"),
        ("no totals", drop("totals")),
        ("a total is a string", lambda c: c["totals"].update(open="3")),
        ("a total is a bool", lambda c: c["totals"].update(open=True)),
        ("a total is missing", lambda c: c["totals"].pop("selected")),
        ("no items", drop("items")),
        ("items not a list", lambda c: c.update(items={"id": "5.12.1"})),
        ("too many items", lambda c: c.update(items=[dict(c["items"][1]) for _ in range(65)])),
        ("a row is not an object", lambda c: c["items"].append("x")),
        ("a bad item id", row(lambda r: r.update(id="../../etc"))),
        ("an unknown weight", row(lambda r: r.update(weight="critical"))),
        ("an unknown status", row(lambda r: r.update(status="done"))),
        ("an unknown scope", row(lambda r: r.update(scope="most"))),
        ("no label", row(lambda r: r.pop("label"))),
        ("an empty label", row(lambda r: r.update(label=""))),
        ("a label that is not text", row(lambda r: r.update(label=5))),
        ("an oversized text", row(lambda r: r.update(text="x" * 601))),
        ("the version is missing", drop("version")),
        ("an unknown state", lambda c: c.update(status="maybe")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mutate"), _mutations(), ids=[m[0] for m in _mutations()])
async def test_a_malformed_catalog_is_left_out_and_never_blocks_the_report(tmp_path, name, mutate):
    projection = _catalog_projection()
    replaced = mutate(projection)
    run = _stage(tmp_path, replaced if isinstance(replaced, str) else projection)

    exported = export_report(run)

    assert "catalog" not in exported, name
    assert exported["version"] == 1, "the rest of the package is untouched"


@pytest.mark.asyncio
async def test_secrets_inside_catalog_text_are_redacted_like_the_rest(tmp_path):
    projection = _catalog_projection()
    projection["items"][0]["limits"] = "Authorization: Bearer synthetic-private-catalog"
    run = _stage(tmp_path, projection)

    serialized = json.dumps(export_report(run))

    assert "synthetic-private-catalog" not in serialized
    assert "[REDACTED_SECRET]" in serialized
