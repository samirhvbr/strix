"""Host-owned MCP capability grants, independent of the provider's catalog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from strix.tools.mcp.config import McpConnectionConfig, McpToolPolicy


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ToolGrant:
    allowed_arguments: frozenset[str]
    required_arguments: frozenset[str]
    argument_values: tuple[tuple[str, tuple[Any, ...]], ...]

    @classmethod
    def from_config(cls, policy: McpToolPolicy) -> _ToolGrant:
        return cls(
            frozenset(policy.allowed_arguments),
            frozenset(policy.required_arguments) | policy.argument_values.keys(),
            tuple((name, tuple(values)) for name, values in policy.argument_values.items()),
        )

    def rejection(self, arguments: dict[str, Any]) -> str | None:
        if arguments.keys() - self.allowed_arguments:
            return "argument_not_allowed"
        if self.required_arguments - arguments.keys():
            return "required_argument_missing"
        for name, choices in self.argument_values:
            value = arguments[name]
            if not any(type(value) is type(choice) and value == choice for choice in choices):
                return "argument_value_not_allowed"
        return None


class McpDispatchPolicy:
    """Snapshot configured grants; discovery and retries cannot widen them.

    No target is inferred from a tool name, description or provider schema.
    Legacy connections remain unrestricted unless configured with an allowlist
    or explicit argument policies. An empty policy mapping denies every tool.
    """

    def __init__(self, config: McpConnectionConfig | None) -> None:
        self._allowed = (
            frozenset(config.allowed_tools)
            if config is not None and config.allowed_tools is not None
            else None
        )
        self._grants = (
            {name: _ToolGrant.from_config(policy) for name, policy in config.tool_policies.items()}
            if config is not None and config.tool_policies is not None
            else None
        )

    def allows_tool(self, name: str) -> bool:
        return (self._allowed is None or name in self._allowed) and (
            self._grants is None or name in self._grants
        )

    def rejection(self, name: str, arguments: Any) -> str | None:
        if not self.allows_tool(name):
            return "tool_not_allowed"
        if not isinstance(arguments, dict):
            return "arguments_not_object"
        return (
            self._grants[name].rejection(cast("dict[str, Any]", arguments))
            if self._grants is not None
            else None
        )


def denied_call(reason: str) -> dict[str, Any]:
    """A stable failed-tool result without argument values or credentials."""
    logger.warning("MCP dispatch blocked by configured policy: %s", reason)
    return {
        "success": False,
        "error": "mcp_policy_denied",
        "reason": reason,
        "message": "The configured MCP dispatch policy denied this call before sending it.",
    }
