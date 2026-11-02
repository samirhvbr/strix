"""A model API key handed to this one process for one run, never written anywhere.

The ShvIA Desktop can fetch a person's own provider key from their SHVIA account and start the
engine with it, when the approved inference block says ``credential: "account"`` (PT-31). The key
travels in the child's environment under a name that is **not** an alias of any setting:
``persist_current`` copies every aliased variable into ``~/.strix/cli-config.json``, so
``LLM_API_KEY`` here would plant the account key on the person's disk on every run, where it
would also outlive a revoked grant.

``main()`` calls :func:`take_run_credentials` as its first statement. It removes the variable from
``os.environ`` before anything can log, persist or spawn (nothing the engine starts afterwards
inherits it, and nothing that walks the environment sees it), checks its shape, and keeps the
value in a private holder. Three things read the holder: the binding of an account grant to this
process (:func:`run_api_key`), the discard of a key that no grant asked for
(:func:`discard_stray_run_credentials`), and the writer of the config file, which must never
persist it (:func:`is_run_api_key`, which answers without handing the key over).

Everything here is inert when the variable is absent.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Final

from pydantic import SecretStr


logger = logging.getLogger(__name__)

#: The one environment variable the key arrives in. It must never become an alias of a setting
#: (``tests/test_ephemeral_credentials.py`` asserts that), or ``persist_current`` would write it.
ENV_NAME: Final = "STRIX_RUN_LLM_API_KEY"

# Printable ASCII without whitespace, 8 to 512 characters. Matched with ``fullmatch`` on purpose:
# ``$`` also matches before a trailing newline, and a newline in a key fails much later as an
# illegal header value or a provider 401 (the header check only rejects non-ASCII).
_SHAPE: Final = re.compile(r"[\x21-\x7e]{8,512}")

# Variables of the person's own connection that an account run must not inherit. Routing names
# (LiteLLM reads ``<PROVIDER>_API_BASE`` at call time) could send the key to another endpoint;
# credential names would be used *instead of* the key: measured with LiteLLM 1.101.0, a person's
# own ``DEEPSEEK_API_KEY`` (and the equivalent of groq, gemini, mistral, xai, moonshot and minimax)
# goes on the wire ahead of the key the engine pinned, and ``_mirror_api_key_to_provider_env``
# only ever fills a variable that is still missing. Only anthropic and openrouter prefer the pin.
_AMBIENT_SUFFIXES: Final = ("_API_BASE", "_BASE_URL", "_API_KEY")
_AMBIENT_NAMES: Final = frozenset({"LLM_EXTRA_HEADERS", "LLM_EXTRA_BODY", "ANTHROPIC_AUTH_TOKEN"})

_held: SecretStr | None = None
_supplied = False


def take_run_credentials() -> None:
    """Take the run's key out of the environment, once, whatever it holds.

    The variable is always removed when present, a malformed value included, so it cannot
    reach a child process or a later reader. A value that fails the shape rule is not kept
    and is never trimmed into shape. Calling this again is harmless: a key already held is
    never replaced, and with the variable gone it changes nothing.
    """
    global _held, _supplied  # noqa: PLW0603
    value = os.environ.pop(ENV_NAME, None)
    if value is None:
        return
    _supplied = True
    if _held is None and _SHAPE.fullmatch(value):
        _held = SecretStr(value)


def run_credentials_supplied() -> bool:
    """Whether a value arrived and has not been discarded, usable or not."""
    return _supplied


def run_api_key() -> str | None:
    """The key this process was handed, or ``None`` when none arrived or it was malformed."""
    return _held.get_secret_value() if _held is not None else None


def is_run_api_key(value: str | None) -> bool:
    """Whether ``value`` is the held key, so a writer can refuse it without ever seeing it."""
    return _held is not None and value is not None and value == _held.get_secret_value()


def discard_run_credentials() -> None:
    """Forget the key. Best effort: Python cannot wipe the string itself."""
    global _held, _supplied  # noqa: PLW0603
    _held = None
    _supplied = False


def discard_stray_run_credentials(reason: str) -> None:
    """Forget a key that arrived for a run that does not use one, and say so without the value."""
    if not _supplied:
        return
    logger.warning("A run model key was supplied but %s; it was discarded unused", reason)
    discard_run_credentials()


def scrub_ambient_connection() -> tuple[str, ...]:
    """Remove the person's own model connection from the environment of an account run.

    An account run talks to the provider's standard host with the key it was handed and with
    nothing else: a person who needs a custom endpoint, extra headers or their own key uses
    local credentials. Every variable that routes a request (``*_API_BASE``, ``*_BASE_URL``,
    ``LLM_EXTRA_HEADERS``, ``LLM_EXTRA_BODY``) or authenticates one (``*_API_KEY``,
    ``ANTHROPIC_AUTH_TOKEN``) goes; the provider's own variable is then filled with the account
    key by ``_mirror_api_key_to_provider_env``, which only fills what is missing. The settings
    already resolved (the ones for the web search, for instance) are not affected: they are read
    once. LiteLLM is also kept from loading a ``.env`` file at import, which would bring the
    removed variables back. Returns the names removed (never values), for the log.
    """
    names = tuple(
        sorted(
            name
            for name in os.environ
            if name.upper().endswith(_AMBIENT_SUFFIXES) or name.upper() in _AMBIENT_NAMES
        )
    )
    for name in names:
        del os.environ[name]
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
    return names
