"""Malformed capability grants disable their connection without leaking config."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from strix.tools.mcp.loader import load_user_mcp_configs


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_invalid_policy_does_not_fall_back_to_unrestricted_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "mcp.json"
    source.write_text(
        json.dumps(
            [
                {
                    "name": "bad-policy",
                    "url": "https://mcp.example.invalid",
                    "auth": {"kind": "bearer", "token": "fixture-private-token"},
                    "tool_policies": {
                        "read": {
                            "allowed_arguments": [],
                            "argument_values": {"project": ["fixture-private-project"]},
                        }
                    },
                },
                {"name": "bad-policy", "url": "https://mcp.example.invalid"},
                {"name": "valid", "url": "https://mcp.example.invalid", "tool_policies": {}},
            ]
        ),
        encoding="utf-8",
    )
    configs = load_user_mcp_configs(source)
    assert [config.name for config in configs] == ["valid"]
    assert configs[0].tool_policies == {}
    assert "validation errors" in caplog.text
    assert "fixture-private-token" not in caplog.text
    assert "fixture-private-project" not in caplog.text
