"""bin/strix-run picks each provider's own key and budget from the environment.

The launcher is a bash script, so it is driven for real: ``STRIX_BIN`` points at a stub
that prints what it was started with, and ``STRIX_ENV_FILE`` at an empty file so a
developer's own ``.env`` never leaks in. The ambient ``LLM_API_KEY`` is set on purpose:
a provider that is missing from the launcher's table falls through to that generic key
and to ``STRIX_BUDGET_DEFAULT``, which is exactly the silent mistake these cases catch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "bin" / "strix-run"

AMBIENT = {"LLM_API_KEY": "ambient-generic-key", "STRIX_BUDGET_DEFAULT": "99"}

CASES = [
    pytest.param(
        "minimax/MiniMax-M3",
        {"MINIMAX_API_KEY": "mm-key", "STRIX_BUDGET_MINIMAX": "7"},
        ("mm-key", "7"),
        id="minimax",
    ),
    pytest.param(
        "deepseek/deepseek-v4-pro",
        {"DEEPSEEK_API_KEY": "ds-key", "STRIX_BUDGET_DEEPSEEK": "5"},
        ("ds-key", "5"),
        id="deepseek",
    ),
    pytest.param(
        "moonshot/kimi-k3",
        {"MOONSHOT_API_KEY": "ms-key", "STRIX_BUDGET_MOONSHOT": "3"},
        ("ms-key", "3"),
        id="moonshot",
    ),
    pytest.param("mistral/mistral-large-latest", {}, ("ambient-generic-key", "99"), id="fallback"),
]


def _launch(tmp_path: Path, model: str, extra_env: dict[str, str]) -> dict[str, Any]:
    stub = tmp_path / "strix-stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'llm': os.environ.get('STRIX_LLM'),\n"
        "                  'key': os.environ.get('LLM_API_KEY'),\n"
        "                  'args': sys.argv[1:]}))\n"
    )
    stub.chmod(0o755)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STRIX_BIN": str(stub),
        "STRIX_ENV_FILE": str(env_file),
        "STRIX_WORKDIR": str(tmp_path / "work"),
        **AMBIENT,
        **extra_env,
    }
    done = subprocess.run(  # noqa: S603
        ["/usr/bin/env", "bash", str(LAUNCHER), "-n", "--agent", model, "https://t.example"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(("model", "provider_env", "expected"), CASES)
def test_launcher_resolves_key_and_budget_per_provider(
    tmp_path: Path, model: str, provider_env: dict[str, str], expected: tuple[str, str]
) -> None:
    key, budget = expected
    started = _launch(tmp_path, model, provider_env)

    assert started["llm"] == model
    assert started["key"] == key
    args = started["args"]
    assert args[args.index("--max-budget") + 1] == budget
