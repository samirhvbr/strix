"""Standalone SHVIA report PDF renderer (replaces the WEB's headless Chromium).

The SHVIA WEB validates, redacts and translates a report package, then builds one JSON document
(``report-pdf-v1``) from it. This module turns that document into a PDF with reportlab.

It is a leaf on purpose: it imports only reportlab, markdown-it and the standard library, never
``strix``. Importing ``strix.interface`` pulls in the whole agent stack, so the WEB host could not
run it without installing all of Strix. As a file it runs from a small virtualenv::

    python -I package_pdf.py --input document.json --output report.pdf

The layout follows the viewer's ``report_pdf.py`` (same palette and fonts) and the section order of
the HTML report the WEB used to print: cover, executive summary, methodology, technical analysis,
coverage, recommendations, findings, appendix. It is deliberately not shared with that file:
``report_pdf.py`` comes from upstream and a refactor would conflict on every sync.

Every dynamic string is escaped before it reaches reportlab's markup parser, and Markdown is parsed
with HTML, links, autolinks and images switched off, so report text cannot add tags, links or file
references. Standard fonts are used, so characters outside WinAnsi that Symbol and ZapfDingbats do
not cover (CJK, emoji) print as a box, the same as in the viewer.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)


if TYPE_CHECKING:
    from collections.abc import Mapping

    from markdown_it.token import Token


__all__ = ["DOCUMENT_VERSION", "MAX_INPUT_BYTES", "DocumentError", "main", "render_package_pdf"]

DOCUMENT_VERSION = 1
MAX_INPUT_BYTES = 16 * 1024 * 1024
LANGUAGES = ("en", "pt-BR")

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_RENDER_FAILED = 3

_SEVERITIES = ("critical", "high", "medium", "low", "info")
_OUTCOMES = ("reported", "no_issue_found", "ruled_out", "not_applicable", "needs_follow_up")
_COVER_KEYS = (
    "scan_id",
    "scan_mode",
    "status",
    "started_at",
    "ended_at",
    "duration_s",
    "strix_version",
    "operator",
    "authorization_ref",
)
_DEFAULT_NARRATIVE = frozenset(
    {
        "title",
        "description",
        "impact",
        "counterevidence",
        "confidence_rationale",
        "severity_change_conditions",
        "technical_analysis",
        "poc_description",
        "remediation_steps",
        "fix_verification",
        "assumptions",
        "executive_summary",
        "methodology",
        "recommendations",
        "contextual_cvss_reasoning",
        "label",
        "reason",
        "detail",
    }
)

_INK = colors.HexColor("#000000")
_TEXT = colors.HexColor("#1a1a1a")
_MUTED = colors.HexColor("#666666")
_FAINT = colors.HexColor("#999999")
_BORDER = colors.HexColor("#e5e5e5")
_LIGHT_BG = colors.HexColor("#f7f7f7")
_WARN_BG = colors.HexColor("#fef3c7")
_WARN_BORDER = colors.HexColor("#d97706")
_SEVERITY_COLORS = {
    "critical": colors.HexColor("#dc2626"),
    "high": colors.HexColor("#ea580c"),
    "medium": colors.HexColor("#ca8a04"),
    "low": colors.HexColor("#2563eb"),
    "info": colors.HexColor("#6b7280"),
}

_SANS = "Helvetica"
_SANS_BOLD = "Helvetica-Bold"
_MONO = "Courier"

_PAGE_W, _PAGE_H = A4
_CONTENT_W = _PAGE_W - 40 * mm
_CODE_COLUMNS = 90
_SECTION_ROOM = 90 * mm

_INLINE_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable(
    ["autolink", "image", "link"]
)
_UNSAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff￾￿]")


class DocumentError(ValueError):
    """The input document does not follow the ``report-pdf-v1`` contract."""


def _normalize_text(value: Any) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return _UNSAFE_TEXT_RE.sub("�", text)


def _esc(value: Any) -> str:
    """Escape a value for reportlab's Paragraph markup."""
    return html.escape(_normalize_text(value)).replace("\n", "<br/>")


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _clip(value: Any, limit: int) -> str:
    """One-line text bounded for a table cell: a table row cannot split across pages."""
    text = " ".join(_normalize_text(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _wrap_code(text: str) -> str:
    lines: list[str] = []
    for source in _normalize_text(text).split("\n"):
        rest = source
        while len(rest) > _CODE_COLUMNS:
            lines.append(rest[:_CODE_COLUMNS])
            rest = rest[_CODE_COLUMNS:]
        lines.append(rest)
    return "\n".join(lines)


class _NumberedCanvas(pdfcanvas.Canvas):  # type: ignore[misc]  # reportlab base is untyped
    """Two-pass canvas that prints 'Page X / Y' on every page after the cover."""

    def __init__(self, *args: Any, page_label: str = "Page", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._page_label = page_label
        self._saved_states: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab API
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_states)
        for index, state in enumerate(self._saved_states):
            self.__dict__.update(state)
            if index > 0:
                self.setFont(_SANS, 8)
                self.setFillColor(_FAINT)
                self.drawCentredString(
                    _PAGE_W / 2,
                    14 * mm,
                    f"{_normalize_text(self._page_label)} {index + 1} / {total}",
                )
            super().showPage()
        super().save()


def _styles() -> dict[str, ParagraphStyle]:
    def style(name: str, **kwargs: Any) -> ParagraphStyle:
        return ParagraphStyle(name, **kwargs)

    return {
        "brand": style("Brand", fontName=_SANS_BOLD, fontSize=15, leading=18, textColor=_INK),
        "eyebrow": style("Eyebrow", fontName=_SANS_BOLD, fontSize=9, leading=12, textColor=_MUTED),
        "cover_title": style(
            "CoverTitle", fontName=_SANS_BOLD, fontSize=34, leading=38, textColor=_INK
        ),
        "cover_target": style(
            "CoverTarget", fontName=_SANS, fontSize=13, leading=18, textColor=_MUTED
        ),
        "meta_label": style(
            "MetaLabel", fontName=_SANS_BOLD, fontSize=8, leading=12, textColor=_MUTED
        ),
        "meta_value": style(
            "MetaValue", fontName=_SANS, fontSize=10.5, leading=14, textColor=_TEXT
        ),
        "section": style(
            "Section", fontName=_SANS_BOLD, fontSize=18, leading=22, textColor=_INK, spaceAfter=6
        ),
        "finding": style(
            "Finding", fontName=_SANS_BOLD, fontSize=14, leading=18, textColor=_INK, spaceBefore=6
        ),
        "subhead": style(
            "Subhead",
            fontName=_SANS_BOLD,
            fontSize=11.5,
            leading=15,
            textColor=_INK,
            spaceBefore=12,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "field_label": style(
            "FieldLabel",
            fontName=_SANS_BOLD,
            fontSize=8.5,
            leading=12,
            textColor=_MUTED,
            spaceBefore=10,
            spaceAfter=2,
            keepWithNext=1,
        ),
        "body": style(
            "Body", fontName=_SANS, fontSize=10, leading=15, textColor=_TEXT, spaceAfter=8
        ),
        "muted": style(
            "Muted", fontName=_SANS, fontSize=9, leading=13, textColor=_MUTED, spaceAfter=6
        ),
        "md_heading": style(
            "MdHeading",
            fontName=_SANS_BOLD,
            fontSize=11,
            leading=15,
            textColor=_INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "bullet": style(
            "Bullet",
            fontName=_SANS,
            fontSize=10,
            leading=15,
            textColor=_TEXT,
            leftIndent=16,
            firstLineIndent=-11,
            spaceAfter=3,
        ),
        # spaceBefore/spaceAfter must exceed borderPadding: reportlab does not reserve a bordered
        # paragraph's top padding, so a smaller gap lets the box bleed over the label above it.
        "code": style(
            "Code",
            fontName=_MONO,
            fontSize=8,
            leading=11,
            textColor=_TEXT,
            backColor=_LIGHT_BG,
            borderColor=_BORDER,
            borderWidth=0.5,
            borderPadding=8,
            spaceBefore=12,
            spaceAfter=12,
        ),
        "count": style("Count", fontName=_SANS_BOLD, fontSize=26, leading=30, alignment=TA_CENTER),
        "count_label": style(
            "CountLabel",
            fontName=_SANS_BOLD,
            fontSize=7.5,
            leading=11,
            textColor=_MUTED,
            alignment=TA_CENTER,
            spaceBefore=4,
        ),
        "badge": style(
            "Badge",
            fontName=_SANS_BOLD,
            fontSize=9,
            leading=11,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "confidential": style(
            "Confidential",
            fontName=_SANS_BOLD,
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "warning": style("Warning", fontName=_SANS_BOLD, fontSize=9.5, leading=14, textColor=_TEXT),
    }


def _inline_md(text: str) -> str:
    """Render a safe subset of inline Markdown as reportlab markup."""
    tokens = _INLINE_MD.parseInline(_normalize_text(text))[0].children or []
    return "".join(_inline_token_markup(token) for token in tokens)


def _inline_token_markup(token: Token) -> str:
    fixed_markup = {
        "strong_open": "<b>",
        "strong_close": "</b>",
        "em_open": "<i>",
        "em_close": "</i>",
        "hardbreak": "<br/>",
        "softbreak": " ",
    }.get(token.type)
    if fixed_markup is not None:
        return fixed_markup
    if token.type == "code_inline":
        return f'<font face="{_MONO}" color="#b31d28">{html.escape(token.content)}</font>'
    # Anything else stays escaped so a parser extension cannot expose reportlab tags.
    return html.escape(token.content)


def _markdown_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    source = _normalize_text(text)
    try:
        return Paragraph(_inline_md(source), style)
    except ValueError:
        return Paragraph(_esc(source), style)


def _code_block(text: str, styles: dict[str, ParagraphStyle]) -> Flowable:
    wrapped = _wrap_code(text)
    return XPreformatted(html.escape(wrapped) or "\u00a0", styles["code"])


def _markdown_flowables(  # noqa: PLR0915 - cohesive block parser, splitting hurts clarity
    md: str, styles: dict[str, ParagraphStyle]
) -> list[Flowable]:
    """Render a Markdown block (headings, lists, fenced code, prose) to flowables."""
    flow: list[Flowable] = []
    para: list[str] = []
    bullets: list[tuple[str, str]] = []

    def flush_para() -> None:
        if para:
            flow.append(_markdown_paragraph(" ".join(para), styles["body"]))
            para.clear()

    def flush_bullets() -> None:
        for marker, item in bullets:
            flow.append(_markdown_paragraph(f"{marker}\u00a0{item}", styles["bullet"]))
        bullets.clear()

    lines = _normalize_text(md).split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            flush_para()
            flush_bullets()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            flow.append(_code_block("\n".join(code), styles))
            continue
        if not stripped:
            flush_para()
            flush_bullets()
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_para()
            flush_bullets()
            flow.append(_markdown_paragraph(heading.group(2), styles["md_heading"]))
            i += 1
            continue
        ordered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        unordered = re.match(r"^[-*+]\s+(.*)$", stripped)
        if ordered:
            flush_para()
            bullets.append((f"{ordered.group(1)}.", ordered.group(2)))
            i += 1
            continue
        if unordered:
            flush_para()
            bullets.append(("•", unordered.group(1)))
            i += 1
            continue
        flush_bullets()
        para.append(stripped)
        i += 1

    flush_para()
    flush_bullets()
    return flow


class _Document:
    """A validated ``report-pdf-v1`` document plus the small helpers the layout needs."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        if raw.get("version") != DOCUMENT_VERSION:
            raise DocumentError(f"unsupported document version: {raw.get('version')!r}")
        language = raw.get("language")
        if language not in LANGUAGES:
            raise DocumentError(f"unsupported language: {language!r}")
        labels = raw.get("labels")
        if not isinstance(labels, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
        ):
            raise DocumentError("labels must be an object of strings")
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            raise DocumentError("metadata must be an object")
        scan_results = raw.get("scan_results")
        if not isinstance(scan_results, dict):
            raise DocumentError("scan_results must be an object")
        findings = raw.get("findings")
        if findings is not None and not (
            isinstance(findings, list) and all(isinstance(item, dict) for item in findings)
        ):
            raise DocumentError("findings must be null or a list of objects")
        coverage = raw.get("coverage")
        if coverage is not None and not isinstance(coverage, dict):
            raise DocumentError("coverage must be null or an object")
        if isinstance(coverage, dict) and not isinstance(coverage.get("entries", []), list):
            raise DocumentError("coverage.entries must be a list")
        counts = raw.get("counts")
        if counts is not None and not isinstance(counts, dict):
            raise DocumentError("counts must be null or an object")
        narrative = raw.get("narrative_fields")
        if narrative is not None and not (
            isinstance(narrative, list) and all(isinstance(item, str) for item in narrative)
        ):
            raise DocumentError("narrative_fields must be a list of strings")

        self.language: str = language
        self.labels: Mapping[str, str] = labels
        self.metadata: Mapping[str, Any] = metadata
        self.scan_results: Mapping[str, Any] = scan_results
        self.findings: list[dict[str, Any]] | None = findings
        self.coverage: Mapping[str, Any] | None = coverage
        self.counts: Mapping[str, Any] = counts or {}
        self.incomplete = bool(raw.get("incomplete"))
        self.omitted_fields = bool(raw.get("omitted_fields"))
        self.source_sha256 = str(raw.get("source_sha256") or "")
        self.narrative = frozenset(narrative) if narrative is not None else _DEFAULT_NARRATIVE
        accounting = metadata.get("accounting")
        self.accounting: Mapping[str, Any] = accounting if isinstance(accounting, dict) else {}

    def label(self, key: str) -> str:
        value = self.labels.get(key)
        return value if value else key.replace("_", " ").upper()

    def unknown(self) -> str:
        return self.label("unknown")


def _section(doc: _Document, styles: dict[str, ParagraphStyle], number: int, key: str) -> Table:
    title = _clip(f"{number:02d} / {doc.label(key)}", 160)
    table = Table([[Paragraph(_esc(title), styles["section"])]], colWidths=[_CONTENT_W])
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 1, _BORDER),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _warning(styles: dict[str, ParagraphStyle], text: str) -> Table:
    table = Table([[Paragraph(_esc(_clip(text, 600)), styles["warning"])]], colWidths=[_CONTENT_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _WARN_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 3, _WARN_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def _severity_badge(doc: _Document, styles: dict[str, ParagraphStyle], severity: Any) -> Table:
    level = str(severity).lower().strip() if str(severity).lower().strip() in _SEVERITIES else "low"
    label = _clip(doc.label(level).upper(), 40)
    cell = Paragraph(_esc(label), styles["badge"])
    table = Table([[cell]], colWidths=[max(len(label), 4) * 6.5 + 20])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SEVERITY_COLORS[level]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _severity_grid(doc: _Document, styles: dict[str, ParagraphStyle]) -> Table:
    cells: list[list[Flowable]] = []
    for level in _SEVERITIES:
        count = doc.counts.get(level, 0)
        count_style = ParagraphStyle(
            f"Count{level}", parent=styles["count"], textColor=_SEVERITY_COLORS[level]
        )
        cells.append(
            [
                Paragraph(_esc(count if isinstance(count, int) else 0), count_style),
                Paragraph(_esc(_clip(doc.label(level).upper(), 30)), styles["count_label"]),
            ]
        )
    column = _CONTENT_W / len(_SEVERITIES)
    table = Table([cells], colWidths=[column] * len(_SEVERITIES))
    rules: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
    ]
    rules.extend(
        ("LINEABOVE", (index, 0), (index, 0), 3, _SEVERITY_COLORS[level])
        for index, level in enumerate(_SEVERITIES)
    )
    table.setStyle(TableStyle(rules))
    return table


def _cover(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    meta_rows = [
        [
            Paragraph(_esc(_clip(doc.label(key).upper(), 40)), styles["meta_label"]),
            Paragraph(
                _esc(_clip(doc.metadata.get(key) or doc.unknown(), 300)), styles["meta_value"]
            ),
        ]
        for key in _COVER_KEYS
    ]
    meta_table = Table(meta_rows, colWidths=[44 * mm, _CONTENT_W - 44 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, _BORDER),
            ]
        )
    )
    confidential = Table(
        [[Paragraph(_esc(_clip(doc.label("confidential"), 40)), styles["confidential"])]],
        colWidths=[130],
    )
    confidential.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _INK),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    confidential.hAlign = "CENTER"
    flow: list[Flowable] = [
        Paragraph("SHVIA / STRIX", styles["brand"]),
        Spacer(1, 130),
        Paragraph(_esc(doc.label("confidential")), styles["eyebrow"]),
        Spacer(1, 20),
        Paragraph(_esc(doc.label("title")), styles["cover_title"]),
        Spacer(1, 6),
        Paragraph(_esc(doc.metadata.get("target") or doc.unknown()), styles["cover_target"]),
        Spacer(1, 28),
        meta_table,
        Spacer(1, 70),
        confidential,
    ]
    if doc.source_sha256:
        flow.extend(
            [
                Spacer(1, 14),
                Paragraph(
                    _esc(f"{doc.label('source_hash')}: {doc.source_sha256}"), styles["muted"]
                ),
            ]
        )
    flow.append(PageBreak())
    return flow


def _prose(doc: _Document, styles: dict[str, ParagraphStyle], key: str) -> list[Flowable]:
    value = doc.scan_results.get(key)
    if not isinstance(value, str) or not value.strip():
        return [Paragraph(_esc(doc.unknown()), styles["body"])]
    return _markdown_flowables(value, styles)


def _summary(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = [_section(doc, styles, 1, "executive_summary"), Spacer(1, 14)]
    if doc.incomplete:
        story.extend([_warning(styles, doc.label("incomplete")), Spacer(1, 10)])
    if doc.findings is None:
        story.append(Paragraph(_esc(doc.unknown()), styles["body"]))
    else:
        story.extend([_severity_grid(doc, styles), Spacer(1, 12)])
    story.extend(_prose(doc, styles, "executive_summary"))
    if doc.language == "pt-BR":
        story.append(Paragraph(_esc(doc.label("translation_notice")), styles["muted"]))
    return story


def _methodology(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    operator = doc.metadata.get("operator") or doc.unknown()
    reference = doc.metadata.get("authorization_ref") or doc.unknown()
    story: list[Flowable] = [
        _section(doc, styles, 2, "methodology"),
        Paragraph(_esc(doc.label("authorization")), styles["subhead"]),
        Paragraph(_esc(f"{operator} · {reference}"), styles["body"]),
    ]
    scope = doc.metadata.get("scope")
    story.append(_code_block(_plain(scope if scope is not None else doc.unknown()), styles))
    story.extend(_prose(doc, styles, "methodology"))
    return story


def _coverage(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = [_section(doc, styles, 4, "coverage"), Spacer(1, 10)]
    coverage = doc.coverage
    if coverage is None:
        story.append(_warning(styles, f"{doc.unknown()}. {doc.label('incomplete')}"))
        return story
    entries = [entry for entry in coverage.get("entries", []) if isinstance(entry, dict)]
    for outcome in _OUTCOMES:
        story.append(Paragraph(_esc(doc.label(outcome)), styles["subhead"]))
        matching = [entry for entry in entries if entry.get("outcome") == outcome]
        if not matching:
            story.append(Paragraph(_esc(doc.unknown()), styles["muted"]))
        story.extend(_code_block(_plain(entry), styles) for entry in matching)
    story.append(Paragraph(_esc(doc.label("gaps")), styles["subhead"]))
    story.append(_code_block(_plain(coverage.get("gaps", [])), styles))
    story.append(Paragraph(_esc(doc.label("provenance")), styles["subhead"]))
    story.append(_code_block(_plain(coverage.get("completeness", {})), styles))
    story.append(_code_block(_plain(coverage.get("machine_observed", {})), styles))
    return story


def _finding(
    doc: _Document, styles: dict[str, ParagraphStyle], finding: dict[str, Any]
) -> list[Flowable]:
    header: list[Flowable] = [
        Paragraph(_esc(finding.get("title") or doc.unknown()), styles["finding"]),
        Spacer(1, 4),
        _severity_badge(doc, styles, finding.get("severity")),
    ]
    if finding.get("id") is not None:
        header.append(Paragraph(_esc(f"ID: {_clip(finding['id'], 128)}"), styles["muted"]))
    story: list[Flowable] = [KeepTogether(header)]
    for key, value in finding.items():
        if key in {"title", "severity", "id"} or value is None:
            continue
        story.append(Paragraph(_esc((doc.labels.get(key) or key).upper()), styles["field_label"]))
        if isinstance(value, str) and key in doc.narrative:
            story.extend(_markdown_flowables(value, styles))
        else:
            story.append(_code_block(_plain(value), styles))
    story.append(Spacer(1, 22))
    return story


def _findings(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = [_section(doc, styles, 6, "findings"), Spacer(1, 12)]
    if doc.findings is None:
        story.append(_warning(styles, doc.unknown()))
    elif not doc.findings:
        story.append(Paragraph(_esc(doc.label("empty")), styles["body"]))
    if doc.omitted_fields:
        story.extend([Spacer(1, 6), _warning(styles, doc.label("omitted"))])
    for finding in doc.findings or []:
        story.extend(_finding(doc, styles, finding))
    return story


def _appendix(doc: _Document, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = [
        _section(doc, styles, 7, "appendix"),
        Spacer(1, 10),
        Paragraph(_esc(doc.label("ai")), styles["body"]),
        Paragraph(_esc(doc.label("provenance")), styles["subhead"]),
        _code_block(_plain(doc.accounting.get("provenance", doc.unknown())), styles),
        Paragraph(_esc(doc.label("models")), styles["subhead"]),
    ]
    models = doc.accounting.get("models")
    story.extend(
        _code_block(_plain(model), styles) for model in (models if isinstance(models, list) else [])
    )
    story.append(Paragraph(_esc(doc.label("incidents")), styles["subhead"]))
    incidents = doc.accounting.get("incidents")
    if isinstance(incidents, list) and incidents:
        story.extend(_code_block(_plain(incident), styles) for incident in incidents)
    else:
        story.append(Paragraph(_esc(doc.unknown()), styles["body"]))
    return story


def _section_with_prose(
    doc: _Document, styles: dict[str, ParagraphStyle], number: int, key: str
) -> list[Flowable]:
    return [_section(doc, styles, number, key), Spacer(1, 12), *_prose(doc, styles, key)]


def render_package_pdf(raw: Mapping[str, Any]) -> bytes:
    """Render a ``report-pdf-v1`` document to PDF bytes.

    Raises ``DocumentError`` when the document does not follow the contract. The output is
    deterministic for a given input (no timestamps, a fixed document id).
    """
    doc = _Document(raw)
    styles = _styles()
    story: list[Flowable] = [*_cover(doc, styles), *_summary(doc, styles)]
    # A section starts on a fresh page only when little room is left, so a short report is not
    # ten mostly blank pages.
    for part in (
        _methodology(doc, styles),
        _section_with_prose(doc, styles, 3, "technical_analysis"),
        _coverage(doc, styles),
        _section_with_prose(doc, styles, 5, "recommendations"),
        _findings(doc, styles),
        _appendix(doc, styles),
    ):
        story.append(CondPageBreak(_SECTION_ROOM))
        story.extend(part)

    buffer = BytesIO()
    page_label = doc.label("page")

    def canvas_maker(*args: Any, **kwargs: Any) -> _NumberedCanvas:
        return _NumberedCanvas(*args, page_label=page_label, **kwargs)

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=_normalize_text(doc.label("title")),
        author="SHVIA Strix",
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=24 * mm,
        invariant=1,
    )
    document.build(story, canvasmaker=canvas_maker)
    return buffer.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).chmod(0o600)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_document(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise DocumentError("the input document is too large")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DocumentError("the document must be a JSON object")
    return raw


def _fail(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a SHVIA report-pdf-v1 document to PDF.")
    parser.add_argument("--input", required=True, type=Path, help="JSON document to render")
    parser.add_argument("--output", required=True, type=Path, help="PDF file to write")
    args = parser.parse_args(argv)

    try:
        pdf = render_package_pdf(_load_document(args.input))
    except (OSError, ValueError) as error:
        _fail(f"invalid input: {type(error).__name__}: {error}")
        return EXIT_INVALID_INPUT
    except Exception as error:  # noqa: BLE001 - any renderer fault must exit with its own code
        _fail(f"render failed: {type(error).__name__}: {error}")
        return EXIT_RENDER_FAILED

    try:
        _write_atomic(args.output, pdf)
    except OSError as error:
        _fail(f"could not write the output: {error}")
        return EXIT_RENDER_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
