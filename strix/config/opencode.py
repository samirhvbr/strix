"""OpenCode subscription auth: API-key sign-in and the OpenAI clients that
route inference through the OpenCode gateway.

Covers both OpenCode offerings — Zen (pay-as-you-go credits) and Go (the
monthly subscription) — which share one account and API key but live behind
different gateway base URLs. Unlike the ChatGPT subscription there is no
OAuth: the user copies a plain API key from https://opencode.ai/auth, and
using the gateway from other agents is officially supported.

Model routing follows the endpoint each model is served on (see
https://opencode.ai/docs/zen/): GPT models use the Responses API, everything
else the OpenAI-compatible Chat Completions API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import requests
from openai import AsyncOpenAI

from strix.config import codex


PROVIDER = "opencode"

ZEN_BASE_URL = "https://opencode.ai/zen/v1"
GO_BASE_URL = "https://opencode.ai/zen/go/v1"

# ``opencode/<model>`` runs on Zen credits; ``opencode-go/<model>`` on the Go
# subscription (matching OpenCode's own ``opencode-go/`` model ids).
ZEN_PREFIX = "opencode/"
GO_PREFIX = "opencode-go/"

AUTH_CONSOLE_URL = "https://opencode.ai/auth"

_KEY_CHECK_TIMEOUT = 30


class OpencodeAuthError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class SubscriptionModel:
    slug: str
    base_url: str
    uses_responses: bool


def _uses_responses(slug: str, base_url: str) -> bool:
    lowered = slug.lower()
    if lowered.startswith("gpt-"):
        return True
    # Grok is served via Responses on Zen but Chat Completions on Go.
    return lowered.startswith("grok") and base_url == ZEN_BASE_URL


def subscription_model(model_name: str | None) -> SubscriptionModel | None:
    """The gateway model behind an ``opencode/`` or ``opencode-go/`` STRIX_LLM."""
    name = (model_name or "").strip()
    lowered = name.lower()
    for prefix, base_url in ((GO_PREFIX, GO_BASE_URL), (ZEN_PREFIX, ZEN_BASE_URL)):
        if lowered.startswith(prefix):
            slug = name[len(prefix) :]
            if not slug:
                return None
            return SubscriptionModel(slug, base_url, _uses_responses(slug, base_url))
    return None


def read_record() -> dict[str, Any] | None:
    record = codex.read_provider_record(PROVIDER)
    if not isinstance(record, dict) or record.get("type") != "api_key":
        return None
    key = record.get("key")
    if not isinstance(key, str) or not key:
        return None
    return record


def is_authenticated() -> bool:
    return read_record() is not None


def save_api_key(key: str) -> None:
    codex.save_provider_record(PROVIDER, {"type": "api_key", "provider": PROVIDER, "key": key})


def logout() -> None:
    codex.remove_provider_record(PROVIDER)


def get_api_key() -> str:
    record = read_record()
    if record is None:
        raise OpencodeAuthError(
            "not_authenticated", "not signed in; run: strix auth login opencode"
        )
    return str(record["key"])


def validate_api_key(key: str) -> None:
    """Check the key against the gateway's models endpoint; raise if rejected."""
    try:
        response = requests.get(
            f"{ZEN_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_KEY_CHECK_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OpencodeAuthError("unavailable", str(exc)) from exc
    if response.status_code in (401, 403):
        raise OpencodeAuthError(
            "invalid_key", f"OpenCode rejected the API key (HTTP {response.status_code})"
        )
    if response.status_code >= 400:
        raise OpencodeAuthError("http_error", f"HTTP {response.status_code}: {response.text[:300]}")


def build_openai_client(base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=get_api_key(),
        base_url=base_url,
        http_client=httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)),
    )


_subscription_clients: dict[str, AsyncOpenAI] = {}


def get_subscription_client(base_url: str) -> AsyncOpenAI:
    client = _subscription_clients.get(base_url)
    if client is None:
        client = build_openai_client(base_url)
        _subscription_clients[base_url] = client
    return client


def auth_mode(model_name: str | None) -> str:
    """Return "subscription" when STRIX_LLM runs on any subscription
    (OpenCode or ChatGPT), else "api_key"."""
    if subscription_model(model_name) or codex.subscription_model(model_name):
        return "subscription"
    return "api_key"
