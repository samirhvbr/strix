"""bin/strix-run picks each provider's own key and budget from the environment.

The launcher is a bash script, so it is driven for real: ``STRIX_BIN`` points at a stub
that prints what it was started with, and ``STRIX_ENV_FILE`` at an empty file so a
developer's own ``.env`` never leaks in. The ambient ``LLM_API_KEY`` and
``STRIX_BUDGET_DEFAULT`` are set on purpose: a provider the launcher does not resolve
falls through to them, which is exactly the silent mistake these cases catch.

The rule under test: any ``<provider>/<model>`` finds ``<PROVIDER>_API_KEY`` and
``STRIX_BUDGET_<PROVIDER>`` by convention, a few provider names are aliases, and the
generic ``LLM_API_KEY`` is only a fallback for providers the launcher does not know by name
(custom gateways), never for one it does, so a key is not sent to the wrong vendor.
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
    pytest.param(
        "openai/gpt-5.6-sol",
        {"OPENAI_API_KEY": "oa-key", "STRIX_BUDGET_OPENAI": "10"},
        ("oa-key", "10"),
        id="openai",
    ),
    # Providers outside any table: the convention finds both variables.
    pytest.param(
        "xai/grok-4.7",
        {"XAI_API_KEY": "xai-key", "STRIX_BUDGET_XAI": "9"},
        ("xai-key", "9"),
        id="convention-xai",
    ),
    pytest.param(
        "together_ai/Qwen/Qwen3-235B-A22B",
        {"TOGETHER_AI_API_KEY": "tg-key", "STRIX_BUDGET_TOGETHER_AI": "4"},
        ("tg-key", "4"),
        id="convention-underscore",
    ),
    # A few provider names are aliases of another provider's variables.
    pytest.param(
        "claude/claude-sonnet-5",
        {"ANTHROPIC_API_KEY": "an-key", "STRIX_BUDGET_ANTHROPIC": "6"},
        ("an-key", "6"),
        id="alias-claude",
    ),
    pytest.param(
        "google/gemini-3.1-pro-preview",
        {"GEMINI_API_KEY": "gm-key", "STRIX_BUDGET_GEMINI": "2"},
        ("gm-key", "2"),
        id="alias-google",
    ),
    pytest.param(
        "kimi/kimi-k3",
        {"MOONSHOT_API_KEY": "ms-key", "STRIX_BUDGET_MOONSHOT": "3"},
        ("ms-key", "3"),
        id="alias-kimi",
    ),
    # No budget of its own: the default one applies, so there is always a ceiling.
    pytest.param(
        "openai/gpt-5.6-sol",
        {"OPENAI_API_KEY": "oa-key"},
        ("oa-key", "99"),
        id="budget-falls-back-to-default",
    ),
    # Unknown provider with no variable of its own: the generic gateway key (as before).
    pytest.param(
        "mistral/mistral-large-latest", {}, ("ambient-generic-key", "99"), id="generic-fallback"
    ),
    # A provider the launcher knows by name never borrows the generic key.
    pytest.param(
        "deepseek/deepseek-v4-pro",
        {"STRIX_BUDGET_DEEPSEEK": "5"},
        (None, "5"),
        id="known-provider-without-key-is-not-lent-the-generic-one",
    ),
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
    started: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    started["stderr"] = done.stderr
    return started


@pytest.mark.parametrize(("model", "provider_env", "expected"), CASES)
def test_launcher_resolves_key_and_budget_per_provider(
    tmp_path: Path, model: str, provider_env: dict[str, str], expected: tuple[str | None, str]
) -> None:
    key, budget = expected
    started = _launch(tmp_path, model, provider_env)

    assert started["llm"] == model
    assert started["key"] == key
    args = started["args"]
    assert args[args.index("--max-budget") + 1] == budget


@pytest.mark.parametrize("model", ["chatgpt/gpt-5.6-sol", "codex/gpt-5.6-sol"])
def test_subscription_models_get_no_key_and_no_budget(tmp_path: Path, model: str) -> None:
    started = _launch(tmp_path, model, {})

    assert started["llm"] == model
    assert started["key"] is None
    assert "--max-budget" not in started["args"]


def test_missing_key_warning_names_the_variable_to_define(tmp_path: Path) -> None:
    started = _launch(tmp_path, "xai/grok-4.7", {"STRIX_BUDGET_XAI": "9"})

    # No XAI_API_KEY: it falls back to the generic key, and says what it looked for.
    assert started["key"] == "ambient-generic-key"
    assert "XAI_API_KEY" in started["stderr"]
