"""Tests for the standalone report-pdf-v1 renderer (strix/report/package_pdf.py)."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from strix.report import package_pdf
from strix.report.package_pdf import DocumentError, main, render_package_pdf


MODULE = Path(package_pdf.__file__)

EN = {
    "title": "Security Assessment",
    "confidential": "CONFIDENTIAL",
    "executive_summary": "Executive Summary",
    "methodology": "Methodology and Scope",
    "technical_analysis": "Technical Analysis",
    "coverage": "Coverage and Follow-up",
    "recommendations": "Recommendations",
    "findings": "Detailed Findings",
    "appendix": "AI Execution and Accounting",
    "authorization": "Authorization",
    "unknown": "Not recorded",
    "incomplete": "Incomplete evidence. Missing coverage does not establish a clean target.",
    "ai": "This assessment was executed by AI agents.",
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Informational",
    "reported": "Finding reported",
    "no_issue_found": "No issue identified",
    "ruled_out": "Ruled out",
    "not_applicable": "Not applicable",
    "needs_follow_up": "Requires further review",
    "scan_id": "Run",
    "scan_mode": "Mode",
    "status": "Status",
    "started_at": "Started",
    "ended_at": "Ended",
    "duration_s": "Duration (s)",
    "strix_version": "Strix version",
    "operator": "Operator",
    "authorization_ref": "Authorization reference",
    "empty": "No findings were recorded in the supplied artifact.",
    "omitted": "Unsupported source fields were omitted; review the original evidence.",
    "page": "Page",
    "provenance": "Provenance",
    "gaps": "Coverage gaps",
    "models": "Model accounting",
    "incidents": "Incidents",
    "source_hash": "Source revision digest",
    "translation_notice": "English is the source of truth.",
    "description": "Description",
    "evidence": "Evidence",
    "poc_script_code": "Proof of Concept Code",
    "catalog": "Assessment Catalog",
    "catalog_intro": "Informational. Shows which checks the runtime evidence closed.",
    "catalog_unavailable": "The catalog plan was not available for this run.",
    "catalog_covered": "Covered",
    "catalog_partially_covered": "Partially covered",
    "catalog_open": "Open",
    "catalog_consultative": "Consultative",
    "catalog_limits": "What is left",
    "catalog_reason": "Reason",
    "catalog_catalog": "Checks in the catalog",
    "catalog_selected": "Selected for this run",
    "catalog_essential": "Essential",
    "catalog_unmet_facts": "Curated checks that do not apply to this target",
    "catalog_not_selected": "Not selected",
}
PT = {
    **EN,
    "title": "Avaliação de Segurança",
    "confidential": "CONFIDENCIAL",
    "executive_summary": "Sumário Executivo",
    "methodology": "Metodologia e Escopo",
    "technical_analysis": "Análise Técnica",
    "coverage": "Cobertura e Acompanhamento",
    "recommendations": "Recomendações",
    "findings": "Achados Detalhados",
    "appendix": "Execução por IA e Consumo",
    "unknown": "Não registrado",
    "critical": "Crítica",
    "high": "Alta",
    "page": "Página",
    "description": "Descrição",
    "evidence": "Evidência",
    "translation_notice": "O inglês é a fonte de verdade.",
    "catalog": "Catálogo de Verificações",
    "catalog_partially_covered": "Parcialmente coberta",
    "catalog_limits": "O que falta",
}
FINDING = {
    "id": "F-1",
    "title": "SQL injection in the login form",
    "severity": "critical",
    "description": "User input reaches the query.\n\n- first step\n- second step",
    "evidence": {"request": "POST /login", "status": 500},
    "poc_script_code": 'curl -d "user=\' or 1=1" https://example.test/login',
}


CATALOG = {
    "status": "ready",
    "totals": {
        "catalog": 171,
        "selected": 3,
        "essential": 2,
        "covered": 1,
        "partially_covered": 1,
        "open": 0,
        "consultative": 1,
        "unmet_facts": 5,
        "not_selected": 163,
    },
    "groups": [
        {
            "status": "covered",
            "items": [
                {
                    "id": "5.13.5",
                    "title": "Single-use credit under concurrency",
                    "scope": "full",
                    "limits": "Covers the one resource.",
                    "reason": None,
                }
            ],
        },
        {
            "status": "partially_covered",
            "items": [
                {
                    "id": "5.12.1",
                    "title": "Cross-tenant private object read",
                    "scope": "partial",
                    "limits": "Exercises read access only.",
                    "reason": None,
                }
            ],
        },
        {
            "status": "consultative",
            "items": [
                {
                    "id": "5.17.4",
                    "title": "Controllable redirects",
                    "scope": None,
                    "limits": None,
                    "reason": None,
                }
            ],
        },
    ],
}


def _document(language: str = "en", **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": 1,
        "language": language,
        "labels": copy.deepcopy(PT if language == "pt-BR" else EN),
        "source_sha256": "a" * 64,
        "incomplete": False,
        "omitted_fields": False,
        "counts": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
        "metadata": {
            "target": "https://example.test",
            "scan_id": "run-1",
            "scan_mode": "deep",
            "status": "completed",
            "started_at": "2026-10-14T10:00:00Z",
            "ended_at": "2026-10-14T11:00:00Z",
            "duration_s": 3600,
            "strix_version": "1.2.35",
            "operator": "operator@example.test",
            "authorization_ref": "AUTH-1",
            "scope": {"hosts": ["example.test"]},
            "accounting": {
                "provenance": {"source": "native"},
                "models": [{"model": "m1", "calls": 3}],
                "incidents": [],
            },
        },
        "scan_results": {
            "executive_summary": "The target has **one** critical issue.",
            "methodology": "Authenticated black-box testing.",
            "technical_analysis": "Details in the findings.",
            "recommendations": "1. Use parameterized queries.",
        },
        "coverage": {
            "entries": [{"outcome": "reported", "surface": "login"}],
            "gaps": [],
            "completeness": {"complete": True},
            "machine_observed": {"requests": 12},
        },
        "findings": [copy.deepcopy(FINDING)],
    }
    document.update(overrides)
    return document


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def test_english_document_has_every_section_and_the_finding() -> None:
    pdf = render_package_pdf(_document())

    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    for number, key in enumerate(
        (
            "executive_summary",
            "methodology",
            "technical_analysis",
            "coverage",
            "recommendations",
            "findings",
            "appendix",
        ),
        start=1,
    ):
        assert f"{number:02d} / {EN[key]}" in text
    assert "SQL injection in the login form" in text
    assert "ID: F-1" in text
    assert "CRITICAL" in text
    assert "first step" in text
    assert "POST /login" in text
    assert "https://example.test" in text
    assert "Page 2 /" in text
    assert "Page 1 /" not in text, "the cover carries no page number"


def test_a_short_report_is_not_padded_with_blank_pages() -> None:
    pages = PdfReader(BytesIO(render_package_pdf(_document()))).pages

    assert len(pages) <= 6


def test_no_page_ends_on_a_heading_whatever_the_page_boundary() -> None:
    headings = {
        EN[key]
        for key in (
            "executive_summary",
            "methodology",
            "technical_analysis",
            "coverage",
            "recommendations",
            "findings",
            "appendix",
            "provenance",
            "gaps",
            "models",
            "incidents",
            "authorization",
            "reported",
            "needs_follow_up",
            "description",
            "evidence",
        )
    }
    for paragraphs in range(0, 60, 3):
        document = _document()
        document["scan_results"]["technical_analysis"] = "\n\n".join(
            f"paragraph {i} " * 30 for i in range(paragraphs)
        )
        for page in PdfReader(BytesIO(render_package_pdf(document))).pages:
            last = [line.strip() for line in page.extract_text().splitlines() if line.strip()]
            footer_free = [line for line in last if not line.startswith("Page ")]
            tail = footer_free[-1] if footer_free else ""
            assert tail.upper() not in {h.upper() for h in headings}, (paragraphs, tail)
            assert not tail.startswith(
                ("01 / ", "02 / ", "03 / ", "04 / ", "05 / ", "06 / ", "07 / ")
            ), (
                paragraphs,
                tail,
            )


def test_portuguese_document_uses_its_glossary_and_keeps_accents() -> None:
    text = _text(render_package_pdf(_document("pt-BR")))

    assert "Avaliação de Segurança" in text
    assert "01 / Sumário Executivo" in text
    assert "Análise Técnica" in text
    assert "Página 2 /" in text
    assert "O inglês é a fonte de verdade." in text


def test_english_document_has_no_translation_notice() -> None:
    assert "English is the source of truth." not in _text(render_package_pdf(_document()))


def test_output_is_deterministic() -> None:
    assert render_package_pdf(_document()) == render_package_pdf(_document())


def test_missing_findings_and_coverage_are_stated_not_hidden() -> None:
    document = _document(findings=None, coverage=None, counts=None, incomplete=True)

    text = _text(render_package_pdf(document))

    assert EN["incomplete"] in text
    assert text.count("Not recorded") >= 2


def test_empty_findings_and_omitted_fields_are_said_out_loud() -> None:
    text = _text(render_package_pdf(_document(findings=[], omitted_fields=True)))

    assert EN["empty"] in text
    assert EN["omitted"] in text


def test_coverage_entries_are_grouped_by_outcome() -> None:
    document = _document()
    document["coverage"]["entries"] = [
        {"outcome": "ruled_out", "surface": "ssh-banner"},
        {"outcome": "needs_follow_up", "surface": "graphql"},
    ]

    text = _text(render_package_pdf(document))

    assert (
        text.index("Ruled out") < text.index("ssh-banner") < text.index("Requires further review")
    )
    assert text.index("Requires further review") < text.index("graphql")


def test_narrative_fields_are_markdown_and_other_fields_are_literal() -> None:
    document = _document()
    document["findings"][0]["description"] = "Has **strong** and `code` text."
    document["findings"][0]["evidence"] = "**not bold** here"

    text = _text(render_package_pdf(document))

    assert "Has strong and code text." in text
    assert "**not bold** here" in text


def test_long_unbroken_token_is_wrapped_inside_the_page() -> None:
    document = _document()
    document["findings"][0]["evidence"] = "A" * 5000

    text = _text(render_package_pdf(document))

    longest = max(len(line) for line in text.splitlines())
    assert longest <= 120


HOSTILE = [
    '<img src="file:///etc/passwd"/>',
    '<a href="http://evil.example/x">click</a>',
    '<font color="red" size="400">big</font>',
    "<para><b>unclosed",
    "</b></i></font></para>",
    "&nonexistent; &#0; &#x110000; &amp;amp;",
    "[link](http://evil.example) ![img](http://evil.example/p.png) <http://evil.example>",
    "<![CDATA[x]]> <!-- c --> <?php echo 1; ?>",
    "<script>alert(1)</script>",
    "NUL\x00 BEL\x07 DEL\x7f C1\x85 lone-surrogate\ud800",
    "日本語 😀 ключ → ≥ ✓",
    "```\n<img src='x'>\n```",
    "\n".join(f"- item {i}" for i in range(50)),
]
FORBIDDEN_IN_PDF = (b"/URI", b"/Launch", b"/JavaScript", b"/JS", b"/EmbeddedFile", b"/OpenAction")


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_text_neither_breaks_the_render_nor_adds_actions(payload: str) -> None:
    document = _document()
    document["metadata"]["target"] = payload
    document["metadata"]["operator"] = payload
    document["metadata"]["scope"] = {"free": payload}
    document["scan_results"] = dict.fromkeys(document["scan_results"], payload)
    document["coverage"]["entries"] = [{"outcome": "reported", "surface": payload}]
    document["findings"][0].update(title=payload, description=payload, evidence=payload)
    document["labels"]["description"] = payload

    pdf = render_package_pdf(document)

    assert pdf.startswith(b"%PDF-")
    for forbidden in FORBIDDEN_IN_PDF:
        assert forbidden not in pdf


def test_markup_in_text_is_printed_not_interpreted() -> None:
    document = _document()
    document["findings"][0]["title"] = '<img src="file:///etc/passwd"/> & <b>bold</b>'
    document["findings"][0]["description"] = "<font color='red'>x</font> [l](http://evil.example)"

    text = _text(render_package_pdf(document))

    assert '<img src="file:///etc/passwd"/> & <b>bold</b>' in text
    assert "<font color='red'>x</font>" in text
    assert "[l](http://evil.example)" in text


def test_many_large_findings_still_render() -> None:
    document = _document()
    document["findings"] = [
        {**copy.deepcopy(FINDING), "id": f"F-{i}", "description": "paragraph text " * 200}
        for i in range(128)
    ]

    reader = PdfReader(BytesIO(render_package_pdf(document)))

    assert len(reader.pages) > 50


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"version": "1"},
        {"language": "fr"},
        {"labels": ["not", "an", "object"]},
        {"labels": {"title": 5}},
        {"metadata": None},
        {"scan_results": "text"},
        {"findings": {"id": "x"}},
        {"findings": ["not an object"]},
        {"coverage": "text"},
        {"counts": []},
        {"narrative_fields": "description"},
        {"narrative_fields": [1]},
    ],
)
def test_contract_violations_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(DocumentError):
        render_package_pdf(_document(**change))


def test_coverage_entries_must_be_a_list() -> None:
    document = _document()
    document["coverage"]["entries"] = {"outcome": "reported"}

    with pytest.raises(DocumentError):
        render_package_pdf(document)


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_cli_writes_a_private_pdf_and_leaves_nothing_else(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.json", _document())
    target = tmp_path / "out.pdf"

    assert main(["--input", str(source), "--output", str(target)]) == 0

    assert target.read_bytes().startswith(b"%PDF-")
    assert target.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["in.json", "out.pdf"]


def test_cli_exit_codes_for_invalid_input_leave_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "out.pdf"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    wrong = _write(tmp_path / "wrong.json", _document(version=9))
    listing = _write(tmp_path / "list.json", [1, 2])

    for source in (broken, wrong, listing, tmp_path / "missing.json"):
        assert main(["--input", str(source), "--output", str(target)]) == 2

    assert not target.exists()
    assert "invalid input" in capsys.readouterr().err


def test_cli_refuses_an_oversized_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _write(tmp_path / "in.json", _document())
    monkeypatch.setattr(package_pdf, "MAX_INPUT_BYTES", 10)

    assert main(["--input", str(source), "--output", str(tmp_path / "out.pdf")]) == 2


def test_cli_reports_a_render_failure_with_its_own_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path / "in.json", _document())

    def explode(_: Any) -> bytes:
        raise RuntimeError("renderer fault")

    monkeypatch.setattr(package_pdf, "render_package_pdf", explode)

    assert main(["--input", str(source), "--output", str(tmp_path / "out.pdf")]) == 3


def test_cli_runs_as_a_plain_script_in_isolated_mode(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.json", _document("pt-BR"))
    target = tmp_path / "out.pdf"

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, our own file
        [sys.executable, "-I", str(MODULE), "--input", str(source), "--output", str(target)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Sumário Executivo" in _text(target.read_bytes())


def test_the_renderer_stays_a_leaf_that_never_imports_strix() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: the file must run on its own"
            imported.add((node.module or "").split(".")[0])

    allowed = set(sys.stdlib_module_names) | {"reportlab", "markdown_it"}
    assert imported <= allowed, sorted(imported - allowed)


def test_a_catalog_adds_a_section_and_renumbers_the_ones_after_it() -> None:
    text = _text(render_package_pdf(_document(catalog=CATALOG)))

    assert "04 / Coverage and Follow-up" in text
    assert "05 / Assessment Catalog" in text
    assert "06 / Recommendations" in text
    assert "07 / Detailed Findings" in text
    assert "08 / AI Execution and Accounting" in text
    assert "05 / Recommendations" not in text


def test_without_a_catalog_the_numbering_is_unchanged() -> None:
    for document in (_document(), _document(catalog=None)):
        text = _text(render_package_pdf(document))
        assert "05 / Recommendations" in text
        assert "Assessment Catalog" not in text


def test_the_catalog_shows_groups_totals_and_what_is_left_for_a_partial_item() -> None:
    text = _text(render_package_pdf(_document(catalog=CATALOG)))

    for expected in (
        "Informational. Shows which checks the runtime evidence closed.",
        "Checks in the catalog",
        "171",
        "Curated checks that do not apply to this target",
        "Covered",
        "Partially covered",
        "Consultative",
        "5.12.1",
        "Cross-tenant private object read",
        "What is left: Exercises read access only.",
        "5.17.4",
    ):
        assert expected in text, expected
    assert "What is left: Covers the one resource." not in text, "a full item has nothing left"


def test_the_catalog_prints_in_the_language_the_document_carries() -> None:
    catalog = copy.deepcopy(CATALOG)
    catalog["groups"][1]["items"][0].update(
        title="Testar acesso a objetos de outro usuário ou tenant.",
        limits="Exercita apenas o acesso de leitura.",
    )

    text = _text(render_package_pdf(_document("pt-BR", catalog=catalog)))

    assert "05 / Catálogo de Verificações" in text
    assert "Parcialmente coberta" in text
    assert "O que falta: Exercita apenas o acesso de leitura." in text
    assert "Testar acesso a objetos de outro usuário ou tenant." in text


def test_an_unavailable_catalog_is_stated() -> None:
    text = _text(render_package_pdf(_document(catalog={"status": "unavailable", "reason": "x"})))

    assert "05 / Assessment Catalog" in text
    assert "The catalog plan was not available for this run." in text


def test_an_open_item_prints_its_reason() -> None:
    catalog = copy.deepcopy(CATALOG)
    catalog["groups"].append(
        {
            "status": "open",
            "items": [
                {
                    "id": "5.3.1",
                    "title": "Certificate chain",
                    "scope": "full",
                    "limits": None,
                    "reason": "receipts_unavailable",
                }
            ],
        }
    )

    text = _text(render_package_pdf(_document(catalog=catalog)))

    assert "Reason: receipts_unavailable" in text


@pytest.mark.parametrize(
    "catalog",
    [
        "text",
        [],
        {"status": "maybe"},
        {"status": "ready"},
        {"status": "ready", "totals": {}, "groups": {}},
        {"status": "ready", "totals": [], "groups": []},
        {"status": "ready", "totals": {}, "groups": ["x"]},
        {"status": "ready", "totals": {}, "groups": [{"status": "done", "items": []}]},
        {"status": "ready", "totals": {}, "groups": [{"status": "open", "items": "x"}]},
        {"status": "ready", "totals": {}, "groups": [{"status": "open", "items": ["x"]}]},
    ],
)
def test_catalog_contract_violations_are_refused(catalog: Any) -> None:
    with pytest.raises(DocumentError):
        render_package_pdf(_document(catalog=catalog))


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_catalog_text_neither_breaks_the_render_nor_adds_actions(payload: str) -> None:
    catalog = copy.deepcopy(CATALOG)
    for group in catalog["groups"]:
        for item in group["items"]:
            item.update(id=payload, title=payload, limits=payload, reason=payload, scope="partial")
    catalog["totals"] = dict.fromkeys(catalog["totals"], payload)
    document = _document(catalog=catalog)
    document["labels"]["catalog_limits"] = payload

    pdf = render_package_pdf(document)

    assert pdf.startswith(b"%PDF-")
    for forbidden in FORBIDDEN_IN_PDF:
        assert forbidden not in pdf
