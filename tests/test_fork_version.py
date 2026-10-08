from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from strix import fork_version
from strix.report import state


if TYPE_CHECKING:
    from pathlib import Path


def test_fork_identity_is_read_from_bundled_file_before_source_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged, source = tmp_path / "packaged-version", tmp_path / "source-version"
    source.write_text("1.2.8\n")
    monkeypatch.setattr(fork_version, "_VERSION_PATHS", (packaged, source))
    assert fork_version.fork_version() == "1.2.8"
    packaged.write_text("1.2.9\n")
    assert fork_version.fork_version() == "1.2.9"
    source.unlink()
    packaged.write_text("not-a-release")
    assert fork_version.fork_version() is None


@pytest.mark.parametrize("original", [None, "1.2.7"])
def test_resume_preserves_original_version_and_records_new_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str | None,
) -> None:
    monkeypatch.setattr(state, "fork_version", lambda: "1.2.8")
    monkeypatch.setattr(state, "run_dir_for", lambda _name: tmp_path)
    record = {"run_id": "old-run", "status": "stopped"}
    if original:
        record.update(strix_version=original, strix_execution_versions=[original])
    (tmp_path / "run.json").write_text(json.dumps(record))
    report = state.ReportState(run_name="old-run")
    report.hydrate_from_run_dir()
    assert report.run_record["strix_version"] == original
    assert report.run_record["strix_execution_versions"] == (
        [original, "1.2.8"] if original else ["1.2.8"]
    )
    report.save_run_data()
    resumed = state.ReportState(run_name="old-run")
    resumed.hydrate_from_run_dir()
    assert (
        resumed.run_record["strix_execution_versions"]
        == report.run_record["strix_execution_versions"]
    )


def test_new_run_records_the_fork_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "fork_version", lambda: "1.2.8")
    report = state.ReportState(run_name="new-run")
    assert report.run_record["strix_version"] == "1.2.8"
    assert report.run_record["strix_execution_versions"] == ["1.2.8"]
