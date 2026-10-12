"""The model a WEB authorization approves for one assessment (PENTEST-12, phase 1).

The authorization carries an optional ``inference`` block, ``{"model": ..., "source": "local"}``.
``local`` means the operator's own credentials pay for the calls, exactly as when no model is
approved; the block only pins which model runs and lets the native consent name it. Anything
else, including the gateway source reserved for a later phase, is refused rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from strix.config import codex, load_settings, pin_run_model


# ``provider/model``: a lowercase provider scope, then a model name that may itself contain
# slashes (OpenRouter). No scheme, no whitespace, no control characters.
_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}/[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}")
SOURCES = frozenset({"local"})

CREDENTIALS_AVAILABLE = "available"
CREDENTIALS_MISSING = "missing"
CREDENTIALS_UNKNOWN = "unknown"


class InferenceChoiceError(ValueError):
    """The approved inference block is malformed or names an unsupported source."""


@dataclass(frozen=True, slots=True)
class InferenceChoice:
    model: str
    source: str

    def summary(self) -> dict[str, str]:
        return {"model": self.model, "source": self.source}


def parse_inference(raw: Any) -> InferenceChoice | None:
    """Validate the optional block of an authorization response; ``None`` means not approved."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"model", "source"}:
        raise InferenceChoiceError("Invalid inference block")
    model, source = raw["model"], raw["source"]
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise InferenceChoiceError("Invalid inference model")
    if not isinstance(source, str) or source not in SOURCES:
        raise InferenceChoiceError("Unsupported inference source")
    return InferenceChoice(model=model, source=source)


def apply_choice(choice: InferenceChoice) -> None:
    """Pin the approved model for this process, unpersisted and without another vendor's key."""
    pin_run_model(choice.model)


def local_credentials_state(model: str) -> str:
    """Whether this machine can pay for ``model`` itself, without revealing any credential.

    Call after :func:`apply_choice`: the settings then describe the approved model, so a
    generic key that belongs to a different provider has already been dropped.
    """
    if codex.subscription_model(model):
        return CREDENTIALS_AVAILABLE if codex.is_authenticated() else CREDENTIALS_MISSING
    if load_settings().llm.api_key:
        return CREDENTIALS_AVAILABLE
    try:
        import litellm  # noqa: PLC0415 -- heavy, and only the consent path needs it

        known = {getattr(item, "value", item) for item in litellm.provider_list}
        if model.split("/", 1)[0].lower() not in known:
            return CREDENTIALS_UNKNOWN  # the catalog says nothing about this provider
        report = litellm.validate_environment(model=model.lower())
    except Exception:  # noqa: BLE001 -- an unreadable catalog must not stop the consent screen
        return CREDENTIALS_UNKNOWN
    if report.get("missing_keys"):
        return CREDENTIALS_MISSING
    return CREDENTIALS_AVAILABLE if report.get("keys_in_environment") else CREDENTIALS_UNKNOWN
