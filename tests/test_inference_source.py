"""The model a WEB authorization approves: parsing, pinning, readiness and the consent output."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import litellm
import pytest

from strix.config import codex, loader
from strix.config.settings import Settings
from strix.core import inference_source
from strix.core.inference_source import (
    InferenceChoice,
    InferenceChoiceError,
    local_credentials_state,
    parse_inference,
)
from strix.core.web_authorization import WebAuthorizationError
from strix.interface.cli_args import parse_arguments
from tests.test_controlled_assessment import (
    approval,
    web_gate,
)
from tests.test_controlled_assessment import (
    server as server,  # noqa: PLC0414 -- Re-export pytest fixture.
)


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts from, and leaves behind, an unresolved settings cache."""
    monkeypatch.setattr(loader, "_cached", None)
    monkeypatch.setattr(loader, "_override", None)
    for name in ("STRIX_LLM", "LLM_API_KEY", "LLM_API_BASE", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def configured(model: str, **llm: Any) -> Settings:
    return Settings(llm={"model": model, **llm})


def use(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(loader, "_cached", settings)


@pytest.mark.parametrize(
    "raw",
    [
        {"model": "deepseek/deepseek-v4-pro", "source": "local"},
        {"model": "chatgpt/gpt-5.4", "source": "local"},
        {"model": "openrouter/anthropic/claude-sonnet-4", "source": "local"},
        {"model": "ollama/llama3:8b", "source": "local"},
    ],
)
def test_parse_accepts_provider_slash_model(raw: dict[str, str]) -> None:
    assert parse_inference(raw) == InferenceChoice(model=raw["model"], source="local")


def test_parse_treats_a_missing_block_as_no_model_approved() -> None:
    assert parse_inference(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "deepseek/x",
        [],
        {"model": "deepseek/x"},
        {"model": "deepseek/x", "source": "local", "api_key": "sk-x"},
        {"model": "deepseek/x", "source": "gateway"},
        {"model": "deepseek/x", "source": ""},
        {"model": "deepseek/x", "source": None},
        {"model": "no-provider", "source": "local"},
        {"model": "Deepseek/x", "source": "local"},
        {"model": "https://evil.invalid/x", "source": "local"},
        {"model": "deepseek/x y", "source": "local"},
        {"model": "deepseek/x\n", "source": "local"},
        {"model": "deepseek/", "source": "local"},
        {"model": "/x", "source": "local"},
        {"model": "deepseek/" + "a" * 200, "source": "local"},
        {"model": 5, "source": "local"},
    ],
)
def test_parse_rejects_anything_else(raw: Any) -> None:
    with pytest.raises(InferenceChoiceError):
        parse_inference(raw)


def test_pin_keeps_the_stored_connection_for_the_same_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(
        monkeypatch,
        configured(
            "deepseek/deepseek-v4-flash",
            api_key="sk-deepseek",
            api_base="https://api.deepseek.test",
        ),
    )
    loader.pin_run_model("deepseek/deepseek-v4-pro")
    llm = loader.load_settings().llm
    assert llm.model == "deepseek/deepseek-v4-pro"
    assert llm.api_key == "sk-deepseek"
    assert llm.api_base == "https://api.deepseek.test"


def test_pin_never_hands_one_vendors_key_to_another(monkeypatch: pytest.MonkeyPatch) -> None:
    use(
        monkeypatch,
        configured(
            "openai/gpt-5",
            api_key="sk-openai",
            api_base="https://api.openai.test",
            extra_headers={"X-Org": "secret"},
        ),
    )
    loader.pin_run_model("deepseek/deepseek-v4-pro")
    llm = loader.load_settings().llm
    assert llm.model == "deepseek/deepseek-v4-pro"
    assert llm.api_key is None
    assert llm.api_base is None
    assert llm.extra_headers is None


def test_pin_with_nothing_configured_starts_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Settings(llm={"api_key": "sk-generic"}))
    loader.pin_run_model("anthropic/claude-sonnet-4")
    assert loader.load_settings().llm.api_key is None


@pytest.mark.parametrize(
    ("configured_model", "pinned", "same"),
    [
        ("litellm/openai/gpt-5", "litellm/deepseek/v4", False),
        ("litellm/openai/gpt-5", "litellm/openai/gpt-5-mini", True),
        ("openrouter/anthropic/claude", "openrouter/deepseek/v4", True),
        ("openai/gpt-5", "litellm/openai/gpt-5", False),
        ("OpenAI/gpt-5", "openai/gpt-5-mini", True),
    ],
)
def test_provider_scope_follows_wrappers_to_their_upstream(
    configured_model: str, pinned: str, same: bool
) -> None:
    assert (loader.provider_scope(configured_model) == loader.provider_scope(pinned)) is same


def test_pin_stays_out_of_the_environment_and_the_operators_saved_defaults(
    tmp_path: Path,
) -> None:
    saved = tmp_path / "cli-config.json"
    saved.write_text(
        json.dumps({"env": {"STRIX_LLM": "openai/gpt-5", "LLM_API_KEY": "sk-openai"}}),
        encoding="utf-8",
    )
    saved.chmod(0o600)
    loader.apply_config_override(saved)
    assert loader.load_settings().llm.model == "openai/gpt-5"

    loader.pin_run_model("deepseek/deepseek-v4-pro")
    loader.persist_current()

    assert "STRIX_LLM" not in os.environ
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "env": {"STRIX_LLM": "openai/gpt-5", "LLM_API_KEY": "sk-openai"}
    }


def test_readiness_for_a_subscription_model_is_the_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, configured("chatgpt/gpt-5.4"))
    monkeypatch.setattr(codex, "is_authenticated", lambda: True)
    assert local_credentials_state("chatgpt/gpt-5.4") == "available"
    monkeypatch.setattr(codex, "is_authenticated", lambda: False)
    assert local_credentials_state("chatgpt/gpt-5.4") == "missing"


def test_readiness_trusts_a_stored_key_that_survived_the_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, configured("deepseek/a", api_key="sk-deepseek"))
    loader.pin_run_model("deepseek/b")
    assert local_credentials_state("deepseek/b") == "available"


def test_readiness_reads_the_providers_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, configured("openai/gpt-5", api_key="sk-openai"))
    loader.pin_run_model("deepseek/deepseek-v4-pro")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert local_credentials_state("deepseek/deepseek-v4-pro") == "missing"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    assert local_credentials_state("deepseek/deepseek-v4-pro") == "available"


def test_readiness_says_unknown_for_a_provider_the_catalog_does_not_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, configured("openai/gpt-5"))
    loader.pin_run_model("nowhere/model")
    assert local_credentials_state("nowhere/model") == "unknown"


def test_readiness_never_raises_when_the_catalog_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, configured("openai/gpt-5"))
    loader.pin_run_model("deepseek/deepseek-v4-pro")

    def broken(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(litellm, "validate_environment", broken)
    assert local_credentials_state("deepseek/deepseek-v4-pro") == "unknown"


def test_authorization_exposes_the_approved_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = approval() | {"inference": {"model": "deepseek/deepseek-v4-pro", "source": "local"}}
    gate = web_gate(tmp_path, monkeypatch, {"body": body})
    assert gate.inference is None
    gate.fetch()
    assert gate.inference == InferenceChoice("deepseek/deepseek-v4-pro", "local")


def test_authorization_without_a_model_approves_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = web_gate(tmp_path, monkeypatch, {"body": approval()})
    gate.fetch()
    assert gate.inference is None


@pytest.mark.parametrize(
    "block",
    [
        {"model": "deepseek/x", "source": "gateway"},
        {"model": "not a model", "source": "local"},
        {"model": "deepseek/x", "source": "local", "api_key": "sk-x"},
        "deepseek/x",
    ],
)
def test_a_malformed_block_voids_the_whole_authorization(
    block: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = web_gate(tmp_path, monkeypatch, {"body": approval() | {"inference": block}})
    with pytest.raises(WebAuthorizationError):
        gate.fetch()
    assert gate.inference is None


def inspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], body: Any
) -> dict[str, Any]:
    web_gate(tmp_path, monkeypatch, {"body": body})
    monkeypatch.setattr(
        "sys.argv", ["strix", "--inspect-web-authorization", str(tmp_path / "authorization.json")]
    )
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def lab(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(body).replace("127.0.0.1", "192.0.2.10"))  # type: ignore[no-any-return]


def test_consent_output_names_the_model_and_whether_this_machine_can_pay_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, configured("openai/gpt-5", api_key="sk-openai"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    block = {"model": "deepseek/deepseek-v4-pro", "source": "local"}
    out = inspect(tmp_path, monkeypatch, capsys, lab(approval()) | {"inference": block})
    assert out["inference"] == block | {"credentials": "missing"}
    assert "sk-openai" not in json.dumps(out)
    # The pin was applied before the readiness answer, so the other vendor's key was dropped.
    assert loader.load_settings().llm.model == "deepseek/deepseek-v4-pro"
    assert loader.load_settings().llm.api_key is None


def test_consent_output_without_an_approved_model_names_the_local_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """WEB approved no model: this run pays with whatever is already configured here.

    Until this was fixed, that case reported ``inference: null`` and nothing ever checked
    the local configuration before a run started — a missing or broken provider key only
    surfaced deep inside the run, after the sandbox had already started. The consent output
    now names what will actually be used and whether this machine can pay for it, the same
    as it already did for an approved model.
    """
    use(monkeypatch, configured("openai/gpt-5", api_key="sk-openai"))
    out = inspect(tmp_path, monkeypatch, capsys, lab(approval()))
    assert out["inference"] == {
        "source": "local",
        "model": "openai/gpt-5",
        "credentials": "available",
    }
    assert "sk-openai" not in json.dumps(out)
    assert loader.load_settings().llm.model == "openai/gpt-5"
    assert loader.load_settings().llm.api_key == "sk-openai"


def test_a_run_refuses_to_start_when_the_approved_model_has_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, configured("openai/gpt-5", api_key="sk-openai"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    body = lab(approval()) | {"inference": {"model": "deepseek/deepseek-v4-pro", "source": "local"}}
    web_gate(tmp_path, monkeypatch, {"body": body})
    monkeypatch.setattr(
        "sys.argv", ["strix", "-n", "--web-authorization", str(tmp_path / "authorization.json")]
    )
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 2
    assert "no credentials on this machine" in capsys.readouterr().err


def test_a_run_refuses_to_start_when_no_model_is_configured_locally_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No model approved by WEB, and nothing configured on this machine: fail before the sandbox."""
    use(monkeypatch, Settings())
    web_gate(tmp_path, monkeypatch, {"body": lab(approval())})
    monkeypatch.setattr(
        "sys.argv", ["strix", "-n", "--web-authorization", str(tmp_path / "authorization.json")]
    )
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 2
    assert "No model is configured on this machine" in capsys.readouterr().err


def test_a_run_refuses_to_start_when_the_local_model_has_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No model approved by WEB; a model IS configured locally, but it has no credentials."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    use(monkeypatch, configured("deepseek/deepseek-v4-pro"))
    web_gate(tmp_path, monkeypatch, {"body": lab(approval())})
    monkeypatch.setattr(
        "sys.argv", ["strix", "-n", "--web-authorization", str(tmp_path / "authorization.json")]
    )
    with pytest.raises(SystemExit) as exit_code:
        parse_arguments()
    assert exit_code.value.code == 2
    err = capsys.readouterr().err
    assert "deepseek/deepseek-v4-pro" in err
    assert "no credentials on this machine" in err


def test_module_exposes_only_the_local_source_for_now() -> None:
    assert frozenset({"local"}) == inference_source.SOURCES
