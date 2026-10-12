"""``--fork-version``: the SHVIA build identity a native client compares (not ``--version``)."""

from __future__ import annotations

import re

import pytest

from strix.fork_version import fork_version
from strix.interface import cli_args
from strix.interface.cli_args import get_version, parse_arguments


def run(flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setattr("sys.argv", ["strix", flag])
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 0
    return capsys.readouterr().out


def test_fork_version_prints_exactly_the_bundled_x_y_z(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run("--fork-version", monkeypatch, capsys)
    assert out == f"{fork_version()}\n"
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", out), "a strict client parses this line as it is"


def test_it_works_without_a_target_or_any_other_argument(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # parse_arguments requires a target for a scan; the version flags exit before that check.
    assert run("--fork-version", monkeypatch, capsys).strip() == fork_version()


def test_the_upstream_version_flag_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `--version` is for humans and must keep saying `strix <upstream>`.
    assert run("--version", monkeypatch, capsys) == f"strix {get_version()}\n"
    assert get_version() != fork_version(), "the two identities are different on purpose"


def test_an_unreadable_fork_version_is_not_an_x_y_z(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_args, "fork_version", lambda: None)
    out = run("--fork-version", monkeypatch, capsys)
    assert out == "unknown\n"
    assert not re.fullmatch(r"\d+\.\d+\.\d+\n", out)
