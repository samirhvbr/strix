"""A model key an account grant hands to one run: out of the environment, off the disk, and the
only key that run can use (PT-31).

Every key here is made up. Nothing talks to a provider: the one test that looks at what LiteLLM
would put on the wire stops the HTTP client before it connects.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
import litellm
import pytest
from agents.models import _openai_shared
from pydantic import BaseModel

from strix.config import ephemeral, loader
from strix.config.models import _mirror_api_key_to_provider_env, configure_sdk_model_defaults
from strix.config.settings import Settings
from strix.core.inference_source import (
    CREDENTIALS_AVAILABLE,
    CREDENTIALS_MISSING,
    InferenceChoice,
    apply_choice,
    local_credentials_state,
)
from strix.interface.cli_args import parse_arguments
from tests.test_controlled_assessment import web_gate
from tests.test_open_scan_authorization import open_approval


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


cli_main: Any = importlib.import_module("strix.interface.main")

KEY = "sk-account-0000000000"
LOCAL = "sk-local-1111111111"
ACCOUNT_MODEL = "deepseek/deepseek-v4-pro"
ACCOUNT = {"model": ACCOUNT_MODEL, "source": "local", "credential": "account"}
_CONNECTION_SUFFIXES = ("_API_KEY", "_API_BASE", "_BASE_URL")
_CONNECTION_NAMES = ("STRIX_LLM", "LLM_EXTRA_HEADERS", "LLM_EXTRA_BODY", "ANTHROPIC_AUTH_TOKEN")


@pytest.fixture(autouse=True)
def _own_settings_and_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start with no model connection anywhere, and give the environment back as it was.

    The code under test deletes variables of the person's own connection and writes provider
    variables, so a test that only used ``monkeypatch.setenv`` would leave the session changed.
    """
    saved = dict(os.environ)
    monkeypatch.setattr(loader, "_cached", None)
    monkeypatch.setattr(loader, "_override", None)
    for name in saved:
        if name.upper().endswith(_CONNECTION_SUFFIXES) or name.upper() in _CONNECTION_NAMES:
            monkeypatch.delenv(name)
    yield
    for name in set(os.environ) - set(saved):
        del os.environ[name]
    for name, value in saved.items():
        if os.environ.get(name) != value:
            os.environ[name] = value


def connection(monkeypatch: pytest.MonkeyPatch, **variables: str) -> None:
    """The person's own configuration, as environment variables."""
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def grant(inference: dict[str, str] | None) -> dict[str, Any]:
    body = open_approval()
    if inference is not None:
        body["inference"] = inference
    return json.loads(json.dumps(body).replace("127.0.0.1", "192.0.2.10"))  # type: ignore[no-any-return]


def start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inference: dict[str, str] | None,
    supplied: str | None = None,
    flag: str = "--web-authorization",
    *extra: str,
) -> dict[str, Any]:
    """Up to the end of argument parsing, as ``main()`` runs it, with a WEB approving it."""
    monkeypatch.chdir(tmp_path)
    state: dict[str, Any] = {"body": grant(inference)}
    web_gate(tmp_path, monkeypatch, state)
    monkeypatch.setattr("sys.argv", ["strix", flag, str(tmp_path / "authorization.json"), *extra])
    if supplied is not None:
        monkeypatch.setenv(ephemeral.ENV_NAME, supplied)
    ephemeral.take_run_credentials()
    return state


def test_the_run_variable_is_not_an_alias_of_any_setting() -> None:
    # persist_current copies every aliased variable into ~/.strix/cli-config.json.
    aliases: set[str] = set()
    for section in Settings.model_fields.values():
        model = section.annotation
        if isinstance(model, type) and issubclass(model, BaseModel):
            for finfo in model.model_fields.values():
                aliases.update(alias.upper() for alias in loader._aliases_for(finfo))
    assert aliases
    assert ephemeral.ENV_NAME.upper() not in aliases


def test_main_takes_the_run_key_out_of_the_environment_before_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def logging_starts() -> None:
        seen["in_environment"] = ephemeral.ENV_NAME in os.environ
        seen["held"] = ephemeral.run_api_key()
        raise SystemExit(0)

    monkeypatch.setattr(cli_main, "setup_console_logging", logging_starts)
    monkeypatch.setenv(ephemeral.ENV_NAME, KEY)
    with pytest.raises(SystemExit):
        cli_main.main()
    assert seen == {"in_environment": False, "held": KEY}


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        (KEY, True),
        ("a" * 8, True),
        ("a" * 512, True),
        ("!~" + "a" * 6, True),
        ("a" * 7, False),
        ("a" * 513, False),
        (KEY + "\n", False),  # `$` would accept this; the key then fails as an illegal header
        (KEY + "\r\n", False),
        ("\n" + KEY, False),
        (KEY.replace("-0", " 0", 1), False),
        ("sk-account-é0000000", False),
        ("", False),
    ],
)
def test_only_a_well_formed_key_is_kept_and_the_variable_is_always_removed(
    monkeypatch: pytest.MonkeyPatch, value: str, kept: bool
) -> None:
    monkeypatch.setenv(ephemeral.ENV_NAME, value)
    ephemeral.take_run_credentials()
    assert ephemeral.ENV_NAME not in os.environ
    assert ephemeral.run_api_key() == (value if kept else None)
    assert ephemeral.run_credentials_supplied()


def test_taking_the_key_again_changes_nothing_and_never_swaps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ephemeral.ENV_NAME, KEY)
    ephemeral.take_run_credentials()
    ephemeral.take_run_credentials()
    assert ephemeral.run_api_key() == KEY
    monkeypatch.setenv(ephemeral.ENV_NAME, LOCAL)
    ephemeral.take_run_credentials()
    assert ephemeral.run_api_key() == KEY
    assert ephemeral.ENV_NAME not in os.environ


@pytest.mark.parametrize("existing", [False, True])
def test_the_run_key_never_reaches_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    saved = loader._DEFAULT_PATH
    assert saved.is_relative_to(tmp_path.parent), "the test must not write the real ~/.strix"
    if existing:
        saved.write_text(
            json.dumps({"env": {"STRIX_LLM": "openai/gpt-5", "LLM_API_KEY": "sk-person"}}, indent=2)
        )
        saved.chmod(0o600)
    before = saved.read_bytes() if existing else None
    model = "openai/gpt-5-mini"
    start(tmp_path, monkeypatch, {**ACCOUNT, "model": model}, supplied=KEY)
    parse_arguments()
    # What configure_sdk_model_defaults does for an OpenAI model: the key goes into an alias
    # of LLM_API_KEY, which is exactly what persist_current copies into the file.
    _mirror_api_key_to_provider_env(model, KEY)
    assert os.environ["OPENAI_API_KEY"] == KEY, "the mirror must have put the key in an alias"
    loader.persist_current()
    written = saved.read_bytes() if saved.exists() else b""
    assert KEY.encode() not in written
    if existing:
        assert written == before
    for directory in (saved.parent, tmp_path):
        for path in directory.rglob("*"):
            assert path.is_dir() or KEY.encode() not in path.read_bytes(), path


@pytest.mark.parametrize("supplied", [None, KEY + "\n", "short"])
def test_an_account_grant_without_a_usable_key_never_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    supplied: str | None,
) -> None:
    # The person's own key for the very same provider is right there: it must not be used.
    connection(monkeypatch, STRIX_LLM="openai/gpt-5", LLM_API_KEY=LOCAL, OPENAI_API_KEY=LOCAL)
    start(tmp_path, monkeypatch, {**ACCOUNT, "model": "openai/gpt-5-mini"}, supplied=supplied)
    with pytest.raises(SystemExit) as refused:
        parse_arguments()
    assert refused.value.code == 2
    assert loader.load_settings().llm.model == "openai/gpt-5", "nothing was pinned"
    assert KEY not in capsys.readouterr().err


def test_an_account_grant_runs_on_exactly_the_key_it_was_handed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The person's own connection to the same provider, which pin_run_model alone would keep.
    connection(
        monkeypatch,
        STRIX_LLM="deepseek/deepseek-v4-flash",
        LLM_API_KEY=LOCAL,
        LLM_API_BASE="https://proxy.invalid/v1",
        LLM_EXTRA_HEADERS='{"X-Org": "mine"}',
        LLM_EXTRA_BODY='{"mine": 1}',
        DEEPSEEK_API_KEY=LOCAL,
        DEEPSEEK_API_BASE="https://deepseek-proxy.invalid",
        STRIX_TELEMETRY="0",
    )
    start(tmp_path, monkeypatch, ACCOUNT, supplied=KEY)
    args = parse_arguments()
    llm = loader.load_settings().llm
    assert (llm.model, llm.api_key) == (ACCOUNT_MODEL, KEY)
    assert (llm.api_base, llm.extra_headers, llm.extra_body) == (None, None, None)
    assert not [name for name in os.environ if name.upper().endswith(_CONNECTION_SUFFIXES)]
    assert not {"LLM_EXTRA_HEADERS", "LLM_EXTRA_BODY"} & set(os.environ)
    assert os.environ["STRIX_TELEMETRY"] == "0"  # only the person's model connection goes
    assert ephemeral.ENV_NAME not in os.environ
    assert args.inference == ACCOUNT
    assert KEY not in caplog.text
    assert LOCAL not in caplog.text


@pytest.mark.parametrize(
    "inference",
    [
        {"model": "openai/gpt-5-mini", "source": "local"},
        {"model": "deepseek/deepseek-v4-pro", "source": "local"},
        None,
    ],
    ids=["local grant, same provider", "local grant, other provider", "no model approved"],
)
def test_a_key_nobody_asked_for_is_discarded_and_never_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    inference: dict[str, str] | None,
) -> None:
    connection(monkeypatch, STRIX_LLM="openai/gpt-5", LLM_API_KEY=LOCAL, DEEPSEEK_API_KEY=LOCAL)
    start(tmp_path, monkeypatch, inference, supplied=KEY)
    parse_arguments()
    assert ephemeral.run_api_key() is None
    assert not ephemeral.run_credentials_supplied()
    assert loader.load_settings().llm.api_key in {LOCAL, None}
    assert [record for record in caplog.records if record.name == "strix.config.ephemeral"]
    assert KEY not in caplog.text


@pytest.mark.parametrize("supplied", [None, KEY])
def test_an_inspection_reports_the_account_and_needs_no_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    supplied: str | None,
) -> None:
    connection(monkeypatch, STRIX_LLM="openai/gpt-5", LLM_API_KEY=LOCAL)
    start(tmp_path, monkeypatch, ACCOUNT, supplied=supplied, flag="--inspect-web-authorization")
    with pytest.raises(SystemExit) as inspected:
        parse_arguments()
    assert inspected.value.code == 0
    inference = json.loads(capsys.readouterr().out)["inference"]
    assert inference == {**ACCOUNT, "credentials": "account"}
    assert ephemeral.run_api_key() is None
    assert loader.load_settings().llm.api_key != KEY


@pytest.mark.parametrize("flag", ["--web-authorization", "--inspect-web-authorization"])
def test_a_config_file_cannot_replace_an_approved_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps({"env": {"STRIX_LLM": "openai/gpt-5"}}))
    configuration.chmod(0o600)
    state = start(tmp_path, monkeypatch, ACCOUNT, KEY, flag, "--config", str(configuration))
    with pytest.raises(SystemExit) as refused:
        parse_arguments()
    assert refused.value.code == 2
    assert "last_scan_id" not in state, "the grant must not even be asked about"
    assert loader._override is None


# The servers WEB can serve the account key for, with the variables a person's own key for them
# lives in. Measured with LiteLLM 1.101.0: for every one of them but anthropic and openrouter the
# variable wins over the key the engine pinned unless the engine removes it first.
_ON_THE_WIRE = [
    ("anthropic/claude-sonnet-4-6", ["ANTHROPIC_API_KEY"]),
    ("deepseek/deepseek-v4-pro", ["DEEPSEEK_API_KEY"]),
    ("groq/llama-3.3-70b-versatile", ["GROQ_API_KEY"]),
    ("openrouter/anthropic/claude-sonnet-4", ["OPENROUTER_API_KEY"]),
    ("gemini/gemini-2.5-pro", ["GOOGLE_API_KEY", "GEMINI_API_KEY"]),
    ("mistral/mistral-large-latest", ["MISTRAL_API_KEY"]),
    ("xai/grok-4", ["XAI_API_KEY"]),
    ("moonshot/kimi-k3", ["MOONSHOT_API_KEY"]),
    ("minimax/MiniMax-M3", ["MINIMAX_API_KEY"]),
]


class _NoConnectionError(Exception):
    """Stops the request before anything connects."""


def keys_on_the_request(model: str) -> tuple[bool, bool]:
    """Whether the account key and the person's key are on what LiteLLM would send for ``model``.

    The HTTP client is stopped before it connects, and only two booleans leave this function.
    LiteLLM may first ask for a tokenizer; those requests carry no key and are looked at too.
    """
    seen: list[str] = []

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        seen.append(" ".join([str(request.url), *(value for _, value in request.headers.items())]))
        raise _NoConnectionError

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.Client, "send", send)
        with contextlib.suppress(Exception):
            litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                num_retries=0,
            )
    return any(KEY in blob for blob in seen), any(LOCAL in blob for blob in seen)


@pytest.mark.parametrize(("model", "variables"), _ON_THE_WIRE)
def test_the_account_key_is_the_one_on_the_wire_even_when_the_person_has_their_own(
    monkeypatch: pytest.MonkeyPatch, model: str, variables: list[str]
) -> None:
    # configure_sdk_model_defaults sets process-wide defaults; give them back afterwards.
    monkeypatch.setattr(litellm, "api_key", None)
    monkeypatch.setattr(litellm, "headers", None)
    monkeypatch.setattr(_openai_shared, "_default_openai_key", None)
    for name in variables:
        monkeypatch.setenv(name, LOCAL)
    # Control: with only the person's key around, the request carries it and not the account's.
    assert keys_on_the_request(model) == (False, True)
    monkeypatch.setenv(ephemeral.ENV_NAME, KEY)
    ephemeral.take_run_credentials()
    apply_choice(InferenceChoice(model, "local", "account"))
    configure_sdk_model_defaults(loader.load_settings())
    assert keys_on_the_request(model) == (True, False)


@pytest.mark.parametrize(
    ("variables", "expected"),
    [
        ({}, CREDENTIALS_MISSING),
        ({"MINIMAX_API_KEY": LOCAL}, CREDENTIALS_AVAILABLE),
        ({"LLM_API_KEY": LOCAL}, CREDENTIALS_AVAILABLE),
    ],
    ids=["no key anywhere", "its own variable", "the generic key"],
)
def test_minimax_is_missing_without_a_key_where_litellm_reports_nothing_missing(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], expected: str
) -> None:
    connection(monkeypatch, **variables)
    assert local_credentials_state("minimax/MiniMax-M3") == expected


def test_a_keyless_local_server_does_not_become_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert local_credentials_state("hosted_vllm/some-model") != CREDENTIALS_MISSING
    connection(monkeypatch, OLLAMA_API_BASE="http://localhost:11434")
    assert local_credentials_state("ollama/llama3:8b") != CREDENTIALS_MISSING
