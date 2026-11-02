"""The model a WEB authorization approves for one assessment (PENTEST-12, phase 1).

The authorization carries an optional ``inference`` block, ``{"model": ..., "source": "local"}``.
``local`` means the operator's own credentials pay for the calls, exactly as when no model is
approved; the block only pins which model runs and lets the native consent name it. Anything
else, including the gateway source reserved for a later phase, is refused rather than guessed.

The block may carry one more key, ``"credential": "account"`` (PT-31): the operator still pays,
but with the key from their own SHVIA account instead of one configured on this computer. The
native app fetches that key after the consent and hands it to this process in the environment
(see :mod:`strix.config.ephemeral`); the key never travels in this block. The binding is
one-to-one and fails closed: an account grant without a key does not start, and a key that no
grant asked for is discarded.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from strix.config import codex, ephemeral, load_settings, pin_run_credentials, pin_run_model


logger = logging.getLogger(__name__)

# ``provider/model``: a lowercase provider scope, then a model name that may itself contain
# slashes (OpenRouter). No scheme, no whitespace, no control characters.
_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}/[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}")
SOURCES = frozenset({"local"})

#: Where the key comes from. ``local`` is the default and is never written in the block;
#: ``account`` is the only value the block may carry.
LOCAL_CREDENTIAL = "local"
ACCOUNT_CREDENTIAL = "account"

_REQUIRED_KEYS = frozenset({"model", "source"})
_OPTIONAL_KEYS = frozenset({"credential"})

CREDENTIALS_AVAILABLE = "available"
CREDENTIALS_MISSING = "missing"
CREDENTIALS_UNKNOWN = "unknown"
#: What an inspection says about an account grant: the key is fetched after the consent, so
#: there is nothing on this machine to check, and the native app must know it is not ``available``.
CREDENTIALS_ACCOUNT = "account"

# Providers whose key LiteLLM reads from a variable of their own but whose
# ``validate_environment`` has no branch for them: it answers "nothing missing" even with an
# empty environment, so its answer cannot be believed. Measured on LiteLLM 1.101.0 (MiniMax
# reported ``available`` with no key anywhere, and the 30/10/2026 run died with a 401).
# Deliberately not a ``<PROVIDER>_API_KEY`` convention: most other providers LiteLLM cannot
# check are keyless local servers or authenticate some other way, and a convention would turn
# runs that work into ``missing``.
_UNCHECKED_PROVIDER_KEYS: dict[str, tuple[str, ...]] = {"minimax": ("MINIMAX_API_KEY",)}


class InferenceChoiceError(ValueError):
    """The approved inference block is malformed or names an unsupported source."""


@dataclass(frozen=True, slots=True)
class InferenceChoice:
    model: str
    source: str
    credential: str = LOCAL_CREDENTIAL

    def summary(self) -> dict[str, str]:
        """What the run record and the consent output say; ``credential`` only when ``account``."""
        summary = {"model": self.model, "source": self.source}
        if self.credential != LOCAL_CREDENTIAL:
            summary["credential"] = self.credential
        return summary


def parse_inference(raw: Any) -> InferenceChoice | None:
    """Validate the optional block of an authorization response; ``None`` means not approved."""
    if raw is None:
        return None
    if (
        not isinstance(raw, dict)
        or not _REQUIRED_KEYS <= set(raw) <= _REQUIRED_KEYS | _OPTIONAL_KEYS
    ):
        raise InferenceChoiceError("Invalid inference block")
    model, source = raw["model"], raw["source"]
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise InferenceChoiceError("Invalid inference model")
    if not isinstance(source, str) or source not in SOURCES:
        raise InferenceChoiceError("Unsupported inference source")
    credential = LOCAL_CREDENTIAL
    if "credential" in raw:
        # Exactly ``account``: ``local`` is the absence of the key, not a value, so an old
        # server can never be mistaken for a new one and nothing else is guessed.
        if not isinstance(raw["credential"], str) or raw["credential"] != ACCOUNT_CREDENTIAL:
            raise InferenceChoiceError("Unsupported inference credential")
        if codex.subscription_model(model):
            # A subscription signs in; an account key would be ignored and the run would pay
            # with this computer's sign-in, which an account grant must never do.
            raise InferenceChoiceError("An account key cannot pay for a subscription model")
        credential = ACCOUNT_CREDENTIAL
    return InferenceChoice(model=model, source=source, credential=credential)


def apply_choice(choice: InferenceChoice, *, inspecting: bool = False) -> None:
    """Pin the approved model for this process, unpersisted and without another vendor's key.

    An ``account`` choice also binds the run to the one key it was handed: the key is required
    (``InferenceChoiceError`` otherwise, so nothing starts), pinned for this process only, and
    the person's own connection variables are removed from the environment so that nothing else
    can pay. Any other choice discards a key nobody asked for. An inspection starts nothing and
    is made before the key is fetched, so it neither needs nor keeps one.
    """
    if choice.credential != ACCOUNT_CREDENTIAL:
        ephemeral.discard_stray_run_credentials("the approved model is paid with local credentials")
        pin_run_model(choice.model)
        return
    if inspecting:
        ephemeral.discard_stray_run_credentials("an inspection starts no run")
        pin_run_model(choice.model)
        return
    api_key = ephemeral.run_api_key()
    if api_key is None:
        raise InferenceChoiceError(
            "The approved model is paid with the key from the SHVIA account, but this process "
            "was not handed a usable key; nothing was started"
        )
    pin_run_model(choice.model)
    pin_run_credentials(api_key)
    removed = ephemeral.scrub_ambient_connection()
    if removed:
        logger.info(
            "account key run: removed %d variable(s) of the local connection from the "
            "environment: %s",
            len(removed),
            ", ".join(removed),
        )


def local_credentials_state(model: str, credential: str = LOCAL_CREDENTIAL) -> str:
    """Whether this machine can pay for ``model`` itself, without revealing any credential.

    Call after :func:`apply_choice`: the settings then describe the approved model, so a
    generic key that belongs to a different provider has already been dropped.

    For an ``account`` grant nothing local is checked: the answer is ``available`` once the
    run holds the key (a run never starts without it), and ``account`` before that, which is
    what an inspection reports.
    """
    if credential == ACCOUNT_CREDENTIAL:
        return CREDENTIALS_AVAILABLE if ephemeral.run_api_key() else CREDENTIALS_ACCOUNT
    if codex.subscription_model(model):
        return CREDENTIALS_AVAILABLE if codex.is_authenticated() else CREDENTIALS_MISSING
    if load_settings().llm.api_key:
        return CREDENTIALS_AVAILABLE
    own_variables = _UNCHECKED_PROVIDER_KEYS.get(model.split("/", 1)[0].lower())
    if own_variables is not None:
        return (
            CREDENTIALS_AVAILABLE
            if any(os.environ.get(name) for name in own_variables)
            else CREDENTIALS_MISSING
        )
    return _catalog_credentials_state(model)


def _catalog_credentials_state(model: str) -> str:
    """What LiteLLM's own environment check says about ``model``'s provider."""
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
