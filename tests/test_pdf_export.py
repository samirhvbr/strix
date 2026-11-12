"""``strix report-pdf``: the run's PDF written to a new file, with nothing else touched."""

from __future__ import annotations

import importlib
import json
import os
import stat
from io import BytesIO
from typing import TYPE_CHECKING

import pytest
from pypdf import PdfReader

from strix.interface.pdf_export import run_pdf_export


if TYPE_CHECKING:
    from pathlib import Path


def _make_run(base: Path) -> Path:
    run_dir = base / "strix_runs" / "sample"
    run_dir.mkdir(parents=True)
    record = {
        "run_name": "sample",
        "targets_info": [{"original": "https://example.com"}],
        "status": "completed",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T01:02:03Z",
    }
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    vulns = [
        {"title": "SQL Injection", "severity": "critical", "description": "Reaches the query."}
    ]
    (run_dir / "vulnerabilities.json").write_text(json.dumps(vulns), encoding="utf-8")
    return run_dir


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)


def test_writes_the_run_pdf_to_a_new_owner_only_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _make_run(tmp_path)
    output = tmp_path / "out.pdf"

    assert run_pdf_export([str(run_dir), "--output", str(output)]) == 0

    pdf = output.read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert "SQL Injection" in _text(pdf)
    assert json.loads(capsys.readouterr().out) == {"bytes": len(pdf)}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_a_directory_that_is_not_a_run_writes_nothing_and_says_nothing_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    not_a_run = tmp_path / "secret-folder-name"
    not_a_run.mkdir()
    output = tmp_path / "out.pdf"

    assert run_pdf_export([str(not_a_run), "--output", str(output)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == '{"error":"report_pdf_unavailable"}\n'
    assert not output.exists()


def test_an_existing_file_or_a_planted_link_is_never_written_through(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"mine")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"untouched")
    link = tmp_path / "link.pdf"
    link.symlink_to(victim)

    assert run_pdf_export([str(run_dir), "--output", str(existing)]) == 1
    assert run_pdf_export([str(run_dir), "--output", str(link)]) == 1

    assert existing.read_bytes() == b"mine"
    assert victim.read_bytes() == b"untouched"


def test_a_write_that_fails_half_way_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path)
    output = tmp_path / "out.pdf"

    def failing_fsync(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    assert run_pdf_export([str(run_dir), "--output", str(output)]) == 1
    assert not output.exists()


def test_the_command_is_reachable_from_the_engine_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path)
    output = tmp_path / "out.pdf"
    monkeypatch.setattr("sys.argv", ["strix", "report-pdf", str(run_dir), "--output", str(output)])

    entry_point = importlib.import_module("strix.interface.main")
    with pytest.raises(SystemExit) as exit_code:
        entry_point.main()

    assert exit_code.value.code == 0
    assert output.read_bytes().startswith(b"%PDF-")
