"""clone_repository refuses path traversal and git argument injection.

Covers upstream PR #1157 (issues #1133 and #1134), which shipped without tests.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from strix.interface import utils


if TYPE_CHECKING:
    from pathlib import Path


class _FakeGitRun:
    """Stands in for subprocess.run and records every argv it receives."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeGitRun:
    fake = _FakeGitRun()
    monkeypatch.setattr(utils.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(utils.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(utils.subprocess, "run", fake)
    return fake


def _other_run_file(tmp_path: Path) -> Path:
    marker = tmp_path / "strix_repos" / "other-run" / "keep.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep", encoding="utf-8")
    return marker


def test_dotdot_repo_name_is_refused_before_anything_is_deleted(
    fake_git: _FakeGitRun, tmp_path: Path
) -> None:
    marker = _other_run_file(tmp_path)

    with pytest.raises(ValueError, match="escapes the temporary directory"):
        utils.clone_repository("https://example.com/org/..git", "run-a")

    assert marker.exists()
    assert fake_git.calls == []


def test_dot_dest_name_is_refused(fake_git: _FakeGitRun) -> None:
    with pytest.raises(ValueError, match="escapes the temporary directory"):
        utils.clone_repository("https://example.com/org/repo.git", "run-b", dest_name=".")

    assert fake_git.calls == []


def test_dash_prefixed_target_never_reaches_git(fake_git: _FakeGitRun) -> None:
    with pytest.raises(ValueError, match="starts with '-'"):
        utils.clone_repository("--upload-pack=touch pwned", "run-c", dest_name="repo")

    assert fake_git.calls == []


def test_clone_separates_options_from_the_url(fake_git: _FakeGitRun) -> None:
    url = "https://example.com/org/repo.git"

    path = utils.clone_repository(url, "run-d")

    assert fake_git.calls == [["/usr/bin/git", "clone", "--", url, path]]
